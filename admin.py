"""admin.py — GOD MODE RiseGram.

Доступ: ТОЛЬКО владелец (config.OWNER_ID) и только по команде /admin7206852053.
Кнопки админки нигде в обычном меню нет. Проверка идёт по числовому ID, юзернеймам доверять нельзя.
"""
from __future__ import annotations

import asyncio
import logging
import math
import re

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.filters import Command, Filter, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

import config
import database as db
from ui import btn, esc, fmt_num, kb, safe_send, show

log = logging.getLogger("risegram.admin")

PAGE = 8


class OwnerOnly(Filter):
    async def __call__(self, event: Message | CallbackQuery) -> bool:
        u = event.from_user
        return bool(u and u.id == config.OWNER_ID)


admin_router = Router(name="admin")
admin_router.message.filter(OwnerOnly(), F.chat.type == "private")
admin_router.callback_query.filter(OwnerOnly(), F.message.chat.type == "private")


class Adm(StatesGroup):
    input = State()      # универсальный ввод; действие лежит в data["act"]
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
    flags = ("🚫" if u.get("banned") else "") + ("✅" if u.get("verified") else "")
    return f"{esc(db.shown_username(u))} · <code>{u['tg_id']}</code> {flags}".strip()


async def parse_target(text: str) -> dict | None:
    return await db.find_user(text)


# ════════════════════════════ главный экран ══════════════════════════════


async def home_screen(event, user: dict):
    await db.ensure_owner()
    me = await db.get_user(user["tg_id"])
    maint = await db.get_meta("maintenance") == "1"
    pending = await db.scalar("SELECT COUNT(*) FROM scam_requests WHERE status='pending'")
    text = (
        f"💎 <b>{config.BRAND} Ecosystem — GOD MODE</b>\n\n"
        f"{config.BRAND} ID: <b>{me['rise_id']}</b>\n"
        f"⭐ {config.CURRENCY_NAME}: <b>{fmt_num(me['risestars'])}</b>\n"
        f"💎 Gram: <b>{fmt_num(me['gram_balance'], 2)}</b>\n"
        f"☑️ Статус: <b>Официальный владелец</b>\n"
        f"🛠 Техработы: <b>{'ВКЛ' if maint else 'выкл'}</b>"
    )
    scam_label = f"⚠️ SCAM-заявки ({pending})" if pending else "⚠️ SCAM-заявки"
    markup = kb(
        [btn("📊 Аналитика", "adm:stats", style="primary"), btn("👥 Юзеры", "adm:users:0", style="primary")],
        [btn("💰 Выдача валюты", "adm:grant", style="success"), btn("🏷 Выдать юзернеймы", "adm:inv", style="success")],
        [btn("⚡ Тумблеры функций", "adm:feat", style="success"), btn("🔨 Банхаммер", "adm:ban", style="danger")],
        [btn("🎯 Задания", "adm:tasks"), btn("🛒 Маркет", "adm:mkt:0"), btn(scam_label, "adm:scam:0", style="danger" if pending else None)],
        [btn("🧾 Покупки", "adm:buys:0"), btn("📣 Рассылка", "adm:cast")],
        [btn("🧪 Тестеры", "adm:testers"), btn("📞 Перенос номеров", "adm:legacy")],
        [btn("📜 Системные логи", "adm:logs:0"), btn(("🟢 Техработы: выключить" if maint else "🛠 Техработы: включить"), "adm:maint")],
        [btn("🏠 Выйти в меню", "menu:main")],
    )
    await show(event, text, markup)


@admin_router.message(Command(config.ADMIN_COMMAND))
async def cmd_admin(message: Message, state: FSMContext, user: dict):
    await state.clear()
    await home_screen(message, user)


# ════════════════════════════ экраны ══════════════════════════════════════


