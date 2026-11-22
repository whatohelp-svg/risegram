"""Read-only production inspection for @paths Spooky Cat and Durov Cap requests."""
import asyncio,json,os
import psycopg
from psycopg.rows import dict_row

async def main():
    async with await psycopg.AsyncConnection.connect(os.environ["RISEGRAM_LEDGER_DSN"],row_factory=dict_row,connect_timeout=12) as conn:
        await conn.execute("SET TRANSACTION READ ONLY")
        peers=await (await conn.execute("SELECT peer_id,username_lower,peer_type FROM peer_usernames WHERE username_lower='paths'")).fetchall()
        print("PATHS_DOUBLE_GIFT_PEER",json.dumps(peers,default=str),flush=True)
        gifts=await (await conn.execute("""
            SELECT c.gift_id,c.enabled,c.collectible_revision_id,a.title,r.status,r.supply_total,r.issued,r.slug_prefix
              FROM star_gift_catalog c
              JOIN star_gift_catalog_revisions a ON a.id=c.active_revision_id
              LEFT JOIN star_gift_collectible_revisions r ON r.id=c.collectible_revision_id
             WHERE a.title ILIKE '%spook%' OR a.title ILIKE '%scared%' OR a.title ILIKE '%cat%'
                OR a.title ILIKE '%durov%' OR a.title ILIKE '%cap%'
             ORDER BY c.gift_id DESC LIMIT 80
        """)).fetchall()
        print("PATHS_DOUBLE_GIFT_CATALOG",json.dumps(gifts,default=str),flush=True)
        for gift in gifts:
            if not gift["collectible_revision_id"]: continue
            rid=gift["collectible_revision_id"]
            for kind,table in (
                ("models","star_gift_collectible_models"),
                ("backdrops","star_gift_collectible_backdrops"),
                ("patterns","star_gift_collectible_patterns")
            ):
                values=await (await conn.execute(f"""SELECT id,name,rarity_permille
                  FROM {table} WHERE collectible_revision_id=%s
                  ORDER BY rarity_permille NULLS LAST,id""",(rid,))).fetchall()
                if kind=="backdrops": values=[v for v in values if 'black' in v["name"].lower()]
                else: values=values[:10]
                print("PATHS_DOUBLE_GIFT_"+kind.upper(),json.dumps({"gift_id":gift["gift_id"],"values":values},default=str),flush=True)
if __name__=="__main__":
    asyncio.run(main())
