"""Repair existing native RiseGram/Major marks' icon and peer-specific wording.

Does NOT issue verification to new peers and does NOT alter platform verified flags.
Re-applies only marks already in PostgreSQL using the native admin API.
"""
import asyncio,json,os,stat
from pathlib import Path
import psycopg
from psycopg.rows import dict_row
from aiohttp import ClientSession,ClientTimeout
from issuer_marks import command

SAVE=Path("/data/risegram-verification-marks-before-20261123.json")
RUN="risegram-mark-wording-icon-repair-20261123-v1"

def wording(mark):
    organization=mark["company_name"].strip().lower()
    if organization not in ("risegram","major"):
        return None
    if mark["peer_type"]=="user":
        noun="Аккаунт верифицирован"
        if "RiseGram" in mark["description"] and "Major" in mark["description"]:
            return noun+" организациями «RiseGram» и «Major»."
    elif mark["peer_type"]=="channel":
        noun="Группа верифицирована" if mark["megagroup"] else "Канал верифицирован"
    else:
        return None
    return noun+(
        " официальной компанией «RiseGram»."
        if organization=="risegram" else " организацией «Major»."
    )

async def read_current():
    async with await psycopg.AsyncConnection.connect(os.environ["RISEGRAM_LEDGER_DSN"],row_factory=dict_row,connect_timeout=10) as db:
        return await (await db.execute("""
            SELECT v.peer_type,v.peer_id,v.verifier_bot_id,v.description,
                   v.icon_document_id,s.icon_document_id AS official_icon,
                   s.company_name,s.enabled,
                   COALESCE(c.megagroup,false) AS megagroup,
                   COALESCE(c.broadcast,false) AS broadcast
              FROM custom_verifications v
              JOIN bot_verifier_settings s ON s.bot_id=v.verifier_bot_id
              LEFT JOIN channels c ON v.peer_type='channel' AND c.id=v.peer_id
             ORDER BY v.peer_type,v.peer_id
        """)).fetchall()

async def main():
    all_marks=await read_current()
    expected={}
    for x in all_marks:
        desc=wording(x)
        if not desc or not x["enabled"] or (x["peer_type"]=="channel" and not
            (x["megagroup"] or x["broadcast"])):
            continue
        key=(x["peer_type"],x["peer_id"])
        expected[key]=(desc,x["official_icon"],x["verifier_bot_id"])
    if not SAVE.exists():
        tmp=SAVE.with_suffix(".tmp")
        tmp.write_text(json.dumps(all_marks,default=str,ensure_ascii=False,indent=2))
        os.chmod(tmp,stat.S_IRUSR|stat.S_IWUSR)
        os.replace(tmp,SAVE)
    if not expected:
        raise RuntimeError("No existing verification marks to inspect")
    async with ClientSession(timeout=ClientTimeout(total=18,connect=6),
            headers={"Authorization":"Bearer "+os.environ["MTPROTO_ADMIN_API_TOKEN"]}) as session:
        for mark in all_marks:
            key=(mark["peer_type"],mark["peer_id"])
            if key not in expected:continue
            desc,icon,bot=expected[key]
            if mark["description"]==desc and mark["icon_document_id"]==icon:
                continue
            fields={"peer_type":key[0],"peer_id":key[1],
                "verifier_bot_id":bot,"description":desc}
            try:
                await command(session,"/v1/botverification/marks/grant",
                    f"{RUN}-{key[0]}-{key[1]}-{bot}",fields)
            except ValueError as exc:
                if "CUSTOM_VERIFICATION_TARGET_INVALID" not in str(exc):
                    raise
                # Pre-release marks may outlive deleted or unresolvable peers.
                # Do not recreate the account or break other valid badge updates.
                expected.pop(key,None)
                print("VERIF_MARK_SKIPPED_UNRESOLVABLE",
                    json.dumps({"peer_type":key[0],"peer_id":key[1]}),flush=True)
                continue
            print("VERIF_MARK_REFRESHED",json.dumps({
                "peer_type":key[0],"peer_id":key[1],
                "issuer":mark["company_name"],"description":desc,
                "old_icon":mark["icon_document_id"],
                "expected_icon":icon
            },ensure_ascii=False),flush=True)
    current=await read_current()
    by={(m["peer_type"],m["peer_id"]):m for m in current}
    mismatches=[key for key,(desc,icon,bot) in expected.items()
                if key not in by or by[key]["description"]!=desc
                or by[key]["icon_document_id"]!=icon
                or by[key]["verifier_bot_id"]!=bot]
    if mismatches:raise RuntimeError("Verification mark re-apply mismatch "+str(mismatches[:5]))
    print("VERIF_MARK_REPAIR_CONFIRMED",json.dumps({
        "verified_mark_count":len(expected),
        "supergroups":sum(1 for m in current if m["peer_type"]=="channel" and m["megagroup"]),
        "channels":sum(1 for m in current if m["peer_type"]=="channel" and m["broadcast"]),
        "user_marks":sum(1 for m in current if m["peer_type"]=="user")
    }),flush=True)

if __name__=="__main__":
    asyncio.run(main())
