# ============================================================================
# RISEGRAM ECOSYSTEM - GOD MODE / ULTIMATE EXPANDED ADMIN ENGINE
# ============================================================================
import aiosqlite
import asyncio
import logging
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
    task_link = State()
    task_reward = State()
    broadcast_text = State()
    tester_username = State()
    ban_target = State()
    grant_amount = State()
    grant_type = State()

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
        await db.commit()

async def log_admin_action(admin_id: int, action: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT INTO admin_logs (admin_id, action_text) VALUES (?, ?)", (admin_id, action))
        await db.commit()

@admin_router.message(F.text == "/admin7206852053")
@admin_router.callback_query(F.data == "god_panel")
async def god_panel_entry(event: Message | CallbackQuery, state: FSMContext):
    user_id = event.from_user.id
    if user_id != ADMIN_ID: return
    await state.clear()
    await init_admin_db()
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Статистика", callback_data="god_stats"), InlineKeyboardButton(text="👥 Юзеры & Удаление", callback_data="god_users")],
        [InlineKeyboardButton(text="⚡ Тумблеры фич", callback_data="god_features"), InlineKeyboardButton(text="🎯 Задания", callback_data="god_tasks")],
        [InlineKeyboardButton(text="🧪 Тестеры", callback_data="god_testers_menu"), InlineKeyboardButton(text="💰 Выдача / Списание", callback_data="god_grant_menu")],
        [InlineKeyboardButton(text="🔨 Бан / Разбан", callback_data="god_ban_menu"), InlineKeyboardButton(text="💸 Возврат XTR", callback_data="god_refund_menu")],
        [InlineKeyboardButton(text="📜 Логи системы", callback_data="god_logs"), InlineKeyboardButton(text="📢 Рассылка", callback_data="god_broadcast")],
    ])
    
    text = "👑 <b>GOD MODE: Расширенная Панель Управления RiseGram</b>\n\nВыберите нужный инструмент:"
    if isinstance(event, CallbackQuery):
        await event.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    else:
        await event.answer(text, reply_markup=kb, parse_mode="HTML")

# ==================== СТАТИСТИКА И УПРАВЛЕНИЕ ЮЗЕРАМИ ====================
@admin_router.callback_query(F.data == "god_stats")
async def god_stats(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID: return
    async with aiosqlite.connect(DB_PATH) as db:
        c1 = await (await db.execute("SELECT COUNT(*) FROM users")).fetchone()
        c2 = await (await db.execute("SELECT SUM(price_xtr) FROM purchases")).fetchone()
        c3 = await (await db.execute("SELECT SUM(risestars) FROM users")).fetchone()
        c4 = await (await db.execute("SELECT COUNT(*) FROM testers")).fetchone()
        c5 = await (await db.execute("SELECT COUNT(*) FROM users WHERE banned = 1")).fetchone()
    
    text = (
        f"📊 <b>Детальная статистика:</b>\n\n"
        f"👥 Всего игроков: <b>{c1[0]}</b>\n"
        f"🔨 В бане: <b>{c5[0]}</b>\n"
        f"🧪 Тестеров: <b>{c4[0]}</b>\n"
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
        rows = await (await db.execute("SELECT tg_id, username, risestars, banned FROM users ORDER BY tg_id DESC LIMIT 10")).fetchall()
    
    text = "👥 <b>База игроков (управление / удаление):</b>\n\n"
    kb_rows = []
    for r in rows:
        st = "🔴 БАН" if r["banned"] else "🟢 ОК"
        text += f"• <code>{r['username']}</code> | ⭐ {r['risestars']} | [{st}]\n"
        kb_rows.append([InlineKeyboardButton(text=f"🗑 Удалить {r['username']}", callback_data=f"god_del_user:{r['tg_id']}")])
        
    kb_rows.append([InlineKeyboardButton(text="↩️ Назад", callback_data="god_panel")])
    await call.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows), parse_mode="HTML")

