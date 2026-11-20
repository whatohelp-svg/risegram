"""api.py — HTTP API для твоего клиента RiseGram (вход по номеру и коду из бота).

Включается, только если задана переменная API_SECRET. Вызывай API с СВОЕГО сервера, ключ нельзя
зашивать в публичное приложение.

    POST /api/auth/request   {"phone": "+7 777 111-22-33"}            -> код уходит владельцу номера в бота
    POST /api/auth/verify    {"phone": "...", "code": "12345"}         -> {"token": "...", "user": {...}}
    GET  /api/me             Authorization: Bearer <token>             -> профиль
    GET  /health

Код входа подтверждается одноразовым кодом из бота; остальные запросы приложения используют Bearer-токен сессии.
"""
from __future__ import annotations

from pathlib import Path

import hmac
import logging
import math
import time

from aiohttp import web

import config
import database as db
from ui import esc, safe_send

log = logging.getLogger("risegram.api")

_last_request: dict[str, float] = {}
_last_ip_request: dict[str, float] = {}
REQUEST_COOLDOWN = 30.0
IP_REQUEST_COOLDOWN = 5.0

# Names/emoji are presentation labels only. The gift IDs and availability are read
# from Telegram at runtime with getAvailableGifts(), so newly added bot-sendable gifts
# appear without a code update.
GIFT_LABELS = {
    "5170145012310081615": ("Love Heart", "💝"),
    "5170233102089322756": ("Teddy Bear", "🧸"),
    "5170250947678437525": ("Gift Box", "🎁"),
    "5168103777563050263": ("Red Rose", "🌹"),
    "5170144170496491616": ("Birthday Cake", "🎂"),
    "5170314324215857265": ("Bouquet of Flowers", "💐"),
    "5170564780938756245": ("Rocket", "🚀"),
    "6028601630662853006": ("Champagne Bottle", "🍾"),
    "5168043875654172773": ("Trophy", "🏆"),
    "5170690322832818290": ("Diamond Ring", "💍"),
    "5170521118301225164": ("Gem", "💎"),
}


def public_user(u: dict) -> dict:
    return {
        "tg_id": u["tg_id"],
        "rise_id": u["rise_id"],
        "name": u.get("name"),
        "username": db.shown_username(u).lstrip("@") if u.get("username") or u.get("active_username") else None,
        "phone": u.get("phone"),
        "verified": bool(u.get("verified")),
        "risestars": float(u.get("risestars") or 0),
        "gram_balance": float(u.get("gram_balance") or 0),
        "is_admin": int(u["tg_id"]) == int(config.OWNER_ID),
    }


def bearer_token(request: web.Request) -> str:
    auth = request.headers.get("Authorization", "")
    return auth[7:].strip() if auth.lower().startswith("bearer ") else ""


async def session_user(request: web.Request) -> dict | None:
    token = bearer_token(request)
    user = await db.get_session_user(token) if token else None
    if not user or user.get("banned"):
        return None
    return user


def gift_attr(gift, key: str, default=None):
    if isinstance(gift, dict):
        return gift.get(key, default)
    return getattr(gift, key, default)


async def telegram_gift_catalog(bot) -> list[dict]:
    """Текущий список подарков, которые именно этот бот может отправлять."""
    response = await bot.get_available_gifts()
    gifts = gift_attr(response, "gifts", []) or []
    out = []
    for gift in gifts:
        gift_id = str(gift_attr(gift, "id", "") or "")
        if not gift_id:
            continue
        sticker = gift_attr(gift, "sticker")
        fallback_name, fallback_emoji = GIFT_LABELS.get(gift_id, ("Telegram Gift", "🎁"))
        emoji = gift_attr(sticker, "emoji", None) or fallback_emoji
        out.append({
            "gift_id": gift_id,
            "name": fallback_name,
            "emoji": emoji,
            "telegram_stars": int(gift_attr(gift, "star_count", 0) or 0),
            "total_count": gift_attr(gift, "total_count"),
            "remaining_count": gift_attr(gift, "remaining_count"),
        })
    return out


