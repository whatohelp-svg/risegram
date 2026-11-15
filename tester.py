"""tester.py — панель тестера RiseGram (отдельный файл, bot.py и admin.py не меняются).

Доступ: игроки из списка тестеров (админка → «🧪 Тестеры») и владелец. Команда: /tester.
Остальным команда не отвечает, как будто её нет.

Что умеет панель (всё безопасно: затрагивает только аккаунт самого тестера, экономику не трогает):
    🔑 Тестовый код входа — выдаёт код для своего номера (проверка входа в приложение)
    📊 Состояние — техработы, лимиты ставок в играх, активные игры и лоты маркета
    ⚡ Модули — какие модули включены или выключены (только просмотр)
    🎮 Мои игры — своя статистика игр
    🗂 Мои NFT и номера — свой инвентарь
    🐞 Сообщить о баге — текст уходит владельцу вместе с ID тестера

Подключается через run.py (там же и запуск бота).
"""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import Command, Filter, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

import config
import database as db
from ui import btn, esc, fmt_num, kb, safe_send, show

log = logging.getLogger("risegram.tester")


async def is_tester(tg_id: int) -> bool:
    return bool(await db.scalar("SELECT COUNT(*) FROM testers WHERE tg_id=?", (tg_id,)))


class TesterAccess(Filter):
    """Пускает владельца и тестеров. Проверка по числовому ID, юзернеймам не доверяем."""

    async def __call__(self, event: Message | CallbackQuery) -> bool:
        u = event.from_user
        if u is None:
            return False
        return u.id == config.OWNER_ID or await is_tester(u.id)


class TesterFlow(StatesGroup):
    bug = State()


tester_router = Router(name="tester")
tester_router.message.filter(TesterAccess(), F.chat.type == "private")
tester_router.callback_query.filter(TesterAccess(), F.message.chat.type == "private")


# ════════════════════════════ экраны ═════════════════════════════════════════


def home_kb():
    return kb(
        [btn("🔑 Тестовый код входа", "tst:code", style="primary"), btn("📊 Состояние", "tst:state")],
        [btn("⚡ Модули", "tst:feat"), btn("🎮 Мои игры", "tst:games")],
        [btn("🗂 Мои NFT и номера", "tst:inv"), btn("🐞 Сообщить о баге", "tst:bug", style="danger")],
        [btn("🔄 Обновить", "tst:home"), btn("🏠 В меню", "menu:main")],
    )


def back_kb():
    return kb([btn("🧪 В панель", "tst:home", style="primary")])


async def screen_home(event, user: dict):
    u = await db.get_user(user["tg_id"]) or user
    maint = await db.get_meta("maintenance") == "1"
    text = (
        f"🧪 <b>Панель тестера {config.BRAND}</b>\n\n"
        f"👤 {esc(db.shown_username(u))} · ID <code>{u['tg_id']}</code> · {config.BRAND} ID <code>{u['rise_id']}</code>\n"
        f"📞 Номер: <code>{esc(u['phone'])}</code>\n"
        f"⭐ {config.CURRENCY_NAME}: <b>{fmt_num(u['risestars'])}</b> · 💎 Gram: <b>{fmt_num(u['gram_balance'], 2)}</b>\n"
        f"🛠 Техработы: <b>{'ВКЛ' if maint else 'выкл'}</b>\n\n"
        "Здесь можно проверить вход, посмотреть состояние бота и отправить отчёт о баге владельцу."
    )
    await show(event, text, home_kb())


async def screen_code(call: CallbackQuery, user: dict):
    code = await db.issue_auth_code(user["tg_id"])
    await db.log_admin_action(user["tg_id"], f"Тестер {user['tg_id']}: выдан тестовый код входа")
    text = (
        "🔑 <b>Тестовый код входа</b>\n\n"
        f"Номер: <code>{esc(user['phone'])}</code>\n"
        f"Код: <code>{code}</code>\n"
        f"Действует {config.AUTH_CODE_TTL_SECONDS // 60} мин. Предыдущие коды аннулированы."
    )
    await show(call, text, kb([btn("🔑 Новый код", "tst:code")], [btn("🧪 В панель", "tst:home", style="primary")]))


async def screen_state(call: CallbackQuery):
    flags = await db.get_flags()
    off = [config.FEATURES.get(f["feature_name"], f["feature_name"]) for f in flags if not f["is_enabled"]]
    maint = await db.get_meta("maintenance") == "1"
    glo, ghi = config.GAME_LIMITS["gram"]
    slo, shi = config.GAME_LIMITS["stars"]
    text = (
        "📊 <b>Состояние системы</b>\n\n"
        f"🛠 Техработы: <b>{'ВКЛ' if maint else 'выкл'}</b>\n"
        f"🔴 Выключены модули: {esc(', '.join(off)) if off else 'нет'}\n"
        f"🌐 HTTP API: {'включено' if config.API_SECRET else 'выключено (коды выдаёт бот)'}\n\n"
        "🎮 <b>Лимиты ставок</b>\n"
        f"💎 Gram: от {fmt_num(glo)} до {fmt_num(ghi)}\n"
        f"⭐ {config.CURRENCY_NAME}: от {fmt_num(slo)} до {fmt_num(shi)}\n"
        f"Идёт игр сейчас: {await db.active_games_count()}\n\n"
        f"🛒 Активных лотов на маркете: {await db.market_count()}\n"
        f"🎯 Активных заданий: {len(await db.list_tasks())}"
    )
    await show(call, text, kb([btn("🔄 Обновить", "tst:state")], [btn("🧪 В панель", "tst:home", style="primary")]))


