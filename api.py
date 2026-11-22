"""HTTP API shared by the RiseGram bot and native client.

The bot and client use the same SQLite database on the Railway /data volume.
Never put API_SECRET or BOT_TOKEN in the APK. Login codes are delivered privately
by the existing Telegram bot; the session token authenticates subsequent calls.
"""
from __future__ import annotations

from pathlib import Path
import asyncio
import hmac
import hashlib
import html
import json
import logging
import math
import secrets
import sqlite3
from urllib.parse import parse_qsl
import time
from collections import defaultdict

from decimal import Decimal, InvalidOperation
from aiohttp import ClientError, ClientSession, ClientTimeout, web
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter

import config
import database as db
import gift_drops as local_gifts
from gift_catalog import available_telegram_gifts
from media_api import create_media_handlers, public_media
from ui import esc, safe_send

log = logging.getLogger("risegram.api")

_last_request: dict[str, float] = {}
_ip_requests: dict[str, list[float]] = {}
REQUEST_COOLDOWN = 30.0
IP_WINDOW_SECONDS = 3600.0
IP_MAX_REQUESTS = 10

# Bounded per-account event queues for authenticated live client updates.
_event_clients: dict[int, set[asyncio.Queue]] = defaultdict(set)
_EVENT_QUEUE_SIZE = 64


async def _publish_event(account_id: int, event: dict) -> None:
    for queue in tuple(_event_clients.get(account_id, ())):
        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:
            while not queue.empty():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
            queue.put_nowait({"type": "sync_required"})


