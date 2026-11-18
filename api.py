"""HTTP API shared by the RiseGram bot and native client.

The bot and client use the same SQLite database on the Railway /data volume.
Never put API_SECRET or BOT_TOKEN in the APK. Login codes are delivered privately
by the existing Telegram bot; the session token authenticates subsequent calls.
"""
from __future__ import annotations

from pathlib import Path
import hmac
import logging
import secrets
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
    # tg_id is the internal RiseGram key; Telegram is only the private delivery channel.
    chat_id = await db.telegram_chat_id(user["tg_id"])
    if not chat_id:
        log.warning("login number has no Telegram delivery chat for Rise ID %s", user.get("rise_id"))
        return 503, {"ok": False, "error": "code_delivery_unavailable"}
    code = await db.issue_auth_code(user["tg_id"])
    delivered = await safe_send(
        bot,
        chat_id,
        f"🔒 <b>Код входа в {config.BRAND}</b>\n\nНомер: <code>{esc(user['phone'])}</code>\nКод: <code>{code}</code>\n\n"
        f"Действует {config.AUTH_CODE_TTL_SECONDS // 60} мин. Никому его не сообщайте.",
    )
    if not delivered:
        await db.execute("UPDATE auth_codes SET used=1 WHERE tg_id=? AND used=0", (user["tg_id"],))
        await db.execute("UPDATE users SET auth_code=NULL, auth_code_expires=NULL WHERE tg_id=?", (user["tg_id"],))
        log.warning("login code delivery failed for Rise ID %s", user.get("rise_id"))
        return 503, {"ok": False, "error": "code_delivery_unavailable"}
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


async def h_user_search(request: web.Request):
    user = await _current_user(request)
    if not user:
        return web.json_response({"ok": False, "error": "unauthorized"}, status=401)
    q = (request.query.get("q") or "").strip().lstrip("@")
    if len(q) < 2:
        return web.json_response({"ok": True, "users": []})
    like = f"%{q.lower()}%"
    rows = await db.fetchall(
        """SELECT * FROM users
           WHERE tg_id != ? AND banned=0 AND
             (CAST(rise_id AS TEXT)=? OR lower(username) LIKE ? OR lower(active_username) LIKE ? OR lower(name) LIKE ?)
           ORDER BY CASE WHEN CAST(rise_id AS TEXT)=? THEN 0 ELSE 1 END, name LIMIT 25""",
        (user["tg_id"], q, like, like, like, q),
    )
    return web.json_response({"ok": True, "users": [public_user(x) for x in rows]})


async def h_chats(request: web.Request):
    user = await _current_user(request)
    if not user:
        return web.json_response({"ok": False, "error": "unauthorized"}, status=401)
    rows = await db.fetchall(
        """SELECT peer.tg_id, peer.user_id, peer.rise_id, peer.name, peer.username,
                  peer.phone, peer.verified, peer.risestars, peer.gram_balance, peer.active_username,
                  m.id AS message_id, m.body AS last_message, m.created_at AS last_at,
                  (SELECT COUNT(*) FROM messages u WHERE u.sender_id=peer.tg_id
                    AND u.recipient_id=? AND u.read_at IS NULL) AS unread
           FROM users peer
           JOIN (
             SELECT CASE WHEN sender_id=? THEN recipient_id ELSE sender_id END AS peer_id,
                    MAX(id) AS last_id
             FROM messages WHERE sender_id=? OR recipient_id=?
             GROUP BY peer_id
           ) recent ON recent.peer_id=peer.tg_id
           JOIN messages m ON m.id=recent.last_id
           WHERE peer.banned=0
           ORDER BY m.id DESC LIMIT 100""",
        (user["tg_id"], user["tg_id"], user["tg_id"], user["tg_id"]),
    )
    chats = []
    for row in rows:
        peer = {k: row.get(k) for k in ("user_id","rise_id","name","username","phone","verified","risestars","gram_balance","active_username")}
        chats.append({"user": public_user(peer), "last_message": row["last_message"],
                      "last_at": row["last_at"], "unread": int(row["unread"] or 0)})
    return web.json_response({"ok": True, "chats": chats})


