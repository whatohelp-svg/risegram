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
# New collections can have a complete collectible pool before the older
# pinned artwork archive catches up. Use their exact original gift artwork.
POOL_IDS = json.loads((Path(__file__).parent / "data/pools/names.json").read_text())
import re
for source_id, title in POOL_IDS.items():
    slug = re.sub(r"[^a-z0-9]", "", title.lower())
    ASSETS.setdefault(slug, [f"originals/{source_id}/Original.tgs", True])

log = logging.getLogger("risegram.native_drops")
_MISSING_ASSET_WARNED = set()


async def asset(session, path):
    url = ("https://api.changes.tg/original/" + path.split("/")[1] + ".tgs"
           if path.startswith("originals/") else SOURCE + quote(path))
    async with session.get(url) as response:
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
    if row["gift_id"] not in ASSETS:
        # Missing source artwork cannot be replaced with an unrelated NFT gift.
        # Report it once per process; the drop remains configured but unpublished.
        if row["gift_id"] not in _MISSING_ASSET_WARNED:
            log.warning("Drop %s awaits exact animation asset for %s; not published",
                        row["id"], row["gift_id"])
            _MISSING_ASSET_WARNED.add(row["gift_id"])
        return
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
    craft_collections = {"deskcalendar", "jinglebells", "mythmask", "resistancedog"}
    needs_craft_pool = row["gift_id"] in craft_collections and not state.get("craft_pool_v1")
    if row["can_upgrade"] and ASSETS[row["gift_id"]][1] and (not state.get("full_pool") or needs_craft_pool):
        fields, files = await pool_material(assets, row["gift_id"])
        # The pre-migration Postgres volume cached failed/stuck receipts for
        # drops 27-31. All five have a gift catalog row but lacked a collectible
        # revision at the time of verification. Retry using a new deterministic
        # key; do not change keys for previously successful earlier drops.
        has_crafted = any(model.get("crafted") for model in fields["models"])
        if needs_craft_pool and not has_crafted:
            raise ValueError("Craft catalog temporarily missing; retaining previous pool")
        pool_action = ("craft-pool-v1" if has_crafted else
                       ("full-pool-v2" if int(row["id"]) in (27, 28, 29, 30, 31) else "full-pool-v1"))
        endpoint = f"/v1/gifts/{state['id']}/collectibles/publish"
        payload = {**fields, "upgrade_stars": 25, "supply_total": row["supply"], "slug_prefix": f"rise{row['id']}"}
        if has_crafted:
            # Older MTProto servers ignore the crafted fields; validate against
            # the actual deployed API before sending a state-changing command.
            preview = await apply_command(session, key, endpoint, pool_action + "-preview",
                                          dict(payload, dry_run=True), files)
            expected = {m["name"]: m["rarity_kind"] for m in fields["models"] if m.get("crafted")}
            actual = {m["name"]: m.get("rarity_kind") for m in preview.get("models", []) if m.get("crafted")}
            if actual != expected:
                raise ValueError("Waiting for MTProto crafted-model API rollout")
        await apply_command(session, key, endpoint, pool_action, payload, files)
        state["full_pool"] = True
        if has_crafted:
            state["craft_pool_v1"] = True
        await db.set_meta(key, json.dumps(state))
        log.info("Full pool published for drop %s: %d models, %d patterns, %d backdrops",
                 row["id"], len(fields["models"]), len(fields["patterns"]), len(fields["backdrops"]))
    # Receipts issued before a collectible pool was published retain the old
    # message action. Repair only active, unconverted user gifts for these
    # two explicitly requested drops, after the real pool is available.
    if row["id"] in (25, 38) and row["gift_id"] == "resistancedog" and state.get("craft_pool_v1") and not state.get("upgrade_receipts_v1"):
        import native_economy
        async with native_economy.transaction() as conn:
            targets = """SELECT p.owner_peer_id,p.msg_id,b.private_message_id,r.upgrade_stars
                FROM peer_star_gifts p JOIN message_boxes b ON b.owner_user_id=p.owner_peer_id AND b.box_id=p.msg_id
                JOIN star_gift_catalog c ON c.gift_id=p.gift_id
                JOIN star_gift_collectible_revisions r ON r.id=c.collectible_revision_id
                WHERE p.gift_id=%s AND p.owner_peer_type='user' AND p.lifecycle_status='active'
                  AND p.unique_gift_id IS NULL AND NOT p.converted AND r.status='published'
                  AND r.upgrade_stars>0 AND r.issued<r.supply_total
                  AND b.media#>>'{service_action,kind}'='star_gift'
                  AND b.media#>>'{service_action,star_gift,gift_id}'=p.gift_id::text"""
            for table, predicate in (
                ("private_messages", "m.id=t.private_message_id"),
                ("message_boxes", "(m.private_message_id=t.private_message_id OR (m.owner_user_id=t.owner_peer_id AND m.box_id=t.msg_id))"),
            ):
                await conn.execute(f"""WITH targets AS ({targets})
                    UPDATE {table} m SET media=jsonb_set(jsonb_set(m.media,'{{service_action,star_gift,can_upgrade}}','true'::jsonb),
                    '{{service_action,star_gift,upgrade_price_stars}}',to_jsonb(t.upgrade_stars))
                    FROM targets t WHERE {predicate}
                    AND m.media#>>'{{service_action,star_gift,gift_id}}'=%s
                    AND m.media#>>'{{service_action,kind}}'='star_gift'""", (state["id"], str(state["id"])))
        state["upgrade_receipts_v1"] = True
        await db.set_meta(key, json.dumps(state))
        log.info("Repaired pre-publication upgrade receipts for Resistance Dog drop %s", row["id"])
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
    # Owner explicitly reported that upgrade was selected for drop 38, but
    # the old catalog lacked the Resistance Dog source and silently cleared it.
    # Repair only this confirmed drop; never alter unrelated drop policies.
    def repair_resistance_dog():
        row = drops.get_admin_drop(38)
        if row and row["gift_id"] == "resistancedog" and row["supply"] == 5 and row["price_stars"] == 2147483647:
            return drops.enable_upgrade(38)
        return False

    if await db.get_meta("owner-confirmed-drop38-upgrade-v1") != "done":
        changed = await asyncio.to_thread(repair_resistance_dog)
        await db.set_meta("owner-confirmed-drop38-upgrade-v1", "done")
        if changed:
            log.info("Restored owner-selected upgrade policy for Resistance Dog drop 38")

    # Owner requested upgrade support for Resistance Dog, including the older
    # 25-copy drop created before its original asset was available.
    def repair_older_resistance_dog():
        row = drops.get_admin_drop(25)
        if row and row["gift_id"] == "resistancedog" and row["supply"] == 25 and row["price_stars"] == 5000:
            return drops.enable_upgrade(25)
        return False

    if await db.get_meta("owner-confirmed-drop25-upgrade-v1") != "done":
        changed = await asyncio.to_thread(repair_older_resistance_dog)
        await db.set_meta("owner-confirmed-drop25-upgrade-v1", "done")
        if changed:
            log.info("Restored upgrade policy for Resistance Dog drop 25")

    # Previous gift restoration and verifier repair jobs were single-use.
    # Never replay administrative mutations on an ordinary app restart.
    timeout = ClientTimeout(total=240)
    async with ClientSession(timeout=timeout, trust_env=True) as assets, ClientSession(timeout=timeout, headers={
        "Authorization": "Bearer " + config.MTPROTO_ADMIN_API_TOKEN,
    }) as session:
        while True:
            storage_full = False
            for row in await asyncio.to_thread(drops.list_admin_drops, -1):
                try:
                    await publish(session, assets, row)
                except Exception as exc:
                    detail = str(exc)
                    if "no space left on device" in detail.lower() or "sqlstate 53100" in detail.lower():
                        # The PostgreSQL volume is full. Retrying every drop every
                        # 15 seconds floods the DB and cannot repair the disk.
                        log.error(
                            "Gift publishing paused for 60 seconds: PostgreSQL volume full "
                            "(drop %s). Expand the existing Railway Postgres volume.",
                            row["id"],
                        )
                        storage_full = True
                        break
                    log.warning("Drop %s publication pending: %s", row["id"], detail[:200])
            if storage_full:
                await asyncio.sleep(60)
                continue
            try:
                # Only remove sold-out limited catalog offers. Gift revisions
                # and purchased collectibles remain available to their owners.
                import native_economy
                async with native_economy.transaction() as conn:
                    await conn.execute("""UPDATE star_gift_catalog g SET enabled=false,updated_at=now()
                        FROM star_gift_catalog_revisions r WHERE r.id=g.active_revision_id
                        AND g.enabled AND r.limited AND g.availability_remains=0""")
            except Exception:
                log.exception("Sold-out gift visibility update failed")
            try:
                await refresh_private_editions(session, assets)
            except Exception as exc:
                log.warning("Legacy collectible pool pending: %s", str(exc)[:200])
            await asyncio.sleep(15)
