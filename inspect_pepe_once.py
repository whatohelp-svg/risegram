"""Read-only inspection for requested owner's blue Plush Pepe gift."""
import asyncio
import os
import json
import psycopg
from psycopg.rows import dict_row

async def main():
    async with await psycopg.AsyncConnection.connect(os.environ["RISEGRAM_LEDGER_DSN"],row_factory=dict_row,connect_timeout=8) as conn:
        cur=await conn.execute("SELECT peer_id,username_lower FROM peer_usernames WHERE username_lower IN ('owner','koyyzz') AND peer_type='user'")
        print("PEPE_OWNER_ALIASES",json.dumps(await cur.fetchall(),default=str),flush=True)
        cur=await conn.execute("""SELECT c.gift_id,ar.title,c.collectible_revision_id,r.status
              FROM star_gift_catalog c JOIN star_gift_catalog_revisions ar ON ar.id=c.active_revision_id LEFT JOIN star_gift_collectible_revisions r ON r.id=c.collectible_revision_id
              WHERE lower(ar.title) LIKE '%pepe%' ORDER BY c.gift_id DESC LIMIT 20""")
        gifts=await cur.fetchall()
        print("PEPE_GIFTS",json.dumps(gifts,default=str),flush=True)
        for gift in gifts:
            rid=gift['collectible_revision_id']
            if not rid: continue
            for table,kind in [('star_gift_collectible_models','MODELS'),('star_gift_collectible_backdrops','BACKDROPS')]:
                cur=await conn.execute(f"SELECT id,name FROM {table} WHERE collectible_revision_id=%s ORDER BY sort_order,id",(rid,))
                rows=await cur.fetchall()
                print("PEPE_"+kind,json.dumps({"gift_id":gift["gift_id"],"matches":[r for r in rows if any(x in r["name"].lower() for x in ("blue","azure","aqua","ice","cyan","ocean","sapphire","sky","navy","cobalt","indigo","arctic","frost"))],"all_names":[r["name"] for r in rows]},default=str),flush=True)
asyncio.run(main())
