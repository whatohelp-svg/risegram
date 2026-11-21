"""One-time delivery for the November 21 operator request."""
import asyncio
import logging
from collectible_pools import pool_material
from native_drops import ASSETS

from aiohttp import ClientSession, ClientTimeout, FormData
import config
import database as db
import native_economy
from native_drops import asset
import json

log = logging.getLogger("risegram.delivery")
PREFIX = "operator-20261121-random150"


async def deliver():
    async with native_economy.transaction() as conn:
        peers = await (await conn.execute("""SELECT peer_id FROM peer_usernames
            WHERE username_lower IN ('koyyzz','owner') AND peer_type='user'""")).fetchall()
        if len(peers) != 2 or peers[0]["peer_id"] != peers[1]["peer_id"]:
            raise ValueError("recipient aliases do not match")
        uid = peers[0]["peer_id"]
    owner = await db.get_user(config.OWNER_ID)
    if await native_economy.account(owner) != uid:
        raise ValueError("recipient does not match owner")
    timeout = ClientTimeout(total=240)
    async with ClientSession(timeout=timeout) as assets, ClientSession(timeout=timeout, headers={
        "Authorization":"Bearer "+config.MTPROTO_ADMIN_API_TOKEN}) as api:
        async def command(path, key, fields, files=None):
            body = dict(command_id=PREFIX+"-"+key, actor="risegram-owner",
                        reason="Requested collectible delivery", **fields)
            args = {"json":body}
            if files:
                form = FormData()
                form.add_field("metadata",json.dumps(body))
                for name,data in files.items():
                    form.add_field(name,data,filename=name+".tgs",content_type="application/x-tgsticker")
                args = {"data":form}
            async with api.post(config.MTPROTO_ADMIN_API_URL+path, **args) as res:
                data = await res.json()
                if res.status != 200 or data.get("status") != "completed":
                    raise ValueError(f"{key}: {str(data.get('error') or res.status)[:200]}")
                return data["details"]

        editions = []
        for slug, title, qty in (("plushpepe", "Plush Pepe", 38), ("signetring", "Signet Ring", 38),
                                 ("preciouspeach", "Precious Peach", 37), ("jellybunny", "Jelly Bunny", 37)):
            key = PREFIX + "-" + slug
            gid = await db.get_meta(key + "-catalog")
            if not gid:
                art = await asset(assets, ASSETS[slug][0])
                base = await command("/v1/gifts/import", slug+"-create", dict(title=title, stars=150,
                                     convert_stars=0, enabled=True, support_only=True, limited=True,
                                     availability_total=qty), {"file": art})
                gid = str(base["gift_id"])
                await db.set_meta(key + "-catalog", gid)
            gid = int(gid)
            editions.append(gid)
            if await db.get_meta(key + "-done"):
                continue
            fields, files = await pool_material(assets, slug)
            if not await db.get_meta(key + "-pool"):
                await command(f"/v1/gifts/{gid}/collectibles/publish", slug+"-pool", dict(
                    **fields, upgrade_stars=25, supply_total=qty, slug_prefix="rgnov"+slug), files)
                await db.set_meta(key+"-pool","1")
            for index in range(qty):
                await command("/v1/gifts/give", f"{slug}-grant-{index}", dict(gift_id=gid, user_id=uid, upgrade=True))
            async with native_economy.transaction() as conn:
                count = await (await conn.execute("""SELECT count(*) AS n FROM unique_star_gifts
                    WHERE gift_id=%s AND owner_peer_type='user' AND owner_peer_id=%s AND NOT burned""",
                    (gid, uid))).fetchone()
            if count["n"] != qty:
                raise ValueError(f"Delivery mismatch for {title}: {count['n']}/{qty}")
            await command("/v1/gifts/set-enabled",slug+"-close",dict(gift_id=gid,enabled=False))
            await db.set_meta(key+"-done","1")
            log.info("Verified %s collectibles for owner: %d", title, qty)
        await cleanup(uid, editions)


async def cleanup(uid, editions):
    async with native_economy.transaction() as conn:
        await conn.execute("""CREATE TABLE IF NOT EXISTS risegram_collectible_cleanup_receipts
            (job text PRIMARY KEY, details jsonb NOT NULL)""")
        await conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (PREFIX,))
        receipt = await (await conn.execute("SELECT details FROM risegram_collectible_cleanup_receipts WHERE job=%s",
                                            (PREFIX,))).fetchone()
        if receipt:
            log.info("Collectible cleanup already verified: %s", receipt["details"])
            return
        old = await (await conn.execute("""SELECT DISTINCT gift_id FROM star_gift_catalog_revisions
            WHERE command_id IN ('operator-20261121-black-plushpepe-base','operator-20261121-black-signetring-base')""")).fetchall()
        tester = await (await conn.execute("""SELECT peer_id FROM peer_usernames WHERE peer_type='user'
            AND username_lower='tester'""")).fetchone()
        if len(old)!=2 or not tester or tester["peer_id"] == uid:
            raise ValueError("Could not verify the two recipients and previous editions")
        protected = editions + [r["gift_id"] for r in old]
        peers = [uid, tester["peer_id"]]
        rows = await (await conn.execute("""SELECT u.id, p.id AS saved, u.owner_peer_id FROM unique_star_gifts u
            JOIN peer_star_gifts p ON p.unique_gift_id=u.id AND p.lifecycle_status='active'
            AND p.owner_peer_type=u.owner_peer_type AND p.owner_peer_id=u.owner_peer_id
            WHERE u.owner_peer_type='user' AND u.owner_peer_id=ANY(%s) AND NOT u.burned AND u.owner_address=''
            AND NOT (u.gift_id=ANY(%s)) ORDER BY u.id FOR UPDATE OF u,p""", (peers, protected))).fetchall()
        ids = [r["id"] for r in rows]
        saved = [r["saved"] for r in rows]
        if ids:
            offers = await (await conn.execute("""SELECT count(*) AS n FROM star_gift_offers
                WHERE unique_gift_id=ANY(%s) AND status='pending'""",(ids,))).fetchone()
            if offers["n"]:
                raise ValueError("Pending funded offer on a selected gift")
            await conn.execute("DELETE FROM star_gift_listings WHERE unique_gift_id=ANY(%s)", (ids,))
            await conn.execute("DELETE FROM star_gift_collection_items WHERE saved_gift_id=ANY(%s)", (saved,))
            await conn.execute("""UPDATE unique_star_gifts SET burned=true,craft_chance_permille=0,
                offer_min_stars=0,updated_at=now() WHERE id=ANY(%s)""",(ids,))
            await conn.execute("""UPDATE peer_star_gifts SET lifecycle_status='burned',unsaved=true,pinned_order=0,
                transfer_stars=0,can_export_at=0,can_transfer_at=0,can_resell_at=0,
                drop_original_details_stars=0,can_craft_at=0 WHERE id=ANY(%s)""",(saved,))
        totals = {label: sum(r["owner_peer_id"]==peer for r in rows) for label,peer in zip(("owner","tester"),peers)}
        await conn.execute("INSERT INTO risegram_collectible_cleanup_receipts VALUES (%s,%s::jsonb)",
                           (PREFIX,json.dumps(totals)))
    log.info("Verified delivery of 150 collectibles; cleanup counts %s",totals)


async def run():
    while not await db.get_meta(PREFIX+"-complete"):
        try:
            await deliver()
            await db.set_meta(PREFIX+"-complete","1")
        except Exception as exc:
            log.warning("Collectible delivery pending: %s",str(exc)[:240])
            await asyncio.sleep(60)
