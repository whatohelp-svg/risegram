"""Read-only release-backup audit for precisely @paths balances and @owner gift restoration."""
import asyncio,csv,gzip,json,os
from collections import Counter
from pathlib import Path
import psycopg
from psycopg.rows import dict_row
from release_reset_backup_once import ROOT,verify

def rows(table):
    with gzip.open(ROOT/(table+".csv.gz"),"rt",newline="",encoding="utf-8") as f:
        return list(csv.DictReader(f))
def valid_num(v):
    return v if v is not None else ""

async def main():
    if not verify():raise RuntimeError("Backup not yet verified")
    paths=1780243207; owner=1780243200
    stars=next((x for x in rows("stars_balances") if x.get("user_id")==str(paths)),None)
    gram=next((x for x in rows("ton_balances") if x.get("user_id")==str(paths)),None)
    print("RESTORE_AUDIT_PATHS_BALANCES",json.dumps({
        "uid":paths,"stars":stars and stars["balance"],
        "gram_nanoton":gram and gram["balance_nanoton"],
        "stars_granted":stars and stars["granted"],
        "gram_granted":gram and gram["granted"],
    }),flush=True)
    uniq=rows("unique_star_gifts")
    saved=rows("peer_star_gifts")
    own=[x for x in uniq if x["owner_peer_type"]=="user"
         and x["owner_peer_id"]==str(owner) and x["burned"]=="f"]
    active_ids={x["id"] for x in own}
    owned_saved=[x for x in saved if x["unique_gift_id"] in active_ids
      and x["owner_peer_type"]=="user" and x["owner_peer_id"]==str(owner)
      and x["lifecycle_status"]=="active"]
    print("RESTORE_AUDIT_OWNER_NFT",json.dumps({
        "uid":owner,"snapshot_uniques_total":len(uniq),
        "owned_active_nft":len(own),"owned_active_saves":len(owned_saved),
        "gift_editions":Counter(x["gift_id"] for x in own),
        "owner_first_ids":sorted(int(x["id"]) for x in own)[:22],
        "owner_count_burned":sum(1 for x in uniq if x["owner_peer_id"]==str(owner) and x["burned"]=="t"),
        "saved_id_match":len(owned_saved)==len(own),
    },default=dict),flush=True)
    for tab in ("star_gift_admin_grant_commands","star_gift_purchase_commands","star_gift_upgrade_commands","star_gift_collection_items","star_gift_user_message_refs","star_gift_listings","star_gift_offers","star_gift_sales","star_gift_transfer_commands"):
        sub=rows(tab)
        selected=[x for x in sub if x.get("unique_gift_id") in active_ids
              or x.get("saved_gift_id") in {v["id"] for v in owned_saved}
              or x.get("source_saved_gift_id") in {v["id"] for v in owned_saved}]
        print("RESTORE_AUDIT_OWNER_DEPENDENCIES",json.dumps({"table":tab,"rows":len(selected)}),flush=True)
    async with await psycopg.AsyncConnection.connect(os.environ["RISEGRAM_LEDGER_DSN"],row_factory=dict_row,connect_timeout=10) as conn:
        await conn.execute("SET TRANSACTION READ ONLY")
        aliases=await (await conn.execute(
             "SELECT username_lower,peer_id,peer_type FROM peer_usernames WHERE username_lower IN ('paths','owner','koyyzz') ORDER BY username_lower"
        )).fetchall()
        present=await (await conn.execute("""
          SELECT
            (SELECT COUNT(*) FROM unique_star_gifts WHERE owner_peer_type='user' AND owner_peer_id=%s AND NOT burned)::bigint AS owner_current_nfts,
            (SELECT balance FROM stars_balances WHERE user_id=%s) AS paths_stars,
            (SELECT balance_nanoton FROM ton_balances WHERE user_id=%s) AS paths_gram,
            (SELECT COUNT(*) FROM users)::bigint AS users,
            (SELECT COUNT(*) FROM account_rating)::bigint AS ratings
        """,(owner,paths,paths))).fetchone()
        print("RESTORE_AUDIT_CURRENT",json.dumps({"aliases":aliases,"state":present},default=str),flush=True)
        vtables=await (await conn.execute("""
            SELECT table_name,column_name FROM information_schema.columns
            WHERE table_schema='public' AND table_name ~* 'verif'
            ORDER BY table_name,column_name
        """)).fetchall()
        by={}
        for row in vtables:by.setdefault(row["table_name"],[]).append(row["column_name"])
        print("RESTORE_AUDIT_VERIF_TABLES",json.dumps(by),flush=True)
if __name__=="__main__":
    asyncio.run(main())
