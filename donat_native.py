"""RiseGram native chat purchases and staff-only risk labels.

Native MTProto (not Telegram) is authoritative for peer ownership and public
usernames. Never charge until the channel creator, namespace availability and
issuer configuration have been checked. Final delivery repeats the checks.
"""
from __future__ import annotations
import os
import re
import time
from aiohttp import ClientSession, ClientTimeout
import psycopg
from psycopg.rows import dict_row

import config
import database as db

NAME = re.compile(r"^[a-z][a-z0-9_]{3,31}$")
KINDS = {"group", "channel"}
LABELS = {"SCAM", "FAKE"}
TIMEOUT = ClientTimeout(total=15, connect=5)


class NativePurchaseError(ValueError):
    pass


def normalized(value: str) -> str:
    return (value or "").strip().lstrip("@").lower()


def ensure_name(value: str) -> str:
    name = normalized(value)
    if not NAME.fullmatch(name):
        raise NativePurchaseError("Юзернейм: 4–32 латинских символа, первая буква")
    return name


def admin_api() -> str:
    if not config.MTPROTO_ADMIN_API_URL or not config.MTPROTO_ADMIN_API_TOKEN:
        raise NativePurchaseError("MTProto Admin API не подключён")
    return config.MTPROTO_ADMIN_API_URL.rstrip("/")


async def native_command(path: str, data: dict, charge: str) -> dict:
    base = admin_api()
    payload = {
        "command_id": "rg-shop-" + charge,
        "actor": "risegram-donation",
        "reason": "Paid RiseGram native chat product issued to verified creator",
        **data,
    }
    async with ClientSession(timeout=TIMEOUT, headers={
        "Authorization": "Bearer " + config.MTPROTO_ADMIN_API_TOKEN
    }) as session:
        async with session.post(base + path, json=payload) as response:
            result = await response.json(content_type=None)
            if response.status != 200 or result.get("status") != "completed":
                raise NativePurchaseError(str(result.get("code") or result.get("error") or
                                              "MTProto rejected issuance")[:150])
            return result.get("details") or {}


async def _native_peer_id(conn, user: dict) -> int:
    digits = db.digits_of(user.get("phone_digits") or user.get("phone"))
    if len(digits) < 7:
        raise NativePurchaseError("У аккаунта нет связанного RiseGram номера")
    row = await (await conn.execute("""
        SELECT id FROM users
        WHERE regexp_replace(phone, '[^0-9]', '', 'g') = %s
          AND deleted_at IS NULL
    """, (digits,))).fetchall()
    if len(row) != 1:
        raise NativePurchaseError("Не удалось однозначно подтвердить RiseGram аккаунт")
    return int(row[0]["id"])


async def validate_chat_owner(uid: int, kind: str, chat_ref: str) -> dict:
    if kind not in KINDS:
        raise NativePurchaseError("Выбери канал или группу")
    if not os.environ.get("RISEGRAM_LEDGER_DSN"):
        raise NativePurchaseError("База MTProto недоступна")
    user = await db.get_user(uid)
    if not user or user.get("banned"):
        raise NativePurchaseError("Аккаунт недоступен")
    raw = (chat_ref or "").strip()
    if raw.startswith("-100"):
        raw = raw[4:]
    numeric = raw.isdecimal() and raw != "0"
    if not numeric:
        name = ensure_name(raw)
    async with await psycopg.AsyncConnection.connect(
        os.environ["RISEGRAM_LEDGER_DSN"], connect_timeout=8, row_factory=dict_row
    ) as conn:
        await conn.execute("SET TRANSACTION READ ONLY")
        actor_id = await _native_peer_id(conn, user)
        if numeric:
            peer_id = int(raw)
            rows = await (await conn.execute("""
                SELECT id, creator_user_id, megagroup, broadcast, username
                FROM channels WHERE id=%s AND NOT deleted
            """, (peer_id,))).fetchall()
        else:
            rows = await (await conn.execute("""
                SELECT DISTINCT c.id, c.creator_user_id, c.megagroup,
                       c.broadcast, c.username
                FROM peer_usernames p JOIN channels c ON c.id=p.peer_id
                WHERE p.peer_type='channel' AND p.username_lower=%s
                  AND p.active=true AND NOT c.deleted
            """, (name,))).fetchall()
        if len(rows) != 1:
            raise NativePurchaseError("Чат не найден в RiseGram")
        peer = rows[0]
        if not ((kind == "group" and peer["megagroup"]) or
                (kind == "channel" and peer["broadcast"])):
            raise NativePurchaseError("Тип группы/канала не совпадает")
        if int(peer["creator_user_id"] or 0) != actor_id:
            raise NativePurchaseError("Покупка доступна только создателю чата")
        return {"channel_id": int(peer["id"]), "kind": kind,
                "username": peer["username"], "owner_id": actor_id}


