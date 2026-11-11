# ============================================================================
# RISEGRAM ECOSYSTEM - GOD MODE / ADVANCED ADMIN ENGINE (GOD LEVEL)
# ============================================================================
import aiosqlite
from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

ADMIN_ID = 7206852053
DB_PATH = "risegram.db"

admin_router = Router()

class GodStates(StatesGroup):
    target_user = State()
    custom_value = State()
    task_title = State()
    task_reward = State()
    broadcast_text = State()
    tester_username = State()
    ban_reason = State()

# Инициализация дополнительных таблиц для админки (тестеры, логи)
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
        await db.commit()

async def log_admin_action(admin_id: int, action: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT INTO admin_logs (admin_id, action_text) VALUES (?, ?)", (admin_id, action))
        await db.commit()

@admin_router.message(F.text == "/admin7206852053")
@admin_router.callback_query(F.data == "god_panel")
async def god_panel_entry(event: Message | CallbackQuery, state: FSMContext):
    user_id = event.from_user.id
    if user_id != ADMIN_ID:
        return
    await state.clear()
    await init_admin_db()
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Статистика", callback_data="god_stats"), InlineKeyboardButton(text="👥 Все юзеры", callback_data="god_users")],
        [InlineKeyboardButton(text="⚡ Тумблеры фич", callback_data="god_features"), InlineKeyboardButton(text="🎯 Задания", callback_data="god_tasks")],
        [InlineKeyboardButton(text="🧪 Управление тестерами", callback_data="god_testers_menu")],
        [InlineKeyboardButton(text="💰 Выдать / Снять валюту", callback_data="god_grant_menu")],
        [InlineKeyboardButton(text="🔨 Бан / Разбан игрока", callback_data="god_ban_menu")],
        [InlineKeyboardButton(text="💸 Возврат Звёзд (Refund)", callback_data="god_refund_menu")],
        [InlineKeyboardButton(text="📜 Логи админки", callback_data="god_logs")],
        [InlineKeyboardButton(text="📢 Рассылка", callback_data="god_broadcast")],
    ])
    
    text = "👑 <b>GOD MODE: Абсолютная Панель Управления RiseGram</b>\n\nВыбери нужный инструмент:"
    if isinstance(event, CallbackQuery):
        await event.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    else:
        await event.answer(text, reply_markup=kb, parse_mode="HTML")

# --- СТАТИСТИКА И ЮЗЕРЫ ---
@admin_router.callback_query(F.data == "god_stats")
async def god_stats(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID: return
    async with aiosqlite.connect(DB_PATH) as db:
        c1 = await (await db.execute("SELECT COUNT(*) FROM users")).fetchone()
        c2 = await (await db.execute("SELECT SUM(price_xtr) FROM purchases")).fetchone()
        c3 = await (await db.execute("SELECT SUM(risestars) FROM users")).fetchone()
        c4 = await (await db.execute("SELECT COUNT(*) FROM testers")).fetchone()
    
    text = (
        f"📊 <b>Статистика системы:</b>\n\n"
        f"👥 Игроков в базе: <b>{c1[0]}</b>\n"
        f"🧪 Активных тестеров: <b>{c4[0]}</b>\n"
        f"⭐ Собрано XTR: <b>{c2[0] or 0} ⭐</b>\n"
        f"💎 Звёзд на руках: <b>{c3[0] or 0} ⭐</b>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="↩️ Назад", callback_data="god_panel")]])
    await call.message.edit_text(text, reply_markup=kb, parse_mode="HTML")

@admin_router.callback_query(F.data == "god_users")
async def god_users(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID: return
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute("SELECT username, risestars, banned FROM users ORDER BY tg_id DESC LIMIT 15")).fetchall()
    
    text = "👥 <b>Последние 15 игроков:</b>\n\n"
    for r in rows:
        status = "🔴 БАН" if r["banned"] else "🟢 ОК"
        text += f"• <code>{r['username']}</code> | ⭐ {r['risestars']} | [{status}]\n"
        
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="↩️ Назад", callback_data="god_panel")]])
    await call.message.edit_text(text, reply_markup=kb, parse_mode="HTML")

