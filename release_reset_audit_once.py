"""Safe read-only preparation: inspect exact release economy/NFT/rating schema and disk availability."""
import asyncio,json,os,shutil,sqlite3,config
import psycopg
from psycopg.rows import dict_row

NEEDLES=("gift","collectible","rating","stars_balance","ton_balance",
         "stars_transaction","ton_transaction","wallet","ledger","nft",
         "gram","economy")
async def main():
    d=shutil.disk_usage("/data")
    print("RESET_AUDIT_APP_DISK",json.dumps({"total":d.total,"used":d.used,"free":d.free}),flush=True)
    with sqlite3.connect("file:"+config.DB_PATH+"?mode=ro",uri=True) as db:
        users=db.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        t=db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
        names=[v[0] for v in t]
        interesting=[x for x in names if any(k in x.lower() for k in NEEDLES)]
        print("RESET_AUDIT_SQLITE",json.dumps({"users":users,"tables":interesting,
           "file_bytes":os.path.getsize(config.DB_PATH)}),flush=True)
        for table in interesting[:65]:
            if not table.replace("_","").isalnum():continue
            cols=db.execute("PRAGMA table_info("+table+")").fetchall()
            count=db.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            print("RESET_AUDIT_SQLITE_TABLE",json.dumps({"name":table,"rows":count,
                 "cols":[c[1] for c in cols]}),flush=True)
        print("RESET_AUDIT_SQLITE_BALANCES",json.dumps(db.execute(
            "SELECT COUNT(*),COALESCE(MIN(risestars),0),COALESCE(MAX(risestars),0),COALESCE(MIN(gram_balance),0),COALESCE(MAX(gram_balance),0) FROM users").fetchone()),flush=True)
    async with await psycopg.AsyncConnection.connect(os.environ["RISEGRAM_LEDGER_DSN"],row_factory=dict_row,connect_timeout=12) as db:
        await db.execute("SET TRANSACTION READ ONLY")
        data=await (await db.execute("""
            SELECT relname,n_live_tup::bigint AS approx_rows, pg_total_relation_size(relid) AS bytes
              FROM pg_stat_user_tables
             WHERE schemaname='public' AND
                (relname ~* 'gift|collectible|rating|stars_balance|ton_balance|stars_transaction|ton_transaction|wallet|ledger|nft|gram|economy')
             ORDER BY relname
        """)).fetchall()
        print("RESET_AUDIT_PG_TABLES",json.dumps(data,default=str),flush=True)
        cols=await (await db.execute("""
            SELECT table_name,column_name,data_type,column_default,is_nullable
              FROM information_schema.columns
             WHERE table_schema='public'
               AND (table_name ~* 'gift|collectible|rating|stars_balance|ton_balance|stars_transaction|ton_transaction|wallet|ledger|nft|gram|economy')
             ORDER BY table_name,ordinal_position
        """)).fetchall()
        by_table={}
        for c in cols:by_table.setdefault(c["table_name"],[]).append({
            "name":c["column_name"],"type":c["data_type"],
            "default":c["column_default"],"nullable":c["is_nullable"]})
        print("RESET_AUDIT_PG_COLUMNS",json.dumps(by_table,default=str),flush=True)
        fks=await (await db.execute("""
            SELECT conrelid::regclass::text AS child,
                   confrelid::regclass::text AS parent,
                   conname, pg_get_constraintdef(oid) AS def
              FROM pg_constraint
             WHERE contype='f'
             AND (conrelid::regclass::text ~* 'gift|rating|balance|transaction|wallet|collectible'
                  OR confrelid::regclass::text ~* 'gift|rating|balance|transaction|wallet|collectible')
             ORDER BY parent,child,conname
        """)).fetchall()
        print("RESET_AUDIT_PG_FKS",json.dumps(fks,default=str),flush=True)
        users=await (await db.execute("""
            SELECT COUNT(*)::bigint AS total,
                   COUNT(*) FILTER(WHERE is_bot)::bigint AS bots,
                   COUNT(*) FILTER(WHERE deleted_at IS NULL)::bigint AS active
              FROM users
        """)).fetchone()
        wallet=await (await db.execute("""
            SELECT
              (SELECT COUNT(*) FROM stars_balances)::bigint AS stars_rows,
              (SELECT COUNT(*) FROM ton_balances)::bigint AS ton_rows,
              (SELECT COALESCE(MIN(balance),0) FROM stars_balances)::bigint AS stars_min,
              (SELECT COALESCE(MAX(balance),0) FROM stars_balances)::bigint AS stars_max,
              (SELECT COALESCE(MIN(balance_nanoton),0) FROM ton_balances)::bigint AS gram_min_nano,
              (SELECT COALESCE(MAX(balance_nanoton),0) FROM ton_balances)::bigint AS gram_max_nano
        """)).fetchone()
        print("RESET_AUDIT_PG_USERS_WALLETS",json.dumps({"users":users,"wallet":wallet},default=str),flush=True)
if __name__=="__main__":
    asyncio.run(main())
