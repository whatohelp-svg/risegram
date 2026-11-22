"""Native RiseGram bot transport (private chats, first phase).

No Telegram token or Telegram session is required. This module deliberately
supports a subset of the Telegram Bot API compatible with aiogram polling.
"""
from __future__ import annotations

import asyncio
import hashlib
import html
import json
import re
import secrets
import sqlite3
import time
from datetime import datetime, timezone

from aiohttp import web

import database as db
import config

BOT_ID_OFFSET = 900_000_000
BOT_MAX_MESSAGES = 100
MAX_TEXT = 4000
BOT_USERNAME_RE = re.compile(r"[a-z][a-z0-9_]{4,27}bot\Z")
_TAGS = re.compile(r"<[^>]*>")


def _err(description: str, status: int = 400):
    return web.json_response(
        {"ok": False, "error_code": status, "description": description}, status=status,
        headers={"Cache-Control": "no-store"},
    )


def _ok(result):
    return web.json_response({"ok": True, "result": result}, headers={"Cache-Control": "no-store"})


def _hash(token: str):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def bot_public(bot: dict):
    return {
        "rise_id": -int(bot["id"]), "bot_id": int(bot["id"]),
        "is_bot": True, "name": bot["display_name"], "username": bot["username"],
        "verified": False,
    }


def bot_api_user(bot: dict):
    return {
        "id": BOT_ID_OFFSET + int(bot["id"]), "is_bot": True,
        "first_name": bot["display_name"], "username": bot["username"],
    }


def chat_obj(user: dict):
    return {
        "id": int(user["rise_id"]), "type": "private",
        "first_name": user.get("name") or "Пользователь",
        **({"username": user["username"]} if user.get("username") else {}),
    }


def from_obj(user: dict):
    return {
        "id": int(user["rise_id"]), "is_bot": False,
        "first_name": user.get("name") or "Пользователь",
        **({"username": user["username"]} if user.get("username") else {}),
    }


def _unix(ts: str):
    try:
        return int(datetime.strptime(ts, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc).timestamp())
    except (ValueError, TypeError):
        return int(time.time())


def _display(text: str, parse_mode: str = ""):
    # Keep HTML safe and legible for native/web text-only rendering.
    if str(parse_mode).lower() == "html":
        return html.unescape(_TAGS.sub("", text))
    return text


def _markup(value):
    if value is None or value == "":
        return None
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            raise ValueError("Invalid reply_markup JSON")
    if not isinstance(value, dict):
        raise ValueError("Invalid reply_markup")
    if len(json.dumps(value, ensure_ascii=False)) > 12000:
        raise ValueError("Keyboard too large")

    if value.get("remove_keyboard") or value.get("force_reply"):
        return None
    is_inline = "inline_keyboard" in value
    rows = value.get("inline_keyboard") if is_inline else value.get("keyboard")
    if not isinstance(rows, list) or len(rows) > 20:
        raise ValueError("Unsupported reply_markup")
    for row in rows:
        if not isinstance(row, list) or len(row) > 8:
            raise ValueError("Keyboard row too large")
        for button in row:
            if not isinstance(button, dict) or not 1 <= len(str(button.get("text", ""))) <= 100:
                raise ValueError("Invalid button")
            if is_inline:
                cb = button.get("callback_data")
                url = button.get("url")
                if cb is not None:
                    if not isinstance(cb, str) or not 1 <= len(cb.encode("utf-8")) <= 64:
                        raise ValueError("Invalid callback_data")
                elif url is not None:
                    if not isinstance(url, str) or not url.startswith("https://") or len(url) > 2048:
                        raise ValueError("Unsupported button URL")
                else:
                    raise ValueError("Only callback and HTTPS URL buttons supported")
            elif button.keys() != {"text"}:
                # Reject unsupported contact/location requests rather than pretending.
                raise ValueError("Only text reply keyboard buttons supported")
    return {"inline_keyboard" if is_inline else "keyboard": rows}