async def check_chat_username(name: str) -> str:
    name = ensure_name(name)
    if config.is_reserved_username(name):
        raise NativePurchaseError("Служебные имена не продаются")
    async with await psycopg.AsyncConnection.connect(
        os.environ["RISEGRAM_LEDGER_DSN"], connect_timeout=8, row_factory=dict_row
    ) as conn:
        await conn.execute("SET TRANSACTION READ ONLY")
        row = await (await conn.execute("""
            SELECT
              EXISTS(SELECT 1 FROM peer_usernames WHERE username_lower=%s)
              OR EXISTS(SELECT 1 FROM collectible_usernames
                         WHERE username_lower=%s AND status <> 'burned')
              OR EXISTS(SELECT 1 FROM users WHERE lower(username)=%s AND deleted_at IS NULL)
              OR EXISTS(SELECT 1 FROM channels WHERE lower(username)=%s AND NOT deleted)
              AS taken
        """, (name, name, name, name))).fetchone()
        if row and row["taken"]:
            raise NativePurchaseError("Юзернейм уже занят в RiseGram")
    if await db.username_taken(name):
        raise NativePurchaseError("Юзернейм занят или зарезервирован в боте")
    return name


async def issue_chat_username(uid: int, kind: str, channel_id: int,
                              name: str, charge: str) -> dict:
    await validate_chat_owner(uid, kind, str(channel_id))
    await check_chat_username(name)
    return await native_command("/v1/collectible-usernames/mint", {
        "username": name,
        "owner_channel_id": str(channel_id),
        "currency": "USD",
        "amount": "0",
        "purchase_date": int(time.time()),
    }, "username-" + charge)


async def native_chat_issuer(uid: int, kind: str, channel_id: int,
                             issuer: str, charge: str) -> dict:
    if issuer not in ("major", "risegram"):
        raise NativePurchaseError("Неверный тип метки")
    await validate_chat_owner(uid, kind, str(channel_id))
    bot_id = await db.get_meta("native_issuer:" + issuer)
    if not bot_id or not str(bot_id).isdigit():
        raise NativePurchaseError("Метка пока не настроена в MTProto")
    # A purchased label states explicitly that it is commercial. Never set
    # channels.verified, which is the official editorial verification flag.
    label = "Major" if issuer == "major" else "RiseGram"
    return await native_command("/v1/botverification/marks/grant", {
        "peer_type": "channel", "peer_id": int(channel_id),
        "verifier_bot_id": int(bot_id),
        "description": f"Платная метка {label} в RiseGram, не официальная верификация",
    }, "badge-" + charge)


async def issue_profile_club(uid: int, charge: str) -> dict:
    user = await db.get_user(uid)
    if not user or user.get("banned"):
        raise NativePurchaseError("Аккаунт недоступен")
    issuer = await db.get_meta("native_issuer:risegramclub")
    if not issuer or not str(issuer).isdigit():
        raise NativePurchaseError("RiseGram Club ещё не настроен на сервере")
    async with await psycopg.AsyncConnection.connect(
        os.environ["RISEGRAM_LEDGER_DSN"], connect_timeout=8, row_factory=dict_row
    ) as conn:
        await conn.execute("SET TRANSACTION READ ONLY")
        peer_id = await _native_peer_id(conn, user)
    result = await native_command("/v1/botverification/marks/grant", {
        "peer_type": "user", "peer_id": peer_id, "verifier_bot_id": int(issuer),
        "description": "Платная косметическая метка RiseGram Club. Не подтверждает личность",
    }, "club-" + charge)
    await db.execute("""
        INSERT INTO purchased_profile_marks(tg_id,issuer,charge_id,created_at)
        VALUES(?,'risegramclub',?,?)
        ON CONFLICT(tg_id,issuer) DO NOTHING
    """, (uid, charge, db.now()))
    return result


async def change_risk_label(target: str, kind: str, label: str,
                            enabled: bool, reason: str, actor: int,
                            command: str) -> dict:
    """Operator-only native safety flag; must be called by admin router."""
    if label not in LABELS or kind not in ("user", "group", "channel"):
        raise NativePurchaseError("Неверная метка или тип")
    if enabled and len(reason.strip()) < 8:
        raise NativePurchaseError("Укажи причину не короче 8 символов")
    from nft_username_admin import resolve_target
    peer = await resolve_target(kind, target)
    table = "users" if kind == "user" else "channels"
    async with await psycopg.AsyncConnection.connect(
        os.environ["RISEGRAM_LEDGER_DSN"], connect_timeout=8, row_factory=dict_row
    ) as conn:
        await conn.execute("SET TRANSACTION READ ONLY")
        row = await (await conn.execute(
            f"SELECT scam,fake FROM {table} WHERE id=%s",
            (peer["peer_id"],)
        )).fetchone()
        if not row:
            raise NativePurchaseError("Объект не найден в базе RiseGram")
    scam, fake = bool(row["scam"]), bool(row["fake"])
    if enabled:
        scam, fake = label == "SCAM", label == "FAKE"
    elif label == "SCAM":
        scam = False
    else:
        fake = False
    path = "/v1/accounts/set-flags" if kind == "user" else "/v1/channels/set-flags"
    field = "user_id" if kind == "user" else "channel_id"
    result = await native_command(path, {
        field: int(peer["peer_id"]), "scam": scam, "fake": fake,
        "actor": "risegram-moderator-" + str(actor),
        "reason": (reason.strip() or "Moderator removed risk label")[:1024],
    }, "moderation-" + command)
    # Only after native confirms do we update the bot's display cache.
    # For numerical ID targets there is no stable username key to store.
    if target.startswith("@"):
        name = ensure_name(target)
        if enabled:
            await db.set_risk_label(name, label, actor, reason)
            other = "FAKE" if label == "SCAM" else "SCAM"
            await db.remove_risk_label(name, other)
        else:
            await db.remove_risk_label(name, label)
            if label == "SCAM":
                await db.revoke_scam_label(name)
    return result