async def core_request_code(bot, phone: str, remote_ip: str = "") -> tuple[int, dict]:
    digits = db.digits_of(phone)
    if len(digits) < 7:
        return 400, {"ok": False, "error": "bad_phone"}
    now = time.monotonic()
    ip_key = remote_ip or "unknown"
    if now - _last_ip_request.get(ip_key, 0.0) < IP_REQUEST_COOLDOWN:
        return 429, {"ok": False, "error": "too_many_requests"}
    if now - _last_request.get(digits, 0.0) < REQUEST_COOLDOWN:
        return 429, {"ok": False, "error": "too_many_requests"}
    _last_ip_request[ip_key] = now
    _last_request[digits] = now
    if len(_last_request) > 50000:
        _last_request.clear()
    if len(_last_ip_request) > 20000:
        _last_ip_request.clear()
    user = await db.fetchone("SELECT * FROM users WHERE phone_digits=?", (digits,))
    if not user or user["banned"] or not await db.is_feature_on("auth_numbers"):
        return 200, {"ok": True}  # одинаковый ответ, чтобы нельзя было перебирать номера
    code = await db.issue_auth_code(user["tg_id"])
    await safe_send(
        bot,
        user["tg_id"],
        f"🔒 <b>Код входа в {config.BRAND}</b>\n\nНомер: <code>{esc(user['phone'])}</code>\nКод: <code>{code}</code>\n\n"
        f"Действует {config.AUTH_CODE_TTL_SECONDS // 60} мин. Никому его не сообщайте. Если это были не вы, просто проигнорируйте.",
    )
    return 200, {"ok": True}


async def core_verify(phone: str, code: str) -> tuple[int, dict]:
    user = await db.verify_auth_code(phone, code)
    if not user:
        return 401, {"ok": False, "error": "invalid_code"}
    token = await db.create_session(user["tg_id"])
    return 200, {"ok": True, "token": token, "user": public_user(user)}


def _key_ok(request: web.Request) -> bool:
    if not config.API_SECRET:
        return False
    # сравниваем байты: hmac.compare_digest со строками падает (TypeError → 500) на не-ASCII в заголовке
    got = request.headers.get("X-API-Key", "").encode("utf-8", "replace")
    return hmac.compare_digest(got, config.API_SECRET.encode("utf-8"))


async def _json(request: web.Request) -> dict:
    try:
        data = await request.json()
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


async def h_request(request: web.Request):
    # These two routes are intentionally public: the native WebView cannot safely
    # contain API_SECRET. Responses do not reveal whether a phone is registered.
    data = await _json(request)
    status, body = await core_request_code(
        request.app["bot"], str(data.get("phone", "")), request.remote or ""
    )
    return web.json_response(body, status=status)


async def h_verify(request: web.Request):
    data = await _json(request)
    status, body = await core_verify(str(data.get("phone", "")), str(data.get("code", "")))
    return web.json_response(body, status=status)


async def h_me(request: web.Request):
    user = await session_user(request)
    if not user:
        return web.json_response({"ok": False, "error": "unauthorized"}, status=401)
    return web.json_response({"ok": True, "user": public_user(user)})


async def h_gifts(request: web.Request):
    user = await session_user(request)
    if not user:
        return web.json_response({"ok": False, "error": "unauthorized"}, status=401)
    is_admin = int(user["tg_id"]) == int(config.OWNER_ID)
    return web.json_response({
        "ok": True,
        "drops": await db.list_gift_drops(active_only=not is_admin),
        "inventory": await db.list_user_gifts(user["tg_id"]),
    })


async def h_buy_gift(request: web.Request):
    user = await session_user(request)
    if not user:
        return web.json_response({"ok": False, "error": "unauthorized"}, status=401)
    data = await _json(request)
    try:
        drop_id = int(data.get("drop_id"))
    except (TypeError, ValueError):
        return web.json_response({"ok": False, "error": "bad_drop_id"}, status=400)
    ok, message, reserved = await db.reserve_gift_drop(user["tg_id"], drop_id)
    if not ok:
        return web.json_response({"ok": False, "error": message}, status=409)
    try:
        # This sends the actual Telegram gift. Telegram charges the bot's own Stars
        # balance; the buyer pays the RiseStars price configured by the owner.
        sent = await request.app["bot"].send_gift(
            user_id=user["tg_id"],
            gift_id=reserved["telegram_gift_id"],
            text="Подарок из RiseGram 🎁",
        )
        if sent is False:
            raise RuntimeError("Telegram did not confirm gift delivery")
    except Exception as exc:  # noqa: BLE001
        log.warning("gift delivery failed gift=%s uid=%s: %s",
                    reserved["telegram_gift_id"], user["tg_id"], exc)
        await db.rollback_gift_purchase(reserved["instance_id"])
        return web.json_response(
            {"ok": False, "error": "Telegram не смог отправить подарок. RiseStars и тираж возвращены."},
            status=503,
        )
    await db.confirm_gift_purchase(reserved["instance_id"])
    refreshed = await db.get_user(user["tg_id"])
    return web.json_response({
        "ok": True,
        "gift": reserved,
        "user": public_user(refreshed),
        "drops": await db.list_gift_drops(active_only=True),
        "inventory": await db.list_user_gifts(user["tg_id"]),
    })


