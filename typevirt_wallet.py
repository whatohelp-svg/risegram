"""TypeVirt internal RiseStars wallet. Internal virtual Stars only; no real money or external Stars.
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
        callback_id = body.get("callback_id","")
        if type(uid) is not int or not 0<uid<2**63 or action not in ("balance","deposit","withdraw","donate","gdep","gout","gdon","gimp"):
            raise ValueError()
        if action!="balance" and (type(amount) is not int or not 1<=amount<=MAX_AMOUNT or not isinstance(key,str) or not re.fullmatch(r"[A-Za-z0-9_-]{16,32}",key) or not isinstance(callback_id,str) or not re.fullmatch(r"-?[0-9]{1,19}",callback_id) or not -(2**63)<=int(callback_id)<2**63):
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
            # The bot token alone cannot authorize a debit. Verify the actual
            # native user's private callback and bind its exact amount/nonce.
            expected = f"tvw:{action}:{amount}:{key}".encode()
            confirmed = await (await conn.execute("""SELECT 1 FROM bot_api_updates
                WHERE bot_user_id=%s AND callback_query_id=%s AND callback_user_id=%s
                  AND update_kind='callback_query' AND peer_type='user' AND peer_id IN (%s,%s) AND callback_data=%s
                  AND created_at>now()-interval '10 minutes' LIMIT 1""",
                (BOT_ID,int(callback_id),uid,uid,BOT_ID,expected))).fetchone()
            if not confirmed:
                return reply({"ok":False,"error":"Подтверждение пользователя не найдено или устарело"},403)
            if action in ("withdraw","donate","gimp") and balance<amount:
                return reply({"ok":False,"error":"Недостаточно RiseStars в кошельке TypeVirt"},409)
            if action=="gdep":
                await economy.post(conn,"typevirt:"+key,[(uid,"stars",-amount)],"TypeVirt: пополнение игрового баланса")
                delta=0
            elif action=="gimp":
                # Existing separately deposited reserve is moved to gameplay
                # exactly once, with the local worker applying the receipt.
                delta=-amount
            elif action in ("gout","gdon"):
                # Virtual game awards are issued only on a confirmed withdrawal.
                # Bound the daily issuance per player across donations and payouts.
                issued=await (await conn.execute("""SELECT coalesce(sum(amount),0) AS n
                    FROM risegram_typevirt_wallet_receipts WHERE user_id=%s
                    AND action IN ('gout','gdon') AND created_at>=date_trunc('day',now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC'""",(uid,))).fetchone()
                if int(issued["n"])+amount>MAX_AMOUNT:
                    return reply({"ok":False,"error":"Дневной лимит вывода и донатов: 1 000 000 000 RiseStars"},409)
                recipient=uid if action=="gout" else int(bot["owner_user_id"])
                await conn.execute("INSERT INTO stars_balances(user_id,balance,granted) VALUES(%s,0,true) ON CONFLICT DO NOTHING",(recipient,))
                await economy.post(conn,"typevirt:"+key,[(recipient,"stars",amount)],"TypeVirt: игровые RiseStars" if action=="gout" else "TypeVirt: донат игровыми RiseStars")
                if action=="gdon":
                    await conn.execute("""INSERT INTO risegram_typevirt_donations(user_id,total) VALUES(%s,%s)
                        ON CONFLICT(user_id) DO UPDATE SET total=risegram_typevirt_donations.total+excluded.total""",(uid,amount))
                delta=0
            elif action=="deposit":
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
            if not native and action not in ("gimp","gdon"):
                raise economy.WalletError("Нет активного кошелька RiseGram")
            native_after=int(native["balance"]) if native else 0
            await conn.execute("""INSERT INTO risegram_typevirt_wallet_receipts(command_key,user_id,action,amount,balance_after,native_after)
                VALUES(%s,%s,%s,%s,%s,%s)""",(key,uid,action,amount,row["balance"],native_after))
            result={"ok":True,"balance":str(row["balance"]),"native_balance":str(native_after)}
        return reply(result)
    except economy.WalletError as exc:
        return reply({"ok":False,"error":str(exc)},409)
    except Exception:
        log.exception("TypeVirt wallet operation failed")
        return reply({"ok":False,"error":"Операция не подтверждена. Повторите с тем же запросом."},503)
