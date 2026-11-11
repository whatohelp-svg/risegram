import aiosqlite
import asyncio
import logging
from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from database import DB_PATH, init_db

ADMIN_ID = 7206852053
OWNER_IDS = {7206852053, 10138362, 1, 13}
OWNER_USERNAMES = {"koyzzov", "@koyzzov", "owner", "@owner", "rise", "@rise"}

admin_router = Router()

class ColoredInlineKeyboardButton(InlineKeyboardButton):
    def __init__(self, text: str, callback_data: str = None, url: str = None, style: str = None, icon_custom_emoji_id: str = None):
        super().__init__(text=text, callback_data=callback_data, url=url)
        if style:
            self.style = style
        if icon_custom_emoji_id:
            self.icon_custom_emoji_id = icon_custom_emoji_id

    def model_dump(self, *args, **kwargs):
        data = super().model_dump(*args, **kwargs)
        if hasattr(self, "style") and self.style:
            data["style"] = self.style
        if hasattr(self, "icon_custom_emoji_id") and self.icon_custom_emoji_id:
            data["icon_custom_emoji_id"] = self.icon_custom_emoji_id
        return data

class GodStates(StatesGroup):
    target_user = State()
    val_type = State()
    grant_amount = State()
    ban_target = State()

async def log_admin_action(admin_id: int, action: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT INTO admin_logs (admin_id, action_text) VALUES (?, ?)", (admin_id, action))
        await db.commit()

def is_owner(user_id: int, username: str = "") -> bool:
    if user_id in OWNER_IDS or user_id == ADMIN_ID:
        return True
    if username and username.lower().lstrip("@") in {u.lstrip("@") for u in OWNER_USERNAMES}:
        return True
    return False

@admin_router.message(F.text == "/admin7206852053")
@admin_router.callback_query(F.data == "god_panel")
async def god_panel_entry(event: Message | CallbackQuery, state: FSMContext):
    user_id = event.from_user.id
    username = event.from_user.username or ""
    if not is_owner(user_id, username): return
    await state.clear()
    await init_db()
    
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE users SET risestars = 999999999, gram_balance = 999999.99, verified = 1 WHERE tg_id = ?", 
            (user_id,)
        )
        await db.commit()
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [ColoredInlineKeyboardButton(text="📊 Аналитика", callback_data="god_stats", style="primary"), ColoredInlineKeyboardButton(text="👥 Юзеры", callback_data="god_users", style="primary")],
        [ColoredInlineKeyboardButton(text="💰 Выдача валюты", callback_data="god_grant_menu", style="success"), ColoredInlineKeyboardButton(text="⚡ Тумблеры функций", callback_data="god_features", style="success")],
        [ColoredInlineKeyboardButton(text="🔨 Банхаммер", callback_data="god_ban_menu", style="danger"), ColoredInlineKeyboardButton(text="📜 Системные логи", callback_data="god_logs", style="primary")]
    ])
    
    text = (
        f"💎 <b>RiseGram Ecosystem — GOD MODE</b>\n\n"
        f"⭐ Звёзды: <b>999,999,999</b>\n"
        f"💎 Gram Balance: <b>999,999.99</b>\n"
        f"☑️ Статус: <b>Официальный Владелец</b>"
    )
    
    if isinstance(event, CallbackQuery):
        await event.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    else:
        await event.answer(text, reply_markup=kb, parse_mode="HTML")

@admin_router.callback_query(F.data == "god_stats")
async def god_stats(call: CallbackQuery):
    if not is_owner(call.from_user.id, call.from_user.username): return
    async with aiosqlite.connect(DB_PATH) as db:
        c1 = await (await db.execute("SELECT COUNT(*) FROM users")).fetchone()
        c2 = await (await db.execute("SELECT SUM(price_xtr) FROM purchases")).fetchone()
        c3 = await (await db.execute("SELECT SUM(risestars) FROM users")).fetchone()
    text = f"📊 <b>Аналитика RiseGram:</b>\n\n👥 Игроков: {c1[0]}\n⭐ Собрано XTR: {c2[0] or 0}\n💎 Эмиссия звёзд: {c3[0] or 0}"
    kb = InlineKeyboardMarkup(inline_keyboard=[[ColoredInlineKeyboardButton(text="↩️ Назад", callback_data="god_panel", style="primary")]])
    await call.message.edit_text(text, reply_markup=kb, parse_mode="HTML")

