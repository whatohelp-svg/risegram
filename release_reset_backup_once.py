"""Snapshot release-related rows to RiseGram's persistent /data, before ANY cleanup.

Backups are private on the mounted volume. This module performs NO database writes.
"""
import asyncio,gzip,hashlib,json,os,shutil,sqlite3,time
from pathlib import Path
import psycopg
from psycopg import sql
import config

ROOT=Path("/data/release-reset-backup-20261123")
PG_TABLES=[
  "users","stars_balances","ton_balances","stars_transactions","ton_transactions",
  "account_rating","account_rating_events",
  "peer_star_gifts","unique_star_gifts","star_gift_admin_grant_commands",
  "star_gift_auction_acquired","star_gift_auction_bids","star_gift_auction_bid_payments",
  "star_gift_auctions","star_gift_channel_notification_jobs","star_gift_claim_challenges",
  "star_gift_claim_history","star_gift_collection_items","star_gift_collections",
  "star_gift_conversions","star_gift_craft_commands","star_gift_drop_details_commands",
  "star_gift_listings","star_gift_offers","star_gift_prepaid_upgrade_commands",
  "star_gift_purchase_commands","star_gift_purchase_forms","star_gift_sales",
  "star_gift_transfer_commands","star_gift_upgrade_commands",
  "star_gift_user_message_refs","star_gift_user_purchases",
  "star_gift_withdrawal_requests","wallet_gift_transfers",
  "star_gift_catalog","star_gift_collectible_revisions",
  "risegram_bot_receipts","risegram_bot_games",
]

def digest(path):
    h=hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda:f.read(1024*1024),b""):h.update(block)
    return h.hexdigest()

def verify():
    manifest=ROOT/"manifest.json"
    if not manifest.is_file():return False
    raw=json.loads(manifest.read_text())
    for item in raw["files"]:
        p=ROOT/item["file"]
        if not p.is_file() or digest(p)!=item["sha256"]:
            raise RuntimeError("Corrupted pre-reset backup: "+item["file"])
    print("RELEASE_BACKUP_VERIFIED",json.dumps({
        "path":str(ROOT),"files":len(raw["files"]),
        "total_bytes":sum(x["bytes"] for x in raw["files"])
    }),flush=True)
    return True

async def main():
    if verify():return
    ROOT.mkdir(parents=True,exist_ok=True)
    files=[]
    src=sqlite3.connect("file:"+config.DB_PATH+"?mode=ro",uri=True)
    try:
        dest=sqlite3.connect(str(ROOT/"risegram.sqlite.tmp"))
        try:
            src.backup(dest)
        finally:dest.close()
    finally:src.close()
    final=ROOT/"risegram.sqlite"
    os.replace(ROOT/"risegram.sqlite.tmp",final)
    files.append({"file":final.name,"sha256":digest(final),"bytes":final.stat().st_size})
    async with await psycopg.AsyncConnection.connect(os.environ["RISEGRAM_LEDGER_DSN"],connect_timeout=12) as conn:
        await conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        for name in PG_TABLES:
            tmp=ROOT/(name+".csv.gz.tmp")
            target=ROOT/(name+".csv.gz")
            stmt=sql.SQL("COPY (SELECT * FROM {}) TO STDOUT WITH CSV HEADER").format(sql.Identifier("public",name))
            with gzip.open(tmp,"wb",compresslevel=6) as sink:
                async with conn.cursor() as cur:
                    async with cur.copy(stmt) as copy:
                        async for block in copy:
                            sink.write(bytes(block) if not isinstance(block,str) else block.encode())
            os.replace(tmp,target)
            files.append({"file":target.name,"sha256":digest(target),"bytes":target.stat().st_size})
    total=sum(x["bytes"] for x in files)
    d=shutil.disk_usage("/data")
    if d.free<100*1024*1024:
        raise RuntimeError("Refusing release reset: backup volume less than 100MB free")
    result={"created_at":int(time.time()),"files":files,"count":len(files),
            "total_bytes":total,"note":"Do not delete; recoverable pre-release asset, economy and rating snapshot"}
    tmp=ROOT/"manifest.json.tmp"
    tmp.write_text(json.dumps(result,sort_keys=True,indent=2))
    os.replace(tmp,ROOT/"manifest.json")
    verify()

if __name__=="__main__":
    asyncio.run(main())