def _client_message(row):
    markup = json.loads(row["reply_markup"]) if row.get("reply_markup") else None
    return {
        "id": int(row["id"]), "from_me": not bool(row["from_bot"]),
        "text": row["body"], "created_at": row["created_at"],
        "read": True, "media": [], "reply_markup": markup,
    }


def _telegram_message(row, bot, user):
    out = {
        "message_id": int(row["id"]), "date": _unix(row["created_at"]),
        "chat": chat_obj(user), "text": row["body"],
        "from": bot_api_user(bot) if row["from_bot"] else from_obj(user),
    }
    if row.get("reply_markup"):
        out["reply_markup"] = json.loads(row["reply_markup"])
    return out


async def _account(request):
    from api import _current_user
    return await _current_user(request)


async def create_bot(request: web.Request):
    user = await _account(request)
    if user is None:
        return _err("Unauthorized", 401)
    try:
        data = await request.json()
    except (ValueError, TypeError):
        return _err("Invalid JSON")
    if not isinstance(data, dict):
        return _err("Invalid JSON")
    username = str(data.get("username", "")).strip().lstrip("@").lower()
    name = str(data.get("name", "")).strip()
    if not BOT_USERNAME_RE.fullmatch(username):
        return _err("Username must end with bot and be 8-34 characters")
    if not 1 <= len(name) <= 80:
        return _err("Name must be 1-80 characters")
    # Reserve the first-party TypeVirt identity for RiseGram's configured owner.
    if username == "typevirtbot" and int(user["tg_id"]) != int(config.OWNER_ID):
        return _err("This username is reserved for the RiseGram owner", 403)
    # Avoid stealing an ordinary RiseGram username.
    taken = await db.scalar(
        """SELECT COUNT(*) FROM users WHERE lower(username)=?
           OR lower(active_username)=?""", (username, username), 0,
    )
    if taken:
        return _err("Username already taken", 409)
    try:
        async with db.connect() as conn:
            cur = await conn.execute(
                "SELECT COUNT(*) FROM rg_bot_accounts WHERE owner_id=?", (int(user["tg_id"]),),
            )
            count = (await cur.fetchone())[0]
            await cur.close()
            if count >= 20:
                return _err("Bot account limit reached", 429)
            # Placeholder hash is not a credential, and is replaced before commit.
            cur = await conn.execute(
                """INSERT INTO rg_bot_accounts
                   (owner_id,username,display_name,token_hash,created_at)
                   VALUES(?,?,?,?,?)""",
                (int(user["tg_id"]), username, name, _hash(secrets.token_urlsafe(64)), db.now()),
            )
            bot_id = int(cur.lastrowid)
            token = str(BOT_ID_OFFSET + bot_id) + ":" + secrets.token_urlsafe(32)
            await conn.execute(
                "UPDATE rg_bot_accounts SET token_hash=? WHERE id=?", (_hash(token), bot_id),
            )
            await conn.commit()
    except sqlite3.IntegrityError:
        return _err("Username already taken", 409)
    return web.json_response(
        {"ok": True, "bot": {"bot_id": bot_id, "username": username, "name": name}, "token": token},
        headers={"Cache-Control": "no-store"},
    )


async def mine(request: web.Request):
    user = await _account(request)
    if user is None:
        return _err("Unauthorized", 401)
    rows = await db.fetchall(
        "SELECT id,username,display_name,enabled,created_at FROM rg_bot_accounts WHERE owner_id=? ORDER BY id DESC",
        (int(user["tg_id"]),),
    )
    return web.json_response({"ok": True, "bots": [
        {"bot_id": x["id"], "username": x["username"], "name": x["display_name"],
         "enabled": bool(x["enabled"]), "created_at": x["created_at"]} for x in rows
    ]}, headers={"Cache-Control": "no-store"})


