"""Native NFT-username management for RiseGram bot administrators.

Revoke detaches an owned collectible back into the vault. Permanent delete is a
separate owner-only action. Never touch editable usernames, phones, sessions,
chat/account entities, or a collectible owned by another peer.
"""
import os
import re
import time

import psycopg
from psycopg.rows import dict_row
from aiohttp import ClientSession, ClientTimeout

import config
import database as db

USERNAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{3,31}$")
NATIVE_URL = "/v1/collectible-usernames"
TYPES = {"user", "group", "channel"}
HTTP_TIMEOUT = ClientTimeout(total=23, connect=7)


class NFTUsernameAdminError(Exception):
    pass


def normalize(username: str) -> str:
    name = username.strip().lstrip("@").lower()
    if not USERNAME_RE.fullmatch(name):
        raise NFTUsernameAdminError("NFT-юзернейм должен состоять из 4–32 латинских букв, цифр и _.")
    return name


def native_session() -> ClientSession:
    if not config.MTPROTO_ADMIN_API_URL or not config.MTPROTO_ADMIN_API_TOKEN:
        raise NFTUsernameAdminError("MTProto Admin API не настроен.")
    return ClientSession(timeout=HTTP_TIMEOUT, headers={
        "Authorization": "Bearer " + config.MTPROTO_ADMIN_API_TOKEN,
        "Accept": "application/json",
    })


def base() -> str:
    return config.MTPROTO_ADMIN_API_URL.rstrip("/")


async def resolve_target(kind: str, target: str) -> dict:
    if kind not in TYPES:
        raise NFTUsernameAdminError("Тип должен быть user, group или channel.")
    raw = target.strip()
    numeric = re.fullmatch(r"(?:-100)?[1-9][0-9]{0,18}", raw)
    async with await psycopg.AsyncConnection.connect(
        os.environ["RISEGRAM_LEDGER_DSN"], row_factory=dict_row, connect_timeout=8,
    ) as conn:
        await conn.execute("SET TRANSACTION READ ONLY")
        if numeric:
            peer_id = int(raw.removeprefix("-100"))
            peer_type = "user" if kind == "user" else "channel"
        else:
            if not raw.startswith("@"):
                raise NFTUsernameAdminError("Для поиска укажи @юзернейм или числовой native ID.")
            name = normalize(raw)
            matches = await (await conn.execute("""
                SELECT peer_type, peer_id FROM peer_usernames
                 WHERE username_lower=%s
            """, (name,))).fetchall()
            expected_type = "user" if kind == "user" else "channel"
            if len(matches) != 1 or matches[0]["peer_type"] != expected_type:
                raise NFTUsernameAdminError("Владелец с таким @именем и типом не найден в RiseGram.")
            peer_id = int(matches[0]["peer_id"])
            peer_type = matches[0]["peer_type"]
        if peer_id <= 0 or peer_id >= 2**63:
            raise NFTUsernameAdminError("Некорректный ID.")
        if peer_type == "user":
            entry = await (await conn.execute("""
                SELECT id,phone FROM users WHERE id=%s AND deleted_at IS NULL
            """, (peer_id,))).fetchone()
            if not entry:
                raise NFTUsernameAdminError("Активный аккаунт не найден.")
            phone_digits = db.digits_of(entry["phone"])
            return {"kind": kind, "peer_type": peer_type, "peer_id": peer_id,
                    "phone_digits": phone_digits}
        entry = await (await conn.execute("""
            SELECT id,megagroup,broadcast FROM channels WHERE id=%s AND NOT deleted
        """, (peer_id,))).fetchone()
        if not entry:
            raise NFTUsernameAdminError("Группа или канал не найден.")
        if (kind == "group" and not entry["megagroup"]) or (
                kind == "channel" and not entry["broadcast"]):
            raise NFTUsernameAdminError("Тип чата не совпадает. NFT-имена групп поддерживаются у супергрупп.")
        return {"kind": kind, "peer_type": peer_type, "peer_id": peer_id,
                "phone_digits": ""}


async def lookup_asset(session: ClientSession, name: str) -> dict | None:
    async with session.get(base() + NATIVE_URL,
                           params={"q": name, "limit": "100"}) as resp:
        if resp.status != 200:
            raise NFTUsernameAdminError("Не удалось прочитать NFT-реестр MTProto (HTTP %s)." % resp.status)
        data = await resp.json(content_type=None)
    matches = [x for x in data.get("assets", [])
               if str(x.get("username") or "").lower() == name
               and x.get("status") != "burned"]
    if len(matches) > 1:
        raise NFTUsernameAdminError("В реестре несколько одинаковых активных NFT, действие отменено.")
    return matches[0] if matches else None


async def local_inventory(target: dict, name: str) -> dict | None:
    item = await db.fetchone("""
        SELECT * FROM user_nft_inventory
        WHERE item_type='username' AND lower(value)=?
    """, (name,))
    if not item:
        return None
    if target["kind"] != "user":
        raise NFTUsernameAdminError(
            "Это имя также находится в инвентаре аккаунта бота. Нельзя менять владельца без сверки."
        )
    local_user = await db.get_user(int(item["tg_id"]))
    if not local_user or db.digits_of(local_user.get("phone_digits") or local_user.get("phone")) != target["phone_digits"]:
        raise NFTUsernameAdminError("В базе бота обнаружен другой владелец NFT. Операция остановлена.")
    blocked = await db.fetchone("""
        SELECT id FROM market_listings
        WHERE item_id=? AND status IN ('active','processing') LIMIT 1
    """, (int(item["id"]),))
    if blocked:
        raise NFTUsernameAdminError("NFT выставлен на маркет или участвует в сделке. Сначала закрой лот.")
    return {"id": int(item["id"]), "tg_id": int(item["tg_id"])}


