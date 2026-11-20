"""database.py — SQLite (aiosqlite) для RiseGram Ecosystem.

Старая база с Railway подхватывается: недостающие колонки добавляются автоматически (init_db).
Колонка purchases.price_xtr называется так исторически — это просто сумма в Telegram Stars.
"""
from __future__ import annotations

import contextlib
import logging
import random
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

import aiosqlite

import config
from config import DB_PATH

log = logging.getLogger("risegram.db")

# ─────────────────────────────── утилиты ────────────────────────────────────


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def now_plus(seconds: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).strftime("%Y-%m-%d %H:%M:%S")


def norm_username(u: str | None) -> str:
    return (u or "").strip().lstrip("@").lower()


def digits_of(s: str | None) -> str:
    return "".join(ch for ch in (s or "") if ch.isdigit())


def fmt_number(d: str) -> str:
    """Красивый вид номера по цифрам."""
    d = digits_of(d)
    if d.startswith("888"):
        rest = d[3:]
        if len(rest) == 4 and rest[0] == "8":
            return f"+888 8 {rest[1:]}"
        if len(rest) == 8 and rest[0] == "0":
            return f"+888 0{rest[1:4]} {rest[4:]}"
        groups = [rest[i:i + 4] for i in range(0, len(rest), 4)]
        return "+888 " + " ".join(groups)
    if d.startswith("7") and len(d) == 11:
        return f"+7 {d[1:4]} {d[4:7]}-{d[7:9]}-{d[9:11]}"
    if d.startswith("1") and len(d) == 11:
        return f"+1 ({d[1:4]}) {d[4:7]}-{d[7:11]}"
    return "+" + d


def gen_phone_digits() -> str:
    return "77" + "".join(str(random.randint(0, 9)) for _ in range(9))


def gen_us_digits() -> str:
    area = random.randint(201, 989)
    return f"1{area}{random.randint(200, 999)}{random.randint(0, 9999):04d}"


def gen_888_digits(fmt: str) -> str:
    f = config.NUMBER_FORMATS[fmt]
    return "888" + f["prefix"] + "".join(str(random.randint(0, 9)) for _ in range(f["digits"]))


def shown_username(user: dict | None) -> str:
    if not user:
        return "—"
    name = user.get("active_username") or user.get("username")
    if name:
        return "@" + norm_username(name)
    return f"id{user.get('tg_id')}"


# ─────────────────────────────── подключение ────────────────────────────────


@contextlib.asynccontextmanager
async def connect():
    db = await aiosqlite.connect(DB_PATH, timeout=30)
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA busy_timeout=30000")
    try:
        yield db
    finally:
        await db.close()


def _d(row) -> dict | None:
    return dict(row) if row is not None else None


async def fetchone(sql: str, params: Iterable[Any] = ()) -> dict | None:
    async with connect() as db:
        cur = await db.execute(sql, tuple(params))
        row = await cur.fetchone()
        await cur.close()
    return _d(row)


async def fetchall(sql: str, params: Iterable[Any] = ()) -> list[dict]:
    async with connect() as db:
        cur = await db.execute(sql, tuple(params))
        rows = await cur.fetchall()
        await cur.close()
    return [dict(r) for r in rows]


async def scalar(sql: str, params: Iterable[Any] = (), default: Any = 0) -> Any:
    async with connect() as db:
        cur = await db.execute(sql, tuple(params))
        row = await cur.fetchone()
        await cur.close()
    if row is None or row[0] is None:
        return default
    return row[0]


async def execute(sql: str, params: Iterable[Any] = ()) -> int:
    async with connect() as db:
        cur = await db.execute(sql, tuple(params))
        await db.commit()
        return cur.rowcount


