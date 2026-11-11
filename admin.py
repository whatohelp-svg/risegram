# ============================================================================
# RISEGRAM ECOSYSTEM - GOD MODE / ADVANCED ADMIN ENGINE (BAN & DELETE CORE)
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
    task_link = State()
    task_reward = State()
    broadcast_text = State()
    tester_username = State()
    ban_target = State()

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
        # Убедимся, что у юзеров есть колонка бана
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
        [InlineKeyboardButton(text="🧪 Тестеры", callback_data="god_testers_menu"), InlineKeyboardButton(text="💰 Выдать валюту", callback_data="god_grant_menu")],
        [InlineKeyboardButton(text="🔨 Бан / Разбан игрока", callback_data="god_ban_menu"), InlineKeyboardButton(text="💸 Возврат XTR", callback_data="god_refund_menu")],
        [InlineKeyboardButton(text="📜 Логи", callback_data="god_logs"), InlineKeyboardButton(text="📢 Рассылка", callback_data="god_broadcast")],
    ])
    
    text = "👑 <b>GOD MODE: Панель Управления RiseGram</b>"
    if isinstance(event, CallbackQuery):
        await event.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    else:
        await event.answer(text, reply_markup=kb, parse_mode="HTML")

# ================= УПРАВЛЕНИЕ ЮЗЕРАМИ И УДАЛЕНИЕ =================
@admin_router.callback_query(F.data == "god_users")
async def god_users(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID: return
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute("SELECT tg_id, username, risestars, banned FROM users ORDER BY tg_id DESC LIMIT 10")).fetchall()
    
    text = "👥 <b>Игроки в базе (управление / удаление):</b>\n\n"
    kb_rows = []
    
    for r in rows:
        status = "🔴 БАН" if r["banned"] else "🟢 ОК"
        text += f"• <code>{r['username']}</code> (ID: {r['tg_id']}) | ⭐ {r['risestars']} | [{status}]\n"
        kb_rows.append([
            InlineKeyboardButton(text=f"🗑 Удалить юзера {r['username']}", callback_data=f"god_del_user:{r['tg_id']}")
        ])
        
    kb_rows.append([InlineKeyboardButton(text="↩️ Назад", callback_data="god_panel")])
    await call.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows), parse_mode="HTML")

@admin_router.callback_query(F.data.startswith("god_del_user:"))
async def god_del_user(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID: return
    target_id = int(call.data.split(":")[1])
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM users WHERE tg_id = ?", (target_id,))
        await db.commit()
    await log_admin_action(ADMIN_ID, f"Удален пользователь с TG ID {target_id}")
    await call.answer("🗑 Пользователь полностью стерт из базы!", show_alert=True)
    await god_users(call)

# ================= БАНХАММЕР (БАН / РАЗБАН) =================
@admin_router.callback_query(F.data == "god_ban_menu")
async def god_ban_menu(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    await state.set_state(GodStates.ban_target)
    await call.message.edit_text(
        "🔨 <b>Бан / Разбан игрока</b>\n\nВведите юзернейм (например <code>@koyzzov</code>) или TG ID игрока:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="god_panel")]])
    )

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
            return await message.answer(f"❌ Пользователь <b>{query}</b> не найден в базе!", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="👑 В админку", callback_data="god_panel")]]), parse_mode="HTML")
            
        new_ban_status = 0 if user["banned"] else 1
        await db.execute("UPDATE users SET banned = ? WHERE tg_id = ?", (new_ban_status, user["tg_id"]))
        await db.commit()
        
    status_text = "заблокирован 🔴" if new_ban_status else "разблокирован 🟢"
    await log_admin_action(ADMIN_ID, f"Юзер {user['username']} (ID {user['tg_id']}) теперь {status_text}")
    
    # Пытаемся уведомить юзера в ЛС
    try:
        msg = "❌ Ваш аккаунт заблокирован администратором в экосистеме RiseGram." if new_ban_status else "✅ Ваш аккаунт разблокирован администратором!"
        await message.bot.send_message(user["tg_id"], msg)
    except Exception:
        pass

    await message.answer(
        f"✅ Пользователь <b>{user['username']}</b> (ID: <code>{user['tg_id']}</code>) успешно <b>{status_text}</b>!",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="👑 В админку", callback_data="god_panel")]]),
        parse_mode="HTML"
    )