async def rotate_token(request: web.Request):
    user = await _account(request)
    if user is None:
        return _err("Unauthorized", 401)
    try:
        bot_id = int(request.match_info["bot_id"])
    except (ValueError, TypeError):
        return _err("Invalid bot id")
    own = await db.fetchone("SELECT id FROM rg_bot_accounts WHERE id=? AND owner_id=?",
                            (bot_id, int(user["tg_id"])))
    if own is None:
        return _err("Bot not found", 404)
    token = str(BOT_ID_OFFSET + bot_id) + ":" + secrets.token_urlsafe(32)
    await db.execute("UPDATE rg_bot_accounts SET token_hash=? WHERE id=?", (_hash(token), bot_id))
    return web.json_response({"ok": True, "token": token}, headers={"Cache-Control": "no-store"})


async def list_chats(user_id: int):
    rows = await db.fetchall(
        """SELECT b.id,b.username,b.display_name,
               (SELECT body FROM rg_bot_messages m WHERE m.bot_id=b.id AND m.user_id=c.user_id
                ORDER BY id DESC LIMIT 1) AS last_message,
               c.last_at
           FROM rg_bot_conversations c
           JOIN rg_bot_accounts b ON b.id=c.bot_id
           WHERE c.user_id=? AND b.enabled=1 ORDER BY c.last_at DESC""",
        (user_id,),
    )
    return [{"user": bot_public(x), "last_message": x["last_message"] or "",
             "last_at": x["last_at"], "unread": 0} for x in rows]


async def search_bots(query: str):
    like = "%" + query.lower().lstrip("@") + "%"
    rows = await db.fetchall(
        """SELECT * FROM rg_bot_accounts
           WHERE enabled=1 AND (lower(username) LIKE ? OR lower(display_name) LIKE ?)
           ORDER BY username LIMIT 20""",
        (like, like),
    )
    return [bot_public(x) for x in rows]


async def _get_bot(bot_id: int):
    return await db.fetchone(
        "SELECT * FROM rg_bot_accounts WHERE id=? AND enabled=1", (bot_id,),
    )


async def client_messages(request: web.Request, user: dict, bot_id: int):
    bot = await _get_bot(bot_id)
    if bot is None:
        return web.json_response({"ok": False, "error": "bot_not_found"}, status=404)
    rows = await db.fetchall(
        """SELECT * FROM rg_bot_messages WHERE bot_id=? AND user_id=? ORDER BY id DESC LIMIT ?""",
        (bot_id, user["tg_id"], BOT_MAX_MESSAGES),
    )
    return web.json_response({
        "ok": True, "peer": bot_public(bot),
        "messages": [_client_message(x) for x in reversed(rows)],
    })


async def client_send(request: web.Request, user: dict, bot_id: int):
    from api import _json
    bot = await _get_bot(bot_id)
    if bot is None:
        return web.json_response({"ok": False, "error": "bot_not_found"}, status=404)
    data = await _json(request)
    body = str(data.get("text") or "").strip()
    if not 1 <= len(body) <= MAX_TEXT or data.get("media_ids"):
        return web.json_response({"ok": False, "error": "invalid_bot_message"}, status=400)
    now = db.now()
    async with db.connect() as conn:
        await conn.execute(
            """INSERT INTO rg_bot_conversations(bot_id,user_id,created_at,last_at)
               VALUES(?,?,?,?) ON CONFLICT(bot_id,user_id) DO UPDATE SET last_at=excluded.last_at""",
            (bot_id, int(user["tg_id"]), now, now),
        )
        cur = await conn.execute(
            """INSERT INTO rg_bot_messages(bot_id,user_id,from_bot,body,created_at)
               VALUES(?,?,0,?,?)""",
            (bot_id, int(user["tg_id"]), body, now),
        )
        message_id = int(cur.lastrowid)
        payload = {
            "message": {
                "message_id": message_id, "date": _unix(now),
                "chat": chat_obj(user), "from": from_obj(user), "text": body,
            },
        }
        await conn.execute(
            "INSERT INTO rg_bot_updates(bot_id,payload,created_at) VALUES(?,?,?)",
            (bot_id, json.dumps(payload, ensure_ascii=False), now),
        )
        await conn.commit()
    return web.json_response({
        "ok": True, "message": {"id": message_id, "text": body, "from_me": True,
                                "created_at": now, "read": True, "media": []},
    })


