"""Give @paths one Scared Cat (Spooky Cat symbol) and one Durov's Cap; both Black and random models.

Each gift uses a separate immutable command ID, so retries cannot mint duplicates.
"""
import asyncio
import hashlib
import json
import os

import psycopg
from psycopg.rows import dict_row
from aiohttp import ClientSession, ClientTimeout

USER_ID=1780243207
GIFTS=(
    {"title":"Scared Cats","gift_id":9000000000000133,"key":"cat",
     "command_id":"operator-20261123-paths-scaredcat-spookycat-black-randommodel-one-v1",
     "symbol":"Spooky Cat"},
    {"title":"Durov’s Caps","gift_id":9000000000000130,"key":"cap",
     "command_id":"operator-20261123-paths-durovscap-black-randommodel-one-v1",
     "symbol":None},
)

async def get_receipt(conn, command_id):
    return await (await conn.execute("""
        SELECT u.id,u.num,u.slug,u.burned,u.owner_peer_type,u.owner_peer_id,u.gift_id,
               m.id AS model_id,m.name AS model_name,
               pa.name AS pattern_name,b.name AS backdrop_name,
               s.lifecycle_status,s.owner_peer_type AS saved_type,s.owner_peer_id AS saved_owner
          FROM star_gift_admin_grant_commands c
          JOIN unique_star_gifts u ON u.id=c.unique_gift_id
          JOIN peer_star_gifts s ON s.id=c.saved_gift_id
          JOIN star_gift_collectible_models m ON m.id=u.model_attribute_id
          JOIN star_gift_collectible_patterns pa ON pa.id=u.pattern_attribute_id
          JOIN star_gift_collectible_backdrops b ON b.id=u.backdrop_attribute_id
         WHERE c.recipient_user_id=%s AND c.command_key=%s
    """,(USER_ID,"admin-gift:"+command_id))).fetchone()

def valid_receipt(item, receipt, expected_model):
    if not receipt or receipt["burned"] or receipt["owner_peer_type"]!="user" or receipt["owner_peer_id"]!=USER_ID or receipt["saved_type"]!="user" or receipt["saved_owner"]!=USER_ID or receipt["lifecycle_status"]!="active" or receipt["gift_id"]!=item["gift_id"] or receipt["model_id"]!=expected_model or receipt["backdrop_name"]!="Black" or (item["symbol"] and receipt["pattern_name"]!=item["symbol"]):
        raise RuntimeError("Gift inventory verification failed for "+item["key"])

def deterministic_random_model(rows,command_id):
    # Pseudorandom but stable for the same command, including after network timeouts.
    return min(rows,key=lambda r:hashlib.sha256(
        (command_id+":"+str(r["id"])).encode("utf-8")).digest())

