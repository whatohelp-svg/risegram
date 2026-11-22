"""RiseGram Bot API MVP: independent RiseGram credentials, no Telegram BotFather tokens.

Implements createBot, getMe, getUpdates, sendMessage for private RiseGram users.
Does not claim complete Telegram Bot API compatibility.
"""
from __future__ import annotations

import hashlib
import json
import re
import secrets
from aiohttp import web
import database as db


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _err(description: str, status: int = 400):
    return web.json_response({"ok": False, "error_code": status, "description": description}, status=status)


async def _owner(request):
    from api import _current_user
    return await _current_user(request)


async def create_bot(request: web.Request):
    user = await _owner(request)
    if not user:
        return _err("Unauthorized", 401)
    try:
        data = await request.json()
    except (ValueError, TypeError):
        return _err("Invalid JSON")
    username = str(data.get("username", "")).strip().lstrip("@").lower()
    title = str(data.get("name", "")).strip()
    if not re.fullmatch(r"[a-z][a-z0-9_]{4,31}bot", username):
        return _err("Username must be 8-34 characters and end with bot")
    if not 1 <= len(title) <= 80:
        return _err("Bot name must be 1-80 characters")
    count = await db.scalar("SELECT COUNT(*) FROM rg_bot_accounts WHERE owner_id=?", (int(user["tg_id"]),), 0)
    if int(count) >= 20:
        return _err("Bot limit reached", 429)
    token = f"rg_{secrets.token_urlsafe(40)}"
    try:
        await db.execute(
            "INSERT INTO rg_bot_accounts(owner_id,username,display_name,token_hash,created_at) VALUES(?,?,?,?,?)",
            (int(user["tg_id"]), username, title, _hash(token), db.now()),
        )
    except Exception:
        return _err("Username already taken", 409)
    return web.json_response({"ok": True, "bot": {"username": username, "name": title},
                              "token": token}, headers={"Cache-Control": "no-store"})


async def _authenticate(request: web.Request):
    token = request.match_info.get("token", "")
    if not token.startswith("rg_") or len(token) > 160:
        return None
    return await db.fetchone(
        "SELECT * FROM rg_bot_accounts WHERE token_hash=? AND enabled=1", (_hash(token),)
    )


def _reply(data):
    return web.json_response({"ok": True, "result": data}, headers={"Cache-Control": "no-store"})


async def handle(request: web.Request):
    bot = await _authenticate(request)
    if not bot:
        return _err("Unauthorized bot token", 401)
    method = request.match_info.get("method", "")
    if method == "getMe":
        return _reply({"id": bot["id"], "is_bot": True, "first_name": bot["display_name"],
                       "username": bot["username"], "can_join_groups": False,
                       "supports_inline_queries": False})
    try:
        data = await request.json() if request.can_read_body else {}
    except (ValueError, TypeError):
        data = {}
    if method == "getUpdates":
        try:
            offset = max(0, int(data.get("offset", 0)))
            limit = min(100, max(1, int(data.get("limit", 100))))
        except (ValueError, TypeError):
            return _err("Invalid offset or limit")
        # Update queue is durable; acknowledge only when the client advances offset.
        if offset:
            await db.execute("DELETE FROM rg_bot_updates WHERE bot_id=? AND id<?", (bot["id"], offset))
        rows = await db.fetchall(
            "SELECT id,payload FROM rg_bot_updates WHERE bot_id=? AND id>=? ORDER BY id LIMIT ?",
            (bot["id"], offset, limit),
        )
        return _reply([{"update_id": row["id"], **json.loads(row["payload"])} for row in rows])
    if method == "sendMessage":
        # No bot-to-user delivery exists yet; do not acknowledge a phantom message.
        return _err("sendMessage not available until RiseGram bot inbox integration", 501)
    return _err("Unsupported Bot API method: " + method, 404)


def routes():
    return [web.post("/api/bots/create", create_bot),
            web.post("/bot{token}/{method}", handle),
            web.get("/bot{token}/{method}", handle)]
