"""Idempotently mint one rare Plush Pepe gift to verified @tester in native RiseGram."""
import asyncio
import json
import os
import uuid

import psycopg
from psycopg.rows import dict_row
from aiohttp import ClientSession, ClientTimeout

from native_drops import ASSETS, apply_command, asset
from collectible_pools import pool_material

TESTER_ID = 1780243203
COMMAND_ID = "operator-20261122-tester-toading-diamonds-black-one-v1"
EDITION_KEY = "operator-20261122-tester-rarepepe-black-diamonds-edition"
MODEL = "Toading..."
PATTERN = "Diamonds"
BACKDROP = "Black"
GRANT_KEY = "admin-gift:" + COMMAND_ID

async def get_granted(conn):
    return await (await conn.execute("""
        SELECT u.id,u.gift_id,u.num,u.slug,u.burned,
               u.owner_peer_type,u.owner_peer_id,
               m.name AS model_name,pat.name AS pattern_name,b.name AS backdrop_name,
               p.lifecycle_status,p.owner_peer_type AS saved_type,p.owner_peer_id AS saved_owner
          FROM star_gift_admin_grant_commands cmd
          JOIN unique_star_gifts u ON u.id=cmd.unique_gift_id
          JOIN peer_star_gifts p ON p.id=cmd.saved_gift_id
          JOIN star_gift_collectible_models m ON m.id=u.model_attribute_id
          JOIN star_gift_collectible_patterns pat ON pat.id=u.pattern_attribute_id
          JOIN star_gift_collectible_backdrops b ON b.id=u.backdrop_attribute_id
         WHERE cmd.recipient_user_id=%s AND cmd.command_key=%s
    """,(TESTER_ID,GRANT_KEY))).fetchone()

def validate_granted(g):
    if not g or g["burned"] or g["owner_peer_type"]!="user" or g["owner_peer_id"]!=TESTER_ID or g["saved_type"]!="user" or g["saved_owner"]!=TESTER_ID or g["lifecycle_status"]!="active" or g["model_name"]!=MODEL or g["pattern_name"]!=PATTERN or g["backdrop_name"]!=BACKDROP:
        raise RuntimeError("Rare Pepe ownership or collectible attributes could not be verified")

