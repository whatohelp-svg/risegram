"""Read-only production proof of restored owner gifts, @paths wallet and chat verification marks."""
import asyncio,json,os
import psycopg
from psycopg.rows import dict_row
async def main():
    async with await psycopg.AsyncConnection.connect(os.environ["RISEGRAM_LEDGER_DSN"],row_factory=dict_row,connect_timeout=10) as db:
        await db.execute("SET TRANSACTION READ ONLY")
        a=await (await db.execute("""SELECT count(*)::bigint AS n FROM unique_star_gifts
          WHERE owner_peer_type='user' AND owner_peer_id=1780243200 AND NOT burned""")).fetchone()
        b=await (await db.execute("""SELECT s.balance AS stars,g.balance_nanoton AS gram_nano
          FROM stars_balances s JOIN ton_balances g ON g.user_id=s.user_id
          WHERE s.user_id=1780243207""")).fetchone()
        counts=await (await db.execute("""SELECT peer_type,count(*)::bigint AS n
          FROM custom_verifications GROUP BY peer_type ORDER BY peer_type""")).fetchall()
        marks=await (await db.execute("""SELECT peer_type,peer_id,
            LEFT(description,130) AS description,icon_document_id
            FROM custom_verifications ORDER BY peer_type,peer_id LIMIT 35""")).fetchall()
        cols=await (await db.execute("""SELECT column_name FROM information_schema.columns
            WHERE table_schema='public' AND table_name='channels'
            ORDER BY ordinal_position""")).fetchall()
        print("VERIF_RESTORE_POSTCHECK",json.dumps({
            "owner_nft_count":a["n"],"paths_wallet":dict(b) if b else None,
            "verifier_peer_counts":counts,"verifier_records":marks,
            "channel_columns":[c["column_name"] for c in cols]
        },default=str),flush=True)
        cset={c["column_name"] for c in cols}
        if {"id","megagroup","broadcast"}.issubset(cset):
            groups=await (await db.execute("""SELECT
                c.id,c.megagroup,c.broadcast,c.verified,
                v.verifier_bot_id,v.description,v.icon_document_id
                FROM channels c LEFT JOIN custom_verifications v
                ON v.peer_type='channel' AND v.peer_id=c.id
                WHERE c.verified OR v.id IS NOT NULL LIMIT 40""")).fetchall()
            print("VERIF_CHAT_MARK_DETAILS",json.dumps(groups,default=str),flush=True)
if __name__=="__main__":
    asyncio.run(main())
