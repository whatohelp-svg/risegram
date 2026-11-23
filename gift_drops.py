"""RiseGram-only collectibles themed after Telegram gift collections.

Not Telegram/TON assets. Uses the RiseGram SQLite database and a transactional
purchase to avoid overselling or debiting a failed order.
Dataset: Gift Club, CC BY 4.0, https://gift-club.github.io/telegram-gifts-dataset/
"""
from __future__ import annotations

import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import config

CATALOG_SOURCE = "https://gift-club.github.io/telegram-gifts-dataset/data/gift-collections.json"
CATALOG_SEED = Path(__file__).with_name("data") / "telegram_gift_collections.json"
SLUG = re.compile(r"^[a-z0-9_-]{3,60}$")
REQUEST_ID = re.compile(r"^[a-zA-Z0-9_-]{8,100}$")
SCHEMA = """
CREATE TABLE IF NOT EXISTS rg_gift_catalog (
 gift_id TEXT PRIMARY KEY, title TEXT NOT NULL, image_url TEXT NOT NULL DEFAULT '',
 collection TEXT NOT NULL DEFAULT 'Telegram', active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS rg_gift_drops (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 gift_id TEXT NOT NULL REFERENCES rg_gift_catalog(gift_id),
 price_stars INTEGER NOT NULL CHECK(price_stars > 0),
 supply INTEGER NOT NULL CHECK(supply > 0),
 sold INTEGER NOT NULL DEFAULT 0,
 starts_at TEXT NOT NULL, ends_at TEXT,
 enabled INTEGER NOT NULL DEFAULT 1,
 can_upgrade INTEGER NOT NULL DEFAULT 0,
 CHECK(sold >= 0 AND sold <= supply)
);
CREATE TABLE IF NOT EXISTS rg_gift_instances (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 drop_id INTEGER NOT NULL REFERENCES rg_gift_drops(id),
 gift_id TEXT NOT NULL, owner_id INTEGER NOT NULL,
 serial INTEGER NOT NULL,
 upgraded INTEGER NOT NULL DEFAULT 0,
 purchased_at TEXT NOT NULL,
 UNIQUE(drop_id, serial)
);
CREATE INDEX IF NOT EXISTS ix_gift_instances_owner ON rg_gift_instances(owner_id);
CREATE TABLE IF NOT EXISTS rg_gift_orders (
 request_id TEXT PRIMARY KEY, owner_id INTEGER NOT NULL,
 drop_id INTEGER NOT NULL, instance_id INTEGER NOT NULL,
 price_stars INTEGER NOT NULL, created_at TEXT NOT NULL
);
"""

def utc_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

@contextmanager
def connection():
    conn = sqlite3.connect(config.DB_PATH, timeout=20)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=20000")
    try:
        yield conn
    finally:
        conn.close()

def setup():
    with connection() as conn:
        conn.executescript(SCHEMA)
        cols = {x["name"] for x in conn.execute("PRAGMA table_info(rg_gift_drops)")}
        if "can_upgrade" not in cols:
            conn.execute("ALTER TABLE rg_gift_drops ADD COLUMN can_upgrade INTEGER NOT NULL DEFAULT 0")
        cols = {x["name"] for x in conn.execute("PRAGMA table_info(rg_gift_instances)")}
        if "upgraded" not in cols:
            conn.execute("ALTER TABLE rg_gift_instances ADD COLUMN upgraded INTEGER NOT NULL DEFAULT 0")
        conn.commit()
    seed_catalog()

def add_catalog_gift(conn, gift_id, title, image_url=""):
    if not SLUG.fullmatch(gift_id) or not isinstance(title, str) or not 1 <= len(title) <= 120:
        raise ValueError("Invalid gift collection")
    conn.execute("""INSERT INTO rg_gift_catalog(gift_id,title,image_url,collection)
      VALUES(?,?,?,'Telegram') ON CONFLICT(gift_id) DO UPDATE SET
      title=excluded.title, image_url=excluded.image_url""",
      (gift_id, title, image_url))

