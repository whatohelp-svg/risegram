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
    task_title = State()
    task_link = State()
    task_reward = State()
    tester_username = State()
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
        [ColoredInlineKeyboardButton(text="📊 Аналитика", callback_data="god_stats", style="primary"), ColoredInlineKeyboardButton(text="👥 Управление юзерами", callback_data="god_users", style="primary")],
        [ColoredInlineKeyboardButton(text="💰 Выдача валюты", callback_data="god_grant_menu", style="success"), ColoredInlineKeyboardButton(text="🎒 Инвентарь & Скины", callback_data="god_inventory_menu", style="primary")],
        [ColoredInlineKeyboardButton(text="⚡ Тумблеры функций (Все)", callback_data="god_features", style="success"), ColoredInlineKeyboardButton(text="🎯 Динамические задачи", callback_data="god_tasks", style="primary")],
        [ColoredInlineKeyboardButton(text="🧪 Пул тестеров", callback_data="god_testers_menu", style="success"), ColoredInlineKeyboardButton(text="🔨 Банхаммер", callback_data="god_ban_menu", style="danger")],
        [ColoredInlineKeyboardButton(text="📜 Системные логи", callback_data="god_logs", style="primary"), ColoredInlineKeyboardButton(text="📢 Рассылка", callback_data="god_broadcast", style="success")],
    ])
    
    text = (
        f"💎 <b>RiseGram Ecosystem — GOD MODE (Owner Core)</b>\n\n"
        f"⭐ Звёзды: <b>999,999,999</b> (∞ Infinite)\n"
        f"💎 Gram Balance: <b>999,999.99</b> (Active)\n"
        f"☑️ Статус: <b>Официальный Владелец / Верифицирован</b>\n\n"
        f"Выберите инструмент администрирования:"
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
        c4 = await (await db.execute("SELECT COUNT(*) FROM testers")).fetchone()
        c5 = await (await db.execute("SELECT COUNT(*) FROM users WHERE banned = 1")).fetchone()
    
    text = (
        f"📊 <b>Детальная аналитика RiseGram:</b>\n\n"
        f"👥 Всего зарегистрировано игроков: <b>{c1[0]}</b>\n"
        f"🔨 Заблокировано пользователей: <b>{c5[0]}</b>\n"
        f"🧪 Активных тестеров: <b>{c4[0]}</b>\n"
        f"⭐ Всего собрано Telegram XTR: <b>{c2[0] or 0} ⭐</b>\n"
        f"💎 Общая эмиссия звёзд: <b>{c3[0] or 0} ⭐</b>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[[ColoredInlineKeyboardButton(text="↩️ В главное меню", callback_data="god_panel", style="primary")]])
    await call.message.edit_text(text, reply_markup=kb, parse_mode="HTML")

@admin_router.callback_query(F.data == "god_users")
async def god_users(call: CallbackQuery):
    if not is_owner(call.from_user.id, call.from_user.username): return
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute("SELECT tg_id, username, risestars, gram_balance, banned, verified FROM users ORDER BY tg_id DESC LIMIT 15")).fetchall()
    
    text = "👥 <b>База игроков RiseGram:</b>\n\n"
    kb_rows = []
    for r in rows:
        st = "🔴 БАН" if r["banned"] else ("☑️ ВЕРХ" if r["verified"] else "🟢 ОК")
        text += f"• <code>{r['username']}</code> (ID: {r['tg_id']}) | ⭐ {r['risestars']} | [{st}]\n"
        kb_rows.append([
            ColoredInlineKeyboardButton(text=f"🗑 Стереть {r['username']}", callback_data=f"god_del_user:{r['tg_id']}", style="danger"),
            ColoredInlineKeyboardButton(text=f"☑️ Вериф", callback_data=f"god_ver_user:{r['tg_id']}", style="success")
        ])
        
    kb_rows.append([ColoredInlineKeyboardButton(text="↩️ В главное меню", callback_data="god_panel", style="primary")])
    await call.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows), parse_mode="HTML")