async def grant_one(item, session):
    async with await psycopg.AsyncConnection.connect(
        os.environ["RISEGRAM_LEDGER_DSN"],row_factory=dict_row,connect_timeout=12) as conn:
        peers=await (await conn.execute(
            "SELECT peer_id,peer_type FROM peer_usernames WHERE username_lower='paths'")).fetchall()
        if len(peers)!=1 or peers[0]["peer_type"]!="user" or peers[0]["peer_id"]!=USER_ID:
            raise RuntimeError("@paths username verification failed")
        edition=await (await conn.execute("""
            SELECT c.enabled,c.collectible_revision_id,a.title,r.status,r.supply_total,r.issued
              FROM star_gift_catalog c
              JOIN star_gift_catalog_revisions a ON a.id=c.active_revision_id
              JOIN star_gift_collectible_revisions r ON r.id=c.collectible_revision_id
             WHERE c.gift_id=%s
        """,(item["gift_id"],))).fetchone()
        if not edition or not edition["enabled"] or edition["title"]!=item["title"] or edition["status"]!="published":
            raise RuntimeError("Requested gift edition not published and enabled: "+item["key"])
        rid=edition["collectible_revision_id"]
        models=await (await conn.execute("""
            SELECT id,name,rarity_permille FROM star_gift_collectible_models
             WHERE collectible_revision_id=%s ORDER BY id
        """,(rid,))).fetchall()
        if not models:
            raise RuntimeError("No collectible models: "+item["key"])
        model=deterministic_random_model(models,item["command_id"])
        black=await (await conn.execute("""
            SELECT id,name FROM star_gift_collectible_backdrops
             WHERE collectible_revision_id=%s AND name='Black'
        """,(rid,))).fetchone()
        if not black:
            raise RuntimeError("Black backdrop not available: "+item["key"])
        pattern_id=0
        if item["symbol"]:
            pattern=await (await conn.execute("""
                SELECT id,name FROM star_gift_collectible_patterns
                 WHERE collectible_revision_id=%s AND name=%s
            """,(rid,item["symbol"]))).fetchone()
            if not pattern:
                raise RuntimeError("Requested Spooky Cat symbol not in catalog; refusing substitution")
            pattern_id=pattern["id"]
        receipt=await get_receipt(conn,item["command_id"])
        if receipt:
            valid_receipt(item,receipt,model["id"])
            print("PATHS_CAT_CAP_ALREADY_VERIFIED",json.dumps(dict(receipt),default=str),flush=True)
            return
        if edition["issued"]>=edition["supply_total"]:
            raise RuntimeError("Gift supply exhausted: "+item["key"])
        print("PATHS_CAT_CAP_PRECHECK",json.dumps({
            "key":item["key"],"recipient_id":USER_ID,"gift_id":item["gift_id"],
            "available":edition["supply_total"]-edition["issued"],
            "model":model,"backdrop":"Black","pattern":item["symbol"] or "(native random)"
        },default=str),flush=True)
    payload={
        "command_id":item["command_id"],
        "actor":"risegram-owner-operator",
        "reason":"One user-requested Black collectible gift with random model for @paths",
        "user_id":USER_ID,
        "gift_id":item["gift_id"],
        "upgrade":True,
        "model_attribute_id":model["id"],
        "backdrop_attribute_id":black["id"],
        "message":item["title"]+" · "+model["name"]+" · Black",
    }
    if pattern_id:
        payload["pattern_attribute_id"]=pattern_id
    url=os.environ["MTPROTO_ADMIN_API_URL"].rstrip("/")+"/v1/gifts/give"
    async def post(data):
        async with session.post(url,json=data) as response:
            result=await response.json(content_type=None)
            if response.status!=200 or result.get("status")!="completed":
                raise RuntimeError("Native grant HTTP "+str(response.status)+" "+
                    str(result.get("error") or result.get("message") or result.get("code"))[:160])
            return result
    checked=await post(dict(payload,command_id=item["command_id"]+"-drycheck",dry_run=True))
    if not checked.get("dry_run"):
        raise RuntimeError("Dry-run not confirmed: "+item["key"])
    print("PATHS_CAT_CAP_DRY_RUN_OK",item["key"],flush=True)
    result=await post(dict(payload,dry_run=False))
    print("PATHS_CAT_CAP_GRANTED",json.dumps({
        "key":item["key"],"gift_id":item["gift_id"],
        "already_executed":result.get("already_executed"),
        "model":model["name"],"backdrop":"Black"
    }),flush=True)
    async with await psycopg.AsyncConnection.connect(
        os.environ["RISEGRAM_LEDGER_DSN"],row_factory=dict_row,connect_timeout=12) as conn:
        receipt=await get_receipt(conn,item["command_id"])
        valid_receipt(item,receipt,model["id"])
        print("PATHS_CAT_CAP_VERIFIED_IN_INVENTORY",
              json.dumps(dict(receipt),default=str),flush=True)

async def main():
    async with ClientSession(timeout=ClientTimeout(total=80,connect=25),
                             headers={"Authorization":"Bearer "+os.environ["MTPROTO_ADMIN_API_TOKEN"]}) as session:
        for item in GIFTS:
            await grant_one(item,session)

if __name__=="__main__":
    asyncio.run(main())
