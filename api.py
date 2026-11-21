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

from pathlib import Path

import asyncio
import hashlib
import hmac
import json
import logging
import sqlite3
import time
from urllib.parse import parse_qsl

from aiohttp import web

import config
import database as db
import gift_drops as gifts
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
        "risestars": u.get("risestars", 0),
        "gram_balance": u.get("gram_balance", 0),
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



# Mini Apps must NOT ship API_SECRET. Authenticate Telegram's signed initData
# and separately confirm the RiseGram number using a code sent to its owner.
def _telegram_identity(raw: str) -> int | None:
    if not isinstance(raw, str) or not raw or len(raw) > 8192 or not config.BOT_TOKEN:
        return None
    try:
        pairs = parse_qsl(raw, keep_blank_values=True, strict_parsing=True, max_num_fields=64)
        fields = dict(pairs)
        if len(fields) != len(pairs) or "hash" not in fields:
            return None
        stamp = int(fields["auth_date"])
        if not 0 <= time.time() - stamp <= 3600:
            return None
        check = "\n".join(f"{k}={v}" for k,v in sorted(fields.items())
                          if k not in ("hash", "signature"))
        secret = hmac.new(b"WebAppData", config.BOT_TOKEN.encode(), hashlib.sha256).digest()
        expected = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, fields["hash"]):
            return None
        tg_id = int(json.loads(fields["user"])["id"])
        return tg_id if tg_id > 0 else None
    except (KeyError, ValueError, TypeError, OverflowError):
        return None

async def _session_user(request: web.Request):
    auth = request.headers.get("Authorization", "")
    token = auth[7:] if auth.lower().startswith("bearer ") else ""
    user = await db.get_session_user(token) if token else None
    return user if user and not user["banned"] else None

async def h_miniapp(request: web.Request):
    path=Path(__file__).with_name("miniapp.html")
    return web.FileResponse(path) if path.is_file() else web.Response(status=404)

async def h_webapp_request(request: web.Request):
    data=await _json(request)
    uid=_telegram_identity(data.get("init_data", ""))
    if uid is None:
        return web.json_response({"ok":False,"error":"invalid_telegram_signature"},status=401)
    phone=db.digits_of(str(data.get("phone","")))
    u=await db.fetchone("SELECT tg_id FROM users WHERE phone_digits=? AND banned=0",(phone,))
    if not u or u["tg_id"] != uid:
        return web.json_response({"ok":True})  # do not leak numbers
    status, body=await core_request_code(request.app["bot"],phone)
    return web.json_response(body,status=status)

async def h_webapp_verify(request: web.Request):
    data=await _json(request)
    uid=_telegram_identity(data.get("init_data", ""))
    if uid is None:
        return web.json_response({"ok":False,"error":"invalid_telegram_signature"},status=401)
    phone=db.digits_of(str(data.get("phone","")))
    u=await db.fetchone("SELECT tg_id FROM users WHERE phone_digits=? AND banned=0",(phone,))
    if not u or u["tg_id"] != uid:
        return web.json_response({"ok":False,"error":"invalid_code"},status=401)
    status,body=await core_verify(phone,str(data.get("code","")))
    return web.json_response(body,status=status)

async def h_gift_catalog(request: web.Request):
    try:page=min(10000,max(0,int(request.query.get("page","0"))))
    except ValueError:page=0
    rows,total=await asyncio.to_thread(gifts.catalog,page,50)
    return web.json_response({"ok":True,"items":rows,"total":total,"page":page})

async def h_gift_drops(request: web.Request):
    rows=await asyncio.to_thread(gifts.list_live_drops)
    return web.json_response({"ok":True,"items":rows})

async def h_gift_inventory(request: web.Request):
    user=await _session_user(request)
    if user is None:return web.json_response({"ok":False,"error":"unauthorized"},status=401)
    items=await asyncio.to_thread(gifts.inventory,user["tg_id"])
    return web.json_response({"ok":True,"items":items})

async def h_gift_purchase(request: web.Request):
    user=await _session_user(request)
    if user is None:return web.json_response({"ok":False,"error":"unauthorized"},status=401)
    data=await _json(request)
    try:
        drop_id=int(data.get("drop_id"))
        request_id=str(data.get("request_id",""))
        instance=await asyncio.to_thread(gifts.purchase,user["tg_id"],drop_id,request_id)
    except (ValueError,TypeError) as e:
        return web.json_response({"ok":False,"error":str(e)},status=409)
    except sqlite3.OperationalError:
        return web.json_response({"ok":False,"error":"database_busy"},status=503)
    fresh=await db.get_user(user["tg_id"])
    return web.json_response({"ok":True,"instance_id":instance,"risestars":fresh["risestars"]})

async def h_gift_upgrade(request: web.Request):
    user=await _session_user(request)
    if user is None:return web.json_response({"ok":False,"error":"unauthorized"},status=401)
    data=await _json(request)
    try:instance_id=int(data.get("instance_id"))
    except (ValueError,TypeError):return web.json_response({"ok":False,"error":"invalid_instance"},status=400)
    ok=await asyncio.to_thread(gifts.upgrade,user["tg_id"],instance_id)
    return web.json_response({"ok":ok,"error":None if ok else "upgrade_not_allowed"},status=200 if ok else 409)


async def h_index(request: web.Request):
    index_path = Path(__file__).with_name("index.html")
    if not index_path.is_file():
        return web.Response(text="RiseGram UI not found", status=404)
    return web.FileResponse(index_path)


async def h_health(request: web.Request):
    return web.json_response({"ok": True})


async def start_api(bot) -> web.AppRunner | None:
    if not config.API_SECRET:
        log.info("API_SECRET не задан — HTTP API выключено, коды выдаются прямо в боте.")
        return None
    app = web.Application(client_max_size=64 * 1024)
    app["bot"] = bot
    app.add_routes([
        web.get("/", h_index),
        web.get("/miniapp", h_miniapp),
        web.post("/api/webapp/auth/request", h_webapp_request),
        web.post("/api/webapp/auth/verify", h_webapp_verify),
        web.get("/api/gifts/catalog", h_gift_catalog),
        web.get("/api/gifts/drops", h_gift_drops),
        web.get("/api/gifts/inventory", h_gift_inventory),
        web.post("/api/gifts/buy", h_gift_purchase),
        web.post("/api/gifts/upgrade", h_gift_upgrade),
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
