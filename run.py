"""run.py — запуск RiseGram с панелью тестера (tester.py).

bot.py и admin.py не меняются: перед запуском сюда подмешивается роутер тестера, и он встаёт
после админки и до основного роутера. Если tester.py по какой-то причине не загрузится,
бот всё равно запустится как раньше, просто без панели тестера.

Запуск:  python run.py   (в Procfile)
Вернуть как было: в Procfile снова написать  web: python bot.py
"""
from __future__ import annotations

import asyncio
import logging

import bot

log = logging.getLogger("risegram.run")

try:
    from tester import tester_router
except Exception:  # noqa: BLE001 — панель не должна ронять бота
    log.exception("tester.py не загрузился, бот запускается без панели тестера")
    tester_router = None

if tester_router is not None:
    _BaseDispatcher = bot.Dispatcher

    class _Dispatcher(_BaseDispatcher):
        def include_router(self, router):
            if router is bot.router:  # основной роутер подключается последним: тестер встаёт перед ним
                super().include_router(tester_router)
            return super().include_router(router)

    bot.Dispatcher = _Dispatcher

if __name__ == "__main__":
    async def run_with_backup():
        # One-shot read-only backups run on the existing /data volume. Do not
        # block login, chat delivery or the bot while the archive is produced.
        import os
        import sys
        if os.getenv("RISEGRAM_BACKUP_ON_START") == "1":
            from safe_db_backup_once import run_async
            asyncio.create_task(run_async(), name="risegram-safe-backup")

        async def neon_restore_once():
            # Restores ONLY into a verified empty Neon database. Never touches
            # MTProto production DSN. The tool itself enforces an empty target
            # and single-transaction pg_restore.
            log.info("NEON_RESTORE_JOB_START")
            proc = await asyncio.create_subprocess_exec(
                sys.executable, "/app/mtproto_neon_restore.py",
                "--restore-to-new-empty-neon",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            out, err = await proc.communicate()
            for line in out.decode(errors="replace").splitlines():
                log.info("%s", line[:240])
            for line in err.decode(errors="replace").splitlines():
                log.error("%s", line[:240])
            log.info("NEON_RESTORE_JOB_EXIT code=%s", proc.returncode)

        if os.getenv("RISEGRAM_NEON_RESTORE_ON_START") == "1":
            asyncio.create_task(neon_restore_once(), name="risegram-neon-restore")
        async def provision_group_bots():
            # Idempotent native bot creation, no secrets in logs or source.
            import config
            import database as db
            import psycopg
            from psycopg.rows import dict_row
            from aiohttp import ClientSession, ClientTimeout
            import account_recovery as recovery
            try:
                await db.init_db()
                await db.ensure_owner()
                # Group bots belong to the verified native project owner,
                # not the separately linked support account in the bot DB.
                async with await psycopg.AsyncConnection.connect(recovery.ledger_dsn(), row_factory=dict_row, connect_timeout=5) as conn:
                    peer = await (await conn.execute("""SELECT u.id FROM users u JOIN peer_usernames p
                      ON p.peer_id=u.id AND p.peer_type='user'
                      WHERE u.id=%s AND p.username_lower='owner' AND p.active
                        AND u.deleted_at IS NULL AND NOT u.is_bot AND NOT u.support""",
                        (1780243200,))).fetchone()
                if not peer:
                    raise RuntimeError("Owner native identity unavailable")
                owner_id = int(peer["id"])
                async with ClientSession(headers={"Authorization": "Bearer " + config.MTPROTO_ADMIN_API_TOKEN},
                                         timeout=ClientTimeout(total=15)) as session:
                    for username, name in (("TypeVirtBot","TypeVirt"),("RiseIrisBot","RiseIris")):
                        async with await psycopg.AsyncConnection.connect(recovery.ledger_dsn(), row_factory=dict_row, connect_timeout=5) as conn:
                            row = await (await conn.execute("""SELECT u.id,u.is_bot,b.owner_user_id
                              FROM users u LEFT JOIN bots b ON b.bot_user_id=u.id
                              WHERE lower(u.username)=%s AND u.deleted_at IS NULL""",(username.lower(),))).fetchone()
                        if row:
                            if not row["is_bot"] or row["owner_user_id"] != owner_id:
                                raise RuntimeError("Requested bot name is occupied by another owner")
                            log.info("RG_NATIVE_BOT_READY %s id=%s",username,row["id"])
                            continue
                        async with session.post(config.MTPROTO_ADMIN_API_URL+"/v1/bots/create",json={
                            "command_id":"rg-group-bot-20261124-"+username.lower(),
                            "actor":"risegram-owner","reason":"Owner requested native group bot integration",
                            "owner_user_id":owner_id,"name":name,"username":username,
                        }) as response:
                            result=await response.json(content_type=None)
                            if response.status!=200 or result.get("status")!="completed":
                                log.error("RG_NATIVE_BOT_CREATE_FAILED %s HTTP %s",username,response.status)
                                continue
                            log.info("RG_NATIVE_BOT_READY %s id=%s",username,result.get("details",{}).get("bot_user_id"))
            except Exception as exc:
                log.error("RG_NATIVE_BOT_SETUP_FAILED %s",type(exc).__name__)

        asyncio.create_task(provision_group_bots(), name="risegram-native-group-bots")
        from myth_mask_import import run as import_myth_mask
        asyncio.create_task(import_myth_mask(), name="risegram-myth-mask-import")
        from role_marks import bootstrap as role_bootstrap
        asyncio.create_task(role_bootstrap(), name="risegram-role-marks")
        await bot.main()

    asyncio.run(run_with_backup())
