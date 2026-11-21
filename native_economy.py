"""Bot operations on the client's PostgreSQL ledgers. A receipt and all movements commit together."""
import os
from contextlib import asynccontextmanager
from decimal import Decimal
import time
import uuid

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb


INSTANCE = uuid.uuid4().hex


class WalletError(ValueError):
    pass


@asynccontextmanager
async def transaction():
    async with await psycopg.AsyncConnection.connect(os.environ["RISEGRAM_LEDGER_DSN"], row_factory=dict_row,
                                                     connect_timeout=8, client_encoding="UTF8") as conn:
        yield conn


async def setup():
    async with transaction() as conn:
        await conn.execute("""CREATE TABLE IF NOT EXISTS risegram_bot_receipts(
            key text PRIMARY KEY, movements jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now())""")
        await conn.execute("""CREATE TABLE IF NOT EXISTS risegram_bot_games(
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, user_id bigint NOT NULL, bot_account bigint NOT NULL,
            request_key text NOT NULL UNIQUE, currency text NOT NULL, stake bigint NOT NULL CHECK(stake>0),
            game text NOT NULL, status text NOT NULL, payout bigint NOT NULL DEFAULT 0, instance text NOT NULL,
            lease_until timestamptz NOT NULL DEFAULT now()+interval '3 minutes')""")
        await conn.execute("""CREATE UNIQUE INDEX IF NOT EXISTS risegram_one_active_game
                              ON risegram_bot_games(bot_account) WHERE status='active'""")


def units(currency, amount):
    n = Decimal(str(amount)) * (1 if currency == "stars" else 10**9)
    if currency not in ("stars", "gram") or not n.is_finite() or n != n.to_integral_value() or abs(n) >= 2**63:
        raise WalletError("Некорректная сумма.")
    return int(n)


async def account(user):
    # Native services apply their configured starting grant on first use.
    from api import native_wallet
    if not user or await native_wallet(user) is None:
        raise WalletError("Сначала войдите с этим номером в клиент RiseGram.")
    async with transaction() as conn:
        row = await (await conn.execute("SELECT id FROM users WHERE phone=%s AND deleted_at IS NULL",
                                        (user["phone_digits"],))).fetchone()
    if not row:
        raise WalletError("Аккаунт RiseGram не найден.")
    return row["id"]


async def post(conn, key, movements, title):
    """Each leg is (native user ID, currency, signed integer units)."""
    details = [list(m) for m in movements if m[2]]
    receipt = await conn.execute("""INSERT INTO risegram_bot_receipts(key,movements) VALUES(%s,%s)
                      ON CONFLICT DO NOTHING RETURNING key""", (key, Jsonb(details)))
    if not await receipt.fetchone():
        old = await (await conn.execute("SELECT movements FROM risegram_bot_receipts WHERE key=%s", (key,))).fetchone()
        if old["movements"] != details:
            raise WalletError("Параметры операции изменились.")
        return False
    for uid, cur, amount in sorted(details, key=lambda leg: (leg[0], leg[1] != "stars")):
        date = int(time.time())
        if cur == "stars":
            changed = await conn.execute("""UPDATE stars_balances SET balance=balance+%s,updated_at=now()
                         WHERE user_id=%s AND balance >= %s RETURNING balance""", (amount, uid, -amount))
            if not await changed.fetchone():
                raise WalletError("Недостаточно RiseStars.")
            await conn.execute("""INSERT INTO stars_transactions(user_id,amount,reason,title,date)
                                  VALUES(%s,%s,'adjust',%s,%s)""", (uid, amount, title, date))
        elif cur == "gram":
            changed = await conn.execute("""UPDATE ton_balances SET balance_nanoton=balance_nanoton+%s,updated_at=now()
                         WHERE user_id=%s AND balance_nanoton >= %s RETURNING balance_nanoton""", (amount, uid, -amount))
            if not await changed.fetchone():
                raise WalletError("Недостаточно GRAM.")
            await conn.execute("""INSERT INTO ton_transactions(user_id,amount_nanoton,reason,comment,date)
                                  VALUES(%s,%s,'adjust',%s,%s)""", (uid, amount, title, date))
        else:
            raise WalletError("Неизвестная валюта.")
    return True


async def book(key, movements, title):
    async with transaction() as conn:
        return await post(conn, key, movements, title)