async def main():
    async with await psycopg.AsyncConnection.connect(
        os.environ["RISEGRAM_LEDGER_DSN"],row_factory=dict_row,connect_timeout=8
    ) as conn:
        peers=await (await conn.execute(
            "SELECT peer_id,username_lower,peer_type FROM peer_usernames WHERE username_lower='tester'"
        )).fetchall()
        if len(peers)!=1 or peers[0]["peer_type"]!="user" or peers[0]["peer_id"]!=TESTER_ID:
            raise RuntimeError("Recipient @tester identity mismatch; refusing gift issuance")
        g=await get_granted(conn)
        if g:
            validate_granted(g)
            print("TESTER_RARE_PEPE_ALREADY_VERIFIED",json.dumps(dict(g),default=str),flush=True)
            return
    timeout=ClientTimeout(total=240,connect=25)
    async with ClientSession(timeout=timeout,trust_env=True) as assets, ClientSession(
        timeout=timeout,headers={"Authorization":"Bearer "+os.environ["MTPROTO_ADMIN_API_TOKEN"]}
    ) as admin:
        image=await asset(assets,ASSETS["plushpepe"][0])
        details=await apply_command(admin,EDITION_KEY,"/v1/gifts/import","create",{
            "title":"Plush Pepe", "stars":1000000000, "convert_stars":0,
            "availability_total":1,"limited":True,"enabled":False,"sort_order":-12000,
        },{"file":image})
        gift_id=int(details["gift_id"])
        print("TESTER_PEPE_EDITION_CREATED_OR_REPLAY",gift_id,flush=True)
        fields,files=await pool_material(assets,"plushpepe")
        await apply_command(admin,EDITION_KEY,f"/v1/gifts/{gift_id}/collectibles/publish","pool-v1",{
            **fields,"upgrade_stars":25,"supply_total":1,"slug_prefix":"rgtesterrarepepe",
        },files)
        print("TESTER_PEPE_POOL_PUBLISHED_OR_REPLAY",gift_id,flush=True)
        async with await psycopg.AsyncConnection.connect(
            os.environ["RISEGRAM_LEDGER_DSN"],row_factory=dict_row,connect_timeout=8
        ) as conn:
            edition=await (await conn.execute("""
                SELECT c.collectible_revision_id,c.enabled,r.supply_total,r.issued,r.status,a.title
                  FROM star_gift_catalog c
                  JOIN star_gift_catalog_revisions a ON a.id=c.active_revision_id
                  JOIN star_gift_collectible_revisions r ON r.id=c.collectible_revision_id
                 WHERE c.gift_id=%s""",(gift_id,))).fetchone()
            if not edition or edition["title"]!="Plush Pepe" or edition["status"]!="published" or edition["supply_total"]!=1 or edition["issued"]>1:
                raise RuntimeError("New edition not in expected state")
            found={}
            for field,table,name,max_permille in [
                ("model_attribute_id","star_gift_collectible_models",MODEL,10),
                ("pattern_attribute_id","star_gift_collectible_patterns",PATTERN,2),
                ("backdrop_attribute_id","star_gift_collectible_backdrops",BACKDROP,20),
            ]:
                row=await (await conn.execute(
                    f"SELECT id,name,rarity_permille FROM {table} WHERE collectible_revision_id=%s AND name=%s",
                    (edition["collectible_revision_id"],name),
                )).fetchone()
                if not row or row["rarity_permille"]>max_permille:
                    raise RuntimeError("Requested rare attribute is missing or rarity unexpectedly changed: "+name)
                found[field]=row["id"]
            if edition["issued"]==1:
                g=await get_granted(conn)
                validate_granted(g)
                print("TESTER_RARE_PEPE_ALREADY_VERIFIED",json.dumps(dict(g),default=str),flush=True)
                return
        common={"actor":"risegram-owner-operator","reason":"One owner-requested rare patterned Black Plush Pepe for @tester"}
        async def call(path,body):
            async with admin.post(os.environ["MTPROTO_ADMIN_API_URL"].rstrip("/")+path,json=body) as r:
                result=await r.json(content_type=None)
                if r.status!=200 or result.get("status")!="completed":
                    raise RuntimeError(f"Native admin API {r.status}: {str(result.get('error') or result.get('message') or result.get('code'))[:180]}")
                return result
        def setbody(enabled):
            return dict(common,command_id=EDITION_KEY+"-toggle-"+uuid.uuid4().hex,
                        gift_id=gift_id,enabled=enabled)
        toggle_attempted=False
        try:
            toggle_attempted=True
            await call("/v1/gifts/set-enabled",setbody(True))
            print("TESTER_PEPE_TEMP_ENABLED",gift_id,flush=True)
            payload=dict(common,command_id=COMMAND_ID,user_id=TESTER_ID,gift_id=gift_id,
                         upgrade=True,**found,message="🎁 Plush Pepe · Toading... · Diamonds · Black")
            preview=await call("/v1/gifts/give",dict(payload,command_id=COMMAND_ID+"-drycheck",dry_run=True))
            if not preview.get("dry_run"):
                raise RuntimeError("Gift dry-run not confirmed")
            print("TESTER_RARE_PEPE_DRY_RUN_OK",json.dumps(found),flush=True)
            receipt=await call("/v1/gifts/give",dict(payload,dry_run=False))
            print("TESTER_RARE_PEPE_GRANTED",json.dumps({
                "gift_id":gift_id,"recipient_id":TESTER_ID,"model":MODEL,
                "pattern":PATTERN,"backdrop":BACKDROP,"already_executed":receipt.get("already_executed")
            }),flush=True)
        finally:
            if toggle_attempted:
                for retry in range(3):
                    try:
                        await call("/v1/gifts/set-enabled",setbody(False))
                        print("TESTER_PEPE_CATALOG_RESTORED",gift_id,flush=True)
                        break
                    except Exception as exc:
                        print("TESTER_PEPE_CATALOG_RESTORE_RETRY",retry+1,str(exc)[:130],flush=True)
                        if retry==2: raise
                        await asyncio.sleep(2)
    async with await psycopg.AsyncConnection.connect(
        os.environ["RISEGRAM_LEDGER_DSN"],row_factory=dict_row,connect_timeout=8
    ) as conn:
        g=await get_granted(conn)
        validate_granted(g)
        print("TESTER_RARE_PEPE_VERIFIED_IN_INVENTORY",json.dumps(dict(g),default=str),flush=True)

if __name__=="__main__":
    asyncio.run(main())