async def screen_features(call: CallbackQuery):
    flags = {f["feature_name"]: f["is_enabled"] for f in await db.get_flags()}
    lines = [f"{'🟢' if flags.get(name, 1) else '🔴'} {esc(title)}" for name, title in config.FEATURES.items()]
    text = "⚡ <b>Модули</b>\n\n" + "\n".join(lines) + "\n\n<i>Только просмотр. Переключает владелец в админке.</i>"
    await show(call, text, back_kb())


async def screen_games(call: CallbackQuery, user: dict):
    st = await db.game_stats(user["tg_id"])
    played, wins = st["played"], st["wins"]
    rate = f"{wins / played * 100:.1f}%" if played else "—"
    text = (
        "🎮 <b>Мои игры</b>\n\n"
        f"Сыграно: <b>{played}</b> · побед: <b>{wins}</b> ({rate})\n"
        f"Поставлено всего: {fmt_num(st['bet'], 2)}\n"
        f"Выплачено всего: {fmt_num(st['paid'], 2)}\n\n"
        "<i>Суммы по обеим валютам вместе (Gram и RiseStars).</i>"
    )
    await show(call, text, kb([btn("🎮 Игровой зал", "games:hub", style="success")], [btn("🧪 В панель", "tst:home", style="primary")]))


async def screen_inventory(call: CallbackQuery, user: dict):
    items = await db.list_nft(user["tg_id"])
    names = [i for i in items if i["item_type"] == "username"]
    nums = [i for i in items if i["item_type"] == "number"]
    text = f"🗂 <b>Мои NFT и номера</b>\n\n🏷 Юзернеймов: {len(names)} · 📞 номеров: {len(nums)}\n\n"
    if names:
        text += "<b>Юзернеймы</b>\n" + "\n".join(f"• @{esc(i['value'])}" for i in names[:15]) + "\n\n"
    if nums:
        text += "<b>Номера</b>\n" + "\n".join(f"• <code>{esc(db.fmt_number(i['value']))}</code>" for i in nums[:15]) + "\n\n"
    if not items:
        text += "Пока пусто."
    await show(call, text, kb([btn("🗂 Открыть в боте", "my:items")], [btn("🧪 В панель", "tst:home", style="primary")]))


# ════════════════════════════ хендлеры ═══════════════════════════════════════


@tester_router.message(Command("tester"))
async def cmd_tester(message: Message, state: FSMContext, user: dict):
    await state.clear()
    await screen_home(message, user)


@tester_router.callback_query(F.data.startswith("tst:"))
async def cb_tester(call: CallbackQuery, state: FSMContext, user: dict):
    action = (call.data or "").split(":")[1]
    await state.clear()
    if action == "home":
        return await screen_home(call, user)
    if action == "code":
        return await screen_code(call, user)
    if action == "state":
        return await screen_state(call)
    if action == "feat":
        return await screen_features(call)
    if action == "games":
        return await screen_games(call, user)
    if action == "inv":
        return await screen_inventory(call, user)
    if action == "bug":
        await state.set_state(TesterFlow.bug)
        return await show(
            call,
            "🐞 <b>Сообщить о баге</b>\n\nОпишите, что делали и что пошло не так (5–1000 символов). "
            "Сообщение уйдёт владельцу вместе с вашим ID.",
            kb([btn("✖️ Отмена", "tst:home", style="danger")]),
        )
    await call.answer("Неизвестное действие.")


@tester_router.message(StateFilter(TesterFlow.bug), F.text, ~F.text.startswith("/"))
async def msg_bug(message: Message, state: FSMContext, user: dict):
    text = (message.text or "").strip()
    if not 5 <= len(text) <= 1000:
        await message.answer("Нужно от 5 до 1000 символов. Опишите баг подробнее:", reply_markup=kb([btn("✖️ Отмена", "tst:home", style="danger")]))
        return
    await state.clear()
    sent = await safe_send(
        message.bot,
        config.OWNER_ID,
        f"🐞 <b>Баг от тестера</b>\n{esc(db.shown_username(user))} · <code>{user['tg_id']}</code>\n\n{esc(text)}",
    )
    await db.log_admin_action(user["tg_id"], f"Баг-репорт от тестера {user['tg_id']}: {text[:200]}")
    await message.answer(
        "✅ Отчёт отправлен владельцу. Спасибо!" if sent else "⚠️ Отчёт сохранён в логах, но владельцу напрямую доставить не вышло.",
        reply_markup=back_kb(),
    )
