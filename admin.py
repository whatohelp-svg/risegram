"""admin.py — GOD MODE RiseGram.

Доступ: владелец через /owner, администраторы и тестеры через /admin.
Кнопки админки нигде в обычном меню нет. Проверка идёт по числовому ID, юзернеймам доверять нельзя.
"""
from __future__ import annotations

import asyncio
import logging
import math
import re
import secrets

import account_recovery as recovery
import nft_username_admin as nftnames
import donat_native as native_shop
import psycopg
import time

from aiohttp import ClientError, ClientSession, ClientTimeout
from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.filters import Command, Filter, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

import config
import database as db
import gift_admin
import native_economy as economy
from api import client_wallet_line, sync_mtproto_verification
from ui import back_row, btn, esc, fmt_num, kb, safe_send, show

log = logging.getLogger("risegram.admin")

PAGE = 8

from gift_catalog import available_telegram_gifts


class OwnerOnly(Filter):
    async def __call__(self, event: Message | CallbackQuery) -> bool:
        u = event.from_user
        return bool(u and u.id == config.OWNER_TELEGRAM_ID)


class AdminOnly(Filter):
    async def __call__(self, event: Message | CallbackQuery) -> bool:
        u = event.from_user
        if not u:
            return False
        if u.id == config.OWNER_TELEGRAM_ID:
            return True
        account = await db.get_by_telegram(u.id)
        return bool(account and await db.is_admin(int(account["tg_id"])))


admin_router = Router(name="admin")
admin_router.message.filter(AdminOnly(), F.chat.type == "private")
admin_router.callback_query.filter(AdminOnly(), F.message.chat.type == "private")


class Adm(StatesGroup):
    input = State()      # универсальный ввод; действие лежит в data["act"]
    delete_confirm = State()
    restore_confirm = State()
    nft_confirm = State()
    cast_confirm = State()


# ════════════════════════════ вспомогательное ════════════════════════════════


def home_row():
    return [btn("👑 В админку", "adm:home", style="primary")]


def cancel_row(cb: str = "adm:home"):
    return [btn("✖️ Отмена", cb, style="danger")]


def pager(prefix: str, page: int, total: int):
    pages = max(1, math.ceil(total / PAGE))
    row = []
    if page > 0:
        row.append(btn("◀️", f"{prefix}:{page - 1}"))
    row.append(btn(f"{page + 1}/{pages}", "adm:noop"))
    if page + 1 < pages:
        row.append(btn("▶️", f"{prefix}:{page + 1}"))
    return row


async def audit(text: str):
    await db.log_admin_action(config.OWNER_ID, text)


def user_line(u: dict) -> str:
    flags = ("🚫" if u.get("banned") else "") + ("☑️" if u.get("verified_risegram") else "") + ("💠" if u.get("verified_major") else "")
    return f"{esc(db.shown_username(u))} · <code>{u['tg_id']}</code> {flags}".strip()


async def parse_target(text: str) -> dict | None:
    text = text.strip()
    if text.startswith("@") or re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{3,31}", text):
        from support_console import resolve, local_for_native
        try:
            nid = await resolve(text)
            if nid:
                return await local_for_native(nid)
            # No native alias: allow unique bot-side name, never an ambiguous match.
            name = db.norm_username(text)
            rows = await db.fetchall("""SELECT DISTINCT u.* FROM users u
                WHERE lower(replace(u.username,'@',''))=? OR lower(replace(u.active_username,'@',''))=?
                OR u.tg_id IN (SELECT tg_id FROM user_nft_inventory WHERE item_type='username' AND is_active=1 AND lower(value)=?)""",
                (name,name,name))
            return rows[0] if len(rows)==1 else None
        except Exception:
            log.exception("Admin username resolution failed")
            return None
    return await db.find_user(text)


async def native_admin_profile(event, nid: int):
    from support_console import profile, native_label, sql
    u = await profile(nid)
    if not u or u["deleted_at"]:
        return await show(event,"Аккаунт не найден",kb(home_row()))
    names = await sql("""SELECT username FROM peer_usernames WHERE peer_type='user'
        AND peer_id=%s AND active ORDER BY sort_order LIMIT 30""",(nid,))
    aliases = ", ".join("@"+row["username"] for row in names) or "—"
    text = ("🤖 <b>" if u["is_bot"] else "👤 <b>")+esc((u["first_name"] or "")+" "+(u["last_name"] or ""))+"</b>"
    text += "\n"+esc(await native_label(nid))+"\nЮзернеймы: "+esc(aliases)
    text += "\nSCAM: "+("да" if u["scam"] else "нет")+" · FAKE: "+("да" if u["fake"] else "нет")
    rows = []
    if event.from_user.id == config.OWNER_TELEGRAM_ID:
        rows += [[btn("🏷 Выдать NFT-юзернейм",f"adm:nativemint:{nid}")],
                 [btn("🔎 Подробности",f"adm:sup:account:{nid}")]]
    await show(event,text,kb(*rows,home_row()))