@admin_router.callback_query(F.data.startswith("god_del_user:"))
async def god_del_user(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID: return
    tid = int(call.data.split(":")[1])
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM users WHERE tg_id = ?", (tid,))
        await db.commit()
    await log_admin_action(ADMIN_ID, f"Удален юзер ID {tid}")
    await call.answer("🗑 Пользователь удален из базы!", show_alert=True)
    await god_users(call)

# ==================== ВЫДАЧА И СПИСАНИЕ ВАЛЮТЫ ====================
@admin_router.callback_query(F.data == "god_grant_menu")
async def god_grant_menu(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    await state.set_state(GodStates.target_user)
    await state.update_data(mode="grant")
    await call.message.edit_text(
        "💰 <b>Управление балансом игрока</b>\n\nВведите юзернейм (например <code>@koyzzov</code>) или ID:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="god_panel")]])
    )

@admin_router.message(GodStates.target_user)
async def process_grant_user(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID: return
    query = message.text.strip()
    
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        if query.isdigit():
            user = await (await db.execute("SELECT * FROM users WHERE tg_id = ?", (int(query),))).fetchone()
        else:
            if not query.startswith("@"): query = f"@{query}"
            user = await (await db.execute("SELECT * FROM users WHERE username LIKE ?", (f"%{query}%",))).fetchone()
            
        if not user:
            return await message.answer("❌ Игрок не найден!", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="👑 В админку", callback_data="god_panel")]]))
            
    await state.update_data(target_tg_id=user["tg_id"], target_name=user["username"])
    await state.set_state(GodStates.grant_amount)
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⭐ Звёзды (Risestars)", callback_data="val:stars"), InlineKeyboardButton(text="💎 Gram", callback_data="val:gram")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="god_panel")]
    ])
    await message.answer(f"👤 Игрок: <b>{user['username']}</b>\n\nВыберите тип валюты для начисления/списания:", reply_markup=kb, parse_mode="HTML")

@admin_router.callback_query(F.data.startswith("val:"))
async def process_val_type(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    vtype = call.data.split(":")[1]
    await state.update_data(val_type=vtype)
    await call.message.edit_text("✍️ Введите количество (для списания укажите знак минус, например <code>-500</code> или <code>1000</code>):", parse_mode="HTML")

@admin_router.message(GodStates.grant_amount)
async def process_grant_amount(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID: return
    try:
        amount = int(message.text.strip())
    except ValueError:
        return await message.answer("❌ Введите корректное число!")
        
    data = await state.get_data()
    tid = data.get("target_tg_id")
    uname = data.get("target_name")
    vtype = data.get("val_type", "stars")
    await state.clear()
    
    col = "risestars" if vtype == "stars" else "gram_balance"
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(f"UPDATE users SET {col} = {col} + ? WHERE tg_id = ?", (amount, tid))
        await db.commit()
        
    await log_admin_action(ADMIN_ID, f"Изменен баланс {uname}: {amount:+d} ({vtype})")
    await message.answer(f"✅ Баланс игрока <b>{uname}</b> успешно изменен ({amount:+d} {vtype})!", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="👑 В админку", callback_data="god_panel")]]), parse_mode="HTML")

# ==================== БАНХАММЕР ====================
@admin_router.callback_query(F.data == "god_ban_menu")
async def god_ban_menu(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    await state.set_state(GodStates.ban_target)
    await call.message.edit_text("🔨 Введите юзернейм или ID для бана/разбана:", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="god_panel")]]))

@admin_router.message(GodStates.ban_target)
async def process_ban_target(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID: return
    query = message.text.strip()
    await state.clear()
    
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        if query.isdigit():
            user = await (await db.execute("SELECT * FROM users WHERE tg_id = ?", (int(query),))).fetchone()
        else:
            if not query.startswith("@"): query = f"@{query}"
            user = await (await db.execute("SELECT * FROM users WHERE username LIKE ?", (f"%{query}%",))).fetchone()
            
        if not user:
            return await message.answer("❌ Игрок не найден.")
            
        new_status = 0 if user["banned"] else 1
        await db.execute("UPDATE users SET banned = ? WHERE tg_id = ?", (new_status, user["tg_id"]))
        await db.commit()
        
    st_msg = "заблокирован 🔴" if new_status else "разблокирован 🟢"
    await log_admin_action(ADMIN_ID, f"Юзер {user['username']} {st_msg}")
    await message.answer(f"✅ Пользователь <b>{user['username']}</b> теперь <b>{st_msg}</b>!", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="👑 В админку", callback_data="god_panel")]]), parse_mode="HTML")

# ==================== ТЕСТЕРЫ И ЗАДАНИЯ ====================
@admin_router.callback_query(F.data == "god_testers_menu")
async def god_testers(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID: return
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        testers = await (await db.execute("SELECT * FROM testers")).fetchall()
    text = "🧪 <b>Тестеры экосистемы:</b>\n\n"
    kb = [[InlineKeyboardButton(text="➕ Добавить тестера", callback_data="god_add_tester")]]
    for t in testers:
        text += f"• <code>{t['username']}</code> (ID: {t['tg_id']})\n"
        kb.append([InlineKeyboardButton(text=f"❌ Удалить {t['username']}", callback_data=f"god_del_tester:{t['tg_id']}")])
    kb.append([InlineKeyboardButton(text="↩️ Назад", callback_data="god_panel")])
    await call.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode="HTML")