async def client_callback(request: web.Request, user: dict, bot_id: int):
    from api import _json
    bot = await _get_bot(bot_id)
    if bot is None:
        return _err("Bot not found", 404)
    data = await _json(request)
    try:
        mid = int(data.get("message_id", 0))
    except (ValueError, TypeError):
        return _err("Invalid message_id")
    cb = data.get("data")
    if not isinstance(cb, str) or not 1 <= len(cb.encode("utf-8")) <= 64:
        return _err("Invalid callback_data")
    row = await db.fetchone(
        """SELECT * FROM rg_bot_messages WHERE id=? AND bot_id=? AND user_id=? AND from_bot=1""",
        (mid, bot_id, int(user["tg_id"])),
    )
    if row is None or not row.get("reply_markup"):
        return _err("Button unavailable", 404)
    markup = json.loads(row["reply_markup"])
    if not any(button.get("callback_data") == cb for line in markup.get("inline_keyboard", []) for button in line):
        return _err("Button unavailable", 403)
    callback_id = secrets.token_urlsafe(24)
    now = db.now()
    async with db.connect() as conn:
        await conn.execute(
            "INSERT INTO rg_bot_callback_queries(id,bot_id,user_id,created_at) VALUES(?,?,?,?)",
            (callback_id, bot_id, int(user["tg_id"]), now),
        )
        await conn.execute(
            "INSERT INTO rg_bot_updates(bot_id,payload,created_at) VALUES(?,?,?)",
            (bot_id, json.dumps({"callback_query": {
                "id": callback_id, "from": from_obj(user),
                "message": _telegram_message(row, bot, user),
                "chat_instance": str(user["rise_id"]), "data": cb,
            }}, ensure_ascii=False), now),
        )
        await conn.commit()
    return web.json_response({"ok": True})


async def _authenticate(request: web.Request):
    token = request.match_info.get("token", "")
    if not token or len(token) > 160 or not re.fullmatch(r"[0-9]+:[A-Za-z0-9_-]+", token):
        return None
    return await db.fetchone(
        "SELECT * FROM rg_bot_accounts WHERE token_hash=? AND enabled=1", (_hash(token),),
    )


async def _parameters(request: web.Request):
    if request.method == "GET":
        return dict(request.query)
    if request.content_type == "application/json":
        try:
            data = await request.json()
            return data if isinstance(data, dict) else {}
        except (ValueError, TypeError):
            return {}
    try:
        form = await request.post()
        return dict(form)
    except (ValueError, TypeError):
        return {}


async def _target(bot: dict, chat_id):
    try:
        rise_id = int(chat_id)
    except (ValueError, TypeError):
        return None
    if rise_id <= 0:
        return None
    user = await db.fetchone("SELECT * FROM users WHERE rise_id=? AND banned=0", (rise_id,))
    if user is None:
        return None
    active = await db.scalar(
        "SELECT COUNT(*) FROM rg_bot_conversations WHERE bot_id=? AND user_id=?",
        (bot["id"], user["tg_id"]), 0,
    )
    return user if active else None


async def _send_message(bot, data):
    user = await _target(bot, data.get("chat_id"))
    if user is None:
        return _err("Chat not found or user has not started the bot", 403)
    body = data.get("text")
    if not isinstance(body, str) or not 1 <= len(body.strip()) <= MAX_TEXT:
        return _err("Message text must be 1-4000 characters")
    try:
        markup = _markup(data.get("reply_markup"))
    except ValueError as exc:
        return _err(str(exc))
    body = _display(body.strip(), data.get("parse_mode") or "")
    now = db.now()
    async with db.connect() as conn:
        cur = await conn.execute(
            """INSERT INTO rg_bot_messages
               (bot_id,user_id,from_bot,body,reply_markup,created_at) VALUES(?,?,1,?,?,?)""",
            (bot["id"], user["tg_id"], body,
             json.dumps(markup, ensure_ascii=False) if markup else None, now),
        )
        mid = int(cur.lastrowid)
        await conn.execute(
            "UPDATE rg_bot_conversations SET last_at=? WHERE bot_id=? AND user_id=?",
            (now, bot["id"], user["tg_id"]),
        )
        await conn.commit()
    from api import _publish_event
    await _publish_event(int(user["tg_id"]), {"type": "new_message", "from_bot_id": bot["id"]})
    row = await db.fetchone("SELECT * FROM rg_bot_messages WHERE id=?", (mid,))
    return _ok(_telegram_message(row, bot, user))


