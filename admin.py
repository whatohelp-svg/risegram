# ============================================================================
# RISEGRAM ECOSYSTEM - GOD MODE / ULTIMATE OWNER ENGINE
# ============================================================================
import aiosqlite
import asyncio
import logging
from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

ADMIN_ID = 7206852053
# Твои официальные ID и юзернеймы для абсолютного доступа
OWNER_IDS = {7206852053, 10138362, 1, 13}
OWNER_USERNAMES = {"koyzzov", "@koyzzov", "owner", "@owner", "rise", "@rise"}

DB_PATH = "risegram.db"

admin_router = Router()

class GodStates(StatesGroup):
    target_user = State()
    custom_value = State()
    task_title = State()
    task_link = State()
    task_reward = State()
    broadcast_text = State()
    tester_username = State()
    ban_target = State()
    grant_amount = State()

async def init_admin_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS testers (
                tg_id INTEGER PRIMARY KEY,
                username TEXT,
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS admin_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                admin_id INTEGER,
                action_text TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        try:
            await db.execute("ALTER TABLE tasks ADD COLUMN link TEXT DEFAULT ''")
        except Exception:
            pass
        try:
            await db.execute("ALTER TABLE users ADD COLUMN banned INTEGER DEFAULT 0")
        except Exception:
            pass
        try:
            await db.execute("ALTER TABLE users ADD COLUMN verified INTEGER DEFAULT 0")
        except Exception:
            pass
        await db.commit()

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
    await init_admin_db()
    
    # Автоматически выдаем тебе вечный бесконечный баланс и верификацию при входе в админку
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE users SET risestars = 999999999, gram_balance = 999999.99, verified = 1 WHERE tg_id = ?", 
            (user_id,)
        )
        await db.commit()
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Статистика", callback_data="god_stats"), InlineKeyboardButton(text="👥 Юзеры & Удаление", callback_data="god_users")],
        [InlineKeyboardButton(text="⚡ Тумблеры фич", callback_data="god_features"), InlineKeyboardButton(text="🎯 Задания", callback_data="god_tasks")],
        [InlineKeyboardButton(text="🧪 Тестеры (Управление)", callback_data="god_testers_menu"), InlineKeyboardButton(text="💰 Выдача валюты", callback_data="god_grant_menu")],
        [InlineKeyboardButton(text="🔨 Бан / Разбан", callback_data="god_ban_menu"), InlineKeyboardButton(text="💸 Возврат XTR", callback_data="god_refund_menu")],
        [InlineKeyboardButton(text="📜 Логи системы", callback_data="god_logs"), InlineKeyboardButton(text="📢 Рассылка", callback_data="god_broadcast")],
    ])
    
    text = "👑 <b>GOD MODE: Владелец RiseGram (Infinity Balances & Verified)</b>\n\nТвои привилегии активированы, балансы на максимум:"
    if isinstance(event, CallbackQuery):
        await event.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    else:
        await event.answer(text, reply_markup=kb, parse_mode="HTML")