# --- УПРАВЛЕНИЕ ТЕСТЕРАМИ (НОВАЯ ФИЧА) ---
@admin_router.callback_query(F.data == "god_testers_menu")
async def god_testers_menu(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID: return
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        testers = await (await db.execute("SELECT * FROM testers")).fetchall()
        
    text = "🧪 <b>Список закрытых тестеров RiseGram:</b>\n\n"
    kb_rows = [[InlineKeyboardButton(text="➕ Добавить тестера", callback_data="god_add_tester")]]
    
    if not testers:
        text += "Тестеров пока нет."
    else:
        for t in testers:
            text += f"• <code>{t['username']}</code> (ID: {t['tg_id']})\n"
            kb_rows.append([InlineKeyboardButton(text=f"❌ Удалить тестера {t['username']}", callback_data=f"god_del_tester:{t['tg_id']}")])
            
    kb_rows.append([InlineKeyboardButton(text="↩️ Назад", callback_data="god_panel")])
    await call.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows), parse_mode="HTML")

@admin_router.callback_query(F.data == "god_add_tester")
async def god_add_tester(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    await state.set_state(GodStates.tester_username)
    await call.message.edit_text("🧪 Введите <b>юзернейм тестера</b> (например @koyzzov):", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="god_testers_menu")]]))

@admin_router.message(GodStates.tester_username)
async def process_add_tester(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID: return
    uname = message.text.strip()
    if not uname.startswith("@"): uname = f"@{uname}"
    await state.clear()
    
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        user = await (await db.execute("SELECT tg_id FROM users WHERE username LIKE ?", (f"%{uname}%",))).fetchone()
        if not user:
            return await message.answer(f"❌ Пользователь {uname} не найден в базе бота! Сначала пусть запустит /start.")
        
        await db.execute("INSERT OR IGNORE INTO testers (tg_id, username) VALUES (?, ?)", (user["tg_id"], uname))
        await db.commit()
        
    await log_admin_action(ADMIN_ID, f"Добавлен тестер: {uname}")
    await message.answer(f"✅ Тестер <b>{uname}</b> успешно добавлен в закрытый пул!", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🧪 К тестерам", callback_data="god_testers_menu")]]), parse_mode="HTML")

@admin_router.callback_query(F.data.startswith("god_del_tester:"))
async def god_del_tester(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID: return
    tid = int(call.data.split(":")[1])
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM testers WHERE tg_id = ?", (tid,))
        await db.commit()
    await call.answer("🗑 Тестер удален!", show_alert=True)
    await god_testers_menu(call)

# --- БАНХАММЕР ИГРОКОВ ---
@admin_router.callback_query(F.data == "god_ban_menu")
async def god_ban_menu(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    await state.set_state(GodStates.target_user)
    await state.update_data(action="toggle_ban")
    await call.message.edit_text("🔨 <b>Управление блокировкой</b>\n\nВведите юзернейм или ID игрока для бана/разбана:", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="god_panel")]]))

# --- ЛОГИ АДМИНОВ ---
@admin_router.callback_query(F.data == "god_logs")
async def god_logs(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID: return
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        logs = await (await db.execute("SELECT * FROM admin_logs ORDER BY id DESC LIMIT 10")).fetchall()
        
    text = "📜 <b>Последние действия администраторов:</b>\n\n"
    if not logs:
        text += "Логов пока нет."
    else:
        for l in logs:
            text += f"• [{l['created_at']}] ID {l['admin_id']}: <b>{l['action_text']}</b>\n"
            
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="↩️ Назад", callback_data="god_panel")]])
    await call.message.edit_text(text, reply_markup=kb, parse_mode="HTML")