async def recovery_target(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith("@") or re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{3,31}", raw):
        from support_console import resolve
        nid = await resolve(raw)
        if not nid:
            raise recovery.RecoveryError("Аккаунт с таким @юзернеймом не найден")
        peer = await recovery.native_by_id(nid)
        if not peer or peer["deleted_at"] or not peer["phone"]:
            raise recovery.RecoveryError("Аккаунт не найден или не имеет номера RiseGram")
        digits = db.digits_of(peer["phone"])
        exact = await recovery.native_info(digits)
        if not exact or int(exact["id"]) != nid:
            raise recovery.RecoveryError("Не удалось однозначно подтвердить аккаунт")
        return digits
    return raw


# ════════════════════════════ главный экран ══════════════════════════════


async def home_screen(event, user: dict, page: int = 0):
    await db.ensure_owner()
    me = await db.get_user(user["tg_id"])
    maint = await db.get_meta("maintenance") == "1"
    pending = await db.scalar("SELECT COUNT(*) FROM scam_requests WHERE status='pending'")
    owner = event.from_user.id == config.OWNER_TELEGRAM_ID
    page = 1 if page==1 else 0
    text = (f"💎 <b>{config.BRAND} — GOD MODE</b>\n"
            f"Страница {page+1}/2 · {'Управление' if page==0 else 'Система'}\n\n"
            f"ID: <b>{me['rise_id']}</b>\n{await client_wallet_line(me)}\n"
            f"Техработы: <b>{'ВКЛ' if maint else 'выкл'}</b>")
    scam_label = f"⚠️ SCAM ({pending})" if pending else "⚠️ SCAM-заявки"
    if page==0:
        rows=[
            [btn("👥 Пользователи","adm:users:0"),btn("📊 Аналитика","adm:stats")],
            [btn("💰 Валюта","adm:grant"),btn("🎁 Подарки","adm:localgifts:0")],
            [btn("☑️ Верификации","adm:vermenu"),btn("🏷 NFT-юзернеймы","adm:nftmenu")],
            [btn("🔨 Банхаммер","adm:ban"),btn("⚠️ SCAM / FAKE","adm:risks")],
            [btn("🛒 Маркет","adm:mkt:0"),btn("🧾 Покупки","adm:buys:0")],
            [btn("♻️ Восстановление","adm:recoverlist"),btn("🗑 Удаление","adm:delphone",style="danger")],
        ]
        if owner:
            rows.append([btn("🛟 Поддержка","adm:sup:home"),btn("⭐ Звёзды поста","adm:postmenu")])
    else:
        rows=[
            [btn("⚡ Функции","adm:feat"),btn("🎯 Задания","adm:tasks")],
            [btn("🧪 Тестеры","adm:testers"),btn(scam_label,"adm:scam:0")],
            [btn("📣 Рассылка","adm:cast"),btn("📜 Логи","adm:logs:0")],
            [btn("📞 Перенос номеров","adm:legacy"),btn("♻️ По @имени","adm:restorephone")],
            [btn("👥 Автовступление","adm:autojoin"),btn("🛠 Техработы","adm:maint")],
        ]
        if owner:
            rows.append([btn("🛡 Администраторы","adm:admins"),btn("💾 База данных","adm:dbmenu")])
    rows += [[btn("◀ 1/2" if page else "• 1/2","adm:home:0"),btn("2/2 ▶" if not page else "• 2/2","adm:home:1")],
             [btn("🏠 Выйти в меню","menu:main")]]
    await show(event,text,kb(*rows))




@admin_router.message(Command("revokeuserverify"))
async def revoke_user_verification(message: Message):
    """Revoke one or both issuer badges from an account, without deleting the user."""
    parts = (message.text or "").split()
    if len(parts) != 3 or parts[2].lower() not in ("risegram", "major", "both"):
        await message.answer("Используйте /revokeuserverify @пользователь risegram|major|both")
        return
    user = await parse_target(parts[1])
    if not user:
        await message.answer("Аккаунт не найден. Укажите RiseGram ID или юзернейм из админки.")
        return
    uid = int(user["tg_id"])
    kinds = ("risegram", "major") if parts[2].lower() == "both" else (parts[2].lower(),)
    for kind in kinds:
        await db.set_verification(uid, kind, False)
    sync = await sync_mtproto_verification(uid, force=True)
    await audit(f"Верификация {', '.join(kinds)} снята с аккаунта {uid} администратором {message.from_user.id}")
    await message.answer(
        f"✅ Галочки {', '.join(kinds)} сняты у {esc(db.shown_username(user))} "
        f"(ID {uid}). Аккаунт сохранён."
        + ("" if sync.get("synced") else "\n⚠️ Обновление клиента пока не подтверждено, повторите синхронизацию.")
    )


@admin_router.message(Command("removenft", "revokenft", "deletenft"))
async def admin_remove_native_nft_username(message: Message, state: FSMContext, *, menu_args=None):
    """Preview before revoking or permanently deleting a *collectible* username.

    Never deletes an ordinary editable username, any profile, chat or session.
    """
    parts = menu_args if menu_args is not None else (message.text or "").split()
    command = parts[0].split("@")[0].lower() if parts else ""
    permanent = command == "/deletenft"
    if permanent and message.from_user.id != config.OWNER_TELEGRAM_ID:
        await message.answer("Полностью удалять NFT может только владелец RiseGram.")
        return
    if len(parts) != 4 or parts[1].lower() not in nftnames.TYPES:
        await message.answer(
            "Укажи <code>/removenft user|group|channel @владелец @NFT</code> "
            "для возврата NFT в резерв или "
            "<code>/deletenft user|group|channel @владелец @NFT</code> "
            "для полного удаления (только владелец).\n"
            "Примеры: <code>/removenft user @paths @rare_name</code>, "
            "<code>/removenft group @mygroup @rare_name</code>, "
            "<code>/deletenft channel @mychannel @rare_name</code>"
        )
        return
    try:
        pending = await nftnames.prepare(
            parts[1].lower(), parts[2], parts[3],
            "delete" if permanent else "revoke",
        )
    except nftnames.NFTUsernameAdminError as exc:
        await message.answer("❌ " + esc(str(exc)))
        return
    except Exception:
        log.exception("NFT username removal preparation failed")
        await message.answer("Сервер не подтвердил владельца NFT, изменений нет.")
        return
    await state.set_state(Adm.nft_confirm)
    await state.update_data(
        nft_pending=pending,
        nft_actor=int(message.from_user.id),
        nft_command_id=f"risegram-nft-{message.chat.id}-{message.message_id}",
    )
    action = "УДАЛИТЬ НАВСЕГДА, включая историю" if permanent else "ОТОЗВАТЬ В РЕЗЕРВ"
    await show(message,
        f"🏷 <b>Подтверждение удаления NFT-юзернейма</b>\n\n"
        f"NFT: <b>@{esc(pending['name'])}</b>\n"
        f"Тип: <b>{esc(pending['kind'])}</b> · Peer ID: <code>{pending['peer_id']}</code>\n"
        f"NFT ID: <code>{esc(pending['asset_id'])}</code>\n"
        f"Действие: <b>{action}</b>\n\n"
        "Обычный юзернейм, аккаунт, группа, канал, номер и сессии останутся нетронутыми",
        kb([btn("✅ Подтвердить", "adm:nftconfirm", style="danger")],
           [btn("✖️ Отмена", "adm:nftcancel")]),
    )


def native_action_error(status: int, result: dict) -> str:
    """Return an actionable owner-only error without exposing backend secrets."""
    detail = " · ".join(str(result[key]) for key in ("code", "error", "message") if result.get(key))
    lowered = detail.lower()
    if ("no space left" in lowered or "sqlstate 53100" in lowered
            or "disk full" in lowered):
        return "На PostgreSQL закончилось место — операции заблокированы. Расширь том Railway и повтори."
    if "already" in lowered and ("exist" in lowered or "taken" in lowered):
        return "NFT уже существует или имя занято — проверь юзернейм перед повтором."
    if "not found" in lowered or "not_found" in lowered:
        return "Объект не найден на MTProto сервере — проверь ID чата и текущую галочку."
    if "invalid" in lowered or "bad_request" in lowered:
        return "Сервер отклонил параметры: " + re.sub(r"\s+", " ", detail)[:180]
    if detail:
        return f"MTProto HTTP {status}: " + re.sub(r"\s+", " ", detail)[:180]
    return f"MTProto HTTP {status} — сервер не выполнил операцию."


@admin_router.message(Command("grantrole","revokerole"), OwnerOnly())
async def role_verification(message: Message, *, menu_args=None):
    import role_marks
    parts=menu_args if menu_args is not None else (message.text or "").split()
    if len(parts)!=3 or not parts[1].startswith("@") or parts[2].lower() not in role_marks.ROLES:
        return await message.answer("/grantrole @username developer|tester|moderator|admin|headadmin|support|hold\n/revokerole @username тип")
    try:
        description=await role_marks.mark(parts[1],parts[2].lower(),parts[0].split("@")[0].lower()=="/revokerole")
        await message.answer("✅ Метка обновлена: "+esc(parts[1])+"\n"+esc(description))
        await audit("Role mark changed "+parts[1]+" "+parts[2])
    except (ValueError,ClientError,TimeoutError,psycopg.Error) as exc:
        await message.answer("❌ "+esc(str(exc)[:180]))

@admin_router.message(Command("grantnft", "grantverify", "revokeverify"))
async def grant_channel_username(message: Message, *, menu_args=None):
    """Mint usernames or grant/revoke native channel and supergroup verification.

    AdminOnly is enforced on the router and the chat is always a private admin
    conversation. Basic (non-super) groups cannot be marked by native MTProto;
    convert them to supergroups first.
    """
    parts = menu_args if menu_args is not None else (message.text or "").split()
    action = parts[0].split("@")[0].lower() if parts else ""
    verification = action in ("/grantverify", "/revokeverify")
    revoking = action == "/revokeverify"
    name_pattern = r"(?:[A-Za-z][A-Za-z0-9_]{0,31}|[А-Яа-яЁё][А-Яа-яЁё0-9_]{0,31})"
    usage = (
        "Используйте /grantverify или /revokeverify @чат official|risegram|major"
        " [group|channel] (тип нужен для числового ID при выдаче)"
        if verification else "Используйте /grantnft @чат @новое_имя. Вместо @чата можно указать ID."
    )
    if len(parts) not in ((3, 4) if verification else (3,)):
        await message.answer(usage)
        return
    target, value = parts[1], parts[2].lower()
    kind_hint = parts[3].lower() if len(parts) == 4 else ""
    numeric = re.fullmatch(r"(?:-100)?[1-9][0-9]{0,18}", target)
    if not numeric and not re.fullmatch("@" + name_pattern, target):
        await message.answer(usage)
        return
    if kind_hint not in ("", "group", "channel"):
        await message.answer(usage)
        return
    if (verification and value not in ("official", "risegram", "major")) or (
        not verification and not re.fullmatch("@?" + name_pattern, value)
    ):
        await message.answer(usage)
        return
    base = config.MTPROTO_ADMIN_API_URL.rstrip("/")
    if not base or not config.MTPROTO_ADMIN_API_TOKEN:
        await message.answer("Сервер выдачи не настроен.")
        return
    headers = {"Authorization": f"Bearer {config.MTPROTO_ADMIN_API_TOKEN}"}
    payload = {
        "command_id": f"channel-{'revoke' if revoking else 'grant'}-{message.chat.id}-{message.message_id}",
        "actor": f"risegram-admin-{message.from_user.id}",
        "reason": "Administrator " + ("revocation" if revoking else "grant") + " for a RiseGram group or channel",
    }
    try:
        async with ClientSession(timeout=ClientTimeout(total=12), headers=headers) as session:
            channel_kind = kind_hint
            if numeric:
                channel_id = int(target.removeprefix("-100"))
            else:
                async with session.get(
                    base + "/v1/channels/resolve-username",
                    params={"username": target[1:].lower()},
                ) as response:
                    response.raise_for_status()
                    peer = await response.json()
                if not peer.get("found"):
                    await message.answer("Публичная группа или канал с таким именем не найдены.")
                    return
                channel_id = int(peer["channel_id"])
                detected = "group" if peer.get("megagroup") else "channel" if peer.get("broadcast") else ""
                if kind_hint and detected and kind_hint != detected:
                    await message.answer("Указанный тип не соответствует типу чата на сервере.")
                    return
                channel_kind = detected or kind_hint
            if not 0 < channel_id < 2**63:
                await message.answer("Некорректный ID чата.")
                return

            entity = "Группа" if channel_kind == "group" else "Канал" if channel_kind == "channel" else "Чат"
            if not verification:
                endpoint = "/v1/collectible-usernames/mint"
                name = value.lstrip("@")
                payload.update(
                    username=name, owner_channel_id=str(channel_id), currency="USD",
                    amount="0", purchase_date=int(message.date.timestamp()),
                )
                success = f"NFT @{name} выдан чату {channel_id}."
            elif value == "official":
                endpoint = "/v1/channels/set-verified"
                payload.update(channel_id=channel_id, verified=not revoking)
                success = f"{entity} {channel_id}: официальная галочка {'снята' if revoking else 'выдана'}."
            else:
                if not revoking and not channel_kind:
                    await message.answer(
                        "Не удалось определить тип чата. Для ID укажите group или channel "
                        "четвёртым аргументом, для публичного чата проверьте обновление MTProto."
                    )
                    return
                async with session.get(
                    base + "/v1/botverification/verifiers",
                    params={"enabled_only": "false" if revoking else "true", "limit": "200"},
                ) as response:
                    response.raise_for_status()
                    verifiers = (await response.json())["rows"]
                matches = [
                    v for v in verifiers
                    if v["CompanyName"].strip().lower() == value and (revoking or v["Enabled"])
                ]
                if len(matches) != 1:
                    await message.answer(f"Для {value} не настроен однозначный верификатор.")
                    return
                endpoint = f"/v1/botverification/marks/{'revoke' if revoking else 'grant'}"
                payload.update(
                    peer_type="channel", peer_id=channel_id,
                    verifier_bot_id=int(matches[0]["BotID"]),
                )
                if not revoking:
                    company = "организацией «RiseGram»" if value == "risegram" else "организацией «Major»"
                    payload["description"] = (
                        f"{entity} верифицирована {company}." if channel_kind == "group"
                        else f"{entity} верифицирован {company}."
                    )
                success = f"{entity} {channel_id}: верификация {value} {'снята' if revoking else 'выдана'}."
            async with session.post(base + endpoint, json=payload) as response:
                status = response.status
                try:
                    result = await response.json(content_type=None)
                except (ValueError, ClientError):
                    result = {}
                if not isinstance(result, dict):
                    result = {}
                if status >= 400 or result.get("status") != "completed":
                    note = native_action_error(status, result)
                    log.warning("Native admin operation rejected: action=%s peer=%s http=%s code=%s",
                                action, channel_id, status, str(result.get("code") or "")[:80])
                    await message.answer("❌ " + esc(note) + "\nНикаких изменений не подтверждено")
                    return
    except (ClientError, TimeoutError, ValueError, KeyError):
        log.exception("Native group/channel verification change failed")
        await message.answer("❌ Ошибка подключения к MTProto серверу. Попробуй после восстановления базы.")
        return
    await audit(f"{success} Администратор: {message.from_user.id}")
    await message.answer("✅ " + esc(success))


@admin_router.message(Command("dbspace"), OwnerOnly())
async def owner_database_space(message: Message):
    """Read-only PostgreSQL table sizes for storage diagnostics; does not delete data."""
    try:
        async with economy.transaction() as conn:
            size_row = await (await conn.execute(
                "SELECT pg_database_size(current_database()) AS bytes"
            )).fetchone()
            rows = await (await conn.execute("""
                SELECT n.nspname AS schema_name, c.relname AS table_name,
                       pg_total_relation_size(c.oid) AS total_bytes
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE c.relkind IN ('r', 'm')
                  AND n.nspname NOT IN ('pg_catalog', 'information_schema')
                  AND n.nspname NOT LIKE 'pg_toast%'
                ORDER BY total_bytes DESC
                LIMIT 12
            """)).fetchall()
        size_mb = int(size_row["bytes"]) / (1024 * 1024)
        lines = [f"📊 <b>PostgreSQL RiseGram</b>",
                 f"Размер базы: <b>{size_mb:.1f} МБ</b> (без WAL и файлов тома)",
                 "Крупнейшие таблицы:"]
        for item in rows:
            mb = int(item["total_bytes"]) / (1024 * 1024)
            name = f"{item['schema_name']}.{item['table_name']}"
            lines.append(f"<code>{esc(name)}</code> — {mb:.1f} МБ")
        lines.append("ℹ️ Только диагностика — аккаунты NFT подарки и сообщения не менялись")
        await message.answer("\n".join(lines))
    except Exception:
        log.exception("Read-only PostgreSQL space diagnostics failed")
        await message.answer("❌ Не удалось прочитать размер базы PostgreSQL — проверь её доступность")


@admin_router.message(Command(config.ADMIN_COMMAND))
async def cmd_admin(message: Message, state: FSMContext, user: dict):
    await state.clear()
    await home_screen(message, user)


@admin_router.message(Command(config.OWNER_COMMAND), OwnerOnly())
async def cmd_owner(message: Message, state: FSMContext, user: dict):
    await state.clear()
    await home_screen(message, user)


# ════════════════════════════ экраны ══════════════════════════════════════


async def screen_gifts(call: CallbackQuery, page: int = 0):
    try:
        all_gifts = await available_telegram_gifts(call.bot)
        catalog = [gift for gift in all_gifts if gift["can_upgrade"]]
        sync_result = await db.sync_gift_catalog_drops(catalog)
    except Exception:  # noqa: BLE001
        log.exception("Telegram NFT gift catalog synchronization failed")
        catalog = []
        sync_result = {"created": 0, "upgraded": 0, "already_live": 0, "skipped": 0}

    per_page = 8
    total = len(catalog)
    pages = max(1, math.ceil(total / per_page))
    page = min(max(0, page), pages - 1)
    current = catalog[page * per_page:(page + 1) * per_page]
    try:
        bot_star_balance = await call.bot.get_my_star_balance()
        bot_stars = int(getattr(bot_star_balance, "amount", 0))
        bot_balance_line = f"Баланс Telegram Stars бота: <b>{fmt_num(bot_stars)}</b> ⭐\n"
    except Exception:  # noqa: BLE001
        bot_stars = None
        bot_balance_line = "Баланс Telegram Stars бота: <b>не удалось проверить</b>\n"

    text = (
        "🎁 <b>NFT-дропы подарков</b>\n\n"
        "Каталог автоматически синхронизируется с Telegram Bot API. В дроп попадают все подарки, "
        "которые бот может отправить и которые поддерживают апгрейд до уникального NFT. "
        "Бот оплачивает подарок и апгрейд своими Telegram Stars.\n\n"
        f"{bot_balance_line}"
        f"NFT-подарков доступно боту: <b>{total}</b> · страница {page + 1}/{pages}\n"
        f"Синхронизация: +{sync_result['created']} новых · {sync_result['upgraded']} обновлено · "
        f"{sync_result['already_live']} уже в продаже\n"
    )
    rows = []
    pair = []
    for gift in current:
        label = f"💎 {gift['emoji']} {gift['name']} · {gift['stars']}+{gift['upgrade_stars']}⭐"
        pair.append(btn(label[:60], f"adm:gifts:pick:{gift['id']}"))
        if len(pair) == 2:
            rows.append(pair)
            pair = []
    if pair:
        rows.append(pair)

    if total == 0:
        text += "\nTelegram сейчас не вернул ни одного подарка, доступного этому боту."
    else:
        text += "\nНажмите на подарок, чтобы выпустить ограниченный тираж."
    active = await db.list_gift_drops(active_only=False)
    text += "\n\n<b>Текущие дропы RiseGram</b>\n"
    for drop in active[:12]:
        status = "активен" if drop["active"] and drop["stock_remaining"] > 0 else "закрыт"
        text += (
            f"\n{esc(drop['emoji'])} {esc(drop['gift_name'])} · "
            f"{drop['stock_remaining']}/{drop['stock_total']} · "
            f"{fmt_num(drop['price_risestars'])} RS · {status}"
        )
    if not active:
        text += "\nПока нет выпущенных дропов."

    nav = []
    if page > 0:
        nav.append(btn("◀️", f"adm:gifts:page:{page - 1}"))
    if page + 1 < pages:
        nav.append(btn("▶️", f"adm:gifts:page:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([btn("🔄 Обновить каталог", "adm:gifts")])
    rows.append(home_row())
    await show(call, text, kb(*rows))


async def screen_stats(call: CallbackQuery):
    s = await db.stats()
    text = (
        "📊 <b>Аналитика</b>\n\n"
        f"👥 Игроков: <b>{s['users']}</b> (+{s['new_day']} за сутки, +{s['new_week']} за неделю)\n"
        f"✅ Верифицировано: {s['verified']} · 🚫 в бане: {s['banned']} · 🧪 тестеров: {s['testers']}\n\n"
        f"⭐ Собрано Stars: <b>{fmt_num(s['stars_total'])}</b> за {s['purchases']} покупок (за сутки {fmt_num(s['stars_day'])})\n"
        f"↩️ Возвращено: {fmt_num(s['refunded'])}\n"
        f"🏷 NFT-юзернеймов: {s['nft_usernames']} · 📞 номеров продано: {s['numbers_sold']}\n"
        f"⚠️ SCAM на проверке: {s['scam_pending']}\n"
        f"🛒 Маркет: активных {s['market_active']} · продано {s['market_sold']} на {fmt_num(s['market_volume'])} RiseStars\n"
        f"🎮 Игр сыграно: {s['games_total']} (активных {s['games_active']}) · 🎯 заданий: {s['tasks']}"
    )
    await show(call, text, kb([btn("🔄 Обновить", "adm:stats")], home_row()))


async def screen_users(call: CallbackQuery, page: int, search: str = ""):
    total = await db.count_users(search)
    rows = await db.list_users(PAGE, page * PAGE, search)
    text = f"👥 <b>Игроки</b> ({total})" + (f"\n🔎 Поиск: <code>{esc(search)}</code>" if search else "") + "\n\n"
    buttons = []
    for u in rows:
        text += f"• {user_line(u)}\n"
        buttons.append([btn(f"{db.shown_username(u)} · {u['tg_id']}", f"adm:u:{u['tg_id']}")])
    if not rows:
        text += "Никого не найдено."
    await show(call, text, kb(*buttons, pager("adm:users", page, total), [btn("🔎 Поиск", "adm:usearch")], home_row()))


async def screen_user(event, uid: int, note: str = ""):
    u = await db.get_user(uid)
    if not u:
        await show(event, "❌ Игрок не найден.", kb(home_row()))
        return
    nfts = len(await db.list_nft(uid))
    st = await db.game_stats(uid)
    spent = await db.scalar("SELECT SUM(price_xtr) FROM purchases WHERE tg_id=? AND status='done'", (uid,))
    text = (
        f"👤 <b>{esc(u.get('name'))}</b>\n"
        f"{user_line(u)}\n"
        f"{config.BRAND} ID: <code>{u['rise_id']}</code>\n"
        f"Номер: <code>{esc(u['phone'])}</code>\n"
        f"Активный юзернейм: {esc(u.get('active_username') or '—')}\n\n"
        f"{await client_wallet_line(u)}\n"
        f"🏷 NFT/номера: {nfts}\n"
        f"💳 Потратил: {fmt_num(spent)} ⭐ · 🎮 игр: {st['played']}\n"
        f"📅 Регистрация: {esc((u.get('created_at') or '')[:16])}\n"
        f"👁 Был: {esc((u.get('last_seen') or '')[:16])}"
    )
    if note:
        text = f"{note}\n\n" + text
    ban_label = "✅ Разбанить" if u["banned"] else "🔨 Забанить"
    rise_label = "❎ Снять RiseGram" if u.get("verified_risegram") else "☑️ Выдать RiseGram"
    major_label = "❎ Снять Major" if u.get("verified_major") else "💠 Выдать Major"
    markup = kb(
        [btn("⭐ RiseStars ±", f"adm:g:{uid}:stars"), btn("💎 Gram ±", f"adm:g:{uid}:gram")],
        [btn(ban_label, f"adm:uban:{uid}", style="success" if u["banned"] else "danger")],
        [btn(rise_label, f"adm:uverrise:{uid}:{0 if u.get('verified_risegram') else 1}"), btn(major_label, f"adm:uvermajor:{uid}:{0 if u.get('verified_major') else 1}")],
        [btn("📞 Сменить номер", f"adm:unum:{uid}"), btn("🏷 Выдать юзернейм", f"adm:uun:{uid}")],
        [btn("📞 Выдать +888", f"adm:uph:{uid}"), btn("🗂 NFT/номера", f"adm:nfts:{uid}")],
        [btn("🔑 Выдать код входа", f"adm:ucode:{uid}"), btn("✉️ Написать", f"adm:umsg:{uid}")],
        [btn("🗑 Удалить игрока", f"adm:udel:{uid}", style="danger")],
        [btn("↩️ К списку", "adm:users:0")],
    )
    await show(event, text, markup)


# ════════════════════════════ маркет, NFT, задания, SCAM, покупки ═════════


async def screen_market(event, page: int, note: str = ""):
    total = await db.market_count()
    rows_db = await db.market_page(None, PAGE, page * PAGE)
    text = f"🛒 <b>Маркет</b> ({total} активных лотов)\n\n"
    if note:
        text = note + "\n\n" + text
    rows = []
    for L in rows_db:
        label = f"@{L['value']}" if L["item_type"] == "username" else db.fmt_number(L["value"])
        text += f"#{L['id']} {esc(label)} — {fmt_num(L['price'])} RS · продавец <code>{L['seller_id']}</code>\n"
        rows.append([btn(f"🗑 Снять #{L['id']} {label}"[:60], f"adm:mktdel:{L['id']}:{page}", style="danger")])
    if not rows_db:
        text += "Лотов нет."
    await show(event, text, kb(*rows, pager("adm:mkt", page, total), home_row()))


async def screen_nfts(event, uid: int, note: str = ""):
    u = await db.get_user(uid)
    if not u:
        await show(event, "❌ Игрок не найден.", kb(home_row()))
        return
    items = await db.list_nft(uid)
    text = f"🗂 <b>NFT и номера {esc(db.shown_username(u))}</b>\n\n"
    if note:
        text = note + "\n\n" + text
    rows = []
    for it in items[:15]:
        label = f"@{it['value']}" if it["item_type"] == "username" else db.fmt_number(it["value"])
        text += f"• {esc(label)} ({'юзернейм' if it['item_type'] == 'username' else 'номер'}, {it['price_stars']} ⭐)\n"
        # Anonymous numbers are identity/login assets and cannot be deleted from bot admin UI.
        if it["item_type"] != "number":
            rows.append([btn(f"🗑 {label}"[:60], "adm:nftcontrol", style="danger")])
    if not items:
        text += "Пусто."
    rows.append([btn("↩️ К игроку", f"adm:u:{uid}")])
    await show(event, text, kb(*rows))


async def fresh_phone(uid: int) -> str:
    for _ in range(100):
        d = db.gen_phone_digits()
        if not await db.number_taken(d):
            await db.set_phone(uid, d)
            return d
    return ""


async def remove_nft(item_id: int, *, force: bool = False) -> str:
    it = await db.get_nft(item_id)
    if not it:
        return "Уже удалено."
    if it["item_type"] == "number" and not force:
        return "Анонимные номера нельзя удалять: они связаны с входом и привязкой аккаунта."
    uid = it["tg_id"]
    await db.delete_nft(item_id)
    u = await db.get_user(uid)
    if it["item_type"] == "username" and u and db.norm_username(u.get("active_username")) == it["value"]:
        await db.set_active_username(uid, None)
    if it["item_type"] == "number" and u and u.get("phone_digits") == it["value"]:
        await fresh_phone(uid)
        return "Номер отозван, игроку выдан новый."
    return "Удалено."


async def revoke_goods(p: dict) -> str:
    """Откатывает выданный товар по payload покупки (насколько это возможно)."""
    kind, *a = (p.get("payload") or "").split(":")
    uid = p["tg_id"]
    try:
        if kind == "rs":
            async with economy.transaction() as conn:
                receipt = await (await conn.execute("SELECT movements FROM risegram_bot_receipts WHERE key=%s",
                                                    ("invoice:" + p["charge_id"],))).fetchone()
                if not receipt:
                    return "историческая покупка, проверьте исходное начисление вручную"
                movements = [(uid, cur, -amount) for uid, cur, amount in receipt["movements"]]
                await economy.post(conn, "invoice-refund:"+p["charge_id"], movements, "Возврат покупки")
            return "начисление RiseStars отменено"
        if kind == "vm" and a[0] == "a":
            await db.set_verification(uid, "major", False)
            return "снят Мажор"
        if kind == "vm":
            await db.execute("DELETE FROM verified_entities WHERE target=?", (a[1],))
            return "снят Мажор с канала"
        if kind == "nu":
            row = await db.fetchone("SELECT id FROM user_nft_inventory WHERE item_type='username' AND value=?", (a[0],))
            return await remove_nft(row["id"], force=True) if row else "юзернейм уже удалён"
        if kind == "nm":
            row = await db.fetchone("SELECT id FROM user_nft_inventory WHERE item_type='number' AND value=?", (a[1],))
            return await remove_nft(row["id"], force=True) if row else "номер уже удалён"
        if kind == "vc":
            if a[0] != "a":
                return "Некорректная покупка"
            bot_id = await db.get_meta("native_issuer:risegramclub")
            if not bot_id:
                return "⚠️ Нужно снять метку вручную"
            async with nftnames.native_session() as session:
                async with session.post(nftnames.base() + "/v1/accounts/resolve-by-phone",
                                        json={"phone": (await db.get_user(uid))["phone"]}) as response:
                    peer = await response.json(content_type=None)
                if not peer.get("found"):
                    return "⚠️ MTProto аккаунт не найден, требуется проверка"
                from issuer_marks import command
                await command(session, "/v1/botverification/marks/revoke",
                              "rg-refund-club-" + str(p["id"]), {
                                  "verifier_bot_id": int(bot_id), "peer_type": "user",
                                  "peer_id": int(peer["user_id"]),
                              })
            await db.execute("DELETE FROM purchased_profile_marks WHERE tg_id=? AND issuer='risegramclub'", (uid,))
            return "метка RiseGram Club снята"
        if kind == "cg":
            peer_kind, peer_id, name = a[0], int(a[1]), a[2]
            snapshot = await nftnames.prepare(peer_kind, str(peer_id), name, "revoke")
            result = await nftnames.execute(snapshot, admin_id=config.OWNER_TELEGRAM_ID,
                                           command_id="rg-refund-nft-" + str(p["id"]))
            return result
        if kind == "vb":
            issuer, peer_id = a[2], int(a[1])
            verifier = await db.get_meta("native_issuer:" + issuer)
            if not verifier:
                return "⚠️ метка не отозвана — не найден верификатор; снимите вручную"
            from issuer_marks import command
            async with nftnames.native_session() as session:
                await command(session, "/v1/botverification/marks/revoke",
                              "rg-refund-mark-" + str(p["id"]), {
                                  "verifier_bot_id": int(verifier), "peer_type": "channel",
                                  "peer_id": peer_id,
                              })
            return "коммерческая метка снята с чата"
        if kind == "sc":
            await db.execute("UPDATE scam_requests SET status='refunded' WHERE id=? AND status IN ('pending','approved')", (int(a[1]),))
            return "метка SCAM снята"
    except (nftnames.NFTUsernameAdminError, native_shop.NativePurchaseError) as exc:
        return "⚠️ отмена товара требует ручной проверки: " + str(exc)[:160]
    except (ClientError, OSError, psycopg.Error) as exc:
        return "⚠️ сервер временно недоступен: " + type(exc).__name__
    except economy.WalletError as exc:
        return f"требуется ручная проверка списания: {exc}"
    except (IndexError, ValueError):
        pass
    return "откат вручную не требуется"


async def refund_stars(bot: Bot, uid: int, charge: str) -> tuple[bool, str]:
    try:
        chat = await db.telegram_chat_id(uid)
        if chat is None:
            return False, "Нет связи с плательщиком Telegram"
        await bot.refund_star_payment(user_id=chat, telegram_payment_charge_id=charge)
        return True, "ok"
    except Exception as e:  # noqa: BLE001 — Telegram возвращает разные ошибки
        log.warning("refund failed: %s", e)
        return False, str(e)[:150]


async def screen_tasks(call: CallbackQuery):
    tasks = await db.list_tasks(active_only=False)
    text = "🎯 <b>Задания</b>\n\n"
    rows = []
    if not tasks:
        text += "Заданий нет."
    for t in tasks:
        lim = f"/{t['max_claims']}" if t["max_claims"] else ""
        text += (
            f"{'🟢' if t['active'] else '⚪'} <b>#{t['id']} {esc(t['title'])}</b> — {fmt_num(t['reward'])} "
            f"({t['claims_count']}{lim})" + (f" · @{esc(t['channel'])}" if t["channel"] else "") + "\n"
        )
        rows.append([btn(("⏸ Выкл #" if t["active"] else "▶️ Вкл #") + str(t["id"]), f"adm:ttog:{t['id']}"), btn(f"🗑 #{t['id']}", f"adm:tdel:{t['id']}", style="danger")])
    rows.append([btn("➕ Создать задание", "adm:tnew", style="success")])
    rows.append(home_row())
    await show(call, text, kb(*rows[:18]))


async def screen_scam(call: CallbackQuery, page: int):
    total = await db.scalar("SELECT COUNT(*) FROM scam_requests WHERE status='pending'")
    rows_db = await db.list_scam("pending", PAGE, page * PAGE)
    approved = await db.scalar("SELECT COUNT(*) FROM scam_requests WHERE status='approved'")
    text = f"⚠️ <b>Жалобы SCAM / FAKE</b> (на проверке: {total}, одобренных: {approved})\n\n"
    rows = []
    for r in rows_db:
        text += f"<b>#{r['id']}</b> [{esc(r.get('label') or 'SCAM')}] @{esc(r['target'])} ({esc(r['kind'])})\nОт <code>{r['requester_id']}</code>: {esc(r['reason'])}\n\n"
        rows.append([btn(f"✅ #{r['id']}", f"adm:scam:ok:{r['id']}", style="success"), btn(f"❌ #{r['id']} + возврат", f"adm:scam:no:{r['id']}", style="danger")])
    if not rows_db:
        text += "Очередь пуста."
    rows.append(pager("adm:scam", page, total))
    rows.append([btn("🧹 Снять метку по юзернейму", "adm:scamrevoke")])
    rows.append(home_row())
    await show(call, text, kb(*rows))


async def scam_decide(call: CallbackQuery, rid: int, approve: bool):
    req = await db.get_scam_request(rid)
    if not req or req["status"] != "pending":
        await call.answer("Заявка уже обработана.", show_alert=True)
        return
    if approve:
        kind = "user" if req["kind"] == "account" else req["kind"]
        label = req.get("label") or "SCAM"
        try:
            await native_shop.change_risk_label(
                "@"+req["target"], kind, label, True, req["reason"],
                int(call.from_user.id), f"report-{rid}")
        except (native_shop.NativePurchaseError, psycopg.Error, OSError) as exc:
            return await call.answer("MTProto не подтвердил метку: " + str(exc)[:130], show_alert=True)
        if not await db.resolve_scam(rid, "approved", config.OWNER_ID):
            return await call.answer("Заявка уже обработана.", show_alert=True)
        await audit(f"{label} #{rid} подтверждена в MTProto: @{req['target']}")
        await safe_send(call.bot, req["requester_id"],
            f"✅ Жалоба #{rid}: метка {label} на @{esc(req['target'])} подтверждена")
        note = f"✅ Заявка #{rid} подтверждена сервером"
    else:
        if not await db.resolve_scam(rid, "rejected", config.OWNER_ID):
            return await call.answer("Заявка уже обработана.", show_alert=True)
        if req["charge_id"]:
            ok, err = await refund_stars(call.bot, req["requester_id"], req["charge_id"])
            await db.execute("UPDATE purchases SET status=? WHERE charge_id=?",
                             ("refunded" if ok else "failed", req["charge_id"]))
            await audit(f"SCAM #{rid} отклонена, возврат {'ok' if ok else err}")
            note = "Заявка отклонена — " + ("⭐ возвращены" if ok else "возврат требует проверки")
        else:
            await audit(f"Бесплатная жалоба SCAM #{rid} отклонена")
            note = f"Заявка #{rid} отклонена"
        await safe_send(call.bot, req["requester_id"],
                        f"ℹ️ Ваша жалоба #{rid} отклонена модерацией")
    try:
        await call.message.edit_text(note, reply_markup=kb([btn("⚠️ К заявкам", "adm:scam:0")], home_row()))
    except TelegramBadRequest:
        await call.message.answer(note, reply_markup=kb([btn("⚠️ К заявкам", "adm:scam:0")], home_row()))


async def screen_buys(call: CallbackQuery, page: int):
    total = await db.scalar("SELECT COUNT(*) FROM purchases")
    rows_db = await db.recent_purchases(PAGE, page * PAGE)
    icon = {"done": "✅", "refunded": "↩️", "failed": "⚠️", "processing": "⏳"}
    text = f"🧾 <b>Покупки</b> ({total})\n\n"
    rows = []
    for p in rows_db:
        text += f"{icon.get(p['status'], '•')} #{p['id']} <code>{p['tg_id']}</code> · {esc(p['item_desc'])} · {p['price_xtr']} ⭐\n"
        rows.append([btn(f"#{p['id']} {p['item_desc']}"[:60], f"adm:buy:{p['id']}")])
    if not rows_db:
        text += "Пока пусто."
    rows.append(pager("adm:buys", page, total))
    rows.append(home_row())
    await show(call, text, kb(*rows))


async def screen_buy(call: CallbackQuery, pid: int):
    p = await db.get_purchase(pid)
    if not p:
        await call.answer("Не найдено.", show_alert=True)
        return
    text = (
        f"🧾 <b>Покупка #{p['id']}</b>\n\nИгрок: <code>{p['tg_id']}</code>\nТовар: {esc(p['item_desc'])}\n"
        f"Сумма: {p['price_xtr']} ⭐\nСтатус: {esc(p['status'])}\nДата: {esc((p['created_at'] or '')[:16])}\n"
        f"Платёж: <code>{esc(p['charge_id'] or '—')}</code>"
    )
    rows = []
    if p["status"] == "done" and p["charge_id"]:
        rows.append([btn("↩️ Вернуть ⭐ и откатить товар", f"adm:refund:{pid}", style="danger")])
    rows.append([btn("↩️ К списку", "adm:buys:0")])
    await show(call, text, kb(*rows))


async def screen_testers(call: CallbackQuery):
    t = await db.list_testers()
    text = "🧪 <b>Тестеры</b>\n\nКаждый тестер имеет полный доступ к /admin и ко всем операциям панели\n\n"
    rows = []
    for x in t[:15]:
        text += f"• @{esc(x['username'] or '—')} · <code>{x['tg_id']}</code>\n"
        rows.append([btn(f"➖ {x['username'] or x['tg_id']}"[:60], f"adm:trm:{x['tg_id']}", style="danger")])
    if not t:
        text += "Пока никого."
    rows.append([btn("➕ Добавить тестера", "adm:tadd", style="success")])
    rows.append(home_row())
    await show(call, text, kb(*rows))


async def screen_admins(call: CallbackQuery):
    admins = await db.list_admins()
    text = "🛡 <b>Администраторы RiseGram</b>\n\nАдминистраторы и тестеры могут пользоваться полной панелью командой /admin\n\n"
    rows = []
    if not admins:
        text += "Дополнительных администраторов пока нет.\n"
    for item in admins[:30]:
        name = item.get("username") or item.get("name") or str(item["tg_id"])
        text += f"• {esc(name)} · <code>{item['tg_id']}</code>\n"
        rows.append([btn(f"➖ {name}"[:60], f"adm:arm:{item['tg_id']}", style="danger")])
    rows.append([btn("➕ Добавить администратора", "adm:aadd", style="success")])
    rows.append(home_row())
    await show(call, text, kb(*rows))


async def screen_legacy(call: CallbackQuery):
    rows_db = await db.list_legacy(15)
    text = (
        "📞 <b>Перенос старых номеров</b>\n\nДобавленные здесь номера игрок может привязать командой /migrate.\n\n"
    )
    for r in rows_db:
        text += f"• <code>{esc(db.fmt_number(r['phone_digits']))}</code> — {'привязан ' + str(r['claimed_by']) if r['claimed_by'] else 'свободен'}\n"
    if not rows_db:
        text += "Список пуст."
    await show(call, text, kb([btn("➕ Добавить номер", "adm:lgadd", style="success")], home_row()))


async def screen_features(call: CallbackQuery):
    flags = {f["feature_name"]: f["is_enabled"] for f in await db.get_flags()}
    rows = []
    for name, title in config.FEATURES.items():
        on = bool(flags.get(name, 1))
        rows.append([btn(f"{'🟢' if on else '🔴'} {title}", f"adm:ft:{name}", style="success" if on else "danger")])
    rows.append(home_row())
    await show(call, "⚡ <b>Тумблеры функций</b>\n\nНажмите, чтобы включить или выключить модуль. Работает сразу.", kb(*rows))


async def screen_logs(call: CallbackQuery, page: int):
    total = await db.scalar("SELECT COUNT(*) FROM admin_logs")
    logs = await db.get_logs(PAGE, page * PAGE)
    text = f"📜 <b>Логи</b> ({total})\n\n"
    text += "\n".join(f"<code>{esc((l['created_at'] or '')[5:16])}</code> {esc(l['action_text'])}" for l in logs) or "Пусто."
    await show(call, text, kb(pager("adm:logs", page, total), home_row()))


# ════════════════════════════ ввод текста (FSM) ══════════════════════════════


async def ask(call: CallbackQuery, state: FSMContext, act: str, prompt: str, back: str = "adm:home", **data):
    await state.set_state(Adm.input)
    await state.set_data({"act": act, **data})
    await show(call, prompt, kb(cancel_row(back)))


def parse_amount(text: str) -> float | None:
    try:
        v = round(float((text or "").replace(" ", "").replace(",", ".")), 2)
    except ValueError:
        return None
    return v if math.isfinite(v) and abs(v) <= 1e12 else None


async def apply_balance(bot, uid, cur, delta, key):
    col = "risestars" if cur == "stars" else "gram_balance"
    try:
        balance = await db.add_balance(uid,col,delta,key=key,title="Операция администратора")
    except economy.WalletError as exc:
        return str(exc)
    await audit(f"Баланс {uid}: {delta:+g} {cur}")
    await safe_send(bot,uid,f"Изменение баланса: {delta:+g} {cur}.")
    return f"Готово. Баланс: {fmt_num(balance)} {cur}."


async def deletion_preview(event, state, raw):
    if getattr(event, "from_user", None) is None or event.from_user.id != config.OWNER_TELEGRAM_ID:
        return await event.answer("Только владелец может удалять аккаунты")
    try:
        info = await recovery.preview(await recovery_target(raw))
        if info["receipt"] and info["receipt"]["status"] in recovery.ACTIVE:
            return await show(event,
                "Аккаунт уже ожидает удаления. Его можно восстановить.",
                kb([btn("↩️ Восстановить", "adm:recoverpick:" + info["phone"])], home_row()))
        if info["pending"]:
            return await show(event,
                "Удаление уже запрошено из клиента. Его можно отменить в разделе восстановления",
                kb([btn("↩️ Восстановить", "adm:recoverpick:" + info["phone"])], home_row()))
        if not info["native"]:
            return await event.answer("Нет native аккаунта RiseGram. Удаление остановлено.")
    except (recovery.RecoveryError, psycopg.Error, ClientError, TimeoutError, OSError) as exc:
        log.warning("Deletion preview failed: %s", type(exc).__name__)
        return await event.answer("❌ " + esc(str(exc)[:200]))
    token = secrets.token_hex(12)
    await state.set_state(Adm.delete_confirm)
    await state.set_data({
        "key": token, "phone": info["phone"],
        "native_id": int(info["native"]["id"]),
        "local_id": int(info["local"]["tg_id"]) if info["local"] else 0,
        "actor": event.from_user.id, "created": time.time(),
    })
    title = esc(info["local"]["name"]) if info["local"] else "native ID " + str(info["native"]["id"])
    await show(event,
        f"🗑 <b>Удалить аккаунт {title}?</b>\n\n"
        f"Номер: <code>+{info['phone']}</code>\n\n"
        "Аккаунт будет ограничен на сервере MTProto на 30 дней.\n"
        "В течение этого срока его можно восстановить по номеру из админки.\n"
        "Сообщения, NFT, балансы и сессии не удаляются на этапе ожидания.\n"
        "После 30 дней удаление окончательное.",
        kb([btn("🗑 Запланировать удаление", "adm:delconfirm:" + token, style="danger")],
           [btn("↩️ Отмена", "adm:home")]))


async def confirm_account_deletion(call, state, key):
    data = await state.get_data()
    if (not key or key != data.get("key")
            or await state.get_state() != Adm.delete_confirm.state
            or call.from_user.id != config.OWNER_TELEGRAM_ID
            or data.get("actor") != call.from_user.id
            or time.time() - float(data.get("created") or 0) > 600):
        return await call.answer("Подтверждение устарело. Выбери номер снова.", show_alert=True)
    try:
        result = await recovery.schedule(
            data["phone"], call.from_user.id, key,
            expected_native_id=int(data["native_id"]),
            expected_local_id=int(data["local_id"]),
        )
    except (recovery.RecoveryError, psycopg.Error, ClientError, TimeoutError, OSError) as exc:
        log.warning("Recoverable deletion failed: %s", type(exc).__name__)
        return await show(call, "❌ Не удалось запланировать удаление: " + esc(str(exc)[:180]),
                          kb(home_row()))
    await state.clear()
    await audit(f"30-дневное удаление: phone=+{data['phone']} native={result['native_id']} "
                f"receipt={result['id']} admin={call.from_user.id}")
    return await show(call,
        f"✅ Аккаунт ограничен на MTProto до <b>{result['due']} UTC</b>\n"
        "Его данные не уничтожены. Восстановление через /owner → «Восстановить по @юзернейму»",
        kb([btn("♻️ Восстановить", "adm:recoverpick:" + data["phone"])],home_row()))


async def restoration_preview(event, state, raw):
    if getattr(event,"from_user",None) is None or event.from_user.id != config.OWNER_TELEGRAM_ID:
        return await event.answer("Только владелец может восстанавливать аккаунты")
    try:
        info = await recovery.preview(await recovery_target(raw))
        if not info["native"]:
            raise recovery.RecoveryError("Native аккаунт отсутствует. Требуется ручное восстановление из резервной копии.")
        record = info["receipt"]
        is_admin_pending = bool(record and record["status"] in ("active","preparing"))
        is_client_pending = bool(info["pending"])
        if not is_admin_pending and not is_client_pending:
            raise recovery.RecoveryError("Для этого номера нет удаления, которое можно отменить")
        if record and record["status"] == "executing":
            raise recovery.RecoveryError("Окончательное удаление уже началось")
        native_id = int(info["native"]["id"])
    except (recovery.RecoveryError, psycopg.Error, ClientError, TimeoutError, OSError) as exc:
        return await show(event, "❌ " + esc(str(exc)[:210]),
                          kb([btn("📞 Другой номер", "adm:restorephone")],home_row()))
    token = secrets.token_hex(12)
    await state.set_state(Adm.restore_confirm)
    await state.set_data({"key": token, "phone": info["phone"], "native_id": native_id,
                          "record_id": int(record["id"]) if is_admin_pending else 0,
                          "actor": event.from_user.id, "created": time.time()})
    desc = []
    if is_admin_pending:
        desc.append(f"Удаление администратором до <b>{esc(record['execute_at'])} UTC</b>")
    if is_client_pending:
        desc.append(f"Удаление из клиента до <b>{info['pending']['execute_at']}</b>")
    return await show(event,
        f"♻️ <b>Восстановить аккаунт?</b>\n\n"
        f"Номер: <code>+{info['phone']}</code>\n"
        f"Native ID: <code>{native_id}</code>\n\n"
        + "\n".join(desc) + "\n\n"
        "Отмена удаления вернёт доступ к тому же профилю, без смены ID, NFT и балансов",
        kb([btn("✅ Восстановить аккаунт", "adm:restoreconfirm:" + token, style="success")],
           [btn("↩️ Отмена", "adm:home")]))


async def confirm_account_restoration(call, state, key):
    data = await state.get_data()
    if (not key or key != data.get("key")
            or await state.get_state() != Adm.restore_confirm.state
            or call.from_user.id != config.OWNER_TELEGRAM_ID
            or data.get("actor") != call.from_user.id
            or time.time() - float(data.get("created") or 0) > 600):
        return await call.answer("Подтверждение устарело. Проверь номер снова.", show_alert=True)
    try:
        result = await recovery.restore(
            data["phone"], call.from_user.id,
            expected_record_id=int(data["record_id"]),
            expected_native_id=int(data["native_id"]),
        )
    except (recovery.RecoveryError, psycopg.Error, ClientError, TimeoutError, OSError) as exc:
        log.warning("Account recovery failed: %s", type(exc).__name__)
        return await show(call, "❌ Сервер не подтвердил восстановление: " + esc(str(exc)[:180]),
                          kb([btn("🔄 Повторить", "adm:recoverpick:" + data["phone"])],home_row()))
    await state.clear()
    await audit(f"Восстановление: phone=+{data['phone']} native={result['native_id']} "
                f"actor={call.from_user.id} admin={result['admin_cancelled']} "
                f"client={result['client_cancelled']}")
    return await show(call,
        f"✅ <b>Аккаунт +{data['phone']} восстановлен на сервере RiseGram</b>\n"
        f"Native ID: <code>{result['native_id']}</code>\n"
        "Профиль, сообщения, NFT и балансы сохранены",
        kb(home_row()))


async def screen_recoverable(event):
    if getattr(event,"from_user",None) is None or event.from_user.id != config.OWNER_TELEGRAM_ID:
        return await event.answer("Только владелец")
    rows = await recovery.recoverable_records()
    description = "♻️ <b>Аккаунты с удалением на ожидании</b>\n\n"
    buttons = []
    for item in rows:
        description += (f"• <code>+{item['phone_digits']}</code> · "
                        f"{esc(item['status'])} · до {esc(item['execute_at'])} UTC\n")
        if item["status"] in ("active","preparing"):
            buttons.append([btn("↩️ Вернуть +" + item["phone_digits"],
                                "adm:recoverpick:" + item["phone_digits"])])
    if not rows:
        description += "В списке нет удалений из админки\n"
    description += "\nУдаления из клиента ищутся по номеру отдельно"
    return await show(event, description,
        kb(*buttons[:12], [btn("📞 Восстановить по @юзернейму", "adm:restorephone", style="success")],
           home_row()))


@admin_router.message(StateFilter(Adm.input), F.text)
async def on_input(message: Message, state: FSMContext, user: dict):
    data = await state.get_data()
    act, text, bot = data.get("act"), (message.text or "").strip(), message.bot
    if text.startswith("/"):  # команда отменяет ввод
        await state.clear()
        if text.lstrip("/").split("@")[0] in (config.ADMIN_COMMAND, config.OWNER_COMMAND):
            await home_screen(message, user)
        return

    async def done(note: str, markup=None):
        await state.clear()
        await message.answer(note, reply_markup=markup or kb(home_row()))

    async def again(note: str):
        await message.answer(note, reply_markup=kb(cancel_row()))

    # Button flows use the same validated command implementations.
    if act in ("menuverify","menunfttarget","menunftname","menupost"):
        if act=="menuverify":
            kind=data.get("kind")
            mode=data.get("mode")
            if mode not in ("grant","revoke") or not text.startswith("@") or len(text.split())!=1:
                return await again("Введите один @юзернейм RiseGram")
            import role_marks
            try:
                peer,pid,entity=await role_marks.lookup(text)
                if kind in role_marks.ROLES:
                    if message.from_user.id!=config.OWNER_TELEGRAM_ID:
                        return await done("Только владелец")
                    await state.clear()
                    return await role_verification(message,menu_args=["/grantrole" if mode=="grant" else "/revokerole",text,kind])
                if kind not in ("risegram","major","official"):
                    return await done("Неизвестный вид")
                if peer=="channel":
                    await state.clear()
                    return await grant_channel_username(message,menu_args=["/grantverify" if mode=="grant" else "/revokeverify",str(pid),kind,"group" if entity["megagroup"] else "channel"])
                from support_console import local_for_native
                local=await local_for_native(pid)
                if local and not entity["is_bot"] and kind!="official":
                    await db.set_verification(int(local["tg_id"]),kind,mode=="grant")
                    result=await sync_mtproto_verification(int(local["tg_id"]),force=True)
                    return await done(("✅ Изменено" if result.get("synced") and not result.get("role_preserved") else "⚠️ Синхронизация не подтвердила замену метки (возможен служебный статус)")+" · "+esc(text))
                # Native-only users/bots: same native admin interface as channels.
                from issuer_marks import command
                async with economy.transaction() as conn:
                    current=await (await conn.execute("""SELECT v.verifier_bot_id,b.company_name FROM custom_verifications v JOIN bot_verifier_settings b ON b.bot_id=v.verifier_bot_id WHERE v.peer_type='user' AND v.peer_id=%s""",(pid,))).fetchone()
                    verifier=await (await conn.execute("SELECT bot_id FROM bot_verifier_settings WHERE lower(company_name)=%s AND enabled",(kind,))).fetchone()
                if kind!="official" and not verifier:
                    return await done("Верификатор не найден")
                if kind!="official" and mode=="revoke" and (not current or current["verifier_bot_id"]!=verifier["bot_id"]):
                    return await done("У аккаунта нет выбранной метки")
                async with nftnames.native_session() as session:
                    key=f"rg-menuverify-{message.chat.id}-{message.message_id}"
                    if kind=="official":
                        await command(session,"/v1/accounts/set-verified",key,{"user_id":pid,"verified":mode=="grant"})
                    else:
                        fields={"verifier_bot_id":int(verifier["bot_id"]),"peer_type":"user","peer_id":pid}
                        if mode=="grant":
                            fields["description"]=("Бот" if entity["is_bot"] else "Аккаунт")+" верифицирован организацией «"+("RiseGram" if kind=="risegram" else "Major")+"»."
                        await command(session,"/v1/botverification/marks/"+mode,key,fields)
                await audit(f"Native verification {mode} {kind} {pid}")
                return await done("✅ Верификация обновлена · "+esc(text))
            except (ValueError,ClientError,TimeoutError,psycopg.Error) as exc:
                return await again("❌ "+esc(str(exc)[:180]))
        if act=="menunfttarget":
            import role_marks
            try:
                if not text.startswith("@") or len(text.split())!=1:
                    raise ValueError("Введите один @юзернейм")
                peer,pid,entity=await role_marks.lookup(text)
                kind="user" if peer=="user" else "group" if entity["megagroup"] else "channel"
                await state.update_data(act="menunftname",target=text,peer_id=pid,target_kind=kind)
                return await message.answer("Введите NFT-юзернейм, например @а или @rare",reply_markup=kb(cancel_row("adm:nftmenu")))
            except (ValueError,psycopg.Error) as exc:
                return await again("❌ "+esc(str(exc)[:180]))
        if act=="menunftname":
            try:
                name=nftnames.normalize(text)
                mode=data.get("mode")
                target=data["target"]
                kind=data["target_kind"]
                pid=int(data["peer_id"])
                import role_marks
                peer,actual,_=await role_marks.lookup(target)
                if actual!=pid or peer!=("user" if kind=="user" else "channel"):
                    raise ValueError("Владелец имени изменился")
                if mode in ("revoke","delete"):
                    if mode=="delete" and message.from_user.id!=config.OWNER_TELEGRAM_ID:
                        return await done("Только владелец")
                    return await admin_remove_native_nft_username(message,state,menu_args=["/deletenft" if mode=="delete" else "/removenft",kind,target,name])
                if mode!="grant" or message.from_user.id!=config.OWNER_TELEGRAM_ID:
                    return await done("Только владелец")
                key=f"rg-menu-nft-{message.chat.id}-{message.message_id}"
                payload={"username":name,"owner_user_id" if kind=="user" else "owner_channel_id":str(pid),"currency":"USD","amount":"0","purchase_date":int(time.time()),"actor":"risegram-owner","reason":"Owner requested NFT grant from menu"}
                async with nftnames.native_session() as session:
                    for dry in (True,False):
                        async with session.post(nftnames.base()+"/v1/collectible-usernames/mint",json={**payload,"command_id":key+("-dry" if dry else ""),"dry_run":dry}) as response:
                            result=await response.json(content_type=None)
                            if response.status!=200 or result.get("status")!="completed" or (dry and result.get("dry_run") is not True):
                                raise ValueError(native_action_error(response.status,result))
                await audit(f"NFT @{name} granted from menu to {kind}:{pid}")
                return await done("✅ NFT @"+esc(name)+" выдан "+esc(target))
            except (ValueError,nftnames.NFTUsernameAdminError,ClientError,TimeoutError,psycopg.Error) as exc:
                return await again("❌ "+esc(str(exc)[:180]))
        if act=="menupost":
            from channel_stars_owner import preview
            await state.clear()
            return await preview(message,user,menu_args=["/poststars",*text.split()])

    # ── поиск и выбор игрока ──
    if act == "delete_phone":
        return await deletion_preview(message, state, text)
    if act == "restore_phone":
        return await restoration_preview(message, state, text)
    if act == "usearch":
        await state.clear()
        if text.startswith("@") or re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{3,31}", text):
            exact = await parse_target(text)
            if exact:
                return await screen_user(message, int(exact["tg_id"]))
            from support_console import resolve
            nid = await resolve(text)
            if nid:
                return await native_admin_profile(message,nid)
            if text.startswith("@"):
                return await message.answer("Аккаунт с таким @юзернеймом не найден", reply_markup=kb(home_row()))
        total = await db.count_users(text)
        rows_db = await db.list_users(PAGE, 0, text)
        out = f"🔎 <b>Поиск «{esc(text)}»</b> ({total})\n\n"
        rows = [[btn(f"{db.shown_username(u)} · {u['tg_id']}", f"adm:u:{u['tg_id']}")] for u in rows_db]
        out += "\n".join(f"• {user_line(u)}" for u in rows_db) or "Ничего не найдено."
        await message.answer(out, reply_markup=kb(*rows, home_row()))
        return
    if act in ("grant_target", "inv_target", "ban_target", "admin_add"):
        t = await parse_target(text)
        if not t:
            if act == "inv_target" and message.from_user.id == config.OWNER_TELEGRAM_ID:
                from support_console import resolve, native_label
                nid = await resolve(text)
                if nid:
                    return await ask(message,state,"native_mint",
                        "🏷 "+esc(await native_label(nid))+"\nВведите новый NFT-юзернейм (один, от 1 символа)",uid=nid)
            return await again("❌ Аккаунт не найден или не связан с ботом. Введите @юзернейм:")
        await state.clear()
        if act == "grant_target":
            await message.answer(
                f"👤 {user_line(t)}\n\nВыберите валюту:",
                reply_markup=kb([btn("⭐ RiseStars", f"adm:g:{t['tg_id']}:stars", style="success"), btn("💎 Gram", f"adm:g:{t['tg_id']}:gram", style="primary")], cancel_row()),
            )
        elif act == "ban_target":
            if t["tg_id"] == config.OWNER_ID:
                return await done("❌ Владельца банить нельзя.")
            await db.set_banned(t["tg_id"], not t["banned"])
            await audit(f"{'Бан' if not t['banned'] else 'Разбан'}: {db.shown_username(t)} ({t['tg_id']})")
            await message.answer(
                f"✅ {user_line(t)}: {'разбанен' if t['banned'] else 'забанен'}.", reply_markup=kb([btn("👤 Карточка", f"adm:u:{t['tg_id']}")], home_row())
            )
        elif act == "admin_add":
            if int(t["tg_id"]) == int(config.OWNER_ID):
                return await done("Владелец уже имеет максимальные права.")
            created = await db.add_admin(t["tg_id"], t.get("username"), int(user["tg_id"]))
            if not created:
                return await done("Этот пользователь уже администратор.")
            await audit(f"Администратор добавлен: {db.shown_username(t)} ({t['tg_id']})")
            target_chat = await db.telegram_chat_id(t["tg_id"])
            if target_chat:
                await safe_send(bot, target_chat, "🛡 Вам выданы права администратора RiseGram. Откройте личный чат с ботом и используйте /admin.")
            return await done(
                f"✅ {esc(db.shown_username(t))} добавлен в администраторы. Команда: /admin",
                kb([btn("🛡 К администраторам", "adm:admins")], home_row()),
            )
        else:
            await ask_usernames(message, state, t)
        return

    if act == "native_mint":
        if message.from_user.id != config.OWNER_TELEGRAM_ID:
            return await done("Только владелец")
        from support_console import profile, native_label, sql
        nid = int(data["uid"])
        try:
            name = nftnames.normalize(text)
            u = await profile(nid)
            if not u or u["deleted_at"]:
                raise ValueError("Аккаунт не найден")
            payload = dict(username=name, owner_user_id=str(nid), currency="USD",amount="0",
                purchase_date=int(time.time()), actor="risegram-owner",
                reason="Owner-requested native NFT username grant")
            key = f"rg-admin-native-nft-{message.chat.id}-{message.message_id}"
            async with nftnames.native_session() as session:
                for dry in (True,False):
                    async with session.post(nftnames.base()+"/v1/collectible-usernames/mint",
                        json={**payload,"command_id":key+("-dry" if dry else ""),"dry_run":dry}) as response:
                        result=await response.json(content_type=None)
                        if response.status!=200 or result.get("status")!="completed" or (dry and not result.get("dry_run")):
                            raise ValueError("Сервер отказал: "+str(result.get("code") or result.get("error") or response.status)[:150])
            rows=await sql("""SELECT c.id FROM collectible_usernames c JOIN peer_usernames p ON p.collectible_id=c.id
                WHERE c.username_lower=%s AND c.owner_peer_type='user' AND c.owner_peer_id=%s
                  AND c.status='owned' AND p.peer_type='user' AND p.peer_id=%s AND p.active""",(name,nid,nid))
            if not rows:
                raise ValueError("MTProto не подтвердил активный NFT")
            await audit(f"Native NFT @{name} выдан {nid}")
            return await done("✅ NFT @"+esc(name)+" выдан "+esc(await native_label(nid)),kb([btn("Карточка",f"adm:nativeprofile:{nid}")],home_row()))
        except (ValueError,nftnames.NFTUsernameAdminError,psycopg.Error,ClientError,TimeoutError,OSError) as exc:
            return await again("❌ "+esc(str(exc)[:180]))

    # ── валюта ──
    if act == "g_amount":
        v = parse_amount(text)
        if v is None or v == 0:
            return await again("❌ Введите число, например <code>1000</code> или <code>-500</code>:")
        note = await apply_balance(bot, data["uid"], data["cur"], v, f"admin:{message.chat.id}:{message.message_id}")
        return await done(note, kb([btn("👤 Карточка", f"adm:u:{data['uid']}")], home_row()))

    # ── действия над игроком ──
    if act == "unum":
        d = db.digits_of(text)
        if len(d) < 7 or len(d) > 15:
            return await again("❌ Номер должен содержать 7–15 цифр:")
        if await db.number_taken(d):
            return await again("❌ Номер занят другим игроком:")
        ok = await db.grant_number_to_user(data["uid"], d, 0, True)
        await audit(f"Номер {data['uid']} → {d}")
        return await done("✅ Номер изменён." if ok else "❌ Не удалось.", kb([btn("👤 Карточка", f"adm:u:{data['uid']}")], home_row()))
    if act == "uun":
        names = parse_usernames(text)
        if not names:
            return await again("❌ Пришлите один или несколько юзернеймов через пробел, запятую или с новой строки (до 30):")
        uid = data["uid"]
        u = await db.get_user(uid)
        if not u:
            return await done("❌ Игрок не найден.")
        given, skipped = [], []
        for n in names:
            if not NAME_RE.match(n) or "__" in n or n.endswith("_"):
                skipped.append(f"@{n} — неверный формат")
            elif await db.username_taken(n, ignore_reserved=True, exclude_user=uid):
                skipped.append(f"@{n} — занят")
            elif await db.add_nft(uid, "username", n, 0):
                given.append(n)
            else:
                skipped.append(f"@{n} — уже у игрока")
        if given and not db.norm_username((await db.get_user(uid)).get("active_username")):
            await db.set_active_username(uid, given[0])
        if given:
            await audit(f"Юзернеймы {', '.join('@' + g for g in given)} → {uid}")
            await safe_send(bot, uid, "🎁 Вам выдали NFT-юзернеймы: " + ", ".join(f"<b>@{esc(g)}</b>" for g in given) + ".\nВыбрать активный можно в «Мои NFT и номера».")
        out = f"✅ Выдано ({len(given)}): " + (", ".join("@" + esc(g) for g in given) or "—")
        if skipped:
            out += "\n⚠️ Пропущено:\n" + "\n".join(esc(x) for x in skipped)
        return await done(out, kb([btn("👤 Карточка", f"adm:u:{uid}")], home_row()))
    if act == "uph":
        nums: list[str] = []
        for chunk in re.split(r"[\n,;]+", text):
            chunk = chunk.strip()
            if not chunk:
                continue
            # «8881234 8880001» через пробел — это разные номера; «+888 0123 4567» с пробелами — один
            parts_ = chunk.split()
            cands = parts_ if all(len(db.digits_of(p)) >= 7 for p in parts_) else [chunk]
            for c in cands:
                d = db.digits_of(c)
                if d and d not in nums:
                    nums.append(d)
        nums = nums[:30]
        if not nums:
            return await again("❌ Пришлите номера +888 (цифры, 7–15):")
        uid = data["uid"]
        if not await db.get_user(uid):
            return await done("❌ Игрок не найден.")
        given, skipped = [], []
        for d in nums:
            if not d.startswith("888") or not 7 <= len(d) <= 15:
                skipped.append(f"{d} — не +888 или длина не 7–15")
            elif await db.number_taken(d):
                skipped.append(f"{db.fmt_number(d)} — занят")
            elif await db.add_nft(uid, "number", d, 0):
                given.append(d)
            else:
                skipped.append(f"{db.fmt_number(d)} — не удалось")
        if given:
            await audit(f"Номера {', '.join(given)} → {uid}")
            await safe_send(bot, uid, "🎁 Вам выдали анонимные номера: " + ", ".join(f"<b>{esc(db.fmt_number(g))}</b>" for g in given) + ".\nВыбрать текущий можно в «Мои NFT и номера».")
        out = f"✅ Выдано ({len(given)}): " + (", ".join(esc(db.fmt_number(g)) for g in given) or "—")
        if skipped:
            out += "\n⚠️ Пропущено:\n" + "\n".join(esc(x) for x in skipped)
        return await done(out, kb([btn("👤 Карточка", f"adm:u:{uid}")], home_row()))
    if act == "umsg":
        ok = await safe_send(bot, data["uid"], f"✉️ <b>Сообщение от администрации</b>\n\n{esc(text)}")
        await audit(f"Сообщение игроку {data['uid']}")
        return await done("✅ Отправлено." if ok else "❌ Не доставлено (игрок заблокировал бота).", kb([btn("👤 Карточка", f"adm:u:{data['uid']}")], home_row()))
    if act in ("localgift_qty", "localgift_price", "localgift_time"):
        return await gift_admin.on_input(message, state, data)

    # ── выпуск лимитированных Telegram-подарков ──
    if act == "gift_stock":
        # Match the native signed-int32 gift stock field; the former million-item
        # limit was artificial and blocked legitimate administrator drops.
        if not text.isdigit() or not 1 <= int(text) <= 2**31 - 1:
            return await again("❌ Тираж должен быть целым числом от 1 до 2 147 483 647:")
        quantity = int(text)
        await state.update_data(act="gift_price", quantity=quantity)
        return await again(
            f"💰 Цена одного экземпляра {esc(data.get('gift_emoji', '🎁'))} "
            f"<b>{esc(data.get('gift_name', 'подарка'))}</b> в RiseStars:"
        )
    if act == "gift_price":
        price = parse_amount(text)
        if price is None or price <= 0:
            return await again("❌ Цена должна быть положительным числом RiseStars:")
        try:
            telegram_cost = (
                int(data.get("telegram_stars") or 0) + int(data.get("upgrade_stars") or 0)
            ) * config.RISESTARS_PER_STAR
            if price < telegram_cost:
                return await again(
                    f"❌ Цена не может быть ниже стоимости NFT-подарка и апгрейда: "
                    f"{fmt_num(telegram_cost)} RiseStars. Введите цену не ниже этого значения:"
                )
            drop_id = await db.create_gift_drop(
                str(data["gift_id"]), str(data["gift_name"]), str(data["gift_emoji"]),
                int(data.get("telegram_stars") or 0), price, int(data["quantity"]),
                upgrade_stars=int(data.get("upgrade_stars") or 0),
                pay_for_upgrade=bool(data.get("pay_for_upgrade")),
            )
        except Exception as exc:  # noqa: BLE001
            log.exception("failed to create gift drop")
            return await done(f"❌ Не удалось создать дроп: {esc(str(exc)[:250])}")
        await audit(
            f"Дроп подарка #{drop_id}: {data['gift_name']} ×{data['quantity']} "
            f"за {price:g} RiseStars"
        )
        return await done(
            f"✅ Дроп #{drop_id} опубликован\n\n"
            f"{esc(data['gift_emoji'])} <b>{esc(data['gift_name'])}</b>\n"
            f"Тираж: <b>{data['quantity']}</b>\n"
            f"Цена: <b>{fmt_num(price)} RiseStars</b> за штуку\n"
            f"Telegram: <b>{int(data.get('telegram_stars') or 0)} ⭐</b> с баланса бота за отправку каждого подарка",
            kb([btn("🎁 К дропам", "adm:gifts")], home_row()),
        )

    # ── задания ──
    if act == "t_title":
        if not 3 <= len(text) <= 60:
            return await again("❌ Название 3–60 символов:")
        await state.update_data(act="t_desc", title=text)
        return await again("Описание задания (до 300 символов):")
    if act == "t_desc":
        if not 3 <= len(text) <= 300:
            return await again("❌ Описание 3–300 символов:")
        await state.update_data(act="t_reward", desc=text)
        return await again(f"Награда в {config.CURRENCY_NAME} (число):")
    if act == "t_reward":
        v = parse_amount(text)
        if v is None or v <= 0:
            return await again("❌ Введите положительное число:")
        await state.update_data(act="t_channel", reward=v)
        return await again("Канал для подписки (@юзернейм, бот должен быть там админом) или «-», если без подписки:")
    if act == "t_channel":
        ch = None if text in ("-", "—", "") else db.norm_username(text)
        if ch and not (4 <= len(ch) <= 32):
            return await again("❌ Некорректный юзернейм канала или «-»:")
        await state.update_data(act="t_max", channel=ch)
        return await again("Лимит выполнений (число) или 0 — без лимита:")
    if act == "t_max":
        if not text.isdigit():
            return await again("❌ Введите целое число:")
        tid = await db.create_task(data["title"], data["desc"], data["reward"], data.get("channel"), int(text))
        await audit(f"Создано задание #{tid} «{data['title']}»")
        return await done(f"✅ Задание #{tid} создано.", kb([btn("🎯 Задания", "adm:tasks")], home_row()))

    # ── SCAM, тестеры, перенос номеров ──
    if act == "scam_revoke":
        return await again("Используй меню «SCAM/FAKE» — снятие теперь синхронизируется с MTProto")
    if act == "risk_label":
        kind = data.get("risk_kind")
        label = data.get("risk_label")
        enabled = bool(data.get("risk_enabled"))
        target, _, reason = text.partition(" ")
        if not target or (enabled and len(reason.strip()) < 8):
            return await again("Формат: @юзернейм причина (минимум 8 символов)\nДля снятия достаточно @юзернейма")
        try:
            await native_shop.change_risk_label(target, kind, label, enabled, reason,
                int(message.from_user.id), f"{message.chat.id}-{message.message_id}")
        except (native_shop.NativePurchaseError, nftnames.NFTUsernameAdminError, psycopg.Error, ClientError, TimeoutError, OSError, ValueError) as exc:
            return await again("❌ " + esc(str(exc)[:180]))
        await audit(f"{'Выдана' if enabled else 'Снята'} метка {label} {kind} {target}: {reason[:150]}")
        return await done(f"✅ MTProto подтвердил действие: {label} {'выдана' if enabled else 'снята'} для {esc(target)}")
    if act == "tester_add":
        t = await parse_target(text)
        if not t:
            return await again("❌ Игрок не найден (он должен запустить бота):")
        await db.add_tester(t["tg_id"], t.get("username"), config.OWNER_ID)
        await audit(f"Тестер добавлен: {t['tg_id']}")
        return await done("✅ Добавлен.", kb([btn("🧪 Тестеры", "adm:testers")], home_row()))
    if act == "legacy_add":
        d = db.digits_of(text)
        if len(d) < 7 or len(d) > 15:
            return await again("❌ Номер 7–15 цифр:")
        if await db.number_taken(d):
            return await again("❌ Этот номер уже занят или добавлен:")
        await db.add_legacy_number(d)
        await audit(f"Номер для переноса: {d}")
        return await done("✅ Добавлено.", kb([btn("📞 Перенос номеров", "adm:legacy")], home_row()))

    # ── рассылка ──
    if act == "cast_text":
        if len(text) > 3500:
            return await again("❌ Слишком длинно (до 3500 символов):")
        total = len(await db.all_user_ids())
        await state.set_state(Adm.cast_confirm)
        await state.set_data({"text": text})
        await message.answer(
            f"📣 <b>Предпросмотр</b>\n\n{esc(text)}\n\nПолучателей: <b>{total}</b>. Отправить?",
            reply_markup=kb([btn("✅ Отправить", "adm:castgo", style="success"), btn("✖️ Отмена", "adm:home", style="danger")]),
        )
        return
    await state.clear()
    await message.answer("Сессия устарела.", reply_markup=kb(home_row()))


async def run_broadcast(bot: Bot, text: str):
    ids = await db.all_user_ids()
    ok = fail = 0
    for uid in ids:
        chat_id = await db.telegram_chat_id(uid)
        if chat_id is None:
            fail += 1
            continue
        try:
            await bot.send_message(chat_id, f"📣 {esc(text)}")
            ok += 1
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after)
            try:
                await bot.send_message(chat_id, f"📣 {esc(text)}")
                ok += 1
            except Exception:  # noqa: BLE001
                fail += 1
        except (TelegramForbiddenError, TelegramBadRequest):
            fail += 1
        except Exception:  # noqa: BLE001
            log.exception("broadcast error uid=%s", uid)
            fail += 1
        await asyncio.sleep(0.05)
    await audit(f"Рассылка: доставлено {ok}, не доставлено {fail}")
    await safe_send(bot, config.OWNER_ID, f"📣 Рассылка завершена: ✅ {ok} · ❌ {fail}")


_bg: set[asyncio.Task] = set()


# ════════════════════════════ роутер кнопок ══════════════════════════════


NAME_RE = re.compile(r"^[a-z][a-z0-9_]{3,31}$")


def parse_usernames(text: str) -> list[str]:
    seen: list[str] = []
    for tok in re.split(r"[\s,;]+", text or ""):
        n = db.norm_username(tok)
        if n and n not in seen:
            seen.append(n)
    return seen[:30]


async def ask_usernames(message, state: FSMContext, t: dict):
    await state.set_state(Adm.input)
    await state.update_data(act="uun", uid=t["tg_id"])
    await message.answer(
        f"🏷 Юзернеймы для {user_line(t)}:\n\nПришлите один или несколько (до 30) через пробел, запятую или с новой строки. "
        "Занятые и «rise…» тоже можно выдать: ограничение действует только на покупку.",
        reply_markup=kb(cancel_row()),
    )


def num(parts: list[str], i: int, default: int = 0) -> int:
    try:
        return int(parts[i])
    except (IndexError, ValueError):
        return default


@admin_router.callback_query(F.data.startswith("adm:"))
async def adm_cb(call: CallbackQuery, state: FSMContext, user: dict):
    parts = (call.data or "").split(":")
    route = parts[1] if len(parts) > 1 else "home"
    bot = call.bot

    if route == "autojoin":
        if int(call.from_user.id) != int(config.OWNER_TELEGRAM_ID):
            return await call.answer("Только владелец", show_alert=True)
        return await screen_autojoin(call)
    if route == "ajtoggle":
        if int(call.from_user.id) != int(config.OWNER_TELEGRAM_ID):
            return await call.answer("Только владелец", show_alert=True)
        space_id = num(parts, 2)
        await db.execute("UPDATE rg_space_autojoin SET enabled=1-enabled WHERE space_id=?", (space_id,))
        await audit(f"Переключено автовступление в пространство {space_id}")
        return await screen_autojoin(call)
    if route == "ajremove":
        if int(call.from_user.id) != int(config.OWNER_TELEGRAM_ID):
            return await call.answer("Только владелец", show_alert=True)
        space_id = num(parts, 2)
        await db.execute("DELETE FROM rg_space_autojoin WHERE space_id=?", (space_id,))
        await audit(f"Удалено пространство {space_id} из автоподписок")
        return await screen_autojoin(call)
    if route == "delphone":
        if call.from_user.id != config.OWNER_TELEGRAM_ID:
            return await call.answer("Только владелец", show_alert=True)
        return await ask(call, state, "delete_phone",
                         "Введите @юзернейм RiseGram или номер. Удаление будет отложено на 30 дней")
    if route == "delconfirm":
        return await confirm_account_deletion(call, state, parts[2] if len(parts) == 3 else "")
    if route == "recoverlist":
        return await screen_recoverable(call)
    if route == "restorephone":
        if call.from_user.id != config.OWNER_TELEGRAM_ID:
            return await call.answer("Только владелец", show_alert=True)
        return await ask(call, state, "restore_phone",
                         "♻️ Введите @юзернейм удаляемого аккаунта RiseGram или номер\n"
                         "Будет восстановлен исходный серверный профиль, если удаление ещё не завершено")
    if route == "recoverpick":
        if call.from_user.id != config.OWNER_TELEGRAM_ID:
            return await call.answer("Только владелец", show_alert=True)
        return await restoration_preview(call, state, parts[2] if len(parts) == 3 else "")
    if route == "restoreconfirm":
        return await confirm_account_restoration(call, state, parts[2] if len(parts) == 3 else "")
    if route == "gifts":
        return await call.answer("Операции с балансом доступны в приложении RiseGram.", show_alert=True)

    if route == "castgo":
        data = await state.get_data()
        await state.clear()
        text = data.get("text")
        if not text:
            await call.answer("Сессия устарела.", show_alert=True)
            return
        task = asyncio.create_task(run_broadcast(bot, text))
        _bg.add(task)
        task.add_done_callback(_bg.discard)
        await show(call, "📣 Рассылка запущена. Отчёт придёт сюда, когда закончится.", kb(home_row()))
        return

    if route in ("localgifts", "localgift"):
        return await gift_admin.on_callback(call, state, parts)

    # NFT confirmation must keep the FSM payload until after the owner approves it.
    # Clearing it here made every valid /deletenft confirmation appear expired.
    if route != "nftconfirm":
        await state.clear()

    if route == "noop":
        return
    if route == "home":
        return await home_screen(call, user, num(parts,2))
    if route == "vermenu":
        return await show(call,"☑️ <b>Верификации</b>\nВыберите действие",kb([btn("Выдать","adm:verpick:grant",style="success"),btn("Снять","adm:verpick:revoke",style="danger")],home_row()))
    if route == "verpick":
        mode=parts[2] if len(parts)==3 else ""
        if mode not in ("grant","revoke"):
            return await call.answer("Неверное действие")
        rows=[[btn("RiseGram",f"adm:verinput:{mode}:risegram"),btn("Major",f"adm:verinput:{mode}:major")],
              [btn("Синяя галочка",f"adm:verinput:{mode}:official")]]
        if call.from_user.id==config.OWNER_TELEGRAM_ID:
            rows += [[btn("Hold",f"adm:verinput:{mode}:hold"),btn("Разработчик",f"adm:verinput:{mode}:developer")],
                     [btn("Тестер",f"adm:verinput:{mode}:tester"),btn("Модератор",f"adm:verinput:{mode}:moderator")],
                     [btn("Админ",f"adm:verinput:{mode}:admin")],
                     [btn("Гл. админ",f"adm:verinput:{mode}:headadmin"),btn("Поддержка",f"adm:verinput:{mode}:support")]]
        return await show(call,"Выберите вид верификации",kb(*rows,back_row("adm:vermenu")))
    if route == "verinput":
        if len(parts)!=4 or parts[2] not in ("grant","revoke"):
            return await call.answer("Неверное действие")
        return await ask(call,state,"menuverify","Введите @юзернейм аккаунта, бота, группы или канала","adm:vermenu",mode=parts[2],kind=parts[3])
    if route == "nftmenu":
        rows=[[btn("Снять NFT в резерв","adm:nftpick:revoke")]]
        if call.from_user.id==config.OWNER_TELEGRAM_ID:
            rows.insert(0,[btn("Выдать NFT","adm:nftpick:grant",style="success")])
            rows.append([btn("Удалить навсегда","adm:nftpick:delete",style="danger")])
        return await show(call,"🏷 <b>NFT-юзернеймы</b>\nАккаунты, боты, группы и каналы",kb(*rows,home_row()))
    if route == "nftpick":
        mode=parts[2] if len(parts)==3 else ""
        if mode not in ("grant","revoke","delete") or (mode!="revoke" and call.from_user.id!=config.OWNER_TELEGRAM_ID):
            return await call.answer("Нет доступа",show_alert=True)
        return await ask(call,state,"menunfttarget","Введите @юзернейм владельца NFT","adm:nftmenu",mode=mode)
    if route == "postmenu":
        if call.from_user.id!=config.OWNER_TELEGRAM_ID:
            return await call.answer("Только владелец",show_alert=True)
        return await ask(call,state,"menupost","Введите ссылку на пост и необязательное количество звёзд для снятия\nНапример: t.me/rise_gram/2 1000","adm:home:0")
    if route == "dbmenu":
        if call.from_user.id!=config.OWNER_TELEGRAM_ID:
            return await call.answer("Только владелец",show_alert=True)
        await call.answer()
        if isinstance(call.message, Message):
            return await owner_database_space(call.message)
    if route == "stats":
        return await screen_stats(call)
    if route == "gifts":
        if len(parts) > 2 and parts[2] == "pick":
            gift_id = parts[3] if len(parts) > 3 else ""
            try:
                catalog = await available_telegram_gifts(bot)
            except Exception:
                log.exception("Telegram getAvailableGifts failed while selecting a gift")
                return await call.answer("Не удалось получить каталог Telegram.", show_alert=True)
            gift = next((item for item in catalog if item["id"] == gift_id and item["can_upgrade"]), None)
            if not gift:
                return await call.answer("NFT-подарок больше недоступен для отправки ботом.", show_alert=True)
            return await ask(
                call, state, "gift_stock",
                f"💎 <b>{esc(gift['emoji'])} {esc(gift['name'])}</b>\n"
                f"Подарок: {gift['stars']} ⭐ + апгрейд NFT: {gift['upgrade_stars']} ⭐ "
                "с баланса бота\n\n"
                "Введите количество экземпляров в тираже (1–1 000 000):",
                "adm:gifts",
                gift_id=gift["id"], gift_name=gift["name"], gift_emoji=gift["emoji"],
                telegram_stars=gift["stars"], upgrade_stars=gift["upgrade_stars"],
                pay_for_upgrade=True,
            )
        if len(parts) > 2 and parts[2] == "page":
            return await screen_gifts(call, num(parts, 3))
        return await screen_gifts(call)
    if route == "maint":
        new = "0" if await db.get_meta("maintenance") == "1" else "1"
        await db.set_meta("maintenance", new)
        await audit(f"Техработы {'включены' if new == '1' else 'выключены'}")
        return await home_screen(call, user)

    # ── игроки ──
    if route == "risks":
        return await show(call,
            "⚠️ <b>Модерация SCAM / FAKE</b>\n\n"
            "Только модераторы — метки синхронизируются с клиентом RiseGram\n"
            "Выберите тип действия, затем укажите @объект и причину\n"
            "SCAM и FAKE не продаются",
            kb(
                [btn("⚠️ SCAM аккаунт", "adm:risk:user:SCAM:on"),
                 btn("🎭 FAKE аккаунт", "adm:risk:user:FAKE:on")],
                [btn("⚠️ SCAM группа", "adm:risk:group:SCAM:on"),
                 btn("🎭 FAKE группа", "adm:risk:group:FAKE:on")],
                [btn("⚠️ SCAM канал", "adm:risk:channel:SCAM:on"),
                 btn("🎭 FAKE канал", "adm:risk:channel:FAKE:on")],
                [btn("🧹 Снять метку", "adm:riskclear")], home_row()))
    if route == "riskclear":
        return await show(call,
            "🧹 Снятие меток (каждая операция логируется)",
            kb(
                [btn("SCAM аккаунт", "adm:risk:user:SCAM:off"),
                 btn("FAKE аккаунт", "adm:risk:user:FAKE:off")],
                [btn("SCAM группа", "adm:risk:group:SCAM:off"),
                 btn("FAKE группа", "adm:risk:group:FAKE:off")],
                [btn("SCAM канал", "adm:risk:channel:SCAM:off"),
                 btn("FAKE канал", "adm:risk:channel:FAKE:off")],
                back_row("adm:risks")))
    if route == "risk":
        if len(parts) != 5 or parts[2] not in ("user", "group", "channel") or parts[3] not in ("SCAM", "FAKE") or parts[4] not in ("on", "off"):
            return await call.answer("Некорректное действие", show_alert=True)
        await state.set_state(Adm.input)
        await state.set_data({"act": "risk_label", "risk_kind": parts[2],
                              "risk_label": parts[3], "risk_enabled": parts[4] == "on"})
        question = ("Пришли @юзернейм и причину от 8 символов\nПример: @example выдаёт себя за администрацию" if parts[4]=="on"
                    else "Пришли @юзернейм для снятия выбранной метки")
        return await show(call, question, kb(cancel_row("adm:risks")))

    if route == "verifychats":
        return await show(call,
            "📣 <b>Верификация каналов и групп RiseGram</b>\n\n"
            "<b>Выдать:</b>\n"
            "<code>/grantverify @чат official</code>\n"
            "<code>/grantverify @чат risegram</code>\n"
            "<code>/grantverify @чат major</code>\n\n"
            "<b>Снять:</b>\n"
            "<code>/revokeverify @чат official</code>\n"
            "<code>/revokeverify @чат risegram</code>\n"
            "<code>/revokeverify @чат major</code>\n\n"
            "У публичных чатов тип группы или канала определяется автоматически. "
            "Для числового ID при выдаче добавь <code>group</code> или <code>channel</code> "
            "четвёртым аргументом. Базовую группу надо сначала превратить в супергруппу.\n\n"
            "Снимается только выбранная галочка, сам чат не удаляется.",
            kb([btn("👥 Верификация аккаунтов", "adm:users:0")],home_row()))
    if route == "verifyrevokeuser":
        return await show(call,
            "❎ <b>Снятие верификации аккаунта</b>\\n\\n"
            "Открой карточку пользователя и нажми «Снять RiseGram» или «Снять Major».\\n"
            "Можно снять оба статуса одной командой:\\n"
            "<code>/revokeuserverify @пользователь both</code>\\n\\n"
            "Аккаунт, чаты и сессия не удаляются.",
            kb([btn("🔎 Найти аккаунт", "adm:usearch", style="primary")],home_row()))
    if route in ("verifyrise", "verifymajor"):
        kind = "RiseGram" if route == "verifyrise" else "Major"
        return await show(call,
            f"☑️ <b>Верификация {kind}</b>\n\nВыбери пользователя или найди его по @username, затем нажми «Выдать {kind}» или «Снять {kind}» в карточке.",
            kb([btn("👥 Выбрать пользователя", "adm:users:0", style="primary")],
               [btn("🔎 Найти пользователя", "adm:usearch")], home_row()))
    if route == "users":
        return await screen_users(call, num(parts, 2))
    if route == "nativeprofile":
        return await native_admin_profile(call,num(parts,2))
    if route == "nativemint":
        if call.from_user.id != config.OWNER_TELEGRAM_ID:
            return await call.answer("Только владелец",show_alert=True)
        from support_console import native_label
        nid=num(parts,2)
        return await ask(call,state,"native_mint","🏷 "+esc(await native_label(nid))+"\nВведите новый NFT-юзернейм",uid=nid)
    if route == "usearch":
        return await ask(call, state, "usearch", "🔎 Введите @юзернейм или часть имени:", "adm:users:0")
    if route == "u":
        return await screen_user(call, num(parts, 2))
    if route in ("uban", "uver", "uverrise", "uvermajor", "udel", "udelok", "ucode", "unum", "uun", "uph", "umsg"):
        uid = num(parts, 2)
        u = await db.get_user(uid)
        if not u:
            return await call.answer("Игрок не найден.", show_alert=True)
        back = f"adm:u:{uid}"
        if route == "uban":
            if uid == config.OWNER_ID:
                return await call.answer("Владельца банить нельзя.", show_alert=True)
            await db.set_banned(uid, not u["banned"])
            await audit(f"{'Разбан' if u['banned'] else 'Бан'}: {db.shown_username(u)} ({uid})")
            return await screen_user(call, uid, "✅ Готово.")
        if route in ("uverrise", "uvermajor"):
            kind = "risegram" if route == "uverrise" else "major"
            if len(parts) != 4 or parts[3] not in ("0", "1"):
                return await screen_user(call, uid, "Карточка обновлена. Выберите действие.")
            enabled = parts[3] == "1"
            await db.set_verification(uid, kind, enabled)
            sync = await sync_mtproto_verification(uid, force=True)
            label = "RiseGram" if kind == "risegram" else "Major"
            await audit(f"Верификация {label} {'выдана' if enabled else 'снята'}: {uid}")
            note = f"{label}: {'выдана' if enabled else 'снята'}."
            if not sync.get("synced"):
                note += " Сервер клиента пока не подтвердил синхронизацию."
            return await screen_user(call, uid, note)
        if route == "ucode":
            code = await db.issue_auth_code(uid)
            await audit(f"Выдан код входа игроку {uid}")
            return await screen_user(call, uid, f"🔑 Код для {esc(u['phone'])}: <code>{code}</code> (действует {config.AUTH_CODE_TTL_SECONDS // 60} мин)")
        if route in ("udel", "udelok"):
            return await deletion_preview(call, state, u["phone"])
        if route == "uph":
            return await ask(
                call, state, "uph",
                f"📞 Номера +888 для {user_line(u)}:\n\nПришлите один или несколько (до 30) через пробел, запятую или с новой строки, "
                "например <code>8881234 8880001</code> или <code>+888 0123 4567</code> (один номер в строке, если с пробелами). "
                "Номера попадут в «Мои NFT и номера»; сделать текущим игрок может сам.",
                back, uid=uid,
            )
        if route == "unum":
            return await ask(call, state, "unum", f"📞 Новый номер для {user_line(u)} (цифры, например <code>8887777</code>):", back, uid=uid)
        if route == "uun":
            return await ask(call, state, "uun", f"🏷 Юзернейм для {user_line(u)}:", back, uid=uid)
        return await ask(call, state, "umsg", f"✉️ Текст сообщения для {user_line(u)}:", back, uid=uid)

    # ── валюта ──
    if route == "grant":
        return await ask(call, state, "grant_target", "💰 <b>Выдача и списание</b>\n\nВведите @юзернейм игрока:")
    if route == "g":
        uid, cur = num(parts, 2), parts[3] if len(parts) > 3 else "stars"
        u = await db.get_user(uid)
        if not u or cur not in ("stars", "gram"):
            return await call.answer("Игрок не найден.", show_alert=True)
        name = "⭐ RiseStars" if cur == "stars" else "💎 Gram"
        return await ask(
            call, state, "g_amount",
            f"✍️ {user_line(u)}\nВалюта: <b>{name}</b>\n\nВведите число (для списания с минусом, например <code>-1000</code>):",
            f"adm:u:{uid}", uid=uid, cur=cur,
        )

    # ── выдача юзернеймов, маркет и NFT ──
    if route == "inv":
        return await ask(call, state, "inv_target", "🏷 <b>Выдача NFT-юзернеймов</b>\n\nВведите @юзернейм игрока:")
    if route == "mkt":
        return await screen_market(call, num(parts, 2))
    if route == "mktdel":
        L = await db.cancel_listing(0, num(parts, 2), admin=True)
        if L:
            await audit(f"Лот #{L['id']} снят админом")
            await safe_send(bot, L["seller_id"], "🛠 Администрация сняла ваш лот с маркета.")
        return await screen_market(call, num(parts, 3), "🗑 Лот снят." if L else "Лот уже закрыт.")
    if route == "nftcontrol":
        return await show(call,
            "🗑 <b>NFT-юзернеймы RiseGram — управление</b>\n\n"
            "Для аккаунта: <code>/removenft user @пользователь @NFT</code>\n"
            "Для супергруппы: <code>/removenft group @группа @NFT</code>\n"
            "Для канала: <code>/removenft channel @канал @NFT</code>\n\n"
            "Команда <code>/removenft</code> снимает имя у владельца и "
            "возвращает NFT в резерв с сохранением истории\n\n"
            "Чтобы полностью удалить запись NFT и историю (только владелец):\n"
            "<code>/deletenft user|group|channel @владелец @NFT</code>\n\n"
            "Каждое удаление требует подтверждения и проверки владельца "
            "в MTProto — аккаунты, группы, каналы и номера не удаляются",
            kb([btn("👥 Пользователи", "adm:users:0")],home_row()))
    if route == "nftcancel":
        await state.clear()
        return await show(call, "Удаление NFT отменено, изменений нет", kb(home_row()))
    if route == "nftconfirm":
        data = await state.get_data()
        pending = data.get("nft_pending")
        if (await state.get_state() != Adm.nft_confirm.state
                or not pending or int(data.get("nft_actor") or 0) != call.from_user.id):
            return await call.answer("Подтверждение устарело. Запусти команду ещё раз.",show_alert=True)
        if (pending.get("mode") == "delete"
                and call.from_user.id != config.OWNER_TELEGRAM_ID):
            return await call.answer("Удаление навсегда доступно только владельцу", show_alert=True)
        try:
            result = await nftnames.execute(
                pending,
                admin_id=int(call.from_user.id),
                command_id=str(data["nft_command_id"]),
            )
        except nftnames.NFTUsernameAdminError as exc:
            log.warning("NFT username removal rejected: %s", str(exc)[:160])
            return await show(call, "❌ " + esc(str(exc)), kb(home_row()))
        except Exception:
            log.exception("Native NFT username removal failed")
            return await show(call, "❌ Сервер не подтвердил удаление NFT. Проверь состояние перед повтором.", kb(home_row()))
        await audit(f"NFT юзернейм @{pending['name']} {pending['mode']} от "
                    f"{pending['kind']}:{pending['peer_id']} админом {call.from_user.id}")
        await state.clear()
        return await show(call, "✅ " + esc(result), kb(home_row()))
    if route == "nfts":
        return await screen_nfts(call, num(parts, 2))
    if route == "nftdel":
        # Old buttons could delete only local SQLite, leaving the real MTProto
        # NFT in the client's profile and letting bot sync restore it again.
        return await show(call,
            "Старое удаление отключено. Используй меню NFT-юзернеймов, "
            "чтобы одновременно обновить MTProto и инвентарь бота.",
            kb([btn("🗑 Управление NFT", "adm:nftcontrol", style="primary")],home_row()))

    # ── тумблеры ──
    if route == "feat":
        return await screen_features(call)
    if route == "ft":
        name = parts[2] if len(parts) > 2 else ""
        if name not in config.FEATURES:
            return await call.answer("Неизвестный модуль.", show_alert=True)
        on = await db.toggle_flag(name)
        await audit(f"Тумблер {name}: {'ВКЛ' if on else 'ВЫКЛ'}")
        return await screen_features(call)

    # ── бан ──
    if route == "ban":
        return await ask(call, state, "ban_target", "🔨 <b>Банхаммер</b>\n\nВведите @юзернейм. Статус переключится (бан ↔ разбан):")

    # ── задания ──
    if route == "tasks":
        return await screen_tasks(call)
    if route == "tnew":
        return await ask(call, state, "t_title", "🎯 <b>Новое задание</b>\n\nНазвание (3–60 символов):", "adm:tasks")
    if route == "ttog":
        await db.toggle_task(num(parts, 2))
        return await screen_tasks(call)
    if route == "tdel":
        await db.delete_task(num(parts, 2))
        await audit(f"Удалено задание #{num(parts, 2)}")
        return await screen_tasks(call)

    # ── SCAM ──
    if route == "scam":
        sub = parts[2] if len(parts) > 2 else "0"
        if sub in ("ok", "no"):
            return await scam_decide(call, num(parts, 3), sub == "ok")
        return await screen_scam(call, num(parts, 2))
    if route == "scamrevoke":
        return await ask(call, state, "scam_revoke", "🧹 Введите @юзернейм, с которого нужно снять метку SCAM:", "adm:scam:0")

    # ── покупки ──
    if route == "buys":
        return await screen_buys(call, num(parts, 2))
    if route == "buy":
        return await screen_buy(call, num(parts, 2))
    if route == "refund":
        pid = num(parts, 2)
        return await show(
            call,
            f"↩️ Вернуть звёзды за покупку #{pid} и откатить товар? Деньги вернутся игроку в Telegram.",
            kb([btn("↩️ Да, вернуть", f"adm:refok:{pid}", style="danger")], [btn("✖️ Нет", f"adm:buy:{pid}")]),
        )
    if route == "refok":
        pid = num(parts, 2)
        p = await db.get_purchase(pid)
        if not p or p["status"] != "done" or not p["charge_id"]:
            return await call.answer("Возврат недоступен.", show_alert=True)
        ok, err = await refund_stars(bot, p["tg_id"], p["charge_id"])
        if not ok:
            return await show(call, f"❌ Telegram не вернул звёзды: {esc(err)}", kb([btn("↩️ Назад", f"adm:buy:{pid}")], home_row()))
        await db.set_purchase_status(pid, "refunded")
        res = await revoke_goods(p)
        await audit(f"Возврат покупки #{pid} ({p['price_xtr']} ⭐): {res}")
        await safe_send(bot, p["tg_id"], f"↩️ Покупка «{esc(p['item_desc'])}» отменена, {p['price_xtr']} ⭐ возвращены на ваш счёт.")
        return await show(call, f"✅ Возвращено {p['price_xtr']} ⭐. Откат: {esc(res)}.", kb([btn("🧾 Покупки", "adm:buys:0")], home_row()))

    # ── рассылка, тестеры, перенос, логи ──
    if route == "cast":
        return await ask(call, state, "cast_text", "📣 <b>Рассылка</b>\n\nВведите текст сообщения для всех игроков:")
    if route == "testers":
        return await screen_testers(call)
    if route in ("admins", "aadd", "arm"):
        if int(user["tg_id"]) != int(config.OWNER_ID):
            return await call.answer("Управление администраторами доступно только владельцу.", show_alert=True)
        if route == "admins":
            return await screen_admins(call)
        if route == "aadd":
            return await ask(call, state, "admin_add", "🛡 Введите @юзернейм пользователя, которого нужно сделать администратором:", "adm:admins")
        target_id = num(parts, 2)
        if target_id == int(config.OWNER_ID):
            return await call.answer("Владельца нельзя удалить из администраторов.", show_alert=True)
        removed = await db.remove_admin(target_id)
        if removed:
            await audit(f"Администратор удалён: {target_id}")
        return await screen_admins(call)
    if route == "tadd":
        return await ask(call, state, "tester_add", "🧪 Введите @юзернейм тестера:", "adm:testers")
    if route == "trm":
        await db.remove_tester(num(parts, 2))
        await audit(f"Тестер удалён: {num(parts, 2)}")
        return await screen_testers(call)
    if route == "legacy":
        return await screen_legacy(call)
    if route == "lgadd":
        return await ask(call, state, "legacy_add", "📞 Введите старый номер игрока (цифры):", "adm:legacy")
    if route == "logs":
        return await screen_logs(call, num(parts, 2))

    await call.answer("Неизвестное действие.")


@admin_router.message(Command("reply"))
async def cmd_support_reply(message: Message):
    if int(message.from_user.id) != int(config.OWNER_TELEGRAM_ID):
        return await message.answer("❌ Только владелец может отвечать на обращения")
    parts = (message.text or "").split(maxsplit=2)
    if len(parts) != 3 or not parts[2].strip():
        return await message.answer("Формат: /reply @username текст ответа (или номер тикета)")
    if parts[1].isdigit():
        ticket = await db.get_support_ticket(int(parts[1]))
    else:
        target = await parse_target(parts[1])
        if not target:
            return await message.answer("Пользователь не найден однозначно")
        rows = await db.fetchall("SELECT * FROM support_tickets WHERE tg_id=? AND status='open' ORDER BY id DESC LIMIT 2",(int(target["tg_id"]),))
        if len(rows)>1:
            return await message.answer("У пользователя несколько обращений. Выберите нужное в панели /supportpanel → Найти тикет")
        ticket = rows[0] if rows else None
    if not ticket:
        return await message.answer("Обращение не найдено")
    if ticket["status"] != "open":
        return await message.answer("На это обращение уже ответили")
    delivered = await safe_send(message.bot, int(ticket["tg_id"]),
        f"🆘 <b>Ответ поддержки RiseGram · #{ticket['id']}</b>\n\n{esc(parts[2])}")
    if not delivered:
        return await message.answer("Не удалось доставить ответ — тикет остаётся открытым")
    await db.close_support_ticket(ticket["id"])
    await audit(f"Ответ на тикет #{ticket['id']}")
    await message.answer(f"✅ Ответ на обращение #{ticket['id']} доставлен")


@admin_router.message(Command("markfake", "markscam", "unmarkfake", "unmarkscam"))
async def cmd_risk_mark(message: Message):
    if int(message.from_user.id) != int(config.OWNER_TELEGRAM_ID):
        return await message.answer("❌ Метки может менять только владелец")
    parts = (message.text or "").split(maxsplit=2)
    cmd = parts[0].split("@")[0].lstrip("/").lower()
    label = "FAKE" if cmd.endswith("fake") else "SCAM"
    enabled = not cmd.startswith("unmark")
    if len(parts) < 2:
        return await message.answer("Формат: /markfake @username причина или /unmarkfake @username (аналогично для scam)")
    reason = parts[2].strip() if len(parts) == 3 else ""
    if enabled and len(reason) < 8:
        return await message.answer("Укажите конкретную причину длиной от 8 символов")
    try:
        await native_shop.change_risk_label(parts[1], "user", label, enabled, reason,
            int(message.from_user.id), f"cmd-{message.chat.id}-{message.message_id}")
    except (native_shop.NativePurchaseError, nftnames.NFTUsernameAdminError, psycopg.Error, ClientError, TimeoutError, OSError, ValueError) as exc:
        return await message.answer("❌ " + esc(str(exc)[:180]))
    await audit(f"Метка {label} {'установлена' if enabled else 'снята'} для {parts[1]}")
    await message.answer(f"✅ MTProto подтвердил: {label} {'выдана' if enabled else 'снята'} для {esc(parts[1])}")

@admin_router.message(Command("tickets"))
async def cmd_support_tickets(message: Message):
    if int(message.from_user.id) != int(config.OWNER_TELEGRAM_ID):
        return await message.answer("❌ Только владелец может просматривать обращения")
    rows = await db.fetchall("SELECT id,tg_id,message,created_at FROM support_tickets WHERE status='open' ORDER BY id DESC LIMIT 15")
    if not rows:
        return await message.answer("🆘 Открытых обращений нет")
    chunks = ["🆘 <b>Открытые обращения RiseGram</b>"]
    for ticket in rows:
        preview = ticket["message"][:180]
        chunks.append(f"\n<b>#{ticket['id']}</b> · ID <code>{ticket['tg_id']}</code>\n{esc(preview)}\n<code>/reply {ticket['id']} ответ</code>")
    await message.answer("\n".join(chunks))


async def screen_autojoin(event):
    if int(event.from_user.id) != int(config.OWNER_TELEGRAM_ID):
        return await event.answer("Только владелец", show_alert=True)
    await db.ensure_default_risegram_spaces()
    rows = await db.fetchall(
        """SELECT a.space_id,a.alias,a.enabled,s.title,s.kind,
                  (SELECT COUNT(*) FROM rg_space_members m WHERE m.space_id=a.space_id) AS members
           FROM rg_space_autojoin a JOIN rg_spaces s ON s.id=a.space_id ORDER BY a.alias""")
    text = ("👥 <b>Автовступление RiseGram</b>\\n\\n"
            "Новые аккаунты добавляются в включённые внутренние группы и каналы при первом входе. "
            "Существующих участников отключение не удаляет.\\n\\n"
            "Добавить существующую группу: <code>/autojoin add ID @alias</code>\\n"
            "Удалить из списка: <code>/autojoin remove @alias</code>\\n"
            "Посмотреть ID пространств: <code>/autojoin spaces</code>\\n")
    buttons = []
    for row in rows:
        text += (f"\\n{'🟢' if row['enabled'] else '⚪'} <b>@{esc(row['alias'])}</b> "
                 f"({esc(row['kind'])}, ID {row['space_id']}, {row['members']} участников)")
        buttons.append([
            btn(("⏸ Выкл " if row["enabled"] else "▶️ Вкл ") + "@" + row["alias"][:18],
                f"adm:ajtoggle:{row['space_id']}"),
            btn("🗑", f"adm:ajremove:{row['space_id']}", style="danger"),
        ])
    buttons.append(home_row())
    await show(event, text.replace("\\\\n", "\\n"), kb(*buttons))


@admin_router.message(Command("autojoin"), OwnerOnly())
async def cmd_autojoin(message: Message):
    args = (message.text or "").split()
    if len(args) == 1 or args[1].lower() == "list":
        return await screen_autojoin(message)
    op = args[1].lower()
    await db.ensure_default_risegram_spaces()
    if op == "spaces":
        spaces = await db.fetchall(
            "SELECT id,title,kind FROM rg_spaces ORDER BY id DESC LIMIT 35")
        return await message.answer(
            "Внутренние группы и каналы RiseGram:\\n" +
            "\\n".join(f"{x['id']} · {esc(x['title'])} ({x['kind']})" for x in spaces)
        )
    if op == "add" and len(args) == 4:
        try:
            space_id = int(args[2])
        except ValueError:
            return await message.answer("Нужен числовой ID группы")
        alias = db.norm_username(args[3])
        if not re.fullmatch(r"[a-z][a-z0-9_]{2,31}", alias):
            return await message.answer("Некорректный @alias")
        space = await db.fetchone("SELECT id FROM rg_spaces WHERE id=?", (space_id,))
        if not space:
            return await message.answer("Внутренняя группа или канал с таким ID не найдены")
        try:
            await db.execute(
                "INSERT INTO rg_space_autojoin(space_id,alias,enabled,created_at) VALUES(?,?,1,?) "
                "ON CONFLICT(space_id) DO UPDATE SET alias=excluded.alias,enabled=1",
                (space_id, alias, db.now()),
            )
        except Exception:
            return await message.answer("Этот @alias уже используется в списке")
        await audit(f"Автовступление: добавлено @{alias}, пространство {space_id}")
        return await screen_autojoin(message)
    if op == "remove" and len(args) == 3:
        alias = db.norm_username(args[2])
        await db.execute("DELETE FROM rg_space_autojoin WHERE alias=?", (alias,))
        await audit(f"Автовступление: удалено @{alias}")
        return await screen_autojoin(message)
    return await message.answer(
        "Использование: /autojoin list | /autojoin spaces | /autojoin add ID @alias | /autojoin remove @alias"
    )