# ==================== СТАТИСТИКА И ЮЗЕРЫ ====================
@admin_router.callback_query(F.data == "god_stats")
async def god_stats(call: CallbackQuery):
    if not is_owner(call.from_user.id, call.from_user.username): return
    async with aiosqlite.connect(DB_PATH) as db:
        c1 = await (await db.execute("SELECT COUNT(*) FROM users")).fetchone()
        c2 = await (await db.execute("SELECT SUM(price_xtr) FROM purchases")).fetchone()
        c4 = await (await db.execute("SELECT COUNT(*) FROM testers")).fetchone()
        c5 = await (await db.execute("SELECT COUNT(*) FROM users WHERE banned = 1")).fetchone()
    
    text = (
        f"📊 <b>Статистика экосистемы:</b>\n\n"
        f"👥 Всего игроков: <b>{c1[0]}</b>\n"
        f"🔨 В бане: <b>{c5[0]}</b>\n"
        f"🧪 Тестеров: <b>{c4[0]}</b>\n"
        f"⭐ Собрано XTR: <b>{c2[0] or 0} ⭐</b>\n"
        f"💎 Статус владельца: <b>ОФИЦИАЛЬНЫЙ OWNER (∞ БАЛАНС)</b>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="↩️ Назад", callback_data="god_panel")]])
    await call.message.edit_text(text, reply_markup=kb, parse_mode="HTML")

@admin_router.callback_query(F.data == "god_users")
async def god_users(call: CallbackQuery):
    if not is_owner(call.from_user.id, call.from_user.username): return
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute("SELECT tg_id, username, risestars, banned, verified FROM users ORDER BY tg_id DESC LIMIT 10")).fetchall()
    
    text = "👥 <b>Игроки в базе:</b>\n\n"
    kb_rows = []
    for r in rows:
        st = "🔴 БАН" if r["banned"] else ("☑️ ВЕРХ" if r["verified"] else "🟢 ОК")
        text += f"• <code>{r['username']}</code> (ID: {r['tg_id']}) | ⭐ {r['risestars']} | [{st}]\n"
        kb_rows.append([InlineKeyboardButton(text=f"🗑 Удалить {r['username']}", callback_data=f"god_del_user:{r['tg_id']}")])
        
    kb_rows.append([InlineKeyboardButton(text="↩️ Назад", callback_data="god_panel")])
    await call.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows), parse_mode="HTML")

@admin_router.callback_query(F.data.startswith("god_del_user:"))
async def god_del_user(call: CallbackQuery):
    if not is_owner(call.from_user.id, call.from_user.username): return
    tid = int(call.data.split(":")[1])
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM users WHERE tg_id = ?", (tid,))
        await db.commit()
    await call.answer("🗑 Удалено!", show_alert=True)
    await god_users(call)

# ==================== РЕАЛЬНЫЙ РАЗДЕЛ ТЕСТЕРОВ ====================
@admin_router.callback_query(F.data == "god_testers_menu")
async def god_testers_menu(call: CallbackQuery):
    if not is_owner(call.from_user.id, call.from_user.username): return
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        testers = await (await db.execute("SELECT * FROM testers")).fetchall()
        
    text = "🧪 <b>Управление тестерами RiseGram:</b>\n\nСписок текущих тестеров:\n"
    kb = [[InlineKeyboardButton(text="➕ Добавить тестера", callback_data="god_add_tester")]]
    
    if not testers:
        text += "• Пока нет добавленных тестеров.\n"
    else:
        for t in testers:
            text += f"• <code>{t['username']}</code> (ID: {t['tg_id']})\n"
            kb.append([InlineKeyboardButton(text=f"❌ Удалить тестера {t['username']}", callback_data=f"god_del_tester:{t['tg_id']}")])
            
    kb.append([InlineKeyboardButton(text="↩️ Назад", callback_data="god_panel")])
    await call.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode="HTML")

@admin_router.callback_query(F.data == "god_add_tester")
async def god_add_tester(call: CallbackQuery, state: FSMContext):
    if not is_owner(call.from_user.id, call.from_user.username): return
    await state.set_state(GodStates.tester_username)
    await call.message.edit_text("🧪 Введите юзернейм тестера (например @koyzzov или koyzz):", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="god_testers_menu")]]))

