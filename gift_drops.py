"""Telegram-themed gift drops for RiseGram. These are RiseGram inventory items, not TON NFTs."""
import sqlite3
from datetime import datetime, timezone

DDL = """
CREATE TABLE IF NOT EXISTS gift_catalog (
 gift_id TEXT PRIMARY KEY, title TEXT NOT NULL, image_url TEXT NOT NULL DEFAULT '',
 collection TEXT NOT NULL DEFAULT 'Telegram', active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS gift_drops (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 gift_id TEXT NOT NULL REFERENCES gift_catalog(gift_id),
 price_stars INTEGER NOT NULL CHECK(price_stars > 0),
 supply INTEGER NOT NULL CHECK(supply > 0),
 sold INTEGER NOT NULL DEFAULT 0,
 starts_at TEXT NOT NULL,
 ends_at TEXT,
 enabled INTEGER NOT NULL DEFAULT 1,
 CHECK(sold >= 0 AND sold <= supply)
);
CREATE TABLE IF NOT EXISTS gift_instances (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 drop_id INTEGER NOT NULL REFERENCES gift_drops(id),
 gift_id TEXT NOT NULL,
 owner_id INTEGER NOT NULL,
 serial INTEGER NOT NULL,
 purchased_at TEXT NOT NULL,
 UNIQUE(drop_id, serial)
);
CREATE INDEX IF NOT EXISTS ix_gift_instances_owner ON gift_instances(owner_id);
CREATE TABLE IF NOT EXISTS gift_orders (
 request_id TEXT PRIMARY KEY,
 owner_id INTEGER NOT NULL,
 drop_id INTEGER NOT NULL,
 instance_id INTEGER NOT NULL,
 price_stars INTEGER NOT NULL,
 created_at TEXT NOT NULL
);
"""

def setup(conn):
    conn.executescript(DDL)

def utc_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

def add_catalog_gift(conn, gift_id, title, image_url="", collection="Telegram"):
    if not gift_id or not title or collection != "Telegram":
        raise ValueError("Only named Telegram catalog gifts are supported")
    conn.execute("INSERT INTO gift_catalog(gift_id,title,image_url,collection) VALUES(?,?,?,?) ON CONFLICT(gift_id) DO UPDATE SET title=excluded.title,image_url=excluded.image_url",
                 (gift_id, title, image_url, collection))
    conn.commit()

def create_drop(conn, gift_id, price_stars, supply, starts_at, ends_at=None):
    if not isinstance(price_stars,int) or price_stars <= 0 or not isinstance(supply,int) or supply <= 0:
        raise ValueError("Price and supply must be positive integers")
    if ends_at is not None and ends_at <= starts_at:
        raise ValueError("End must be after start")
    cur=conn.execute("INSERT INTO gift_drops(gift_id,price_stars,supply,starts_at,ends_at) SELECT gift_id,?,?,?,? FROM gift_catalog WHERE gift_id=? AND active=1,
                     (price_stars,supply,starts_at,ends_at,gift_id))
    if not cur.rowcount:
        raise ValueError("Unknown gift")
    conn.commit()
    return cur.lastrowid

def list_live_drops(conn, at=None):
    at=at or utc_now()
    return conn.execute("""SELECT d.id,c.gift_id,c.title,c.image_url,d.price_stars,d.supply,d.sold,d.starts_at,d.ends_at
        FROM gift_drops d JOIN gift_catalog c ON c.gift_id=d.gift_id
        WHERE d.enabled=1 AND c.active=1 AND d.starts_at<=? AND (d.ends_at IS NULL OR d.ends_at>?) AND d.sold<d.supply
        ORDER BY d.starts_at DESC""",(at,at)).fetchall()

def purchase(conn, owner_id, drop_id, request_id, at=None):
    """Atomic debit + stock reservation + inventory issue. Caller must authenticate owner_id."""
    if not request_id or not isinstance(owner_id,int) or owner_id <= 0:
        raise ValueError("Invalid purchase request")
    at=at or utc_now()
    conn.execute("BEGIN IMMEDIATE")
    try:
        existing=conn.execute("SELECT instance_id FROM gift_orders WHERE request_id=? AND owner_id=?",(request_id,owner_id)).fetchone()
        if existing:
            conn.commit()
            return existing[0]
        if conn.execute("SELECT 1 FROM gift_orders WHERE request_id=?",(request_id,)).fetchone():
            raise ValueError("Request ID already belongs to another user")
        d=conn.execute("""SELECT d.gift_id,d.price_stars,d.supply,d.sold FROM gift_drops d JOIN gift_catalog c ON c.gift_id=d.gift_id
            WHERE d.id=? AND d.enabled=1 AND c.active=1 AND d.starts_at<=? AND (d.ends_at IS NULL OR d.ends_at>?)""",(drop_id,at,at)).fetchone()
        if not d or d[3]>=d[2]:
            raise ValueError("Drop unavailable or sold out")
        charged=conn.execute("UPDATE users SET risestars=risestars-? WHERE tg_id=? AND banned=0 AND risestars>=?",(d[1],owner_id,d[1]))
        if charged.rowcount != 1:
            raise ValueError("Insufficient RiseStars or inactive account")
        serial=d[3]+1
        conn.execute("UPDATE gift_drops SET sold=sold+1 WHERE id=?",(drop_id,))
        instance=conn.execute("INSERT INTO gift_instances(drop_id,gift_id,owner_id,serial,purchased_at) VALUES(?,?,?,?,?)",
                              (drop_id,d[0],owner_id,serial,at)).lastrowid
        conn.execute("INSERT INTO gift_orders(request_id,owner_id,drop_id,instance_id,price_stars,created_at) VALUES(?,?,?,?,?,?)",
                     (request_id,owner_id,drop_id,instance,d[1],at))
        conn.commit()
        return instance
    except Exception:
        conn.rollback()
        raise

def inventory(conn, owner_id):
    return conn.execute("""SELECT i.id,i.serial,i.purchased_at,c.gift_id,c.title,c.image_url
      FROM gift_instances i JOIN gift_catalog c ON c.gift_id=i.gift_id WHERE i.owner_id=? ORDER BY i.id DESC""",(owner_id,)).fetchall()
