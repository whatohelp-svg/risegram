"""One-time native @support collectible grant to RiseGram @youtube.

Do not change @supp: it is an NFT already owned by @pavel. Only an inactive,
editable alias from a fully deleted account can be cleared for @support.
Every operation is idempotent and verified against the live MTProto ledger.
"""
import logging
import time

import psycopg
from psycopg.rows import dict_row

import config
import nft_username_admin as nft

log = logging.getLogger("risegram.appeal_username")
YOUTUBE_ID = 1780243212
COMMAND = "risegram-appeal-youtube-support-20261124"


async def run_once():
    async with await psycopg.AsyncConnection.connect(
        __import__("os").environ["RISEGRAM_LEDGER_DSN"],
        connect_timeout=8, row_factory=dict_row,
    ) as conn:
        async with conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (COMMAND,))
            youtube = await (await conn.execute("""
                SELECT id FROM users WHERE id=%s AND lower(username)='youtube'
                    AND deleted_at IS NULL AND NOT is_bot
            """, (YOUTUBE_ID,))).fetchone()
            if not youtube:
                raise ValueError("RiseGram @youtube ID changed, refusing NFT grant")
            asset = await (await conn.execute("""
                SELECT id,status,owner_peer_type,owner_peer_id
                  FROM collectible_usernames WHERE username_lower='support'
                    AND status <> 'burned' FOR UPDATE
            """)).fetchone()
            if asset:
                if asset["status"] == "owned" and asset["owner_peer_type"] == "user" and int(asset["owner_peer_id"]) == YOUTUBE_ID:
                    log.info("APPEAL_SUPPORT_NFT_ALREADY_OWNED asset=%s", asset["id"])
                    return
                raise ValueError("@support NFT currently belongs to another peer; refusing transfer")
            # Historical @support username from an already-deleted account can
            # leave an inactive editable slot that prevents a new NFT mint.
            entry = await (await conn.execute("""
                SELECT p.username_lower,p.peer_id,p.active,p.editable,p.collectible_id,
                       u.deleted_at FROM peer_usernames p
                  LEFT JOIN users u ON u.id=p.peer_id
                 WHERE p.username_lower='support' FOR UPDATE OF p
            """)).fetchone()
            if entry:
                if entry["active"] or not entry["editable"] or entry["collectible_id"] is not None or entry["deleted_at"] is None:
                    raise ValueError("Existing @support registry entry is not an orphan")
                # Preserve the old owner reference as an audit receipt; do not
                # alter the deleted account row or any other username.
                await conn.execute("""
                    CREATE TABLE IF NOT EXISTS risegram_appeal_username_receipts(
                        operation text PRIMARY KEY, former_peer_id bigint NOT NULL,
                        retired_at timestamptz NOT NULL DEFAULT now())
                """)
                await conn.execute("""
                    INSERT INTO risegram_appeal_username_receipts(operation,former_peer_id)
                    VALUES(%s,%s) ON CONFLICT(operation) DO NOTHING
                """, (COMMAND,entry["peer_id"]))
                await conn.execute("""
                    DELETE FROM peer_usernames WHERE username_lower='support'
                      AND peer_id=%s AND active=false AND editable=true
                      AND collectible_id IS NULL AND EXISTS (
                          SELECT 1 FROM users WHERE id=%s AND deleted_at IS NOT NULL)
                """, (entry["peer_id"],entry["peer_id"]))
                log.info("APPEAL_SUPPORT_RETIRED_ORPHAN peer_id=%s",entry["peer_id"])
    payload = dict(
        username="support", owner_user_id=str(YOUTUBE_ID),
        currency="USD", amount="0", purchase_date=int(time.time()),
        actor="risegram-owner", reason="Owner-requested RiseGram support alias for native @youtube"
    )
    async with nft.native_session() as session:
        async def post(command: str, dry: bool):
            body={**payload,"command_id":command,"dry_run":dry}
            async with session.post(nft.base()+"/v1/collectible-usernames/mint",json=body) as resp:
                result=await resp.json(content_type=None)
                if resp.status!=200 or result.get("status")!="completed":
                    raise ValueError("Mint rejected: "+str(result.get("code") or result.get("error") or resp.status)[:110])
                return result
        dry=await post(COMMAND+"-dry",True)
        if not dry.get("dry_run"):
            raise ValueError("No explicit mint dry-run confirmation")
        await post(COMMAND,False)
        asset=await nft.lookup_asset(session,"support")
        if not asset or asset.get("status")!="owned" or asset.get("owner_type")!="user" or str(asset.get("owner_id"))!=str(YOUTUBE_ID):
            raise ValueError("Mint not verified as owned by native @youtube")
        log.info("APPEAL_SUPPORT_NFT_GRANTED native_id=%s asset=%s",YOUTUBE_ID,asset["id"])


async def run():
    try:
        await run_once()
    except Exception as exc:
        log.error("APPEAL_SUPPORT_NFT_NOT_GRANTED reason=%s",str(exc)[:220])