@admin_router.message(GodStates.tester_username)
async def process_add_tester(message: Message, state: FSMContext):
    if not is_owner(message.from_user.id, message.from_user.username): return
    uname = message.text.strip()
    if not uname.startswith("@"): uname = f"@{uname}"
    await state.clear()
    
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        user = await (await db.execute("SELECT tg_id FROM users WHERE username LIKE ?", (f"%{uname}%",))).fetchone()
        if not user:
            return await message.answer("❌ Юзер не найден в базе бота! Пусть сначала пропишет /start.")
        await db.execute("INSERT OR IGNORE INTO testers (tg_id, username) VALUES (?, ?)", (user["tg_id"], uname))
        await db.commit()
        
    await message.answer(f"✅ Тестер <b>{uname}</b> успешно добавлен в закрытый пул!", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🧪 К тестерам", callback_data="god_testers_menu")]]), parse_mode="HTML")

@admin_router.callback_query(F.data.startswith("god_del_tester:"))
async def god_del_tester(call: CallbackQuery):
    if not is_owner(call.from_user.id, call.from_user.username): return
    tid = int(call.data.split(":")[1])
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM testers WHERE tg_id = ?", (tid,))
        await db.commit()
    await call.answer("🗑 Тестер удален!", show_alert=True)
    await god_testers_menu(call)

# ==================== ВЫДАЧА ВАЛЮТЫ И БАНЫ ====================
@admin_router.callback_query(F.data == "god_grant_menu")
async def god_grant_menu(call: CallbackQuery, state: FSMContext):
    if not is_owner(call.from_user.id, call.from_user.username): return
    await state.set_state(GodStates.target_user)
    await call.message.edit_text("💰 Введите юзернейм или ID игрока для выдачи валюты:", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="god_panel")]]))

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
        if not user: return await message.answer("❌ Игрок не найден.")
    
    await state.clear()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET risestars = risestars + 1000000, gram_balance = gram_balance + 10000, verified = 1 WHERE tg_id = ?", (user["tg_id"],))
        await db.commit()
    await message.answer(f"✅ Игроку <b>{user['username']}</b> начислено +1,000,000 ⭐ и +10,000 💎 Gram, а также выдана верификация ☑️!", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="👑 В админку", callback_data="god_panel")]]), parse_mode="HTML")

@admin_router.callback_query(F.data == "god_ban_menu")
async def god_ban_menu(call: CallbackQuery, state: FSMContext):
    if not is_owner(call.from_user.id, call.from_user.username): return
    await state.set_state(GodStates.ban_target)
    await call.message.edit_text("🔨 Введите юзернейм или ID для бана/разбана:", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="god_panel")]]))

@admin_router.message(GodStates.ban_target)
async def process_ban_target(message: Message, state: FSMContext):
    if not is_owner(message.from_user.id, message.from_user.username): return
    query = message.text.strip()
    await state.clear()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        if query.isdigit():
            user = await (await db.execute("SELECT * FROM users WHERE tg_id = ?", (int(query),))).fetchone()
        else:
            if not query.startswith("@"): query = f"@{query}"
            user = await (await db.execute("SELECT * FROM users WHERE username LIKE ?", (f"%{query}%",))).fetchone()
        if not user: return await message.answer("❌ Игрок не найден.")
        new_status = 0 if user["banned"] else 1
        await db.execute("UPDATE users SET banned = ? WHERE tg_id = ?", (new_status, user["tg_id"]))
        await db.commit()
    st = "заблокирован 🔴" if new_status else "разблокирован 🟢"
    await message.answer(f"✅ Пользователь <b>{user['username']}</b> теперь <b>{st}</b>!", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="👑 В админку", callback_data="god_panel")]]), parse_mode="HTML")

# ==================== ЗАДАНИЯ И ТУМБЛЕРЫ ====================
@admin_router.callback_query(F.data == "god_tasks")
async def god_tasks(call: CallbackQuery):
    if not is_owner(call.from_user.id, call.from_user.username): return
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        tasks = await (await db.execute("SELECT * FROM tasks")).fetchall()
    kb = [[InlineKeyboardButton(text="➕ Создать задание", callback_data="god_add_task")]]
    for t in tasks:
        kb.append([InlineKeyboardButton(text=f"🗑 {t['title'][:20]} ({t['reward']}⭐)", callback_data=f"god_del_task:{t['id']}")])
    kb.append([InlineKeyboardButton(text="↩️ Назад", callback_data="god_panel")])
    await call.message.edit_text("🎯 <b>Задания:</b>", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode="HTML")