def same_owner(asset: dict, target: dict) -> bool:
    return (asset.get("status") == "owned"
            and asset.get("owner_type") == target["peer_type"]
            and str(asset.get("owner_id") or "") == str(target["peer_id"]))


async def prepare(kind: str, target_name: str, asset_name: str, mode: str) -> dict:
    if mode not in ("revoke", "delete"):
        raise NFTUsernameAdminError("Неизвестная операция.")
    target = await resolve_target(kind, target_name)
    name = normalize(asset_name)
    async with native_session() as session:
        asset = await lookup_asset(session, name)
    if not asset:
        raise NFTUsernameAdminError("NFT @" + name + " не найден в MTProto. Обычные юзернеймы не удаляем.")
    if not same_owner(asset, target):
        raise NFTUsernameAdminError("NFT не принадлежит указанному пользователю, группе или каналу.")
    local = await local_inventory(target, name)
    return {
        "kind": kind, "target": target_name, "peer_type": target["peer_type"],
        "peer_id": target["peer_id"], "name": name, "mode": mode,
        "asset_id": str(asset["id"]), "local_id": local["id"] if local else None,
        "local_uid": local["tg_id"] if local else None,
        "created_at": int(time.time()),
    }


async def clean_local(pending: dict) -> None:
    item_id = pending.get("local_id")
    if not item_id:
        return
    uid = pending["local_uid"]
    name = pending["name"]
    async with db.connect() as conn:
        await conn.execute("BEGIN IMMEDIATE")
        item = await (await conn.execute("""
            SELECT id,tg_id,item_type,value FROM user_nft_inventory WHERE id=?
        """,(item_id,))).fetchone()
        if item is None:
            await conn.rollback()
            return
        if (int(item["tg_id"]) != uid or item["item_type"] != "username"
                or item["value"].lower() != name):
            raise NFTUsernameAdminError("Локальный NFT сменился, автоматическое удаление отменено.")
        active = await (await conn.execute("""
            SELECT 1 FROM market_listings WHERE item_id=?
              AND status IN ('active','processing')
        """,(item_id,))).fetchone()
        if active:
            raise NFTUsernameAdminError(
                "На маркетплейсе появилась сделка; NFT снят с MTProto, локальный лот требует проверки."
            )
        await conn.execute("DELETE FROM user_nft_inventory WHERE id=?",(item_id,))
        user = await (await conn.execute(
            "SELECT active_username FROM users WHERE tg_id=?",(uid,)
        )).fetchone()
        if user and db.norm_username(user["active_username"]) == name:
            other = await (await conn.execute("""
                SELECT value FROM user_nft_inventory
                WHERE tg_id=? AND item_type='username' AND is_active=1
                ORDER BY id DESC LIMIT 1
            """,(uid,))).fetchone()
            await conn.execute("UPDATE users SET active_username=? WHERE tg_id=?",
                               (other["value"] if other else None,uid))
        await conn.commit()


async def execute(pending: dict, *, admin_id: int, command_id: str) -> str:
    if time.time() - int(pending["created_at"]) > 600:
        raise NFTUsernameAdminError("Подтверждение устарело — начни снова.")
    target = await resolve_target(pending["kind"], pending["target"])
    if target["peer_type"] != pending["peer_type"] or target["peer_id"] != pending["peer_id"]:
        raise NFTUsernameAdminError("Владелец поменялся, действие отменено.")
    name = pending["name"]
    mode = pending["mode"]
    await local_inventory(target,name)
    async with native_session() as session:
        asset = await lookup_asset(session, name)
        if asset is None or str(asset.get("id") or "") != pending["asset_id"] or not same_owner(asset,target):
            raise NFTUsernameAdminError("NFT или его владелец изменился после подтверждения.")
        payload = {
            "command_id": command_id,
            "actor": "risegram-admin-" + str(admin_id),
            "reason": "Administrator-requested collectible username removal from verified peer",
            "username": name,
        }
        if mode == "revoke":
            payload["burn"] = False
            if target["kind"] == "user":
                payload["expected_owner_user_id"] = str(target["peer_id"])
        endpoint = NATIVE_URL + ("/revoke" if mode == "revoke" else "/delete")
        # Dry-run is a separately idempotent command, not a destructive operation.
        async def call(request: dict) -> dict:
            async with session.post(base()+endpoint, json=request) as resp:
                response = await resp.json(content_type=None)
                if resp.status != 200 or response.get("status") != "completed":
                    raise NFTUsernameAdminError(
                        "MTProto отклонил операцию: " +
                        str(response.get("code") or response.get("error") or resp.status)[:120]
                    )
                return response
        dry = await call(dict(payload, command_id=command_id+"-check", dry_run=True))
        if not dry.get("dry_run"):
            raise NFTUsernameAdminError("MTProto не подтвердил dry-run.")
        await call(dict(payload, dry_run=False))
        updated = await lookup_asset(session,name)
        if mode == "revoke":
            if not updated or str(updated.get("id")) != pending["asset_id"] or updated.get("status") != "vault":
                raise NFTUsernameAdminError("MTProto не подтвердил возврат NFT в резерв.")
        elif updated is not None and str(updated.get("id")) == pending["asset_id"]:
            raise NFTUsernameAdminError("MTProto не подтвердил полное удаление NFT.")
    await clean_local(pending)
    return "NFT @%s %s у %s %s, аккаунт/чат сохранён" % (
        name, "отозван в резерв" if mode == "revoke" else "удалён навсегда",
        pending["kind"], pending["peer_id"]
    )