async def _edit_message(bot, data):
    user = await _target(bot, data.get("chat_id"))
    if user is None:
        return _err("Chat not found", 404)
    try:
        mid = int(data.get("message_id", 0))
    except (ValueError, TypeError):
        return _err("Invalid message_id")
    row = await db.fetchone(
        """SELECT * FROM rg_bot_messages
           WHERE id=? AND bot_id=? AND user_id=? AND from_bot=1""",
        (mid, bot["id"], user["tg_id"]),
    )
    if row is None:
        return _err("Message not found", 404)
    text = data.get("text")
    if not isinstance(text, str) or not 1 <= len(text.strip()) <= MAX_TEXT:
        return _err("Invalid text")
    try:
        markup = _markup(data["reply_markup"]) if "reply_markup" in data else (
            json.loads(row["reply_markup"]) if row.get("reply_markup") else None
        )
    except ValueError as exc:
        return _err(str(exc))
    await db.execute(
        """UPDATE rg_bot_messages SET body=?,reply_markup=?,edited_at=?
           WHERE id=?""",
        (_display(text.strip(), data.get("parse_mode") or ""),
         json.dumps(markup, ensure_ascii=False) if markup else None, db.now(), mid),
    )
    from api import _publish_event
    await _publish_event(int(user["tg_id"]), {"type": "new_message", "from_bot_id": bot["id"]})
    row = await db.fetchone("SELECT * FROM rg_bot_messages WHERE id=?", (mid,))
    return _ok(_telegram_message(row, bot, user))


async def _send_dice(bot, data):
    user = await _target(bot, data.get("chat_id"))
    if user is None:
        return _err("Chat not found or user has not started the bot", 403)
    emoji = str(data.get("emoji") or "🎲")
    ranges = {"🎲": 6, "🎯": 6, "🏀": 5, "⚽": 5, "🎳": 6, "🎰": 64}
    if emoji not in ranges:
        return _err("Unsupported dice emoji")
    value = secrets.randbelow(ranges[emoji]) + 1
    now = db.now()
    async with db.connect() as conn:
        cur = await conn.execute(
            """INSERT INTO rg_bot_messages(bot_id,user_id,from_bot,body,created_at)
               VALUES(?,?,1,?,?)""",
            (bot["id"], user["tg_id"], emoji + " " + str(value), now),
        )
        mid = int(cur.lastrowid)
        await conn.execute(
            "UPDATE rg_bot_conversations SET last_at=? WHERE bot_id=? AND user_id=?",
            (now, bot["id"], user["tg_id"]),
        )
        await conn.commit()
    from api import _publish_event
    await _publish_event(int(user["tg_id"]), {"type": "new_message", "from_bot_id": bot["id"]})
    return _ok({
        "message_id": mid, "date": _unix(now), "chat": chat_obj(user),
        "from": bot_api_user(bot), "dice": {"emoji": emoji, "value": value},
    })


async def _edit_markup(bot, data):
    user = await _target(bot, data.get("chat_id"))
    if user is None:
        return _err("Chat not found", 404)
    try:
        mid = int(data.get("message_id", 0))
    except (ValueError, TypeError):
        return _err("Invalid message_id")
    row = await db.fetchone(
        """SELECT * FROM rg_bot_messages
           WHERE id=? AND bot_id=? AND user_id=? AND from_bot=1""",
        (mid, bot["id"], user["tg_id"]),
    )
    if row is None:
        return _err("Message not found", 404)
    try:
        markup = _markup(data.get("reply_markup"))
    except ValueError as exc:
        return _err(str(exc))
    await db.execute(
        "UPDATE rg_bot_messages SET reply_markup=?,edited_at=? WHERE id=?",
        (json.dumps(markup, ensure_ascii=False) if markup else None, db.now(), mid),
    )
    from api import _publish_event
    await _publish_event(int(user["tg_id"]), {"type": "new_message", "from_bot_id": bot["id"]})
    row = await db.fetchone("SELECT * FROM rg_bot_messages WHERE id=?", (mid,))
    return _ok(_telegram_message(row, bot, user))


