import aiosqlite
import random

DB_PATH = "risegram.db"

def gen_phone():
    return "+7 (7" + "".join([str(random.randint(0, 9)) for _ in range(9)]) + ")"

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                tg_id INTEGER PRIMARY KEY,
                rise_id INTEGER,
                name TEXT,
                username TEXT,
                phone TEXT,
                risestars REAL DEFAULT 0.0,
                gram_balance REAL DEFAULT 0.0,
                banned INTEGER DEFAULT 0,
                verified INTEGER DEFAULT 0,
                auth_code TEXT DEFAULT '12345'
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS testers (
                tg_id INTEGER PRIMARY KEY,
                username TEXT,
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS admin_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                admin_id INTEGER,
                action_text TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS user_inventory (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tg_id INTEGER,
                item_name TEXT,
                item_type TEXT,
                acquired_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS purchases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tg_id INTEGER,
                price_xtr INTEGER,
                item_desc TEXT,
                purchased_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS feature_flags (
                feature_name TEXT PRIMARY KEY,
                is_enabled INTEGER DEFAULT 1
            )
        """)
        
        default_features = [
            ("games", 1),
            ("store", 1),
            ("inventory", 1),
            ("auth_numbers", 1),
            ("nft_usernames", 1),
            ("verification", 1),
            ("scam_labels", 1)
        ]
        for fname, status in default_features:
            await db.execute(
                "INSERT OR IGNORE INTO feature_flags (feature_name, is_enabled) VALUES (?, ?)",
                (fname, status)
            )
        await db.commit()

async def get_or_create_user(tg_id: int, full_name: str = "User", username: str | None = None):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM users WHERE tg_id = ?", (tg_id,))
        user = await cur.fetchone()
        if not user:
            rise_id = random.randint(100000000, 999999999)
            phone = gen_phone()
            uname = f"@{username}" if username else f"@user_{tg_id}"
            auth_code = "".join([str(random.randint(0, 9)) for _ in range(5)])
            await db.execute(
                "INSERT INTO users (tg_id, rise_id, name, username, phone, risestars, gram_balance, banned, verified, auth_code) VALUES (?, ?, ?, ?, ?, 0, 0.0, 0, 0, ?)",
                (tg_id, rise_id, full_name, uname, phone, auth_code),
            )
            await db.commit()
            cur = await db.execute("SELECT * FROM users WHERE tg_id = ?", (tg_id,))
            user = await cur.fetchone()
        return dict(user)

async def is_feature_on(feature_name: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT is_enabled FROM feature_flags WHERE feature_name = ?", (feature_name,))
        row = await cur.fetchone()
        return bool(row[0]) if row else True
        
