"""Owner's narrowly scoped native gift consent. No session keys are exported."""
import hmac
import logging
import secrets
from aiohttp import web
import native_economy as economy

LOG=logging.getLogger("risegram.gift_permit")
OWNER=1780243200
RECIPIENT=1780243207
BOT=1780243214

async def setup():
    async with economy.transaction() as conn:
        await conn.execute("""CREATE TABLE IF NOT EXISTS risegram_owner_gift_permits (
            token text PRIMARY KEY, created_at timestamptz NOT NULL DEFAULT now(),
            expires_at timestamptz NOT NULL DEFAULT now()+interval '5 minutes',
            status text NOT NULL DEFAULT 'pending', confirmed_update_id bigint UNIQUE,
            sent_count integer NOT NULL DEFAULT 0, error text NOT NULL DEFAULT '')""")
        await conn.execute("ALTER TABLE risegram_owner_gift_permits ADD COLUMN IF NOT EXISTS request_message_id bigint")
        await conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_rg_gift_permit_request ON risegram_owner_gift_permits(request_message_id)")

def reply(data,status=200):
    return web.json_response(data,status=status,headers={"Cache-Control":"no-store"})

async def handle(request):
    auth=request.headers.get("Authorization","")
    if not auth.startswith("Bearer ") or len(auth)>240:
        return reply({"ok":False,"error":"Unauthorized"},401)
    try:
        data=await request.json()
        if data.get("user_id")!=OWNER or data.get("chat_id")!=OWNER:
            return reply({"ok":False,"error":"Только @owner в личном чате с TypeVirt"},403)
        action=data.get("action")
        if action not in ("create","status"):
            return reply({"ok":False,"error":"Неизвестная операция"},400)
        async with economy.transaction() as conn:
            bot=await (await conn.execute("SELECT token_secret FROM bots WHERE bot_user_id=%s AND owner_user_id=%s",(BOT,OWNER))).fetchone()
            if not bot or not hmac.compare_digest(auth[7:],str(BOT)+":"+bot["token_secret"]):
                return reply({"ok":False,"error":"Unauthorized"},401)
            await conn.execute("SELECT pg_advisory_xact_lock(%s)",(OWNER,))
            if action=="status":
                rows=await (await conn.execute("""SELECT status,sent_count,error,expires_at>now() AS active
                    FROM risegram_owner_gift_permits ORDER BY created_at DESC LIMIT 1""")).fetchone()
                return reply({"ok":True,"permit":rows})
            mid=data.get("message_id")
            if type(mid) is not int or not 0<mid<=2147483647:
                return reply({"ok":False,"error":"Неверное сообщение"},400)
            proof=await (await conn.execute("""SELECT 1 FROM bot_api_updates u
                JOIN private_messages m ON m.sender_user_id=u.peer_id AND m.recipient_user_id=u.bot_user_id AND m.recipient_box_id=u.message_id
                WHERE u.bot_user_id=%s AND u.peer_type='user' AND u.peer_id=%s AND u.update_kind='message'
                AND u.message_id=%s AND u.created_at>now()-interval '2 minutes'
                AND lower(trim(m.body)) IN ('/giftpermit','/giftpermit@typevirtbot')""",(BOT,OWNER,mid))).fetchone()
            if not proof:
                return reply({"ok":False,"error":"Отправь /giftpermit заново из @owner"},403)
            if await (await conn.execute("""SELECT 1 FROM risegram_owner_gift_permits
                WHERE status='running' OR (status IN ('pending','pending_delayed') AND expires_at>now()) LIMIT 1""")).fetchone():
                return reply({"ok":False,"error":"Уже есть незавершённое разрешение. Проверь /giftstatus"},409)
            if await (await conn.execute("SELECT 1 FROM risegram_owner_gift_permits WHERE request_message_id=%s",(mid,))).fetchone():
                return reply({"ok":False,"error":"Эта команда уже использована. Отправь /giftpermit заново"},409)
            identities=await (await conn.execute("""SELECT count(*) AS n FROM peer_usernames p JOIN users u ON u.id=p.peer_id
                WHERE p.peer_type='user' AND p.active AND u.deleted_at IS NULL AND NOT u.is_bot
                AND ((u.id=%s AND p.username_lower='owner') OR (u.id=%s AND p.username_lower='paths'))""",(OWNER,RECIPIENT))).fetchone()
            if identities["n"]!=2:
                return reply({"ok":False,"error":"Аккаунты не совпадают"},409)
            gifts=await (await conn.execute("""SELECT r.title,r.stars,c.availability_remains FROM star_gift_catalog c
                JOIN star_gift_catalog_revisions r ON r.id=c.active_revision_id
                WHERE c.enabled AND NOT r.sold_out AND c.gift_id IN (9000000000000134,9000000000000136)""")).fetchall()
            catalog={r["title"]:(int(r["stars"]),int(r["availability_remains"])) for r in gifts}
            if catalog.get("Mini Oscars",(0,0))[0]!=8000 or catalog.get("Precious Peaches",(0,0))[0]!=6666 or catalog["Mini Oscars"][1]<2 or catalog["Precious Peaches"][1]<3:
                return reply({"ok":False,"error":"Цена или наличие подарков изменились. Разрешение не создано"},409)
            token=secrets.token_hex(16)
            await conn.execute("INSERT INTO risegram_owner_gift_permits(token,request_message_id,status) VALUES(%s,%s,'pending_delayed')",(token,mid))
            return reply({"ok":True,"token":token,"total":35998})
    except Exception:
        LOG.exception("Gift consent failed")
        return reply({"ok":False,"error":"Не удалось проверить разрешение"},503)
