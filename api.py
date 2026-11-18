"""HTTP API shared by the RiseGram bot and native client.

The bot and client use the same SQLite database on the Railway /data volume.
Never put API_SECRET or BOT_TOKEN in the APK. Login codes are delivered privately
by the existing Telegram bot; the session token authenticates subsequent calls.
"""
from __future__ import annotations

from pathlib import Path
import hmac
import logging
import time

from aiohttp import web

import config
import database as db
from ui import esc, safe_send

log = logging.getLogger("risegram.api")

_last_request: dict[str, float] = {}
_ip_requests: dict[str, list[float]] = {}
REQUEST_COOLDOWN = 30.0
IP_WINDOW_SECONDS = 3600.0
IP_MAX_REQUESTS = 10


def public_user(u: dict) -> dict:
    return {
        "user_id": u.get("user_id"),
        "rise_id": u["rise_id"],
        "name": u.get("name"),
        "username": db.shown_username(u).lstrip("@") if u.get("username") or u.get("active_username") else None,
        "phone": u.get("phone"),
        "verified": bool(u.get("verified")),
        "risestars": float(u.get("risestars") or 0),
        "gram_balance": float(u.get("gram_balance") or 0),
        "active_username": u.get("active_username"),
    }


def _bearer(request: web.Request) -> str:
    auth = request.headers.get("Authorization", "")
    return auth[7:].strip() if auth.lower().startswith("bearer ") else ""


async def _current_user(request: web.Request) -> dict | None:
    token = _bearer(request)
    if not token:
        return None
    user = await db.get_session_user(token)
    if not user or user.get("banned"):
        return None
    return user


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
    # Deliberately identical response for unknown, banned and disabled accounts.
    if not user or user["banned"] or not await db.is_feature_on("auth_numbers"):
        return 200, {"ok": True}
    code = await db.issue_auth_code(user["tg_id"])
    delivered = await safe_send(
        bot,
        user["tg_id"],
        f"🔒 <b>Код входа в {config.BRAND}</b>\n\nНомер: <code>{esc(user['phone'])}</code>\nКод: <code>{code}</code>\n\n"
        f"Действует {config.AUTH_CODE_TTL_SECONDS // 60} мин. Никому его не сообщайте.",
    )
    if not delivered:
        log.warning("login code delivery failed for Rise ID %s", user.get("rise_id"))
    return 200, {"ok": True}


async def core_verify(phone: str, code: str) -> tuple[int, dict]:
    user = await db.verify_auth_code(phone, code)
    if not user:
        return 401, {"ok": False, "error": "invalid_code"}
    token = await db.create_session(user["tg_id"])
    return 200, {"ok": True, "token": token, "user": public_user(user)}


async def _json(request: web.Request) -> dict:
    try:
        data = await request.json()
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


async def h_request(request: web.Request):
    # A per-IP sliding window complements the per-phone cooldown and prevents
    # the public login endpoint from being used to spam the bot's private inbox.
    ip = request.remote or "unknown"
    now_mono = time.monotonic()
    recent = [t for t in _ip_requests.get(ip, []) if now_mono - t < IP_WINDOW_SECONDS]
    if len(recent) >= IP_MAX_REQUESTS:
        return web.json_response({"ok": False, "error": "too_many_requests"}, status=429)
    recent.append(now_mono)
    _ip_requests[ip] = recent
    if len(_ip_requests) > 10000:
        _ip_requests.clear()
    data = await _json(request)
    status, body = await core_request_code(request.app["bot"], str(data.get("phone", "")))
    return web.json_response(body, status=status)


async def h_verify(request: web.Request):
    data = await _json(request)
    status, body = await core_verify(str(data.get("phone", "")), str(data.get("code", "")))
    return web.json_response(body, status=status)


async def h_me(request: web.Request):
    user = await _current_user(request)
    if not user:
        return web.json_response({"ok": False, "error": "unauthorized"}, status=401)
    return web.json_response({"ok": True, "user": public_user(user)})


async def h_inventory(request: web.Request):
    user = await _current_user(request)
    if not user:
        return web.json_response({"ok": False, "error": "unauthorized"}, status=401)
    items = await db.list_nft(user["tg_id"])
    return web.json_response({"ok": True, "items": [
        {"id": x["id"], "type": x["item_type"], "value": x["value"],
         "created_at": x.get("created_at"), "price_stars": x.get("price_stars", 0)}
        for x in items
    ]})