@admin_router.callback_query(F.data == "god_users")
async def god_users(call: CallbackQuery):
    if not is_owner(call.from_user.id, call.from_user.username): return
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute("SELECT tg_id, username, risestars, gram_balance, banned FROM users ORDER BY tg_id DESC LIMIT 10")).fetchall()
    text = "👥 <b>Игроки:</b>\n\n"
    for r in rows:
        text += f"• <code>{r['username']}</code> ({r['tg_id']}) | ⭐ {r['risestars']} | 💎 {r['gram_balance']}\n"
    kb = InlineKeyboardMarkup(inline_keyboard=[[ColoredInlineKeyboardButton(text="↩️ Назад", callback_data="god_panel", style="primary")]])
    await call.message.edit_text(text, reply_markup=kb, parse_mode="HTML")

@admin_router.callback_query(F.data == "god_grant_menu")
async def god_grant_menu(call: CallbackQuery, state: FSMContext):
    if not is_owner(call.from_user.id, call.from_user.username): return
    await state.set_state(GodStates.target_user)
    await call.message.edit_text(
        "💰 <b>Выдача и списание валюты</b>\n\nВведите юзернейм (например <code>@koyzzov</code>) или TG ID игрока:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[ColoredInlineKeyboardButton(text="❌ Отмена", callback_data="god_panel", style="danger")]])
    )

@admin_router.message(GodStates.target_user)
async def process_grant_target(message: Message, state: FSMContext):
    if not is_owner(message.from_user.id, message.from_user.username): return
    query = message.text.strip()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        if query.isdigit():
            user = await (await db.execute("SELECT * FROM users WHERE tg_id = ?", (int(query),))).fetchone()
        else:
            if not query.startswith("@"): query = f"@{query}"
            user = await (await db.execute("SELECT * FROM users WHERE username LIKE ?", (f"%{query}%",))).fetchone()
        if not user:
            return await message.answer("❌ Игрок не найден в базе данных!", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[ColoredInlineKeyboardButton(text="👑 В админку", callback_data="god_panel", style="primary")]]))
            
    await state.update_data(target_tg_id=user["tg_id"], target_name=user["username"])
    await state.set_state(GodStates.val_type)
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [ColoredInlineKeyboardButton(text="⭐ Звёзды (Risestars)", callback_data="val:stars", style="success"), ColoredInlineKeyboardButton(text="💎 Gram Balance", callback_data="val:gram", style="primary")],
        [ColoredInlineKeyboardButton(text="❌ Отмена", callback_data="god_panel", style="danger")]
    ])
    await message.answer(f"👤 Игрок найден: <b>{user['username']}</b> (ID: <code>{user['tg_id']}</code>)\n\nВыберите тип валюты:", reply_markup=kb, parse_mode="HTML")

@admin_router.callback_query(F.data.startswith("val:"))
async def process_val_selection(call: CallbackQuery, state: FSMContext):
    if not is_owner(call.from_user.id, call.from_user.username): return
    vtype = call.data.split(":")[1]
    await state.update_data(val_type=vtype)
    await state.set_state(GodStates.grant_amount)
    await call.message.edit_text(
        f"✍️ Вы выбрали валюту: <b>{'Звёзды ⭐' if vtype == 'stars' else 'Gram 💎'}</b>\n\nВведите число (для списания минус, например <code>-1000</code>):",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[ColoredInlineKeyboardButton(text="❌ Отмена", callback_data="god_panel", style="danger")]])
    )

