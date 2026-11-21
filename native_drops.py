"""Publish bot-managed drops into the app's Stars catalog."""
import asyncio
import json
import logging
from pathlib import Path
from urllib.parse import quote

from aiohttp import ClientSession, ClientTimeout, FormData
import config
import database as db
import gift_drops as drops

ASSETS = json.loads((Path(__file__).parent / "data/drop_assets.json").read_text())
SOURCE = "https://raw.githubusercontent.com/ssamy2/TelegramGiftsAssests/1eb0666fff81b63f9002004084ff0ee3a9eed10f/"
log = logging.getLogger("risegram.native_drops")


async def asset(session, path):
    async with session.get(SOURCE + quote(path)) as response:
        response.raise_for_status()
        data = await response.read()
    if not 0 < len(data) <= 512 * 1024:
        raise ValueError("unexpected gift animation size")
    return data


async def publish(session, assets, row):
    key = f"rg-app-drop-{row['id']}"
    base = config.MTPROTO_ADMIN_API_URL
    state = json.loads(await db.get_meta(key) or "{}")
    enabled = bool(row["enabled"] and row["starts_at"] <= drops.utc_now()
                   and (not row["ends_at"] or row["ends_at"] > drops.utc_now()))
    stock = row["supply"] - row["sold"]
    if not stock or not state and not enabled:
        return

    async def apply(path, action, fields, files=None):
        body = dict(command_id=f"{key}-{action}", actor="risegram-drops",
                    reason="Publish administrator's app gift drop", **fields)
        args = {"json": body}
        if files:
            form = FormData()
            form.add_field("metadata", json.dumps(body))
            for name, data in files.items():
                form.add_field(name, data, filename=name+".tgs", content_type="application/x-tgsticker")
            args = {"data": form}
        async with session.post(base + path, **args) as response:
            result = await response.json()
            if response.status != 200 or result.get("status") != "completed":
                raise ValueError(str(result.get("error") or response.status)[:200])
            return result["details"]

    if "id" not in state:
        art = await asset(assets, ASSETS[row["gift_id"]][0])
        result = await apply("/v1/gifts/import", "create", {
            "title": row["title"], "stars": row["price_stars"], "convert_stars": 0,
            "availability_total": stock, "limited": True, "enabled": False, "sort_order": -row["id"],
        }, {"file": art})
        state = {"id": int(result["gift_id"]), "enabled": False, "version": 0}
        await db.set_meta(key, json.dumps(state))
    if enabled and row["can_upgrade"] and not state.get("upgrade"):
        path = ASSETS[row["gift_id"]][1]
        model = await asset(assets, path)
        pattern = (Path(__file__).parent / "data/drop-star.tgs").read_bytes()
        await apply(f"/v1/gifts/{state['id']}/collectibles/publish", "upgrade", {
            "upgrade_stars": 1, "supply_total": stock, "slug_prefix": f"rise{row['id']}",
            "models": [{"name": Path(path).stem.replace("_"," ").title(), "file_key":"model","rarity_permille":1000}],
            "patterns": [{"name":"Rise Star","file_key":"pattern","rarity_permille":1000}],
            "backdrops": [{"name":"Midnight","backdrop_id":1,"center_color":3556758,"edge_color":1186089,
                          "pattern_color":5606092,"text_color":16777215,"rarity_permille":1000}],
        }, {"model":model,"pattern":pattern})
        state["upgrade"] = True
        await db.set_meta(key, json.dumps(state))
    if state["enabled"] != enabled:
        version = state["version"] + 1
        await apply("/v1/gifts/set-enabled", f"visibility-{version}", {"gift_id":state["id"], "enabled":enabled})
        state.update(enabled=enabled, version=version)
        await db.set_meta(key, json.dumps(state))
        log.info("Drop %s native gift %s enabled=%s price=%s supply=%s",
                 row["id"], state["id"], enabled, row["price_stars"], stock)


async def worker():
    if not config.MTPROTO_ADMIN_API_TOKEN:
        return
    timeout = ClientTimeout(total=20)
    async with ClientSession(timeout=timeout) as assets, ClientSession(timeout=timeout, headers={
        "Authorization": "Bearer " + config.MTPROTO_ADMIN_API_TOKEN,
    }) as session:
        while True:
            for row in await asyncio.to_thread(drops.list_admin_drops, -1):
                try:
                    await publish(session, assets, row)
                except Exception as exc:
                    log.warning("Drop %s publication pending: %s", row["id"], str(exc)[:200])
            await asyncio.sleep(15)