async def screen_stats(call: CallbackQuery):
    s = await db.stats()
    text = (
        "📊 <b>Аналитика</b>\n\n"
        f"👥 Игроков: <b>{s['users']}</b> (+{s['new_day']} за сутки, +{s['new_week']} за неделю)\n"
        f"✅ Верифицировано: {s['verified']} · 🚫 в бане: {s['banned']} · 🧪 тестеров: {s['testers']}\n\n"
        f"⭐ Собрано Stars: <b>{fmt_num(s['stars_total'])}</b> за {s['purchases']} покупок (за сутки {fmt_num(s['stars_day'])})\n"
        f"↩️ Возвращено: {fmt_num(s['refunded'])}\n"
        f"💰 Эмиссия {config.CURRENCY_NAME}: {fmt_num(s['risestars_emission'])}\n"
        f"💎 Эмиссия Gram: {fmt_num(s['gram_emission'], 2)}\n\n"
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
        text += f"• {user_line(u)} · ⭐{fmt_num(u['risestars'])}\n"
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
        f"⭐ {config.CURRENCY_NAME}: <b>{fmt_num(u['risestars'])}</b> · 💎 <b>{fmt_num(u['gram_balance'], 2)}</b>\n"
        f"🏷 NFT/номера: {nfts}\n"
        f"💳 Потратил: {fmt_num(spent)} ⭐ · 🎮 игр: {st['played']}\n"
        f"📅 Регистрация: {esc((u.get('created_at') or '')[:16])}\n"
        f"👁 Был: {esc((u.get('last_seen') or '')[:16])}"
    )
    if note:
        text = f"{note}\n\n" + text
    ban_label = "✅ Разбанить" if u["banned"] else "🔨 Забанить"
    ver_label = "❎ Снять Мажор" if u["verified"] else "✅ Выдать Мажор"
    markup = kb(
        [btn("⭐ RiseStars ±", f"adm:g:{uid}:stars", style="success"), btn("💎 Gram ±", f"adm:g:{uid}:gram", style="success")],
        [btn(ban_label, f"adm:uban:{uid}", style="success" if u["banned"] else "danger"), btn(ver_label, f"adm:uver:{uid}")],
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
        rows.append([btn(f"🗑 {label}"[:60], f"adm:nftdel:{it['id']}:{uid}", style="danger")])
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


async def remove_nft(item_id: int) -> str:
    it = await db.get_nft(item_id)
    if not it:
        return "Уже удалено."
    uid = it["tg_id"]
    await db.delete_nft(item_id)
    u = await db.get_user(uid)
    if it["item_type"] == "username" and u and db.norm_username(u.get("active_username")) == it["value"]:
        await db.set_active_username(uid, None)
    if it["item_type"] == "number" and u and u.get("phone_digits") == it["value"]:
        await fresh_phone(uid)
        return "Номер удалён, игроку выдан новый."
    return "Удалено."


async def revoke_goods(p: dict) -> str:
    """Откатывает выданный товар по payload покупки (насколько это возможно)."""
    kind, *a = (p.get("payload") or "").split(":")
    uid = p["tg_id"]
    try:
        if kind == "rs":
            amount = int(a[0]) * config.RISESTARS_PER_STAR
            u = await db.get_user(int(a[1]))
            if u:
                new = max(0.0, u["risestars"] - amount)
                await db.set_balance(u["tg_id"], "risestars", new)
            return f"списано до {fmt_num(amount)} RiseStars"
        if kind == "vm" and a[0] == "a":
            await db.set_verified(uid, False)
            return "снят Мажор"
        if kind == "vm":
            await db.execute("DELETE FROM verified_entities WHERE target=?", (a[1],))
            return "снят Мажор с канала"
        if kind == "nu":
            row = await db.fetchone("SELECT id FROM user_nft_inventory WHERE item_type='username' AND value=?", (a[0],))
            return await remove_nft(row["id"]) if row else "юзернейм уже удалён"
        if kind == "nm":
            row = await db.fetchone("SELECT id FROM user_nft_inventory WHERE item_type='number' AND value=?", (a[1],))
            return await remove_nft(row["id"]) if row else "номер уже удалён"
        if kind == "sc":
            await db.execute("UPDATE scam_requests SET status='refunded' WHERE id=? AND status IN ('pending','approved')", (int(a[1]),))
            return "метка SCAM снята"
    except (IndexError, ValueError):
        pass
    return "откат вручную не требуется"


async def refund_stars(bot: Bot, uid: int, charge: str) -> tuple[bool, str]:
    try:
        await bot.refund_star_payment(user_id=uid, telegram_payment_charge_id=charge)
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
    text = f"⚠️ <b>Заявки SCAM</b> (на проверке: {total}, активных меток: {approved})\n\n"
    rows = []
    for r in rows_db:
        text += f"<b>#{r['id']}</b> @{esc(r['target'])} ({esc(r['kind'])})\nОт <code>{r['requester_id']}</code>: {esc(r['reason'])}\n\n"
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
        if not await db.resolve_scam(rid, "approved", config.OWNER_ID):
            await call.answer("Заявка уже обработана.", show_alert=True)
            return
        await audit(f"SCAM #{rid} подтверждена: @{req['target']}")
        await safe_send(call.bot, req["requester_id"], f"✅ Ваша заявка <b>#{rid}</b>: метка SCAM на @{esc(req['target'])} подтверждена.")
        note = f"✅ Заявка #{rid} подтверждена."
    else:
        if not await db.resolve_scam(rid, "rejected", config.OWNER_ID):
            await call.answer("Заявка уже обработана.", show_alert=True)
            return
        ok, err = await refund_stars(call.bot, req["requester_id"], req["charge_id"] or "")
        if req["charge_id"]:
            await db.execute("UPDATE purchases SET status=? WHERE charge_id=?", ("refunded" if ok else "failed", req["charge_id"]))
        await audit(f"SCAM #{rid} отклонена, возврат {'ок' if ok else 'ОШИБКА ' + err}")
        await safe_send(
            call.bot,
            req["requester_id"],
            f"❌ Заявка <b>#{rid}</b> на @{esc(req['target'])} отклонена модерацией. "
            + ("Звёзды вернулись на ваш счёт." if ok else "Возврат звёзд не прошёл автоматически, мы вернём их вручную."),
        )
        note = f"❌ Заявка #{rid} отклонена. " + ("⭐ возвращены." if ok else f"⚠️ Возврат не прошёл: {esc(err)}")
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
    text = "🧪 <b>Тестеры</b>\n\n"
    rows = []
    for x in t[:15]:
        text += f"• @{esc(x['username'] or '—')} · <code>{x['tg_id']}</code>\n"
        rows.append([btn(f"➖ {x['username'] or x['tg_id']}"[:60], f"adm:trm:{x['tg_id']}", style="danger")])
    if not t:
        text += "Пока никого."
    rows.append([btn("➕ Добавить тестера", "adm:tadd", style="success")])
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


async def apply_balance(bot: Bot, uid: int, cur: str, delta: float) -> str:
    col = "risestars" if cur == "stars" else "gram_balance"
    sym = "⭐" if cur == "stars" else "💎"
    u = await db.get_user(uid)
    if not u:
        return "❌ Игрок не найден."
    if uid == config.OWNER_ID:
        return "♾ У владельца бесконечные балансы, менять их не нужно."
    if delta < 0 and -delta > u[col]:
        await db.set_balance(uid, col, 0)
        new = 0.0
    else:
        new = await db.add_balance(uid, col, delta)
    await audit(f"Баланс {db.shown_username(u)} ({uid}): {delta:+g} {col} → {new}")
    await safe_send(bot, uid, f"🛠 Администрация изменила ваш баланс: <b>{delta:+g} {sym}</b>.")
    return f"✅ {esc(db.shown_username(u))}: <b>{delta:+g} {sym}</b>, теперь {fmt_num(new, 2)}"


@admin_router.message(StateFilter(Adm.input), F.text)
async def on_input(message: Message, state: FSMContext, user: dict):
    data = await state.get_data()
    act, text, bot = data.get("act"), (message.text or "").strip(), message.bot
    if text.startswith("/"):  # команда отменяет ввод
        await state.clear()
        if text.lstrip("/").split("@")[0] == config.ADMIN_COMMAND:
            await home_screen(message, user)
        return

    async def done(note: str, markup=None):
        await state.clear()
        await message.answer(note, reply_markup=markup or kb(home_row()))

    async def again(note: str):
        await message.answer(note, reply_markup=kb(cancel_row()))

    # ── поиск и выбор игрока ──
    if act == "usearch":
        await state.clear()
        total = await db.count_users(text)
        rows_db = await db.list_users(PAGE, 0, text)
        out = f"🔎 <b>Поиск «{esc(text)}»</b> ({total})\n\n"
        rows = [[btn(f"{db.shown_username(u)} · {u['tg_id']}", f"adm:u:{u['tg_id']}")] for u in rows_db]
        out += "\n".join(f"• {user_line(u)}" for u in rows_db) or "Ничего не найдено."
        await message.answer(out, reply_markup=kb(*rows, home_row()))
        return
    if act in ("grant_target", "inv_target", "ban_target"):
        t = await parse_target(text)
        if not t:
            return await again("❌ Игрок не найден. Ещё раз (@юзернейм, ID или номер):")
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
        else:
            await ask_usernames(message, state, t)
        return

    # ── валюта ──
    if act == "g_amount":
        v = parse_amount(text)
        if v is None or v == 0:
            return await again("❌ Введите число, например <code>1000</code> или <code>-500</code>:")
        note = await apply_balance(bot, data["uid"], data["cur"], v)
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
        n = await db.revoke_scam_label(text)
        await audit(f"Снята метка SCAM @{db.norm_username(text)} ({n})")
        return await done("✅ Метка снята." if n else "ℹ️ Активной метки на этот юзернейм нет.", kb([btn("⚠️ К заявкам", "adm:scam:0")], home_row()))
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
        try:
            await bot.send_message(uid, f"📣 {esc(text)}")
            ok += 1
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after)
            try:
                await bot.send_message(uid, f"📣 {esc(text)}")
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

    await state.clear()

    if route == "noop":
        return
    if route == "home":
        return await home_screen(call, user)
    if route == "stats":
        return await screen_stats(call)
    if route == "maint":
        new = "0" if await db.get_meta("maintenance") == "1" else "1"
        await db.set_meta("maintenance", new)
        await audit(f"Техработы {'включены' if new == '1' else 'выключены'}")
        return await home_screen(call, user)

    # ── игроки ──
    if route == "users":
        return await screen_users(call, num(parts, 2))
    if route == "usearch":
        return await ask(call, state, "usearch", "🔎 Введите часть имени, @юзернейм или ID:", "adm:users:0")
    if route == "u":
        return await screen_user(call, num(parts, 2))
    if route in ("uban", "uver", "udel", "udelok", "ucode", "unum", "uun", "uph", "umsg"):
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
        if route == "uver":
            await db.set_verified(uid, not u["verified"])
            await audit(f"Мажор {'снят' if u['verified'] else 'выдан'}: {uid}")
            return await screen_user(call, uid, "✅ Готово.")
        if route == "ucode":
            code = await db.issue_auth_code(uid)
            await audit(f"Выдан код входа игроку {uid}")
            return await screen_user(call, uid, f"🔑 Код для {esc(u['phone'])}: <code>{code}</code> (действует {config.AUTH_CODE_TTL_SECONDS // 60} мин)")
        if route == "udel":
            if uid == config.OWNER_ID:
                return await call.answer("Владельца удалить нельзя.", show_alert=True)
            return await show(
                call,
                f"🗑 Удалить {user_line(u)} со всеми данными? Это необратимо.",
                kb([btn("🗑 Да, удалить", f"adm:udelok:{uid}", style="danger")], [btn("✖️ Нет", back)]),
            )
        if route == "udelok":
            if uid == config.OWNER_ID:
                return await call.answer("Владельца удалить нельзя.", show_alert=True)
            await db.delete_user(uid)
            await audit(f"Удалён игрок {db.shown_username(u)} ({uid})")
            return await show(call, "🗑 Игрок удалён.", kb([btn("👥 Юзеры", "adm:users:0")], home_row()))
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
        return await ask(call, state, "grant_target", "💰 <b>Выдача и списание</b>\n\nВведите @юзернейм, ID или номер игрока:")
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
        return await ask(call, state, "inv_target", "🏷 <b>Выдача NFT-юзернеймов</b>\n\nВведите @юзернейм, ID или номер игрока:")
    if route == "mkt":
        return await screen_market(call, num(parts, 2))
    if route == "mktdel":
        L = await db.cancel_listing(0, num(parts, 2), admin=True)
        if L:
            await audit(f"Лот #{L['id']} снят админом")
            await safe_send(bot, L["seller_id"], "🛠 Администрация сняла ваш лот с маркета.")
        return await screen_market(call, num(parts, 3), "🗑 Лот снят." if L else "Лот уже закрыт.")
    if route == "nfts":
        return await screen_nfts(call, num(parts, 2))
    if route == "nftdel":
        res = await remove_nft(num(parts, 2))
        await audit(f"Удалён NFT #{num(parts, 2)} у {num(parts, 3)}: {res}")
        return await screen_nfts(call, num(parts, 3), f"🗑 {res}")

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
        return await ask(call, state, "ban_target", "🔨 <b>Банхаммер</b>\n\nВведите @юзернейм, ID или номер. Статус переключится (бан ↔ разбан):")

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
    if route == "tadd":
        return await ask(call, state, "tester_add", "🧪 Введите @юзернейм или ID тестера:", "adm:testers")
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
