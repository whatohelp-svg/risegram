"""database.py — SQLite (aiosqlite) для RiseGram Ecosystem.

Старая база с Railway подхватывается: недостающие колонки добавляются автоматически (init_db).
Колонка purchases.price_xtr называется так исторически — это просто сумма в Telegram Stars.
"""
from __future__ import annotations

import contextlib
import logging
import math
import random
import secrets
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

import aiosqlite

import native_economy as economy
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
    """Красивый вид виртуального номера RiseGram по цифрам."""
    d = digits_of(d)
    if d.startswith("8888") and len(d) == 7:
        return f"+888 8 {d[4:]}"
    if d.startswith("8880") and len(d) == 11:
        return f"+888 0{d[4:7]} {d[7:]}"
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
    """RiseGram virtual number: +7 (986) XXX-XX-XX."""
    return "7986" + "".join(str(random.randint(0, 9)) for _ in range(7))


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
    """CREATE TABLE IF NOT EXISTS native_game_results(
        game_id INTEGER PRIMARY KEY, won INTEGER NOT NULL, payout TEXT NOT NULL, refunded INTEGER NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS native_wallet_jobs(
        key TEXT PRIMARY KEY, movements TEXT NOT NULL, title TEXT NOT NULL,
        sale INTEGER, status TEXT NOT NULL DEFAULT 'pending')""",
    """CREATE TABLE IF NOT EXISTS verified_peer_links (
        peer_id INTEGER PRIMARY KEY, account_id INTEGER NOT NULL UNIQUE, phone_digits TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS rg_profile_music (
        tg_id INTEGER PRIMARY KEY,
        media_id TEXT NOT NULL,
        title TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        FOREIGN KEY(tg_id) REFERENCES users(tg_id) ON DELETE CASCADE,
        FOREIGN KEY(media_id) REFERENCES rg_media(id)
    )""",
    """CREATE TABLE IF NOT EXISTS users (
        tg_id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT UNIQUE,
        rise_id INTEGER,
        name TEXT,
        username TEXT,
        phone TEXT,
        phone_digits TEXT,
        risestars REAL NOT NULL DEFAULT 0,
        gram_balance REAL NOT NULL DEFAULT 0,
        banned INTEGER NOT NULL DEFAULT 0,
        verified INTEGER NOT NULL DEFAULT 0,
        verified_risegram INTEGER NOT NULL DEFAULT 0,
        verified_major INTEGER NOT NULL DEFAULT 0,
        auth_code TEXT,
        auth_code_expires TEXT,
        active_username TEXT,
        gift_mode TEXT NOT NULL DEFAULT 'real',
        last_bonus_at TEXT,
        dox_claimed INTEGER NOT NULL DEFAULT 0,
        created_at TEXT,
        last_seen TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS telegram_links (
        telegram_chat_id INTEGER PRIMARY KEY,
        tg_id INTEGER NOT NULL UNIQUE,
        linked_at TEXT,
        FOREIGN KEY(tg_id) REFERENCES users(tg_id) ON DELETE CASCADE
    )""",
    """CREATE TABLE IF NOT EXISTS support_tickets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tg_id INTEGER NOT NULL,
        message TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'open',
        created_at TEXT NOT NULL,
        replied_at TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS account_risk_labels (
        target TEXT NOT NULL,
        label TEXT NOT NULL CHECK(label IN ('SCAM', 'FAKE')),
        issued_by INTEGER NOT NULL,
        reason TEXT NOT NULL,
        created_at TEXT NOT NULL,
        PRIMARY KEY(target, label)
    )""",
    """CREATE TABLE IF NOT EXISTS testers (
        tg_id INTEGER PRIMARY KEY,
        username TEXT,
        added_at TEXT,
        added_by INTEGER
    )""",
    """CREATE TABLE IF NOT EXISTS admins (
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
    """CREATE TABLE IF NOT EXISTS messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        sender_id INTEGER NOT NULL,
        recipient_id INTEGER NOT NULL,
        body TEXT NOT NULL,
        created_at TEXT NOT NULL,
        read_at TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS rg_spaces (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL,
        description TEXT DEFAULT '',
        kind TEXT NOT NULL DEFAULT 'group',
        owner_id INTEGER NOT NULL,
        invite_code TEXT NOT NULL UNIQUE,
        created_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS rg_bot_accounts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        owner_id INTEGER NOT NULL,
        username TEXT NOT NULL UNIQUE COLLATE NOCASE,
        display_name TEXT NOT NULL,
        token_hash TEXT NOT NULL UNIQUE,
        created_at TEXT NOT NULL,
        enabled INTEGER NOT NULL DEFAULT 1
    )""",
    """CREATE TABLE IF NOT EXISTS rg_bot_updates (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        bot_id INTEGER NOT NULL,
        payload TEXT NOT NULL,
        created_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS rg_bot_conversations (
        bot_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        created_at TEXT NOT NULL,
        last_at TEXT NOT NULL,
        PRIMARY KEY(bot_id, user_id)
    )""",
    """CREATE TABLE IF NOT EXISTS rg_bot_messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        bot_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        from_bot INTEGER NOT NULL,
        body TEXT NOT NULL,
        reply_markup TEXT,
        created_at TEXT NOT NULL,
        edited_at TEXT
    )""",
    """CREATE INDEX IF NOT EXISTS idx_rg_bot_messages_chat
       ON rg_bot_messages(bot_id, user_id, id)""",
    """CREATE TABLE IF NOT EXISTS rg_bot_callback_queries (
        id TEXT PRIMARY KEY,
        bot_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        answered INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS rg_bot_update_cursors (
        bot_id INTEGER PRIMARY KEY,
        last_update_id INTEGER NOT NULL DEFAULT 0
    )""",
    """CREATE TABLE IF NOT EXISTS rg_space_autojoin_enrolled (
        user_id INTEGER PRIMARY KEY,
        enrolled_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS rg_space_autojoin (
        space_id INTEGER PRIMARY KEY,
        alias TEXT NOT NULL UNIQUE COLLATE NOCASE,
        enabled INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS rg_space_members (
        space_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        role TEXT NOT NULL DEFAULT 'member',
        joined_at TEXT NOT NULL,
        PRIMARY KEY(space_id, user_id)
    )""",
    """CREATE TABLE IF NOT EXISTS rg_space_messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        space_id INTEGER NOT NULL,
        sender_id INTEGER NOT NULL,
        body TEXT NOT NULL,
        created_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS chat_blocks (
        owner_id INTEGER NOT NULL,
        blocked_id INTEGER NOT NULL,
        created_at TEXT NOT NULL,
        PRIMARY KEY(owner_id, blocked_id)
    )""",
    """CREATE TABLE IF NOT EXISTS rg_media (
        id TEXT PRIMARY KEY, uploader_id INTEGER NOT NULL, storage_name TEXT NOT NULL UNIQUE,
        original_name TEXT NOT NULL, mime_type TEXT NOT NULL, byte_size INTEGER NOT NULL,
        sha256 TEXT NOT NULL, created_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS rg_message_media (
        message_id INTEGER NOT NULL, media_id TEXT NOT NULL, position INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY(message_id, media_id)
    )""",
    """CREATE TABLE IF NOT EXISTS rg_space_message_media (
        message_id INTEGER NOT NULL, media_id TEXT NOT NULL, position INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY(message_id, media_id)
    )""",
    """CREATE TABLE IF NOT EXISTS rg_otp_deliveries (
        delivery_id TEXT PRIMARY KEY, purpose TEXT NOT NULL, channel TEXT NOT NULL,
        status TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS gift_drops (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        telegram_gift_id TEXT NOT NULL,
        gift_name TEXT NOT NULL,
        emoji TEXT NOT NULL DEFAULT '🎁',
        telegram_stars INTEGER NOT NULL DEFAULT 0,
        price_risestars REAL NOT NULL,
        stock_total INTEGER NOT NULL,
        stock_remaining INTEGER NOT NULL,
        active INTEGER NOT NULL DEFAULT 1,
        created_at TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS user_gifts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tg_id INTEGER NOT NULL,
        drop_id INTEGER NOT NULL,
        telegram_gift_id TEXT NOT NULL,
        gift_name TEXT NOT NULL,
        emoji TEXT NOT NULL DEFAULT '🎁',
        serial_no INTEGER NOT NULL,
        price_paid REAL NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending',
        received_at TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS meta (
        key TEXT PRIMARY KEY,
        value TEXT
    )""",
]

# Колонки, которые должны быть, даже если таблица осталась от старой версии бота.
COLUMNS: dict[str, dict[str, str]] = {
    "users": {
        "user_id": "TEXT",
        "rise_id": "INTEGER", "name": "TEXT", "username": "TEXT", "phone": "TEXT",
        "phone_digits": "TEXT", "risestars": "REAL DEFAULT 0", "gram_balance": "REAL DEFAULT 0",
        "banned": "INTEGER DEFAULT 0", "verified": "INTEGER DEFAULT 0",
        "verified_risegram": "INTEGER DEFAULT 0", "verified_major": "INTEGER DEFAULT 0",
        "auth_code": "TEXT",
        "auth_code_expires": "TEXT", "active_username": "TEXT", "gift_mode": "TEXT DEFAULT 'real'",
        "last_bonus_at": "TEXT", "dox_claimed": "INTEGER DEFAULT 0", "created_at": "TEXT",
        "last_seen": "TEXT",
    },
    "testers": {"username": "TEXT", "added_at": "TEXT", "added_by": "INTEGER"},
    "admin_logs": {"admin_id": "INTEGER", "action_text": "TEXT", "created_at": "TEXT"},
    "user_nft_inventory": {
        "item_type": "TEXT DEFAULT 'username'", "value": "TEXT", "price_stars": "INTEGER DEFAULT 0",
        "created_at": "TEXT", "is_active": "INTEGER DEFAULT 0",
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
    "gift_drops": {
        "upgrade_stars": "INTEGER DEFAULT 0",
        "pay_for_upgrade": "INTEGER DEFAULT 0",
    },
    "games": {"currency": "TEXT DEFAULT 'gram'"},
}

INDEXES = [
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_users_user_id ON users(user_id)",
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
    "CREATE INDEX IF NOT EXISTS ix_messages_pair ON messages(sender_id, recipient_id, id)",
    "CREATE INDEX IF NOT EXISTS ix_messages_recipient ON messages(recipient_id, read_at, id)",
    "CREATE INDEX IF NOT EXISTS ix_space_members_user ON rg_space_members(user_id, space_id)",
    "CREATE INDEX IF NOT EXISTS ix_space_messages ON rg_space_messages(space_id, id)",
    "CREATE INDEX IF NOT EXISTS ix_rg_media_owner ON rg_media(uploader_id, created_at)",
    "CREATE INDEX IF NOT EXISTS ix_rg_message_media_message ON rg_message_media(message_id, position)",
    "CREATE INDEX IF NOT EXISTS ix_rg_space_media_message ON rg_space_message_media(message_id, position)",
    "CREATE INDEX IF NOT EXISTS ix_gift_drops_active ON gift_drops(active, stock_remaining)",
    "CREATE INDEX IF NOT EXISTS ix_user_gifts_owner ON user_gifts(tg_id, status, id)",
]


async def _migrate_legacy_telegram_keys(db, rows) -> None:
    """Move legacy Telegram-keyed rows to internal RiseGram IDs and preserve delivery links."""
    cur = await db.execute("SELECT COUNT(*) FROM telegram_links")
    linked_count = (await cur.fetchone())[0]
    await cur.close()
    if linked_count or not rows:
        return

    old_ids = [int(row["tg_id"]) for row in rows]
    old_set = set(old_ids)
    mapping: dict[int, int] = {}
    if config.OWNER_TELEGRAM_ID in old_set:
        mapping[config.OWNER_TELEGRAM_ID] = config.OWNER_ID
    next_id = max(config.OWNER_ID + 1, 2)
    for old_id in old_ids:
        if old_id not in mapping:
            while next_id in old_set or next_id in mapping.values():
                next_id += 1
            mapping[old_id] = next_id
            next_id += 1

    relations = [
        ("testers", "tg_id"), ("testers", "added_by"),
        ("admin_logs", "admin_id"), ("user_nft_inventory", "tg_id"),
        ("purchases", "tg_id"), ("task_claims", "tg_id"),
        ("scam_requests", "requester_id"), ("scam_requests", "resolved_by"),
        ("verified_entities", "owner_id"), ("reservations", "tg_id"),
        ("auth_codes", "tg_id"), ("sessions", "tg_id"),
        ("legacy_numbers", "claimed_by"), ("games", "tg_id"),
        ("transfers", "from_id"), ("transfers", "to_id"),
        ("market_listings", "seller_id"), ("market_listings", "buyer_id"),
    ]
    # Temporary negative keys prevent collisions while primary and foreign keys move.
    for old_id, new_id in mapping.items():
        temp_id = -new_id
        await db.execute("UPDATE users SET tg_id=? WHERE tg_id=?", (temp_id, old_id))
        for table, column in relations:
            await db.execute(f"UPDATE {table} SET {column}=? WHERE {column}=?", (temp_id, old_id))
    for old_id, new_id in mapping.items():
        temp_id = -new_id
        await db.execute("UPDATE users SET tg_id=? WHERE tg_id=?", (new_id, temp_id))
        for table, column in relations:
            await db.execute(f"UPDATE {table} SET {column}=? WHERE {column}=?", (new_id, temp_id))
        await db.execute(
            "INSERT INTO telegram_links (telegram_chat_id,tg_id,linked_at) VALUES (?,?,?)",
            (old_id, new_id, now()),
        )
    # RiseStars gift invoice payloads also contain the target account key.
    cur = await db.execute("SELECT id, payload FROM purchases WHERE payload LIKE 'rs:%'")
    payload_rows = await cur.fetchall()
    await cur.close()
    for purchase in payload_rows:
        parts = (purchase["payload"] or "").split(":")
        if len(parts) == 3 and parts[0] == "rs":
            try:
                old_target = int(parts[2])
            except ValueError:
                continue
            if old_target in mapping:
                parts[2] = str(mapping[old_target])
                await db.execute("UPDATE purchases SET payload=? WHERE id=?", (":".join(parts), purchase["id"]))

    log.info("migrated %d legacy Telegram identities to internal RiseGram IDs", len(mapping))


async def init_db() -> None:
    async with connect() as db:
        await db.execute("PRAGMA journal_mode=WAL")
        for stmt in SCHEMA:
            await db.execute(stmt)
        # Remove the old optional personal-phone link store. RiseGram OTP uses
        # only the virtual number assigned to a bot account.
        await db.execute("DROP TABLE IF EXISTS telegram_phone_links")
        for table, cols in COLUMNS.items():
            cur = await db.execute(f"PRAGMA table_info({table})")
            have = {r["name"] for r in await cur.fetchall()}
            await cur.close()
            for col, decl in cols.items():
                if col not in have:
                    await db.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
        # Preserve legacy verified accounts as RiseGram-issued badges.
        await db.execute("UPDATE users SET verified_risegram=1 WHERE verified=1 AND verified_risegram=0 AND verified_major=0")
        await db.execute(
            "UPDATE users SET verified=CASE WHEN verified_risegram=1 OR verified_major=1 THEN 1 ELSE 0 END"
        )
        # Keep a previously selected NFT username active when upgrading.
        await db.execute(
            "UPDATE user_nft_inventory SET is_active=1 WHERE item_type='username' AND value IN "
            "(SELECT lower(replace(active_username,'@','')) FROM users WHERE active_username IS NOT NULL AND active_username!='')"
        )
        await db.commit()

        # Дозаполняем rise_id / phone_digits у старых пользователей.
        cur = await db.execute("SELECT tg_id, user_id, phone, rise_id, phone_digits FROM users")
        rows = await cur.fetchall()
        await cur.close()
        await _migrate_legacy_telegram_keys(db, rows)
        cur = await db.execute("SELECT tg_id, user_id, phone, rise_id, phone_digits FROM users")
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
                "UPDATE users SET user_id=COALESCE(NULLIF(user_id,''), ?), rise_id=?, phone_digits=?, phone=COALESCE(NULLIF(phone,''), ?) WHERE tg_id=?",
                (str(uuid.uuid4()), rid, pd, fmt_number(pd), r["tg_id"]),
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
        await db.execute("DELETE FROM reservations WHERE expires_at < ?", (now(),))
        await db.execute("UPDATE market_listings SET status='cancelled' WHERE status='active' AND item_id NOT IN (SELECT id FROM user_nft_inventory)")
        await db.commit()
    await ensure_owner()


def _new_rise_id(used: set) -> int:
    while True:
        v = 10_000_000 + secrets.randbelow(90_000_000)
        if v not in used:
            return v


def _new_phone(used: set) -> str:
    # Reserve +888 for scarce premium/collectible number releases.
    # New accounts receive a random virtual +7 986 number from RiseGram's
    # private namespace. This is an app identity, not a carrier-issued SIM.
    while True:
        v = "7986" + f"{secrets.randbelow(10_000_000):07d}"
        if v not in used:
            return v

# ─────────────────────────────── пользователи ───────────────────────────────


async def get_user(tg_id: int) -> dict | None:
    """Fetch by internal RiseGram numeric key (never by Telegram chat ID)."""
    return await fetchone("SELECT * FROM users WHERE tg_id=?", (tg_id,))


async def get_by_telegram(telegram_chat_id: int) -> dict | None:
    return await fetchone(
        "SELECT u.* FROM telegram_links l JOIN users u ON u.tg_id=l.tg_id WHERE l.telegram_chat_id=?",
        (int(telegram_chat_id),),
    )


async def telegram_chat_id(user_id: int) -> int | None:
    return await scalar("SELECT telegram_chat_id FROM telegram_links WHERE tg_id=?", (int(user_id),), None)


async def get_or_create_user(telegram_id: int, name: str, username: str | None) -> dict:
    """Resolve Telegram update to a RiseGram account; Telegram ID is only a delivery link."""
    uname = norm_username(username) or None
    owner_link = int(telegram_id) == config.OWNER_TELEGRAM_ID
    user = await get_by_telegram(telegram_id)
    # Backward compatibility: older accounts were keyed by Telegram ID before
    # telegram_links existed. Reattach that private chat to the same account
    # instead of creating a duplicate RiseGram identity.
    if not user:
        legacy = await get_user(int(telegram_id))
        if legacy:
            await link_telegram(legacy["tg_id"], int(telegram_id))
            user = legacy
    if user:
        effective_uname = config.OWNER_USERNAME if owner_link else (uname or user.get("username"))
        await execute(
            "UPDATE users SET name=?, username=?, last_seen=? WHERE tg_id=?",
            (name, effective_uname, now(), user["tg_id"]),
        )
        user["name"], user["username"] = name, effective_uname
        if owner_link:
            await ensure_owner()
            user = await get_by_telegram(telegram_id) or user
        return user

    async with connect() as db:
        cur = await db.execute("SELECT rise_id, phone_digits FROM users")
        existing = await cur.fetchall()
        used_rise = {r["rise_id"] for r in existing if r["rise_id"]}
        used_phone = {r["phone_digits"] for r in existing if r["phone_digits"]}
        await cur.close()
        digits = _new_phone(used_phone)
        cur = await db.execute(
            """INSERT INTO users (user_id,rise_id,name,username,phone,phone_digits,
                                  risestars,gram_balance,created_at,last_seen)
               VALUES (?,?,?,?,?,?,0,0,?,?)""",
            (str(uuid.uuid4()), _new_rise_id(used_rise), name,
             config.OWNER_USERNAME if owner_link else uname, fmt_number(digits), digits, now(), now()),
        )
        internal_id = cur.lastrowid
        await db.execute(
            "INSERT INTO telegram_links (telegram_chat_id,tg_id,linked_at) VALUES (?,?,?)",
            (int(telegram_id), internal_id, now()),
        )
        await db.commit()
    if owner_link:
        await ensure_owner()
    return await get_user(internal_id)


async def link_telegram(user_id: int, telegram_id: int) -> None:
    """Attach the private bot chat to an existing RiseGram account."""
    async with connect() as db:
        await db.execute(
            "INSERT INTO telegram_links (telegram_chat_id,tg_id,linked_at) VALUES (?,?,?) "
            "ON CONFLICT(telegram_chat_id) DO UPDATE SET tg_id=excluded.tg_id, linked_at=excluded.linked_at",
            (int(telegram_id), int(user_id), now()),
        )
        await db.commit()


async def find_user(query: str) -> dict | None:
    q = (query or "").strip()
    if not q:
        return None
    if q.startswith("@") or any(ch.isalpha() for ch in q) or not digits_of(q):
        return await fetchone(
            "SELECT * FROM users WHERE lower(replace(username,'@',''))=? OR lower(replace(active_username,'@',''))=? "
            "OR tg_id IN (SELECT tg_id FROM user_nft_inventory WHERE item_type='username' AND is_active=1 AND value=?) LIMIT 1",
            (norm_username(q), norm_username(q), norm_username(q)),
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


async def set_verification(tg_id: int, kind: str, enabled: bool) -> None:
    if kind not in ("risegram", "major"):
        raise ValueError("Unknown verification kind")
    column = "verified_risegram" if kind == "risegram" else "verified_major"
    async with connect() as conn:
        await conn.execute(f"UPDATE users SET {column}=? WHERE tg_id=?", (1 if enabled else 0, tg_id))
        await conn.execute(
            "UPDATE users SET verified=CASE WHEN verified_risegram=1 OR verified_major=1 THEN 1 ELSE 0 END WHERE tg_id=?",
            (tg_id,),
        )


async def set_verified(tg_id: int, verified: bool) -> None:
    await set_verification(tg_id, "risegram", verified)


async def set_gift_mode(tg_id: int, mode: str) -> None:
    await execute("UPDATE users SET gift_mode=? WHERE tg_id=?", (mode, tg_id))


async def delete_user(tg_id: int, *, expected_phone: str) -> None:
    async with connect() as db:
        await db.execute("BEGIN IMMEDIATE")
        cursor = await db.execute("SELECT phone_digits FROM users WHERE tg_id=?", (tg_id,))
        target = await cursor.fetchone()
        if target is None:
            return
        if tg_id == config.OWNER_ID or target["phone_digits"] != expected_phone:
            raise ValueError("account changed")
        await db.execute("UPDATE market_listings SET status='cancelled' WHERE seller_id=? AND status='active'", (tg_id,))
        for table, col in [
            ("admins", "tg_id"), ("telegram_links", "tg_id"), ("verified_peer_links", "account_id"),
            ("user_nft_inventory", "tg_id"),
            ("task_claims", "tg_id"), ("auth_codes", "tg_id"), ("sessions", "tg_id"),
            ("reservations", "tg_id"), ("games", "tg_id"), ("testers", "tg_id"), ("users", "tg_id"),
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


async def add_balance(tg_id, col, delta, *, key, title="Пополнение"):
    uid = await economy.account(await get_user(tg_id))
    cur = {"risestars": "stars", "gram_balance": "gram"}[col]
    async with economy.transaction() as conn:
        await economy.post(conn, key, [(uid, cur, economy.units(cur, delta))], title)
        query = ("SELECT balance AS amount FROM stars_balances WHERE user_id=%s" if cur == "stars"
                 else "SELECT balance_nanoton AS amount FROM ton_balances WHERE user_id=%s")
        balance = await (await conn.execute(query, (uid,))).fetchone()
        return balance["amount"] if cur == "stars" else balance["amount"] / 10**9



async def transfer_risestars(from_id, to_id, amount, fee_percent, *, key):
    if from_id == to_id or amount <= 0:
        return False, "Некорректный перевод."
    source = await economy.account(await get_user(from_id))
    dest = await economy.account(await get_user(to_id))
    amount = economy.units("stars", amount)
    fee = round(amount * fee_percent / 100)
    await economy.book(key, [(source,"stars",-amount-fee),(dest,"stars",amount)], "Перевод RiseStars")
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


async def create_session(tg_id: int, days: int = 30, *, native=False) -> str:
    token = ("client." if native else "") + secrets.token_urlsafe(32)
    await execute(
        "INSERT INTO sessions (token, tg_id, created_at, expires_at) VALUES (?,?,?,?)",
        (token, tg_id, now(), now_plus(days * 86400)),
    )
    return token


async def get_session_user(token: str) -> dict | None:
    # Rolling 30-day inactivity window: active Gram sessions stay linked
    # without requiring another one-time code every calendar month.
    async with connect() as conn:
        cur = await conn.execute(
            "SELECT tg_id FROM sessions WHERE token=? AND expires_at>?",
            (token, now()),
        )
        row = await cur.fetchone()
        if not row:
            return None
        await conn.execute(
            "UPDATE sessions SET expires_at=? WHERE token=?",
            (now_plus(30 * 86400), token),
        )
        await conn.commit()
    return await get_user(row["tg_id"])


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


# ─────────────────────────────── Telegram gift drops ─────────────────────────


async def create_gift_drop(
    telegram_gift_id: str, gift_name: str, emoji: str, telegram_stars: int,
    price_risestars: float, quantity: int, upgrade_stars: int = 0,
    pay_for_upgrade: bool = False,
) -> int:
    """Create a limited drop backed by a real Telegram gift and optional NFT upgrade."""
    async with connect() as conn:
        cur = await conn.execute(
            """INSERT INTO gift_drops
               (telegram_gift_id,gift_name,emoji,telegram_stars,upgrade_stars,pay_for_upgrade,
                price_risestars,stock_total,stock_remaining,active,created_at)
               VALUES (?,?,?,?,?,?,?,?,?,1,?)""",
            (str(telegram_gift_id), gift_name[:80], (emoji or "🎁")[:16],
             max(0, int(telegram_stars)), max(0, int(upgrade_stars)), int(bool(pay_for_upgrade)),
             round(float(price_risestars), 2), int(quantity), int(quantity), now()),
        )
        await conn.commit()
        return int(cur.lastrowid)


async def sync_gift_catalog_drops(catalog: list[dict]) -> dict[str, int]:
    """Publish every currently sendable NFT-capable Telegram gift without duplicating live drops.

    RiseGram uses the configured one-Star-to-RiseStars rate for the base gift
    and its prepaid unique-gift upgrade. Existing live drops keep their chosen
    price and stock; legacy rows are upgraded once so their next delivery is
    also an actual collectible gift.
    """
    created = upgraded = already_live = skipped = 0
    max_unlimited_stock = 1_000_000
    async with connect() as conn:
        await conn.execute("BEGIN IMMEDIATE")
        for gift in catalog:
            gift_id = str(gift.get("id") or "").strip()
            if not gift_id or not bool(gift.get("can_upgrade")):
                skipped += 1
                continue
            try:
                star_count = max(0, int(gift.get("stars") or 0))
                upgrade_stars = max(0, int(gift.get("upgrade_stars") or 0))
            except (TypeError, ValueError):
                skipped += 1
                continue
            if star_count <= 0:
                skipped += 1
                continue

            cur = await conn.execute(
                """SELECT id,telegram_stars,upgrade_stars,pay_for_upgrade,price_risestars
                   FROM gift_drops
                   WHERE telegram_gift_id=? AND active=1 AND stock_remaining>0
                   ORDER BY id DESC LIMIT 1""",
                (gift_id,),
            )
            existing = await cur.fetchone()
            if existing:
                if not bool(existing["pay_for_upgrade"]):
                    # Older drops were regular gifts. Move them to the unique
                    # version and enforce the current Telegram gift + upgrade
                    # cost floor while preserving any higher manual markup.
                    minimum_price = (star_count + upgrade_stars) * config.RISESTARS_PER_STAR
                    new_price = round(max(float(existing["price_risestars"]), minimum_price), 2)
                    await conn.execute(
                        """UPDATE gift_drops
                           SET telegram_stars=?,upgrade_stars=?,pay_for_upgrade=1,price_risestars=?
                           WHERE id=? AND pay_for_upgrade=0""",
                        (star_count, upgrade_stars, new_price, existing["id"]),
                    )
                    upgraded += 1
                else:
                    already_live += 1
                continue

            personal_remaining = gift.get("personal_remaining_count")
            global_remaining = gift.get("remaining_count")
            personal_total = gift.get("personal_total_count")
            global_total = gift.get("total_count")
            stock_value = next(
                (v for v in (personal_remaining, global_remaining, personal_total, global_total) if v is not None),
                max_unlimited_stock,
            )
            try:
                stock = max(0, int(stock_value))
            except (TypeError, ValueError):
                skipped += 1
                continue
            if stock <= 0:
                skipped += 1
                continue

            price = round((star_count + upgrade_stars) * config.RISESTARS_PER_STAR, 2)
            await conn.execute(
                """INSERT INTO gift_drops
                   (telegram_gift_id,gift_name,emoji,telegram_stars,upgrade_stars,pay_for_upgrade,
                    price_risestars,stock_total,stock_remaining,active,created_at)
                   VALUES (?,?,?,?,?,1,?,?,?,?,?)""",
                (gift_id, str(gift.get("name") or f"Telegram Gift {gift_id[-4:]}")[:80],
                 str(gift.get("emoji") or "🎁")[:16], star_count, upgrade_stars,
                 price, stock, stock, 1, now()),
            )
            created += 1
        await conn.commit()
    return {
        "created": created,
        "upgraded": upgraded,
        "already_live": already_live,
        "skipped": skipped,
    }


async def list_gift_drops(active_only: bool = True) -> list[dict]:
    where = "WHERE active=1 AND stock_remaining>0" if active_only else ""
    return await fetchall(f"SELECT * FROM gift_drops {where} ORDER BY id DESC")


async def list_user_gifts(tg_id: int, include_pending: bool = False) -> list[dict]:
    status_filter = "status IN ('sent','pending')" if include_pending else "status='sent'"
    return await fetchall(
        f"""SELECT id,drop_id,telegram_gift_id,gift_name,emoji,serial_no,price_paid,status,received_at
            FROM user_gifts WHERE tg_id=? AND {status_filter} ORDER BY id DESC""",
        (int(tg_id),),
    )


async def reserve_gift_drop(tg_id: int, drop_id: int) -> tuple[bool, str, dict]:
    """Atomically debit RiseStars, reserve one stock unit, and create a pending issue."""
    async with connect() as conn:
        await conn.execute("BEGIN IMMEDIATE")
        cur = await conn.execute("SELECT * FROM gift_drops WHERE id=?", (drop_id,))
        drop = await cur.fetchone()
        cur = await conn.execute("SELECT risestars,banned FROM users WHERE tg_id=?", (tg_id,))
        user = await cur.fetchone()
        if not drop or not drop["active"]:
            await conn.rollback()
            return False, "Подарок больше недоступен.", {}
        if not user or user["banned"]:
            await conn.rollback()
            return False, "Аккаунт недоступен.", {}
        if int(drop["stock_remaining"]) <= 0:
            await conn.rollback()
            return False, "Тираж распродан.", {}
        price = round(float(drop["price_risestars"]), 2)
        if float(user["risestars"]) < price:
            await conn.rollback()
            return False, "Недостаточно RiseStars.", {}
        changed = await conn.execute(
            "UPDATE gift_drops SET stock_remaining=stock_remaining-1 WHERE id=? AND active=1 AND stock_remaining>0",
            (drop_id,),
        )
        if changed.rowcount != 1:
            await conn.rollback()
            return False, "Тираж только что закончился.", {}
        changed = await conn.execute(
            "UPDATE users SET risestars=ROUND(risestars-?,2) WHERE tg_id=? AND risestars>=?",
            (price, tg_id, price),
        )
        if changed.rowcount != 1:
            await conn.rollback()
            return False, "Недостаточно RiseStars.", {}
        cur = await conn.execute(
            "SELECT COALESCE(MAX(serial_no),0)+1 FROM user_gifts WHERE drop_id=?",
            (drop_id,),
        )
        serial_no = int((await cur.fetchone())[0])
        cur = await conn.execute(
            """INSERT INTO user_gifts
               (tg_id,drop_id,telegram_gift_id,gift_name,emoji,serial_no,price_paid,status,received_at)
               VALUES (?,?,?,?,?,?,?,'pending',?)""",
            (tg_id, drop_id, drop["telegram_gift_id"], drop["gift_name"], drop["emoji"],
             serial_no, price, now()),
        )
        instance_id = int(cur.lastrowid)
        await conn.commit()
        return True, "ok", {
            "instance_id": instance_id,
            "telegram_gift_id": drop["telegram_gift_id"],
            "gift_name": drop["gift_name"],
            "emoji": drop["emoji"],
            "serial_no": serial_no,
            "price_paid": price,
            "telegram_stars": int(drop["telegram_stars"] or 0),
            "upgrade_stars": int(drop["upgrade_stars"] or 0),
            "pay_for_upgrade": bool(drop["pay_for_upgrade"]),
        }


async def confirm_gift_purchase(instance_id: int) -> bool:
    return (await execute(
        "UPDATE user_gifts SET status='sent' WHERE id=? AND status='pending'", (instance_id,)
    )) == 1


async def rollback_gift_purchase(instance_id: int) -> bool:
    """Restore payment and one stock unit only while delivery remains pending."""
    async with connect() as conn:
        await conn.execute("BEGIN IMMEDIATE")
        cur = await conn.execute(
            "SELECT * FROM user_gifts WHERE id=? AND status='pending'", (instance_id,)
        )
        gift = await cur.fetchone()
        if not gift:
            await conn.rollback()
            return False
        await conn.execute(
            "UPDATE users SET risestars=ROUND(risestars+?,2) WHERE tg_id=?",
            (gift["price_paid"], gift["tg_id"]),
        )
        await conn.execute(
            "UPDATE gift_drops SET stock_remaining=MIN(stock_total,stock_remaining+1) WHERE id=?",
            (gift["drop_id"],),
        )
        await conn.execute("UPDATE user_gifts SET status='failed' WHERE id=?", (instance_id,))
        await conn.commit()
        return True


async def exchange_gram_balance(tg_id: int, direction: str, amount: float) -> tuple[bool, str, dict]:
    """Exchange Gram and RiseStars at the fixed 1 Gram = 100 RiseStars rate."""
    if direction not in ("gram_to_stars", "stars_to_gram"):
        return False, "Неизвестное направление обмена.", {}
    try:
        raw_amount = float(amount)
        amount = round(raw_amount, 2)
    except (TypeError, ValueError):
        return False, "Некорректная сумма.", {}
    if not math.isfinite(amount) or amount <= 0 or amount > 1e12:
        return False, "Введите положительную сумму.", {}
    if abs(raw_amount - amount) > 1e-8:
        return False, "Точность суммы — не более 2 знаков после запятой.", {}
    if direction == "stars_to_gram" and not amount.is_integer():
        return False, "Введите целое количество звёзд.", {}
    rate = float(config.GRAM_TO_RISESTARS)
    async with connect() as conn:
        await conn.execute("BEGIN IMMEDIATE")
        cur = await conn.execute(
            "SELECT risestars,gram_balance,banned FROM users WHERE tg_id=?", (tg_id,)
        )
        user = await cur.fetchone()
        if not user or user["banned"]:
            await conn.rollback()
            return False, "Аккаунт недоступен.", {}
        if direction == "gram_to_stars":
            if float(user["gram_balance"]) < amount:
                await conn.rollback()
                return False, "Недостаточно Gram.", {}
            await conn.execute(
                "UPDATE users SET gram_balance=ROUND(gram_balance-?,2),risestars=ROUND(risestars+?,2) WHERE tg_id=?",
                (amount, amount * rate, tg_id),
            )
        else:
            if float(user["risestars"]) < amount:
                await conn.rollback()
                return False, "Недостаточно RiseStars.", {}
            await conn.execute(
                "UPDATE users SET risestars=ROUND(risestars-?,2),gram_balance=ROUND(gram_balance+?,2) WHERE tg_id=?",
                (amount, amount / rate, tg_id),
            )
        cur = await conn.execute(
            "SELECT risestars,gram_balance FROM users WHERE tg_id=?", (tg_id,)
        )
        updated = await cur.fetchone()
        await conn.commit()
        return True, "ok", {
            "risestars": float(updated["risestars"]),
            "gram_balance": float(updated["gram_balance"]),
        }


async def delete_nft(item_id: int) -> None:
    async with connect() as conn:
        await conn.execute("BEGIN IMMEDIATE")
        pending = await (await conn.execute("SELECT 1 FROM market_listings WHERE item_id=? AND status='processing'", (item_id,))).fetchone()
        if pending:
            raise economy.WalletError("Покупка этого предмета ещё обрабатывается.")
        await conn.execute("UPDATE market_listings SET status='cancelled' WHERE item_id=? AND status='active'", (item_id,))
        await conn.execute("DELETE FROM user_nft_inventory WHERE id=?", (item_id,))
        await conn.commit()



async def set_active_username(tg_id: int, value: str | None) -> None:
    """Set the primary displayed alias while keeping NFT activation state consistent."""
    async with connect() as conn:
        await conn.execute("UPDATE user_nft_inventory SET is_active=0 WHERE tg_id=? AND item_type='username'", (tg_id,))
        if value:
            await conn.execute(
                "UPDATE user_nft_inventory SET is_active=1 WHERE tg_id=? AND item_type='username' AND value=?",
                (tg_id, norm_username(value)),
            )
        await conn.execute("UPDATE users SET active_username=? WHERE tg_id=?", (value, tg_id))
        await conn.commit()


async def set_nft_username_active(tg_id: int, item_id: int, active: bool) -> tuple[bool, str | None]:
    """Activate/deactivate owned NFT usernames independently; no one-at-a-time limit."""
    async with connect() as conn:
        cur = await conn.execute(
            "SELECT value FROM user_nft_inventory WHERE id=? AND tg_id=? AND item_type='username'",
            (item_id, tg_id),
        )
        item = await cur.fetchone()
        await cur.close()
        if not item:
            return False, None
        cur = await conn.execute("SELECT 1 FROM market_listings WHERE item_id=? AND status='active'", (item_id,))
        listed = await cur.fetchone()
        await cur.close()
        if listed:
            return False, None
        await conn.execute("UPDATE user_nft_inventory SET is_active=? WHERE id=? AND tg_id=?", (1 if active else 0, item_id, tg_id))
        cur = await conn.execute(
            "SELECT value FROM user_nft_inventory WHERE tg_id=? AND item_type='username' AND is_active=1 ORDER BY id DESC LIMIT 1",
            (tg_id,),
        )
        primary = await cur.fetchone()
        await cur.close()
        await conn.execute("UPDATE users SET active_username=? WHERE tg_id=?", (primary["value"] if primary else None, tg_id))
        await conn.commit()
        return True, item["value"]


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
    peer = await economy.account(await get_user(tg_id))
    key = f"task:{task_id}:{tg_id}"
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
        await economy.queue(db, key, [(peer, "stars", economy.units("stars", round(task["reward"])))], "Награда за задание")
        await db.commit()
    await economy.deliver(key)
    return True, "ok", round(task["reward"])


# ─────────────────────────────── игры и бонусы ──────────────────────────────


async def game_stats(tg_id):
    history = await fetchone("""SELECT count(*) AS played, count(CASE WHEN status='won' THEN 1 END) AS wins,
                              COALESCE(SUM(payout),0) AS paid, COALESCE(SUM(bet),0) AS bet FROM games
                              WHERE tg_id=? AND status IN ('won','lost')""", (tg_id,))
    async with economy.transaction() as conn:
        stats = await (await conn.execute("""SELECT count(*) AS played, count(*) FILTER (WHERE status='won') AS wins,
                     COALESCE(SUM(payout / CASE WHEN currency='gram' THEN 1e9 ELSE 1 END),0) AS paid,
                     COALESCE(SUM(stake / CASE WHEN currency='gram' THEN 1e9 ELSE 1 END),0) AS bet
                     FROM risegram_bot_games WHERE bot_account=%s AND status IN ('won','lost')""",(tg_id,))).fetchone()
    return {key: history[key] + (int(stats[key]) if key in ('played','wins') else float(stats[key])) for key in history}


async def active_games_count():
    async with economy.transaction() as conn:
        return (await (await conn.execute("SELECT count(*) AS n FROM risegram_bot_games WHERE status='active'")).fetchone())["n"]


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
    peer = await economy.account(user)
    stamp = now()
    key = f"daily:{tg_id}:{stamp}"
    async with connect() as db:
        cur = await db.execute("UPDATE users SET last_bonus_at=? WHERE tg_id=? AND last_bonus_at IS ?", (stamp,tg_id,last))
        if cur.rowcount != 1:
            return False, "Бонус уже получен."
        await economy.queue(db, key, [(peer,"stars",economy.units("stars",config.DAILY_BONUS_RISESTARS)),
                                     (peer,"gram",economy.units("gram",config.DAILY_BONUS_GRAM))], "Ежедневный бонус")
        await db.commit()
    await economy.deliver(key)
    return True, ""


async def claim_dox_bonus(tg_id: int, digits: str) -> bool:
    """/doxnumber: выдаёт синтетический номер +1 и бонус RiseStars — один раз на аккаунт."""
    peer = await economy.account(await get_user(tg_id))
    key = f"number-bonus:{tg_id}"
    digits = digits_of(digits)
    async with connect() as db:
        await db.execute("BEGIN IMMEDIATE")
        old = await (await db.execute("SELECT phone_digits FROM users WHERE tg_id=? AND dox_claimed=0",
                                      (tg_id,))).fetchone()
        if not old:
            return False
        try:
            await db.execute("INSERT INTO user_nft_inventory(tg_id,item_type,value,created_at) VALUES(?,'number',?,?)",
                             (tg_id,digits,now()))
            await db.execute("UPDATE users SET dox_claimed=1,phone=?,phone_digits=? WHERE tg_id=?",
                             (fmt_number(digits),digits,tg_id))
        except sqlite3.IntegrityError:
            return False
        if old["phone_digits"] and old["phone_digits"] != digits:
            await db.execute("INSERT OR IGNORE INTO user_nft_inventory(tg_id,item_type,value,created_at) VALUES(?,'number',?,?)",
                             (tg_id,old["phone_digits"],now()))
        await economy.queue(db, key, [(peer,"stars",economy.units("stars",config.DOXNUMBER_BONUS))], "Бонус за номер")
        await db.commit()
    await economy.deliver(key)
    return True


# ─────────────────────────────── топ и статистика ───────────────────────────


async def top_users(limit: int = 10) -> list[dict]:
    rows = await fetchall("SELECT * FROM users WHERE banned=0 AND tg_id!=?", (config.OWNER_ID,))
    by_phone = {row["phone_digits"]: row for row in rows}
    async with economy.transaction() as conn:
        leaders = await (await conn.execute("""SELECT u.phone,s.balance FROM users u JOIN stars_balances s ON s.user_id=u.id
                    WHERE u.deleted_at IS NULL AND u.phone=ANY(%s) ORDER BY s.balance DESC,u.id LIMIT %s""",
                    (list(by_phone),limit))).fetchall()
    return [dict(by_phone[r["phone"]],risestars=r["balance"]) for r in leaders]


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


async def is_admin(tg_id: int) -> bool:
    if int(tg_id) == int(config.OWNER_ID):
        return True
    return bool(await scalar(
        "SELECT 1 FROM admins WHERE tg_id=? UNION ALL SELECT 1 FROM testers WHERE tg_id=? LIMIT 1",
        (int(tg_id), int(tg_id)),
    ))


async def add_admin(tg_id: int, username: str | None, added_by: int) -> bool:
    if int(tg_id) == int(config.OWNER_ID):
        return False
    return await execute(
        "INSERT OR IGNORE INTO admins (tg_id,username,added_at,added_by) VALUES (?,?,?,?)",
        (int(tg_id), norm_username(username), now(), int(added_by)),
    ) > 0


async def remove_admin(tg_id: int) -> bool:
    if int(tg_id) == int(config.OWNER_ID):
        return False
    return await execute("DELETE FROM admins WHERE tg_id=?", (int(tg_id),)) > 0


async def list_admins() -> list[dict]:
    return await fetchall(
        """SELECT a.tg_id,a.username,a.added_at,a.added_by,
                  u.name,u.rise_id
           FROM admins a LEFT JOIN users u ON u.tg_id=a.tg_id
           ORDER BY a.added_at DESC"""
    )


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


# ─────────────────────────────── владелец: кошелёк и ID 1 ───────────────────


async def ensure_owner() -> None:
    """Create the owner's internal account and link the Telegram bot only for private delivery."""
    oid = config.OWNER_ID
    async with connect() as db:
        cur = await db.execute("SELECT tg_id,rise_id,risestars,gram_balance FROM users WHERE tg_id=?", (oid,))
        row = await cur.fetchone()
        await cur.close()
        cur = await db.execute("SELECT rise_id FROM users WHERE rise_id IS NOT NULL")
        used_rise = {r["rise_id"] for r in await cur.fetchall() if r["rise_id"]}
        await cur.close()
        owner_rise_id = row["rise_id"] if row and row["rise_id"] and 10_000_000 <= row["rise_id"] <= 99_999_999 else _new_rise_id(used_rise)
        if not row:
            await db.execute(
                "INSERT INTO users (tg_id,user_id,rise_id,name,username,phone,phone_digits,risestars,gram_balance,verified,created_at,last_seen) "
                "VALUES (?,?,?,?,?,?,?,?,?,1,?,?)",
                (oid, str(uuid.uuid4()), owner_rise_id, "RiseGram Owner", config.OWNER_USERNAME,
                 "+70000000000", "70000000000", 0, 0, now(), now()),
            )
        await db.execute(
            "INSERT INTO telegram_links (telegram_chat_id,tg_id,linked_at) VALUES (?,?,?) "
            "ON CONFLICT(telegram_chat_id) DO UPDATE SET tg_id=excluded.tg_id, linked_at=excluded.linked_at",
            (config.OWNER_TELEGRAM_ID, oid, now()),
        )
        await db.execute(
            "UPDATE users SET verified=CASE WHEN verified_risegram=1 OR verified_major=1 THEN 1 ELSE 0 END, rise_id=?, username=? WHERE tg_id=?",
            (owner_rise_id, config.OWNER_USERNAME, oid),
        )
        await db.execute(
            "INSERT OR IGNORE INTO admins (tg_id,username,added_at,added_by) VALUES (?,?,?,?)",
            (oid, config.OWNER_USERNAME, now(), oid),
        )
        for nft_name in config.STARTER_NFT_USERNAMES:
            if not (config.NFT_MIN_LEN <= len(nft_name) <= config.NFT_MAX_LEN) or not nft_name[0].isalpha():
                continue
            cur = await db.execute(
                "SELECT 1 FROM user_nft_inventory WHERE item_type='username' AND lower(value)=? LIMIT 1",
                (nft_name,),
            )
            exists = await cur.fetchone()
            await cur.close()
            if exists:
                continue
            cur = await db.execute(
                "SELECT 1 FROM users WHERE (lower(COALESCE(username,''))=? OR lower(COALESCE(active_username,''))=?) AND tg_id!=? LIMIT 1",
                (nft_name, nft_name, oid),
            )
            claimed = await cur.fetchone()
            await cur.close()
            if claimed:
                log.warning("starter NFT @%s skipped: username is already active on an account", nft_name)
                continue
            await db.execute(
                "INSERT INTO user_nft_inventory (tg_id,item_type,value,price_stars,created_at) VALUES (?,'username',?,0,?)",
                (oid, nft_name, now()),
            )
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
    if not float(price).is_integer():
        return False, "Цена в звёздах должна быть целой.", 0
    price = int(price)
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
    elif bool(it.get("is_active")):
        return False, "Этот NFT-юзернейм активен. Сначала отключите его в инвентаре.", 0
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
    listing = await fetchone("SELECT seller_id FROM market_listings WHERE id=? AND status='active'", (lid,))
    if not listing:
        return False, "Лот недоступен.", {}
    buyer = await economy.account(await get_user(buyer_id))
    seller = await economy.account(await get_user(listing["seller_id"]))
    key = "market:" + uuid.uuid4().hex
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
        if not price.is_integer():
            return False, "Продавцу нужно перевыставить лот с ценой в целых RiseStars.", {}
        fee = round(price * config.MARKET_FEE_PERCENT / 100)
        payout = round(price - fee, 2)
        cur = await db.execute("UPDATE user_nft_inventory SET tg_id=? WHERE id=? AND tg_id=?", (-lid,L["item_id"],L["seller_id"]))
        if cur.rowcount != 1:
            await db.rollback()
            return False, "Предмет больше не доступен.", {}
        await db.execute("UPDATE market_listings SET status='processing',buyer_id=? WHERE id=?", (buyer_id,lid))
        await economy.queue(db,key,[(buyer,"stars",-economy.units("stars",price)),(seller,"stars",economy.units("stars",payout))],
                            "Покупка на маркете", sale=lid)
        await db.commit()
    if not await economy.deliver(key):
        return False, "Недостаточно RiseStars.", {}
    return True, "ok", {
            "item_type": L["item_type"], "value": L["value"], "price": price, "fee": fee,
            "payout": payout, "seller_id": L["seller_id"],
        }


async def account_role(tg_id: int) -> str | None:
    if int(tg_id) == int(config.OWNER_ID):
        return "Владелец"
    if await scalar("SELECT 1 FROM admins WHERE tg_id=?", (int(tg_id),)):
        return "Администратор"
    if await scalar("SELECT 1 FROM testers WHERE tg_id=?", (int(tg_id),)):
        return "Тестер"
    return None


async def risk_labels(username: str | None) -> list[str]:
    target = norm_username(username)
    if not target:
        return []
    rows = await fetchall("SELECT label FROM account_risk_labels WHERE target=?", (target,))
    return [row["label"] for row in rows]


async def set_risk_label(username: str, label: str, actor: int, reason: str) -> None:
    target = norm_username(username)
    if not target or label not in ("SCAM", "FAKE") or not reason.strip():
        raise ValueError("Некорректная метка, юзернейм или причина")
    await execute("INSERT INTO account_risk_labels(target,label,issued_by,reason,created_at) VALUES(?,?,?,?,?) ON CONFLICT(target,label) DO UPDATE SET issued_by=excluded.issued_by, reason=excluded.reason, created_at=excluded.created_at", (target,label,actor,reason.strip(),now()))


async def remove_risk_label(username: str, label: str) -> int:
    if label not in ("SCAM", "FAKE"):
        raise ValueError("Некорректная метка")
    return await execute("DELETE FROM account_risk_labels WHERE target=? AND label=?", (norm_username(username),label))


async def create_support_ticket(tg_id: int, message: str) -> int:
    async with connect() as conn:
        cur = await conn.execute("INSERT INTO support_tickets(tg_id,message,status,created_at) VALUES(?,?,?,?)", (int(tg_id),message,"open",now()))
        await conn.commit()
        return int(cur.lastrowid)


async def get_support_ticket(ticket_id: int) -> dict | None:
    return await fetchone("SELECT * FROM support_tickets WHERE id=?", (int(ticket_id),))


async def close_support_ticket(ticket_id: int) -> None:
    await execute("UPDATE support_tickets SET status='answered', replied_at=? WHERE id=?", (now(),int(ticket_id)))


# RiseGram internal channels/groups only; Telegram channels are not affected.
DEFAULT_RISEGRAM_SPACES = (
    ("chat", "Чат RiseGram", "group"),
    ("news", "Новости RiseGram", "channel"),
    ("testrisegram", "Тестовый канал RiseGram", "channel"),
)


async def ensure_default_risegram_spaces() -> None:
    """Seed initial internal rooms once. Never recreate an intentionally removed default."""
    async with connect() as conn:
        await conn.execute("""CREATE TABLE IF NOT EXISTS rg_space_autojoin_bootstrap (
            id INTEGER PRIMARY KEY CHECK(id=1)
        )""")
        cur = await conn.execute("SELECT id FROM rg_space_autojoin_bootstrap WHERE id=1")
        if await cur.fetchone():
            return
        for alias, title, kind in DEFAULT_RISEGRAM_SPACES:
            cur = await conn.execute("SELECT space_id FROM rg_space_autojoin WHERE alias=?", (alias,))
            if await cur.fetchone():
                continue
            # Do not hijack existing groups: create explicitly owned RiseGram spaces.
            invite = __import__("secrets").token_urlsafe(12)
            cur = await conn.execute(
                "INSERT INTO rg_spaces(title,description,kind,owner_id,invite_code,created_at) VALUES(?,?,?,?,?,?)",
                (title, "Официальное пространство RiseGram", kind, int(config.OWNER_ID), invite, now()),
            )
            await conn.execute(
                "INSERT INTO rg_space_autojoin(space_id,alias,enabled,created_at) VALUES(?,?,1,?)",
                (cur.lastrowid, alias, now()),
            )
        await conn.execute("INSERT INTO rg_space_autojoin_bootstrap(id) VALUES(1)")
        await conn.commit()


async def join_default_risegram_spaces(user_id: int) -> int:
    """Idempotent enrollment into currently enabled internal spaces."""
    await ensure_default_risegram_spaces()
    async with connect() as conn:
        cur = await conn.execute(
            "INSERT OR IGNORE INTO rg_space_autojoin_enrolled(user_id,enrolled_at) VALUES(?,?)",
            (int(user_id), now()),
        )
        if cur.rowcount == 0:
            return 0  # Respect a user's choice to leave: never rejoin on later logins.
        cur = await conn.execute(
            """INSERT OR IGNORE INTO rg_space_members(space_id,user_id,role,joined_at)
               SELECT s.id, ?, 'member', ?
               FROM rg_spaces s JOIN rg_space_autojoin a ON a.space_id=s.id
               WHERE a.enabled=1 AND s.owner_id!=?""",
            (int(user_id), now(), int(user_id)),
        )
        count = max(0, cur.rowcount)
        await conn.commit()
    return count
