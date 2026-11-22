"""One-time, idempotent official RiseGram collectible grant: owner blue Plush Pepe."""
import asyncio
import os
import json
from aiohttp import ClientSession, ClientTimeout
import psycopg
from psycopg.rows import dict_row

OWNER_ID = 1780243200
COMMAND_ID = "operator-20261122-owner-aquaplush-azureblue-one-v1"

async def main():
    async with await psycopg.AsyncConnection.connect(os.environ["RISEGRAM_LEDGER_DSN"], row_factory=dict_row, connect_timeout=8) as conn:
        cur = await conn.execute("SELECT username_lower,peer_id FROM peer_usernames WHERE username_lower IN ('owner','koyyzz') AND peer_type='user'")
        aliases = await cur.fetchall()
        if len(aliases) != 2 or {r["username_lower"] for r in aliases} != {"owner","koyyzz"} or {r["peer_id"] for r in aliases} != {OWNER_ID}:
            raise RuntimeError("Owner identity aliases do not match; refusing grant")
        cur = await conn.execute("""SELECT c.gift_id,ar.title,c.enabled,c.collectible_revision_id
            FROM star_gift_catalog c JOIN star_gift_catalog_revisions ar ON ar.id=c.active_revision_id
            JOIN star_gift_collectible_revisions cr ON cr.id=c.collectible_revision_id
            WHERE lower(ar.title) LIKE '%pepe%' AND cr.status='published'
            ORDER BY c.enabled DESC,c.gift_id DESC""")
        editions = await cur.fetchall()
        print("PEPE_EDITION_ENABLED",json.dumps([{"id":r["gift_id"],"enabled":r["enabled"],"title":r["title"]} for r in editions]),flush=True)
        candidates=[]
        for edition in editions:
            if not edition["enabled"]: continue
            rid=edition["collectible_revision_id"]
            cur=await conn.execute("SELECT id FROM star_gift_collectible_models WHERE collectible_revision_id=%s AND name='Aqua Plush'",(rid,))
            model=await cur.fetchone()
            cur=await conn.execute("SELECT id FROM star_gift_collectible_backdrops WHERE collectible_revision_id=%s AND name='Azure Blue'",(rid,))
            backdrop=await cur.fetchone()
            if model and backdrop: candidates.append((edition["gift_id"],model["id"],backdrop["id"]))
        if not candidates: raise RuntimeError("No enabled Plush Pepe edition with blue attributes")
    url = os.environ["MTPROTO_ADMIN_API_URL"].rstrip("/")+"/v1/gifts/give"
    token = os.environ["MTPROTO_ADMIN_API_TOKEN"]
    async with ClientSession(timeout=ClientTimeout(total=25),headers={"Authorization":"Bearer "+token}) as client:
        async def grant(data):
            async with client.post(url,json=data) as response:
                result=await response.json(content_type=None)
                return response.status,result
        for gift_id, model_id, backdrop_id in candidates:
            payload = {
                "command_id":COMMAND_ID,"actor":"risegram-owner-operator",
                "reason":"Owner-requested one blue Plush Pepe on 2026-11-22",
                "user_id":OWNER_ID,"gift_id":gift_id,"upgrade":True,
                "model_attribute_id":model_id,"backdrop_attribute_id":backdrop_id,
                "message":"🎁 Plush Pepe • Aqua Plush • Azure Blue",
            }
            status,dry=await grant(dict(payload,command_id=COMMAND_ID+"-dry-"+str(gift_id),dry_run=True))
            if status==400 and "not found" in str(dry.get("error") or dry.get("message")).lower():
                print("PEPE_EDITION_REJECTED",gift_id,flush=True)
                continue
            if status!=200 or dry.get("status")!="completed" or not dry.get("dry_run"):
                raise RuntimeError("Gift precheck rejected: "+str({"http":status,"error":dry.get("error"),"message":dry.get("message")})[:250])
            print("PEPE_DRY_RUN_OK",json.dumps({"gift_id":gift_id,"owner_id":OWNER_ID,"model":"Aqua Plush","backdrop":"Azure Blue"}),flush=True)
            status,result=await grant(dict(payload,dry_run=False))
            if status!=200 or result.get("status")!="completed":
                raise RuntimeError("Gift grant rejected: "+str({"http":status,"error":result.get("error"),"message":result.get("message")})[:250])
            print("PEPE_GRANTED",json.dumps({"command_id":COMMAND_ID,"gift_id":gift_id,"status":result.get("status"),"already_executed":result.get("already_executed"),"details":result.get("details")},default=str),flush=True)
            return
        raise RuntimeError("No published enabled Plush Pepe edition is visible to the API")

asyncio.run(main())