def seed_catalog(rows=None):
    if rows is None:
        rows = json.loads(CATALOG_SEED.read_text(encoding="utf-8"))
    if not isinstance(rows, list) or len(rows) < 1 or len(rows) > 5000:
        raise ValueError("Invalid catalog data")
    with connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            count = 0
            for row in rows:
                if not isinstance(row, dict):
                    continue
                gift_id, title = row.get("gift_id", row.get("slug", "")), row.get("title", row.get("name", ""))
                if not SLUG.fullmatch(str(gift_id)) or not isinstance(title, str):
                    continue
                add_catalog_gift(conn, gift_id, title)
                count += 1
            conn.commit()
            return count
        except Exception:
            conn.rollback()
            raise

def catalog(page=0, per_page=50):
    page = max(0, int(page))
    with connection() as conn:
        count = conn.execute("SELECT count(*) FROM rg_gift_catalog WHERE active=1").fetchone()[0]
        rows = conn.execute("""SELECT gift_id,title,image_url FROM rg_gift_catalog WHERE active=1
          ORDER BY title COLLATE NOCASE LIMIT ? OFFSET ?""",
          (min(50, max(1, int(per_page))), page * min(50, max(1, int(per_page))))).fetchall()
        return [dict(r) for r in rows], count

def gift_info(gift_id):
    with connection() as conn:
        r = conn.execute("SELECT * FROM rg_gift_catalog WHERE gift_id=? AND active=1", (gift_id,)).fetchone()
        return dict(r) if r else None

def create_drop(gift_id, price_stars, supply, starts_at=None, can_upgrade=False, ends_at=None):
    # No product-level cap on drop size. Keep the native MTProto signed int32
    # bound so a published gift can be represented safely by the Android client.
    if type(price_stars) is not int or not 1 <= price_stars <= 2**31 - 1:
        raise ValueError("Price must be 1..2,147,483,647 RiseStars")
    if type(supply) is not int or not 1 <= supply <= 2**31 - 1:
        raise ValueError("Quantity must be 1..2,147,483,647")
    starts_at = starts_at or utc_now()
    if ends_at is not None and ends_at <= starts_at:
        raise ValueError("Invalid end date")
    with connection() as conn:
        cur = conn.execute("""INSERT INTO rg_gift_drops
          (gift_id,price_stars,supply,starts_at,ends_at,can_upgrade)
          SELECT gift_id,?,?,?,?,? FROM rg_gift_catalog
          WHERE gift_id=? AND active=1""",
          (price_stars,supply,starts_at,ends_at,int(bool(can_upgrade)),gift_id))
        if cur.rowcount != 1:
            raise ValueError("Gift not in catalog")
        conn.commit()
        return cur.lastrowid

def list_live_drops(at=None):
    at = at or utc_now()
    with connection() as conn:
        return [dict(r) for r in conn.execute("""SELECT d.*, c.title, c.image_url
        FROM rg_gift_drops d JOIN rg_gift_catalog c ON c.gift_id=d.gift_id
        WHERE d.enabled=1 AND c.active=1 AND d.starts_at<=?
          AND (d.ends_at IS NULL OR d.ends_at>?) AND d.sold<d.supply
        ORDER BY d.starts_at DESC, d.id DESC""", (at,at))]

def get_admin_drop(drop_id):
    with connection() as conn:
        row = conn.execute("""SELECT d.*, c.title FROM rg_gift_drops d
            JOIN rg_gift_catalog c ON c.gift_id = d.gift_id
            WHERE d.id = ?""", (int(drop_id),)).fetchone()
        return dict(row) if row else None

def count_admin_drops():
    with connection() as conn:
        return conn.execute("SELECT COUNT(*) FROM rg_gift_drops").fetchone()[0]

