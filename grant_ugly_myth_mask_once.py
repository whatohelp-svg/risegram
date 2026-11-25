"""One authorized grant batch for @ugly, capped by existing Myth Mask supply."""
import asyncio
import hashlib
import logging
from aiohttp import ClientSession, ClientTimeout
import config
import native_economy as economy

LOG = logging.getLogger("risegram.ugly44")
PREFIX = "operator-20261125-ugly-myth-mask-44-v1"
RECIPIENT = 1780243211
SENDER = 777000
GIFT = 9000000000000173
LIMIT = 44


async def receipts(conn):
    rows = await (await conn.execute("""SELECT c.command_key, s.from_user_id, u.gift_id
        FROM star_gift_admin_grant_commands c
        JOIN peer_star_gifts s ON s.id=c.saved_gift_id
        JOIN unique_star_gifts u ON u.id=c.unique_gift_id
        WHERE c.recipient_user_id=%s AND c.command_key LIKE %s""",
        (RECIPIENT, "admin-gift:"+PREFIX+"-%"))).fetchall()
    if any(r["from_user_id"] != SENDER or r["gift_id"] != GIFT for r in rows):
        raise ValueError("Unexpected grant receipt")
    return {r["command_key"] for r in rows}


async def run():
    try:
        async with economy.transaction() as conn:
            row = await (await conn.execute("""SELECT count(*) AS n FROM users
                WHERE deleted_at IS NULL AND NOT is_bot AND
                ((id=%s AND lower(username)='risegram' AND support) OR
                 (id=%s AND EXISTS (SELECT 1 FROM peer_usernames n WHERE n.peer_type='user' AND n.peer_id=users.id AND n.active AND n.username_lower='ugly')))""", (SENDER,RECIPIENT))).fetchone()
            if row["n"] != 2:
                raise ValueError("Account identity changed")
            done = await receipts(conn)
            if len(done) == LIMIT:
                LOG.info("UGLY44_COMPLETE verified=%s",len(done))
                return
            gift = await (await conn.execute("""SELECT g.enabled,g.availability_remains,r.id,
                r.supply_total-r.issued AS available FROM star_gift_catalog g
                JOIN star_gift_collectible_revisions r ON r.id=g.collectible_revision_id
                JOIN star_gift_catalog_revisions a ON a.id=g.active_revision_id
                WHERE g.gift_id=%s AND a.title='Myth Mask' AND r.status='published'""",
                (GIFT,))).fetchone()
            if not gift or not gift["enabled"]:
                raise ValueError("Myth Mask unavailable")
            remaining = min(LIMIT-len(done),gift["available"],gift["availability_remains"])
            if remaining<=0:
                LOG.warning("UGLY44_STOCK_EXHAUSTED verified=%s",len(done))
                return
            models = await (await conn.execute("""SELECT id FROM star_gift_collectible_models
                WHERE collectible_revision_id=%s AND NOT crafted AND rarity_permille>0""",
                (gift["id"],))).fetchall()
            ids=sorted((m["id"] for m in models),key=lambda mid:hashlib.sha256(f"{PREFIX}:{mid}".encode()).digest())
            if len(ids)<LIMIT:
                raise ValueError("Not enough distinct normal models")
            pending=[(f"{PREFIX}-{i:03d}",ids[i]) for i in range(LIMIT)
                     if "admin-gift:"+f"{PREFIX}-{i:03d}" not in done][:remaining]
        url=config.MTPROTO_ADMIN_API_URL.rstrip("/")+"/v1/gifts/give"
        async with ClientSession(timeout=ClientTimeout(total=60),headers={
            "Authorization":"Bearer "+config.MTPROTO_ADMIN_API_TOKEN}) as session:
            for i,(command,mid) in enumerate(pending):
                body={"command_id":command,"actor":"risegram-owner-operator",
                    "reason":"Owner-requested 44 Myth Mask for verified @ugly alias",
                    "user_id":RECIPIENT,"gift_id":GIFT,"upgrade":True,"model_attribute_id":mid}
                async with session.post(url,json=body) as response:
                    answer=await response.json(content_type=None)
                    if response.status!=200 or answer.get("status")!="completed":
                        raise RuntimeError(f"HTTP {response.status}: {str(answer.get('error') or answer.get('message') or answer.get('code'))[:180]}")
                if (i+1)%10==0:
                    LOG.info("UGLY44_PROGRESS %s/%s",len(done)+i+1,LIMIT)
                await asyncio.sleep(0.2)
        async with economy.transaction() as conn:
            verified=await receipts(conn)
        LOG.info("UGLY44_RESULT verified=%s/%s",len(verified),LIMIT)
    except Exception:
        LOG.exception("UGLY44_STOPPED; inspect native receipts before retry")