async def h_exchange(request: web.Request):
    user = await session_user(request)
    if not user:
        return web.json_response({"ok": False, "error": "unauthorized"}, status=401)
    data = await _json(request)
    ok, message, balances = await db.exchange_balance(
        user["tg_id"], str(data.get("direction", "")), data.get("amount")
    )
    if not ok:
        return web.json_response({"ok": False, "error": message}, status=400)
    refreshed = await db.get_user(user["tg_id"])
    return web.json_response({"ok": True, "user": public_user(refreshed)})


async def h_admin_gift_catalog(request: web.Request):
    user = await session_user(request)
    if not user or int(user["tg_id"]) != int(config.OWNER_ID):
        return web.json_response({"ok": False, "error": "forbidden"}, status=403)
    try:
        gifts = await telegram_gift_catalog(request.app["bot"])
    except Exception as exc:  # noqa: BLE001
        log.exception("getAvailableGifts failed")
        return web.json_response({"ok": False, "error": f"Не удалось получить каталог Telegram: {exc}"}, status=502)
    return web.json_response({"ok": True, "gifts": gifts})


async def h_admin_create_drop(request: web.Request):
    user = await session_user(request)
    if not user or int(user["tg_id"]) != int(config.OWNER_ID):
        return web.json_response({"ok": False, "error": "forbidden"}, status=403)
    data = await _json(request)
    gift_id = str(data.get("gift_id", "")).strip()
    try:
        quantity = int(data.get("quantity"))
        price = float(data.get("price_risestars"))
    except (TypeError, ValueError):
        return web.json_response({"ok": False, "error": "Количество и цена должны быть числами."}, status=400)
    if not gift_id or quantity < 1 or quantity > 1_000_000 or not math.isfinite(price) or price <= 0 or price > 1e12:
        return web.json_response({"ok": False, "error": "Проверьте подарок, тираж и цену."}, status=400)
    try:
        catalog = await telegram_gift_catalog(request.app["bot"])
    except Exception as exc:  # noqa: BLE001
        log.exception("getAvailableGifts failed during drop creation")
        return web.json_response({"ok": False, "error": "Каталог Telegram сейчас недоступен."}, status=502)
    gift = next((g for g in catalog if g["gift_id"] == gift_id), None)
    if not gift:
        return web.json_response({"ok": False, "error": "Этот подарок больше недоступен для отправки ботом."}, status=409)
    drop_id = await db.create_gift_drop(
        gift["gift_id"], gift["name"], gift["emoji"], gift["telegram_stars"], price, quantity
    )
    await db.log_admin_action(
        config.OWNER_ID,
        f"Дроп подарка #{drop_id}: {gift['name']} ×{quantity} за {price:g} RiseStars"
    )
    return web.json_response({
        "ok": True,
        "drop_id": drop_id,
        "drop": (await db.list_gift_drops(active_only=False))[0],
    })


async def h_index(request: web.Request):
    index_path = Path(__file__).with_name("index.html")
    if not index_path.is_file():
        return web.Response(text="RiseGram UI not found", status=404)
    return web.FileResponse(index_path)


async def h_health(request: web.Request):
    return web.json_response({"ok": True})


async def start_api(bot) -> web.AppRunner | None:
    # Auth is protected by one-time bot codes and session tokens; the secret must
    # never be shipped inside the Android WebView.
    app = web.Application(client_max_size=64 * 1024)
    app["bot"] = bot
    app.add_routes([
        web.get("/", h_index),
        web.post("/api/auth/request", h_request),
        web.post("/api/auth/verify", h_verify),
        web.get("/api/me", h_me),
        web.get("/api/gifts", h_gifts),
        web.post("/api/gifts/buy", h_buy_gift),
        web.post("/api/exchange", h_exchange),
        web.get("/api/admin/gifts/catalog", h_admin_gift_catalog),
        web.post("/api/admin/gifts/drop", h_admin_create_drop),
        web.get("/health", h_health),
    ])
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", config.PORT).start()
    log.info("HTTP API слушает порт %s", config.PORT)
    return runner
