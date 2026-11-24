"""One-time owner-authorized official RiseGram support account presentation.

Native @youtube owns the collectible alias @support. Mark that *same* account as
official support, set a clear profile name and replace its old personal bio.
Do not touch other users, usernames, gifts, balances or sessions.
"""
import logging
import os

import psycopg
from psycopg.rows import dict_row
from aiohttp import ClientSession, ClientTimeout

import config

log = logging.getLogger("risegram.support_branding")
USER_ID = 1780243212
NAME = "🛟 Поддержка RiseGram"
BIO = (
    "Официальная поддержка RiseGram. Апелляции по заморозке, "
    "помощь с входом, подарками и жалобами. "
    "Никогда не просим коды, пароли или деньги."
)
KEY = "rg-official-support-20261124-v1"


async def native_command(session, endpoint, suffix, fields, dry=False):
    payload = {
        "command_id": KEY + "-" + suffix + ("-dry" if dry else ""),
        "actor": "risegram-owner",
        "reason": "Brand the owner-designated @support account as official RiseGram support",
        "dry_run": dry,
        **fields
    }
    async with session.post(config.MTPROTO_ADMIN_API_URL + endpoint, json=payload) as response:
        result = await response.json(content_type=None)
        if response.status != 200 or result.get("status") != "completed":
            raise RuntimeError("Support branding rejected: " +
                               str(result.get("code") or result.get("error") or response.status)[:160])
        if dry and not result.get("dry_run"):
            raise RuntimeError("Admin API did not confirm dry-run")
    return result


async def run_once():
    if not config.MTPROTO_ADMIN_API_URL or not config.MTPROTO_ADMIN_API_TOKEN:
        raise RuntimeError("MTProto Admin API missing")
    async with await psycopg.AsyncConnection.connect(
        os.environ["RISEGRAM_LEDGER_DSN"], connect_timeout=8, row_factory=dict_row
    ) as conn:
        async with conn.transaction():
            user = await (await conn.execute("""
                SELECT id, username, about, verified, deleted_at, scam, fake
                  FROM users WHERE id=%s FOR UPDATE
            """, (USER_ID,))).fetchone()
            if not user or user["username"].lower() != "youtube" or user["deleted_at"] is not None:
                raise RuntimeError("Official support peer ID/username mismatch")
            if not user["verified"] or user["scam"] or user["fake"]:
                raise RuntimeError("Support peer identity must remain official and unflagged")
            ownership = await (await conn.execute("""
                SELECT c.id FROM collectible_usernames c
                  JOIN peer_usernames p ON p.collectible_id=c.id
                WHERE c.username_lower='support' AND c.status='owned'
                  AND c.owner_peer_type='user' AND c.owner_peer_id=%s
                  AND p.username_lower='support' AND p.peer_type='user'
                  AND p.peer_id=%s AND p.active
            """, (USER_ID, USER_ID))).fetchone()
            if not ownership:
                raise RuntimeError("@support NFT not owned by the authorized appeal account")
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS risegram_support_branding_receipts (
                    command_key text PRIMARY KEY,
                    user_id bigint NOT NULL,
                    former_name text NOT NULL,
                    former_about text NOT NULL,
                    changed_at timestamptz NOT NULL DEFAULT now()
                )
            """)
            await conn.execute("""
                INSERT INTO risegram_support_branding_receipts
                    (command_key,user_id,former_name,former_about)
                SELECT %s,id,first_name,about FROM users WHERE id=%s
                ON CONFLICT(command_key) DO NOTHING
            """, (KEY,USER_ID))
            # The administrative profile API can change name but does not
            # expose a bio field. Preserve the old bio in the audit receipt.
            await conn.execute("""
                UPDATE users SET about=%s,updated_at=now()
                WHERE id=%s AND deleted_at IS NULL
                  AND username='youtube' AND about IS DISTINCT FROM %s
            """, (BIO, USER_ID, BIO))

    async with ClientSession(
        headers={"Authorization": "Bearer " + config.MTPROTO_ADMIN_API_TOKEN},
        timeout=ClientTimeout(total=16, connect=5),
    ) as session:
        support_fields = {"user_id": USER_ID, "support": True}
        profile_fields = {"user_id": USER_ID, "first_name": NAME, "last_name": ""}
        await native_command(session, "/v1/accounts/set-support", "support", support_fields, dry=True)
        await native_command(session, "/v1/accounts/set-profile", "profile", profile_fields, dry=True)
        await native_command(session, "/v1/accounts/set-support", "support", support_fields)
        await native_command(session, "/v1/accounts/set-profile", "profile", profile_fields)
    log.info("SUPPORT_BRANDING_APPLIED native_id=%s nft=@support", USER_ID)


async def run():
    try:
        await run_once()
    except Exception as exc:
        log.error("SUPPORT_BRANDING_FAILED reason=%s", str(exc)[:220])