async def h_market(request: web.Request):
    user = await _current_user(request)
    if not user:
        return web.json_response({"ok": False, "error": "unauthorized"}, status=401)
    if not await db.is_feature_on("market"):
        return web.json_response({"ok": False, "error": "market_disabled"}, status=403)
    kind = request.query.get("type") or None
    if kind not in (None, "username", "number"):
        return web.json_response({"ok": False, "error": "bad_type"}, status=400)
    try:
        limit = min(50, max(1, int(request.query.get("limit", "20"))))
        offset = max(0, int(request.query.get("offset", "0")))
    except ValueError:
        return web.json_response({"ok": False, "error": "bad_pagination"}, status=400)
    rows = await db.market_page(kind, limit, offset)
    return web.json_response({"ok": True, "items": [
        {"id": x["id"], "type": x["item_type"], "value": x["value"],
         "price": float(x["price"]), "seller_username": x.get("seller_username")}
        for x in rows
    ], "balance": float(user.get("risestars") or 0)})


async def h_market_list(request: web.Request):
    user = await _current_user(request)
    if not user:
        return web.json_response({"ok": False, "error": "unauthorized"}, status=401)
    if not await db.is_feature_on("market"):
        return web.json_response({"ok": False, "error": "market_disabled"}, status=403)
    data = await _json(request)
    try:
        item_id, price = int(data.get("item_id")), float(data.get("price"))
    except (TypeError, ValueError):
        return web.json_response({"ok": False, "error": "bad_item_or_price"}, status=400)
    ok, message, listing_id = await db.create_listing(user["tg_id"], item_id, price)
    if not ok:
        return web.json_response({"ok": False, "error": "listing_rejected", "message": message}, status=400)
    return web.json_response({"ok": True, "listing_id": listing_id})


async def h_market_buy(request: web.Request):
    user = await _current_user(request)
    if not user:
        return web.json_response({"ok": False, "error": "unauthorized"}, status=401)
    if not await db.is_feature_on("market"):
        return web.json_response({"ok": False, "error": "market_disabled"}, status=403)
    try:
        listing_id = int(request.match_info["listing_id"])
    except ValueError:
        return web.json_response({"ok": False, "error": "bad_listing_id"}, status=400)
    ok, message, result = await db.buy_listing(user["tg_id"], listing_id)
    if not ok:
        return web.json_response({"ok": False, "error": "purchase_failed", "message": message}, status=409)
    # The DB transaction already transferred ownership and debited/credited balances.
    # Bot and client therefore see the same result immediately.
    await safe_send(request.app["bot"], result["seller_id"],
        f"🛒 Ваш лот «{esc(result['value'])}» куплен за {result['price']:g} RiseStars.")
    await safe_send(request.app["bot"], user["tg_id"],
        f"✅ Покупка завершена: «{esc(result['value'])}» теперь в вашем инвентаре.")
    fresh = await db.get_user(user["tg_id"])
    return web.json_response({"ok": True, "purchase": {
        "type": result["item_type"], "value": result["value"],
        "price": result["price"], "fee": result["fee"],
    }, "user": public_user(fresh)})


async def h_game_stats(request: web.Request):
    user = await _current_user(request)
    if not user:
        return web.json_response({"ok": False, "error": "unauthorized"}, status=401)
    stats = await db.game_stats(user["tg_id"])
    # Read balances fresh: bot games and client always share the same source of truth.
    fresh = await db.get_user(user["tg_id"])
    return web.json_response({"ok": True, "stats": stats, "user": public_user(fresh)})


async def h_purchases(request: web.Request):
    user = await _current_user(request)
    if not user:
        return web.json_response({"ok": False, "error": "unauthorized"}, status=401)
    rows = await db.list_purchases(user["tg_id"], 30)
    return web.json_response({"ok": True, "purchases": [
        {"id": x["id"], "type": x.get("item_type"), "description": x.get("item_desc"),
         "status": x.get("status"), "created_at": x.get("created_at")}
        for x in rows
    ]})


async def h_index(request: web.Request):
    index_path = Path(__file__).with_name("index.html")
    if not index_path.is_file():
        return web.Response(text="RiseGram UI not found", status=404)
    return web.FileResponse(index_path)


async def h_health(request: web.Request):
    return web.json_response({"ok": True, "service": "risegram-api"})


async def start_api(bot) -> web.AppRunner | None:
    # Public auth endpoints are protected by per-phone cooldown and one-time codes.
    # API_SECRET is intentionally not required by a public APK.
    app = web.Application(client_max_size=64 * 1024)
    app["bot"] = bot
    app.add_routes([
        web.get("/", h_index),
        web.post("/api/auth/request", h_request),
        web.post("/api/auth/verify", h_verify),
        web.get("/api/me", h_me),
        web.get("/api/inventory", h_inventory),
        web.get("/api/market", h_market),
        web.post("/api/market", h_market_list),
        web.post("/api/market/{listing_id}/buy", h_market_buy),
        web.get("/api/games/stats", h_game_stats),
        web.get("/api/purchases", h_purchases),
        web.get("/health", h_health),
    ])
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", config.PORT).start()
    log.info("HTTP API listening on port %s", config.PORT)
    return runner