async def _delete_message(bot, data):
    user = await _target(bot, data.get("chat_id"))
    if user is None:
        return _err("Chat not found", 404)
    try:
        mid = int(data.get("message_id", 0))
    except (ValueError, TypeError):
        return _err("Invalid message_id")
    deleted = await db.execute(
        """DELETE FROM rg_bot_messages WHERE id=? AND bot_id=? AND user_id=? AND from_bot=1""",
        (mid, bot["id"], user["tg_id"]),
    )
    if not deleted:
        return _err("Message not found or not owned by bot", 404)
    from api import _publish_event
    await _publish_event(int(user["tg_id"]), {"type": "new_message", "from_bot_id": bot["id"]})
    return _ok(True)


async def handle(request: web.Request):
    bot = await _authenticate(request)
    if bot is None:
        return _err("Unauthorized bot token", 401)
    method = request.match_info.get("method", "")
    if method == "getMe":
        return _ok({**bot_api_user(bot), "can_join_groups": False,
                    "supports_inline_queries": False, "can_read_all_group_messages": False})
    data = await _parameters(request)
    if method == "getUpdates":
        try:
            offset = max(0, int(data.get("offset", 0)))
            limit = min(100, max(1, int(data.get("limit", 100))))
            timeout = min(25, max(0, int(data.get("timeout", 0))))
        except (ValueError, TypeError):
            return _err("Invalid offset/limit/timeout")
        if offset:
            await db.execute(
                "DELETE FROM rg_bot_updates WHERE bot_id=? AND id<?",
                (bot["id"], offset),
            )
        deadline = time.monotonic() + timeout
        while True:
            rows = await db.fetchall(
                """SELECT id,payload FROM rg_bot_updates
                   WHERE bot_id=? AND id>=? ORDER BY id LIMIT ?""",
                (bot["id"], offset, limit),
            )
            if rows or time.monotonic() >= deadline:
                break
            await asyncio.sleep(min(0.5, max(0, deadline - time.monotonic())))
        return _ok([{"update_id": row["id"], **json.loads(row["payload"])} for row in rows])
    if method == "sendMessage":
        return await _send_message(bot, data)
    if method == "sendDice":
        return await _send_dice(bot, data)
    if method == "editMessageReplyMarkup":
        return await _edit_markup(bot, data)
    if method == "deleteMessage":
        return await _delete_message(bot, data)
    if method == "editMessageText":
        return await _edit_message(bot, data)
    if method == "answerCallbackQuery":
        cb_id = str(data.get("callback_query_id", ""))
        affected = await db.execute(
            """UPDATE rg_bot_callback_queries SET answered=1
               WHERE id=? AND bot_id=? AND answered=0""",
            (cb_id, bot["id"]),
        )
        return _ok(True) if affected else _err("Callback query not found", 404)
    if method == "deleteWebhook":
        return _ok(True)
    if method == "getWebhookInfo":
        return _ok({"url": "", "has_custom_certificate": False, "pending_update_count": 0})
    return _err("Unsupported Bot API method: " + method, 501)


def routes():
    return [
        web.post("/api/bots/create", create_bot),
        web.get("/api/bots/mine", mine),
        web.post("/api/bots/{bot_id}/rotate-token", rotate_token),
        web.post("/api/bots/{bot_id}/callback", client_callback_route),
        web.post("/bot{token}/{method}", handle),
        web.get("/bot{token}/{method}", handle),
    ]


async def client_callback_route(request):
    user = await _account(request)
    if user is None:
        return _err("Unauthorized", 401)
    try:
        bot_id = int(request.match_info["bot_id"])
    except (TypeError, ValueError):
        return _err("Invalid bot id")
    return await client_callback(request, user, bot_id)
