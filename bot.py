"""bot.py — основной бот RiseGram: авторизация, магазин (Telegram Stars), номера, NFT, игры.

Запуск:  python bot.py
Админка: файл admin.py, вход только у владельца по команде /admin7206852053.
"""
from __future__ import annotations

import asyncio
import logging
import math
import random
import re
import secrets
import sqlite3
import time

from aiogram import BaseMiddleware, Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery, LabeledPrice, Message, PreCheckoutQuery

import config
import database as db
from admin import admin_router
from api import start_api
from ui import back_row, btn, cancel_kb, esc, fmt_num, kb, safe_send, show

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
log = logging.getLogger("risegram")

router = Router(name="main")
router.message.filter(F.chat.type == "private")
router.callback_query.filter(F.message.chat.type == "private")

NAME_RE = re.compile(r"^[a-z][a-z0-9_]{3,31}$")
CHANNEL_RE = re.compile(r"^[a-z][a-z0-9_]{3,31}$")


class Flow(StatesGroup):
    custom_stars = State()
    gift_target = State()
    verify_channel = State()
    scam_target = State()
    scam_reason = State()
    number_custom = State()
    nft_name = State()
    transfer_target = State()
    transfer_amount = State()
    crash_bet = State()
    migrate_number = State()


# ═══════════════════════════ защита, бан, техработы ═════════════════════════

_last_click: dict[int, float] = {}


async def _reply_block(event, text: str) -> None:
    if isinstance(event, CallbackQuery):
        await event.answer(text, show_alert=True)
    else:
        await event.answer(text)


class GuardMiddleware(BaseMiddleware):
    """Регистрирует пользователя, режет спам, проверяет бан и техработы, сбрасывает FSM при нажатии кнопок."""

    async def __call__(self, handler, event, data):
        tg_user = getattr(event, "from_user", None)
        if tg_user is None or tg_user.is_bot:
            return await handler(event, data)
        if isinstance(event, Message) and event.chat.type != "private":
            return await handler(event, data)
        is_cb = isinstance(event, CallbackQuery)
        if is_cb:
            t = time.monotonic()
            if t - _last_click.get(tg_user.id, 0.0) < 0.3:
                await event.answer("Не так быстро 🙂")
                return
            _last_click[tg_user.id] = t
            if len(_last_click) > 20000:
                _last_click.clear()
        user = await db.get_or_create_user(tg_user.id, tg_user.full_name or "User", tg_user.username)
        data["user"] = user
        is_owner = tg_user.id == config.OWNER_ID
        if user.get("banned") and not is_owner:
            await _reply_block(event, "❌ Ваш аккаунт заблокирован.")
            return
        if not is_owner and await db.get_meta("maintenance") == "1":
            await _reply_block(event, "🛠 Технические работы. Загляните позже.")
            return
        if is_cb and not (event.data or "").startswith("adm:"):
            state = data.get("state")
            if state:
                await state.clear()
        try:
            return await handler(event, data)
        finally:
            if is_cb:
                try:
                    await event.answer()
                except Exception:  # уже отвечали
                    pass


async def need_feature(event, name: str) -> bool:
    if await db.is_feature_on(name):
        return True
    text = "🔒 Раздел временно отключён."
    if isinstance(event, CallbackQuery):
        await event.answer(text, show_alert=True)
    else:
        await event.answer(text)
    return False


# ═══════════════════════════ главное меню ═══════════════════════════════════


async def badges(user: dict) -> str:
    parts = []
    if user.get("verified"):
        parts.append("✅ Мажор")
    if await db.is_scam_target(user.get("username")) or await db.is_scam_target(user.get("active_username")):
        parts.append("⚠️ SCAM")
    return (" · " + " · ".join(parts)) if parts else ""


async def main_text(user: dict) -> str:
    return (
        f"💼 <b>{config.BRAND}</b>\n\n"
        f"📞 <b>Номер:</b> <code>{esc(user['phone'])}</code>\n"
        f"👤 <b>Аккаунт:</b> {esc(user.get('name') or 'User')} · ID <code>{user['tg_id']}</code>{await badges(user)}\n"
        f"⭐ {config.CURRENCY_NAME}: <b>{fmt_num(user['risestars'])}</b> · 💎 Gram: <b>{fmt_num(user['gram_balance'], 2)}</b>\n\n"
        f"Выберите действие ниже или откройте приложение."
    )


def webapp_button(style: str | None = "primary"):
    if config.WEBAPP_URL:
        return btn("📱 Открыть приложение", web_app=config.WEBAPP_URL, style=style)
    return btn("📱 Открыть приложение", "menu:webapp", style=style)


def main_kb():
    return kb(
        [btn("🔒 Код входа", "menu:code", style="primary"), btn("📞 Мой номер", "menu:number")],
        [btn("💎 Донат", "donat:hub", style="success")],
        [webapp_button(None)],
        [btn("🕒 Мои покупки", "menu:purchases"), btn("ℹ️ Как это работает", "menu:help")],
        [btn("⚙️ Настройки", "menu:settings"), btn("✨ Ещё", "menu:more")],
    )


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext, user: dict):
    await state.clear()
    await message.answer(await main_text(user), reply_markup=main_kb())


@router.callback_query(F.data == "menu:main")
async def cb_main(call: CallbackQuery, user: dict):
    await show(call, await main_text(user), main_kb())


@router.callback_query(F.data == "menu:webapp")
async def cb_webapp(call: CallbackQuery):
    await call.answer("Приложение пока не подключено. Владелец ещё не указал ссылку.", show_alert=True)


# ─── код входа и номер ──────────────────────────────────────────────────────


