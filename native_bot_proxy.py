"""Public Bot API gateway to RiseGram's *native* gramsrv MTProto service.

The backend has a legacy SQLite bot registry whose identities are NOT visible
to Android's MTProto chats. Do not use it for the public /bot API endpoint.
This proxy forwards Telegram-compatible Bot API requests to gramsrv, preserving
the native bot account, groups, permissions, and update queue.
"""
from __future__ import annotations

import os
import re

from aiohttp import ClientError, ClientSession, ClientTimeout, web

_NATIVE_BASE = os.getenv("MTPROTO_BOT_API_URL", "http://risegram-mtproto.railway.internal:8081").rstrip("/")
_VALID_TOKEN = re.compile(r"[0-9]{1,20}:[A-Za-z0-9_-]{12,200}\Z")
_VALID_METHOD = re.compile(r"[A-Za-z][A-Za-z0-9]{0,80}\Z")
_VALID_FILE_PATH = re.compile(r"[A-Za-z0-9_./-]{1,1024}\Z")
_MAX_BYTES = 26 * 1024 * 1024
_SUPPORTED_CT = ("application/json", "application/x-www-form-urlencoded", "multipart/form-data")


def _invalid(message: str, status: int = 400):
    return web.json_response({"ok": False, "error_code": status, "description": message}, status=status)


async def _forward(request: web.Request, destination: str, *, file_response: bool = False):
    token = request.match_info.get("token", "")
    if not _VALID_TOKEN.fullmatch(token):
        return _invalid("ACCESS_TOKEN_INVALID", 401)

    if request.method == "POST":
        content_type = request.content_type
        if content_type not in _SUPPORTED_CT:
            return _invalid("UNSUPPORTED_CONTENT_TYPE", 415)
        if request.content_length is not None and request.content_length > _MAX_BYTES:
            return _invalid("FILE_TOO_BIG", 413)
        payload = await request.read()
        if len(payload) > _MAX_BYTES:
            return _invalid("FILE_TOO_BIG", 413)
    else:
        payload = None
    session = request.app["native_bot_api_session"]
    headers = {"Accept": "application/json"}
    if payload is not None:
        headers["Content-Type"] = request.headers["Content-Type"]
    try:
        async with session.request(
            request.method, destination, params=request.query,
            data=payload, headers=headers, allow_redirects=False,
        ) as response:
            body = await response.read()
            # Bot API files can be large, but never buffer unlimited responses.
            if len(body) > _MAX_BYTES:
                return _invalid("FILE_TOO_BIG", 413)
            return web.Response(
                body=body, status=response.status,
                content_type=response.content_type or ("application/octet-stream" if file_response else "application/json"),
                headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
            )
    except (ClientError, TimeoutError, OSError):
        return _invalid("NATIVE_BOT_API_UNAVAILABLE", 503)


async def method(request: web.Request):
    name = request.match_info.get("method", "")
    if not _VALID_METHOD.fullmatch(name):
        return _invalid("METHOD_NOT_FOUND", 404)
    token = request.match_info["token"]
    return await _forward(request, f"{_NATIVE_BASE}/bot{token}/{name}")


async def file_download(request: web.Request):
    path = request.match_info.get("file_path", "")
    if not path or path.startswith("/") or ".." in path.split("/") or not _VALID_FILE_PATH.fullmatch(path):
        return _invalid("FILE_PATH_INVALID", 400)
    token = request.match_info["token"]
    return await _forward(request, f"{_NATIVE_BASE}/file/bot{token}/{path}", file_response=True)


async def install(app: web.Application):
    app["native_bot_api_session"] = ClientSession(
        timeout=ClientTimeout(total=65), trust_env=False,
    )


async def cleanup(app: web.Application):
    await app["native_bot_api_session"].close()


def routes():
    return [
        web.get("/bot{token}/{method}", method),
        web.post("/bot{token}/{method}", method),
        web.get("/file/bot{token}/{file_path:.*}", file_download),
    ]
