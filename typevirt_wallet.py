"""TypeVirt internal RiseStars wallet. No real money, external Stars or game payouts.
Only the registered native TypeVirt service can call this endpoint.
All debits/credits and idempotency receipts commit in one PostgreSQL transaction.
"""
import hmac
import logging
import re
import time

from aiohttp import web
import native_economy as economy

log = logging.getLogger("risegram.typevirt_wallet")
BOT_ID = 1780243214
MAX_AMOUNT = 1000000000


async def setup():
    async with economy.transaction() as conn:
        await conn.execute("""CREATE TABLE IF NOT EXISTS risegram_typevirt_wallet (
            user_id bigint PRIMARY KEY REFERENCES users(id), balance bigint NOT NULL DEFAULT 0 CHECK(balance>=0),
            updated_at timestamptz NOT NULL DEFAULT now())""")
        await conn.execute("""CREATE TABLE IF NOT EXISTS risegram_typevirt_wallet_receipts (
            command_key text PRIMARY KEY, user_id bigint NOT NULL, action text NOT NULL,
            amount bigint NOT NULL, balance_after bigint NOT NULL, native_after bigint NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now())""")
        await conn.execute("""CREATE TABLE IF NOT EXISTS risegram_typevirt_donations (
            user_id bigint PRIMARY KEY, total bigint NOT NULL DEFAULT 0 CHECK(total>=0))""")


def reply(body, status=200):
    return web.json_response(body, status=status, headers={"Cache-Control":"no-store"})


async def handle(request):
    header = request.headers.get("Authorization","")
    if not header.startswith("Bearer ") or len(header)>240:
        return reply({"ok":False,"error":"Unauthorized"},401)
    token = header[7:]
    try:
        body = await request.json()
        if not isinstance(body,dict):
            raise ValueError()
        uid = body.get("user_id")
        action = body.get("action")
        amount = body.get("amount",0)
        key = body.get("key","")
        if type(uid) is not int or not 0<uid<2**63 or action not in ("balance","deposit","withdraw","donate"):
            raise ValueError()
        if action!="balance" and (type(amount) is not int or not 1<=amount<=MAX_AMOUNT or not isinstance(key,str) or not re.fullmatch(r"[A-Za-z0-9_-]{10,100}",key)):
            raise ValueError()
    except (ValueError,TypeError):
        return reply({"ok":False,"error":"Некорректная операция"},400)
    try:
        async with economy.transaction() as conn:
            await conn.execute("SET LOCAL statement_timeout='8s'")
            bot = await (await conn.execute("SELECT b.token_secret,b.owner_user_id FROM bots b JOIN users u ON u.id=b.bot_user_id WHERE b.bot_user_id=%s AND u.is_bot AND u.deleted_at IS NULL",(BOT_ID,))).fetchone()
            if not bot or not bot["token_secret"] or not hmac.compare_digest(token,str(BOT_ID)+":"+bot["token_secret"]):
                return reply({"ok":False,"error":"Unauthorized"},401)
            user = await (await conn.execute("SELECT id FROM users WHERE id=%s AND deleted_at IS NULL AND NOT is_bot",(uid,))).fetchone()
            if not user:
                return reply({"ok":False,"error":"Аккаунт не найден"},404)
            await conn.execute("SELECT pg_advisory_xact_lock(%s)",(uid,))
            await conn.execute("INSERT INTO risegram_typevirt_wallet(user_id) VALUES(%s) ON CONFLICT DO NOTHING",(uid,))
            wallet = await (await conn.execute("SELECT balance FROM risegram_typevirt_wallet WHERE user_id=%s FOR UPDATE",(uid,))).fetchone()
            native = await (await conn.execute("SELECT balance FROM stars_balances WHERE user_id=%s",(uid,))).fetchone()
            balance = int(wallet["balance"])
            native_balance = int(native["balance"]) if native else 0
            if action=="balance":
                return reply({"ok":True,"balance":str(balance),"native_balance":str(native_balance)})
            old = await (await conn.execute("SELECT * FROM risegram_typevirt_wallet_receipts WHERE command_key=%s",(key,))).fetchone()
            if old:
                if (old["user_id"],old["action"],old["amount"])!=(uid,action,amount):
                    return reply({"ok":False,"error":"Повтор с другими параметрами"},409)
                return reply({"ok":True,"balance":str(old["balance_after"]),"native_balance":str(old["native_after"]),"duplicate":True})
            if action in ("withdraw","donate") and balance<amount:
                return reply({"ok":False,"error":"Недостаточно снежного кошелька RiseStars"},409)
            if action=="deposit":
                # Spendable native balance is debited; a matching reserve is
                # represented by the user's withdrawable wallet balance.
                await economy.post(conn,"typevirt:"+key,[(uid,"stars",-amount)],"TypeVirt: пополнение кошелька")
                delta=amount
            elif action=="withdraw":
                await economy.post(conn,"typevirt:"+key,[(uid,"stars",amount)],"TypeVirt: вывод из кошелька")
                delta=-amount
            else:
                # A donation is irreversible after user confirmation, credited
                # to the actual native bot owner rather than minted.
                recipient=int(bot["owner_user_id"])
                await economy.post(conn,"typevirt:"+key,[(recipient,"stars",amount)],"TypeVirt: добровольная поддержка")
                await conn.execute("""INSERT INTO risegram_typevirt_donations(user_id,total) VALUES(%s,%s)
                    ON CONFLICT(user_id) DO UPDATE SET total=risegram_typevirt_donations.total+excluded.total""",(uid,amount))
                delta=-amount
            row = await (await conn.execute("UPDATE risegram_typevirt_wallet SET balance=balance+%s,updated_at=now() WHERE user_id=%s RETURNING balance",(delta,uid))).fetchone()
            native = await (await conn.execute("SELECT balance FROM stars_balances WHERE user_id=%s",(uid,))).fetchone()
            if not native:
                raise economy.WalletError("Нет активного кошелька RiseGram")
            await conn.execute("""INSERT INTO risegram_typevirt_wallet_receipts(command_key,user_id,action,amount,balance_after,native_after)
                VALUES(%s,%s,%s,%s,%s,%s)""",(key,uid,action,amount,row["balance"],native["balance"]))
            result={"ok":True,"balance":str(row["balance"]),"native_balance":str(native["balance"])}
        return reply(result)
    except economy.WalletError as exc:
        return reply({"ok":False,"error":str(exc)},409)
    except Exception:
        log.exception("TypeVirt wallet operation failed")
        return reply({"ok":False,"error":"Операция не подтверждена. Повторите с тем же запросом."},503)
