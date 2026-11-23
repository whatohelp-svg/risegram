"""One-time release reset: remove native collectible NFT gifts, zero account ratings,
and set each existing human wallet to 1000 RiseStars / 10 GRAM.

DO NOT DELETE USERS, USERS' USERNAMES, LOGIN SESSIONS OR CHATS.
All removals occur after verifying private compressed snapshots in /data.
Idempotency is stored in the PostgreSQL commit and in SQLite.
"""
import asyncio,datetime,json,os,sqlite3
from collections import Counter
import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
import config
from release_reset_backup_once import verify as verify_backup

JOB="risegram-release-20261123-nft-rating-wallet-v1"
STARS=1000
GRAM_NANO=10_000_000_000
SYSTEM_IDS=(777000,93372553,1063110917,1250000007,1250000011,1250000013)

# Explicit allowlist of FKs to minted NFT identities, never any account or chat table.
CHILDREN=[
  ("star_gift_admin_grant_commands","unique_gift_id = ANY(%s) OR saved_gift_id = ANY(%s)","both"),
  ("star_gift_auction_acquired","saved_gift_id = ANY(%s)","saved"),
  ("star_gift_channel_notification_jobs","saved_gift_id = ANY(%s)","saved"),
  ("star_gift_collection_items","saved_gift_id = ANY(%s)","saved"),
  ("star_gift_conversions","saved_gift_id = ANY(%s)","saved"),
  ("star_gift_drop_details_commands","unique_gift_id = ANY(%s) OR saved_gift_id = ANY(%s)","both"),
  ("star_gift_prepaid_upgrade_commands","saved_gift_id = ANY(%s)","saved"),
  ("star_gift_purchase_commands","saved_gift_id = ANY(%s)","saved"),
  ("star_gift_upgrade_commands","unique_gift_id = ANY(%s) OR source_saved_gift_id = ANY(%s)","both"),
  ("star_gift_user_message_refs","saved_gift_id = ANY(%s)","saved"),
  ("star_gift_claim_challenges","unique_gift_id = ANY(%s)","unique"),
  ("star_gift_claim_history","unique_gift_id = ANY(%s)","unique"),
  ("star_gift_craft_commands",
   "result_unique_gift_id = ANY(%s) OR input_unique_gift_ids && %s::bigint[]","double_unique"),
  ("star_gift_listings","unique_gift_id = ANY(%s)","unique"),
  ("star_gift_offers","unique_gift_id = ANY(%s)","unique"),
  ("star_gift_sales","unique_gift_id = ANY(%s)","unique"),
  ("star_gift_transfer_commands","unique_gift_id = ANY(%s)","unique"),
  ("star_gift_withdrawal_requests","unique_gift_id = ANY(%s)","unique"),
  ("wallet_gift_transfers","unique_gift_id = ANY(%s)","unique"),
]

def baseline_score(positive,negative,messages,age_days,gifts):
    stars=max(0,positive)+max(0,negative)*250//1000
    activity=min(5000,max(0,messages)+max(0,age_days)*2+max(0,gifts)*25)
    return stars,activity