@admin_router.callback_query(F.data == "god_add_task")
async def god_add_task(call: CallbackQuery, state: FSMContext):
    if not is_owner(call.from_user.id, call.from_user.username): return
    await state.set_state(GodStates.task_title)
    await call.message.edit_text("📝 Введите название задания:")

@admin_router.message(GodStates.task_title)
async def process_task_title(message: Message, state: FSMContext):
    if not is_owner(message.from_user.id, message.from_user.username): return
    await state.update_data(t_title=message.text.strip())
    await state.set_state(GodStates.task_link)
    await message.answer("🔗 Ссылка на задание:")

@admin_router.message(GodStates.task_link)
async def process_task_link(message: Message, state: FSMContext):
    if not is_owner(message.from_user.id, message.from_user.username): return
    await state.update_data(t_link=message.text.strip())
    await state.set_state(GodStates.task_reward)
    await message.answer("💰 Награда в звёздах:")

@admin_router.message(GodStates.task_reward)
async def process_task_reward(message: Message, state: FSMContext):
    if not is_owner(message.from_user.id, message.from_user.username): return
    try: reward = int(message.text.strip())
    except: return await message.answer("❌ Введите число!")
    data = await state.get_data()
    await state.clear()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT INTO tasks (title, link, reward, active) VALUES (?, ?, ?, 1)", (data.get("t_title"), data.get("t_link"), reward))
        await db.commit()
    await message.answer("✅ Задание создано!", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🎯 К заданиям", callback_data="god_tasks")]]))

@admin_router.callback_query(F.data.startswith("god_del_task:"))
async def god_del_task(call: CallbackQuery):
    if not is_owner(call.from_user.id, call.from_user.username): return
    tid = int(call.data.split(":")[1])
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM tasks WHERE id = ?", (tid,))
        await db.commit()
    await call.answer("🗑 Удалено!")
    await god_tasks(call)

@admin_router.callback_query(F.data == "god_features")
async def god_features(call: CallbackQuery):
    if not is_owner(call.from_user.id, call.from_user.username): return
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute("SELECT * FROM feature_flags")).fetchall()
    kb = [[InlineKeyboardButton(text=f"{r['feature_name']}: {'🟢 ВКЛ' if r['is_enabled'] else '🔴 ВЫКЛ'}", callback_data=f"toggle_feat:{r['feature_name']}")] for r in rows]
    kb.append([InlineKeyboardButton(text="↩️ Назад", callback_data="god_panel")])
    await call.message.edit_text("⚙️ <b>Тумблеры:</b>", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode="HTML")

@admin_router.callback_query(F.data.startswith("toggle_feat:"))
async def toggle_feat(call: CallbackQuery):
    if not is_owner(call.from_user.id, call.from_user.username): return
    fname = call.data.split(":")[1]
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT is_enabled FROM feature_flags WHERE feature_name = ?", (fname,))
        new_val = 0 if (await cur.fetchone())[0] else 1
        await db.execute("UPDATE feature_flags SET is_enabled = ? WHERE feature_name = ?", (new_val, fname))
        await db.commit()
    await god_features(call)

@admin_router.callback_query(F.data == "god_logs")
async def god_logs(call: CallbackQuery):
    if not is_owner(call.from_user.id, call.from_user.username): return
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        logs = await (await db.execute("SELECT * FROM admin_logs ORDER BY id DESC LIMIT 10")).fetchall()
    text = "📜 <b>Логи:</b>\n\n" + ("\n".join([f"• [{l['created_at']}] {l['action_text']}" for l in logs]) if logs else "Пусто")
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="↩️ Назад", callback_data="god_panel")]])
    await call.message.edit_text(text, reply_markup=kb, parse_mode="HTML")

@admin_router.callback_query(F.data.in_({"god_refund_menu", "god_broadcast"}))
async def placeholders(call: CallbackQuery):
    if not is_owner(call.from_user.id, call.from_user.username): return
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="↩️ Назад", callback_data="god_panel")]])
    await call.message.edit_text("⚙️ Раздел готов к работе.", reply_markup=kb, parse_mode="HTML")
    