# --- ТУМБЛЕРЫ ФИЧ ---
@admin_router.callback_query(F.data == "god_features")
async def god_features(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID: return
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute("SELECT * FROM feature_flags")).fetchall()
    
    kb_rows = []
    for r in rows:
        status = "🟢 ВКЛ" if r["is_enabled"] else "🔴 ВЫКЛ"
        kb_rows.append([InlineKeyboardButton(text=f"{r['feature_name']}: {status}", callback_data=f"toggle_feat:{r['feature_name']}")])
    kb_rows.append([InlineKeyboardButton(text="↩️ Назад", callback_data="god_panel")])
    
    await call.message.edit_text("⚙️ <b>Тумблеры функций:</b>", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows), parse_mode="HTML")

@admin_router.callback_query(F.data.startswith("toggle_feat:"))
async def toggle_feat(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID: return
    fname = call.data.split(":")[1]
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT is_enabled FROM feature_flags WHERE feature_name = ?", (fname,))
        val = (await cur.fetchone())[0]
        new_val = 0 if val else 1
        await db.execute("UPDATE feature_flags SET is_enabled = ? WHERE feature_name = ?", (new_val, fname))
        await db.commit()
    await log_admin_action(ADMIN_ID, f"Переключен тумблер {fname} в {new_val}")
    await god_features(call)

# --- УПРАВЛЕНИЕ ЗАДАНИЯМИ ---
@admin_router.callback_query(F.data == "god_tasks")
async def god_tasks(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID: return
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        tasks = await (await db.execute("SELECT * FROM tasks")).fetchall()
    
    kb = [[InlineKeyboardButton(text="➕ Создать задание", callback_data="god_add_task")]]
    for t in tasks:
        kb.append([InlineKeyboardButton(text=f"🗑 Удалить: {t['title'][:20]}", callback_data=f"god_del_task:{t['id']}")])
    kb.append([InlineKeyboardButton(text="↩️ Назад", callback_data="god_panel")])
    
    await call.message.edit_text("🎯 <b>Динамические задания:</b>", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode="HTML")

@admin_router.callback_query(F.data == "god_add_task")
async def god_add_task(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    await state.set_state(GodStates.task_title)
    await call.message.edit_text("📝 Введите текст нового задания:", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="god_tasks")]]))

@admin_router.message(GodStates.task_title)
async def process_task_title(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID: return
    await state.update_data(t_title=message.text.strip())
    await state.set_state(GodStates.task_reward)
    await message.answer("💰 Введите награду в звёздах:")

@admin_router.message(GodStates.task_reward)
async def process_task_reward(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID: return
    try:
        reward = int(message.text.strip())
    except ValueError:
        return await message.answer("❌ Введите число!")
    
    data = await state.get_data()
    title = data.get("t_title")
    await state.clear()
    
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT INTO tasks (title, reward, active) VALUES (?, ?, 1)", (title, reward))
        await db.commit()
    
    await log_admin_action(ADMIN_ID, f"Создано задание: {title} ({reward}⭐)")
    await message.answer(f"✅ Задание создано!", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🎯 К заданиям", callback_data="god_tasks")]]))

@admin_router.callback_query(F.data.startswith("god_del_task:"))
async def god_del_task(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID: return
    tid = int(call.data.split(":")[1])
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM tasks WHERE id = ?", (tid,))
        await db.commit()
    await log_admin_action(ADMIN_ID, f"Удалено задание ID {tid}")
    await call.answer("🗑 Удалено!")
    await god_tasks(call)

# --- ВОЗВРАТ ЗВЕЗД ---
@admin_router.callback_query(F.data == "god_refund_menu")
async def god_refund_menu(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    await state.set_state(GodStates.target_user)
    await state.update_data(action="refund_xtr")
    await call.message.edit_text("💸 <b>Возврат Telegram Stars (XTR)</b>\n\nВведите Telegram ID юзера для возврата:", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="god_panel")]]))
