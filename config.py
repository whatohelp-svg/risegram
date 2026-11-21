"""config.py — настройки RiseGram Ecosystem.

Всё секретное берётся из переменных окружения (Railway -> Variables), в коде токенов нет.

Обязательные переменные:
    BOT_TOKEN     токен от @BotFather
Необязательные:
    OWNER_ACCOUNT_ID внутренний ID владельца (по умолчанию 1)
    OWNER_TELEGRAM_ID Telegram ID владельца только для доставки кодов и проверки админки
    DB_PATH       путь к SQLite (на Railway: /data/risegram.db + подключённый Volume)
    WEBAPP_URL    ссылка на твоё приложение для кнопки «Открыть приложение»
    API_SECRET    необязательный внутренний секрет; не встраивать в APK
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
OWNER_ID = _int("OWNER_ACCOUNT_ID", 1)  # internal RiseGram account key
OWNER_TELEGRAM_ID = _int("OWNER_TELEGRAM_ID", 7206852053)  # private bot delivery link
OWNER_USERNAME = os.getenv("OWNER_USERNAME", "koyyzzback").strip().lstrip("@").lower()
STARTER_NFT_USERNAMES = tuple(dict.fromkeys(
    n.strip().lstrip("@").lower() for n in os.getenv("STARTER_NFT_USERNAMES", "koyyzz,owner,rise").split(",") if n.strip()
))
ADMIN_COMMAND = "admin"
OWNER_COMMAND = "owner"
DB_PATH = os.getenv("DB_PATH", "risegram.db").strip() or "risegram.db"
MEDIA_PATH = os.getenv("MEDIA_PATH", "/data/risegram-media").strip() or "/data/risegram-media"
MTPROTO_OTP_SECRET = os.getenv("MTPROTO_OTP_SECRET", "").strip()
MTPROTO_ADMIN_API_URL = os.getenv("MTPROTO_ADMIN_API_URL", "").strip().rstrip("/")
MTPROTO_ADMIN_API_TOKEN = os.getenv("MTPROTO_ADMIN_API_TOKEN", "").strip()
WEBAPP_URL = os.getenv("WEBAPP_URL", "").strip()
API_SECRET = os.getenv("API_SECRET", "").strip()
PORT = _int("PORT", 8080)

BRAND = "RiseGram"
CURRENCY_NAME = "RiseStars"
GRAM_TO_RISESTARS = 100  # 1 Gram = 100 RiseStars

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
    "major", "koyzzov",
}

# ── Настройки «ценность подарков» (экран «Настройки») ───────────────────────
TG_RUB_PER_100_STARS = 132.0             # «Как в Telegram»: 100 ⭐ ≈ 132 ₽
REAL_RUB_PER_100_STARS = 1.39            # «Настоящие рубли»: 100 ⭐ = 1,39 ₽

# ── Коды входа ──────────────────────────────────────────────────────────────
AUTH_CODE_LENGTH = 5
AUTH_CODE_TTL_SECONDS = 300

# ── Игры и бонусы (ставки в 💎 Gram или ⭐ RiseStars) ───────────────────
# Лимиты ставок: gram = 💎 Gram, stars = ⭐ RiseStars (внутренние валюты, вывода нет).
# Обе валюты: от 1 до 100 000 000. Реальный потолок ставки — баланс игрока.
GAME_LIMITS = {"gram": (1.0, 100_000_000.0), "stars": (1.0, 100_000_000.0)}
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
    "auth_numbers": "Номера и коды входа",
    "nft_usernames": "NFT-юзернеймы",
    "verification": "Верификация «Мажор»",
    "scam_labels": "Метки SCAM",
    "top": "Таблица лидеров",
    "transfers": "Переводы RiseStars",
    "tasks": "Задания",
    "bonus": "Ежедневный бонус",
    "market": "Маркет",
}

# ── Хай-Лоу ─────────────────────────────────────────────────────────────────
HL_HOUSE_EDGE = 0.04                     # множитель шага = (1 - edge) / шанс угадать
HL_MAX_MULTIPLIER = 100.0
HL_IDLE_SECONDS = 600                    # забытая игра автоматически закрывается с выплатой текущего множителя

# ── Маркет (цены в RiseStars) ───────────────────────────────────────────────
MARKET_FEE_PERCENT = 5.0                 # комиссия берётся с продавца
MARKET_MIN_PRICE = 10.0
MARKET_MAX_PRICE = 1_000_000_000.0
MARKET_PAGE = 6

# ── Владелец: обычный кошелёк ───────────────────────────────────────────────
# Стартовые значения совпадают с текущим RiseGram wallet в Android-клиенте.
OWNER_START_RISESTARS = 1_000.0
OWNER_START_GRAM = 10.0
INFINITE = 1e15  # legacy sentinel; owner balances are no longer infinite
OWNER_RISE_ID = 0  # assigned a random unique 8-digit value on first initialization


def is_reserved_username(name: str | None) -> bool:
    """Юзернеймы, которые купить нельзя: служебные, всё на «rise…», всё с risegram / risestars."""
    n = (name or "").strip().lstrip("@").lower()
    if n in STARTER_NFT_USERNAMES:
        return False
    return n in RESERVED_USERNAMES or n.startswith("rise") or "risegram" in n or "risestars" in n
