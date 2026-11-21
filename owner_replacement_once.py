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
PREFIX = "operator-20261121-variety150"


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
    await remove_previous(uid)

async def remove_previous(uid):
    async with native_economy.transaction() as conn:
        await conn.execute("""CREATE TABLE IF NOT EXISTS risegram_collectible_cleanup_receipts
            (job text PRIMARY KEY, details jsonb NOT NULL)""")
        await conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (PREFIX,))
        receipt = await (await conn.execute("SELECT details FROM risegram_collectible_cleanup_receipts WHERE job=%s",
                                            (PREFIX,))).fetchone()
        if receipt:
            log.info("Collectible cleanup already verified: %s", receipt["details"])
            return
        cmds = ["operator-20261121-random150-" + s + "-create" for s in ("plushpepe","signetring","preciouspeach","jellybunny")]
        old = await (await conn.execute("SELECT DISTINCT gift_id FROM star_gift_catalog_revisions WHERE command_id=ANY(%s)",(cmds,))).fetchall()
        if len(old)!=4:
            raise ValueError("Could not identify all four prior editions")
        editions = [r["gift_id"] for r in old]
        rows = await (await conn.execute("""SELECT u.id, p.id AS saved, u.owner_peer_id FROM unique_star_gifts u
            JOIN peer_star_gifts p ON p.unique_gift_id=u.id AND p.lifecycle_status='active'
            AND p.owner_peer_type=u.owner_peer_type AND p.owner_peer_id=u.owner_peer_id
            WHERE u.owner_peer_type='user' AND u.owner_peer_id=%s AND NOT u.burned AND u.owner_address=''
            AND u.gift_id=ANY(%s) ORDER BY u.id FOR UPDATE OF u,p""", (uid, editions))).fetchall()
        ids = [r["id"] for r in rows]
        saved = [r["saved"] for r in rows]
        if len(ids)>150:
            raise ValueError("Previous batch exceeds 150 items")
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
        totals = {"removed_from_owner":len(ids)}
        await conn.execute("INSERT INTO risegram_collectible_cleanup_receipts VALUES (%s,%s::jsonb)",
                           (PREFIX,json.dumps(totals)))
    log.info("Revoked previous four-edition batch %s",totals)



async def run():
    while not await db.get_meta(PREFIX+"-revoked"):
        try:
            await deliver()
            await db.set_meta(PREFIX+"-revoked","1")
        except Exception as exc:
            log.warning("Replacement pending: %s",str(exc)[:240])
            await asyncio.sleep(40)
