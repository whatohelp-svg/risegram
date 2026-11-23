"""Restore only @owner's pre-reset active native collectible gifts and @paths' RiseStars.

Source: checksum-verified private 2026-11-23 release backup. Everything else
stays reset. Re-run safe via a single receipt committed with the data.
"""
import asyncio,csv,gzip,json,os,datetime
from collections import Counter
import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from release_reset_backup_once import ROOT,verify as backup_verified

JOB="release20261123-selective-return-owner16-paths-stars-v1"
OWNER_ID=1780243200
PATHS_ID=1780243207
RELEASE_STARS=1000

def archive(name):
    with gzip.open(ROOT/(name+".csv.gz"),"rt",encoding="utf-8",newline="") as f:
        return list(csv.DictReader(f))

async def insert_archived(conn,table,items,*,blank_unique_link=False):
    if not items:return 0
    schema=await (await conn.execute("""
        SELECT a.attname,format_type(a.atttypid,a.atttypmod) AS type,
               NOT a.attnotnull AS nullable
          FROM pg_attribute a
          JOIN pg_class c ON c.oid=a.attrelid
          JOIN pg_namespace n ON n.oid=c.relnamespace
         WHERE n.nspname='public' AND c.relname=%s
           AND a.attnum>0 AND NOT a.attisdropped
         ORDER BY a.attnum
    """,(table,))).fetchall()
    cols=list(items[0].keys())
    by={x["attname"]:x for x in schema}
    if not all(x in by for x in cols):
        raise RuntimeError("Backup columns differ from current schema for "+table)
    placeholders=sql.SQL(", ").join(sql.SQL("CAST({} AS {})").format(
        sql.Placeholder(),sql.SQL(by[c]["type"])) for c in cols)
    statement=sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(
        sql.Identifier("public",table),
        sql.SQL(", ").join(map(sql.Identifier,cols)),placeholders)
    count=0
    for row in items:
        vals=[]
        for col in cols:
            v=row[col]
            if blank_unique_link and col=="unique_gift_id":
                v=None
            elif v=="" and by[col]["nullable"]:
                v=None
            vals.append(v)
        await conn.execute(statement,vals)
        count+=1
    return count