async def h_messages(request: web.Request):
    user = await _current_user(request)
    if not user:
        return web.json_response({"ok": False, "error": "unauthorized"}, status=401)
    try:
        peer_id = int(request.match_info["rise_id"])
    except ValueError:
        return web.json_response({"ok": False, "error": "bad_rise_id"}, status=400)
    peer = await db.fetchone("SELECT * FROM users WHERE rise_id=? AND banned=0", (peer_id,))
    if not peer or peer["tg_id"] == user["tg_id"]:
        return web.json_response({"ok": False, "error": "user_not_found"}, status=404)
    blocked = await db.scalar(
        "SELECT COUNT(*) FROM chat_blocks WHERE (owner_id=? AND blocked_id=?) OR (owner_id=? AND blocked_id=?)",
        (user["tg_id"], peer["tg_id"], peer["tg_id"], user["tg_id"]), 0)
    if blocked:
        return web.json_response({"ok": False, "error": "chat_unavailable"}, status=403)
    await db.execute(
        "UPDATE messages SET read_at=? WHERE sender_id=? AND recipient_id=? AND read_at IS NULL",
        (db.now(), peer["tg_id"], user["tg_id"]))
    rows = await db.fetchall(
        """SELECT id,sender_id,recipient_id,body,created_at,read_at FROM messages
           WHERE (sender_id=? AND recipient_id=?) OR (sender_id=? AND recipient_id=?)
           ORDER BY id DESC LIMIT 100""",
        (user["tg_id"], peer["tg_id"], peer["tg_id"], user["tg_id"]))
    rows.reverse()
    return web.json_response({"ok": True, "peer": public_user(peer), "messages": [
        {"id": x["id"], "from_me": x["sender_id"] == user["tg_id"], "text": x["body"],
         "created_at": x["created_at"], "read": bool(x["read_at"])} for x in rows
    ]})


async def h_send_message(request: web.Request):
    user = await _current_user(request)
    if not user:
        return web.json_response({"ok": False, "error": "unauthorized"}, status=401)
    try:
        peer_id = int(request.match_info["rise_id"])
    except ValueError:
        return web.json_response({"ok": False, "error": "bad_rise_id"}, status=400)
    data = await _json(request)
    body = str(data.get("text") or "").strip()
    if not body or len(body) > 4000:
        return web.json_response({"ok": False, "error": "message_length"}, status=400)
    peer = await db.fetchone("SELECT * FROM users WHERE rise_id=? AND banned=0", (peer_id,))
    if not peer or peer["tg_id"] == user["tg_id"]:
        return web.json_response({"ok": False, "error": "user_not_found"}, status=404)
    blocked = await db.scalar(
        "SELECT COUNT(*) FROM chat_blocks WHERE (owner_id=? AND blocked_id=?) OR (owner_id=? AND blocked_id=?)",
        (user["tg_id"], peer["tg_id"], peer["tg_id"], user["tg_id"]), 0)
    if blocked:
        return web.json_response({"ok": False, "error": "chat_unavailable"}, status=403)
    created = db.now()
    async with db.connect() as conn:
        cur = await conn.execute(
            "INSERT INTO messages(sender_id,recipient_id,body,created_at) VALUES(?,?,?,?)",
            (user["tg_id"], peer["tg_id"], body, created))
        message_id = cur.lastrowid
        await conn.commit()
    return web.json_response({"ok": True, "message": {
        "id": message_id, "from_me": True, "text": body, "created_at": created, "read": False
    }})


async def h_spaces(request: web.Request):
    user = await _current_user(request)
    if not user:
        return web.json_response({"ok": False, "error": "unauthorized"}, status=401)
    rows = await db.fetchall(
        """SELECT s.*, m.role,
             (SELECT body FROM rg_space_messages x WHERE x.space_id=s.id ORDER BY x.id DESC LIMIT 1) AS last_message
           FROM rg_spaces s JOIN rg_space_members m ON m.space_id=s.id
           WHERE m.user_id=? ORDER BY s.id DESC LIMIT 100""",
        (user["tg_id"],))
    return web.json_response({"ok": True, "spaces": [
        {"id": x["id"], "title": x["title"], "description": x["description"],
         "type": x["kind"], "role": x["role"], "invite_code": x["invite_code"],
         "last_message": x["last_message"] or ""} for x in rows
    ]})


async def h_space_create(request: web.Request):
    user = await _current_user(request)
    if not user:
        return web.json_response({"ok": False, "error": "unauthorized"}, status=401)
    data = await _json(request)
    title = str(data.get("title") or "").strip()
    description = str(data.get("description") or "").strip()[:500]
    kind = str(data.get("type") or "group")
    if len(title) < 2 or len(title) > 64 or kind not in ("group", "channel"):
        return web.json_response({"ok": False, "error": "invalid_space"}, status=400)
    invite = secrets.token_urlsafe(8)
    async with db.connect() as conn:
        cur = await conn.execute(
            "INSERT INTO rg_spaces(title,description,kind,owner_id,invite_code,created_at) VALUES(?,?,?,?,?,?)",
            (title, description, kind, user["tg_id"], invite, db.now()))
        space_id = cur.lastrowid
        await conn.execute(
            "INSERT INTO rg_space_members(space_id,user_id,role,joined_at) VALUES(?,?,?,?)",
            (space_id, user["tg_id"], "owner", db.now()))
        await conn.commit()
    return web.json_response({"ok": True, "space": {"id": space_id, "title": title,
        "type": kind, "invite_code": invite, "role": "owner"}})


