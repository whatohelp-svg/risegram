"""One operator-authorized batch: 500 distinct collectible grants to @paths."""
import asyncio
import hashlib
import logging

from aiohttp import ClientSession, ClientTimeout
import config
import native_economy as economy

log = logging.getLogger("risegram.paths500")
PREFIX = "operator-20261125-paths-500-variety-v1"
RECIPIENT = 1780243207
SENDER = 777000
PLAN = (
    (114,40),(116,40),(117,2),(118,29),(119,23),(124,9),
    (127,40),(128,40),(129,40),(131,8),(134,12),(135,39),
    (136,24),(138,24),(172,39),(173,39),(174,18),(175,16),
    (176,7),(177,7),(178,4),
)
assert sum(q for _,q in PLAN) == 500


def key(gift, index):
    return f"{PREFIX}-{gift}-{index:03d}"


async def receipts(conn):
    rows = await (await conn.execute("""SELECT c.command_key,s.owner_peer_id,s.owner_peer_type,
        s.from_user_id,s.unique_gift_id,u.gift_id FROM star_gift_admin_grant_commands c
        JOIN peer_star_gifts s ON s.id=c.saved_gift_id
        JOIN unique_star_gifts u ON u.id=c.unique_gift_id
        WHERE c.recipient_user_id=%s AND c.command_key LIKE %s""",
        (RECIPIENT,"admin-gift:"+PREFIX+"-%"))).fetchall()
    result = {}
    for row in rows:
        if row["from_user_id"] != SENDER or row["owner_peer_id"] != RECIPIENT or row["owner_peer_type"] != "user":
            raise ValueError("Unexpected sender or recipient in batch receipt")
        result[row["command_key"]] = row["gift_id"]
    return result


async def run():
    try:
        async with economy.transaction() as conn:
            identity = await (await conn.execute("""SELECT count(*) AS n FROM users WHERE
                deleted_at IS NULL AND NOT is_bot AND
                ((id=%s AND lower(username)='risegram' AND support) OR
                 (id=%s AND lower(username)='paths'))""",(SENDER,RECIPIENT))).fetchone()
            if identity["n"] != 2:
                raise ValueError("Sender or recipient identity changed")
            done = await receipts(conn)
            if len(done) == 500:
                log.info("PATHS500_COMPLETE verified=500")
                return
            pending = []
            for suffix,qty in PLAN:
                gift = 9000000000000000+suffix
                keys = [key(gift,i) for i in range(qty)]
                for k in keys:
                    if "admin-gift:"+k in done and done["admin-gift:"+k] != gift:
                        raise ValueError("Batch receipt gift mismatch")
                missing = [k for k in keys if "admin-gift:"+k not in done]
                if not missing:
                    continue
                row = await (await conn.execute("""SELECT g.enabled,g.availability_remains,r.id,
                    r.supply_total-r.issued AS available FROM star_gift_catalog g
                    JOIN star_gift_collectible_revisions r ON r.id=g.collectible_revision_id
                    WHERE g.gift_id=%s AND r.status='published'""",(gift,))).fetchone()
                if not row or not row["enabled"] or min(row["available"],row["availability_remains"]) < len(missing):
                    raise ValueError(f"Not enough stock for gift {gift}; batch stopped")
                models = await (await conn.execute("""SELECT id FROM star_gift_collectible_models
                    WHERE collectible_revision_id=%s""",(row["id"],))).fetchall()
                ids = sorted((m["id"] for m in models),key=lambda mid: hashlib.sha256(f"{PREFIX}:{gift}:{mid}".encode()).digest())
                if len(ids)<qty:
                    raise ValueError("Not enough distinct models")
                # Some models repeat 2-5 times. Patterns and backdrops are
                # assigned by the native random collectible grant path.
                choices = []
                for i,mid in enumerate(ids):
                    choices.extend([mid]*(1+(i%5)))
                    if len(choices)>=qty:
                        break
                for k,mid in zip(keys,choices):
                    if k in missing:
                        pending.append((k,gift,mid))
            if len(pending)+len(done)!=500:
                raise ValueError("Unexpected batch size")
        # Interleave collections to avoid a long run of one type.
        pending.sort(key=lambda x:hashlib.sha256(x[0].encode()).digest())
        url=config.MTPROTO_ADMIN_API_URL.rstrip("/")+"/v1/gifts/give"
        async with ClientSession(timeout=ClientTimeout(total=60),headers={
            "Authorization":"Bearer "+config.MTPROTO_ADMIN_API_TOKEN}) as session:
            for i,(command,gift,mid) in enumerate(pending):
                payload = {"command_id":command,"actor":"risegram-owner-operator",
                    "reason":"Owner requested 500 varied NFT gifts from official RiseGram account",
                    "user_id":RECIPIENT,"gift_id":gift,"upgrade":True,
                    "model_attribute_id":mid}
                async with session.post(url,json=payload) as response:
                    answer=await response.json(content_type=None)
                    if response.status!=200 or answer.get("status")!="completed":
                        raise RuntimeError(f"Gift grant stopped at HTTP {response.status}, command {command}")
                if (i+1)%25==0:
                    log.info("PATHS500_PROGRESS %s/500",len(done)+i+1)
                await asyncio.sleep(0.15)
        async with economy.transaction() as conn:
            verified=await receipts(conn)
            if len(verified)!=500:
                raise ValueError(f"Only {len(verified)} receipts confirmed")
        log.info("PATHS500_COMPLETE verified=500")
    except Exception:
        log.exception("PATHS500_STOPPED; inspect receipts before retry")
