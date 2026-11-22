"""One idempotent admin-issued rare Jolly Chimps gift to verified @paths in native RiseGram."""
import asyncio
import json
import os

import psycopg
from psycopg.rows import dict_row
from aiohttp import ClientSession, ClientTimeout

USER_ID = 1780243207
GIFT_ID = 9000000000000114
MODEL_ID = 691       # Smash King (0.5%)
PATTERN_ID = 2928    # Sombrero (0.1%)
BACKDROP_ID = 726    # Black (1%)
COMMAND_ID = "operator-20261122-paths-jollychimps-smashking-sombrero-black-one-v1"
COMMAND_KEY = "admin-gift:" + COMMAND_ID

async def get_gift_receipt(conn):
    return await (await conn.execute("""
        SELECT u.id,u.num,u.slug,u.gift_id,u.burned,u.owner_peer_type,u.owner_peer_id,
               u.model_attribute_id,u.pattern_attribute_id,u.backdrop_attribute_id,
               m.name AS model_name,pa.name AS pattern_name,b.name AS backdrop_name,
               s.lifecycle_status,s.owner_peer_type AS saved_owner_type,
               s.owner_peer_id AS saved_owner_id
          FROM star_gift_admin_grant_commands g
          JOIN unique_star_gifts u ON u.id=g.unique_gift_id
          JOIN peer_star_gifts s ON s.id=g.saved_gift_id
          JOIN star_gift_collectible_models m ON m.id=u.model_attribute_id
          JOIN star_gift_collectible_patterns pa ON pa.id=u.pattern_attribute_id
          JOIN star_gift_collectible_backdrops b ON b.id=u.backdrop_attribute_id
         WHERE g.recipient_user_id=%s AND g.command_key=%s
    """,(USER_ID,COMMAND_KEY))).fetchone()

def verify_receipt(row):
    if not row or row["gift_id"]!=GIFT_ID or row["burned"] or row["owner_peer_type"]!="user" or row["owner_peer_id"]!=USER_ID or row["saved_owner_type"]!="user" or row["saved_owner_id"]!=USER_ID or row["lifecycle_status"]!="active" or row["model_attribute_id"]!=MODEL_ID or row["pattern_attribute_id"]!=PATTERN_ID or row["backdrop_attribute_id"]!=BACKDROP_ID or row["model_name"]!="Smash King" or row["pattern_name"]!="Sombrero" or row["backdrop_name"]!="Black":
        raise RuntimeError("Jolly Chimps grant receipt or inventory does not match @paths and exact attributes")

async def main():
    async with await psycopg.AsyncConnection.connect(
        os.environ["RISEGRAM_LEDGER_DSN"],row_factory=dict_row,connect_timeout=12
    ) as conn:
        peers=await (await conn.execute("""
            SELECT peer_id,username_lower,peer_type FROM peer_usernames
            WHERE username_lower='paths'
        """)).fetchall()
        if len(peers)!=1 or peers[0]["peer_type"]!="user" or peers[0]["peer_id"]!=USER_ID:
            raise RuntimeError("Recipient @paths verification failed")
        existing=await get_gift_receipt(conn)
        if existing:
            verify_receipt(existing)
            print("PATHS_JOLLY_ALREADY_VERIFIED",json.dumps(dict(existing),default=str),flush=True)
            return
        gift=await (await conn.execute("""
            SELECT c.enabled,c.collectible_revision_id,a.title,r.status,r.supply_total,r.issued
              FROM star_gift_catalog c
              JOIN star_gift_catalog_revisions a ON a.id=c.active_revision_id
              JOIN star_gift_collectible_revisions r ON r.id=c.collectible_revision_id
             WHERE c.gift_id=%s
        """,(GIFT_ID,))).fetchone()
        if not gift or not gift["enabled"] or gift["status"]!="published" or gift["title"]!="Jolly Chimps" or gift["issued"]>=gift["supply_total"]:
            raise RuntimeError("Original Jolly Chimps edition not available")
        for table,aid,name,max_rarity in [
            ("star_gift_collectible_models",MODEL_ID,"Smash King",5),
            ("star_gift_collectible_patterns",PATTERN_ID,"Sombrero",1),
            ("star_gift_collectible_backdrops",BACKDROP_ID,"Black",10),
        ]:
            row=await (await conn.execute(
                f"SELECT name,rarity_permille FROM {table} WHERE id=%s AND collectible_revision_id=%s",
                (aid,gift["collectible_revision_id"]),
            )).fetchone()
            if not row or row["name"]!=name or row["rarity_permille"]>max_rarity:
                raise RuntimeError("Incorrect rare Jolly Chimps attribute "+name)
        print("PATHS_JOLLY_PRECHECK_OK",json.dumps({"peer_id":USER_ID,"gift_id":GIFT_ID,
            "issued":gift["issued"],"supply_total":gift["supply_total"]}),flush=True)
    url=os.environ["MTPROTO_ADMIN_API_URL"].rstrip("/")+"/v1/gifts/give"
    token=os.environ["MTPROTO_ADMIN_API_TOKEN"]
    payload={
        "command_id":COMMAND_ID,
        "actor":"risegram-owner-operator",
        "reason":"Owner-requested one rare Jolly Chimps with Black backdrop for @paths",
        "user_id":USER_ID,"gift_id":GIFT_ID,"upgrade":True,
        "model_attribute_id":MODEL_ID,"pattern_attribute_id":PATTERN_ID,
        "backdrop_attribute_id":BACKDROP_ID,
        "message":"🎁 Jolly Chimps · Smash King · Sombrero · Black",
    }
    async with ClientSession(timeout=ClientTimeout(total=70,connect=25),
                             headers={"Authorization":"Bearer "+token}) as session:
        async def post(body):
            async with session.post(url,json=body) as response:
                answer=await response.json(content_type=None)
                if response.status!=200 or answer.get("status")!="completed":
                    raise RuntimeError("Native grant HTTP "+str(response.status)+": "+str(answer.get("error") or answer.get("message") or answer.get("code"))[:160])
                return answer
        checked=await post(dict(payload,command_id=COMMAND_ID+"-validation",dry_run=True))
        if not checked.get("dry_run"):
            raise RuntimeError("Jolly Chimps validation dry-run not confirmed")
        print("PATHS_JOLLY_DRY_RUN_OK",flush=True)
        result=await post(dict(payload,dry_run=False))
        print("PATHS_JOLLY_GRANTED",json.dumps({"user_id":USER_ID,"gift_id":GIFT_ID,
            "already_executed":result.get("already_executed"),
            "model":"Smash King","pattern":"Sombrero","backdrop":"Black"}),flush=True)
    async with await psycopg.AsyncConnection.connect(os.environ["RISEGRAM_LEDGER_DSN"],
                row_factory=dict_row,connect_timeout=12) as conn:
        receipt=await get_gift_receipt(conn)
        verify_receipt(receipt)
        print("PATHS_JOLLY_VERIFIED_IN_INVENTORY",json.dumps(dict(receipt),default=str),flush=True)

if __name__=="__main__":
    asyncio.run(main())