# ================= ЗАДАНИЯ =================
@admin_router.callback_query(F.data == "god_tasks")
async def god_tasks(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID: return
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        tasks = await (await db.execute("SELECT * FROM tasks")).fetchall()
    
    kb = [[InlineKeyboardButton(text="➕ Создать задание со ссылкой", callback_data="god_add_task")]]
    for t in tasks:
        kb.append([InlineKeyboardButton(text=f"🗑 Удалить: {t['title'][:20]} ({t['reward']}⭐)", callback_data=f"god_del_task:{t['id']}")])
    kb.append([InlineKeyboardButton(text="↩️ Назад", callback_data="god_panel")])
    
    await call.message.edit_text("🎯 <b>Динамические задания:</b>", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode="HTML")

@admin_router.callback_query(F.data == "god_add_task")
async def god_add_task(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    await state.set_state(GodStates.task_title)
    await call.message.edit_text("📝 Введите <b>название/описание</b> задания:", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="god_tasks")]]))

@admin_router.message(GodStates.task_title)
async def process_task_title(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID: return
    await state.update_data(t_title=message.text.strip())
    await state.set_state(GodStates.task_link)
    await message.answer("🔗 Введите <b>ссылку</b> на задание (например https://t.me/...):")

@admin_router.message(GodStates.task_link)
async def process_task_link(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID: return
    await state.update_data(t_link=message.text.strip())
    await state.set_state(GodStates.task_reward)
    await message.answer("💰 Введите <b>награду в звёздах</b> (число):")

@admin_router.message(GodStates.task_reward)
async def process_task_reward(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID: return
    try:
        reward = int(message.text.strip())
        if reward <= 0: raise ValueError()
    except ValueError:
        return await message.answer("❌ Введите число!")
    
    data = await state.get_data()
    title = data.get("t_title")
    link = data.get("t_link")
    await state.clear()
    
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT INTO tasks (title, link, reward, active) VALUES (?, ?, ?, 1)", (title, link, reward))
        await db.commit()
    
    await log_admin_action(ADMIN_ID, f"Создано задание: {title} ({reward}⭐)")
    await message.answer(f"✅ Задание <b>{title}</b> добавлено!", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🎯 К заданиям", callback_data="god_tasks")]]), parse_mode="HTML")

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

# Остальные стандартные вкладки админки
@admin_router.callback_query(F.data == "god_features")
async def god_features(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID: return
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute("SELECT * FROM feature_flags")).fetchall()
    kb_rows = [[InlineKeyboardButton(text=f"{r['feature_name']}: {'🟢 ВКЛ' if r['is_enabled'] else '🔴 ВЫКЛ'}", callback_data=f"toggle_feat:{r['feature_name']}")] for r in rows]
    kb_rows.append([InlineKeyboardButton(text="↩️ Назад", callback_data="god_panel")])
    await call.message.edit_text("⚙️ <b>Тумблеры функций:</b>", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows), parse_mode="HTML")

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

@admin_router.callback_query(F.data == "god_stats")
async def god_stats(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID: return
    async with aiosqlite.connect(DB_PATH) as db:
        c1 = await (await db.execute("SELECT COUNT(*) FROM users")).fetchone()
        c2 = await (await db.execute("SELECT SUM(price_xtr) FROM purchases")).fetchone()
    text = f"📊 <b>Статистика:</b>\n\n👥 Юзеров: <b>{c1[0]}</b>\n⭐ Собрано XTR: <b>{c2[0] or 0} ⭐</b>"
    await call.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="↩️ Назад", callback_data="god_panel")]]), parse_mode="HTML")

@admin_router.callback_query(F.data == "god_testers_menu")
async def god_testers(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID: return
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="↩️ Назад", callback_data="god_panel")]])
    await call.message.edit_text("🧪 Раздел тестеров в разработке / активен.", reply_markup=kb, parse_mode="HTML")

@admin_router.callback_query(F.data == "god_grant_menu")
async def god_grant(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID: return
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="↩️ Назад", callback_data="god_panel")]])
    await call.message.edit_text("💰 Меню выдачи валюты активно.", reply_markup=kb, parse_mode="HTML")

@admin_router.callback_query(F.data == "god_refund_menu")
async def god_refund(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID: return
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="↩️ Назад", callback_data="god_panel")]])
    await call.message.edit_text("💸 Меню возврата XTR активно.", reply_markup=kb, parse_mode="HTML")

@admin_router.callback_query(F.data == "god_logs")
async def god_logs(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID: return
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        logs = await (await db.execute("SELECT * FROM admin_logs ORDER BY id DESC LIMIT 10")).fetchall()
    text = "📜 <b>Логи админки:</b>\n\n" + ("\n".join([f"• [{l['created_at']}] {l['action_text']}" for l in logs]) if logs else "Пусто")
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="↩️ Назад", callback_data="god_panel")]])
    await call.message.edit_text(text, reply_markup=kb, parse_mode="HTML")

@admin_router.callback_query(F.data == "god_broadcast")
async def god_broad(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID: return
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="↩️ Назад", callback_data="god_panel")]])
    await call.message.edit_text("📢 Меню рассылки активно.", reply_markup=kb, parse_mode="HTML")
    
