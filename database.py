# ============================================================================
# RISEGRAM ECOSYSTEM - DATABASE CORE (aiosqlite)
# ============================================================================
import aiosqlite
import random

DB_PATH = "risegram.db"

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        # Таблица юзеров со всеми привязками, балансами, флагами
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                tg_id INTEGER PRIMARY KEY,
                rise_id INTEGER UNIQUE,
                name TEXT,
                username TEXT,
                phone TEXT,
                risestars INTEGER DEFAULT 10000,
                gram_balance REAL DEFAULT 100.0,
                starter_claimed INTEGER DEFAULT 0,
                verified INTEGER DEFAULT 0,
                scam_mark INTEGER DEFAULT 0,
                banned INTEGER DEFAULT 0,
                price_mode TEXT DEFAULT 'rub',
                auth_code TEXT DEFAULT NULL
            )
        """)
        
        # Лог транзакций и покупок за Telegram Stars
        await db.execute("""
            CREATE TABLE IF NOT EXISTS purchases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tg_id INTEGER,
                title TEXT,
                price_xtr INTEGER,
                charge_id TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Динамические задания
        await db.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT,
                reward INTEGER,
                active INTEGER DEFAULT 1
            )
        """)
        
        # Глобальные тумблеры фич (админ может рубить функционал на лету)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS feature_flags (
                feature_name TEXT PRIMARY KEY,
                is_enabled INTEGER DEFAULT 1
            )
        """)
        
        # Дефолтные флаги фич
        default_flags = [
            ("games_rocket", 1),
            ("games_dice", 1),
            ("games_coin", 1),
            ("shop_store", 1),
            ("tasks_system", 1)
        ]
        for f_name, f_val in default_flags:
            await db.execute(
                "INSERT OR IGNORE INTO feature_flags (feature_name, is_enabled) VALUES (?, ?)", 
                (f_name, f_val)
            )

        # Дефолтное задание
        cur = await db.execute("SELECT COUNT(*) FROM tasks")
        if (await cur.fetchone())[0] == 0:
            await db.execute("INSERT INTO tasks (title, reward, active) VALUES (?, ?, ?)", ("Подписаться на канал RiseGram", 500, 1))
            
        await db.commit()

def gen_phone() -> str:
    return f"+7 982 {random.randint(100, 999)}-{random.randint(10, 99)}-{random.randint(10, 99)}"

async def get_or_create_user(tg_id: int, full_name: str, username: str | None):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM users WHERE tg_id = ?", (tg_id,))
        user = await cur.fetchone()
        if not user:
            rise_id = random.randint(100000000, 999999999)
            phone = gen_phone()
            uname = f"@{username}" if username else f"@user_{tg_id}"
            await db.execute(
                "INSERT INTO users (tg_id, rise_id, name, username, phone) VALUES (?, ?, ?, ?, ?)",
                (tg_id, rise_id, full_name, uname, phone),
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
      