@admin_router.message(GodStates.grant_amount)
async def process_grant_amount(message: Message, state: FSMContext):
    if not is_owner(message.from_user.id, message.from_user.username): return
    try:
        amount = float(message.text.strip())
    except ValueError:
        return await message.answer("❌ Введите корректное число!")
        
    data = await state.get_data()
    tid = data.get("target_tg_id")
    uname = data.get("target_name")
    vtype = data.get("val_type", "stars")
    
    if not tid:
        return await message.answer("❌ Ошибка сессии: цель не найдена.", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[ColoredInlineKeyboardButton(text="👑 В админку", callback_data="god_panel", style="primary")]]))
        
    await state.clear()
    col = "risestars" if vtype == "stars" else "gram_balance"
    
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(f"UPDATE users SET {col} = {col} + ? WHERE tg_id = ?", (amount, tid))
        await db.commit()
        
    await log_admin_action(ADMIN_ID, f"Изменен баланс {uname}: {amount:+f} ({vtype})")
    await message.answer(f"✅ Успешно! Игроку <b>{uname}</b> изменено: <b>{amount:+g} {'⭐' if vtype == 'stars' else '💎'}</b>", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[ColoredInlineKeyboardButton(text="👑 В админку", callback_data="god_panel", style="primary")]]), parse_mode="HTML")

@admin_router.callback_query(F.data == "god_features")
async def god_features(call: CallbackQuery):
    if not is_owner(call.from_user.id, call.from_user.username): return
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute("SELECT * FROM feature_flags")).fetchall()
    kb = []
    for r in rows:
        st = "🟢 ВКЛ" if r['is_enabled'] else "🔴 ВЫКЛ"
        kb.append([ColoredInlineKeyboardButton(text=f"{r['feature_name']}: {st}", callback_data=f"toggle_feat:{r['feature_name']}", style="success" if r['is_enabled'] else "danger")])
    kb.append([ColoredInlineKeyboardButton(text="↩️ Назад", callback_data="god_panel", style="primary")])
    await call.message.edit_text("⚙️ <b>Управление тумблерами:</b>", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode="HTML")

@admin_router.callback_query(F.data.startswith("toggle_feat:"))
async def toggle_feat(call: CallbackQuery):
    if not is_owner(call.from_user.id, call.from_user.username): return
    fname = call.data.split(":")[1]
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT is_enabled FROM feature_flags WHERE feature_name = ?", (fname,))
        row = await cur.fetchone()
        new_val = 0 if row and row[0] else 1
        await db.execute("UPDATE feature_flags SET is_enabled = ? WHERE feature_name = ?", (new_val, fname))
        await db.commit()
    await god_features(call)

@admin_router.callback_query(F.data == "god_ban_menu")
async def god_ban_menu(call: CallbackQuery, state: FSMContext):
    if not is_owner(call.from_user.id, call.from_user.username): return
    await state.set_state(GodStates.ban_target)
    await call.message.edit_text("🔨 Введите юзернейм или ID для бана:", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[ColoredInlineKeyboardButton(text="❌ Отмена", callback_data="god_panel", style="danger")]]))

@admin_router.message(GodStates.ban_target)
async def process_ban(message: Message, state: FSMContext):
    if not is_owner(message.from_user.id, message.from_user.username): return
    q = message.text.strip()
    await state.clear()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        user = await (await db.execute("SELECT * FROM users WHERE tg_id = ? OR username LIKE ?", (int(q) if q.isdigit() else 0, f"%{q}%"))).fetchone()
        if not user: return await message.answer("❌ Не найден.")
        nb = 0 if user["banned"] else 1
        await db.execute("UPDATE users SET banned = ? WHERE tg_id = ?", (nb, user["tg_id"]))
        await db.commit()
    await message.answer(f"✅ Статус баня изменен для {user['username']}", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[ColoredInlineKeyboardButton(text="👑 В админку", callback_data="god_panel", style="primary")]]))

@admin_router.callback_query(F.data == "god_logs")
async def god_logs(call: CallbackQuery):
    if not is_owner(call.from_user.id, call.from_user.username): return
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        logs = await (await db.execute("SELECT * FROM admin_logs ORDER BY id DESC LIMIT 10")).fetchall()
    text = "📜 <b>Логи:</b>\n\n" + ("\n".join([f"• {l['action_text']}" for l in logs]) if logs else "Пусто")
    kb = InlineKeyboardMarkup(inline_keyboard=[[ColoredInlineKeyboardButton(text="↩️ Назад", callback_data="god_panel", style="primary")]])
    await call.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    
