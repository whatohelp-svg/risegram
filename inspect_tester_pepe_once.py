"""Read-only lookup of RiseGram @tester and real collectible Plush Pepe attributes."""
import asyncio
import json
import os
import psycopg
from psycopg.rows import dict_row

async def inspect():
    async with await psycopg.AsyncConnection.connect(
        os.environ["RISEGRAM_LEDGER_DSN"], row_factory=dict_row, connect_timeout=8
    ) as conn:
        await conn.execute("SET TRANSACTION READ ONLY")
        aliases = await (await conn.execute("""
            SELECT peer_id, username_lower, peer_type
              FROM peer_usernames
             WHERE username_lower='tester'
        """)).fetchall()
        print("TESTER_PEER_LOOKUP", json.dumps(aliases), flush=True)
        gifts = await (await conn.execute("""
            SELECT c.gift_id,c.enabled,c.collectible_revision_id,
                   a.title,r.status,r.supply_total,r.issued,r.slug_prefix
              FROM star_gift_catalog c
              JOIN star_gift_catalog_revisions a ON a.id=c.active_revision_id
              JOIN star_gift_collectible_revisions r ON r.id=c.collectible_revision_id
             WHERE a.title ILIKE '%pepe%'
             ORDER BY c.gift_id DESC
        """)).fetchall()
        print("TESTER_PEPE_EDITION_STOCK", json.dumps(gifts,default=str), flush=True)
        for gift in gifts:
            rid = gift["collectible_revision_id"]
            for name, table in (
                ("models","star_gift_collectible_models"),
                ("patterns","star_gift_collectible_patterns"),
                ("backdrops","star_gift_collectible_backdrops"),
            ):
                rows=await (await conn.execute(
                    f"SELECT id,name,rarity_kind,rarity_permille FROM {table} WHERE collectible_revision_id=%s ORDER BY rarity_permille ASC NULLS LAST,id ASC",
                    (rid,),
                )).fetchall()
                if name=="backdrops":
                    rows=[r for r in rows if "black" in r["name"].lower()]
                else:
                    rows=rows[:12]
                print("TESTER_PEPE_"+name.upper(),json.dumps({
                    "gift_id":gift["gift_id"],"revision_id":rid,"choices":rows,
                },default=str),flush=True)

if __name__=="__main__":
    asyncio.run(inspect())