async def start(user, game, currency, bet, key):
    uid = await account(user)
    stake = units(currency, bet)
    if stake <= 0:
        raise WalletError("Введите положительную ставку.")
    async with transaction() as conn:
        row = await (await conn.execute("""INSERT INTO risegram_bot_games
            (user_id,bot_account,request_key,currency,stake,game,status,instance)
            VALUES(%s,%s,%s,%s,%s,%s,'active',%s) ON CONFLICT DO NOTHING RETURNING id""",
            (uid, user["tg_id"], key, currency, stake, game, INSTANCE))).fetchone()
        if not row:
            raise WalletError("Эта ставка уже принята или у вас идёт игра.")
        await post(conn, "bet:"+key, [(uid, currency, -stake)], "Ставка · "+game)
        return row["id"]


async def settle(gid, won, payout, *, status=None, conn=None):
    if conn is None:
        async with transaction() as c:
            return await settle(gid, won, payout, status=status, conn=c)
    g = await (await conn.execute("SELECT * FROM risegram_bot_games WHERE id=%s FOR UPDATE", (gid,))).fetchone()
    if not g or g["status"] != "active":
        return False
    refund = status == "refunded"
    pay = g["stake"] if refund else units(g["currency"], payout) if won else 0
    if pay < 0:
        raise WalletError("Некорректная выплата.")
    if pay:
        await post(conn, "settle:"+g["request_key"], [(g["user_id"],g["currency"],pay)],
                   ("Возврат ставки · " if refund else "Выигрыш · ")+g["game"])
    await conn.execute("UPDATE risegram_bot_games SET status=%s,payout=%s WHERE id=%s",
                       ("refunded" if refund else "won" if won else "lost", pay, gid))
    return True


async def record_result(gid, won, payout, *, status=None):
    import database as db
    await db.execute("""INSERT INTO native_game_results(game_id,won,payout,refunded) VALUES(?,?,?,?)
                       ON CONFLICT DO NOTHING""", (gid, int(won), str(payout), int(status == "refunded")))
    return await deliver_result(gid)


async def deliver_result(gid):
    import database as db
    result = await db.fetchone("SELECT * FROM native_game_results WHERE game_id=?", (gid,))
    if not result:
        return False
    applied = await settle(gid, result["won"], result["payout"], status="refunded" if result["refunded"] else None)
    await db.execute("DELETE FROM native_game_results WHERE game_id=?", (gid,))
    return applied


async def maintain_games(live_ids):
    async with transaction() as conn:
        if live_ids:
            await conn.execute("""UPDATE risegram_bot_games SET lease_until=now()+interval '3 minutes'
                              WHERE instance=%s AND status='active' AND id=ANY(%s)""", (INSTANCE,list(live_ids)))
        rows = await (await conn.execute("""SELECT id FROM risegram_bot_games WHERE status='active'
                        AND lease_until<now() FOR UPDATE SKIP LOCKED""")).fetchall()
        for row in rows:
            await settle(row["id"],False,0,status="refunded",conn=conn)


async def queue(conn, key, movements, title, sale=None):
    import json
    await conn.execute("""INSERT INTO native_wallet_jobs(key,movements,title,sale) VALUES(?,?,?,?)
                       ON CONFLICT(key) DO NOTHING""", (key,json.dumps(movements),title,sale))


async def deliver(key):
    import json
    import database as db
    # Serialize local finalization with administrators and other workers. A crash after
    # the native commit replays the same immutable receipt before releasing the item.
    async with db.connect() as conn:
        await conn.execute("BEGIN IMMEDIATE")
        job = await (await conn.execute("SELECT * FROM native_wallet_jobs WHERE key=?", (key,))).fetchone()
        if not job or job["status"] == "done":
            return True
        if job["status"] == "declined":
            return False
        ok = True
        try:
            await book(key,json.loads(job["movements"]),job["title"])
        except WalletError:
            if not job["sale"]:
                raise
            ok = False
        if job["sale"]:
            row = await (await conn.execute("SELECT * FROM market_listings WHERE id=? AND status='processing'",
                                             (job["sale"],))).fetchone()
            if not row:
                raise RuntimeError("market reservation missing")
            await conn.execute("UPDATE user_nft_inventory SET tg_id=? WHERE id=? AND tg_id=?",
                               (row["buyer_id"] if ok else row["seller_id"],row["item_id"],-row["id"]))
            await conn.execute("UPDATE market_listings SET status=?,sold_at=?,buyer_id=? WHERE id=?",
                               ("sold" if ok else "active",db.now() if ok else None,row["buyer_id"] if ok else None,row["id"]))
        await conn.execute("UPDATE native_wallet_jobs SET status=? WHERE key=?", ("done" if ok else "declined",key))
        await conn.commit()
        return ok