@admin_router.callback_query(F.data.startswith("god_del_user:"))
async def god_del_user(call: CallbackQuery):
    if not is_owner(call.from_user.id, call.from_user.username): return
    tid = int(call.data.split(":")[1])
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM users WHERE tg_id = ?", (tid,))
        await db.commit()
    await log_admin_action(ADMIN_ID, f"Полностью удален юзер с ID {tid}")
    await call.answer("🗑 Пользователь стерт из базы!", show_alert=True)
    await god_users(call)

@admin_router.callback_query(F.data.startswith("god_ver_user:"))
async def god_ver_user(call: CallbackQuery):
    if not is_owner(call.from_user.id, call.from_user.username): return
    tid = int(call.data.split(":")[1])
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT verified FROM users WHERE tg_id = ?", (tid,))
        row = await cur.fetchone()
        new_v = 0 if row and row[0] else 1
        await db.execute("UPDATE users SET verified = ? WHERE tg_id = ?", (new_v, tid))
        await db.commit()
    await call.answer(f"☑️ Статус верификации изменен на {new_v}!", show_alert=True)
    await god_users(call)

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
    tid, uname, vtype = data.get("target_tg_id"), data.get("target_name"), data.get("val_type", "stars")
    await state.clear()
    col = "risestars" if vtype == "stars" else "gram_balance"
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(f"UPDATE users SET {col} = {col} + ? WHERE tg_id = ?", (amount, tid))
        await db.commit()
    await log_admin_action(ADMIN_ID, f"Изменен баланс {uname}: {amount:+f} ({vtype})")
    await message.answer(f"✅ Успешно! Игроку <b>{uname}</b> изменено: <b>{amount:+g} {'⭐' if vtype == 'stars' else '💎'}</b>", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[ColoredInlineKeyboardButton(text="👑 В админку", callback_data="god_panel", style="primary")]]), parse_mode="HTML")

@admin_router.callback_query(F.data == "god_inventory_menu")
async def god_inventory_menu(call: CallbackQuery):
    if not is_owner(call.from_user.id, call.from_user.username): return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [ColoredInlineKeyboardButton(text="🔪 Выдать Knife:Butterfly 'Black Window'", callback_data="give_item:butterfly", style="success")],
        [ColoredInlineKeyboardButton(text="🔫 Выдать M4A1 'Kitsune'", callback_data="give_item:kitsune", style="success")],
        [ColoredInlineKeyboardButton(text="↩️ В главное меню", callback_data="god_panel", style="primary")]
    ])
    await call.message.edit_text("🎒 <b>Инвентарь и скины:</b>", reply_markup=kb, parse_mode="HTML")

@admin_router.callback_query(F.data.startswith("give_item:"))
async def give_item_exec(call: CallbackQuery):
    if not is_owner(call.from_user.id, call.from_user.username): return
    item = call.data.split(":")[1]
    iname = "Knife:Butterfly 'Black Window'" if item == "butterfly" else "M4A1 'Kitsune'"
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT INTO user_inventory (tg_id, item_name, item_type) VALUES (?, ?, ?)", (call.from_user.id, iname, "weapon_skin"))
        await db.commit()
    await call.answer(f"✅ Предмет {iname} добавлен!", show_alert=True)

