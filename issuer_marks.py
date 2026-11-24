"""Publish the issuer marks used by native RiseGram profiles."""
import asyncio
import json
import logging
from pathlib import Path

from aiohttp import ClientSession, ClientTimeout, FormData
import config
import database as db

log = logging.getLogger("risegram.verification")
ISSUERS = {"risegram": "RiseGram", "major": "Major", "risegramclub": "RiseGram Club"}
_ready = asyncio.Event()


async def command(session, path, key, fields, file=None):
    body = dict(command_id=key, actor="risegram-verification", reason="Configure RiseGram issuer marks", **fields)
    if file:
        form = FormData()
        form.add_field("metadata", json.dumps(body))
        form.add_field("file", file.read_bytes(), filename=file.name, content_type="application/x-tgsticker" if file.suffix == ".tgs" else "image/webp")
        options = {"data": form}
    else:
        options = {"json": body}
    async with session.post(config.MTPROTO_ADMIN_API_URL + path, **options) as response:
        result = await response.json()
        if response.status != 200 or result.get("status") != "completed":
            raise ValueError(f"{path}: {result.get('code') or result.get('error') or response.status}")
        return result.get("details", {})


async def install():
    owner = await db.get_user(config.OWNER_ID)
    if not owner or not config.MTPROTO_ADMIN_API_TOKEN:
        return
    async with ClientSession(headers={"Authorization": "Bearer " + config.MTPROTO_ADMIN_API_TOKEN},
                             timeout=ClientTimeout(total=10)) as session:
        async with session.post(config.MTPROTO_ADMIN_API_URL + "/v1/accounts/resolve-by-phone",
                                json={"phone": owner["phone"]}) as response:
            response.raise_for_status()
            peer = await response.json()
        if not peer.get("found"):
            return
        for slug, label in ISSUERS.items():
            prefix = f"rg-issuer-v1-{slug}"
            saved_bot = await db.get_meta("native_issuer:" + slug)
            if saved_bot:
                bot_id = int(saved_bot)
            else:
                bot = await command(session, "/v1/bots/create", prefix + "-bot", {
                    "owner_user_id": int(peer["user_id"]), "name": f"{label} · Верификация RiseGram",
                    "username": f"rg_{slug}_verification_bot",
                })
                bot_id = int(bot["bot_user_id"])
            pack = await command(session, "/v1/stickers/create", prefix + "-emoji", {
                "title": f"{label} Verification", "short_name": f"rg_{slug}_verification",
                "kind": "emoji", "emoji": "☑️",
            }, Path(__file__).parent / "data" / f"{'risegram' if slug == 'risegramclub' else slug}-issuer.webp")
            pack_set_id = pack["set_id"]
            if "document_id" not in pack:
                # Older servers returned only the set ID when creating a pack.
                pack = await command(session, "/v1/stickers/add", prefix + "-material",
                                     {"set_id": str(pack["set_id"]), "emoji": "☑️"},
                                     Path(__file__).parent / "data" / f"{'risegram' if slug == 'risegramclub' else slug}-issuer.webp")
            doc = int(pack["document_id"])
            await command(session, "/v1/botverification/icons/upsert", prefix + "-icon", {
                "document_id": doc, "name": label, "owner_bot_id": bot_id,
            })
            await command(session, "/v1/botverification/verifiers/grant", prefix + "-grant", {
                "bot_id": bot_id, "icon_document_id": doc, "company_name": label,
                "can_modify_custom_description": True,
                "default_description": ("Платная косметическая метка RiseGram Club, не официальная верификация." if slug == "risegramclub" else f"Аккаунт верифицирован организацией «{label}»."),
            })
            # Native profile badges use animated custom emoji documents.
            asset = Path(__file__).parent / "data" / f"{'risegram' if slug == 'risegramclub' else slug}-issuer.tgs"
            material = await command(session, "/v1/stickers/add", prefix + "-animated-material",
                                     {"set_id": str(pack_set_id), "emoji": "☑️"}, asset)
            animated_id = int(material["document_id"])
            await command(session, "/v1/botverification/icons/upsert", prefix + "-animated-icon",
                          {"document_id": animated_id, "name": label, "owner_bot_id": bot_id})
            async with session.get(config.MTPROTO_ADMIN_API_URL + "/v1/botverification/verifiers",
                                   params={"limit":"200"}) as response:
                response.raise_for_status()
                rows = (await response.json())["rows"]
            current = next(v for v in rows if int(v["BotID"]) == bot_id)
            if int(current["IconDocumentID"]) != animated_id:
                await command(session, "/v1/botverification/verifiers/grant",
                              prefix + "-animated-grant-" + str(current["Version"]), {
                    "version": int(current["Version"]), "bot_id": bot_id, "icon_document_id": animated_id,
                    "company_name": label, "can_modify_custom_description": True,
                    "default_description": ("Платная косметическая метка RiseGram Club, не официальная верификация." if slug == "risegramclub" else f"Аккаунт верифицирован организацией «{label}»."),
                })
            await db.set_meta("native_issuer:" + slug, str(bot_id))
            log.info("Native issuer ready: %s", label)
    _ready.set()
    from api import sync_mtproto_verification
    for row in await db.fetchall("SELECT tg_id FROM users WHERE banned=0 AND (verified_risegram=1 OR verified_major=1)"):
        await sync_mtproto_verification(row["tg_id"], force=True)


async def bootstrap():
    for delay in (0, 10, 30, 60, 120):
        await asyncio.sleep(delay)
        try:
            await install()
            if _ready.is_set():
                return
        except Exception as exc:
            log.warning("Issuer setup pending (%s)", str(exc)[:180] if isinstance(exc, (KeyError, ValueError)) else type(exc).__name__)
