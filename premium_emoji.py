"""Install the bundled premium emoji packs into the native catalog."""
import asyncio
import json
import logging
import zipfile
from pathlib import Path

from aiohttp import ClientSession, ClientTimeout, FormData
import config
import database as db

log = logging.getLogger("risegram.emoji")
PACKS = {"PlushPepe": "🐸", "tgmacicons": "💻", "SdmFH9KUAGAAAJCCCACF_by_e4zybot": "✨"}


async def install_pack(session, title, emoji):
    slug = title.lower()
    marker = "emoji-import:" + slug
    if await db.get_meta(marker) == "done":
        return
    with zipfile.ZipFile(Path(__file__).parent / "data/emoji" / (title + ".zip")) as bundle:
        for index, name in enumerate(sorted(bundle.namelist())):
            key = marker + ":" + name
            if saved := await db.get_meta(key):
                set_id = saved
                continue
            fields = {"set_id": set_id} if index else {"short_name": slug, "title": title, "kind": "emoji"}
            attempt = await db.get_meta(key + ":limit-retries") or "0"
            form = FormData()
            form.add_field("metadata", json.dumps(dict(command_id=key.replace(":", "-") + (f"-extended-{attempt}" if index >= 120 else ""), actor="risegram-emoji",
                           reason="Publish premium emoji pack", emoji=emoji, **fields)))
            form.add_field("file", bundle.read(name), filename=name, content_type="application/x-tgsticker")
            action = "add" if index else "create"
            async with session.post(config.MTPROTO_ADMIN_API_URL + "/v1/stickers/" + action, data=form) as response:
                result = await response.json()
                if result.get("status") != "completed":
                    reason = str(result.get("error") or result.get("code") or response.status)
                    if index >= 120 and ("too much" in reason.lower() or "too_many" in reason.lower() or "too_much" in reason.lower()):
                        await db.set_meta(key + ":limit-retries", str(int(attempt) + 1))
                    raise ValueError(reason[:150])
                details = result["details"]
                set_id = str(details["set_id"])
                if index and int(details["count"]) != index + 1:
                    raise ValueError("unexpected pack size")
            await db.set_meta(key, set_id)
        log.info("Published premium pack %s with %d emoji", slug, index + 1)
        await db.set_meta(marker, "done")


async def install_packs():
    async with ClientSession(headers={"Authorization": "Bearer " + config.MTPROTO_ADMIN_API_TOKEN},
                             timeout=ClientTimeout(total=30)) as session:
        pending = dict(PACKS)
        while pending:
            for title, emoji in list(pending.items()):
                try:
                    await install_pack(session, title, emoji)
                    del pending[title]
                except Exception as error:
                    log.warning("Emoji pack %s pending: %s", title, str(error)[:180])
            if pending:
                await asyncio.sleep(40)
