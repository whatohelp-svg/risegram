"""config.py — настройки RiseGram Ecosystem.

Всё секретное берётся из переменных окружения (Railway -> Variables), в коде токенов нет.

Обязательные переменные:
    BOT_TOKEN     токен от @BotFather
Необязательные:
    OWNER_ID      ID владельца (по умолчанию 7206852053)
    DB_PATH       путь к SQLite (на Railway: /data/risegram.db + подключённый Volume)
    WEBAPP_URL    ссылка на твоё приложение для кнопки «Открыть приложение»
    API_SECRET    ключ для API (/api/auth/request и т.д.); без него API не запускается
    PORT          порт API (Railway задаёт сам)
"""
from __future__ import annotations

import os


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "").strip() or default)
    except ValueError:
        return default


BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
OWNER_ID = _int("OWNER_ID", 7206852053)
ADMIN_COMMAND = "admin7206852053"
DB_PATH = os.getenv("DB_PATH", "risegram.db").strip() or "risegram.db"
WEBAPP_URL = os.getenv("WEBAPP_URL", "").strip()
API_SECRET = os.getenv("API_SECRET", "").strip()
PORT = _int("PORT", 8080)

BRAND = "RiseGram"
CURRENCY_NAME = "RiseStars"

# ── Цены (всё в Telegram Stars ⭐) ───────────────────────────────────────────
RISESTARS_PER_STAR = 100                 # 100 RiseStars = 1 ⭐
RISESTARS_PACKS = [1_000, 5_000, 10_000, 50_000, 100_000]   # кнопки в магазине
MIN_CUSTOM_RISESTARS = 100
MAX_STARS_PER_INVOICE = 10_000           # лимит одного счёта в Telegram

VERIFY_PRICE = 50                        # «Мажор»
SCAM_PRICE = 15                          # метка SCAM
NUMBER_FORMATS = {                       # +888
    "a": {"title": "+888 8 XXX", "price": 50, "prefix": "8", "digits": 3},
    "b": {"title": "+888 0XXX XXXX", "price": 25, "prefix": "0", "digits": 7},
}
CUSTOM_NUMBER_PRICE = 75
CUSTOM_NUMBER_MIN_DIGITS = 4
CUSTOM_NUMBER_MAX_DIGITS = 10
NFT_PRICE_SHORT = 14                     # 4 символа
NFT_PRICE_LONG = 10                      # 5 и больше
NFT_MIN_LEN = 4
NFT_MAX_LEN = 32
RESERVE_MINUTES = 10                     # на сколько резервируем номер/юзернейм под счёт

# Эти юзернеймы продать нельзя
RESERVED_USERNAMES = {
    "admin", "administrator", "owner", "rise", "risegram", "risestars", "support",
    "help", "bot", "telegram", "official", "moderator", "mod", "system", "root",
    "koyzzov",
}

# ── Настройки «ценность подарков» (экран «Настройки») ───────────────────────
TG_RUB_PER_100_STARS = 132.0             # «Как в Telegram»: 100 ⭐ ≈ 132 ₽
REAL_RUB_PER_100_STARS = 1.39            # «Настоящие рубли»: 100 ⭐ = 1,39 ₽

# ── Коды входа ──────────────────────────────────────────────────────────────
AUTH_CODE_LENGTH = 5
AUTH_CODE_TTL_SECONDS = 300

# ── Игры и бонусы (играют на Gram 💎 — его нельзя купить за Stars) ──────────
CRASH_MIN_BET = 1.0
CRASH_MAX_BET = 1000.0
CRASH_HOUSE_EDGE = 0.04                  # RTP 96%, честное распределение
CRASH_MAX_MULTIPLIER = 100.0
CRASH_TICK_SECONDS = 1.0
CRASH_GROWTH_PER_SECOND = 0.12           # m(t) = exp(g*t)
DAILY_BONUS_GRAM = 10.0
DAILY_BONUS_RISESTARS = 50.0
DOXNUMBER_BONUS = 500.0                  # /doxnumber: один раз, +RiseStars

# ── Переводы ────────────────────────────────────────────────────────────────
TRANSFER_MIN = 10.0
TRANSFER_FEE_PERCENT = 0.0

# ── Модули (feature flags) ──────────────────────────────────────────────────
FEATURES = {
    "games": "Игровой зал (Crash Rocket)",
    "store": "Донат-магазин",
    "inventory": "Инвентарь и скины",
    "auth_numbers": "Номера и коды входа",
    "nft_usernames": "NFT-юзернеймы",
    "verification": "Верификация «Мажор»",
    "scam_labels": "Метки SCAM",
    "top": "Таблица лидеров",
    "transfers": "Переводы RiseStars",
    "tasks": "Задания",
    "bonus": "Ежедневный бонус",
}

# ── Каталог скинов для админки (StandKnife / StandRise) ─────────────────────
SKIN_CATALOG = [
    ("Нож", "Knife: Butterfly", "Black Window", "legendary"),
    ("Винтовка", "M4A1", "Kitsune", "epic"),
    ("Нож", "Knife: Karambit", "Deep Rise", "epic"),
    ("Пистолет", "Deagle", "Sunrise", "rare"),
]

RARITY_EMOJI = {
    "common": "⚪", "rare": "🔵", "epic": "🟣", "legendary": "🟠", "mythic": "🔴",
}