async def run_postgres():
    async with await psycopg.AsyncConnection.connect(
        os.environ["RISEGRAM_LEDGER_DSN"],autocommit=True,row_factory=dict_row,connect_timeout=12
    ) as db:
        async with db.transaction():
            await db.execute("SET LOCAL lock_timeout='8s'")
            await db.execute("SET LOCAL statement_timeout='120s'")
            await db.execute("SELECT pg_advisory_xact_lock(hashtext(%s))",(JOB,))
            await db.execute("""CREATE TABLE IF NOT EXISTS risegram_release_reset_receipts
                (job text PRIMARY KEY,state jsonb NOT NULL,created_at timestamptz DEFAULT now())""")
            receipt=await (await db.execute(
                "SELECT state FROM risegram_release_reset_receipts WHERE job=%s",(JOB,)
            )).fetchone()
            if receipt:
                print("RELEASE_RESET_PG_ALREADY_COMMITTED",json.dumps(receipt["state"],default=str),flush=True)
                return receipt["state"]
            # Identity proof: these identifiers must survive exactly as they were.
            before=await (await db.execute("""
                SELECT id,phone,username,is_bot,deleted_at FROM users ORDER BY id
            """)).fetchall()
            baseline_identities=[dict(x) for x in before]
            before_legacy=await (await db.execute("SELECT COUNT(*)::bigint AS n FROM collectible_usernames")).fetchone()
            if not before:raise RuntimeError("Cannot reset: native accounts missing")
            eligible=await (await db.execute("""
                SELECT id,created_at FROM users
                 WHERE deleted_at IS NULL AND NOT is_bot
                   AND id <> ALL(%s::bigint[])
                 ORDER BY id
            """,(list(SYSTEM_IDS),))).fetchall()
            if not eligible:raise RuntimeError("No human accounts found; refusing reset")

            urows=await (await db.execute("SELECT id,gift_id,source_saved_gift_id FROM unique_star_gifts")).fetchall()
            unique_ids=[x["id"] for x in urows]
            saved_ids=list({x["source_saved_gift_id"] for x in urows})
            if unique_ids:
                extra=await (await db.execute("""
                    SELECT id FROM peer_star_gifts WHERE unique_gift_id = ANY(%s::bigint[])
                """,(unique_ids,))).fetchall()
                saved_ids=list(set(saved_ids)|{x["id"] for x in extra})
            saved_ids.sort()
            counts=Counter(x["gift_id"] for x in urows)
            print("RELEASE_RESET_PRECHECK",json.dumps({
                "native_accounts":len(before),"eligible_humans":len(eligible),
                "unique_nft_rows":len(unique_ids),"related_saved_gift_rows":len(saved_ids),
                "collectible_username_records_preserved":before_legacy["n"]
            }),flush=True)
            deleted={}
            if unique_ids:
                await db.execute("""
                    UPDATE users SET emoji_status_collectible_id=NULL
                     WHERE emoji_status_collectible_id = ANY(%s::bigint[])
                """,(unique_ids,))
                # Remove links and command receipts that would otherwise FK-restrict
                # deletion of minted gifts; unrelated gifts remain intact.
                for table,predicate,mode in CHILDREN:
                    parameters=(
                        (unique_ids,saved_ids) if mode=="both" else
                        (saved_ids,) if mode=="saved" else
                        (unique_ids,unique_ids) if mode=="double_unique" else
                        (unique_ids,)
                    )
                    row=await db.execute(f"DELETE FROM {table} WHERE {predicate}",parameters)
                    deleted[table]=row.rowcount
                await db.execute("""
                    UPDATE peer_star_gifts SET unique_gift_id=NULL
                     WHERE unique_gift_id = ANY(%s::bigint[])
                """,(unique_ids,))
                d=await db.execute("DELETE FROM unique_star_gifts WHERE id=ANY(%s::bigint[])",(unique_ids,))
                deleted["unique_star_gifts"]=d.rowcount
                d=await db.execute("DELETE FROM peer_star_gifts WHERE id=ANY(%s::bigint[])",(saved_ids,))
                deleted["peer_star_gifts"]=d.rowcount
                if deleted["unique_star_gifts"]!=len(unique_ids):
                    raise RuntimeError("Unique NFT deletion count mismatch")
            await db.execute("""
                UPDATE star_gift_collectible_revisions SET issued=0 WHERE issued<>0
            """)
            # Users should be eligible to buy new collectible gifts after the reset.
            # Restore only the stock consumed by those removed unique gifts,
            # never override the stock of unrelated normal gifts.
            for gift_id,used in counts.items():
                await db.execute("""
                    UPDATE star_gift_catalog AS c
                       SET availability_remains=LEAST(
                           r.availability_total,c.availability_remains+%s),
                           updated_at=now()
                      FROM star_gift_catalog_revisions AS r
                     WHERE c.gift_id=%s
                       AND r.id=c.active_revision_id
                       AND r.availability_total>0
                """,(used,gift_id))
            # Per-user collectible gift purchase caps also start anew.
            await db.execute("""
                DELETE FROM star_gift_user_purchases
                 WHERE gift_id IN (SELECT gift_id FROM star_gift_collectible_revisions)
            """)

            # Only real, active people; never official/system/bot accounts.
            # Mark both grants applied to prevent default lazy grants doubling
            # the requested 1000/10 on first login.
            await db.execute("""
                INSERT INTO stars_balances(user_id,balance,granted,updated_at)
                SELECT id,%s,true,now() FROM users
                 WHERE deleted_at IS NULL AND NOT is_bot
                   AND id <> ALL(%s::bigint[])
                ON CONFLICT(user_id) DO UPDATE
                   SET balance=EXCLUDED.balance,granted=true,updated_at=now()
            """,(STARS,list(SYSTEM_IDS)))
            await db.execute("""
                INSERT INTO ton_balances(user_id,balance_nanoton,granted,updated_at)
                SELECT id,%s,true,now() FROM users
                 WHERE deleted_at IS NULL AND NOT is_bot
                   AND id <> ALL(%s::bigint[])
                ON CONFLICT(user_id) DO UPDATE
                   SET balance_nanoton=EXCLUDED.balance_nanoton,granted=true,updated_at=now()
            """,(GRAM_NANO,list(SYSTEM_IDS)))

            # The rating is automatically recomputed from historic stars transactions
            # and messages, so deletion alone would regenerate pre-release ratings.
            # Cancel ONLY existing positive contribution as a signed baseline event.
            # This keeps original payment/audit records and preserves safety penalties;
            # activity accrued after release can earn rating normally.
            await db.execute("DELETE FROM account_rating_events")
            await db.execute("DELETE FROM account_rating")
            now=datetime.datetime.now(datetime.timezone.utc)
            for user in eligible:
                uid=user["id"]
                sums=await (await db.execute("""
                    SELECT COALESCE(sum(amount) FILTER(WHERE amount>0),0)::bigint AS plus,
                           COALESCE(-sum(amount) FILTER(WHERE amount<0),0)::bigint AS minus
                      FROM stars_transactions WHERE user_id=%s
                """,(uid,))).fetchone()
                messages=await (await db.execute("""
                    SELECT count(DISTINCT private_message_id)::bigint AS n
                      FROM message_boxes
                     WHERE message_sender_id=%s AND NOT deleted
                """,(uid,))).fetchone()
                gifts=await (await db.execute("""
                    SELECT count(*)::bigint AS n FROM peer_star_gifts
                     WHERE owner_peer_type='user' AND owner_peer_id=%s
                       AND lifecycle_status='active'
                """,(uid,))).fetchone()
                penalties=await (await db.execute("""
                    SELECT count(*)::bigint AS n FROM moderation_cases
                     WHERE target_peer_type='user' AND target_peer_id=%s
                       AND status IN ('action_pending','action_failed','resolved')
                """,(uid,))).fetchone()
                flags=await (await db.execute("SELECT scam,fake FROM users WHERE id=%s",(uid,))).fetchone()
                age=max(0,int((now-user["created_at"]).total_seconds()//86400))
                star_score,activity_score=baseline_score(int(sums["plus"]),int(sums["minus"]),
                    int(messages["n"]),age,int(gifts["n"]))
                penalty=150*penalties["n"]+5000*int(flags["scam"])+5000*int(flags["fake"])
                baseline=-(star_score+activity_score)
                if not -(2**63)<baseline<2**63:
                    raise RuntimeError("Unsafe rating baseline magnitude")
                if baseline:
                    await db.execute("""
                        INSERT INTO account_rating_events(
                          user_id,kind,amount,reason,actor,command_key,created_at)
                        VALUES (%s,'manual',%s,%s,%s,%s,now())
                    """,(uid,baseline,"Release rating history baseline",
                         "risegram-release-reset",JOB+"-rating-"+str(uid)))
                await db.execute("""
                    INSERT INTO account_rating(
                        user_id,level,stars,current_level_stars,next_level_stars,
                        stars_component,activity_component,penalty_component,
                        manual_component,pending_stars,pending_date,computed_at,updated_at,version)
                    VALUES (%s,0,0,0,25,%s,%s,%s,%s,0,NULL,now(),now(),1)
                """,(uid,star_score,activity_score,penalty,baseline))

            # Do not carry an old active bot bet across the one-time balance reset.
            games=await db.execute("""
                UPDATE risegram_bot_games
                   SET status='refunded',payout=0 WHERE status='active'
            """)

            after=await (await db.execute("""
                SELECT id,phone,username,is_bot,deleted_at FROM users ORDER BY id
            """)).fetchall()
            if [dict(x) for x in after]!=baseline_identities:
                raise RuntimeError("Account identity changed; rolling back all PG operations")
            check=await (await db.execute("""
                SELECT
                  (SELECT COUNT(*) FROM unique_star_gifts)::bigint AS nft,
                  (SELECT COUNT(*) FROM account_rating
                    WHERE stars<>0 OR level<>0 OR pending_stars<>0)::bigint AS rated,
                  (SELECT COUNT(*) FROM account_rating)::bigint AS ratings,
                  (SELECT COUNT(*) FROM users)::bigint AS accounts
            """)).fetchone()
            wallets=await (await db.execute("""
                SELECT COUNT(*)::bigint AS n FROM users u
                JOIN stars_balances s ON s.user_id=u.id
                JOIN ton_balances t ON t.user_id=u.id
                WHERE u.deleted_at IS NULL AND NOT u.is_bot
                  AND u.id <> ALL(%s::bigint[])
                  AND s.balance=%s AND s.granted
                  AND t.balance_nanoton=%s AND t.granted
            """,(list(SYSTEM_IDS),STARS,GRAM_NANO))).fetchone()
            usernames=await (await db.execute(
                "SELECT COUNT(*)::bigint AS n FROM collectible_usernames")).fetchone()
            if check["nft"] or check["rated"] or check["ratings"]!=len(eligible) or check["accounts"]!=len(before) or wallets["n"]!=len(eligible) or usernames["n"]!=before_legacy["n"]:
                raise RuntimeError("Release reset safety postcheck failed; rolling back")
            outcome={
                "accounts_preserved":check["accounts"],"eligible_human_wallets":wallets["n"],
                "native_unique_nft_removed":len(unique_ids),"saved_nft_rows_removed":deleted.get("peer_star_gifts",0),
                "ratings_set_to_zero":check["ratings"],
                "username_nft_records_preserved":usernames["n"],
                "bot_games_closed":games.rowcount,
                "deleted_linked_rows":deleted,
            }
            await db.execute("""
                INSERT INTO risegram_release_reset_receipts(job,state)
                VALUES (%s,%s)
            """,(JOB,Jsonb(outcome)))
        print("RELEASE_RESET_PG_COMMITTED",json.dumps(outcome,default=str),flush=True)
        return outcome

def run_sqlite():
    db=sqlite3.connect(config.DB_PATH,timeout=40)
    try:
        db.execute("PRAGMA busy_timeout=40000")
        db.execute("BEGIN IMMEDIATE")
        db.execute("""CREATE TABLE IF NOT EXISTS risegram_release_reset_receipts
            (job TEXT PRIMARY KEY,created_at TEXT NOT NULL)""")
        old=db.execute("SELECT job FROM risegram_release_reset_receipts WHERE job=?",(JOB,)).fetchone()
        if old:
            db.rollback()
            print("RELEASE_RESET_SQLITE_ALREADY_COMMITTED",flush=True)
            return
        schema=[x[1] for x in db.execute("PRAGMA table_info(users)").fetchall()]
        before=db.execute("SELECT * FROM users ORDER BY tg_id").fetchall()
        fixed=[tuple(v for i,v in enumerate(row) if schema[i] not in ("risestars","gram_balance")) for row in before]
        count=len(before)
        # Account records, links, and verification fields are preserved.
        db.execute("UPDATE users SET risestars=?,gram_balance=?",(float(STARS),10.0))
        # Retire only local collectible-gift instances, not purchased NFT
        # usernames/phone numbers which are account identity assets.
        legacy_gifts=db.execute("DELETE FROM rg_gift_instances").rowcount
        db.execute("DELETE FROM rg_gift_orders")
        local_nfts=db.execute("""
            DELETE FROM user_nft_inventory
             WHERE lower(item_type) IN ('gift','nft_gift','collectible_gift','telegram_gift')
        """).rowcount
        db.execute("UPDATE rg_gift_drops SET sold=0 WHERE sold<>0")
        after=db.execute("SELECT * FROM users ORDER BY tg_id").fetchall()
        keep=[tuple(v for i,v in enumerate(row) if schema[i] not in ("risestars","gram_balance")) for row in after]
        if len(after)!=count or keep!=fixed or any(row[schema.index("risestars")]!=float(STARS) or row[schema.index("gram_balance")]!=10.0 for row in after):
            raise RuntimeError("SQLite account preservation and wallet verification failed")
        db.execute("""
            INSERT INTO risegram_release_reset_receipts(job,created_at)
            VALUES (?,datetime('now'))
        """,(JOB,))
        db.commit()
        print("RELEASE_RESET_SQLITE_COMMITTED",json.dumps({
            "accounts_preserved":count,"balances_risestars":STARS,"balances_gram":10,
            "legacy_gift_instances_removed":legacy_gifts,"legacy_gift_nfts_removed":local_nfts
        }),flush=True)
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

async def main():
    if not verify_backup():
        raise RuntimeError("No verified release backup; refusing any changes")
    await run_postgres()
    run_sqlite()
    print("RELEASE_RESET_ALL_VERIFIED",json.dumps({"stars":STARS,"gram":10,"accounts_not_deleted":True}),flush=True)

if __name__=="__main__":
    asyncio.run(main())
