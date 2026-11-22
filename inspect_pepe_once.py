"""One-time, idempotent official RiseGram collectible grant: owner blue Plush Pepe."""
import asyncio
import os
import json
from aiohttp import ClientSession, ClientTimeout
import psycopg
from psycopg.rows import dict_row

GIFT_ID = 9000000000000120
OWNER_ID = 1780243200
MODEL_ID = 1026
BACKDROP_ID = 1106
COMMAND_ID = "operator-20261122-owner-aquaplush-azureblue-one-v1"

async def main():
    async with await psycopg.AsyncConnection.connect(os.environ["RISEGRAM_LEDGER_DSN"], row_factory=dict_row, connect_timeout=8) as conn:
        cur = await conn.execute("SELECT username_lower,peer_id FROM peer_usernames WHERE username_lower IN ('owner','koyyzz') AND peer_type='user'")
        aliases = await cur.fetchall()
        if len(aliases) != 2 or {r["username_lower"] for r in aliases} != {"owner","koyyzz"} or {r["peer_id"] for r in aliases} != {OWNER_ID}:
            raise RuntimeError("Owner identity aliases do not match; refusing grant")
        cur = await conn.execute("""SELECT ar.title,c.collectible_revision_id
            FROM star_gift_catalog c JOIN star_gift_catalog_revisions ar ON ar.id=c.active_revision_id
            JOIN star_gift_collectible_revisions cr ON cr.id=c.collectible_revision_id
            WHERE c.gift_id=%s AND cr.status='published'""", (GIFT_ID,))
        row = await cur.fetchone()
        if not row or "pepe" not in row["title"].lower():
            raise RuntimeError("Plush Pepe published edition not found")
        rid = row["collectible_revision_id"]
        for table, attr_id, expected in [
            ("star_gift_collectible_models",MODEL_ID,"Aqua Plush"),
            ("star_gift_collectible_backdrops",BACKDROP_ID,"Azure Blue"),
        ]:
            cur = await conn.execute(f"SELECT name FROM {table} WHERE id=%s AND collectible_revision_id=%s",(attr_id,rid))
            record = await cur.fetchone()
            if not record or record["name"] != expected:
                raise RuntimeError("Gift attribute mismatch: "+expected)
    url = os.environ["MTPROTO_ADMIN_API_URL"].rstrip("/")+"/v1/gifts/give"
    token = os.environ["MTPROTO_ADMIN_API_TOKEN"]
    payload = {
        "command_id":COMMAND_ID,"actor":"risegram-owner-operator",
        "reason":"Owner-requested one blue Plush Pepe on 2026-11-22",
        "user_id":OWNER_ID,"gift_id":GIFT_ID,"upgrade":True,
        "model_attribute_id":MODEL_ID,"backdrop_attribute_id":BACKDROP_ID,
        "message":"🎁 Plush Pepe • Aqua Plush • Azure Blue",
    }
    async with ClientSession(timeout=ClientTimeout(total=100),headers={"Authorization":"Bearer "+token}) as client:
        async def grant(data):
            async with client.post(url,json=data) as response:
                result=await response.json(content_type=None)
                if response.status!=200 or result.get("status")!="completed":
                    raise RuntimeError(f"Gift admin API rejected: HTTP {response.status} - {str(result.get('error') or result.get('message') or result.get('code'))[:180]}")
                return result
        dry = await grant(dict(payload,command_id=COMMAND_ID+"-dry",dry_run=True))
        if not dry.get("dry_run"):
            raise RuntimeError("Gift dry-run did not confirm dry_run=true")
        print("PEPE_DRY_RUN_OK",json.dumps({"gift_id":GIFT_ID,"owner_id":OWNER_ID,"model":"Aqua Plush","backdrop":"Azure Blue"}),flush=True)
        actual = await grant(dict(payload,dry_run=False))
        print("PEPE_GRANTED",json.dumps({"command_id":COMMAND_ID,"status":actual.get("status"),"already_executed":actual.get("already_executed"),"message":actual.get("message"),"details":actual.get("details")},default=str),flush=True)

asyncio.run(main())
