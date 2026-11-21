"""One-time delivery for the November 21 operator request."""
import asyncio
import logging
from pathlib import Path

from aiohttp import ClientSession, ClientTimeout, FormData
import config
import database as db
import native_economy
from native_drops import asset
import json

log = logging.getLogger("risegram.delivery")
PREFIX = "operator-20261121-black"


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
    timeout = ClientTimeout(total=40)
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

        for slug,title,model,file,qty,rarity,alternative in (
            ("plushpepe","Plush Pepe","Toading...","models/plush_pepe/toading....tgs",3,0,None),
            ("signetring","Signet Ring","Skibidi","models/signet_ring/skibidi.tgs",4,3,"models/signet_ring/hearts.tgs")):
            key = PREFIX+"-"+slug
            if await db.get_meta(key+":done"):
                continue
            # Use a support-only edition so this delivery cannot consume a public drop.
            base = "plush_pepe" if slug=="plushpepe" else "signet_ring"
            entry = await command("/v1/gifts/import",slug+"-base",{
                "title":title,"stars":2000 if slug=="plushpepe" else 150,"convert_stars":0,
                "support_only":True,"enabled":True,"limited":True,"availability_total":qty},
                {"file":await asset(assets,f"tgs/by_name/{base}.tgs")})
            gid = int(entry["gift_id"])
            files = {"model":await asset(assets,file),"pattern":Path("data/drop-star.tgs").read_bytes()}
            models = [{"name":model,"file_key":"model","rarity_permille":rarity or 1000}]
            if alternative:
                files["alternate"]=await asset(assets,alternative)
                models.append({"name":"Hearts","file_key":"alternate","rarity_permille":1000-rarity})
            await command(f"/v1/gifts/{gid}/collectibles/publish",slug+"-pool",{
                "upgrade_stars":25,"supply_total":qty,"slug_prefix":"rgblack"+slug,
                "models":models,"patterns":[{"name":"Rise Star","file_key":"pattern","rarity_permille":1000}],
                "backdrops":[{"name":"Black","backdrop_id":1,"center_color":0x141414,"edge_color":0,
                    "pattern_color":0x262626,"text_color":0xffffff,"rarity_permille":500},
                    {"name":"Silver","backdrop_id":2,"center_color":0xb0b0b0,"edge_color":0x797979,
                    "pattern_color":0xaaaaaa,"text_color":0xffffff,"rarity_permille":500}]}, files)
            async with api.get(f"{config.MTPROTO_ADMIN_API_URL}/v1/gifts/{gid}/collectibles") as resp:
                resp.raise_for_status()
                preview = await resp.json()
            mid = int(next(x["id"] for x in preview["models"] if x["name"]==model))
            bid = int(next(x["id"] for x in preview["backdrops"] if x["name"]=="Black"))
            for i in range(qty):
                await command("/v1/gifts/give",f"{slug}-{i}",dict(user_id=uid,gift_id=gid,
                              upgrade=True,model_attribute_id=mid,backdrop_attribute_id=bid))
            async with native_economy.transaction() as conn:
                rows = await (await conn.execute("""SELECT slug FROM unique_star_gifts WHERE gift_id=%s
                  AND owner_peer_type='user' AND owner_peer_id=%s AND model_attribute_id=%s AND backdrop_attribute_id=%s ORDER BY num""",
                  (gid,uid,mid,bid))).fetchall()
            if len(rows)!=qty:
                raise ValueError("delivery count mismatch")
            await command("/v1/gifts/set-enabled",slug+"-close",{"gift_id":gid,"enabled":False})
            await db.set_meta(key+":done","1")
            log.info("Collectible delivery verified %s", ",".join(x["slug"] for x in rows))


async def run():
    for delay in (0,10,30,60,120):
        await asyncio.sleep(delay)
        try:
            await deliver()
            return
        except Exception as exc:
            log.warning("Collectible delivery pending: %s", str(exc)[:240])
