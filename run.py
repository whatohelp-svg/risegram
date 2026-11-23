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
        await bot.main()

    asyncio.run(run_with_backup())