def list_admin_drops(limit=20, offset=0):
    # LIMIT -1 remains supported for the publisher, which must process every drop.
    limit = int(limit)
    offset = max(0, int(offset))
    with connection() as conn:
        return [dict(r) for r in conn.execute("""SELECT d.*,c.title FROM rg_gift_drops d
        JOIN rg_gift_catalog c ON c.gift_id=d.gift_id
        ORDER BY d.id DESC LIMIT ? OFFSET ?""",(limit,offset))]

def toggle_drop(drop_id):
    with connection() as conn:
        cur=conn.execute("UPDATE rg_gift_drops SET enabled=1-enabled WHERE id=?",(drop_id,))
        conn.commit()
        return cur.rowcount > 0

def purchase(owner_id, drop_id, request_id, at=None):
    """Idempotent SQLite transaction; caller MUST derive owner_id from server-side auth."""
    if type(owner_id) is not int or owner_id <= 0 or not REQUEST_ID.fullmatch(str(request_id)):
        raise ValueError("Invalid purchase request")
    at = at or utc_now()
    with connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            old=conn.execute("SELECT owner_id,instance_id FROM rg_gift_orders WHERE request_id=?",
                             (request_id,)).fetchone()
            if old:
                if old["owner_id"] != owner_id:
                    raise ValueError("Request belongs to another account")
                conn.commit()
                return old["instance_id"]
            d=conn.execute("""SELECT d.gift_id,d.price_stars,d.supply,d.sold
                FROM rg_gift_drops d JOIN rg_gift_catalog c ON c.gift_id=d.gift_id
                WHERE d.id=? AND d.enabled=1 AND c.active=1 AND d.starts_at<=?
                  AND (d.ends_at IS NULL OR d.ends_at>?)""",
                (drop_id,at,at)).fetchone()
            if not d or d["sold"] >= d["supply"]:
                raise ValueError("Drop unavailable or sold out")
            updated=conn.execute("""UPDATE users SET risestars=risestars-?
                WHERE tg_id=? AND banned=0 AND risestars>=?""",
                (d["price_stars"],owner_id,d["price_stars"]))
            if updated.rowcount != 1:
                raise ValueError("Not enough RiseStars")
            serial=d["sold"]+1
            conn.execute("UPDATE rg_gift_drops SET sold=sold+1 WHERE id=?",(drop_id,))
            item=conn.execute("""INSERT INTO rg_gift_instances
                (drop_id,gift_id,owner_id,serial,purchased_at) VALUES(?,?,?,?,?)""",
                (drop_id,d["gift_id"],owner_id,serial,at)).lastrowid
            conn.execute("""INSERT INTO rg_gift_orders
                (request_id,owner_id,drop_id,instance_id,price_stars,created_at)
                VALUES(?,?,?,?,?,?)""",
                (request_id,owner_id,drop_id,item,d["price_stars"],at))
            conn.commit()
            return item
        except Exception:
            conn.rollback()
            raise

def inventory(owner_id):
    with connection() as conn:
        return [dict(r) for r in conn.execute("""SELECT i.id,i.serial,i.upgraded,i.purchased_at,
        c.gift_id,c.title,c.image_url,d.can_upgrade
        FROM rg_gift_instances i JOIN rg_gift_catalog c ON c.gift_id=i.gift_id
        JOIN rg_gift_drops d ON d.id=i.drop_id
        WHERE i.owner_id=? ORDER BY i.id DESC""",(owner_id,))]

def upgrade(owner_id, instance_id):
    """RiseGram-local cosmetic upgrade, no TON or Telegram NFT conversion."""
    with connection() as conn:
        cur=conn.execute("""UPDATE rg_gift_instances SET upgraded=1 WHERE id=? AND owner_id=?
         AND upgraded=0 AND EXISTS(SELECT 1 FROM rg_gift_drops d
         WHERE d.id=rg_gift_instances.drop_id AND d.can_upgrade=1)""",
         (instance_id,owner_id))
        conn.commit()
        return cur.rowcount == 1