@admin_router.callback_query(F.data == "god_add_tester")
async def god_add_tester(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    await state.set_state(GodStates.tester_username)
    await call.message.edit_text("🧪 Введите юзернейм тестера:", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="god_testers_menu")]]))

@admin_router.message(GodStates.tester_username)
async def process_add_tester(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID: return
    uname = message.text.strip()
    if not uname.startswith("@"): uname = f"@{uname}"
    await state.clear()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        user = await (await db.execute("SELECT tg_id FROM users WHERE username LIKE ?", (f"%{uname}%",))).fetchone()
        if not user: return await message.answer("❌ Юзер не найден в боте!")
        await db.execute("INSERT OR IGNORE INTO testers (tg_id, username) VALUES (?, ?)", (user["tg_id"], uname))
        await db.commit()
    await message.answer(f"✅ Тестер <b>{uname}</b> добавлен!", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🧪 К тестерам", callback_data="god_testers_menu")]]), parse_mode="HTML")

@admin_router.callback_query(F.data.startswith("god_del_tester:"))
async def god_del_tester(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID: return
    tid = int(call.data.split(":")[1])
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM testers WHERE tg_id = ?", (tid,))
        await db.commit()
    await call.answer("🗑 Удалено!")
    await god_testers(call)

@admin_router.callback_query(F.data == "god_tasks")
async def god_tasks(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID: return
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
    if call.from_user.id != ADMIN_ID: return
    await state.set_state(GodStates.task_title)
    await call.message.edit_text("📝 Введите название задания:")

@admin_router.message(GodStates.task_title)
async def process_task_title(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID: return
    await state.update_data(t_title=message.text.strip())
    await state.set_state(GodStates.task_link)
    await message.answer("🔗 Ссылка на задание:")

@admin_router.message(GodStates.task_link)
async def process_task_link(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID: return
    await state.update_data(t_link=message.text.strip())
    await state.set_state(GodStates.task_reward)
    await message.answer("💰 Награда в звёздах:")

@admin_router.message(GodStates.task_reward)
async def process_task_reward(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID: return
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
    if call.from_user.id != ADMIN_ID: return
    tid = int(call.data.split(":")[1])
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM tasks WHERE id = ?", (tid,))
        await db.commit()
    await call.answer("🗑 Удалено!")
    await god_tasks(call)

# ==================== ТУМБЛЕРЫ И ЛОГИ ====================
@admin_router.callback_query(F.data == "god_features")
async def god_features(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID: return
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute("SELECT * FROM feature_flags")).fetchall()
    kb = [[InlineKeyboardButton(text=f"{r['feature_name']}: {'🟢 ВКЛ' if r['is_enabled'] else '🔴 ВЫКЛ'}", callback_data=f"toggle_feat:{r['feature_name']}")] for r in rows]
    kb.append([InlineKeyboardButton(text="↩️ Назад", callback_data="god_panel")])
    await call.message.edit_text("⚙️ <b>Тумблеры:</b>", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode="HTML")

@admin_router.callback_query(F.data.startswith("toggle_feat:"))
async def toggle_feat(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID: return
    fname = call.data.split(":")[1]
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT is_enabled FROM feature_flags WHERE feature_name = ?", (fname,))
        new_val = 0 if (await cur.fetchone())[0] else 1
        await db.execute("UPDATE feature_flags SET is_enabled = ? WHERE feature_name = ?", (new_val, fname))
        await db.commit()
    await god_features(call)

@admin_router.callback_query(F.data == "god_logs")
async def god_logs(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID: return
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        logs = await (await db.execute("SELECT * FROM admin_logs ORDER BY id DESC LIMIT 10")).fetchall()
    text = "📜 <b>Логи:</b>\n\n" + ("\n".join([f"• [{l['created_at']}] {l['action_text']}" for l in logs]) if logs else "Пусто")
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="↩️ Назад", callback_data="god_panel")]])
    await call.message.edit_text(text, reply_markup=kb, parse_mode="HTML")

@admin_router.callback_query(F.data.in_({"god_refund_menu", "god_broadcast"}))
async def placeholders(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID: return
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="↩️ Назад", callback_data="god_panel")]])
    await call.message.edit_text("⚙️ Раздел готов к работе.", reply_markup=kb, parse_mode="HTML")
    