# ==================== УПРАВЛЕНИЕ ВСЕМИ ТУМБЛЕРАМИ ====================
@admin_router.callback_query(F.data == "god_features")
async def god_features(call: CallbackQuery):
    if not is_owner(call.from_user.id, call.from_user.username): return
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute("SELECT * FROM feature_flags")).fetchall()
        
    kb = []
    for r in rows:
        status_text = "🟢 ВКЛ" if r['is_enabled'] else "🔴 ВЫКЛ"
        btn_style = "success" if r['is_enabled'] else "danger"
        kb.append([ColoredInlineKeyboardButton(text=f"{r['feature_name']}: {status_text}", callback_data=f"toggle_feat:{r['feature_name']}", style=btn_style)])
        
    kb.append([ColoredInlineKeyboardButton(text="↩️ В главное меню", callback_data="god_panel", style="primary")])
    await call.message.edit_text("⚙️ <b>Центр управления тумблерами функций:</b>\n\nНажмите для мгновенного включения/отключения модулей:", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode="HTML")

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
    await log_admin_action(ADMIN_ID, f"Тумблер {fname} изменен на {new_val}")
    await call.answer(f"⚙️ Функция {fname} теперь {'включена 🟢' if new_val else 'выключена 🔴'}!")
    await god_features(call)

@admin_router.callback_query(F.data == "god_testers_menu")
async def god_testers_menu(call: CallbackQuery):
    if not is_owner(call.from_user.id, call.from_user.username): return
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        testers = await (await db.execute("SELECT * FROM testers")).fetchall()
    text = "🧪 <b>Пул тестеров:</b>\n\n"
    kb = [[ColoredInlineKeyboardButton(text="➕ Добавить тестера", callback_data="god_add_tester", style="success")]]
    for t in testers:
        text += f"• <code>{t['username']}</code> (ID: {t['tg_id']})\n"
        kb.append([ColoredInlineKeyboardButton(text=f"❌ Удалить {t['username']}", callback_data=f"god_del_tester:{t['tg_id']}", style="danger")])
    kb.append([ColoredInlineKeyboardButton(text="↩️ В главное меню", callback_data="god_panel", style="primary")])
    await call.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode="HTML")

@admin_router.callback_query(F.data == "god_add_tester")
async def god_add_tester(call: CallbackQuery, state: FSMContext):
    if not is_owner(call.from_user.id, call.from_user.username): return
    await state.set_state(GodStates.tester_username)
    await call.message.edit_text("🧪 Введите юзернейм тестера:", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[ColoredInlineKeyboardButton(text="❌ Отмена", callback_data="god_testers_menu", style="danger")]]))

@admin_router.message(GodStates.tester_username)
async def process_add_tester(message: Message, state: FSMContext):
    if not is_owner(message.from_user.id, message.from_user.username): return
    uname = message.text.strip()
    if not uname.startswith("@"): uname = f"@{uname}"
    await state.clear()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        user = await (await db.execute("SELECT tg_id FROM users WHERE username LIKE ?", (f"%{uname}%",))).fetchone()
        if not user: return await message.answer("❌ Юзер не найден в боте!")
        await db.execute("INSERT OR IGNORE INTO testers (tg_id, username) VALUES (?, ?)", (user["tg_id"], uname))
        await db.commit()
    await message.answer(f"✅ Тестер <b>{uname}</b> добавлен!", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[ColoredInlineKeyboardButton(text="🧪 К тестерам", callback_data="god_testers_menu", style="primary")]]), parse_mode="HTML")

@admin_router.callback_query(F.data.startswith("god_del_tester:"))
async def god_del_tester(call: CallbackQuery):
    if not is_owner(call.from_user.id, call.from_user.username): return
    tid = int(call.data.split(":")[1])
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM testers WHERE tg_id = ?", (tid,))
        await db.commit()
    await call.answer("🗑 Удалено!", show_alert=True)
    await god_testers_menu(call)

@admin_router.callback_query(F.data == "god_ban_menu")
async def god_ban_menu(call: CallbackQuery, state: FSMContext):
    if not is_owner(call.from_user.id, call.from_user.username): return
    await state.set_state(GodStates.ban_target)
    await call.message.edit_text("🔨 Введите юзернейм или ID для бана/разбана:", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[ColoredInlineKeyboardButton(text="❌ Отмена", callback_data="god_panel", style="danger")]]))

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
    await message.answer(f"✅ Пользователь <b>{user['username']}</b> теперь <b>{st}</b>!", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[ColoredInlineKeyboardButton(text="👑 В админку", callback_data="god_panel", style="primary")]]), parse_mode="HTML")