# ─────────────────────────────── схема ──────────────────────────────────────

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS users (
        tg_id INTEGER PRIMARY KEY,
        rise_id INTEGER,
        name TEXT,
        username TEXT,
        phone TEXT,
        phone_digits TEXT,
        risestars REAL NOT NULL DEFAULT 0,
        gram_balance REAL NOT NULL DEFAULT 0,
        banned INTEGER NOT NULL DEFAULT 0,
        verified INTEGER NOT NULL DEFAULT 0,
        auth_code TEXT,
        auth_code_expires TEXT,
        active_username TEXT,
        gift_mode TEXT NOT NULL DEFAULT 'real',
        last_bonus_at TEXT,
        dox_claimed INTEGER NOT NULL DEFAULT 0,
        created_at TEXT,
        last_seen TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS testers (
        tg_id INTEGER PRIMARY KEY,
        username TEXT,
        added_at TEXT,
        added_by INTEGER
    )""",
    """CREATE TABLE IF NOT EXISTS admin_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        admin_id INTEGER,
        action_text TEXT,
        created_at TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS user_nft_inventory (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tg_id INTEGER NOT NULL,
        item_type TEXT NOT NULL DEFAULT 'username',
        value TEXT,
        price_stars INTEGER DEFAULT 0,
        created_at TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS purchases (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tg_id INTEGER NOT NULL,
        item_type TEXT,
        item_desc TEXT,
        price_xtr INTEGER DEFAULT 0,
        charge_id TEXT,
        payload TEXT,
        status TEXT DEFAULT 'done',
        created_at TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS tasks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT,
        description TEXT,
        reward REAL DEFAULT 0,
        channel TEXT,
        max_claims INTEGER DEFAULT 0,
        claims_count INTEGER DEFAULT 0,
        active INTEGER DEFAULT 1,
        created_at TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS task_claims (
        task_id INTEGER NOT NULL,
        tg_id INTEGER NOT NULL,
        claimed_at TEXT,
        PRIMARY KEY (task_id, tg_id)
    )""",
    """CREATE TABLE IF NOT EXISTS feature_flags (
        feature_name TEXT PRIMARY KEY,
        is_enabled INTEGER NOT NULL DEFAULT 1
    )""",
    """CREATE TABLE IF NOT EXISTS scam_requests (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        requester_id INTEGER NOT NULL,
        kind TEXT,
        target TEXT,
        reason TEXT,
        status TEXT DEFAULT 'unpaid',
        price INTEGER DEFAULT 0,
        charge_id TEXT,
        created_at TEXT,
        resolved_at TEXT,
        resolved_by INTEGER
    )""",
    """CREATE TABLE IF NOT EXISTS verified_entities (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        owner_id INTEGER NOT NULL,
        kind TEXT,
        target TEXT,
        created_at TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS reservations (
        kind TEXT NOT NULL,
        value TEXT NOT NULL,
        tg_id INTEGER NOT NULL,
        expires_at TEXT,
        PRIMARY KEY (kind, value)
    )""",
    """CREATE TABLE IF NOT EXISTS auth_codes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tg_id INTEGER NOT NULL,
        code TEXT NOT NULL,
        created_at TEXT,
        expires_at TEXT,
        used INTEGER DEFAULT 0,
        attempts INTEGER DEFAULT 0
    )""",
    """CREATE TABLE IF NOT EXISTS sessions (
        token TEXT PRIMARY KEY,
        tg_id INTEGER NOT NULL,
        created_at TEXT,
        expires_at TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS legacy_numbers (
        phone_digits TEXT PRIMARY KEY,
        claimed_by INTEGER,
        created_at TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS games (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tg_id INTEGER NOT NULL,
        kind TEXT,
        bet REAL,
        crash_point REAL,
        status TEXT DEFAULT 'active',
        payout REAL DEFAULT 0,
        currency TEXT DEFAULT 'gram',
        created_at TEXT,
        finished_at TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS transfers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        from_id INTEGER,
        to_id INTEGER,
        amount REAL,
        fee REAL,
        created_at TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS market_listings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        seller_id INTEGER NOT NULL,
        item_id INTEGER NOT NULL,
        item_type TEXT NOT NULL,
        value TEXT NOT NULL,
        price REAL NOT NULL,
        status TEXT NOT NULL DEFAULT 'active',
        buyer_id INTEGER,
        created_at TEXT,
        sold_at TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS meta (
        key TEXT PRIMARY KEY,
        value TEXT
    )""",
]

# Колонки, которые должны быть, даже если таблица осталась от старой версии бота.
COLUMNS: dict[str, dict[str, str]] = {
    "users": {
        "rise_id": "INTEGER", "name": "TEXT", "username": "TEXT", "phone": "TEXT",
        "phone_digits": "TEXT", "risestars": "REAL DEFAULT 0", "gram_balance": "REAL DEFAULT 0",
        "banned": "INTEGER DEFAULT 0", "verified": "INTEGER DEFAULT 0", "auth_code": "TEXT",
        "auth_code_expires": "TEXT", "active_username": "TEXT", "gift_mode": "TEXT DEFAULT 'real'",
        "last_bonus_at": "TEXT", "dox_claimed": "INTEGER DEFAULT 0", "created_at": "TEXT",
        "last_seen": "TEXT",
    },
    "testers": {"username": "TEXT", "added_at": "TEXT", "added_by": "INTEGER"},
    "admin_logs": {"admin_id": "INTEGER", "action_text": "TEXT", "created_at": "TEXT"},
    "user_nft_inventory": {
        "item_type": "TEXT DEFAULT 'username'", "value": "TEXT", "price_stars": "INTEGER DEFAULT 0",
        "created_at": "TEXT",
    },
    "purchases": {
        "item_type": "TEXT", "item_desc": "TEXT", "price_xtr": "INTEGER DEFAULT 0",
        "charge_id": "TEXT", "payload": "TEXT", "status": "TEXT DEFAULT 'done'", "created_at": "TEXT",
    },
    "tasks": {
        "title": "TEXT", "description": "TEXT", "reward": "REAL DEFAULT 0", "channel": "TEXT",
        "max_claims": "INTEGER DEFAULT 0", "claims_count": "INTEGER DEFAULT 0",
        "active": "INTEGER DEFAULT 1", "created_at": "TEXT",
    },
    "feature_flags": {"is_enabled": "INTEGER DEFAULT 1"},
    "games": {"currency": "TEXT DEFAULT 'gram'"},
}

INDEXES = [
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_users_rise_id ON users(rise_id)",
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_users_phone_digits ON users(phone_digits)",
    "CREATE INDEX IF NOT EXISTS ix_users_username ON users(username)",
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_nft_item ON user_nft_inventory(item_type, value)",
    "CREATE INDEX IF NOT EXISTS ix_nft_owner ON user_nft_inventory(tg_id)",
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_purchases_charge ON purchases(charge_id) WHERE charge_id IS NOT NULL",
    "CREATE INDEX IF NOT EXISTS ix_purchases_user ON purchases(tg_id)",
    "CREATE INDEX IF NOT EXISTS ix_codes_user ON auth_codes(tg_id)",
    "CREATE INDEX IF NOT EXISTS ix_games_user ON games(tg_id, status)",
    "CREATE INDEX IF NOT EXISTS ix_scam_status ON scam_requests(status)",
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_market_item ON market_listings(item_id) WHERE status='active'",
    "CREATE INDEX IF NOT EXISTS ix_market_status ON market_listings(status, item_type, price)",
]


async def init_db() -> None:
    async with connect() as db:
        await db.execute("PRAGMA journal_mode=WAL")
        for stmt in SCHEMA:
            await db.execute(stmt)
        for table, cols in COLUMNS.items():
            cur = await db.execute(f"PRAGMA table_info({table})")
            have = {r["name"] for r in await cur.fetchall()}
            await cur.close()
            for col, decl in cols.items():
                if col not in have:
                    await db.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
        await db.commit()

        # Дозаполняем rise_id / phone_digits у старых пользователей.
        cur = await db.execute("SELECT tg_id, phone, rise_id, phone_digits FROM users")
        rows = await cur.fetchall()
        await cur.close()
        used_rise = {r["rise_id"] for r in rows if r["rise_id"]}
        used_phone = {r["phone_digits"] for r in rows if r["phone_digits"]}
        seen_rise: set = set()
        for r in rows:
            rid, pd = r["rise_id"], r["phone_digits"]
            if not rid or rid in seen_rise:  # пусто или дубликат
                rid = _new_rise_id(used_rise)
                used_rise.add(rid)
            seen_rise.add(rid)
            if not pd:
                pd = digits_of(r["phone"])
                if not pd or pd in used_phone:
                    pd = _new_phone(used_phone)
                used_phone.add(pd)
            await db.execute(
                "UPDATE users SET rise_id=?, phone_digits=?, phone=COALESCE(NULLIF(phone,''), ?) WHERE tg_id=?",
                (rid, pd, fmt_number(pd), r["tg_id"]),
            )
        await db.execute("UPDATE users SET created_at=? WHERE created_at IS NULL", (now(),))
        await db.commit()

        for stmt in INDEXES:
            try:
                await db.execute(stmt)
            except sqlite3.DatabaseError as e:
                log.warning("index skipped (%s): %s", e, stmt)
        await db.commit()

        for name in config.FEATURES:
            await db.execute("INSERT OR IGNORE INTO feature_flags (feature_name, is_enabled) VALUES (?, 1)", (name,))
        # Игры, прерванные перезапуском: возвращаем ставку.
        cur = await db.execute("SELECT id, tg_id, bet, COALESCE(currency,'gram') AS currency FROM games WHERE status='active'")
        stuck = await cur.fetchall()
        await cur.close()
        for g in stuck:
            await db.execute("UPDATE games SET status='refunded', finished_at=? WHERE id=?", (now(), g["id"]))
            col = "risestars" if g["currency"] == "stars" else "gram_balance"
            await db.execute(f"UPDATE users SET {col}=ROUND({col}+?,2) WHERE tg_id=?", (g["bet"], g["tg_id"]))
        if stuck:
            log.info("refunded %d interrupted games", len(stuck))
        await db.execute("DELETE FROM reservations WHERE expires_at < ?", (now(),))
        await db.execute("UPDATE market_listings SET status='cancelled' WHERE status='active' AND item_id NOT IN (SELECT id FROM user_nft_inventory)")
        await db.commit()
    await ensure_owner()


def _new_rise_id(used: set) -> int:
    while True:
        v = random.randint(100_000_000, 999_999_999)
        if v not in used:
            return v


def _new_phone(used: set) -> str:
    while True:
        v = gen_phone_digits()
        if v not in used:
            return v


# ─────────────────────────────── пользователи ───────────────────────────────


async def get_user(tg_id: int) -> dict | None:
    return await fetchone("SELECT * FROM users WHERE tg_id=?", (tg_id,))


async def get_or_create_user(tg_id: int, name: str, username: str | None) -> dict:
    uname = norm_username(username) or None
    for _ in range(10):
        user = await get_user(tg_id)
        if user:
            if tg_id == config.OWNER_ID:
                await ensure_owner()
                user = await get_user(tg_id) or user
            await execute(
                "UPDATE users SET name=?, username=?, last_seen=? WHERE tg_id=?",
                (name, uname or user.get("username"), now(), tg_id),
            )
            user["name"], user["username"] = name, uname or user.get("username")
            return user
        used_rise = {r["rise_id"] for r in await fetchall("SELECT rise_id FROM users")}
        digits = gen_phone_digits()
        try:
            async with connect() as db:
                await db.execute(
                    """INSERT INTO users (tg_id, rise_id, name, username, phone, phone_digits,
                                          risestars, gram_balance, created_at, last_seen)
                       VALUES (?,?,?,?,?,?,0,0,?,?)""",
                    (tg_id, _new_rise_id(used_rise), name, uname, fmt_number(digits), digits, now(), now()),
                )
                await db.commit()
        except sqlite3.IntegrityError:
            continue
    raise RuntimeError("cannot create user")


async def find_user(query: str) -> dict | None:
    q = (query or "").strip()
    if not q:
        return None
    if q.startswith("@") or any(ch.isalpha() for ch in q) or not digits_of(q):
        return await fetchone(
            "SELECT * FROM users WHERE lower(replace(username,'@',''))=? OR lower(replace(active_username,'@',''))=? LIMIT 1",
            (norm_username(q), norm_username(q)),
        )
    d = digits_of(q)
    if len(d) <= 12:
        row = await fetchone("SELECT * FROM users WHERE tg_id=?", (int(d),))
        if row:
            return row
        row = await fetchone("SELECT * FROM users WHERE rise_id=?", (int(d),))
        if row:
            return row
    return await fetchone("SELECT * FROM users WHERE phone_digits=?", (d,))


async def count_users(search: str = "") -> int:
    if search:
        like = f"%{norm_username(search)}%"
        return await scalar(
            "SELECT COUNT(*) FROM users WHERE lower(COALESCE(username,'')) LIKE ? OR lower(COALESCE(name,'')) LIKE ?",
            (like, f"%{search.lower()}%"),
        )
    return await scalar("SELECT COUNT(*) FROM users")


async def list_users(limit: int = 8, offset: int = 0, search: str = "") -> list[dict]:
    if search:
        like = f"%{norm_username(search)}%"
        return await fetchall(
            """SELECT * FROM users WHERE lower(COALESCE(username,'')) LIKE ? OR lower(COALESCE(name,'')) LIKE ?
               ORDER BY created_at DESC, tg_id DESC LIMIT ? OFFSET ?""",
            (like, f"%{search.lower()}%", limit, offset),
        )
    return await fetchall("SELECT * FROM users ORDER BY created_at DESC, tg_id DESC LIMIT ? OFFSET ?", (limit, offset))


async def all_user_ids(only_active: bool = True) -> list[int]:
    sql = "SELECT tg_id FROM users" + (" WHERE banned=0" if only_active else "")
    return [r["tg_id"] for r in await fetchall(sql)]


async def set_banned(tg_id: int, banned: bool) -> None:
    await execute("UPDATE users SET banned=? WHERE tg_id=?", (1 if banned else 0, tg_id))


async def set_verified(tg_id: int, verified: bool) -> None:
    await execute("UPDATE users SET verified=? WHERE tg_id=?", (1 if verified else 0, tg_id))


async def set_gift_mode(tg_id: int, mode: str) -> None:
    await execute("UPDATE users SET gift_mode=? WHERE tg_id=?", (mode, tg_id))


async def delete_user(tg_id: int) -> None:
    async with connect() as db:
        await db.execute("UPDATE market_listings SET status='cancelled' WHERE seller_id=? AND status='active'", (tg_id,))
        for table, col in [
            ("users", "tg_id"), ("user_nft_inventory", "tg_id"),
            ("task_claims", "tg_id"), ("auth_codes", "tg_id"), ("sessions", "tg_id"),
            ("reservations", "tg_id"), ("games", "tg_id"), ("testers", "tg_id"),
        ]:
            await db.execute(f"DELETE FROM {table} WHERE {col}=?", (tg_id,))
        await db.commit()


async def set_phone(tg_id: int, digits: str) -> bool:
    """Меняет текущий номер. False — номер занят другим."""
    digits = digits_of(digits)
    try:
        async with connect() as db:
            cur = await db.execute(
                "UPDATE users SET phone=?, phone_digits=? WHERE tg_id=?", (fmt_number(digits), digits, tg_id)
            )
            await db.commit()
            return cur.rowcount > 0
    except sqlite3.IntegrityError:
        return False


# ─────────────────────────────── баланс ─────────────────────────────────────


async def add_balance(tg_id: int, col: str, delta: float, *, allow_negative: bool = False) -> float | None:
    """Атомарно меняет баланс. None — пользователя нет или не хватает средств."""
    if col not in ("risestars", "gram_balance"):
        raise ValueError(col)
    async with connect() as db:
        if delta < 0 and not allow_negative:
            cur = await db.execute(
                f"UPDATE users SET {col}=ROUND({col}+?,2) WHERE tg_id=? AND {col}>=?", (delta, tg_id, -delta)
            )
        else:
            cur = await db.execute(f"UPDATE users SET {col}=ROUND({col}+?,2) WHERE tg_id=?", (delta, tg_id))
        if cur.rowcount == 0:
            await db.rollback()
            return None
        cur = await db.execute(f"SELECT {col} FROM users WHERE tg_id=?", (tg_id,))
        row = await cur.fetchone()
        await db.commit()
        return row[0]


async def set_balance(tg_id: int, col: str, value: float) -> None:
    if col not in ("risestars", "gram_balance"):
        raise ValueError(col)
    await execute(f"UPDATE users SET {col}=? WHERE tg_id=?", (value, tg_id))


async def transfer_risestars(from_id: int, to_id: int, amount: float, fee_percent: float) -> tuple[bool, str]:
    fee = round(amount * fee_percent / 100, 2)
    async with connect() as db:
        cur = await db.execute(
            "UPDATE users SET risestars=ROUND(risestars-?,2) WHERE tg_id=? AND risestars>=?",
            (amount + fee, from_id, amount + fee),
        )
        if cur.rowcount == 0:
            await db.rollback()
            return False, "Недостаточно RiseStars."
        cur = await db.execute("UPDATE users SET risestars=ROUND(risestars+?,2) WHERE tg_id=?", (amount, to_id))
        if cur.rowcount == 0:
            await db.rollback()
            return False, "Получатель не найден."
        await db.execute(
            "INSERT INTO transfers (from_id,to_id,amount,fee,created_at) VALUES (?,?,?,?,?)",
            (from_id, to_id, amount, fee, now()),
        )
        await db.commit()
    return True, "ok"


# ─────────────────────────────── флаги и мета ───────────────────────────────


async def is_feature_on(name: str) -> bool:
    row = await fetchone("SELECT is_enabled FROM feature_flags WHERE feature_name=?", (name,))
    return True if row is None else bool(row["is_enabled"])


async def get_flags() -> list[dict]:
    return await fetchall("SELECT * FROM feature_flags ORDER BY feature_name")


async def toggle_flag(name: str) -> bool:
    row = await fetchone("SELECT is_enabled FROM feature_flags WHERE feature_name=?", (name,))
    new = 0 if (row is None or row["is_enabled"]) else 1
    await execute(
        "INSERT INTO feature_flags (feature_name,is_enabled) VALUES (?,?) "
        "ON CONFLICT(feature_name) DO UPDATE SET is_enabled=excluded.is_enabled",
        (name, new),
    )
    return bool(new)


async def get_meta(key: str, default: str | None = None) -> str | None:
    row = await fetchone("SELECT value FROM meta WHERE key=?", (key,))
    return row["value"] if row else default


async def set_meta(key: str, value: str) -> None:
    await execute(
        "INSERT INTO meta (key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value)
    )


async def log_admin_action(admin_id: int, text: str) -> None:
    await execute("INSERT INTO admin_logs (admin_id, action_text, created_at) VALUES (?,?,?)", (admin_id, text, now()))


async def get_logs(limit: int = 10, offset: int = 0) -> list[dict]:
    return await fetchall("SELECT * FROM admin_logs ORDER BY id DESC LIMIT ? OFFSET ?", (limit, offset))


# ─────────────────────────────── коды входа ─────────────────────────────────


async def issue_auth_code(tg_id: int) -> str:
    code = "".join(str(secrets.randbelow(10)) for _ in range(config.AUTH_CODE_LENGTH))
    exp = now_plus(config.AUTH_CODE_TTL_SECONDS)
    async with connect() as db:
        await db.execute("UPDATE auth_codes SET used=1 WHERE tg_id=? AND used=0", (tg_id,))
        await db.execute(
            "INSERT INTO auth_codes (tg_id, code, created_at, expires_at) VALUES (?,?,?,?)", (tg_id, code, now(), exp)
        )
        await db.execute("UPDATE users SET auth_code=?, auth_code_expires=? WHERE tg_id=?", (code, exp, tg_id))
        await db.commit()
    return code


async def get_active_code(tg_id: int) -> dict | None:
    return await fetchone(
        "SELECT * FROM auth_codes WHERE tg_id=? AND used=0 AND expires_at>? ORDER BY id DESC LIMIT 1", (tg_id, now())
    )


async def verify_auth_code(phone_digits: str, code: str) -> dict | None:
    """Проверяет код входа для номера. Успех — возвращает пользователя и гасит код."""
    user = await fetchone("SELECT * FROM users WHERE phone_digits=?", (digits_of(phone_digits),))
    if not user or user["banned"]:
        return None
    row = await get_active_code(user["tg_id"])
    if not row:
        return None
    if row["attempts"] >= 5:
        await execute("UPDATE auth_codes SET used=1 WHERE id=?", (row["id"],))
        return None
    if not secrets.compare_digest(str(row["code"]), str(code).strip()):
        await execute("UPDATE auth_codes SET attempts=attempts+1 WHERE id=?", (row["id"],))
        return None
    await execute("UPDATE auth_codes SET used=1 WHERE id=?", (row["id"],))
    await execute("UPDATE users SET auth_code=NULL, auth_code_expires=NULL WHERE tg_id=?", (user["tg_id"],))
    return user


async def create_session(tg_id: int, days: int = 30) -> str:
    token = secrets.token_urlsafe(32)
    await execute(
        "INSERT INTO sessions (token, tg_id, created_at, expires_at) VALUES (?,?,?,?)",
        (token, tg_id, now(), now_plus(days * 86400)),
    )
    return token


async def get_session_user(token: str) -> dict | None:
    row = await fetchone("SELECT tg_id FROM sessions WHERE token=? AND expires_at>?", (token, now()))
    return await get_user(row["tg_id"]) if row else None


# ─────────────────────────────── покупки ────────────────────────────────────


async def purchase_exists(charge_id: str) -> bool:
    return bool(await scalar("SELECT COUNT(*) FROM purchases WHERE charge_id=?", (charge_id,)))


async def add_purchase(
    tg_id: int, item_type: str, desc: str, stars: int, charge_id: str | None, payload: str, status: str = "done"
) -> int:
    async with connect() as db:
        cur = await db.execute(
            """INSERT INTO purchases (tg_id,item_type,item_desc,price_xtr,charge_id,payload,status,created_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (tg_id, item_type, desc, stars, charge_id, payload, status, now()),
        )
        await db.commit()
        return cur.lastrowid


async def list_purchases(tg_id: int, limit: int = 10) -> list[dict]:
    return await fetchall("SELECT * FROM purchases WHERE tg_id=? ORDER BY id DESC LIMIT ?", (tg_id, limit))


async def get_purchase(pid: int) -> dict | None:
    return await fetchone("SELECT * FROM purchases WHERE id=?", (pid,))


async def set_purchase_status(pid: int, status: str) -> None:
    await execute("UPDATE purchases SET status=? WHERE id=?", (status, pid))


async def recent_purchases(limit: int = 10, offset: int = 0) -> list[dict]:
    return await fetchall("SELECT * FROM purchases ORDER BY id DESC LIMIT ? OFFSET ?", (limit, offset))


# ─────────────────────────────── резервы ────────────────────────────────────


async def reserve(kind: str, value: str, tg_id: int) -> bool:
    exp = now_plus(config.RESERVE_MINUTES * 60)
    async with connect() as db:
        await db.execute("DELETE FROM reservations WHERE expires_at < ?", (now(),))
        cur = await db.execute(
            "INSERT OR IGNORE INTO reservations (kind,value,tg_id,expires_at) VALUES (?,?,?,?)", (kind, value, tg_id, exp)
        )
        if cur.rowcount == 0:
            cur = await db.execute(
                "UPDATE reservations SET expires_at=? WHERE kind=? AND value=? AND tg_id=?", (exp, kind, value, tg_id)
            )
            ok = cur.rowcount > 0
        else:
            ok = True
        await db.commit()
    return ok


async def release(kind: str, value: str) -> None:
    await execute("DELETE FROM reservations WHERE kind=? AND value=?", (kind, value))


async def reserved_by_other(kind: str, value: str, tg_id: int) -> bool:
    row = await fetchone("SELECT tg_id FROM reservations WHERE kind=? AND value=? AND expires_at>?", (kind, value, now()))
    return bool(row and row["tg_id"] != tg_id)


# ─────────────────────────────── NFT и номера ───────────────────────────────


async def number_taken(digits: str) -> bool:
    d = digits_of(digits)
    if await scalar("SELECT COUNT(*) FROM users WHERE phone_digits=?", (d,)):
        return True
    if await scalar("SELECT COUNT(*) FROM user_nft_inventory WHERE item_type='number' AND value=?", (d,)):
        return True
    return bool(await scalar("SELECT COUNT(*) FROM legacy_numbers WHERE phone_digits=?", (d,)))


async def username_taken(name: str, *, ignore_reserved: bool = False, exclude_user: int | None = None) -> bool:
    """Занят ли юзернейм. ignore_reserved — для админа (может выдать и «rise»), exclude_user — не считать его собственный."""
    n = norm_username(name)
    if not ignore_reserved and config.is_reserved_username(n):
        return True
    if await scalar("SELECT COUNT(*) FROM user_nft_inventory WHERE item_type='username' AND value=?", (n,)):
        return True
    return bool(
        await scalar(
            "SELECT COUNT(*) FROM users WHERE lower(replace(username,'@',''))=? AND tg_id!=?", (n, exclude_user or 0)
        )
    )


async def add_nft(tg_id: int, item_type: str, value: str, price: int = 0) -> bool:
    try:
        async with connect() as db:
            await db.execute(
                "INSERT INTO user_nft_inventory (tg_id,item_type,value,price_stars,created_at) VALUES (?,?,?,?,?)",
                (tg_id, item_type, value, price, now()),
            )
            await db.commit()
        return True
    except sqlite3.IntegrityError:
        return False


async def list_nft(tg_id: int, item_type: str | None = None) -> list[dict]:
    if item_type:
        return await fetchall(
            "SELECT * FROM user_nft_inventory WHERE tg_id=? AND item_type=? ORDER BY id DESC", (tg_id, item_type)
        )
    return await fetchall("SELECT * FROM user_nft_inventory WHERE tg_id=? ORDER BY id DESC", (tg_id,))


async def get_nft(item_id: int) -> dict | None:
    return await fetchone("SELECT * FROM user_nft_inventory WHERE id=?", (item_id,))


async def delete_nft(item_id: int) -> None:
    await execute("UPDATE market_listings SET status='cancelled' WHERE item_id=? AND status='active'", (item_id,))
    await execute("DELETE FROM user_nft_inventory WHERE id=?", (item_id,))


async def set_active_username(tg_id: int, value: str | None) -> None:
    await execute("UPDATE users SET active_username=? WHERE tg_id=?", (value, tg_id))


async def grant_number_to_user(tg_id: int, digits: str, price: int = 0, make_active: bool = True) -> bool:
    """Записывает номер в инвентарь и (по желанию) делает текущим. Старый номер остаётся у пользователя."""
    digits = digits_of(digits)
    user = await get_user(tg_id)
    if not user:
        return False
    old = user.get("phone_digits")
    if not await add_nft(tg_id, "number", digits, price):
        return False
    if old and old != digits:
        await add_nft(tg_id, "number", old, 0)  # вернуть старый можно в «Мои NFT и номера»
    if make_active:
        return await set_phone(tg_id, digits)
    return True


async def add_legacy_number(digits: str) -> bool:
    try:
        await execute("INSERT INTO legacy_numbers (phone_digits, created_at) VALUES (?,?)", (digits_of(digits), now()))
        return True
    except sqlite3.IntegrityError:
        return False


async def claim_legacy_number(digits: str, tg_id: int) -> bool:
    d = digits_of(digits)
    async with connect() as db:
        cur = await db.execute(
            "UPDATE legacy_numbers SET claimed_by=? WHERE phone_digits=? AND claimed_by IS NULL", (tg_id, d)
        )
        await db.commit()
        if cur.rowcount == 0:
            return False
    ok = await grant_number_to_user(tg_id, d, 0, True)
    if not ok:
        await execute("UPDATE legacy_numbers SET claimed_by=NULL WHERE phone_digits=?", (d,))
    return ok


async def list_legacy(limit: int = 20) -> list[dict]:
    return await fetchall("SELECT * FROM legacy_numbers ORDER BY created_at DESC LIMIT ?", (limit,))


# ─────────────────────────────── верификация и SCAM ─────────────────────────


async def add_verified_entity(owner_id: int, kind: str, target: str) -> None:
    await execute(
        "INSERT INTO verified_entities (owner_id,kind,target,created_at) VALUES (?,?,?,?)",
        (owner_id, kind, norm_username(target), now()),
    )


async def create_scam_request(requester_id: int, kind: str, target: str, reason: str, price: int) -> int:
    async with connect() as db:
        cur = await db.execute(
            "INSERT INTO scam_requests (requester_id,kind,target,reason,status,price,created_at) VALUES (?,?,?,?, 'unpaid',?,?)",
            (requester_id, kind, norm_username(target), reason, price, now()),
        )
        await db.commit()
        return cur.lastrowid


async def get_scam_request(rid: int) -> dict | None:
    return await fetchone("SELECT * FROM scam_requests WHERE id=?", (rid,))


async def mark_scam_paid(rid: int, charge_id: str) -> bool:
    n = await execute(
        "UPDATE scam_requests SET status='pending', charge_id=? WHERE id=? AND status='unpaid'", (charge_id, rid)
    )
    return n > 0


async def list_scam(status: str = "pending", limit: int = 20, offset: int = 0) -> list[dict]:
    return await fetchall(
        "SELECT * FROM scam_requests WHERE status=? ORDER BY id DESC LIMIT ? OFFSET ?", (status, limit, offset)
    )


async def resolve_scam(rid: int, status: str, admin_id: int) -> bool:
    n = await execute(
        "UPDATE scam_requests SET status=?, resolved_at=?, resolved_by=? WHERE id=? AND status='pending'",
        (status, now(), admin_id, rid),
    )
    return n > 0


async def is_scam_target(target: str | None) -> bool:
    t = norm_username(target)
    if not t:
        return False
    return bool(await scalar("SELECT COUNT(*) FROM scam_requests WHERE status='approved' AND target=?", (t,)))


async def revoke_scam_label(target: str) -> int:
    return await execute(
        "UPDATE scam_requests SET status='revoked' WHERE status='approved' AND target=?", (norm_username(target),)
    )


# ─────────────────────────────── задания ────────────────────────────────────


async def create_task(title: str, description: str, reward: float, channel: str | None, max_claims: int) -> int:
    async with connect() as db:
        cur = await db.execute(
            "INSERT INTO tasks (title,description,reward,channel,max_claims,claims_count,active,created_at) VALUES (?,?,?,?,?,0,1,?)",
            (title, description, reward, channel, max_claims, now()),
        )
        await db.commit()
        return cur.lastrowid


async def list_tasks(active_only: bool = True) -> list[dict]:
    sql = "SELECT * FROM tasks" + (" WHERE active=1" if active_only else "") + " ORDER BY id DESC"
    return await fetchall(sql)


async def get_task(task_id: int) -> dict | None:
    return await fetchone("SELECT * FROM tasks WHERE id=?", (task_id,))


async def delete_task(task_id: int) -> None:
    async with connect() as db:
        await db.execute("DELETE FROM tasks WHERE id=?", (task_id,))
        await db.execute("DELETE FROM task_claims WHERE task_id=?", (task_id,))
        await db.commit()


async def toggle_task(task_id: int) -> None:
    await execute("UPDATE tasks SET active=1-COALESCE(active,0) WHERE id=?", (task_id,))


async def task_claimed(task_id: int, tg_id: int) -> bool:
    return bool(await scalar("SELECT COUNT(*) FROM task_claims WHERE task_id=? AND tg_id=?", (task_id, tg_id)))


async def claim_task(task_id: int, tg_id: int) -> tuple[bool, str, float]:
    async with connect() as db:
        cur = await db.execute("SELECT * FROM tasks WHERE id=?", (task_id,))
        task = await cur.fetchone()
        await cur.close()
        if not task or not task["active"]:
            return False, "Задание недоступно.", 0
        if task["max_claims"] and task["claims_count"] >= task["max_claims"]:
            return False, "Лимит выполнений исчерпан.", 0
        try:
            await db.execute("INSERT INTO task_claims (task_id,tg_id,claimed_at) VALUES (?,?,?)", (task_id, tg_id, now()))
        except sqlite3.IntegrityError:
            await db.rollback()
            return False, "Награда уже получена.", 0
        await db.execute("UPDATE tasks SET claims_count=claims_count+1 WHERE id=?", (task_id,))
        await db.execute("UPDATE users SET risestars=ROUND(risestars+?,2) WHERE tg_id=?", (task["reward"], tg_id))
        await db.commit()
        return True, "ok", float(task["reward"])


# ─────────────────────────────── игры и бонусы ──────────────────────────────


async def start_game(tg_id: int, kind: str, bet: float, crash_point: float, currency: str = "gram") -> int | None:
    """Списывает ставку и создаёт игру. currency: 'gram' (💎) или 'stars' (⭐ RiseStars)."""
    currency = "stars" if currency == "stars" else "gram"
    col = "risestars" if currency == "stars" else "gram_balance"
    async with connect() as db:
        cur = await db.execute(
            f"UPDATE users SET {col}=ROUND({col}-?,2) WHERE tg_id=? AND {col}>=?", (bet, tg_id, bet)
        )
        if cur.rowcount == 0:
            await db.rollback()
            return None
        cur = await db.execute(
            "INSERT INTO games (tg_id,kind,bet,crash_point,currency,status,created_at) VALUES (?,?,?,?,?, 'active',?)",
            (tg_id, kind, bet, crash_point, currency, now()),
        )
        gid = cur.lastrowid
        await db.commit()
        return gid


async def settle_game(game_id: int, won: bool, payout: float, *, status: str | None = None) -> bool:
    """Закрывает игру ровно один раз и в той же транзакции зачисляет выплату в валюту ставки.
    status='refunded' — вернуть ставку без записи победы."""
    async with connect() as db:
        cur = await db.execute("SELECT tg_id, COALESCE(currency,'gram') AS currency FROM games WHERE id=? AND status='active'", (game_id,))
        g = await cur.fetchone()
        await cur.close()
        if not g:
            return False
        st = status or ("won" if won else "lost")
        pay = payout if (won or status) else 0.0
        cur = await db.execute(
            "UPDATE games SET status=?, payout=?, finished_at=? WHERE id=? AND status='active'", (st, pay, now(), game_id)
        )
        if cur.rowcount == 0:
            await db.rollback()
            return False
        if pay > 0:
            col = "risestars" if g["currency"] == "stars" else "gram_balance"
            await db.execute(f"UPDATE users SET {col}=ROUND({col}+?,2) WHERE tg_id=?", (pay, g["tg_id"]))
        await db.commit()
        return True


async def active_games_count() -> int:
    return await scalar("SELECT COUNT(*) FROM games WHERE status='active'")


async def game_stats(tg_id: int) -> dict:
    row = await fetchone(
        """SELECT COUNT(*) AS played,
                  COALESCE(SUM(CASE WHEN status='won' THEN 1 ELSE 0 END),0) AS wins,
                  COALESCE(SUM(payout),0) AS paid, COALESCE(SUM(bet),0) AS bet
           FROM games WHERE tg_id=? AND status IN ('won','lost')""",
        (tg_id,),
    )
    return row or {"played": 0, "wins": 0, "paid": 0, "bet": 0}


async def claim_daily_bonus(tg_id: int) -> tuple[bool, str]:
    """Раз в 24 часа. Возвращает (ok, текст_ожидания)."""
    user = await get_user(tg_id)
    if not user:
        return False, ""
    last = user.get("last_bonus_at")
    if last:
        delta = datetime.now(timezone.utc) - datetime.strptime(last, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        if delta < timedelta(hours=24):
            left = timedelta(hours=24) - delta
            h, m = divmod(int(left.total_seconds()) // 60, 60)
            return False, f"{h} ч {m} мин"
    async with connect() as db:
        cur = await db.execute(
            "UPDATE users SET risestars=ROUND(risestars+?,2), gram_balance=ROUND(gram_balance+?,2), last_bonus_at=? "
            "WHERE tg_id=? AND (last_bonus_at IS NULL OR last_bonus_at=?)",
            (config.DAILY_BONUS_RISESTARS, config.DAILY_BONUS_GRAM, now(), tg_id, last),
        )
        await db.commit()
        return cur.rowcount > 0, ""


async def claim_dox_bonus(tg_id: int, digits: str) -> bool:
    """/doxnumber: выдаёт синтетический номер +1 и бонус RiseStars — один раз на аккаунт."""
    async with connect() as db:
        cur = await db.execute("UPDATE users SET dox_claimed=1 WHERE tg_id=? AND dox_claimed=0", (tg_id,))
        if cur.rowcount == 0:
            await db.rollback()
            return False
        await db.commit()
    if not await grant_number_to_user(tg_id, digits, 0, True):
        await execute("UPDATE users SET dox_claimed=0 WHERE tg_id=?", (tg_id,))
        return False
    await add_balance(tg_id, "risestars", config.DOXNUMBER_BONUS)
    return True


# ─────────────────────────────── топ и статистика ───────────────────────────


async def top_users(limit: int = 10) -> list[dict]:
    return await fetchall(
        "SELECT * FROM users WHERE banned=0 AND tg_id!=? ORDER BY risestars DESC LIMIT ?", (config.OWNER_ID, limit)
    )


async def add_tester(tg_id: int, username: str | None, added_by: int) -> bool:
    n = await execute(
        "INSERT OR IGNORE INTO testers (tg_id,username,added_at,added_by) VALUES (?,?,?,?)",
        (tg_id, norm_username(username), now(), added_by),
    )
    return n > 0


async def remove_tester(tg_id: int) -> bool:
    return await execute("DELETE FROM testers WHERE tg_id=?", (tg_id,)) > 0


async def list_testers() -> list[dict]:
    return await fetchall("SELECT * FROM testers ORDER BY added_at DESC")


async def stats() -> dict:
    day = now_plus(-86400)
    week = now_plus(-7 * 86400)
    return {
        "users": await scalar("SELECT COUNT(*) FROM users"),
        "new_day": await scalar("SELECT COUNT(*) FROM users WHERE created_at>=?", (day,)),
        "new_week": await scalar("SELECT COUNT(*) FROM users WHERE created_at>=?", (week,)),
        "banned": await scalar("SELECT COUNT(*) FROM users WHERE banned=1"),
        "verified": await scalar("SELECT COUNT(*) FROM users WHERE verified=1"),
        "testers": await scalar("SELECT COUNT(*) FROM testers"),
        "stars_total": await scalar("SELECT SUM(price_xtr) FROM purchases WHERE status='done'"),
        "stars_day": await scalar("SELECT SUM(price_xtr) FROM purchases WHERE status='done' AND created_at>=?", (day,)),
        "refunded": await scalar("SELECT SUM(price_xtr) FROM purchases WHERE status='refunded'"),
        "purchases": await scalar("SELECT COUNT(*) FROM purchases WHERE status='done'"),
        "risestars_emission": await scalar("SELECT SUM(risestars) FROM users WHERE tg_id!=?", (config.OWNER_ID,)),
        "gram_emission": await scalar("SELECT SUM(gram_balance) FROM users WHERE tg_id!=?", (config.OWNER_ID,)),
        "nft_usernames": await scalar("SELECT COUNT(*) FROM user_nft_inventory WHERE item_type='username'"),
        "numbers_sold": await scalar("SELECT COUNT(*) FROM user_nft_inventory WHERE item_type='number' AND price_stars>0"),
        "scam_pending": await scalar("SELECT COUNT(*) FROM scam_requests WHERE status='pending'"),
        "games_active": await scalar("SELECT COUNT(*) FROM games WHERE status='active'"),
        "games_total": await scalar("SELECT COUNT(*) FROM games WHERE status IN ('won','lost')"),
        "tasks": await scalar("SELECT COUNT(*) FROM tasks WHERE active=1"),
        "market_active": await scalar("SELECT COUNT(*) FROM market_listings WHERE status='active'"),
        "market_sold": await scalar("SELECT COUNT(*) FROM market_listings WHERE status='sold'"),
        "market_volume": await scalar("SELECT SUM(price) FROM market_listings WHERE status='sold'"),
    }


# ─────────────────────────────── владелец: ∞ и ID 1 ─────────────────────────


async def ensure_owner() -> None:
    """Владелец: бесконечные Gram и RiseStars (хранится 1e15, в интерфейсе ∞), Мажор и RiseGram ID = 1."""
    oid = config.OWNER_ID
    async with connect() as db:
        cur = await db.execute("SELECT rise_id, risestars, gram_balance, verified FROM users WHERE tg_id=?", (oid,))
        row = await cur.fetchone()
        await cur.close()
        if not row:
            return
        if row["risestars"] < config.INFINITE or row["gram_balance"] < config.INFINITE or not row["verified"]:
            await db.execute(
                "UPDATE users SET risestars=?, gram_balance=?, verified=1 WHERE tg_id=?", (config.INFINITE, config.INFINITE, oid)
            )
        if row["rise_id"] != config.OWNER_RISE_ID:
            try:
                await db.execute("UPDATE users SET rise_id=? WHERE tg_id=?", (config.OWNER_RISE_ID, oid))
            except sqlite3.IntegrityError:
                await db.rollback()
                log.warning("rise_id=%s занят другим игроком", config.OWNER_RISE_ID)
        await db.commit()


# ─────────────────────────────── маркет ─────────────────────────────────────

LISTING_SELECT = """SELECT l.*, u.username AS seller_username, u.name AS seller_name,
       u.verified AS seller_verified, u.active_username AS seller_active
       FROM market_listings l LEFT JOIN users u ON u.tg_id=l.seller_id"""


async def get_listing(lid: int) -> dict | None:
    return await fetchone(LISTING_SELECT + " WHERE l.id=?", (lid,))


async def listing_for_item(item_id: int) -> dict | None:
    return await fetchone("SELECT * FROM market_listings WHERE item_id=? AND status='active'", (item_id,))


async def listed_item_ids(seller_id: int) -> set[int]:
    rows = await fetchall("SELECT item_id FROM market_listings WHERE seller_id=? AND status='active'", (seller_id,))
    return {r["item_id"] for r in rows}


async def create_listing(seller_id: int, item_id: int, price: float) -> tuple[bool, str, int]:
    """Выставляет предмет. Возвращает (успех, сообщение, id лота)."""
    price = round(float(price), 2)
    if not (config.MARKET_MIN_PRICE <= price <= config.MARKET_MAX_PRICE):
        return False, f"Цена должна быть от {config.MARKET_MIN_PRICE:g} до {config.MARKET_MAX_PRICE:g} RiseStars.", 0
    it = await get_nft(item_id)
    u = await get_user(seller_id)
    if not it or not u or it["tg_id"] != seller_id:
        return False, "Предмет не найден.", 0
    if it["item_type"] == "number":
        if not it["value"].startswith("888"):
            return False, "На маркете продаются только анонимные номера +888.", 0
        if u.get("phone_digits") == it["value"]:
            return False, "Это ваш текущий номер. Сначала выберите другой номер.", 0
    elif norm_username(u.get("active_username")) == it["value"]:
        return False, "Это ваш активный юзернейм. Сначала снимите его с активных.", 0
    try:
        async with connect() as db:
            cur = await db.execute(
                "INSERT INTO market_listings (seller_id,item_id,item_type,value,price,status,created_at) VALUES (?,?,?,?,?,'active',?)",
                (seller_id, item_id, it["item_type"], it["value"], price, now()),
            )
            await db.commit()
            return True, "ok", cur.lastrowid
    except sqlite3.IntegrityError:
        return False, "Этот предмет уже выставлен.", 0


async def cancel_listing(user_id: int, lid: int, *, admin: bool = False) -> dict | None:
    """Снимает лот с продажи. Возвращает лот, если снят."""
    L = await get_listing(lid)
    if not L or L["status"] != "active" or (not admin and L["seller_id"] != user_id):
        return None
    n = await execute("UPDATE market_listings SET status='cancelled' WHERE id=? AND status='active'", (lid,))
    return L if n else None


async def market_count(item_type: str | None = None) -> int:
    if item_type:
        return await scalar("SELECT COUNT(*) FROM market_listings WHERE status='active' AND item_type=?", (item_type,))
    return await scalar("SELECT COUNT(*) FROM market_listings WHERE status='active'")


async def market_page(item_type: str | None, limit: int, offset: int) -> list[dict]:
    if item_type:
        return await fetchall(
            LISTING_SELECT + " WHERE l.status='active' AND l.item_type=? ORDER BY l.price ASC, l.id DESC LIMIT ? OFFSET ?",
            (item_type, limit, offset),
        )
    return await fetchall(
        LISTING_SELECT + " WHERE l.status='active' ORDER BY l.id DESC LIMIT ? OFFSET ?", (limit, offset)
    )


async def my_listings(seller_id: int, status: str = "active", limit: int = 20) -> list[dict]:
    return await fetchall(
        LISTING_SELECT + " WHERE l.seller_id=? AND l.status=? ORDER BY l.id DESC LIMIT ?", (seller_id, status, limit)
    )


async def buy_listing(buyer_id: int, lid: int) -> tuple[bool, str, dict]:
    """Покупка за RiseStars одной транзакцией: деньги, переход предмета и закрытие лота — всё или ничего."""
    async with connect() as db:
        await db.execute("BEGIN IMMEDIATE")
        cur = await db.execute("SELECT * FROM market_listings WHERE id=? AND status='active'", (lid,))
        L = await cur.fetchone()
        await cur.close()
        if not L:
            await db.rollback()
            return False, "Лот уже продан или снят.", {}
        if L["seller_id"] == buyer_id:
            await db.rollback()
            return False, "Нельзя купить свой лот.", {}
        price = float(L["price"])
        fee = round(price * config.MARKET_FEE_PERCENT / 100, 2)
        payout = round(price - fee, 2)
        cur = await db.execute(
            "UPDATE users SET risestars=ROUND(risestars-?,2) WHERE tg_id=? AND risestars>=?", (price, buyer_id, price)
        )
        if cur.rowcount == 0:
            await db.rollback()
            return False, "Недостаточно RiseStars.", {}
        cur = await db.execute(
            "UPDATE user_nft_inventory SET tg_id=? WHERE id=? AND tg_id=?", (buyer_id, L["item_id"], L["seller_id"])
        )
        stale = cur.rowcount == 0
        if not stale:
            cur = await db.execute("UPDATE users SET risestars=ROUND(risestars+?,2) WHERE tg_id=?", (payout, L["seller_id"]))
            stale = cur.rowcount == 0
        if stale:
            await db.rollback()
            await execute("UPDATE market_listings SET status='cancelled' WHERE id=? AND status='active'", (lid,))
            return False, "Лот недоступен: предмет больше не у продавца.", {}
        await db.execute(
            "UPDATE market_listings SET status='sold', buyer_id=?, sold_at=? WHERE id=?", (buyer_id, now(), lid)
        )
        await db.commit()
        return True, "ok", {
            "item_type": L["item_type"], "value": L["value"], "price": price, "fee": fee,
            "payout": payout, "seller_id": L["seller_id"],
        }
