"""api.py — HTTP API для твоего клиента RiseGram (вход по номеру и коду из бота).

Включается, только если задана переменная API_SECRET. Вызывай API с СВОЕГО сервера, ключ нельзя
зашивать в публичное приложение.

    POST /api/auth/request   {"phone": "+7 777 111-22-33"}            -> код уходит владельцу номера в бота
    POST /api/auth/verify    {"phone": "...", "code": "12345"}         -> {"token": "...", "user": {...}}
    GET  /api/me             Authorization: Bearer <token>             -> профиль
    GET  /health

Во всех запросах (кроме /health и /api/me) нужен заголовок  X-API-Key: <API_SECRET>.
"""
from __future__ import annotations

import hmac
import logging
import time

from aiohttp import web

import config
import database as db
from ui import esc, safe_send

log = logging.getLogger("risegram.api")

_last_request: dict[str, float] = {}
REQUEST_COOLDOWN = 30.0


def public_user(u: dict) -> dict:
    return {
        "tg_id": u["tg_id"],
        "rise_id": u["rise_id"],
        "name": u.get("name"),
        "username": db.shown_username(u).lstrip("@") if u.get("username") or u.get("active_username") else None,
        "phone": u.get("phone"),
        "verified": bool(u.get("verified")),
    }


async def core_request_code(bot, phone: str) -> tuple[int, dict]:
    digits = db.digits_of(phone)
    if len(digits) < 7:
        return 400, {"ok": False, "error": "bad_phone"}
    now = time.monotonic()
    if now - _last_request.get(digits, 0.0) < REQUEST_COOLDOWN:
        return 429, {"ok": False, "error": "too_many_requests"}
    _last_request[digits] = now
    if len(_last_request) > 50000:
        _last_request.clear()
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
    return bool(config.API_SECRET) and hmac.compare_digest(request.headers.get("X-API-Key", ""), config.API_SECRET)


async def _json(request: web.Request) -> dict:
    try:
        data = await request.json()
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


async def h_request(request: web.Request):
    if not _key_ok(request):
        return web.json_response({"ok": False, "error": "forbidden"}, status=403)
    data = await _json(request)
    status, body = await core_request_code(request.app["bot"], str(data.get("phone", "")))
    return web.json_response(body, status=status)


async def h_verify(request: web.Request):
    if not _key_ok(request):
        return web.json_response({"ok": False, "error": "forbidden"}, status=403)
    data = await _json(request)
    status, body = await core_verify(str(data.get("phone", "")), str(data.get("code", "")))
    return web.json_response(body, status=status)


async def h_me(request: web.Request):
    auth = request.headers.get("Authorization", "")
    token = auth[7:] if auth.lower().startswith("bearer ") else ""
    user = await db.get_session_user(token) if token else None
    if not user or user["banned"]:
        return web.json_response({"ok": False, "error": "unauthorized"}, status=401)
    return web.json_response({"ok": True, "user": public_user(user)})


async def h_health(request: web.Request):
    return web.json_response({"ok": True})


async def start_api(bot) -> web.AppRunner | None:
    if not config.API_SECRET:
        log.info("API_SECRET не задан — HTTP API выключено, коды выдаются прямо в боте.")
        return None
    app = web.Application(client_max_size=64 * 1024)
    app["bot"] = bot
    app.add_routes([
        web.post("/api/auth/request", h_request),
        web.post("/api/auth/verify", h_verify),
        web.get("/api/me", h_me),
        web.get("/health", h_health),
    ])
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", config.PORT).start()
    log.info("HTTP API слушает порт %s", config.PORT)
    return runner
