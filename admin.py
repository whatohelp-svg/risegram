# ============================================================================
# RISEGRAM ECOSYSTEM - GOD MODE / ADVANCED ADMIN ENGINE
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
    ban_reason = State()

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
        # Проверяем, есть ли колонка link в tasks, если нет — добавляем
        try:
            await db.execute("ALTER TABLE tasks ADD COLUMN link TEXT DEFAULT ''")
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
        [InlineKeyboardButton(text="📊 Статистика", callback_data="god_stats"), InlineKeyboardButton(text="👥 Юзеры", callback_data="god_users")],
        [InlineKeyboardButton(text="⚡ Тумблеры фич", callback_data="god_features"), InlineKeyboardButton(text="🎯 Задания", callback_data="god_tasks")],
        [InlineKeyboardButton(text="🧪 Тестеры", callback_data="god_testers_menu"), InlineKeyboardButton(text="💰 Выдать валюту", callback_data="god_grant_menu")],
        [InlineKeyboardButton(text="🔨 Бан / Разбан", callback_data="god_ban_menu"), InlineKeyboardButton(text="💸 Возврат XTR", callback_data="god_refund_menu")],
        [InlineKeyboardButton(text="📜 Логи", callback_data="god_logs"), InlineKeyboardButton(text="📢 Рассылка", callback_data="god_broadcast")],
    ])
    
    text = "👑 <b>GOD MODE: Панель Управления RiseGram</b>"
    if isinstance(event, CallbackQuery):
        await event.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    else:
        await event.answer(text, reply_markup=kb, parse_mode="HTML")

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
    await message.answer("💰 Введите <b>награду в звёздах</b> (число, например 50 или 500):")

@admin_router.message(GodStates.task_reward)
async def process_task_reward(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID: return
    try:
        reward = int(message.text.strip())
        if reward <= 0: raise ValueError()
    except ValueError:
        return await message.answer("❌ Введите нормальное число для награды!")
    
    data = await state.get_data()
    title = data.get("t_title")
    link = data.get("t_link")
    await state.clear()
    
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT INTO tasks (title, link, reward, active) VALUES (?, ?, ?, 1)", (title, link, reward))
        await db.commit()
    
    await log_admin_action(ADMIN_ID, f"Создано задание: {title} | Награда: {reward}⭐")
    await message.answer(f"✅ Задание <b>{title}</b> с наградой <b>{reward} ⭐</b> успешно добавлено!", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🎯 К заданиям", callback_data="god_tasks")]]), parse_mode="HTML")

@admin_router.callback_query(F.data.startswith("god_del_task:"))
async def god_del_task(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID: return
    tid = int(call.data.split(":")[1])
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM tasks WHERE id = ?", (tid,))
        await db.commit()
    await call.answer("🗑 Удалено!")
    await god_tasks(call)

# Стандартные тумблеры и статистика для админки
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
    
