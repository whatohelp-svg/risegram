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
from collectible_pools import pool_material

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


async def apply_command(session, key, path, action, fields, files=None):
    body = dict(command_id=f"{key}-{action}", actor="risegram-drops",
                reason="Publish administrator's app gift drop", **fields)
    args = {"json": body}
    if files:
        form = FormData()
        form.add_field("metadata", json.dumps(body))
        for name, data in files.items():
            form.add_field(name, data, filename=name+".tgs", content_type="application/x-tgsticker")
        args = {"data": form}
    async with session.post(config.MTPROTO_ADMIN_API_URL + path, **args) as response:
        result = await response.json()
        if response.status != 200 or result.get("status") != "completed":
            raise ValueError(str(result.get("error") or response.status)[:200])
        return result["details"]


async def refresh_private_editions(session, assets):
    import native_economy

    if await db.get_meta("legacy-gift-pools-v1") == "done":
        return
    async with native_economy.transaction() as conn:
        rows = await (await conn.execute("""SELECT c.gift_id, r.supply_total, r.slug_prefix, c.command_id
            FROM star_gift_catalog_revisions c
            JOIN star_gift_catalog g ON g.gift_id=c.gift_id
            JOIN star_gift_collectible_revisions r ON r.id=g.collectible_revision_id
            WHERE c.command_id IN ('operator-20261121-black-plushpepe-base','operator-20261121-black-signetring-base')
            """)).fetchall()
    for row in rows:
        slug = row["command_id"].split("-")[-2]
        fields, files = await pool_material(assets, slug)
        await apply_command(session, "legacy-full-"+slug, f"/v1/gifts/{row['gift_id']}/collectibles/publish",
                            "v1", dict(**fields, upgrade_stars=25, supply_total=row["supply_total"],
                                       slug_prefix=row["slug_prefix"]), files)
        log.info("Updated legacy %s pool: %d models, %d patterns, %d backdrops", slug,
                 len(fields["models"]), len(fields["patterns"]), len(fields["backdrops"]))
    await db.set_meta("legacy-gift-pools-v1", "done")


async def publish(session, assets, row):
    key = f"rg-app-drop-{row['id']}"
    state = json.loads(await db.get_meta(key) or "{}")
    enabled = bool(row["enabled"] and row["starts_at"] <= drops.utc_now()
                   and (not row["ends_at"] or row["ends_at"] > drops.utc_now()))
    stock = row["supply"] - row["sold"]
    if not state and (not enabled or not stock):
        return

    if "id" not in state:
        art = await asset(assets, ASSETS[row["gift_id"]][0])
        result = await apply_command(session, key, "/v1/gifts/import", "create", {
            "title": row["title"], "stars": row["price_stars"], "convert_stars": 0,
            "availability_total": stock, "limited": True, "enabled": False, "sort_order": -row["id"],
        }, {"file": art})
        state = {"id": int(result["gift_id"]), "enabled": False, "version": 0}
        await db.set_meta(key, json.dumps(state))
    if row["can_upgrade"] and ASSETS[row["gift_id"]][1] and not state.get("full_pool"):
        fields, files = await pool_material(assets, row["gift_id"])
        await apply_command(session, key, f"/v1/gifts/{state['id']}/collectibles/publish", "full-pool-v1", {
            **fields, "upgrade_stars": 25, "supply_total": row["supply"], "slug_prefix": f"rise{row['id']}",
        }, files)
        state["full_pool"] = True
        await db.set_meta(key, json.dumps(state))
        log.info("Full pool published for drop %s: %d models, %d patterns, %d backdrops",
                 row["id"], len(fields["models"]), len(fields["patterns"]), len(fields["backdrops"]))
    if state["enabled"] != enabled:
        version = state["version"] + 1
        await apply_command(session, key, "/v1/gifts/set-enabled", f"visibility-{version}", {"gift_id":state["id"], "enabled":enabled})
        state.update(enabled=enabled, version=version)
        await db.set_meta(key, json.dumps(state))
        log.info("Drop %s native gift %s enabled=%s price=%s supply=%s",
                 row["id"], state["id"], enabled, row["price_stars"], stock)


async def worker():
    if not config.MTPROTO_ADMIN_API_TOKEN:
        return
    # Production networking is available to this running worker, not to Railway pre-deploy jobs.
    # The gift's immutable command key and verified DB receipt make restarts idempotent.
    from inspect_pepe_once import main as issue_one_owner_pepe
    async def issue_once():
        # Repo deployments restart both app and MTProto. Wait until the native service
        # is ready and retry temporary startup/network failures without duplicate grants.
        await asyncio.sleep(25)
        for attempt in range(1, 7):
            try:
                await issue_one_owner_pepe()
                log.info("OWNER_BLUE_PEPE_ISSUANCE_CONFIRMED")
                return
            except Exception as exc:
                log.warning("OWNER_BLUE_PEPE_ISSUANCE_RETRY attempt=%d error=%s",
                            attempt, str(exc)[:160])
                if attempt == 6:
                    log.exception("OWNER_BLUE_PEPE_ISSUANCE_FAILED_FINAL")
                    return
                await asyncio.sleep(20)
    asyncio.create_task(issue_once(), name="owner-blue-pepe-20261122")
    from grant_tester_rare_pepe_once import main as grant_tester_pepe
    async def issue_tester_once():
        await asyncio.sleep(28)
        for attempt in range(1, 5):
            try:
                await grant_tester_pepe()
                log.info("TESTER_RARE_PEPE_ISSUANCE_CONFIRMED")
                return
            except Exception as exc:
                log.warning("TESTER_RARE_PEPE_ISSUANCE_RETRY attempt=%d error=%s",
                            attempt,str(exc)[:160])
                if attempt == 4:
                    log.exception("TESTER_RARE_PEPE_ISSUANCE_FAILED_FINAL")
                    return
                await asyncio.sleep(25)
    asyncio.create_task(issue_tester_once(), name="tester-rare-pepe-black-20261122")
    timeout = ClientTimeout(total=240)
    async with ClientSession(timeout=timeout, trust_env=True) as assets, ClientSession(timeout=timeout, headers={
        "Authorization": "Bearer " + config.MTPROTO_ADMIN_API_TOKEN,
    }) as session:
        while True:
            for row in await asyncio.to_thread(drops.list_admin_drops, -1):
                try:
                    await publish(session, assets, row)
                except Exception as exc:
                    log.warning("Drop %s publication pending: %s", row["id"], str(exc)[:200])
            try:
                await refresh_private_editions(session, assets)
            except Exception as exc:
                log.warning("Legacy collectible pool pending: %s", str(exc)[:200])
            await asyncio.sleep(15)
