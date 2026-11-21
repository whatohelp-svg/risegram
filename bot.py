"""bot.py — основной бот RiseGram: авторизация, магазин (Telegram Stars), номера, NFT, игры.

Запуск:  python bot.py
Админка: владелец /owner, администраторы и тестеры /admin.
"""
from __future__ import annotations

import asyncio
import os
from urllib.parse import urlsplit
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
import gift_drops
from admin import admin_router
from api import client_wallet_line, start_api, sync_collectible_usernames, sync_mtproto_verification
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
    game_bet = State()
    market_price = State()
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
        try:
            await sync_collectible_usernames(int(user["tg_id"]))
            await sync_mtproto_verification(int(user["tg_id"]))
        except Exception:
            logging.getLogger("risegram.bot").debug("RiseGram account sync deferred", exc_info=True)
        if isinstance(event, Message) and event.successful_payment:
            # Деньги уже списаны Telegram: товар выдаём всегда, даже при бане или техработах,
            # иначе человек заплатил бы и ничего не получил (и возврата тоже не было бы).
            result = await handler(event, data)
            try:
                await sync_collectible_usernames(int(user["tg_id"]))
                await sync_mtproto_verification(int(user["tg_id"]), force=True)
            except Exception:
                logging.getLogger("risegram.bot").debug("post-payment RiseGram sync deferred", exc_info=True)
            return result
        is_admin = tg_user.id == config.OWNER_TELEGRAM_ID or await db.is_admin(int(user["tg_id"]))
        data["is_admin"] = is_admin
        if user.get("banned") and not is_admin:
            await _reply_block(event, "❌ Ваш аккаунт заблокирован.")
            return
        if not is_admin and await db.get_meta("maintenance") == "1":
            await _reply_block(event, "🛠 Технические работы. Загляните позже.")
            return
        if is_cb and not (event.data or "").startswith("adm:"):
            state = data.get("state")
            if state:
                await state.clear()
        try:
            return await handler(event, data)
        finally:
            try:
                await sync_collectible_usernames(int(user["tg_id"]))
            except Exception:
                logging.getLogger("risegram.bot").debug("collectible username post-handler sync deferred", exc_info=True)
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
    if user.get("verified_risegram"):
        parts.append("☑️ Верификация RiseGram")
    if user.get("verified_major"):
        parts.append("💠 Верификация Major")
    if await db.is_scam_target(user.get("username")) or await db.is_scam_target(user.get("active_username")):
        parts.append("⚠️ SCAM")
    return (" · " + " · ".join(parts)) if parts else ""


async def main_text(user: dict) -> str:
    return (
        f"💼 <b>{config.BRAND}</b>\n\n"
        f"📞 <b>Номер:</b> <code>{esc(user['phone'])}</code>\n"
        f"👤 <b>Аккаунт:</b> {esc(user.get('name') or 'User')} · ID <code>{user['rise_id']}</code>{await badges(user)}\n"
        f"{await client_wallet_line(user)}\n"
        f"Выберите действие ниже или откройте приложение."
    )