async def h_events(request: web.Request):
    user = await _current_user(request)
    if not user:
        return web.json_response({"ok": False, "error": "unauthorized"}, status=401)
    account_id = int(user["tg_id"])
    queue: asyncio.Queue = asyncio.Queue(maxsize=_EVENT_QUEUE_SIZE)
    _event_clients[account_id].add(queue)
    response = web.StreamResponse(status=200, headers={
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache, no-transform",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    })
    await response.prepare(request)
    try:
        await response.write(b"event: ready\ndata: {}\n\n")
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=20)
                payload = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
                frame = "event: " + str(event.get("type", "update")) + "\ndata: " + payload + "\n\n"
                await response.write(frame.encode("utf-8"))
            except asyncio.TimeoutError:
                await response.write(b": keep-alive\n\n")
    except (ConnectionResetError, asyncio.CancelledError, RuntimeError):
        pass
    finally:
        clients = _event_clients.get(account_id)
        if clients:
            clients.discard(queue)
            if not clients:
                _event_clients.pop(account_id, None)
    return response


def public_user(u: dict) -> dict:
    shown_username = db.shown_username(u).lstrip("@")
    public_username = shown_username if u.get("username") or u.get("active_username") else None
    verified_risegram = bool(u.get("verified_risegram"))
    verified_major = bool(u.get("verified_major"))
    return {
        "user_id": u.get("user_id"),
        "rise_id": u["rise_id"],
        "name": u.get("name"),
        "username": public_username,
        "phone": u.get("phone"),
        "verified": verified_risegram or verified_major,
        "verified_risegram": verified_risegram,
        "verified_major": verified_major,
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


h_media_upload, h_media_download = create_media_handlers(_current_user)

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
    # Refresh owned collectible usernames using the assigned +888 number only.
    # Missing MTProto accounts are handled on the bot's next update after sign-in.
    await sync_collectible_usernames(int(user["tg_id"]))
    chat_id = await db.telegram_chat_id(user["tg_id"])
    if not chat_id:
        log.warning("login number has no Telegram delivery chat for Rise ID %s", user.get("rise_id"))
        return 503, {"ok": False, "error": "code_delivery_unavailable"}
    code = await db.issue_auth_code(user["tg_id"])
    delivered = await safe_send(
        bot,
        user["tg_id"],
        f"🔒 <b>Код входа в {config.BRAND}</b>\n\nНомер: <code>{esc(user['phone'])}</code>\nКод: <code>{code}</code>\n\n"
        f"Действует {config.AUTH_CODE_TTL_SECONDS // 60} мин. Никому его не сообщайте.",
    )
    if not delivered:
        await db.execute("UPDATE auth_codes SET used=1 WHERE tg_id=? AND used=0", (user["tg_id"],))
        await db.execute("UPDATE users SET auth_code=NULL, auth_code_expires=NULL WHERE tg_id=?", (user["tg_id"],))
        log.warning("login code delivery failed for Rise ID %s", user.get("rise_id"))
        return 503, {"ok": False, "error": "code_delivery_unavailable"}
    return 200, {"ok": True}


async def core_verify(phone: str, code: str, *, native=False) -> tuple[int, dict]:
    user = await db.verify_auth_code(phone, code)
    if not user:
        return 401, {"ok": False, "error": "invalid_code"}
    await db.join_default_risegram_spaces(int(user["tg_id"]))
    token = await db.create_session(user["tg_id"], native=native)
    return 200, {"ok": True, "token": token, "user": await self_profile(user)}


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
    native = "Origin" not in request.headers and "Sec-Fetch-Site" not in request.headers
    status, body = await core_verify(str(data.get("phone", "")), str(data.get("code", "")), native=native)
    return web.json_response(body, status=status)


async def h_me(request: web.Request):
    user = await _current_user(request)
    if not user:
        return web.json_response({"ok": False, "error": "unauthorized"}, status=401)
    return web.json_response({"ok": True, "user": await self_profile(user)})


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
    message_ids = [int(x["id"]) for x in rows]
    media_by_message = defaultdict(list)
    if message_ids:
        marks = ",".join("?" for _ in message_ids)
        attachments = await db.fetchall(
            f"""SELECT mm.message_id, med.* FROM rg_message_media mm
                JOIN rg_media med ON med.id=mm.media_id
                WHERE mm.message_id IN ({marks}) ORDER BY mm.position""",
            message_ids,
        )
        for item in attachments:
            media_by_message[int(item["message_id"])].append(public_media(item))
    return web.json_response({"ok": True, "peer": public_user(peer), "messages": [
        {"id": x["id"], "from_me": x["sender_id"] == user["tg_id"], "text": x["body"],
         "created_at": x["created_at"], "read": bool(x["read_at"]),
         "media": media_by_message[int(x["id"])]} for x in rows
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
    media_ids = data.get("media_ids", [])
    if not isinstance(media_ids, list) or len(media_ids) > 10:
        return web.json_response({"ok": False, "error": "invalid_media_ids"}, status=400)
    if any(not isinstance(mid, str) or not mid or len(mid) > 64 for mid in media_ids):
        return web.json_response({"ok": False, "error": "invalid_media_ids"}, status=400)
    media_ids = list(dict.fromkeys(media_ids))
    media_rows = []
    if media_ids:
        marks = ",".join("?" for _ in media_ids)
        media_rows = await db.fetchall(
            f"SELECT * FROM rg_media WHERE uploader_id=? AND id IN ({marks})",
            (user["tg_id"], *media_ids),
        )
        if len(media_rows) != len(media_ids):
            return web.json_response({"ok": False, "error": "media_not_owned"}, status=403)
        media_by_id = {item["id"]: item for item in media_rows}
        media_rows = [media_by_id[mid] for mid in media_ids]
    if (not body and not media_rows) or len(body) > 4000:
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
        message_id = int(cur.lastrowid)
        for position, media in enumerate(media_rows):
            await conn.execute(
                "INSERT INTO rg_message_media(message_id,media_id,position) VALUES(?,?,?)",
                (message_id, media["id"], position),
            )
        await conn.commit()
    await _publish_event(int(peer["tg_id"]), {
        "type": "new_message", "message_id": int(message_id),
        "from_rise_id": int(user["rise_id"]),
    })
    return web.json_response({"ok": True, "message": {
        "id": message_id, "from_me": True, "text": body, "created_at": created, "read": False,
        "media": [public_media(item) for item in media_rows],
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
    message_ids = [int(x["id"]) for x in rows]
    media_by_message = defaultdict(list)
    if message_ids:
        marks = ",".join("?" for _ in message_ids)
        attachments = await db.fetchall(
            f"""SELECT mm.message_id, med.* FROM rg_space_message_media mm
                JOIN rg_media med ON med.id=mm.media_id
                WHERE mm.message_id IN ({marks}) ORDER BY mm.position""",
            message_ids,
        )
        for item in attachments:
            media_by_message[int(item["message_id"])].append(public_media(item))
    return web.json_response({"ok": True, "space": {"id": space_id, "title": space["title"],
        "type": space["kind"], "role": member["role"], "invite_code": space["invite_code"]},
        "messages": [{"id": x["id"], "text": x["body"], "created_at": x["created_at"],
          "sender": x["name"] or "Пользователь", "rise_id": x["rise_id"],
          "username": x["username"], "media": media_by_message[int(x["id"])]} for x in rows]})


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
    media_ids = data.get("media_ids", [])
    if not isinstance(media_ids, list) or len(media_ids) > 10:
        return web.json_response({"ok": False, "error": "invalid_media_ids"}, status=400)
    if any(not isinstance(mid, str) or not mid or len(mid) > 64 for mid in media_ids):
        return web.json_response({"ok": False, "error": "invalid_media_ids"}, status=400)
    media_ids = list(dict.fromkeys(media_ids))
    media_rows = []
    if media_ids:
        marks = ",".join("?" for _ in media_ids)
        media_rows = await db.fetchall(
            f"SELECT * FROM rg_media WHERE uploader_id=? AND id IN ({marks})",
            (user["tg_id"], *media_ids),
        )
        if len(media_rows) != len(media_ids):
            return web.json_response({"ok": False, "error": "media_not_owned"}, status=403)
        media_by_id = {item["id"]: item for item in media_rows}
        media_rows = [media_by_id[mid] for mid in media_ids]
    if (not body and not media_rows) or len(body) > 4000:
        return web.json_response({"ok": False, "error": "message_length"}, status=400)
    space = await db.fetchone("SELECT * FROM rg_spaces WHERE id=?", (space_id,))
    member = await db.fetchone("SELECT role FROM rg_space_members WHERE space_id=? AND user_id=?",
                               (space_id, user["tg_id"]))
    if not space or not member:
        return web.json_response({"ok": False, "error": "not_a_member"}, status=403)
    if space["kind"] == "channel" and member["role"] not in ("owner", "admin"):
        return web.json_response({"ok": False, "error": "channel_read_only"}, status=403)
    async with db.connect() as conn:
        cur = await conn.execute(
            "INSERT INTO rg_space_messages(space_id,sender_id,body,created_at) VALUES(?,?,?,?)",
            (space_id, user["tg_id"], body, db.now()))
        message_id = int(cur.lastrowid)
        for position, media in enumerate(media_rows):
            await conn.execute(
                "INSERT INTO rg_space_message_media(message_id,media_id,position) VALUES(?,?,?)",
                (message_id, media["id"], position),
            )
        await conn.commit()
    members = await db.fetchall("SELECT user_id FROM rg_space_members WHERE space_id=?", (space_id,))
    for member_row in members:
        member_id = int(member_row["user_id"])
        if member_id != int(user["tg_id"]):
            await _publish_event(member_id, {
                "type": "new_space_message", "space_id": space_id,
                "message_id": message_id,
            })
    return web.json_response({"ok": True, "message_id": message_id,
                              "media": [public_media(item) for item in media_rows]})


async def h_space_members(request: web.Request):
    user = await _current_user(request)
    if not user:
        return web.json_response({"ok": False, "error": "unauthorized"}, status=401)
    try:
        space_id = int(request.match_info["space_id"])
    except ValueError:
        return web.json_response({"ok": False, "error": "bad_space_id"}, status=400)
    membership = await db.fetchone(
        "SELECT role FROM rg_space_members WHERE space_id=? AND user_id=?",
        (space_id, user["tg_id"]),
    )
    if not membership:
        return web.json_response({"ok": False, "error": "not_a_member"}, status=403)
    rows = await db.fetchall(
        """SELECT u.rise_id,u.name,u.username,u.active_username,u.verified,m.role,m.joined_at
           FROM rg_space_members m JOIN users u ON u.tg_id=m.user_id
           WHERE m.space_id=? ORDER BY CASE m.role WHEN 'owner' THEN 0 WHEN 'admin' THEN 1 ELSE 2 END,m.joined_at""",
        (space_id,),
    )
    return web.json_response({"ok": True, "members": [
        {"rise_id": x["rise_id"], "name": x["name"],
         "username": x["active_username"] or x["username"],
         "verified": bool(x["verified"]), "role": x["role"], "joined_at": x["joined_at"]}
        for x in rows
    ]})


async def h_space_change_role(request: web.Request):
    user = await _current_user(request)
    if not user:
        return web.json_response({"ok": False, "error": "unauthorized"}, status=401)
    try:
        space_id = int(request.match_info["space_id"])
        target_rise_id = int(request.match_info["rise_id"])
    except ValueError:
        return web.json_response({"ok": False, "error": "bad_identifier"}, status=400)
    data = await _json(request)
    role = str(data.get("role") or "")
    if role not in ("admin", "member"):
        return web.json_response({"ok": False, "error": "invalid_role"}, status=400)
    space = await db.fetchone("SELECT owner_id FROM rg_spaces WHERE id=?", (space_id,))
    actor = await db.fetchone(
        "SELECT role FROM rg_space_members WHERE space_id=? AND user_id=?",
        (space_id, user["tg_id"]),
    )
    target = await db.fetchone(
        """SELECT m.user_id,m.role FROM rg_space_members m JOIN users u ON u.tg_id=m.user_id
           WHERE m.space_id=? AND u.rise_id=?""",
        (space_id, target_rise_id),
    )
    if not space or not actor or int(space["owner_id"]) != int(user["tg_id"]):
        return web.json_response({"ok": False, "error": "owner_required"}, status=403)
    if not target or target["role"] == "owner" or int(target["user_id"]) == int(user["tg_id"]):
        return web.json_response({"ok": False, "error": "member_not_found_or_protected"}, status=404)
    await db.execute(
        "UPDATE rg_space_members SET role=? WHERE space_id=? AND user_id=?",
        (role, space_id, target["user_id"]),
    )
    await _publish_event(int(target["user_id"]), {"type": "space_membership_updated", "space_id": space_id})
    return web.json_response({"ok": True, "rise_id": target_rise_id, "role": role})


async def h_space_remove_member(request: web.Request):
    user = await _current_user(request)
    if not user:
        return web.json_response({"ok": False, "error": "unauthorized"}, status=401)
    try:
        space_id = int(request.match_info["space_id"])
        target_rise_id = int(request.match_info["rise_id"])
    except ValueError:
        return web.json_response({"ok": False, "error": "bad_identifier"}, status=400)
    space = await db.fetchone("SELECT owner_id FROM rg_spaces WHERE id=?", (space_id,))
    actor = await db.fetchone(
        "SELECT role FROM rg_space_members WHERE space_id=? AND user_id=?",
        (space_id, user["tg_id"]),
    )
    target = await db.fetchone(
        """SELECT m.user_id,m.role FROM rg_space_members m JOIN users u ON u.tg_id=m.user_id
           WHERE m.space_id=? AND u.rise_id=?""",
        (space_id, target_rise_id),
    )
    if not space or not actor or actor["role"] not in ("owner", "admin"):
        return web.json_response({"ok": False, "error": "moderator_required"}, status=403)
    if not target or target["role"] == "owner":
        return web.json_response({"ok": False, "error": "member_not_found_or_protected"}, status=404)
    if actor["role"] == "admin" and target["role"] == "admin":
        return web.json_response({"ok": False, "error": "owner_required"}, status=403)
    await db.execute(
        "DELETE FROM rg_space_members WHERE space_id=? AND user_id=?",
        (space_id, target["user_id"]),
    )
    await _publish_event(int(target["user_id"]), {"type": "space_removed", "space_id": space_id})
    return web.json_response({"ok": True, "removed_rise_id": target_rise_id})


async def h_space_transfer_owner(request: web.Request):
    user = await _current_user(request)
    if not user:
        return web.json_response({"ok": False, "error": "unauthorized"}, status=401)
    try:
        space_id = int(request.match_info["space_id"])
        target_rise_id = int(request.match_info["rise_id"])
    except ValueError:
        return web.json_response({"ok": False, "error": "bad_identifier"}, status=400)
    async with db.connect() as conn:
        cur = await conn.execute("SELECT owner_id FROM rg_spaces WHERE id=?", (space_id,))
        space = await cur.fetchone()
        if not space or int(space["owner_id"]) != int(user["tg_id"]):
            await conn.rollback()
            return web.json_response({"ok": False, "error": "owner_required"}, status=403)
        cur = await conn.execute(
            """SELECT m.user_id FROM rg_space_members m JOIN users u ON u.tg_id=m.user_id
               WHERE m.space_id=? AND u.rise_id=? AND m.user_id!=?""",
            (space_id, target_rise_id, user["tg_id"]),
        )
        target = await cur.fetchone()
        if not target:
            await conn.rollback()
            return web.json_response({"ok": False, "error": "member_not_found"}, status=404)
        await conn.execute(
            "UPDATE rg_space_members SET role='admin' WHERE space_id=? AND user_id=?",
            (space_id, user["tg_id"]),
        )
        await conn.execute(
            "UPDATE rg_space_members SET role='owner' WHERE space_id=? AND user_id=?",
            (space_id, target["user_id"]),
        )
        await conn.execute(
            "UPDATE rg_spaces SET owner_id=? WHERE id=?",
            (target["user_id"], space_id),
        )
        await conn.commit()
    for member in await db.fetchall("SELECT user_id FROM rg_space_members WHERE space_id=?", (space_id,)):
        await _publish_event(int(member["user_id"]), {"type": "space_membership_updated", "space_id": space_id})
    return web.json_response({"ok": True, "owner_rise_id": target_rise_id})


async def h_space_rotate_invite(request: web.Request):
    user = await _current_user(request)
    if not user:
        return web.json_response({"ok": False, "error": "unauthorized"}, status=401)
    try:
        space_id = int(request.match_info["space_id"])
    except ValueError:
        return web.json_response({"ok": False, "error": "bad_space_id"}, status=400)
    owner = await db.fetchone("SELECT owner_id FROM rg_spaces WHERE id=?", (space_id,))
    if not owner or int(owner["owner_id"]) != int(user["tg_id"]):
        return web.json_response({"ok": False, "error": "owner_required"}, status=403)
    invite = secrets.token_urlsafe(12)
    await db.execute("UPDATE rg_spaces SET invite_code=? WHERE id=?", (invite, space_id))
    return web.json_response({"ok": True, "invite_code": invite})


async def h_space_leave(request: web.Request):
    user = await _current_user(request)
    if not user:
        return web.json_response({"ok": False, "error": "unauthorized"}, status=401)
    try:
        space_id = int(request.match_info["space_id"])
    except ValueError:
        return web.json_response({"ok": False, "error": "bad_space_id"}, status=400)
    space = await db.fetchone("SELECT owner_id FROM rg_spaces WHERE id=?", (space_id,))
    membership = await db.fetchone(
        "SELECT role FROM rg_space_members WHERE space_id=? AND user_id=?",
        (space_id, user["tg_id"]),
    )
    if not space or not membership:
        return web.json_response({"ok": False, "error": "not_a_member"}, status=404)
    if int(space["owner_id"]) == int(user["tg_id"]):
        return web.json_response({"ok": False, "error": "transfer_ownership_first"}, status=409)
    await db.execute(
        "DELETE FROM rg_space_members WHERE space_id=? AND user_id=?",
        (space_id, user["tg_id"]),
    )
    return web.json_response({"ok": True, "left": True})


async def h_chat_blocks(request: web.Request):
    user = await _current_user(request)
    if not user:
        return web.json_response({"ok": False, "error": "unauthorized"}, status=401)
    rows = await db.fetchall(
        """SELECT u.rise_id,u.name,u.username,b.created_at
           FROM chat_blocks b JOIN users u ON u.tg_id=b.blocked_id
           WHERE b.owner_id=? ORDER BY b.created_at DESC LIMIT 200""",
        (user["tg_id"],))
    return web.json_response({"ok": True, "blocked": [
        {"rise_id": x["rise_id"], "name": x["name"], "username": x["username"],
         "created_at": x["created_at"]} for x in rows
    ]})


async def h_block_user(request: web.Request):
    user = await _current_user(request)
    if not user:
        return web.json_response({"ok": False, "error": "unauthorized"}, status=401)
    try:
        peer_rise_id = int(request.match_info["rise_id"])
    except ValueError:
        return web.json_response({"ok": False, "error": "bad_rise_id"}, status=400)
    peer = await db.fetchone("SELECT tg_id FROM users WHERE rise_id=? AND banned=0", (peer_rise_id,))
    if not peer or int(peer["tg_id"]) == int(user["tg_id"]):
        return web.json_response({"ok": False, "error": "user_not_found"}, status=404)
    await db.execute(
        "INSERT OR IGNORE INTO chat_blocks(owner_id,blocked_id,created_at) VALUES(?,?,?)",
        (user["tg_id"], peer["tg_id"], db.now()))
    return web.json_response({"ok": True, "blocked": True, "rise_id": peer_rise_id})


async def h_unblock_user(request: web.Request):
    user = await _current_user(request)
    if not user:
        return web.json_response({"ok": False, "error": "unauthorized"}, status=401)
    try:
        peer_rise_id = int(request.match_info["rise_id"])
    except ValueError:
        return web.json_response({"ok": False, "error": "bad_rise_id"}, status=400)
    peer = await db.fetchone("SELECT tg_id FROM users WHERE rise_id=?", (peer_rise_id,))
    if not peer:
        return web.json_response({"ok": False, "error": "user_not_found"}, status=404)
    await db.execute(
        "DELETE FROM chat_blocks WHERE owner_id=? AND blocked_id=?",
        (user["tg_id"], peer["tg_id"]))
    return web.json_response({"ok": True, "blocked": False, "rise_id": peer_rise_id})


async def h_inventory(request: web.Request):
    user = await _current_user(request)
    if not user:
        return web.json_response({"ok": False, "error": "unauthorized"}, status=401)
    items = await db.list_nft(user["tg_id"])
    gifts = await db.list_user_gifts(user["tg_id"])
    return web.json_response({"ok": True, "items": [
        {"id": x["id"], "type": x["item_type"], "value": x["value"],
         "created_at": x.get("created_at"), "price_stars": x.get("price_stars", 0)}
        for x in items
    ], "gifts": gifts, "local_gifts": await asyncio.to_thread(local_gifts.inventory,user["tg_id"])})


async def _telegram_bot_star_balance(bot) -> int:
    balance = await bot.get_my_star_balance()
    return max(0, int(getattr(balance, "amount", 0)))


async def h_exchange(request: web.Request):
    user = await _current_user(request)
    if not user:
        return web.json_response({"ok": False, "error": "unauthorized"}, status=401)
    data = await _json(request)
    try:
        amount = Decimal(str(data["amount"]))
        direction = data["direction"]
        if not amount.is_finite() or amount <= 0 or amount > 900000000000 or direction not in ("gram_to_stars", "stars_to_gram"):
            raise ValueError
        delta = amount * 100 if direction == "gram_to_stars" else -amount
        if delta != delta.to_integral_value() or abs(delta) > 900000000000:
            raise ValueError
    except (InvalidOperation, ValueError, KeyError):
        return web.json_response({"ok": False, "error": "Сумма обмена должна соответствовать целому числу звёзд."}, status=400)
    key = request.headers.get("Idempotency-Key") or secrets.token_hex(16)
    if not (16 <= len(key) <= 64 and key.isascii() and all(c.isalnum() or c in "-_" for c in key)):
        raise web.HTTPBadRequest()
    try:
        async with ClientSession(timeout=ClientTimeout(total=10), headers={
            "Authorization": "Bearer " + config.MTPROTO_ADMIN_API_TOKEN
        }) as session:
            async with session.post(config.MTPROTO_ADMIN_API_URL.rstrip("/") + "/v1/accounts/exchange-gram",
                                    json={"phone": user["phone"], "stars_delta": int(delta), "request_id": key}) as response:
                if response.status == 409:
                    return web.json_response({"ok": False, "error": "Недостаточно средств."}, status=409)
                response.raise_for_status()
                values = wallet_amounts(await response.json())
    except (ClientError, TimeoutError, ValueError, KeyError):
        return web.json_response({"ok": False, "error": "Проверьте баланс в приложении перед повторным обменом."}, status=503)
    balances = dict(values, gram_balance=float(values["gram_balance"]))
    return web.json_response({"ok": True, "user": dict(public_user(user), **balances), "balances": balances})


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
    return web.json_response({"ok": True, "user": await self_profile(fresh), "active_username": item["value"]})


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
    ], "balance": (await self_profile(user))["risestars"]})


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
    }, "user": await self_profile(fresh)})


async def h_game_stats(request: web.Request):
    user = await _current_user(request)
    if not user:
        return web.json_response({"ok": False, "error": "unauthorized"}, status=401)
    stats = await db.game_stats(user["tg_id"])
    # Read balances fresh: bot games and client always share the same source of truth.
    fresh = await db.get_user(user["tg_id"])
    return web.json_response({"ok": True, "stats": stats, "user": await self_profile(fresh)})


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


_verification_sync_last: dict[int, float] = {}
_verification_sync_state: dict[int, tuple[str, bool]] = {}
_verification_sync_locks: dict[int, asyncio.Lock] = {}


async def native_wallet(user):
    """Read the same account ledgers as MTProto payments.getStarsStatus."""
    if not config.MTPROTO_ADMIN_API_TOKEN or not config.MTPROTO_ADMIN_API_URL or not user.get("phone"):
        return None
    async with ClientSession(timeout=ClientTimeout(total=5), headers={
        "Authorization": "Bearer " + config.MTPROTO_ADMIN_API_TOKEN
    }) as session:
        async with session.post(config.MTPROTO_ADMIN_API_URL.rstrip("/") + "/v1/accounts/client-balances",
                                json={"phone": user["phone"]}) as response:
            response.raise_for_status()
            data = await response.json()
    return wallet_amounts(data) if data["found"] else None


def wallet_amounts(data):
    return {"risestars": int(data["stars"]), "gram_balance": Decimal(int(data["gram_nano"])).scaleb(-9)}


async def client_wallet_line(user: dict) -> str:
    try:
        wallet = await native_wallet(user)
    except (ClientError, TimeoutError, KeyError, ValueError, TypeError):
        return "Баланс приложения временно недоступен"
    if wallet is None:
        return "К номеру ещё не привязан кошелёк приложения"
    gram = format(wallet["gram_balance"], "f").rstrip("0").rstrip(".") or "0"
    return f"⭐ {wallet['risestars']:,} RiseStars · 💎 {gram} GRAM".replace(",", " ")


async def self_profile(user):
    result = public_user(user)
    try:
        wallet = await native_wallet(user)
    except (ClientError, TimeoutError, KeyError, ValueError, TypeError):
        wallet = None
    result.update(risestars=wallet["risestars"] if wallet else None,
                  gram_balance=float(wallet["gram_balance"]) if wallet else None)
    result["profile_music"] = _music_public(await _profile_music_row(int(user["rise_id"])))
    return result


async def sync_mtproto_verification(user_id: int, force: bool = False) -> dict:
    """Mirror the RiseGram bot's verified flag onto its native MTProto peer."""
    base = str(getattr(config, "MTPROTO_ADMIN_API_URL", "") or "").rstrip("/")
    token = str(getattr(config, "MTPROTO_ADMIN_API_TOKEN", "") or "")
    if not base or not token or int(user_id) <= 0:
        return {"configured": False, "synced": False}
    user = await db.get_user(int(user_id))
    if not user or user.get("banned"):
        return {"configured": True, "synced": False}
    phone = str(user.get("phone") or "")
    digits = db.digits_of(phone)
    if len(digits) < 7:
        return {"configured": True, "synced": False}
    verified = bool(user.get("verified"))
    issuer = "major" if user.get("verified_major") else "risegram" if user.get("verified_risegram") else ""
    bot_id = await db.get_meta("native_issuer:" + issuer) if issuer else None
    fingerprint = (digits, verified, issuer, bot_id, bool(user.get("verified_risegram")))
    now_mono = time.monotonic()
    if not force and _verification_sync_state.get(int(user_id)) == fingerprint and now_mono - _verification_sync_last.get(int(user_id), 0.0) < 300:
        return {"configured": True, "synced": True, "cached": True}

    lock = _verification_sync_locks.setdefault(int(user_id), asyncio.Lock())
    if lock.locked():
        return {"configured": True, "synced": False, "pending": True}
    async with lock:
        now_mono = time.monotonic()
        if not force and _verification_sync_state.get(int(user_id)) == fingerprint and now_mono - _verification_sync_last.get(int(user_id), 0.0) < 300:
            return {"configured": True, "synced": True, "cached": True}
        timeout = ClientTimeout(total=8, connect=3)
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        try:
            async with ClientSession(timeout=timeout, headers=headers) as session:
                async with session.post(f"{base}/v1/accounts/resolve-by-phone", json={"phone": phone}) as response:
                    if response.status != 200:
                        log.warning("MTProto verification account lookup returned HTTP %s", response.status)
                        _verification_sync_last[int(user_id)] = time.monotonic() - 270
                        return {"configured": True, "synced": False, "http_status": response.status}
                    resolved = await response.json(content_type=None)
                if not resolved.get("found") or not resolved.get("user_id"):
                    # The MTProto peer can appear after this bot update. Retry shortly.
                    _verification_sync_last[int(user_id)] = time.monotonic() - 270
                    return {"configured": True, "synced": False, "pending_account": True}
                await db.execute(
                    "INSERT OR REPLACE INTO verified_peer_links(peer_id,account_id,phone_digits) VALUES(?,?,?)",
                    (int(resolved["user_id"]), user_id, digits),
                )
                payload = {
                    "command_id": f"risegram-verified-{int(user_id)}-{time.time_ns()}",
                    "actor": "risegram-bot",
                    "reason": "Synchronize RiseGram account verification with native MTProto",
                    "dry_run": False,
                    "user_id": int(resolved["user_id"]),
                    "verified": verified,
                }
                async with session.post(f"{base}/v1/accounts/set-verified", json=payload) as response:
                    result = await response.json(content_type=None)
                    if response.status != 200 or result.get("error"):
                        log.warning("MTProto verification update returned HTTP %s", response.status)
                        _verification_sync_last[int(user_id)] = time.monotonic() - 270
                        return {"configured": True, "synced": False, "http_status": response.status}
                native_peer = int(resolved["user_id"])
                mark_key = f"native_profile_mark:{native_peer}"
                old = await db.get_meta(mark_key)
                if bot_id or old:
                    from issuer_marks import command
                    operation = "grant" if bot_id else "revoke"
                    mark = {"verifier_bot_id": int(bot_id or old), "peer_type": "user", "peer_id": native_peer}
                    if bot_id and user.get("verified_major") and user.get("verified_risegram"):
                        mark["description"] = "Аккаунт верифицирован организациями «RiseGram» и «Major»."
                    await command(session, f"/v1/botverification/marks/{operation}",
                                  f"rg-user-mark-{native_peer}-{time.time_ns()}", mark)
                    await db.set_meta(mark_key, bot_id or "")
            _verification_sync_state[int(user_id)] = fingerprint
            _verification_sync_last[int(user_id)] = time.monotonic()
            return {"configured": True, "synced": True}
        except Exception:
            log.exception("MTProto verification sync failed for RiseGram user %s", user_id)
            _verification_sync_last[int(user_id)] = time.monotonic() - 270
            return {"configured": True, "synced": False, "error": True}


async def h_public_link(request: web.Request):
    username = str(request.match_info.get("username", "")).strip().lstrip("@")
    if (len(username) < 5 or len(username) > 32 or not username[0].isalpha()
            or not username.isascii() or not all(ch.isalnum() or ch == "_" for ch in username)):
        return web.Response(text="Not found", status=404)
    safe_name = html.escape(username)
    deep_link = html.escape(f"tg://resolve?domain={username}", quote=True)
    page = f"""<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>@{safe_name} — RiseGram</title><meta name="theme-color" content="#17232f"><style>body{{margin:0;background:#17232f;color:#fff;font:16px Arial,sans-serif;display:grid;min-height:100vh;place-items:center}}main{{max-width:420px;padding:32px;text-align:center}}a{{display:inline-block;margin-top:18px;padding:13px 22px;border-radius:14px;background:#229ed9;color:white;text-decoration:none;font-weight:700}}</style><main><h1>RiseGram</h1><p>Открыть <b>@{safe_name}</b> в приложении RiseGram</p><a href="{deep_link}">Открыть в RiseGram</a></main></html>"""
    return web.Response(text=page, content_type="text/html", charset="utf-8")



# A signed Telegram Mini App identity is separate from the RiseGram account
# number and the bot-delivered one-time code. Never trust initDataUnsafe alone.
def _miniapp_telegram_id(raw: str) -> int | None:
    if not isinstance(raw,str) or not raw or len(raw)>8192 or not config.BOT_TOKEN:
        return None
    try:
        pairs=parse_qsl(raw,keep_blank_values=True,strict_parsing=True,max_num_fields=64)
        fields=dict(pairs)
        if len(fields)!=len(pairs) or "hash" not in fields:
            return None
        stamp=int(fields["auth_date"])
        if not 0<=time.time()-stamp<=3600:
            return None
        check="\n".join(f"{key}={value}" for key,value in sorted(fields.items())
                         if key != "hash")
        secret=hmac.new(b"WebAppData",config.BOT_TOKEN.encode(),hashlib.sha256).digest()
        expected=hmac.new(secret,check.encode(),hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected,fields["hash"]):
            return None
        user_id=int(json.loads(fields["user"])["id"])
        return user_id if user_id>0 else None
    except (KeyError,ValueError,TypeError,OverflowError):
        return None

async def h_local_miniapp(request: web.Request):
    file=Path(__file__).with_name("miniapp.html")
    return web.FileResponse(file) if file.is_file() else web.Response(status=404)

async def h_local_webapp_auth_request(request: web.Request):
    data=await _json(request)
    tg_id=_miniapp_telegram_id(data.get("init_data",""))
    if tg_id is None:
        return web.json_response({"ok":False,"error":"invalid_telegram_signature"},status=401)
    phone=db.digits_of(str(data.get("phone","")))
    account=await db.get_by_telegram(tg_id)
    by_phone=await db.fetchone("SELECT tg_id FROM users WHERE phone_digits=? AND banned=0",(phone,))
    if not account or not by_phone or account["tg_id"]!=by_phone["tg_id"]:
        return web.json_response({"ok":True})
    status,body=await core_request_code(request.app["bot"],phone)
    return web.json_response(body,status=status)

async def h_local_webapp_auth_verify(request: web.Request):
    data=await _json(request)
    tg_id=_miniapp_telegram_id(data.get("init_data",""))
    if tg_id is None:
        return web.json_response({"ok":False,"error":"invalid_telegram_signature"},status=401)
    phone=db.digits_of(str(data.get("phone","")))
    account=await db.get_by_telegram(tg_id)
    by_phone=await db.fetchone("SELECT tg_id FROM users WHERE phone_digits=? AND banned=0",(phone,))
    if not account or not by_phone or account["tg_id"]!=by_phone["tg_id"]:
        return web.json_response({"ok":False,"error":"invalid_code"},status=401)
    status,body=await core_verify(phone,str(data.get("code","")))
    return web.json_response(body,status=status)

async def h_local_catalog(request: web.Request):
    try:page=max(0,min(10000,int(request.query.get("page","0"))))
    except ValueError:page=0
    rows,total=await asyncio.to_thread(local_gifts.catalog,page,50)
    return web.json_response({"ok":True,"items":rows,"total":total,"page":page})

async def h_local_drops(request: web.Request):
    return web.json_response({"ok":True,"items":[]})

async def h_local_inventory(request: web.Request):
    user=await _current_user(request)
    if not user:
        return web.json_response({"ok":False,"error":"unauthorized"},status=401)
    items=await asyncio.to_thread(local_gifts.inventory,user["tg_id"])
    return web.json_response({"ok":True,"items":items})

async def h_native_only(request: web.Request):
    return web.json_response({"ok":False,"error":"Откройте нативный раздел в приложении RiseGram."}, status=410)

async def h_index(request: web.Request):
    index_path = Path(__file__).with_name("index.html")
    if not index_path.is_file():
        return web.Response(text="RiseGram UI not found", status=404)
    return web.FileResponse(index_path)



_username_sync_last: dict[int, float] = {}
_username_sync_fingerprints: dict[int, tuple[tuple[int, str], ...]] = {}
_username_sync_locks: dict[int, asyncio.Lock] = {}


async def sync_collectible_usernames(user_id: int) -> dict:
    """Mirror the bot-owned username inventory into the matching MTProto peer.

    The bot-issued +888 number is the only cross-system identity key. Personal
    Telegram phone numbers are never read or sent to the MTProto Admin API.
    """
    base = config.MTPROTO_ADMIN_API_URL
    token = config.MTPROTO_ADMIN_API_TOKEN
    if not base or not token or user_id <= 0:
        return {"synced": 0, "skipped": 0, "configured": False}

    user = await db.get_user(int(user_id))
    if not user:
        return {"synced": 0, "skipped": 0, "configured": True}
    phone = str(user.get("phone") or "")
    phone_digits = db.digits_of(phone)
    if not phone_digits.startswith("888") or len(phone_digits) < 7:
        return {"synced": 0, "skipped": 0, "configured": True}

    inventory = await db.list_nft(int(user_id), "username")
    wanted = []
    for item in inventory:
        name = db.norm_username(str(item.get("value") or ""))
        if (not name or len(name) < 4 or len(name) > 32
                or not name[0].isalpha()
                or not all(ch.isascii() and (ch.isalnum() or ch == "_") for ch in name)):
            continue
        wanted.append((int(item["id"]), name))
    fingerprint = tuple(sorted(set(wanted)))
    if not fingerprint:
        return {"synced": 0, "skipped": 0, "configured": True}

    now_mono = time.monotonic()
    previous = _username_sync_fingerprints.get(int(user_id))
    last = _username_sync_last.get(int(user_id), 0.0)
    if previous == fingerprint and now_mono - last < 180:
        return {"synced": 0, "skipped": len(fingerprint), "configured": True}

    lock = _username_sync_locks.setdefault(int(user_id), asyncio.Lock())
    if lock.locked():
        return {"synced": 0, "skipped": len(fingerprint), "configured": True}
    async with lock:
        now_mono = time.monotonic()
        previous = _username_sync_fingerprints.get(int(user_id))
        last = _username_sync_last.get(int(user_id), 0.0)
        if previous == fingerprint and now_mono - last < 180:
            return {"synced": 0, "skipped": len(fingerprint), "configured": True}
        _username_sync_last[int(user_id)] = now_mono

        timeout = ClientTimeout(total=8, connect=3)
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        synced = 0
        skipped = 0
        try:
            async with ClientSession(timeout=timeout, headers=headers) as session:
                async with session.post(
                    f"{base}/v1/accounts/resolve-by-phone",
                    json={"phone": phone},
                ) as response:
                    if response.status != 200:
                        log.warning("MTProto username sync: account resolution returned HTTP %s", response.status)
                        return {"synced": 0, "skipped": len(fingerprint), "configured": True}
                    resolved = await response.json(content_type=None)
                if not resolved.get("found") or not resolved.get("user_id"):
                    # First-time logins are not in the MTProto account store until
                    # auth.signIn completes. Retry on a later bot interaction.
                    _username_sync_last[int(user_id)] = time.monotonic() - 165
                    return {"synced": 0, "skipped": len(fingerprint), "configured": True}
                mt_user_id = int(resolved["user_id"])

                async with session.get(
                    f"{base}/v1/collectible-usernames",
                    params={"owner_user_id": str(mt_user_id), "limit": "100"},
                ) as response:
                    if response.status != 200:
                        log.warning("MTProto username sync: current profile lookup returned HTTP %s", response.status)
                        return {"synced": 0, "skipped": len(fingerprint), "configured": True}
                    owned_data = await response.json(content_type=None)
                owned = {
                    db.norm_username(str(asset.get("username") or ""))
                    for asset in owned_data.get("assets", [])
                    if asset.get("status") == "owned"
                    and asset.get("owner_type") == "user"
                    and str(asset.get("owner_id") or "") == str(mt_user_id)
                }

                for item_id, name in fingerprint:
                    if name in owned:
                        skipped += 1
                        continue
                    async with session.get(
                        f"{base}/v1/collectible-usernames",
                        params={"q": name, "limit": "100"},
                    ) as response:
                        if response.status != 200:
                            skipped += 1
                            continue
                        search_data = await response.json(content_type=None)
                    asset = next(
                        (entry for entry in search_data.get("assets", [])
                         if db.norm_username(str(entry.get("username") or "")) == name),
                        None,
                    )
                    command_id = f"risegram-{int(user_id)}-{item_id}"
                    if asset:
                        status = asset.get("status")
                        owner_type = asset.get("owner_type")
                        owner_id = str(asset.get("owner_id") or "")
                        if status == "vault":
                            payload = {
                                "command_id": f"{command_id}-transfer",
                                "actor": "risegram-bot-sync",
                                "reason": "Sync bot-owned username inventory to MTProto profile",
                                "username": name,
                                "to_user_id": str(mt_user_id),
                            }
                            endpoint = "transfer"
                        elif status == "owned" and owner_type == "user" and owner_id == str(mt_user_id):
                            owned.add(name)
                            skipped += 1
                            continue
                        else:
                            # Never seize a name that is already owned by a
                            # different peer or has been permanently burned.
                            skipped += 1
                            continue
                    else:
                        payload = {
                            "command_id": f"{command_id}-mint",
                            "actor": "risegram-bot-sync",
                            "reason": "Import bot-owned username inventory into MTProto profile",
                            "username": name,
                            "owner_user_id": str(mt_user_id),
                            "currency": "USD",
                            # fragment.collectibleInfo.amount uses minor currency
                            # units: 2,243,700 = USD 22,437.00.
                            "amount": "2243700" if int(user["tg_id"]) == int(config.OWNER_ID) else "0",
                        }
                        endpoint = "mint"

                    async with session.post(
                        f"{base}/v1/collectible-usernames/{endpoint}",
                        json=payload,
                    ) as response:
                        result = await response.json(content_type=None)
                        if response.status == 200 and result.get("status") == "completed":
                            synced += 1
                            owned.add(name)
                        else:
                            skipped += 1
                            log.warning(
                                "MTProto username sync: %s rejected for item %s (HTTP %s, status %s)",
                                endpoint, item_id, response.status, result.get("status"),
                            )
        except Exception as exc:
            log.warning("MTProto username sync unavailable (%s)", type(exc).__name__)
            return {"synced": synced, "skipped": skipped, "configured": True}

        _username_sync_fingerprints[int(user_id)] = fingerprint
        _username_sync_last[int(user_id)] = time.monotonic()
        if synced:
            log.info("MTProto collectible usernames synced for RiseGram account %s: %s", user.get("rise_id"), synced)
        return {"synced": synced, "skipped": skipped, "configured": True}


async def h_mtproto_otp_delivery(request: web.Request):
    """Deliver gramsrv login codes into the existing linked RiseGram bot chat."""
    secret = config.MTPROTO_OTP_SECRET
    if not secret:
        return web.json_response({"accepted": False, "error_code": "provider_not_configured", "retryable": False}, status=503)
    raw = await request.read()
    if len(raw) > 16 * 1024:
        return web.json_response({"accepted": False, "error_code": "payload_too_large", "retryable": False}, status=413)
    timestamp = request.headers.get("X-Telesrv-Timestamp", "")
    signature = request.headers.get("X-Telesrv-Signature", "")
    try:
        sent_at = int(timestamp)
    except (TypeError, ValueError):
        return web.json_response({"accepted": False, "error_code": "invalid_signature", "retryable": False}, status=401)
    if abs(time.time() - sent_at) > 300:
        return web.json_response({"accepted": False, "error_code": "expired_signature", "retryable": False}, status=401)
    expected = "sha256=" + hmac.new(secret.encode(), timestamp.encode("ascii") + b"." + raw, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        return web.json_response({"accepted": False, "error_code": "invalid_signature", "retryable": False}, status=401)
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return web.json_response({"accepted": False, "error_code": "invalid_json", "retryable": False}, status=400)
    if not isinstance(data, dict) or data.get("version") != "1":
        return web.json_response({"accepted": False, "error_code": "invalid_payload", "retryable": False}, status=400)
    delivery_id = str(data.get("delivery_id") or "")
    purpose, channel = str(data.get("purpose") or ""), str(data.get("channel") or "")
    recipient, code = str(data.get("recipient") or ""), str(data.get("code") or "")
    if (not delivery_id or len(delivery_id) > 128 or channel != "sms" or purpose != "login_sms"
            or not recipient or len(recipient) > 40 or not code or len(code) > 16 or not code.isdigit()):
        return web.json_response({"accepted": False, "error_code": "unsupported_delivery", "retryable": False}, status=400)
    existing = await db.fetchone("SELECT status FROM rg_otp_deliveries WHERE delivery_id=?", (delivery_id,))
    if existing:
        if existing["status"] == "sent":
            return web.json_response({"accepted": True, "message_id": delivery_id})
        return web.json_response({"accepted": False, "error_code": "delivery_already_processed", "retryable": False}, status=409)
    now = db.now()
    await db.execute(
        """INSERT INTO rg_otp_deliveries(delivery_id,purpose,channel,status,created_at,updated_at)
           VALUES(?,?,?,'processing',?,?)""",
        (delivery_id, purpose, channel, now, now),
    )
    digits = db.digits_of(recipient)
    user = await db.fetchone("SELECT * FROM users WHERE phone_digits=? AND banned=0", (digits,)) if len(digits) >= 7 else None
    if not user or not await db.is_feature_on("auth_numbers"):
        await db.execute("UPDATE rg_otp_deliveries SET status='rejected',updated_at=? WHERE delivery_id=?", (db.now(), delivery_id))
        return web.json_response({"accepted": False, "error_code": "recipient_unavailable", "retryable": False}, status=404)
    chat_id = await db.telegram_chat_id(user["tg_id"])
    if not chat_id:
        await db.execute("UPDATE rg_otp_deliveries SET status='rejected',updated_at=? WHERE delivery_id=?", (db.now(), delivery_id))
        return web.json_response({"accepted": False, "error_code": "delivery_unavailable", "retryable": False}, status=503)
    message = (
        f"🔐 <b>Код входа в {config.BRAND}</b>\n\n"
        f"Номер: <code>{esc(user.get('phone') or recipient)}</code>\n"
        f"Код: <code>{esc(code)}</code>\n\n"
        "Код одноразовый. Никому его не сообщайте"
    )
    delivered = await safe_send(request.app["bot"], user["tg_id"], message)
    await db.execute(
        "UPDATE rg_otp_deliveries SET status=?,updated_at=? WHERE delivery_id=?",
        ("sent" if delivered else "failed", db.now(), delivery_id),
    )
    if not delivered:
        log.warning("MTProto OTP delivery failed for delivery id %s", delivery_id)
        return web.json_response({"accepted": False, "error_code": "delivery_failed", "retryable": False}, status=503)
    return web.json_response({"accepted": True, "message_id": delivery_id})


async def h_profile_verification(request: web.Request):
    peer = request.match_info["peer"]
    if not peer.isascii() or not peer.isdigit() or len(peer) > 19 or int(peer) >= 2**63:
        raise web.HTTPBadRequest()
    row = await db.fetchone(
        """SELECT u.verified_risegram, u.verified_major FROM verified_peer_links p
           JOIN users u ON u.tg_id=p.account_id AND u.phone_digits=p.phone_digits
           WHERE p.peer_id=? AND u.banned=0""", (int(peer),),
    )
    # Android builds with bundled issuer drawables prefer them to a native mark.
    # Let the installed app render the confirmed MTProto emoji and description.
    if row and "Dalvik/" in request.headers.get("User-Agent", "") and await db.get_meta(f"native_profile_mark:{int(peer)}"):
        row = None
    return web.json_response({
        "verified_risegram": bool(row and row["verified_risegram"]),
        "verified_major": bool(row and row["verified_major"]),
    }, headers={"Cache-Control": "no-store", "Vary": "User-Agent"})


async def h_health(request: web.Request):
    return web.json_response({"ok": True, "service": "risegram-api"})



async def _profile_music_row(rise_id: int) -> dict | None:
    try:
        return await db.fetchone(
            """SELECT m.title,m.media_id,m.updated_at, u.rise_id, f.storage_name,
                      f.mime_type,f.byte_size
               FROM rg_profile_music m JOIN users u ON u.tg_id=m.tg_id
               JOIN rg_media f ON f.id=m.media_id
               WHERE u.rise_id=? AND u.banned=0 LIMIT 1""",
            (int(rise_id),),
        )
    except sqlite3.OperationalError as exc:
        # Older/isolated test databases may not have run this optional migration yet.
        if "no such table: rg_profile_music" in str(exc):
            return None
        raise


def _music_public(row: dict | None) -> dict | None:
    if not row:
        return None
    return {
        "rise_id": row["rise_id"],
        "title": row["title"],
        "mime_type": row["mime_type"],
        "updated_at": row["updated_at"],
        "audio_url": f"/api/profiles/{row['rise_id']}/music/audio",
    }


async def h_profile_music_get(request: web.Request):
    viewer = await _current_user(request)
    if not viewer:
        return web.json_response({"ok": False, "error": "unauthorized"}, status=401)
    try:
        rise_id = int(request.match_info["rise_id"])
    except (ValueError, KeyError):
        return web.json_response({"ok": False, "error": "invalid_profile"}, status=400)
    return web.json_response({"ok": True, "music": _music_public(await _profile_music_row(rise_id))})


async def h_profile_music_save(request: web.Request):
    user = await _current_user(request)
    if not user:
        return web.json_response({"ok": False, "error": "unauthorized"}, status=401)
    data = await _json(request)
    media_id = str(data.get("media_id", "")).strip()
    title = str(data.get("title", "")).strip()
    if not 1 <= len(title) <= 100 or not 1 <= len(media_id) <= 64:
        return web.json_response({"ok": False, "error": "bad_music_details"}, status=400)
    media = await db.fetchone("SELECT * FROM rg_media WHERE id=? AND uploader_id=?",
                              (media_id, int(user["tg_id"])))
    if not media or media["mime_type"] not in ("audio/mpeg", "audio/ogg", "audio/opus", "audio/wav", "audio/mp4"):
        return web.json_response({"ok": False, "error": "music_upload_required"}, status=400)
    path = Path(config.MEDIA_PATH) / media["storage_name"]
    if not path.is_file():
        return web.json_response({"ok": False, "error": "media_missing"}, status=404)
    await db.execute("""INSERT INTO rg_profile_music(tg_id,media_id,title,updated_at)
        VALUES(?,?,?,?)
        ON CONFLICT(tg_id) DO UPDATE SET
            media_id=excluded.media_id,title=excluded.title,updated_at=excluded.updated_at""",
        (int(user["tg_id"]), media_id, title, db.now()))
    await _publish_event(int(user["tg_id"]), {"type": "profile_music_changed"})
    return web.json_response({"ok": True, "music": _music_public(await _profile_music_row(int(user["rise_id"])))})


async def h_profile_music_delete(request: web.Request):
    user = await _current_user(request)
    if not user:
        return web.json_response({"ok": False, "error": "unauthorized"}, status=401)
    await db.execute("DELETE FROM rg_profile_music WHERE tg_id=?", (int(user["tg_id"]),))
    await _publish_event(int(user["tg_id"]), {"type": "profile_music_changed"})
    return web.json_response({"ok": True})


async def h_profile_music_audio(request: web.Request):
    viewer = await _current_user(request)
    if not viewer:
        return web.json_response({"ok": False, "error": "unauthorized"}, status=401)
    try:
        rise_id = int(request.match_info["rise_id"])
    except (ValueError, KeyError):
        return web.json_response({"ok": False, "error": "invalid_profile"}, status=400)
    row = await _profile_music_row(rise_id)
    if not row:
        return web.json_response({"ok": False, "error": "music_not_found"}, status=404)
    path = Path(config.MEDIA_PATH) / row["storage_name"]
    if not path.is_file():
        return web.json_response({"ok": False, "error": "media_missing"}, status=404)
    return web.FileResponse(path, headers={
        "Content-Type": row["mime_type"],
        "Content-Disposition": 'inline',
        "Cache-Control": "private, no-store",
        "X-Content-Type-Options": "nosniff",
    })


async def h_desktop(request: web.Request):
    path = Path(__file__).parent / "desktop.html"
    return web.FileResponse(path, headers={
        "Content-Type": "text/html; charset=utf-8",
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; connect-src 'self'; media-src 'self' blob:; img-src 'self' data: blob:; object-src 'none'; frame-ancestors 'none'; base-uri 'none'",
    })


async def start_api(bot) -> web.AppRunner | None:
    # Public auth endpoints are protected by per-phone cooldown and one-time codes.
    # API_SECRET is intentionally not required by a public APK.
    app = web.Application(client_max_size=26 * 1024 * 1024)
    app["bot"] = bot
    app.add_routes([
        web.get("/", h_index),
        web.get("/miniapp", h_local_miniapp),
        web.get("/desktop", h_desktop),
        web.get("/api/profiles/{rise_id}/music", h_profile_music_get),
        web.get("/api/profiles/{rise_id}/music/audio", h_profile_music_audio),
        web.post("/api/profile/music", h_profile_music_save),
        web.delete("/api/profile/music", h_profile_music_delete),
        web.post("/api/webapp/auth/request", h_local_webapp_auth_request),
        web.post("/api/webapp/auth/verify", h_local_webapp_auth_verify),
        web.get("/api/local-gifts/catalog", h_local_catalog),
        web.get("/api/local-gifts/drops", h_local_drops),
        web.get("/api/local-gifts/inventory", h_local_inventory),
        web.post("/api/local-gifts/buy", h_native_only),
        web.post("/api/local-gifts/upgrade", h_native_only),
        web.post("/api/auth/request", h_request),
        web.post("/api/auth/verify", h_verify),
        web.get("/api/me", h_me),
        web.get("/api/events", h_events),
        web.get("/api/users/search", h_user_search),
        web.get("/api/chats", h_chats),
        web.get("/api/chats/{rise_id}/messages", h_messages),
        web.get("/api/spaces", h_spaces),
        web.post("/api/spaces", h_space_create),
        web.post("/api/spaces/join", h_space_join),
        web.get("/api/spaces/{space_id}/messages", h_space_messages),
        web.post("/api/spaces/{space_id}/messages", h_space_send),
        web.get("/api/spaces/{space_id}/members", h_space_members),
        web.post("/api/spaces/{space_id}/members/{rise_id}/role", h_space_change_role),
        web.delete("/api/spaces/{space_id}/members/{rise_id}", h_space_remove_member),
        web.post("/api/spaces/{space_id}/owner/{rise_id}", h_space_transfer_owner),
        web.post("/api/spaces/{space_id}/invite/rotate", h_space_rotate_invite),
        web.post("/api/spaces/{space_id}/leave", h_space_leave),
        web.post("/api/chats/{rise_id}/messages", h_send_message),
        web.post("/api/media", h_media_upload),
        web.get("/api/media/{media_id}", h_media_download),
        web.get("/api/blocks", h_chat_blocks),
        web.post("/api/blocks/{rise_id}", h_block_user),
        web.delete("/api/blocks/{rise_id}", h_unblock_user),
        web.get("/api/inventory", h_inventory),
        web.post("/api/inventory/{item_id}/activate", h_activate_nft),
        web.get("/api/gifts", h_native_only),
        web.post("/api/gifts/{drop_id}/buy", h_native_only),
        web.post("/api/exchange", h_exchange),
        web.get("/api/market", h_market),
        web.post("/api/market", h_market_list),
        web.post("/api/market/{listing_id}/buy", h_market_buy),
        web.get("/api/games/stats", h_game_stats),
        web.get("/api/purchases", h_purchases),
        web.post("/internal/mtproto/otp", h_mtproto_otp_delivery),
        web.get("/api/profiles/{peer}/verification", h_profile_verification),
        web.get("/health", h_health),
        web.get("/{username}", h_public_link),
    ])
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", config.PORT).start()
    log.info("HTTP API listening on port %s", config.PORT)
    return runner
