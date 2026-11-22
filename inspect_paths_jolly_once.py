"""Read-only owner-requested lookup for @paths and native Jolly Chimp variants."""
import asyncio, json, os
import psycopg
from psycopg.rows import dict_row

async def main():
    async with await psycopg.AsyncConnection.connect(os.environ["RISEGRAM_LEDGER_DSN"],row_factory=dict_row,connect_timeout=12) as conn:
        await conn.execute("SET TRANSACTION READ ONLY")
        rows=await (await conn.execute("""SELECT peer_id,peer_type,username_lower FROM peer_usernames WHERE username_lower='paths'""")).fetchall()
        print("PATHS_JOLLY_USER",json.dumps(rows,default=str),flush=True)
        gifts=await (await conn.execute("""SELECT c.gift_id,c.enabled,c.collectible_revision_id,a.title,
            r.status,r.supply_total,r.issued,r.slug_prefix
            FROM star_gift_catalog c
            JOIN star_gift_catalog_revisions a ON a.id=c.active_revision_id
            LEFT JOIN star_gift_collectible_revisions r ON r.id=c.collectible_revision_id
            WHERE a.title ILIKE '%jolly%' OR a.title ILIKE '%chimp%'
            ORDER BY c.gift_id DESC LIMIT 30""")).fetchall()
        print("PATHS_JOLLY_GIFTS",json.dumps(gifts,default=str),flush=True)
        for gift in gifts:
            if not gift["collectible_revision_id"]:continue
            rid=gift["collectible_revision_id"]
            for kind,table in [("models","star_gift_collectible_models"),("patterns","star_gift_collectible_patterns"),("backdrops","star_gift_collectible_backdrops")]:
                rows=await (await conn.execute(f"""SELECT id,name,rarity_permille FROM {table}
                     WHERE collectible_revision_id=%s ORDER BY rarity_permille ASC NULLS LAST,id ASC""",(rid,))).fetchall()
                if kind=="backdrops":
                    rows=[v for v in rows if "black" in v["name"].lower()]
                else:
                    rows=rows[:16]
                print("PATHS_JOLLY_"+kind.upper(),json.dumps({"gift_id":gift["gift_id"],"items":rows},default=str),flush=True)
if __name__=="__main__":
    asyncio.run(main())