def main_kb():
    return kb(
        [btn("🔒 Код входа", "menu:code", style="primary"), btn("📞 Мой номер", "menu:number")],
        [btn("👤 Профиль", "more:profile"), btn("ℹ️ Помощь", "menu:help")],
        [btn("🔄 Обновить баланс", "menu:main")],
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
    "Баланс RiseStars и GRAM поступает из приложения. Для покупки откройте профиль получателя → «Подарок»."
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
    await show(call, "Профиль RiseGram", kb([btn("👤 Профиль", "more:profile")], back_row()))


@router.callback_query(F.data == "more:profile")
async def cb_profile(call: CallbackQuery, user: dict):
    nft_u = len(await db.list_nft(user["tg_id"], "username"))
    nft_n = len(await db.list_nft(user["tg_id"], "number"))
    text = (
        "👤 <b>Профиль</b>\n\n"
        f"Имя: {esc(user.get('name'))}\n"
        f"Юзернейм: {esc(db.shown_username(user))}{await badges(user)}\n"
        f"Telegram ID: <code>{user['tg_id']}</code>\n"
        f"{config.BRAND} ID: <code>{user['rise_id']}</code>\n"
        f"Номер: <code>{esc(user['phone'])}</code>\n\n"
        f"{await client_wallet_line(user)}\n"
        f"🏷 NFT-юзернеймов: {nft_u} · 📞 номеров: {nft_n}\n"
        f"📅 В экосистеме с {esc((user.get('created_at') or '')[:10])}"
    )
    await show(call, text, kb([btn("🗂 Мои NFT и номера", "my:items")], back_row("menu:main")))


# ═══════════════════════════ донат: хаб ═════════════════════════════════════


@router.pre_checkout_query()
async def on_pre_checkout(q: PreCheckoutQuery):
    await q.answer(ok=False, error_message="Оплата доступна только в приложении RiseGram.")


@router.message(F.successful_payment)
async def on_paid(message: Message, user: dict):
    sp = message.successful_payment
    uid, stars, charge, payload = user["tg_id"], sp.total_amount, sp.telegram_payment_charge_id, sp.invoice_payload
    try:  # запись ДО выдачи: повторная доставка апдейта не выдаст товар второй раз
        pid = await db.add_purchase(uid, "pending", "В обработке", stars, charge, payload, "processing")
    except sqlite3.IntegrityError:
        return
    itype, desc = "legacy_invoice", "Возврат оплаты старого счёта"
    try:
        telegram_id = await db.telegram_chat_id(uid)
        if telegram_id is None:
            raise RuntimeError("account has no Telegram payment link")
        await message.bot.refund_star_payment(user_id=telegram_id, telegram_payment_charge_id=charge)
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


ITEMS_SHOWN = 30  # сколько юзернеймов и номеров показывать списком: у Telegram лимит 4096 символов на сообщение


async def items_screen(event, user: dict):
    nums = await db.list_nft(user["tg_id"], "number")
    names = await db.list_nft(user["tg_id"], "username")
    text = "🗂 <b>Мои NFT и номера</b>\n\n"
    rows = []
    listed = await db.listed_item_ids(user["tg_id"])
    if not nums and not names:
        text += "Пока пусто."
    if names:
        text += "<b>Юзернеймы</b>\n"
        active_count = sum(1 for n in names if n.get("is_active"))
        text += f"Активно: {active_count} из {len(names)} — ограничений по количеству нет\n"
        for n in names[:ITEMS_SHOWN]:
            on = bool(n.get("is_active"))
            lock = " 🛒 на маркете" if n["id"] in listed else ""
            text += f"{'🟢' if on else '⚪'} @{esc(n['value'])}{lock}\n"
            if n["id"] not in listed:
                action = "Отключить" if on else "Использовать"
                rows.append([btn(f"{action} @{n['value']}", f"my:un:{n['id']}")])
        if len(names) > ITEMS_SHOWN:
            text += f"…и ещё {len(names) - ITEMS_SHOWN}\n"
        if active_count:
            rows.append([btn("Отключить все NFT-юзернеймы", "my:un:0")])
        text += "\n"
    if nums:
        text += "<b>Номера</b>\n"
        for n in nums[:ITEMS_SHOWN]:
            on = user.get("phone_digits") == n["value"]
            lock = " 🛒 на маркете" if n["id"] in listed else ""
            text += f"{'🟢' if on else '⚪'} <code>{esc(db.fmt_number(n['value']))}</code>{lock}\n"
            if not on and n["id"] not in listed:
                rows.append([btn(f"Сделать текущим {db.fmt_number(n['value'])}", f"my:ph:{n['id']}")])
        if len(nums) > ITEMS_SHOWN:
            text += f"…и ещё {len(nums) - ITEMS_SHOWN}\n"
    await show(event, text, kb(*rows[:20], back_row("menu:main")))


@router.callback_query(F.data == "my:items")
async def cb_items(call: CallbackQuery, user: dict):
    await items_screen(call, user)


@router.callback_query(F.data.startswith("my:un:"))
async def cb_my_username(call: CallbackQuery, user: dict):
    item_id = int(call.data.split(":")[2])
    if item_id == 0:
        await db.execute("UPDATE user_nft_inventory SET is_active=0 WHERE tg_id=? AND item_type='username'", (user["tg_id"],))
        await db.set_active_username(user["tg_id"], None)
    else:
        it = await db.get_nft(item_id)
        if not it or it["tg_id"] != user["tg_id"] or it["item_type"] != "username":
            await call.answer("Не найдено.", show_alert=True)
            return
        ok, _ = await db.set_nft_username_active(user["tg_id"], item_id, not bool(it.get("is_active")))
        if not ok:
            await call.answer("NFT не найден или выставлен на маркете.", show_alert=True)
            return
    user = await db.get_user(user["tg_id"])
    await items_screen(call, user)


@router.callback_query(F.data.startswith("my:ph:"))
async def cb_my_phone(call: CallbackQuery, user: dict):
    it = await db.get_nft(int(call.data.split(":")[2]))
    if not it or it["tg_id"] != user["tg_id"] or it["item_type"] != "number":
        await call.answer("Не найдено.", show_alert=True)
        return
    if await db.listing_for_item(it["id"]):
        await call.answer("Номер выставлен на маркете. Сначала снимите лот.", show_alert=True)
        return
    old = user.get("phone_digits")
    if old and old != it["value"]:
        # Прежний номер сохраняем в инвентаре, иначе он пропадёт насовсем (как при покупке номера).
        # Если он там уже есть, add_nft вернёт False — это нормально.
        await db.add_nft(user["tg_id"], "number", old, 0)
    if not await db.set_phone(user["tg_id"], it["value"]):
        await call.answer("Не удалось сменить номер.", show_alert=True)
        return
    user = await db.get_user(user["tg_id"])
    await items_screen(call, user)


# ═══════════════════════════ /doxnumber и /migrate ══════════════════════════


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


# ═══════════════════════════ топ, бонус ══════════════════════════


@router.message(Command("shop", "top", "bonus", "send", "games", "market", "doxnumber"))
async def native_operation(message: Message):
    await message.answer("Операции с балансом доступны только в приложении RiseGram.")


@router.callback_query()
async def retired_button(call: CallbackQuery):
    await call.answer("Откройте приложение RiseGram.", show_alert=True)


@router.message(StateFilter("*"), Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext, user: dict):
    await state.clear()
    await message.answer("Отменено.", reply_markup=kb([btn("🏠 Меню", "menu:main")]))


@router.message(F.text & ~F.text.startswith("/"), StateFilter(None))
async def msg_fallback(message: Message, user: dict):
    await message.answer(await main_text(user), reply_markup=main_kb())


async def on_startup(bot: Bot):
    await db.init_db()
    await db.ensure_owner()
    await bot.delete_webhook(drop_pending_updates=False)
    me = await bot.get_me()
    log.info("%s запущен как @%s", config.BRAND, me.username)
    await db.log_admin_action(0, f"Бот запущен (@{me.username})")


async def sync_client_gifts(bot):
    from gift_catalog import populate_client_catalog
    for delay in (0, 10, 30, 60, 120, 300):
        await asyncio.sleep(delay)
        if await populate_client_catalog(bot):
            return


async def main():
    if not config.BOT_TOKEN:
        raise SystemExit("BOT_TOKEN не задан. Railway → Variables → BOT_TOKEN (токен из @BotFather).")
    bot = Bot(config.BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=MemoryStorage())
    dp.message.outer_middleware(GuardMiddleware())
    dp.callback_query.outer_middleware(GuardMiddleware())
    dp.include_router(admin_router)  # панель /admin и владелец /owner обрабатываются раньше общего меню
    dp.include_router(router)
    dp.startup.register(on_startup)
    await db.init_db()
    await asyncio.to_thread(gift_drops.setup)
    runner = await start_api(bot)
    from issuer_marks import bootstrap
    from native_drops import worker
    drop_publisher = asyncio.create_task(worker())
    issuer_setup = asyncio.create_task(bootstrap())
    gift_sync = asyncio.create_task(sync_client_gifts(bot))
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        gift_sync.cancel()
        issuer_setup.cancel()
        drop_publisher.cancel()
        await asyncio.gather(gift_sync, issuer_setup, drop_publisher, return_exceptions=True)
        if runner:
            await runner.cleanup()
        for t in list(crash_tasks.values()) + list(hl_tasks.values()):
            t.cancel()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
