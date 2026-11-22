"""Authenticated media storage for the RiseGram HTTP API."""
from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import uuid
from pathlib import Path
from aiohttp import web

import config
import database as db

log = logging.getLogger("risegram.media")

MAX_MEDIA_BYTES = 25 * 1024 * 1024
MAX_MEDIA_PER_USER = 250 * 1024 * 1024
MEDIA_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "video/mp4": ".mp4",
    "video/webm": ".webm",
    "video/quicktime": ".mov",
    "audio/mpeg": ".mp3",
    "audio/ogg": ".ogg",
    "audio/opus": ".opus",
    "audio/wav": ".wav",
    "audio/mp4": ".m4a",
    "application/pdf": ".pdf",
    "text/plain": ".txt",
    "application/zip": ".zip",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
}


def public_media(row: dict) -> dict:
    return {
        "id": row["id"],
        "name": row["original_name"],
        "mime_type": row["mime_type"],
        "size": int(row["byte_size"]),
        "url": f"/api/media/{row['id']}",
    }


def create_media_handlers(current_user):
    async def upload(request: web.Request):
        user = await current_user(request)
        if not user:
            return web.json_response({"ok": False, "error": "unauthorized"}, status=401)

        used = await db.scalar(
            "SELECT COALESCE(SUM(byte_size),0) FROM rg_media WHERE uploader_id=?",
            (user["tg_id"],), 0,
        )
        if int(used or 0) >= MAX_MEDIA_PER_USER:
            return web.json_response({"ok": False, "error": "storage_quota_exceeded"}, status=413)

        try:
            reader = await request.multipart()
            part = await reader.next()
            while part and part.name != "file":
                part = await reader.next()
            if not part or not part.filename:
                return web.json_response({"ok": False, "error": "file_required"}, status=400)

            mime_type = (part.headers.get("Content-Type") or part.content_type or "").split(";")[0].lower()
            extension = MEDIA_TYPES.get(mime_type)
            if not extension:
                return web.json_response({"ok": False, "error": "unsupported_file_type"}, status=415)

            supplied_name = part.filename.replace(chr(92), "/").split("/")[-1]
            original_name = re.sub(r"[^A-Za-z0-9._-]", "_", supplied_name)[:120].strip("._") or "upload"
            chunks = []
            total = 0
            digest = hashlib.sha256()
            while True:
                chunk = await part.read_chunk(size=64 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_MEDIA_BYTES:
                    return web.json_response(
                        {"ok": False, "error": "file_too_large", "max_bytes": MAX_MEDIA_BYTES},
                        status=413,
                    )
                chunks.append(chunk)
                digest.update(chunk)
            if total == 0:
                return web.json_response({"ok": False, "error": "empty_file"}, status=400)
        except (ValueError, RuntimeError):
            return web.json_response({"ok": False, "error": "invalid_multipart"}, status=400)

        media_id = uuid.uuid4().hex
        storage_name = media_id + extension
        directory = Path(config.MEDIA_PATH)
        destination = directory / storage_name
        try:
            await asyncio.to_thread(directory.mkdir, parents=True, exist_ok=True)
            await asyncio.to_thread(destination.write_bytes, b"".join(chunks))
            await db.execute(
                """INSERT INTO rg_media
                   (id,uploader_id,storage_name,original_name,mime_type,byte_size,sha256,created_at)
                   VALUES(?,?,?,?,?,?,?,?)""",
                (media_id, user["tg_id"], storage_name, original_name, mime_type,
                 total, digest.hexdigest(), db.now()),
            )
        except Exception:
            try:
                await asyncio.to_thread(destination.unlink, missing_ok=True)
            except OSError:
                pass
            log.exception("media upload failed for Rise ID %s", user.get("rise_id"))
            return web.json_response({"ok": False, "error": "media_storage_failed"}, status=500)

        media = {
            "id": media_id,
            "original_name": original_name,
            "mime_type": mime_type,
            "byte_size": total,
        }
        return web.json_response({"ok": True, "media": public_media(media)})

    async def download(request: web.Request):
        user = await current_user(request)
        if not user:
            return web.json_response({"ok": False, "error": "unauthorized"}, status=401)

        media_id = request.match_info["media_id"]
        media = await db.fetchone("SELECT * FROM rg_media WHERE id=?", (media_id,))
        if not media:
            return web.json_response({"ok": False, "error": "media_not_found"}, status=404)

        account_id = int(user["tg_id"])
        allowed = int(media["uploader_id"]) == account_id
        if not allowed:
            allowed = bool(await db.scalar(
                """SELECT 1 FROM rg_message_media mm
                   JOIN messages m ON m.id=mm.message_id
                   WHERE mm.media_id=? AND (m.sender_id=? OR m.recipient_id=?) LIMIT 1""",
                (media_id, account_id, account_id), 0,
            ))
        if not allowed:
            allowed = bool(await db.scalar(
                """SELECT 1 FROM rg_space_message_media smm
                   JOIN rg_space_messages sm ON sm.id=smm.message_id
                   JOIN rg_space_members mem ON mem.space_id=sm.space_id
                   WHERE smm.media_id=? AND mem.user_id=? LIMIT 1""",
                (media_id, account_id), 0,
            ))
        if not allowed:
            return web.json_response({"ok": False, "error": "media_forbidden"}, status=403)

        path = Path(config.MEDIA_PATH) / media["storage_name"]
        if not path.is_file():
            return web.json_response({"ok": False, "error": "media_file_missing"}, status=404)
        return web.FileResponse(path, headers={
            "Content-Type": media["mime_type"],
            "Content-Disposition": f'attachment; filename="{media["original_name"]}"',
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
        })

    return upload, download