@admin_router.callback_query(F.data == "god_tasks")
async def god_tasks(call: CallbackQuery):
    if not is_owner(call.from_user.id, call.from_user.username): return
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        tasks = await (await db.execute("SELECT * FROM tasks")).fetchall()
    kb = [[ColoredInlineKeyboardButton(text="➕ Создать задание", callback_data="god_add_task", style="success")]]
    for t in tasks:
        kb.append([ColoredInlineKeyboardButton(text=f"🗑 {t['title'][:20]} ({t['reward']}⭐)", callback_data=f"god_del_task:{t['id']}", style="danger")])
    kb.append([ColoredInlineKeyboardButton(text="↩️ В главное меню", callback_data="god_panel", style="primary")])
    await call.message.edit_text("🎯 <b>Задания:</b>", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode="HTML")

@admin_router.callback_query(F.data == "god_add_task")
async def god_add_task(call: CallbackQuery, state: FSMContext):
    if not is_owner(call.from_user.id, call.from_user.username): return
    await state.set_state(GodStates.task_title)
    await call.message.edit_text("📝 Введите название:", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[ColoredInlineKeyboardButton(text="❌ Отмена", callback_data="god_tasks", style="danger")]]))

@admin_router.message(GodStates.task_title)
async def process_task_title(message: Message, state: FSMContext):
    if not is_owner(message.from_user.id, message.from_user.username): return
    await state.update_data(t_title=message.text.strip())
    await state.set_state(GodStates.task_link)
    await message.answer("🔗 Ссылка:")

@admin_router.message(GodStates.task_link)
async def process_task_link(message: Message, state: FSMContext):
    if not is_owner(message.from_user.id, message.from_user.username): return
    await state.update_data(t_link=message.text.strip())
    await state.set_state(GodStates.task_reward)
    await message.answer("💰 Награда (число):")

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
    await message.answer("✅ Задание создано!", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[ColoredInlineKeyboardButton(text="🎯 К заданиям", callback_data="god_tasks", style="primary")]]))

@admin_router.callback_query(F.data.startswith("god_del_task:"))
async def god_del_task(call: CallbackQuery):
    if not is_owner(call.from_user.id, call.from_user.username): return
    tid = int(call.data.split(":")[1])
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM tasks WHERE id = ?", (tid,))
        await db.commit()
    await call.answer("🗑 Удалено!")
    await god_tasks(call)

@admin_router.callback_query(F.data == "god_logs")
async def god_logs(call: CallbackQuery):
    if not is_owner(call.from_user.id, call.from_user.username): return
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        logs = await (await db.execute("SELECT * FROM admin_logs ORDER BY id DESC LIMIT 15")).fetchall()
    text = "📜 <b>Логи:</b>\n\n" + ("\n".join([f"• [{l['created_at']}] {l['action_text']}" for l in logs]) if logs else "Пусто")
    kb = InlineKeyboardMarkup(inline_keyboard=[[ColoredInlineKeyboardButton(text="↩️ В главное меню", callback_data="god_panel", style="primary")]])
    await call.message.edit_text(text, reply_markup=kb, parse_mode="HTML")

@admin_router.callback_query(F.data.in_({"god_broadcast", "god_refund_menu"}))
async def placeholders(call: CallbackQuery):
    if not is_owner(call.from_user.id, call.from_user.username): return
    kb = InlineKeyboardMarkup(inline_keyboard=[[ColoredInlineKeyboardButton(text="↩️ В главное меню", callback_data="god_panel", style="primary")]])
    await call.message.edit_text("⚙️ Раздел готов к работе.", reply_markup=kb, parse_mode="HTML")