async def h_space_join(request: web.Request):
    user = await _current_user(request)
    if not user:
        return web.json_response({"ok": False, "error": "unauthorized"}, status=401)
    data = await _json(request)
    invite = str(data.get("invite_code") or "").strip()
    space = await db.fetchone("SELECT * FROM rg_spaces WHERE invite_code=?", (invite,))
    if not space:
        return web.json_response({"ok": False, "error": "invite_not_found"}, status=404)
    await db.execute(
        "INSERT OR IGNORE INTO rg_space_members(space_id,user_id,role,joined_at) VALUES(?,?,?,?)",
        (space["id"], user["tg_id"], "member", db.now()))
    return web.json_response({"ok": True, "space": {"id": space["id"], "title": space["title"], "type": space["kind"]}})


async def h_space_messages(request: web.Request):
    user = await _current_user(request)
    if not user:
        return web.json_response({"ok": False, "error": "unauthorized"}, status=401)
    try:
        space_id = int(request.match_info["space_id"])
    except ValueError:
        return web.json_response({"ok": False, "error": "bad_space_id"}, status=400)
    member = await db.fetchone("SELECT role FROM rg_space_members WHERE space_id=? AND user_id=?",
                               (space_id, user["tg_id"]))
    if not member:
        return web.json_response({"ok": False, "error": "not_a_member"}, status=403)
    space = await db.fetchone("SELECT * FROM rg_spaces WHERE id=?", (space_id,))
    rows = await db.fetchall(
        """SELECT m.id,m.body,m.created_at,u.rise_id,u.name,u.username
           FROM rg_space_messages m JOIN users u ON u.tg_id=m.sender_id
           WHERE m.space_id=? ORDER BY m.id DESC LIMIT 100""", (space_id,))
    rows.reverse()
    return web.json_response({"ok": True, "space": {"id": space_id, "title": space["title"],
        "type": space["kind"], "role": member["role"], "invite_code": space["invite_code"]},
        "messages": [{"id": x["id"], "text": x["body"], "created_at": x["created_at"],
          "sender": x["name"] or "Пользователь", "rise_id": x["rise_id"],
          "username": x["username"]} for x in rows]})


async def h_space_send(request: web.Request):
    user = await _current_user(request)
    if not user:
        return web.json_response({"ok": False, "error": "unauthorized"}, status=401)
    try:
        space_id = int(request.match_info["space_id"])
    except ValueError:
        return web.json_response({"ok": False, "error": "bad_space_id"}, status=400)
    data = await _json(request)
    body = str(data.get("text") or "").strip()
    if not body or len(body) > 4000:
        return web.json_response({"ok": False, "error": "message_length"}, status=400)
    space = await db.fetchone("SELECT * FROM rg_spaces WHERE id=?", (space_id,))
    member = await db.fetchone("SELECT role FROM rg_space_members WHERE space_id=? AND user_id=?",
                               (space_id, user["tg_id"]))
    if not space or not member:
        return web.json_response({"ok": False, "error": "not_a_member"}, status=403)
    if space["kind"] == "channel" and member["role"] not in ("owner", "admin"):
        return web.json_response({"ok": False, "error": "channel_read_only"}, status=403)
    await db.execute("INSERT INTO rg_space_messages(space_id,sender_id,body,created_at) VALUES(?,?,?,?)",
                     (space_id, user["tg_id"], body, db.now()))
    return web.json_response({"ok": True})


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


async def h_activate_nft(request: web.Request):
    user = await _current_user(request)
    if not user:
        return web.json_response({"ok": False, "error": "unauthorized"}, status=401)
    try:
        item_id = int(request.match_info["item_id"])
    except ValueError:
        return web.json_response({"ok": False, "error": "bad_item_id"}, status=400)
    item = await db.fetchone(
        "SELECT * FROM user_nft_inventory WHERE id=? AND tg_id=?",
        (item_id, user["tg_id"]),
    )
    if not item or item["item_type"] != "username":
        return web.json_response({"ok": False, "error": "username_nft_not_found"}, status=404)
    await db.set_active_username(user["tg_id"], item["value"])
    fresh = await db.get_user(user["tg_id"])
    return web.json_response({"ok": True, "user": public_user(fresh), "active_username": item["value"]})


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
    seller_chat_id = await db.telegram_chat_id(result["seller_id"])
    buyer_chat_id = await db.telegram_chat_id(user["tg_id"])
    if seller_chat_id:
        await safe_send(request.app["bot"], seller_chat_id,
            f"🛒 Ваш лот «{esc(result['value'])}» куплен за {result['price']:g} RiseStars.")
    if buyer_chat_id:
        await safe_send(request.app["bot"], buyer_chat_id,
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
        web.get("/api/users/search", h_user_search),
        web.get("/api/chats", h_chats),
        web.get("/api/chats/{rise_id}/messages", h_messages),
        web.get("/api/spaces", h_spaces),
        web.post("/api/spaces", h_space_create),
        web.post("/api/spaces/join", h_space_join),
        web.get("/api/spaces/{space_id}/messages", h_space_messages),
        web.post("/api/spaces/{space_id}/messages", h_space_send),
        web.post("/api/chats/{rise_id}/messages", h_send_message),
        web.get("/api/inventory", h_inventory),
        web.post("/api/inventory/{item_id}/activate", h_activate_nft),
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