async def main():
    if not backup_verified():
        raise RuntimeError("Pre-release backup missing; refusing restoration")
    snapshot_stars=next((r for r in archive("stars_balances") if
                         r["user_id"]==str(PATHS_ID)),None)
    if not snapshot_stars or not snapshot_stars["balance"].isdigit():
        raise RuntimeError("Archived stars balance for @paths unavailable")
    archived_paths_stars=int(snapshot_stars["balance"])
    if archived_paths_stars<RELEASE_STARS:
        raise RuntimeError("Archived @paths Stars below release baseline")
    all_gifts=archive("unique_star_gifts")
    owner_gifts=[r for r in all_gifts if r["owner_peer_type"]=="user"
        and r["owner_peer_id"]==str(OWNER_ID) and r["burned"]=="f"]
    owner_ids={r["id"] for r in owner_gifts}
    all_saved=archive("peer_star_gifts")
    saved=[r for r in all_saved if r["unique_gift_id"] in owner_ids
           and r["owner_peer_type"]=="user"
           and r["owner_peer_id"]==str(OWNER_ID)
           and r["lifecycle_status"]=="active"]
    saved_ids={r["id"] for r in saved}
    sources={r["source_saved_gift_id"] for r in owner_gifts}
    missing=sources-saved_ids
    if missing:
        print("OWNER_NFT_RESTORE_SOURCE_MISSING",json.dumps({
            "missing_count":len(missing),"missing_ids":sorted(missing)[:25]}),flush=True)
        raise RuntimeError("Historical owner gifts depend on non-owner source rows; refusing unrelated inventory restoration")
    if len(owner_gifts)!=16 or len(saved)!=16 or len(owner_ids)!=16 or len(saved_ids)!=16:
        raise RuntimeError("Pre-release owner gift snapshot not exactly 16 active unique gifts")
    grants=[r for r in archive("star_gift_admin_grant_commands")
            if r["unique_gift_id"] in owner_ids and r["saved_gift_id"] in saved_ids]
    links=[r for r in archive("star_gift_user_message_refs")
           if r["saved_gift_id"] in saved_ids]
    collections=[r for r in archive("star_gift_collection_items")
                 if r["saved_gift_id"] in saved_ids]
    async with await psycopg.AsyncConnection.connect(os.environ["RISEGRAM_LEDGER_DSN"],
            row_factory=dict_row,autocommit=True,connect_timeout=12) as conn:
        async with conn.transaction():
            await conn.execute("SET LOCAL lock_timeout='8s'")
            await conn.execute("SET LOCAL statement_timeout='90s'")
            await conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))",(JOB,))
            await conn.execute("""CREATE TABLE IF NOT EXISTS risegram_selective_restore_receipts
                (job text PRIMARY KEY, details jsonb NOT NULL, created_at timestamptz DEFAULT now())""")
            old=await (await conn.execute(
                "SELECT details FROM risegram_selective_restore_receipts WHERE job=%s",(JOB,)
            )).fetchone()
            if old:
                print("SELECTIVE_RESTORE_ALREADY_CONFIRMED",json.dumps(old["details"]),flush=True)
                return
            peers=await (await conn.execute("""
                SELECT username_lower,peer_id,peer_type FROM peer_usernames
                WHERE username_lower IN ('owner','koyyzz','paths')
            """)).fetchall()
            found={r["username_lower"]:r for r in peers}
            if set(found)!={"owner","koyyzz","paths"} or any(
              found[k]["peer_type"]!="user" or found[k]["peer_id"]!=expect
              for k,expect in (("owner",OWNER_ID),("koyyzz",OWNER_ID),("paths",PATHS_ID))
            ):
                raise RuntimeError("@paths or @owner aliases changed, refusing restoration")
            active=await (await conn.execute("""
                SELECT id FROM users WHERE id IN (%s,%s) AND deleted_at IS NULL
            """,(OWNER_ID,PATHS_ID))).fetchall()
            if len(active)!=2:
                raise RuntimeError("Restoration target account missing")
            present=await (await conn.execute("""
                SELECT id FROM unique_star_gifts WHERE id = ANY(%s::bigint[])
            """,([int(x) for x in owner_ids],))).fetchall()
            if present:
                raise RuntimeError("Existing unique gift ID would collide, refusing archive replay")
            saves_present=await (await conn.execute("""
                SELECT id FROM peer_star_gifts WHERE id=ANY(%s::bigint[])
            """,([int(x) for x in saved_ids],))).fetchall()
            if saves_present:
                raise RuntimeError("Existing saved gift ID would collide, refusing archive replay")
            balance=await (await conn.execute("""
                SELECT balance FROM stars_balances WHERE user_id=%s FOR UPDATE
            """,(PATHS_ID,))).fetchone()
            if not balance:
                raise RuntimeError("@paths Stars wallet missing")
            new_stars=archived_paths_stars+balance["balance"]-RELEASE_STARS
            if new_stars<0:
                raise RuntimeError("Unsafe adjusted balance")
            await conn.execute("""
                UPDATE stars_balances SET balance=%s,granted=true,updated_at=now()
                WHERE user_id=%s
            """,(new_stars,PATHS_ID))

            # Restore original saved gift identities first (without cyclic NFT FK),
            # then the immutable unique NFT rows, and finally join the links.
            await insert_archived(conn,"peer_star_gifts",saved,blank_unique_link=True)
            await insert_archived(conn,"unique_star_gifts",owner_gifts)
            for r in saved:
                await conn.execute("""
                    UPDATE peer_star_gifts SET unique_gift_id=%s WHERE id=%s
                """,(int(r["unique_gift_id"]),int(r["id"])))
            await insert_archived(conn,"star_gift_admin_grant_commands",grants)
            await insert_archived(conn,"star_gift_user_message_refs",links)
            await insert_archived(conn,"star_gift_collection_items",collections)

            # Prevent the reset rating from being re-inflated by 16 historical
            # gifts when the native recompute worker scans the owner next.
            user=await (await conn.execute(
                "SELECT created_at FROM users WHERE id=%s",(OWNER_ID,)
            )).fetchone()
            age=max(0,int((datetime.datetime.now(datetime.timezone.utc)-user["created_at"]).total_seconds()//86400))
            sent=await (await conn.execute("""
                SELECT count(DISTINCT private_message_id)::bigint AS n
                  FROM message_boxes WHERE message_sender_id=%s AND NOT deleted
            """,(OWNER_ID,))).fetchone()
            base=int(sent["n"])+2*age
            delta=min(5000,base+25*len(owner_gifts))-min(5000,base)
            rating=await (await conn.execute("""
                SELECT user_id,manual_component,activity_component,version
                  FROM account_rating WHERE user_id=%s FOR UPDATE
            """,(OWNER_ID,))).fetchone()
            if rating:
                key="risegram-release-20261123-nft-rating-wallet-v1-rating-"+str(OWNER_ID)
                updated=await conn.execute("""
                    UPDATE account_rating_events SET amount=amount-%s
                     WHERE user_id=%s AND command_key=%s AND kind='manual'
                """,(delta,OWNER_ID,key))
                if updated.rowcount!=1:
                    raise RuntimeError("Owner release rating baseline missing")
                await conn.execute("""
                    UPDATE account_rating
                       SET stars=0,level=0,current_level_stars=0,next_level_stars=25,
                           activity_component=activity_component+%s,
                           manual_component=manual_component-%s,
                           pending_stars=0,pending_date=NULL,
                           computed_at=now(),updated_at=now(),version=version+1
                     WHERE user_id=%s
                """,(delta,delta,OWNER_ID))

            wallet=await (await conn.execute("""
                SELECT balance FROM stars_balances WHERE user_id=%s
            """,(PATHS_ID,))).fetchone()
            owned=await (await conn.execute("""
                SELECT COUNT(*)::bigint AS n FROM unique_star_gifts
                 WHERE owner_peer_type='user' AND owner_peer_id=%s AND NOT burned
            """,(OWNER_ID,))).fetchone()
            saved_count=await (await conn.execute("""
                SELECT COUNT(*)::bigint AS n FROM peer_star_gifts
                 WHERE owner_peer_type='user' AND owner_peer_id=%s
                   AND lifecycle_status='active' AND unique_gift_id=ANY(%s::bigint[])
            """,(OWNER_ID,[int(x) for x in owner_ids]))).fetchone()
            if wallet["balance"]!=new_stars or owned["n"]!=16 or saved_count["n"]!=16:
                raise RuntimeError("Selective restoration post-check failed: rolling back")
            result={
                "owner_peer_id":OWNER_ID,"owner_nfts_restored":owned["n"],
                "owner_active_saved_gifts_restored":saved_count["n"],
                "owner_editions":dict(Counter(x["gift_id"] for x in owner_gifts)),
                "owner_grant_receipts_restored":len(grants),
                "owner_profile_collection_entries_restored":len(collections),
                "paths_peer_id":PATHS_ID,"paths_before_release_stars":archived_paths_stars,
                "paths_stars_after_restoration":new_stars,
                "paths_gram_untouched":True,
                "other_accounts_untouched":True
            }
            await conn.execute("""
                INSERT INTO risegram_selective_restore_receipts(job,details)
                VALUES (%s,%s)
            """,(JOB,Jsonb(result)))
        print("SELECTIVE_RESTORE_COMMITTED_VERIFIED",json.dumps(result),flush=True)

if __name__=="__main__":
    asyncio.run(main())
