"""Recoverable owner-controlled account deletion.

Only the bot's OWNER-only admin UI may call these functions. Two authoritative
databases are deliberately treated separately: SQLite tracks admin deletion
receipts, while MTProto's Neon database holds account state and self-service
deletion requests. Never attempt to reconstruct already-tombstoned profiles.
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone

import psycopg
from psycopg.rows import dict_row
from aiohttp import ClientSession, ClientTimeout

import config
import database as db

log = logging.getLogger("risegram.account_recovery")
GRACE_DAYS = 30
ACTIVE = ("preparing", "active", "executing")
TIMEOUT = ClientTimeout(total=20, connect=6)


class RecoveryError(ValueError):
    pass


def utc_now():
    return datetime.now(timezone.utc)


def sqltime(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def parse_phone(text: str) -> str:
    raw = (text or "").strip()
    if not raw or any(c not in "+0123456789 ()-" for c in raw):
        raise RecoveryError("Укажи полный номер RiseGram, включая код страны")
    digits = db.digits_of(raw)
    if not 7 <= len(digits) <= 15:
        raise RecoveryError("Номер должен содержать 7–15 цифр")
    return digits


def ledger_dsn() -> str:
    dsn = os.environ.get("RISEGRAM_LEDGER_DSN")
    if not dsn:
        raise RecoveryError("База MTProto Neon недоступна")
    return dsn


async def native_info(phone: str) -> dict | None:
    """Resolve exact current phone, not a username or recycled alias."""
    async with await psycopg.AsyncConnection.connect(
        ledger_dsn(), connect_timeout=8, row_factory=dict_row
    ) as conn:
        await conn.execute("SET TRANSACTION READ ONLY")
        rows = await (await conn.execute("""
            SELECT id, phone, is_bot, deleted_at
              FROM users
             WHERE regexp_replace(phone,'[^0-9]','','g')=%s
               AND deleted_at IS NULL
        """, (phone,))).fetchall()
        if len(rows) > 1:
            raise RecoveryError("Несколько native аккаунтов используют этот номер")
        return dict(rows[0]) if rows else None


async def native_pending(user_id: int) -> dict | None:
    if user_id <= 0:
        return None
    async with await psycopg.AsyncConnection.connect(
        ledger_dsn(), connect_timeout=8, row_factory=dict_row
    ) as conn:
        await conn.execute("SET TRANSACTION READ ONLY")
        row = await (await conn.execute("""
            SELECT id, execute_at, requested_at FROM account_deletion_requests
             WHERE user_id=%s AND state='pending'
        """, (user_id,))).fetchone()
        return dict(row) if row else None


async def native_flags(user_id: int) -> dict | None:
    if user_id <= 0:
        return None
    async with await psycopg.AsyncConnection.connect(
        ledger_dsn(), connect_timeout=8, row_factory=dict_row
    ) as conn:
        await conn.execute("SET TRANSACTION READ ONLY")
        row = await (await conn.execute("""
            SELECT frozen, actor, command_id FROM account_restrictions WHERE user_id=%s
        """, (user_id,))).fetchone()
        return dict(row) if row else None


async def native_by_id(user_id: int) -> dict | None:
    async with await psycopg.AsyncConnection.connect(
        ledger_dsn(), connect_timeout=8, row_factory=dict_row
    ) as conn:
        await conn.execute("SET TRANSACTION READ ONLY")
        row = await (await conn.execute("""
            SELECT id,phone,deleted_at,is_bot FROM users WHERE id=%s
        """, (user_id,))).fetchone()
        return dict(row) if row else None


async def native_action(path: str, key: str, actor: int, fields: dict) -> dict:
    if not config.MTPROTO_ADMIN_API_URL or not config.MTPROTO_ADMIN_API_TOKEN:
        raise RecoveryError("MTProto Admin API недоступен")
    payload = {
        "command_id": key, "actor": "risegram-admin-archive-" + str(actor),
        "reason": "Recoverable account removal managed by RiseGram", **fields,
    }
    async with ClientSession(timeout=TIMEOUT, headers={
        "Authorization": "Bearer " + config.MTPROTO_ADMIN_API_TOKEN
    }) as session:
        async with session.post(
            config.MTPROTO_ADMIN_API_URL.rstrip("/") + "/v1/accounts/" + path,
            json=payload
        ) as response:
            result = await response.json(content_type=None)
            if response.status != 200 or result.get("status") != "completed":
                raise RecoveryError("MTProto отказал: " + str(
                    result.get("code") or result.get("error") or response.status
                )[:130])
            return result


async def recent_record(phone: str) -> dict | None:
    return await db.fetchone("""
        SELECT * FROM admin_account_deletions
        WHERE phone_digits=? ORDER BY id DESC LIMIT 1
    """, (phone,))


async def recoverable_records(limit: int = 12) -> list[dict]:
    return await db.fetchall("""
        SELECT * FROM admin_account_deletions
        WHERE status IN ('preparing','active','executing')
        ORDER BY id DESC LIMIT ?
    """, (limit,))


async def pending_for_local(uid: int) -> bool:
    return bool(await db.scalar("""
        SELECT COUNT(*) FROM admin_account_deletions
         WHERE local_id=? AND status IN ('preparing','active','executing')
    """, (uid,)))


async def preview(phone: str) -> dict:
    phone = parse_phone(phone)
    local = await db.fetchone("SELECT tg_id, name, phone_digits FROM users WHERE phone_digits=?", (phone,))
    peer = await native_info(phone)
    if peer and peer["is_bot"]:
        raise RecoveryError("Нельзя удалять или восстанавливать сервисного бота")
    if local and int(local["tg_id"]) == int(config.OWNER_ID):
        raise RecoveryError("Нельзя удалять аккаунт владельца")
    if not peer and not local:
        raise RecoveryError("Активный аккаунт по этому номеру не найден. Если он уже окончательно удалён, восстановление требует резервной копии.")
    pending = await native_pending(int(peer["id"])) if peer else None
    receipt = await recent_record(phone)
    if receipt and receipt["status"] in ACTIVE:
        if int(receipt["native_id"]) != (int(peer["id"]) if peer else 0):
            raise RecoveryError("Номер теперь принадлежит другому аккаунту, операция запрещена")
    return dict(phone=phone, local=local, native=peer, pending=pending, receipt=receipt)


async def schedule(phone: str, actor: int, key: str,
                   expected_native_id: int = 0, expected_local_id: int = 0) -> dict:
    info = await preview(phone)
    local = info["local"]
    peer = info["native"]
    if (expected_native_id and (not peer or int(peer["id"]) != expected_native_id)
            or expected_local_id and (not local or int(local["tg_id"]) != expected_local_id)):
        raise RecoveryError("Владелец номера изменился после подтверждения")
    if info["pending"]:
        raise RecoveryError("Удаление уже ожидает выполнения на MTProto — используй восстановление")
    if info["receipt"] and info["receipt"]["status"] in ACTIVE:
        raise RecoveryError("Этот аккаунт уже ожидает окончательного удаления")
    if not peer:
        raise RecoveryError("Native аккаунт не найден — удаление остановлено, чтобы не потерять локальные данные")
    due = utc_now() + timedelta(days=GRACE_DAYS)
    due_str = sqltime(due)
    now_str = sqltime(utc_now())
    native_id = int(peer["id"])
    flags = await native_flags(native_id)
    if flags and flags["frozen"]:
        raise RecoveryError("Аккаунт уже ограничен модерацией — его нельзя перезаписывать удалением")
    local_id = int(local["tg_id"]) if local else 0
    async with db.connect() as conn:
        await conn.execute("BEGIN IMMEDIATE")
        conflict = await (await conn.execute("""
            SELECT id FROM admin_account_deletions WHERE phone_digits=?
            AND status IN ('preparing','active','executing')
        """, (phone,))).fetchone()
        if conflict:
            raise RecoveryError("Для этого номера уже запланировано удаление")
        cur = await conn.execute("""
            INSERT INTO admin_account_deletions
                (phone_digits,local_id,native_id,status,requested_at,execute_at,
                 actor_telegram_id,command_key)
            VALUES(?,?,?,'preparing',?,?,?,?)
        """, (phone,local_id,native_id,now_str,due_str,actor,key))
        record_id = int(cur.lastrowid)
        await conn.commit()
    try:
        await native_action("set-frozen", "rg-admin-archive-" + key,
                            actor, {"user_id": native_id, "frozen": True,
                                    "freeze_until": (due + timedelta(days=365)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                                    "freeze_appeal_url": "https://t.me/risegrambot"})
    except Exception as exc:
        # A network timeout may mean the server already froze the peer. Keep a
        # recoverable receipt rather than losing the only safe unfreeze link.
        await db.execute("""
            UPDATE admin_account_deletions SET last_error=?
            WHERE id=? AND status='preparing'
        """, (str(exc)[:200], record_id))
        raise
    await db.execute("""
        UPDATE admin_account_deletions SET status='active',last_error=NULL
        WHERE id=? AND status='preparing'
    """, (record_id,))
    return {"id": record_id, "due": due_str, "native_id": native_id,
            "local_id": local_id}


async def _cancel_native_pending(user_id: int, actor: int) -> bool:
    """Revoke the deletion requester's session before cancelling its token.

    This mirrors upstream CancelAccountDeletion's auth security semantics, but
    uses the authenticated admin API for session revocation instead of raw
    deleting auth tables. Never expose hashes or tokens to the bot UI.
    """
    async with await psycopg.AsyncConnection.connect(
        ledger_dsn(), connect_timeout=8, row_factory=dict_row
    ) as conn:
        await conn.execute("SET TRANSACTION READ ONLY")
        row = await (await conn.execute("""
            SELECT d.id, d.execute_at, a.hash AS auth_hash
              FROM account_deletion_requests d
              LEFT JOIN authorizations a
                ON a.auth_key_id=d.requester_auth_key_id AND a.user_id=d.user_id
             WHERE d.user_id=%s AND d.state='pending'
        """, (user_id,))).fetchone()
    if not row:
        return False
    if row["execute_at"] <= utc_now():
        raise RecoveryError("Срок восстановления из клиента уже закончился")
    if row["auth_hash"]:
        await native_action("revoke-sessions",
                            "rg-admin-restore-requester-" + str(row["id"]),
                            actor, {"user_id": user_id, "hash": int(row["auth_hash"])})
    async with await psycopg.AsyncConnection.connect(
        ledger_dsn(), connect_timeout=8, row_factory=dict_row
    ) as conn:
        async with conn.transaction():
            user = await (await conn.execute("""
                SELECT id,deleted_at FROM users WHERE id=%s FOR UPDATE
            """, (user_id,))).fetchone()
            if not user or user["deleted_at"] is not None:
                raise RecoveryError("Аккаунт уже окончательно удалён на MTProto")
            updated = await (await conn.execute("""
                UPDATE account_deletion_requests
                   SET state='cancelled',completed_at=now(),updated_at=now()
                 WHERE id=%s AND user_id=%s AND state='pending' AND execute_at>now()
             RETURNING id
            """, (int(row["id"]), user_id))).fetchone()
            return updated is not None

async def restore(phone: str, actor: int, expected_record_id: int = 0,
                  expected_native_id: int = 0) -> dict:
    info = await preview(phone)
    peer = info["native"]
    if not peer:
        raise RecoveryError("Аккаунт уже удалён на сервере — автоматическое восстановление невозможно")
    native_id = int(peer["id"])
    if expected_native_id and expected_native_id != native_id:
        raise RecoveryError("Владелец номера изменился")
    receipt = info["receipt"]
    restored_admin = False
    cancelled_self = False
    if receipt and receipt["status"] in ("preparing","active"):
        if expected_record_id and receipt["id"] != expected_record_id:
            raise RecoveryError("Запись восстановления изменилась")
        if int(receipt["native_id"]) != native_id:
            raise RecoveryError("Не совпадает исходный native ID")
        if sqltime(utc_now()) >= receipt["execute_at"]:
            raise RecoveryError("30-дневный срок истёк; требуется проверка резервной копии")
        flags = await native_flags(native_id)
        if flags and flags["frozen"]:
            if not str(flags.get("actor") or "").startswith("risegram-admin-archive-"):
                raise RecoveryError("На аккаунте другая блокировка модерации — снимать её нельзя")
            await native_action("set-frozen", "rg-admin-restore-" + str(receipt["id"]),
                                actor, {"user_id": native_id, "frozen": False})
        await db.execute("""
            UPDATE admin_account_deletions
               SET status='cancelled',completed_at=?,last_error=NULL
             WHERE id=? AND status IN ('preparing','active')
        """, (db.now(),receipt["id"]))
        restored_admin = True
    elif receipt and receipt["status"] == "executing":
        raise RecoveryError("Окончательное удаление уже началось")
    if info["pending"]:
        cancelled_self = await _cancel_native_pending(native_id, actor)
        if not cancelled_self:
            raise RecoveryError("Запрос удаления уже исполнен или истёк")
    if not restored_admin and not cancelled_self:
        raise RecoveryError("Для этого номера нет активного удаления для отмены")
    return {"native_id": native_id, "local_id": int(info["local"]["tg_id"]) if info["local"] else 0,
            "admin_cancelled": restored_admin, "client_cancelled": cancelled_self}


async def purge_due_once() -> None:
    """After grace, finalize a *confirmed* admin soft delete, never a new user."""
    rows = await db.fetchall("""
        SELECT * FROM admin_account_deletions
        WHERE (status='active' AND execute_at<=?) OR status='executing'
        ORDER BY id LIMIT 5
    """, (db.now(),))
    for row in rows:
        rid = int(row["id"])
        try:
            async with db.connect() as conn:
                await conn.execute("BEGIN IMMEDIATE")
                cur = await conn.execute("""
                    UPDATE admin_account_deletions SET status='executing'
                     WHERE id=? AND (status='executing' OR
                          (status='active' AND execute_at<=?))
                """, (rid, db.now()))
                await conn.commit()
                if not cur.rowcount:
                    continue
            peer = await native_by_id(int(row["native_id"]))
            if not peer:
                raise RecoveryError("Исходный native ID пропал из базы")
            if peer["deleted_at"] is None:
                if db.digits_of(peer["phone"]) != row["phone_digits"]:
                    raise RecoveryError("Номер native аккаунта изменился, окончательное удаление остановлено")
                await native_action("delete", "rg-admin-purge-" + str(rid),
                                    int(row["actor_telegram_id"]), {"user_id": int(row["native_id"])})
            if row["local_id"]:
                await db.delete_user(int(row["local_id"]), expected_phone=row["phone_digits"])
            await db.execute("""
                UPDATE admin_account_deletions SET status='completed',completed_at=?,last_error=NULL
                WHERE id=? AND status='executing'
            """, (db.now(),rid))
            await db.log_admin_action(0, "Окончательное удаление после 30 дней: receipt #" + str(rid))
        except Exception as exc:
            log.exception("Admin deletion receipt %s requires retry/manual review", rid)
            await db.execute("""
                UPDATE admin_account_deletions SET last_error=? WHERE id=? AND status='executing'
            """, (str(exc)[:200],rid))


async def deletion_worker() -> None:
    while True:
        try:
            await purge_due_once()
        except Exception:
            log.exception("Admin account deletion worker failed")
        await asyncio.sleep(900)
