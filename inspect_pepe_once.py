"""One-time idempotent owner gift via RiseGram's authenticated native admin API."""
import asyncio
import json
import os
import uuid

import psycopg
from psycopg.rows import dict_row
from aiohttp import ClientSession, ClientTimeout

GIFT_ID=9000000000000120
OWNER_ID=1780243200
MODEL_ID=1026
BACKDROP_ID=1106
COMMAND_ID="operator-20261122-owner-aquaplush-azureblue-one-v1"

async def main():
    async with await psycopg.AsyncConnection.connect(os.environ["RISEGRAM_LEDGER_DSN"],row_factory=dict_row,connect_timeout=8) as conn:
        existing=await (await conn.execute("""SELECT u.id,u.num,u.slug,u.burned,u.owner_peer_id,p.lifecycle_status,
           u.model_attribute_id,u.backdrop_attribute_id
           FROM star_gift_admin_grant_commands c
           JOIN unique_star_gifts u ON u.id=c.unique_gift_id
           JOIN peer_star_gifts p ON p.id=c.saved_gift_id
           WHERE c.recipient_user_id=%s AND c.command_key=%s""",
           (OWNER_ID,"admin-gift:"+COMMAND_ID))).fetchone()
        if existing:
            if (existing["burned"] or existing["owner_peer_id"]!=OWNER_ID or
                existing["lifecycle_status"]!="active" or existing["model_attribute_id"]!=MODEL_ID or
                existing["backdrop_attribute_id"]!=BACKDROP_ID):
                raise RuntimeError("Existing gift receipt does not match expected owned item")
            print("PEPE_ALREADY_GRANTED",json.dumps(dict(existing),default=str),flush=True)
            return
        rows=await (await conn.execute("SELECT peer_id,username_lower FROM peer_usernames WHERE username_lower IN ('owner','koyyzz') AND peer_type='user'")).fetchall()
        if len(rows)!=2 or {r["username_lower"] for r in rows}!={"owner","koyyzz"} or {r["peer_id"] for r in rows}!={OWNER_ID}:
            raise RuntimeError("Recipient identity verification failed")
        gift=await (await conn.execute("""SELECT c.enabled,cr.status,c.collectible_revision_id,ar.title
              FROM star_gift_catalog c JOIN star_gift_catalog_revisions ar ON ar.id=c.active_revision_id
              JOIN star_gift_collectible_revisions cr ON cr.id=c.collectible_revision_id
              WHERE c.gift_id=%s""",(GIFT_ID,))).fetchone()
        if not gift or gift["enabled"] or gift["status"]!="published" or "pepe" not in gift["title"].lower():
            raise RuntimeError("Expected disabled published Plush Pepe edition not found")
        for table,ident,name in [("star_gift_collectible_models",MODEL_ID,"Aqua Plush"),("star_gift_collectible_backdrops",BACKDROP_ID,"Azure Blue")]:
            found=await (await conn.execute(f"SELECT name FROM {table} WHERE collectible_revision_id=%s AND id=%s",(gift["collectible_revision_id"],ident))).fetchone()
            if not found or found["name"]!=name: raise RuntimeError("Wrong collectible "+name)
    base=os.environ["MTPROTO_ADMIN_API_URL"].rstrip("/")
    token=os.environ["MTPROTO_ADMIN_API_TOKEN"]
    async with ClientSession(timeout=ClientTimeout(total=40),headers={"Authorization":"Bearer "+token}) as client:
        async def call(path,body):
            async with client.post(base+path,json=body) as response:
                answer=await response.json(content_type=None)
                if response.status!=200 or answer.get("status")!="completed":
                    raise RuntimeError("Native admin API HTTP "+str(response.status)+" "+str(answer.get("error") or answer.get("message"))[:120])
                return answer
        common={"actor":"risegram-owner-operator","reason":"One owner-requested blue Plush Pepe"}
        async def set_enabled(enabled):
            return await call("/v1/gifts/set-enabled",dict(common,command_id="operator-20261122-bluepepe-toggle-"+uuid.uuid4().hex,gift_id=GIFT_ID,enabled=enabled))
        granted=False
        enable_attempted=False
        try:
            enable_attempted=True
            await set_enabled(True)
            print("PEPE_TEMP_ENABLED",GIFT_ID,flush=True)
            payload=dict(common,command_id=COMMAND_ID,user_id=OWNER_ID,gift_id=GIFT_ID,upgrade=True,model_attribute_id=MODEL_ID,backdrop_attribute_id=BACKDROP_ID,message="🎁 Plush Pepe · Aqua Plush · Azure Blue")
            dry=await call("/v1/gifts/give",dict(payload,command_id=COMMAND_ID+"-checked",dry_run=True))
            if not dry.get("dry_run"): raise RuntimeError("Native gift dry-run validation missing")
            print("PEPE_DRY_RUN_OK",flush=True)
            receipt=await call("/v1/gifts/give",dict(payload,dry_run=False))
            granted=True
            print("PEPE_GRANTED",json.dumps({"command_id":COMMAND_ID,"user_id":OWNER_ID,"gift_id":GIFT_ID,"model":"Aqua Plush","backdrop":"Azure Blue","already_executed":receipt.get("already_executed"),"details":receipt.get("details")},default=str),flush=True)
        finally:
            if enable_attempted:
                for attempt in range(3):
                    try:
                        await set_enabled(False)
                        print("PEPE_CATALOG_RESTORED",GIFT_ID,flush=True)
                        break
                    except Exception as exc:
                        print("PEPE_RESTORE_RETRY",attempt+1,str(exc)[:100],flush=True)
                        if attempt==2: raise
                        await asyncio.sleep(2)
        if not granted: raise RuntimeError("Gift not granted")
    async with await psycopg.AsyncConnection.connect(os.environ["RISEGRAM_LEDGER_DSN"],row_factory=dict_row,connect_timeout=8) as conn:
        result=await (await conn.execute("""SELECT u.id,u.num,u.slug,u.burned,u.owner_peer_id,u.gift_id,
          u.model_attribute_id,u.backdrop_attribute_id,p.lifecycle_status,p.owner_peer_id AS saved_owner
          FROM star_gift_admin_grant_commands c
          JOIN unique_star_gifts u ON u.id=c.unique_gift_id
          JOIN peer_star_gifts p ON p.id=c.saved_gift_id
          WHERE c.recipient_user_id=%s AND c.command_key=%s""",
          (OWNER_ID,"admin-gift:"+COMMAND_ID))).fetchone()
        if not result or result["burned"] or result["owner_peer_id"]!=OWNER_ID or result["saved_owner"]!=OWNER_ID or result["lifecycle_status"]!="active" or result["gift_id"]!=GIFT_ID or result["model_attribute_id"]!=MODEL_ID or result["backdrop_attribute_id"]!=BACKDROP_ID:
            raise RuntimeError("Native collectible inventory verification failed")
        print("PEPE_VERIFIED_IN_INVENTORY",json.dumps(dict(result),default=str),flush=True)

if __name__ == "__main__":
    asyncio.run(main())