async def code_screen(event, user: dict, *, refresh_local: bool = False):
    if not await need_feature(event, "auth_numbers"):
        return
    row = await db.get_active_code(user["tg_id"])
    if row is None and refresh_local and not config.API_SECRET:
        # API не подключено — выдаём код прямо из бота (как в старой версии)
        await db.issue_auth_code(user["tg_id"])
        row = await db.get_active_code(user["tg_id"])
    text = f"🔒 <b>Код входа</b>\n\n📞 Номер: <code>{esc(user['phone'])}</code>\n"
    if row:
        left = max(1, int((_parse_ts(row["expires_at"]) - time.time()) // 60) + 1)
        text += f"Ваш код: <code>{esc(row['code'])}</code>\nДействует ещё около {left} мин. Никому его не сообщайте."
    else:
        text += "Кода пока нет. Запросите вход по этому номеру в приложении — код придёт сюда."
    markup = kb(
        [btn("🔄 Код входа", "menu:code:refresh", style="primary"), btn("📞 Мой номер", "menu:number")],
        [btn("🏠 Меню", "menu:main")],
    )
    await show(event, text, markup)


def _parse_ts(s: str) -> float:
    import calendar
    import time as _t

    return calendar.timegm(_t.strptime(s, "%Y-%m-%d %H:%M:%S"))


@router.message(Command("code"))
async def cmd_code(message: Message, user: dict):
    await code_screen(message, user, refresh_local=True)


@router.callback_query(F.data == "menu:code")
async def cb_code(call: CallbackQuery, user: dict):
    await code_screen(call, user, refresh_local=True)


@router.callback_query(F.data == "menu:code:refresh")
async def cb_code_refresh(call: CallbackQuery, user: dict):
    await code_screen(call, user, refresh_local=True)


@router.callback_query(F.data == "menu:number")
async def cb_number(call: CallbackQuery, user: dict):
    if not await need_feature(call, "auth_numbers"):
        return
    text = f"📞 <b>Ваш номер</b>\n\n<b>Номер:</b> <code>{esc(user['phone'])}</code>"
    await show(call, text, kb([btn("🔒 Код входа", "menu:code", style="primary")], [btn("🏠 Меню", "menu:main")]))


# ─── помощь и настройки ─────────────────────────────────────────────────────

HELP_TEXT = (
    "ℹ️ <b>Как это работает</b>\n\n"
    "1. Откройте «Мой номер» — бот покажет ваш номер.\n"
    f"2. В приложении {config.BRAND} запросите вход по этому номеру.\n"
    "3. Код придёт сюда.\n\n"
    "💎 В разделе «Донат» — RiseStars, верификация, метки SCAM, номера +888 и NFT-юзернеймы. "
    "Оплата звёздами Telegram.\n"
    "Всё купленное действует внутри экосистемы RiseGram, а не в официальном Telegram.\n\n"
    "/start — меню\n/shop — донат\n/code — код входа\n"
    f"/doxnumber — номер +1 с бонусом {config.CURRENCY_NAME}\n/migrate — привязать старый номер\n"
    "/top — топ игроков\n/bonus — ежедневный бонус\n/games — Crash Rocket\n/send — перевод"
)


@router.callback_query(F.data == "menu:help")
async def cb_help(call: CallbackQuery):
    await show(call, HELP_TEXT, kb(back_row()))


async def settings_screen(event, user: dict):
    mode = user.get("gift_mode") or "real"
    cur = "Настоящие рубли" if mode == "real" else "Как в Telegram"
    text = (
        "⚙️ <b>Настройки</b>\n\n"
        f"<b>Ценность подарков</b>\nСейчас: <b>{cur}</b>\n\n"
        f"Как в Telegram: 100 ⭐ ≈ {fmt_num(config.TG_RUB_PER_100_STARS)} ₽\n"
        f"Настоящие рубли: 100 ⭐ = {fmt_num(config.REAL_RUB_PER_100_STARS, 2)} ₽\n\n"
        "Выбор действует на всех ваших аккаунтах. Новая цена появится в подарке примерно через 15 секунд, "
        "откройте его заново."
    )
    mark = lambda m, t: btn(("✓ " if mode == m else "") + t, f"set:gift:{m}", style="primary" if mode == m else None)
    await show(event, text, kb([mark("telegram", "Как в Telegram"), mark("real", "Настоящие рубли")], back_row()))


@router.callback_query(F.data == "menu:settings")
async def cb_settings(call: CallbackQuery, user: dict):
    await settings_screen(call, user)


@router.callback_query(F.data.startswith("set:gift:"))
async def cb_set_gift(call: CallbackQuery, user: dict):
    mode = call.data.split(":")[2]
    if mode not in ("telegram", "real"):
        return
    await db.set_gift_mode(user["tg_id"], mode)
    user["gift_mode"] = mode
    await settings_screen(call, user)


# ─── «Ещё» и профиль ────────────────────────────────────────────────────────


@router.callback_query(F.data == "menu:more")
async def cb_more(call: CallbackQuery):
    await show(
        call,
        "✨ <b>Ещё</b>\n\nПрофиль, игры, задания и другие разделы:",
        kb(
            [btn("👤 Профиль", "more:profile"), btn("🗂 Мои NFT и номера", "my:items")],
            [btn("🎮 Игры", "games:hub", style="success"), btn("🏆 Топ", "more:top")],
            [btn("🎯 Задания", "more:tasks"), btn("🎒 Инвентарь", "more:inv")],
            [btn("💸 Перевод", "more:send"), btn("🎁 Бонус", "more:bonus")],
            back_row(),
        ),
    )


@router.callback_query(F.data == "more:profile")
async def cb_profile(call: CallbackQuery, user: dict):
    nft_u = len(await db.list_nft(user["tg_id"], "username"))
    nft_n = len(await db.list_nft(user["tg_id"], "number"))
    skins = len(await db.list_skins(user["tg_id"]))
    text = (
        "👤 <b>Профиль</b>\n\n"
        f"Имя: {esc(user.get('name'))}\n"
        f"Юзернейм: {esc(db.shown_username(user))}{await badges(user)}\n"
        f"Telegram ID: <code>{user['tg_id']}</code>\n"
        f"{config.BRAND} ID: <code>{user['rise_id']}</code>\n"
        f"Номер: <code>{esc(user['phone'])}</code>\n\n"
        f"⭐ {config.CURRENCY_NAME}: <b>{fmt_num(user['risestars'])}</b>\n"
        f"💎 Gram: <b>{fmt_num(user['gram_balance'], 2)}</b>\n\n"
        f"🏷 NFT-юзернеймов: {nft_u} · 📞 номеров: {nft_n} · 🎒 предметов: {skins}\n"
        f"📅 В экосистеме с {esc((user.get('created_at') or '')[:10])}"
    )
    await show(call, text, kb([btn("🗂 Мои NFT и номера", "my:items")], back_row("menu:more")))


# ═══════════════════════════ донат: хаб ═════════════════════════════════════


async def send_stars_invoice(bot: Bot, chat_id: int, *, title: str, description: str, payload: str, stars: int, label: str):
    await bot.send_invoice(
        chat_id=chat_id,
        title=title[:32],
        description=description[:255],
        payload=payload[:128],
        currency="XTR",
        prices=[LabeledPrice(label=label[:32], amount=stars)],
    )


def parse_int(text: str) -> int | None:
    t = (text or "").replace(" ", "").replace("_", "").replace(",", "").replace(" ", "")
    return int(t) if t.isdigit() else None


async def donat_hub(event):
    if not await need_feature(event, "store"):
        return
    text = (
        f"💎 <b>Донат {config.BRAND}</b>\n\n"
        "Оплата звёздами Telegram, выдача — сразу после оплаты.\n\n"
        f"⭐ {config.CURRENCY_NAME} — пополнение баланса\n"
        "✅ Верификация — Мажор для аккаунта или канала\n"
        "⚠️ Метка SCAM — пометить мошенника\n"
        "📞 Номера +888 — анонимный номер\n"
        "🏷 NFT-юзернеймы — коллекционный @username\n\n"
        "<i>Покупки действуют внутри RiseGram, не в официальном Telegram.</i>"
    )
    await show(
        event,
        text,
        kb(
            [btn(f"⭐ {config.CURRENCY_NAME}", "rs:self"), btn("✅ Верификация", "donat:verify")],
            [btn("⚠️ Метка SCAM", "donat:scam"), btn("📞 Номера +888", "donat:numbers")],
            [btn("🏷 NFT-юзернеймы", "donat:nft")],
            [webapp_button("primary")],
            back_row(),
        ),
    )


@router.message(Command("shop"))
async def cmd_shop(message: Message, state: FSMContext):
    await state.clear()
    await donat_hub(message)


@router.callback_query(F.data == "donat:hub")
async def cb_donat(call: CallbackQuery):
    await donat_hub(call)


# ═══════════════════════════ RiseStars ══════════════════════════════════════


async def rs_screen(event, user: dict, target_id: int = 0):
    if not await need_feature(event, "store"):
        return
    target = user if target_id in (0, user["tg_id"]) else await db.get_user(target_id)
    if not target:
        await show(event, "❌ Игрок не найден.", kb(back_row("donat:hub")))
        return
    tid = 0 if target["tg_id"] == user["tg_id"] else target["tg_id"]
    rate = config.RISESTARS_PER_STAR
    text = (
        f"⭐ <b>{config.CURRENCY_NAME}</b>\n\n"
        f"Курс: {rate} {config.CURRENCY_NAME} = 1 ⭐\n"
        f"Зачисление на {esc(db.shown_username(target))}.\n\n"
        "Выберите количество или введите своё:"
    )

    def pack(p: int):
        stars = p // rate
        return btn(f"⭐ {fmt_num(p)} · {fmt_num(stars)} ⭐", f"rs:pack:{stars}:{tid}", style="primary" if p == 10_000 else None)

    packs = config.RISESTARS_PACKS
    rows = [[pack(p) for p in packs[i:i + 2]] for i in range(0, len(packs), 2)]
    rows.append([btn("✏️ Своё количество", f"rs:custom:{tid}", style="success")])
    rows.append([btn("👥 Зачислить другому", "rs:gift")])
    rows.append(back_row("donat:hub"))
    await show(event, text, kb(*rows))


@router.callback_query(F.data == "rs:self")
async def cb_rs_self(call: CallbackQuery, user: dict):
    await rs_screen(call, user)


async def send_rs_invoice(bot: Bot, payer: dict, stars: int, target_id: int) -> str | None:
    """None — счёт отправлен, иначе текст ошибки."""
    if stars < 1 or stars > config.MAX_STARS_PER_INVOICE:
        return f"Допустимо от 1 до {fmt_num(config.MAX_STARS_PER_INVOICE)} ⭐ за раз."
    target = await db.get_user(target_id)
    if not target or target.get("banned"):
        return "Получатель не найден."
    amount = stars * config.RISESTARS_PER_STAR
    await send_stars_invoice(
        bot,
        payer["tg_id"],
        title=f"{fmt_num(amount)} {config.CURRENCY_NAME}",
        description=f"Пополнение {config.CURRENCY_NAME} в {config.BRAND} для {db.shown_username(target)}.",
        payload=f"rs:{stars}:{target_id}",
        stars=stars,
        label=f"{config.CURRENCY_NAME} x{fmt_num(amount)}",
    )
    return None


@router.callback_query(F.data.startswith("rs:pack:"))
async def cb_rs_pack(call: CallbackQuery, user: dict):
    if not await need_feature(call, "store"):
        return
    _, _, stars_s, tid_s = call.data.split(":")
    stars, tid = int(stars_s), int(tid_s) or user["tg_id"]
    if stars * config.RISESTARS_PER_STAR not in config.RISESTARS_PACKS:
        await call.answer("Пакет недоступен.", show_alert=True)
        return
    err = await send_rs_invoice(call.bot, user, stars, tid)
    await call.answer(err or "Счёт отправлен ниже ⬇️", show_alert=bool(err))


@router.callback_query(F.data.startswith("rs:custom:"))
async def cb_rs_custom(call: CallbackQuery, user: dict, state: FSMContext):
    if not await need_feature(call, "store"):
        return
    tid = int(call.data.split(":")[2]) or user["tg_id"]
    await state.set_state(Flow.custom_stars)
    await state.update_data(target=tid)
    await show(
        call,
        f"✏️ <b>Своё количество</b>\n\nОтправьте число {config.CURRENCY_NAME} (от {fmt_num(config.MIN_CUSTOM_RISESTARS)}). "
        f"Курс {config.RISESTARS_PER_STAR} = 1 ⭐, сумма округлится вверх до целых ⭐.",
        cancel_kb("rs:self"),
    )


@router.message(StateFilter(Flow.custom_stars), F.text)
async def msg_rs_custom(message: Message, state: FSMContext, user: dict):
    n = parse_int(message.text)
    if n is None or n < config.MIN_CUSTOM_RISESTARS:
        await message.answer(f"Введите целое число не меньше {fmt_num(config.MIN_CUSTOM_RISESTARS)}.")
        return
    stars = math.ceil(n / config.RISESTARS_PER_STAR)
    data = await state.get_data()
    err = await send_rs_invoice(message.bot, user, stars, int(data.get("target") or user["tg_id"]))
    if err:
        await message.answer("❌ " + err)
        return
    await state.clear()


@router.callback_query(F.data == "rs:gift")
async def cb_rs_gift(call: CallbackQuery, state: FSMContext):
    if not await need_feature(call, "store"):
        return
    await state.set_state(Flow.gift_target)
    await show(
        call,
        "👥 <b>Зачислить другому</b>\n\nОтправьте @юзернейм или ID игрока. Он должен хотя бы раз запустить бота.",
        cancel_kb("rs:self"),
    )


@router.message(StateFilter(Flow.gift_target), F.text)
async def msg_rs_gift(message: Message, state: FSMContext, user: dict):
    target = await db.find_user(message.text)
    if not target or target.get("banned"):
        await message.answer("❌ Игрок не найден. Он должен запустить бота. Попробуйте ещё раз:")
        return
    await state.clear()
    await rs_screen(message, user, target["tg_id"])


# ═══════════════════════════ верификация «Мажор» ════════════════════════════


async def is_chat_admin(bot: Bot, name: str, uid: int) -> bool | None:
    """True/False — результат проверки, None — бот не может проверить (не состоит в чате)."""
    try:
        m = await bot.get_chat_member(f"@{name}", uid)
    except (TelegramBadRequest, TelegramForbiddenError):
        return None
    return m.status in ("creator", "administrator")


@router.callback_query(F.data == "donat:verify")
async def cb_verify_hub(call: CallbackQuery):
    if not await need_feature(call, "verification"):
        return
    await show(
        call,
        "✅ <b>Верификация</b>\n\nВыберите вид верификации:",
        kb([btn(f"💎 Мажор · от {config.VERIFY_PRICE} ⭐", "verify:major", style="primary")], back_row("donat:hub")),
    )


@router.callback_query(F.data == "verify:major")
async def cb_verify_major(call: CallbackQuery):
    if not await need_feature(call, "verification"):
        return
    p = config.VERIFY_PRICE
    await show(
        call,
        "💎 <b>Мажор</b>\n\nГалочка статуса рядом с именем: в профиле, топе и приложении RiseGram. "
        "Действует внутри экосистемы, в официальном Telegram галочки не будет.\n\nВыберите, что верифицировать:",
        kb(
            [btn(f"👤 Аккаунт · {p} ⭐", "verify:account", style="success")],
            [btn(f"📣 Канал или группа · {p} ⭐", "verify:channel")],
            back_row("donat:verify"),
        ),
    )


@router.callback_query(F.data == "verify:account")
async def cb_verify_account(call: CallbackQuery, user: dict):
    if not await need_feature(call, "verification"):
        return
    if user.get("verified"):
        await call.answer("Ваш аккаунт уже верифицирован ✅", show_alert=True)
        return
    await send_stars_invoice(
        call.bot,
        user["tg_id"],
        title="Мажор · аккаунт",
        description="Статус «Мажор» для вашего аккаунта в RiseGram.",
        payload=f"vm:a:{user['tg_id']}",
        stars=config.VERIFY_PRICE,
        label="Мажор · аккаунт",
    )
    await call.answer("Счёт отправлен ниже ⬇️")


@router.callback_query(F.data == "verify:channel")
async def cb_verify_channel(call: CallbackQuery, state: FSMContext):
    if not await need_feature(call, "verification"):
        return
    await state.set_state(Flow.verify_channel)
    await show(
        call,
        "📣 <b>Мажор для канала или группы</b>\n\nОтправьте @юзернейм канала или группы. "
        "Добавьте бота администратором туда — так мы убедимся, что чат ваш.",
        cancel_kb("verify:major"),
    )


@router.message(StateFilter(Flow.verify_channel), F.text)
async def msg_verify_channel(message: Message, state: FSMContext, user: dict):
    name = db.norm_username(message.text)
    if not CHANNEL_RE.match(name):
        await message.answer("❌ Нужен корректный @юзернейм (латиница, цифры, _; от 4 символов). Ещё раз:")
        return
    if await db.fetchone("SELECT 1 AS x FROM verified_entities WHERE target=?", (name,)):
        await message.answer("✅ Этот чат уже верифицирован.")
        await state.clear()
        return
    ok = await is_chat_admin(message.bot, name, user["tg_id"])
    if ok is None:
        await message.answer("❌ Не могу проверить чат. Добавьте бота администратором и отправьте юзернейм снова:")
        return
    if not ok and user["tg_id"] != config.OWNER_ID:
        await message.answer("❌ Вы не администратор этого чата.")
        await state.clear()
        return
    await state.clear()
    await send_stars_invoice(
        message.bot,
        user["tg_id"],
        title="Мажор · канал или группа",
        description=f"Статус «Мажор» для @{name} в RiseGram.",
        payload=f"vm:c:{name}",
        stars=config.VERIFY_PRICE,
        label=f"Мажор · @{name}"[:32],
    )


# ═══════════════════════════ метка SCAM ═════════════════════════════════════


@router.callback_query(F.data == "donat:scam")
async def cb_scam_hub(call: CallbackQuery):
    if not await need_feature(call, "scam_labels"):
        return
    p = config.SCAM_PRICE
    await show(
        call,
        "⚠️ <b>Метка SCAM</b>\n\nПометить аккаунт или канал мошенника. Заявку проверяет модерация: "
        "если метку не подтвердят, звёзды вернутся автоматически.",
        kb(
            [btn(f"👤 Аккаунт · {p} ⭐", "scam:account", style="danger")],
            [btn(f"📣 Канал или группа · {p} ⭐", "scam:channel", style="danger")],
            back_row("donat:hub"),
        ),
    )


@router.callback_query(F.data.in_({"scam:account", "scam:channel"}))
async def cb_scam_kind(call: CallbackQuery, state: FSMContext):
    if not await need_feature(call, "scam_labels"):
        return
    kind = call.data.split(":")[1]
    await state.set_state(Flow.scam_target)
    await state.update_data(kind=kind)
    what = "аккаунта" if kind == "account" else "канала или группы"
    await show(call, f"⚠️ Отправьте @юзернейм {what}, который нужно пометить.", cancel_kb("donat:scam"))


@router.message(StateFilter(Flow.scam_target), F.text)
async def msg_scam_target(message: Message, state: FSMContext, user: dict):
    name = db.norm_username(message.text)
    if not CHANNEL_RE.match(name):
        await message.answer("❌ Нужен корректный @юзернейм. Ещё раз:")
        return
    mine = {db.norm_username(user.get("username")), db.norm_username(user.get("active_username"))} - {""}
    if name in mine or name in config.RESERVED_USERNAMES:
        await message.answer("❌ Этот юзернейм пометить нельзя.")
        return
    if await db.fetchone("SELECT 1 AS x FROM scam_requests WHERE target=? AND status IN ('pending','approved')", (name,)):
        await message.answer("ℹ️ На этот юзернейм уже есть заявка или метка.")
        await state.clear()
        return
    await state.update_data(target=name)
    await state.set_state(Flow.scam_reason)
    await message.answer(
        f"Кратко опишите, почему @{esc(name)} мошенник (10–300 символов). Это увидит модератор:",
        reply_markup=cancel_kb("donat:scam"),
    )


@router.message(StateFilter(Flow.scam_reason), F.text)
async def msg_scam_reason(message: Message, state: FSMContext, user: dict):
    reason = (message.text or "").strip()
    if not 10 <= len(reason) <= 300:
        await message.answer("Нужно от 10 до 300 символов.")
        return
    data = await state.get_data()
    await state.clear()
    kind, target = data.get("kind", "account"), data.get("target")
    if not target:
        await message.answer("Сессия устарела, начните заново: /shop")
        return
    rid = await db.create_scam_request(user["tg_id"], kind, target, reason, config.SCAM_PRICE)
    await send_stars_invoice(
        message.bot,
        user["tg_id"],
        title="Метка SCAM",
        description=f"Заявка #{rid}: пометить @{target}. Если модерация откажет, звёзды вернутся.",
        payload=f"sc:{kind[0]}:{rid}",
        stars=config.SCAM_PRICE,
        label=f"SCAM · @{target}"[:32],
    )


# ═══════════════════════════ номера +888 ════════════════════════════════════


def number_price(fmt: str) -> int:
    return config.NUMBER_FORMATS[fmt]["price"] if fmt in config.NUMBER_FORMATS else config.CUSTOM_NUMBER_PRICE


def number_valid(fmt: str, digits: str) -> bool:
    if not digits.isdigit() or not digits.startswith("888"):
        return False
    rest = digits[3:]
    if fmt in config.NUMBER_FORMATS:
        f = config.NUMBER_FORMATS[fmt]
        return rest.startswith(f["prefix"]) and len(rest) == len(f["prefix"]) + f["digits"]
    if fmt == "c":
        return config.CUSTOM_NUMBER_MIN_DIGITS <= len(rest) <= config.CUSTOM_NUMBER_MAX_DIGITS
    return False


async def pick_free_number(fmt: str, uid: int) -> str | None:
    for _ in range(60):
        d = db.gen_888_digits(fmt)
        if not await db.number_taken(d) and not await db.reserved_by_other("number", d, uid):
            return d
    return None


@router.callback_query(F.data == "donat:numbers")
async def cb_numbers(call: CallbackQuery, user: dict):
    if not await need_feature(call, "auth_numbers"):
        return
    fa, fb = config.NUMBER_FORMATS["a"], config.NUMBER_FORMATS["b"]
    text = (
        "📞 <b>Анонимные номера +888</b>\n\n"
        "Купленный номер станет вашим текущим номером в боте, коды входа будут приходить сюда.\n\n"
        f"🔁 Аккаунт {esc(db.shown_username(user))} будет перенесён на новый номер. Старый номер сохранится в «Мои NFT и номера».\n\n"
        "Выберите формат или задайте свои цифры:"
    )
    await show(
        call,
        text,
        kb(
            [btn(f"💎 {fa['title']} · {fa['price']} ⭐", "num:fmt:a", style="success")],
            [btn(f"📞 {fb['title']} · {fb['price']} ⭐", "num:fmt:b", style="primary")],
            [btn(f"✏️ Свой номер · {config.CUSTOM_NUMBER_PRICE} ⭐", "num:custom")],
            back_row("donat:hub"),
        ),
    )


def number_offer(fmt: str, digits: str):
    price = number_price(fmt)
    text = (
        "📞 <b>Номер на покупку</b>\n\n"
        f"<code>{esc(db.fmt_number(digits))}</code>\n"
        f"Цена: <b>{price} ⭐</b>\n\n"
        "После оплаты это станет вашим текущим номером, а коды входа придут в этого бота."
    )
    rows = [[btn(f"✅ Купить · {price} ⭐", f"num:buy:{fmt}:{digits}", style="success")]]
    if fmt in config.NUMBER_FORMATS:
        rows.append([btn("🎲 Другой номер", f"num:fmt:{fmt}")])
    else:
        rows.append([btn("✏️ Другие цифры", "num:custom")])
    rows.append(back_row("donat:numbers"))
    return text, kb(*rows)


@router.callback_query(F.data.startswith("num:fmt:"))
async def cb_num_fmt(call: CallbackQuery, user: dict):
    if not await need_feature(call, "auth_numbers"):
        return
    fmt = call.data.split(":")[2]
    if fmt not in config.NUMBER_FORMATS:
        return
    d = await pick_free_number(fmt, user["tg_id"])
    if not d:
        await call.answer("Свободных номеров этого формата не осталось.", show_alert=True)
        return
    text, markup = number_offer(fmt, d)
    await show(call, text, markup)


@router.callback_query(F.data == "num:custom")
async def cb_num_custom(call: CallbackQuery, state: FSMContext):
    if not await need_feature(call, "auth_numbers"):
        return
    await state.set_state(Flow.number_custom)
    await show(
        call,
        f"✏️ <b>Свой номер</b>\n\nОтправьте {config.CUSTOM_NUMBER_MIN_DIGITS}–{config.CUSTOM_NUMBER_MAX_DIGITS} цифр после +888, "
        f"например <code>7777</code>. Цена {config.CUSTOM_NUMBER_PRICE} ⭐.",
        cancel_kb("donat:numbers"),
    )


@router.message(StateFilter(Flow.number_custom), F.text)
async def msg_num_custom(message: Message, state: FSMContext, user: dict):
    raw = message.text.strip()
    d = db.digits_of(raw)
    if raw.startswith("+888"):
        d = d[3:]
    full = "888" + d
    if not number_valid("c", full):
        await message.answer(
            f"❌ Нужно {config.CUSTOM_NUMBER_MIN_DIGITS}–{config.CUSTOM_NUMBER_MAX_DIGITS} цифр. Ещё раз:"
        )
        return
    if await db.number_taken(full) or await db.reserved_by_other("number", full, user["tg_id"]):
        await message.answer("❌ Этот номер занят. Попробуйте другой:")
        return
    await state.clear()
    text, markup = number_offer("c", full)
    await message.answer(text, reply_markup=markup)


@router.callback_query(F.data.startswith("num:buy:"))
async def cb_num_buy(call: CallbackQuery, user: dict):
    if not await need_feature(call, "auth_numbers"):
        return
    _, _, fmt, digits = call.data.split(":")
    if not number_valid(fmt, digits):
        await call.answer("Номер недоступен.", show_alert=True)
        return
    if await db.number_taken(digits) or not await db.reserve("number", digits, user["tg_id"]):
        await call.answer("Номер уже занят.", show_alert=True)
        return
    price = number_price(fmt)
    await send_stars_invoice(
        call.bot,
        user["tg_id"],
        title="Номер +888",
        description=f"Анонимный номер {db.fmt_number(digits)}. Станет вашим текущим номером.",
        payload=f"nm:{fmt}:{digits}",
        stars=price,
        label=db.fmt_number(digits)[:32],
    )
    await call.answer(f"Счёт отправлен. Номер зарезервирован на {config.RESERVE_MINUTES} мин.", show_alert=True)


# ═══════════════════════════ NFT-юзернеймы ══════════════════════════════════


def valid_username(name: str) -> bool:
    return bool(NAME_RE.match(name)) and "__" not in name and not name.endswith("_")


def nft_price(name: str) -> int:
    return config.NFT_PRICE_SHORT if len(name) <= 4 else config.NFT_PRICE_LONG


@router.callback_query(F.data == "donat:nft")
async def cb_nft(call: CallbackQuery, state: FSMContext):
    if not await need_feature(call, "nft_usernames"):
        return
    await state.set_state(Flow.nft_name)
    await show(
        call,
        "🏷 <b>NFT-юзернеймы</b>\n\nКоллекционный @username на ваш аккаунт.\n\n"
        f"4 буквы — {config.NFT_PRICE_SHORT} ⭐\n5 и больше — {config.NFT_PRICE_LONG} ⭐\n\n"
        "Отправьте желаемый юзернейм, например <code>@rise</code>.",
        cancel_kb("donat:hub"),
    )


@router.message(StateFilter(Flow.nft_name), F.text)
async def msg_nft_name(message: Message, state: FSMContext, user: dict):
    name = db.norm_username(message.text)
    if not valid_username(name):
        await message.answer(
            f"❌ Юзернейм: {config.NFT_MIN_LEN}–{config.NFT_MAX_LEN} символов, латиница, цифры и _, "
            "начинается с буквы, без «__» и «_» в конце. Ещё раз:"
        )
        return
    if await db.username_taken(name) or await db.reserved_by_other("username", name, user["tg_id"]):
        await message.answer(f"❌ @{esc(name)} занят или зарезервирован. Попробуйте другой:")
        return
    await state.clear()
    price = nft_price(name)
    await message.answer(
        f"🏷 <b>@{esc(name)}</b> свободен!\nЦена: <b>{price} ⭐</b>\n\n"
        "После оплаты он станет вашим активным юзернеймом в RiseGram.",
        reply_markup=kb(
            [btn(f"✅ Купить · {price} ⭐", f"nft:buy:{name}", style="success")],
            [btn("✏️ Другой юзернейм", "donat:nft")],
            back_row("donat:hub"),
        ),
    )


@router.callback_query(F.data.startswith("nft:buy:"))
async def cb_nft_buy(call: CallbackQuery, user: dict):
    if not await need_feature(call, "nft_usernames"):
        return
    name = call.data.split(":", 2)[2]
    if not valid_username(name):
        await call.answer("Недопустимый юзернейм.", show_alert=True)
        return
    if await db.username_taken(name) or not await db.reserve("username", name, user["tg_id"]):
        await call.answer("Юзернейм уже занят.", show_alert=True)
        return
    await send_stars_invoice(
        call.bot,
        user["tg_id"],
        title=f"NFT @{name}"[:32],
        description=f"Коллекционный юзернейм @{name} в RiseGram.",
        payload=f"nu:{name}",
        stars=nft_price(name),
        label=f"@{name}"[:32],
    )
    await call.answer(f"Счёт отправлен. Юзернейм зарезервирован на {config.RESERVE_MINUTES} мин.", show_alert=True)


# ═══════════════════════════ оплата ═════════════════════════════════════════


async def expected_price(payload: str) -> int | None:
    kind, *a = payload.split(":")
    try:
        if kind == "rs":
            return int(a[0])
        if kind == "vm":
            return config.VERIFY_PRICE
        if kind == "sc":
            return config.SCAM_PRICE
        if kind == "nm":
            return number_price(a[0])
        if kind == "nu":
            return nft_price(a[0])
    except (IndexError, ValueError):
        return None
    return None


async def validate_order(uid: int, payload: str, total: int) -> str | None:
    """Текст ошибки для pre_checkout или None, если заказ можно оплатить."""
    user = await db.get_user(uid)
    if not user or (user.get("banned") and uid != config.OWNER_ID):
        return "Аккаунт недоступен."
    exp = await expected_price(payload)
    if exp is None or exp != total:
        return "Счёт устарел. Откройте магазин заново: /shop"
    kind, *a = payload.split(":")
    if kind in ("rs", "vm", "sc", "nm", "nu") and not await db.is_feature_on(
        {"rs": "store", "vm": "verification", "sc": "scam_labels", "nm": "auth_numbers", "nu": "nft_usernames"}[kind]
    ):
        return "Раздел временно отключён."
    try:
        if kind == "rs":
            if not await db.get_user(int(a[1])):
                return "Получатель не найден."
        elif kind == "vm":
            if a[0] == "a" and user.get("verified"):
                return "Аккаунт уже верифицирован."
            if a[0] == "c" and await db.fetchone("SELECT 1 AS x FROM verified_entities WHERE target=?", (a[1],)):
                return "Этот чат уже верифицирован."
        elif kind == "sc":
            req = await db.get_scam_request(int(a[1]))
            if not req or req["status"] != "unpaid" or req["requester_id"] != uid:
                return "Заявка не найдена или уже оплачена."
        elif kind == "nm":
            if not number_valid(a[0], a[1]) or await db.number_taken(a[1]) or await db.reserved_by_other("number", a[1], uid):
                return "Номер уже занят."
        elif kind == "nu":
            if not valid_username(a[0]) or await db.username_taken(a[0]) or await db.reserved_by_other("username", a[0], uid):
                return "Юзернейм уже занят."
    except (IndexError, ValueError):
        return "Счёт повреждён."
    return None


@router.pre_checkout_query()
async def on_pre_checkout(q: PreCheckoutQuery):
    err = await validate_order(q.from_user.id, q.invoice_payload, q.total_amount)
    if err:
        await q.answer(ok=False, error_message=err)
    else:
        await q.answer(ok=True)


async def deliver_order(bot: Bot, uid: int, payload: str, stars: int, charge: str) -> tuple[bool, str, str, str]:
    """Выдаёт покупку. Возвращает (успех, тип, описание, текст пользователю)."""
    kind, *a = payload.split(":")
    me = await db.get_user(uid)
    if kind == "rs":
        n, target = int(a[0]), int(a[1])
        amount = n * config.RISESTARS_PER_STAR
        if await db.add_balance(target, "risestars", amount) is None:
            return False, "risestars", "Получатель не найден", ""
        t = await db.get_user(target)
        if target == uid:
            return True, "risestars", f"{fmt_num(amount)} {config.CURRENCY_NAME}", (
                f"✅ Оплата прошла успешно! Зачислено <b>{fmt_num(amount)} {config.CURRENCY_NAME}</b>."
            )
        await safe_send(bot, target, f"🎁 {esc(db.shown_username(me))} подарил(а) вам <b>{fmt_num(amount)} {config.CURRENCY_NAME}</b>!")
        return True, "risestars", f"{fmt_num(amount)} {config.CURRENCY_NAME} → {db.shown_username(t)}", (
            f"✅ Оплата прошла успешно! {esc(db.shown_username(t))} получил(а) <b>{fmt_num(amount)} {config.CURRENCY_NAME}</b>."
        )
    if kind == "vm":
        if a[0] == "a":
            await db.set_verified(uid, True)
            return True, "verify", "Мажор · аккаунт", "✅ Готово! Ваш аккаунт получил статус <b>Мажор</b>."
        await db.add_verified_entity(uid, "channel", a[1])
        return True, "verify", f"Мажор · @{a[1]}", f"✅ Готово! @{esc(a[1])} получил(а) статус <b>Мажор</b>."
    if kind == "sc":
        rid = int(a[1])
        if not await db.mark_scam_paid(rid, charge):
            return False, "scam", "Заявка уже оплачена", ""
        req = await db.get_scam_request(rid)
        await safe_send(
            bot,
            config.OWNER_ID,
            f"⚠️ <b>Заявка SCAM #{rid}</b>\nЦель: @{esc(req['target'])} ({esc(req['kind'])})\n"
            f"От: {esc(db.shown_username(me))} (<code>{uid}</code>)\nПричина: {esc(req['reason'])}",
            kb([btn("✅ Подтвердить", f"adm:scam:ok:{rid}", style="success"), btn("❌ Отклонить и вернуть ⭐", f"adm:scam:no:{rid}", style="danger")]),
        )
        return True, "scam", f"Метка SCAM · @{req['target']} (заявка #{rid})", (
            f"✅ Оплата прошла. Заявка <b>#{rid}</b> на @{esc(req['target'])} отправлена модерации. "
            "Если метку не подтвердят, звёзды вернутся автоматически."
        )
    if kind == "nm":
        fmt, digits = a[0], a[1]
        if not await db.grant_number_to_user(uid, digits, stars, True):
            await db.release("number", digits)
            return False, "number", "Номер уже занят", ""
        await db.release("number", digits)
        return True, "number", db.fmt_number(digits), (
            f"✅ Номер <code>{esc(db.fmt_number(digits))}</code> теперь ваш. Коды входа будут приходить в этого бота."
        )
    if kind == "nu":
        name = a[0]
        if not valid_username(name) or await db.username_taken(name) or not await db.add_nft(uid, "username", name, stars):
            await db.release("username", name)
            return False, "nft_username", "Юзернейм уже занят", ""
        await db.set_active_username(uid, name)
        await db.release("username", name)
        return True, "nft_username", f"NFT @{name}", f"✅ Юзернейм <b>@{esc(name)}</b> теперь ваш и выбран активным."
    return False, "unknown", "Неизвестный товар", ""


@router.message(F.successful_payment)
async def on_paid(message: Message, user: dict):
    sp = message.successful_payment
    uid, stars, charge, payload = message.from_user.id, sp.total_amount, sp.telegram_payment_charge_id, sp.invoice_payload
    try:  # запись ДО выдачи: повторная доставка апдейта не выдаст товар второй раз
        pid = await db.add_purchase(uid, "pending", "В обработке", stars, charge, payload, "processing")
    except sqlite3.IntegrityError:
        return
    try:
        ok, itype, desc, text = await deliver_order(message.bot, uid, payload, stars, charge)
    except Exception:
        log.exception("deliver_order failed uid=%s payload=%s", uid, payload)
        ok, itype, desc, text = False, "error", "Ошибка выдачи", ""
    if ok:
        await db.execute("UPDATE purchases SET item_type=?, item_desc=?, status='done' WHERE id=?", (itype, desc, pid))
        await message.answer(text, reply_markup=kb([btn("🏠 Меню", "menu:main")]))
        if uid != config.OWNER_ID:
            await safe_send(
                message.bot,
                config.OWNER_ID,
                f"💳 <b>Покупка</b>: {esc(desc)}\nОт: {esc(db.shown_username(user))} (<code>{uid}</code>) · {stars} ⭐",
            )
        return
    try:
        await message.bot.refund_star_payment(user_id=uid, telegram_payment_charge_id=charge)
        status, note = "refunded", "Не удалось выдать покупку, поэтому звёзды возвращены на ваш счёт Telegram."
    except Exception:
        log.exception("refund failed uid=%s charge=%s", uid, charge)
        status, note = "failed", "Не удалось выдать покупку, а возврат не прошёл автоматически. Напишите владельцу — вернём вручную."
        await safe_send(message.bot, config.OWNER_ID, f"🚨 Не удалось вернуть ⭐: uid <code>{uid}</code>, charge <code>{esc(charge)}</code>, {stars} ⭐, {esc(payload)}")
    await db.execute("UPDATE purchases SET item_type=?, item_desc=?, status=? WHERE id=?", (itype, desc, status, pid))
    await message.answer(f"⚠️ {note}", reply_markup=kb([btn("🏠 Меню", "menu:main")]))


# ═══════════════════════════ покупки, мои NFT и номера ══════════════════════

STATUS_ICON = {"done": "✅", "refunded": "↩️", "failed": "⚠️", "processing": "⏳"}


@router.callback_query(F.data == "menu:purchases")
async def cb_purchases(call: CallbackQuery, user: dict):
    rows = await db.list_purchases(user["tg_id"], 10)
    text = "🕒 <b>Мои покупки</b>\n\n"
    if not rows:
        text += "Покупок пока нет."
    else:
        for p in rows:
            text += f"{STATUS_ICON.get(p['status'], '•')} {esc(p['item_desc'])} — {p['price_xtr']} ⭐ · {esc((p['created_at'] or '')[:10])}\n"
    await show(call, text, kb(back_row()))


async def items_screen(event, user: dict):
    nums = await db.list_nft(user["tg_id"], "number")
    names = await db.list_nft(user["tg_id"], "username")
    text = "🗂 <b>Мои NFT и номера</b>\n\n"
    rows = []
    if not nums and not names:
        text += "Пока пусто. Купить можно в разделе «Донат»."
    if names:
        text += "<b>Юзернеймы</b>\n"
        for n in names:
            on = db.norm_username(user.get("active_username")) == n["value"]
            text += f"{'🟢' if on else '⚪'} @{esc(n['value'])}\n"
            if not on:
                rows.append([btn(f"Выбрать @{n['value']}", f"my:un:{n['id']}")])
        if user.get("active_username"):
            rows.append([btn("Убрать активный юзернейм", "my:un:0")])
        text += "\n"
    if nums:
        text += "<b>Номера</b>\n"
        for n in nums:
            on = user.get("phone_digits") == n["value"]
            text += f"{'🟢' if on else '⚪'} <code>{esc(db.fmt_number(n['value']))}</code>\n"
            if not on:
                rows.append([btn(f"Сделать текущим {db.fmt_number(n['value'])}", f"my:ph:{n['id']}")])
    await show(event, text, kb(*rows[:20], back_row("menu:more")))


@router.callback_query(F.data == "my:items")
async def cb_items(call: CallbackQuery, user: dict):
    await items_screen(call, user)


@router.callback_query(F.data.startswith("my:un:"))
async def cb_my_username(call: CallbackQuery, user: dict):
    item_id = int(call.data.split(":")[2])
    if item_id == 0:
        await db.set_active_username(user["tg_id"], None)
        user["active_username"] = None
    else:
        it = await db.get_nft(item_id)
        if not it or it["tg_id"] != user["tg_id"] or it["item_type"] != "username":
            await call.answer("Не найдено.", show_alert=True)
            return
        await db.set_active_username(user["tg_id"], it["value"])
        user["active_username"] = it["value"]
    await items_screen(call, user)


@router.callback_query(F.data.startswith("my:ph:"))
async def cb_my_phone(call: CallbackQuery, user: dict):
    it = await db.get_nft(int(call.data.split(":")[2]))
    if not it or it["tg_id"] != user["tg_id"] or it["item_type"] != "number":
        await call.answer("Не найдено.", show_alert=True)
        return
    if not await db.set_phone(user["tg_id"], it["value"]):
        await call.answer("Не удалось сменить номер.", show_alert=True)
        return
    user = await db.get_user(user["tg_id"])
    await items_screen(call, user)


# ═══════════════════════════ /doxnumber и /migrate ══════════════════════════


@router.message(Command("doxnumber"))
async def cmd_doxnumber(message: Message, user: dict):
    if not await need_feature(message, "auth_numbers"):
        return
    if user.get("dox_claimed"):
        await message.answer("ℹ️ Бонусный номер +1 уже получен.")
        return
    for _ in range(40):
        d = db.gen_us_digits()
        if not await db.number_taken(d):
            break
    else:
        await message.answer("Не удалось подобрать номер, попробуйте позже.")
        return
    if await db.claim_dox_bonus(user["tg_id"], d):
        await message.answer(
            f"🇺🇸 Ваш номер <code>{esc(db.fmt_number(d))}</code> активирован, бонус <b>{fmt_num(config.DOXNUMBER_BONUS)} {config.CURRENCY_NAME}</b> зачислен.\n"
            "Старый номер сохранён в «Мои NFT и номера».",
            reply_markup=kb([btn("🏠 Меню", "menu:main")]),
        )
    else:
        await message.answer("❌ Не получилось выдать номер, попробуйте ещё раз.")


@router.message(Command("migrate"))
async def cmd_migrate(message: Message, state: FSMContext):
    if not await need_feature(message, "auth_numbers"):
        return
    await state.set_state(Flow.migrate_number)
    await message.answer(
        "🔁 <b>Привязать старый номер</b>\n\nОтправьте номер из прошлой версии RiseGram. "
        "Привязка работает только для номеров, которые владелец заранее добавил в список переноса.",
        reply_markup=cancel_kb("menu:main"),
    )


@router.message(StateFilter(Flow.migrate_number), F.text)
async def msg_migrate(message: Message, state: FSMContext, user: dict):
    d = db.digits_of(message.text)
    if len(d) < 7:
        await message.answer("❌ Введите номер полностью.")
        return
    await state.clear()
    if await db.claim_legacy_number(d, user["tg_id"]):
        await message.answer(f"✅ Номер <code>{esc(db.fmt_number(d))}</code> привязан и стал текущим.")
    else:
        await message.answer("❌ Номер не найден в списке переноса или уже привязан.")


# ═══════════════════════════ топ, бонус, инвентарь ══════════════════════════


async def top_screen(event):
    if not await need_feature(event, "top"):
        return
    rows = await db.top_users(10)
    medals = ["🥇", "🥈", "🥉"] + ["▫️"] * 7
    text = f"🏆 <b>Топ-10 по {config.CURRENCY_NAME}</b>\n\n"
    if not rows:
        text += "Пока никого нет."
    for i, r in enumerate(rows):
        mark = " ✅" if r.get("verified") else ""
        text += f"{medals[i]} {esc(db.shown_username(r))}{mark} — <b>{fmt_num(r['risestars'])}</b>\n"
    await show(event, text, kb(back_row("menu:more")))


@router.message(Command("top"))
async def cmd_top(message: Message):
    await top_screen(message)


@router.callback_query(F.data == "more:top")
async def cb_top(call: CallbackQuery):
    await top_screen(call)


async def bonus_do(event, user: dict):
    if not await need_feature(event, "bonus"):
        return
    ok, wait = await db.claim_daily_bonus(user["tg_id"])
    if ok:
        text = (
            f"🎁 Бонус получен: <b>+{fmt_num(config.DAILY_BONUS_RISESTARS)} {config.CURRENCY_NAME}</b> и "
            f"<b>+{fmt_num(config.DAILY_BONUS_GRAM, 2)} 💎</b>.\nСледующий — через 24 часа."
        )
    else:
        text = f"⏳ Бонус уже получен. Следующий через {wait}."
    await show(event, text, kb(back_row("menu:more")))


@router.message(Command("bonus"))
async def cmd_bonus(message: Message, user: dict):
    await bonus_do(message, user)


@router.callback_query(F.data == "more:bonus")
async def cb_bonus(call: CallbackQuery, user: dict):
    await bonus_do(call, user)


@router.callback_query(F.data == "more:inv")
async def cb_inv(call: CallbackQuery, user: dict):
    if not await need_feature(call, "inventory"):
        return
    items = await db.list_skins(user["tg_id"], 30)
    text = "🎒 <b>Инвентарь</b>\n\n"
    if not items:
        text += "Пока пусто. Предметы выдаёт администрация за активность."
    for it in items:
        text += f"{config.RARITY_EMOJI.get(it['rarity'], '⚪')} {esc(it['item_name'])} «{esc(it['skin'])}»\n"
    await show(call, text, kb(back_row("menu:more")))


# ═══════════════════════════ задания ════════════════════════════════════════


@router.callback_query(F.data == "more:tasks")
async def cb_tasks(call: CallbackQuery, user: dict):
    if not await need_feature(call, "tasks"):
        return
    tasks = await db.list_tasks()
    text = "🎯 <b>Задания</b>\n\n"
    rows = []
    if not tasks:
        text += "Заданий пока нет."
    for t in tasks:
        done = await db.task_claimed(t["id"], user["tg_id"])
        full = bool(t["max_claims"]) and t["claims_count"] >= t["max_claims"]
        mark = "✅" if done else ("🚫" if full else "🎯")
        text += f"{mark} <b>{esc(t['title'])}</b> — {fmt_num(t['reward'])} {config.CURRENCY_NAME}\n"
        if not done and not full:
            rows.append([btn(f"Открыть: {t['title'][:28]}", f"task:{t['id']}")])
    await show(call, text, kb(*rows[:15], back_row("menu:more")))


@router.callback_query(F.data.regexp(r"^task:\d+$"))
async def cb_task_open(call: CallbackQuery, user: dict):
    t = await db.get_task(int(call.data.split(":")[1]))
    if not t or not t["active"]:
        await call.answer("Задание недоступно.", show_alert=True)
        return
    text = f"🎯 <b>{esc(t['title'])}</b>\n\n{esc(t['description'])}\n\nНаграда: <b>{fmt_num(t['reward'])} {config.CURRENCY_NAME}</b>"
    rows = []
    if t["channel"]:
        rows.append([btn("📣 Перейти", url=f"https://t.me/{t['channel']}")])
    rows.append([btn("✅ Проверить", f"task:check:{t['id']}", style="success")])
    await show(call, text, kb(*rows, back_row("more:tasks")))


@router.callback_query(F.data.startswith("task:check:"))
async def cb_task_check(call: CallbackQuery, user: dict):
    t = await db.get_task(int(call.data.split(":")[2]))
    if not t:
        await call.answer("Задание недоступно.", show_alert=True)
        return
    if t["channel"]:
        try:
            m = await call.bot.get_chat_member(f"@{t['channel']}", user["tg_id"])
            subscribed = m.status not in ("left", "kicked")
        except (TelegramBadRequest, TelegramForbiddenError):
            await call.answer("Не могу проверить подписку: бот должен быть админом канала.", show_alert=True)
            return
        if not subscribed:
            await call.answer("Вы ещё не подписаны.", show_alert=True)
            return
    ok, reason, reward = await db.claim_task(t["id"], user["tg_id"])
    if not ok:
        await call.answer(reason, show_alert=True)
        return
    await show(call, f"🎉 Задание выполнено! Начислено <b>{fmt_num(reward)} {config.CURRENCY_NAME}</b>.", kb(back_row("more:tasks")))


# ═══════════════════════════ переводы ═══════════════════════════════════════


async def send_start(event, state: FSMContext):
    if not await need_feature(event, "transfers"):
        return
    await state.set_state(Flow.transfer_target)
    await show(
        event,
        f"💸 <b>Перевод {config.CURRENCY_NAME}</b>\n\nОтправьте @юзернейм или ID получателя.",
        cancel_kb("menu:more"),
    )


@router.message(Command("send"))
async def cmd_send(message: Message, state: FSMContext):
    await send_start(message, state)


@router.callback_query(F.data == "more:send")
async def cb_send(call: CallbackQuery, state: FSMContext):
    await send_start(call, state)


@router.message(StateFilter(Flow.transfer_target), F.text)
async def msg_transfer_target(message: Message, state: FSMContext, user: dict):
    t = await db.find_user(message.text)
    if not t or t.get("banned"):
        await message.answer("❌ Игрок не найден. Ещё раз:")
        return
    if t["tg_id"] == user["tg_id"]:
        await message.answer("❌ Нельзя переводить самому себе.")
        return
    await state.update_data(to=t["tg_id"])
    await state.set_state(Flow.transfer_amount)
    await message.answer(
        f"Кому: {esc(db.shown_username(t))}\nВаш баланс: <b>{fmt_num(user['risestars'])}</b>\n"
        f"Минимум {fmt_num(config.TRANSFER_MIN)}. Введите сумму:",
        reply_markup=cancel_kb("menu:more"),
    )


@router.message(StateFilter(Flow.transfer_amount), F.text)
async def msg_transfer_amount(message: Message, state: FSMContext, user: dict):
    try:
        amount = round(float(message.text.replace(" ", "").replace(",", ".")), 2)
    except ValueError:
        await message.answer("Введите число.")
        return
    if not math.isfinite(amount) or amount < config.TRANSFER_MIN:
        await message.answer(f"Минимальная сумма — {fmt_num(config.TRANSFER_MIN)}.")
        return
    data = await state.get_data()
    to = data.get("to")
    await state.clear()
    ok, err = await db.transfer_risestars(user["tg_id"], to, amount, config.TRANSFER_FEE_PERCENT)
    if not ok:
        await message.answer("❌ " + err)
        return
    await message.answer(f"✅ Переведено <b>{fmt_num(amount)} {config.CURRENCY_NAME}</b>.", reply_markup=kb([btn("🏠 Меню", "menu:main")]))
    await safe_send(message.bot, to, f"💸 {esc(db.shown_username(user))} отправил(а) вам <b>{fmt_num(amount)} {config.CURRENCY_NAME}</b>.")


# ═══════════════════════════ Crash Rocket (на Gram 💎, не за Stars) ═════════
# Игра идёт только на внутренней валюте Gram: её нельзя купить за Stars и нельзя вывести.
# Точка краша честная: P(crash >= x) = (1 - edge) / x, ожидаемый возврат = 96%.

crash_tasks: dict[int, asyncio.Task] = {}
crash_live: dict[int, dict] = {}


def crash_point() -> float:
    r = secrets.randbelow(10**9) / 10**9
    if r < config.CRASH_HOUSE_EDGE:
        return 1.0
    return min(config.CRASH_MAX_MULTIPLIER, math.floor((1 - config.CRASH_HOUSE_EDGE) / (1 - r) * 100) / 100)


async def games_hub(event, user: dict):
    if not await need_feature(event, "games"):
        return
    st = await db.game_stats(user["tg_id"])
    text = (
        "🎮 <b>Игровой зал</b>\n\n"
        "🚀 <b>Crash Rocket</b> — ставка в 💎 Gram, заберите выигрыш до краха ракеты.\n"
        "Играют на Gram: его нельзя купить за ⭐ и вывести, это просто игра.\n\n"
        f"Баланс: <b>{fmt_num(user['gram_balance'], 2)} 💎</b>\n"
        f"Сыграно: {st['played']} · побед: {st['wins']}"
    )
    await show(event, text, kb([btn("🚀 Играть", "crash:new", style="success")], [btn("🎁 Получить Gram (бонус)", "more:bonus")], back_row("menu:more")))


@router.message(Command("games"))
async def cmd_games(message: Message, user: dict):
    await games_hub(message, user)


@router.callback_query(F.data == "games:hub")
async def cb_games(call: CallbackQuery, user: dict):
    await games_hub(call, user)


@router.callback_query(F.data == "crash:new")
async def cb_crash_new(call: CallbackQuery, user: dict, state: FSMContext):
    if not await need_feature(call, "games"):
        return
    if user["tg_id"] in crash_live:
        await call.answer("У вас уже идёт игра.", show_alert=True)
        return
    await state.set_state(Flow.crash_bet)
    await show(
        call,
        f"🚀 <b>Crash Rocket</b>\n\nВведите ставку в 💎 (от {fmt_num(config.CRASH_MIN_BET)} до {fmt_num(config.CRASH_MAX_BET)}).\n"
        f"Баланс: <b>{fmt_num(user['gram_balance'], 2)} 💎</b>",
        cancel_kb("games:hub"),
    )


def crash_kb(gid: int, win: float | None = None):
    label = f"💰 Забрать {fmt_num(win, 2)} 💎" if win is not None else "💰 Забрать"
    return kb([btn(label, f"crash:cash:{gid}", style="success")])


@router.message(StateFilter(Flow.crash_bet), F.text)
async def msg_crash_bet(message: Message, state: FSMContext, user: dict):
    if not await need_feature(message, "games"):
        return
    try:
        bet = round(float(message.text.replace(" ", "").replace(",", ".")), 2)
    except ValueError:
        await message.answer("Введите число.")
        return
    if not math.isfinite(bet) or not config.CRASH_MIN_BET <= bet <= config.CRASH_MAX_BET:
        await message.answer(f"Ставка от {fmt_num(config.CRASH_MIN_BET)} до {fmt_num(config.CRASH_MAX_BET)}.")
        return
    uid = user["tg_id"]
    if uid in crash_live:
        await message.answer("У вас уже идёт игра.")
        return
    cp = crash_point()
    gid = await db.start_game(uid, "crash", bet, cp)
    if gid is None:
        await message.answer("❌ Недостаточно 💎 Gram.")
        return
    await state.clear()
    msg = await message.answer(f"🚀 Ракета взлетает… ×1.00\nСтавка: {fmt_num(bet, 2)} 💎", reply_markup=crash_kb(gid, bet))
    crash_live[uid] = {"gid": gid, "bet": bet, "cp": cp, "t0": time.monotonic(), "msg": msg, "done": False}
    crash_tasks[uid] = asyncio.create_task(run_crash(message.bot, uid))


def mult_at(t: float) -> float:
    return min(config.CRASH_MAX_MULTIPLIER, math.floor(math.exp(min(config.CRASH_GROWTH_PER_SECOND * t, 20.0)) * 100) / 100)


async def finish_crash(uid: int, won: bool, mult: float):
    g = crash_live.get(uid)
    if not g or g["done"]:
        return
    g["done"] = True
    payout = round(g["bet"] * mult, 2) if won else 0.0
    ok = await db.settle_game(g["gid"], won, payout)
    crash_live.pop(uid, None)
    if not ok:
        return
    user = await db.get_user(uid)
    bal = fmt_num(user["gram_balance"], 2) if user else "—"
    if won:
        text = f"✅ Забрали на ×{mult:.2f}! Выигрыш <b>{fmt_num(payout, 2)} 💎</b>\nБаланс: {bal} 💎"
    else:
        text = f"💥 Ракета упала на ×{g['cp']:.2f}. Ставка {fmt_num(g['bet'], 2)} 💎 потеряна.\nБаланс: {bal} 💎"
    try:
        await g["msg"].edit_text(text, reply_markup=kb([btn("🚀 Ещё раз", "crash:new", style="success")], [btn("🏠 Меню", "menu:main")]))
    except TelegramBadRequest:
        await g["msg"].answer(text)


async def run_crash(bot: Bot, uid: int):
    try:
        g = crash_live.get(uid)
        while g and not g["done"]:
            await asyncio.sleep(config.CRASH_TICK_SECONDS)
            g = crash_live.get(uid)
            if not g or g["done"]:
                return
            t = time.monotonic() - g["t0"]
            m = mult_at(t)
            if m >= g["cp"]:
                await finish_crash(uid, False, g["cp"])
                return
            try:
                await g["msg"].edit_text(
                    f"🚀 Ракета летит… <b>×{m:.2f}</b>\nСтавка: {fmt_num(g['bet'], 2)} 💎",
                    reply_markup=crash_kb(g["gid"], g["bet"] * m),
                )
            except TelegramRetryAfter as e:
                await asyncio.sleep(e.retry_after)
            except TelegramBadRequest:
                pass
    except asyncio.CancelledError:
        raise
    except Exception:
        log.exception("crash loop failed uid=%s", uid)
        g = crash_live.pop(uid, None)
        if g and not g["done"]:
            await db.execute("UPDATE games SET status='refunded', finished_at=? WHERE id=? AND status='active'", (db.now(), g["gid"]))
            await db.add_balance(uid, "gram_balance", g["bet"])
    finally:
        crash_tasks.pop(uid, None)


@router.callback_query(F.data.startswith("crash:cash:"))
async def cb_crash_cash(call: CallbackQuery):
    uid = call.from_user.id
    g = crash_live.get(uid)
    if not g or g["done"] or g["gid"] != int(call.data.split(":")[2]):
        await call.answer("Игра уже закончилась.")
        return
    m = mult_at(time.monotonic() - g["t0"])
    if m >= g["cp"]:  # ракета уже упала, кнопка нажата поздно
        await finish_crash(uid, False, g["cp"])
        return
    await finish_crash(uid, True, m)


# ═══════════════════════════ запуск ═════════════════════════════════════════


@router.message(StateFilter("*"), Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext, user: dict):
    await state.clear()
    await message.answer("Отменено.", reply_markup=kb([btn("🏠 Меню", "menu:main")]))


@router.message(F.text & ~F.text.startswith("/"), StateFilter(None))
async def msg_fallback(message: Message, user: dict):
    await message.answer(await main_text(user), reply_markup=main_kb())


async def on_startup(bot: Bot):
    await db.init_db()
    await bot.delete_webhook(drop_pending_updates=True)
    me = await bot.get_me()
    log.info("%s запущен как @%s", config.BRAND, me.username)
    await db.log_admin_action(0, f"Бот запущен (@{me.username})")


async def main():
    if not config.BOT_TOKEN:
        raise SystemExit("BOT_TOKEN не задан. Railway → Variables → BOT_TOKEN (токен из @BotFather).")
    bot = Bot(config.BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=MemoryStorage())
    dp.message.outer_middleware(GuardMiddleware())
    dp.callback_query.outer_middleware(GuardMiddleware())
    dp.include_router(admin_router)  # админка первой: /admin7206852053 не должна попадать в обычный fallback
    dp.include_router(router)
    dp.startup.register(on_startup)
    await db.init_db()
    runner = await start_api(bot)
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        if runner:
            await runner.cleanup()
        for t in list(crash_tasks.values()):
            t.cancel()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
