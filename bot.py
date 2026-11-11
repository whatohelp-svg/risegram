# ============================================================================
# RISEGRAM ECOSYSTEM - MAIN BOT CORE (aiogram 3)
# ============================================================================
import asyncio
import logging
import random
import aiosqlite
from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LabeledPrice,
    Message,
    PreCheckoutQuery,
)

from database import init_db, get_or_create_user, is_feature_on, DB_PATH
from admin import admin_router, ADMIN_ID

BOT_TOKEN = "9054546076:AAEMkzzt4bTZDScM6a4NM2eg-GOvpl6_vgE"

logging.basicConfig(level=logging.INFO, format="%(asctime)s - [%(levelname)s] - %(message)s")
router = Router()

# ================= МАШИНА СОСТОЯНИЙ (FSM) =================
class ShopStates(StatesGroup):
    waiting_nft_username = State()
    waiting_custom_888 = State()
    waiting_scam_target = State()

class GameStates(StatesGroup):
    waiting_rocket_bet = State()
    playing_rocket = State()
    waiting_dice_bet = State()
    waiting_coin_bet = State()

# ================= КЛАВИАТУРЫ =================
def kb_main(user_id: int) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="👥 Смотреть анкеты", callback_data="menu_code"), InlineKeyboardButton(text="👤 Профиль", callback_data="menu_phone")],
        [InlineKeyboardButton(text="🌐 Лента", callback_data="menu_purchases"), InlineKeyboardButton(text="⚡ Задания", callback_data="menu_tasks")],
        [InlineKeyboardButton(text="⭐ Магазин", callback_data="menu_shop")],
        [InlineKeyboardButton(text="🚀 Игры (Gram & Stars)", callback_data="menu_games"), InlineKeyboardButton(text="⚙️ Настройки", callback_data="menu_settings")],
    ]
    if user_id == ADMIN_ID:
        rows.append([InlineKeyboardButton(text="👑 Панель создателя (GOD MODE)", callback_data="god_panel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def kb_shop(starter_claimed: int) -> InlineKeyboardMarkup:
    st_text = "🟢 Звёзды (Получено)" if starter_claimed else "🟢 Звёзды (1000 ⭐ • 10 TG ⭐)"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=st_text, callback_data="buy_starter")],
        [InlineKeyboardButton(text="🔵 Верификация (50 ⭐)", callback_data="buy_verify"), InlineKeyboardButton(text="🔴 Метка SCAM (15 ⭐)", callback_data="buy_scam")],
        [InlineKeyboardButton(text="📞 Номера +888 (от 25 ⭐)", callback_data="shop_888"), InlineKeyboardButton(text="🏷 NFT-юзернеймы", callback_data="shop_nft")],
        [InlineKeyboardButton(text="↩️ Назад", callback_data="back_main")],
    ])

# ================= СТАРТ И МЕНЮ =================
@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    u = await get_or_create_user(message.from_user.id, message.from_user.full_name, message.from_user.username)
    if u.get("banned"):
        return await message.answer("❌ Ваш аккаунт заблокирован в экосистеме RiseGram.")
    
    badge = " ☑️" if u["verified"] else ""
    scam = " [SCAM]" if u["scam_mark"] else ""
    text = (
        f"💎 <b>RiseGram Ecosystem Core</b>\n\n"
        f"📞 Номер: <code>{u['phone']}</code>\n"
        f"👤 Аккаунт: <b>{u['name']}{badge}{scam}</b> • ID <code>{u['rise_id']}</code>\n"
        f"⭐ Звёзды: <b>{u['risestars']:,} ⭐</b>\n"
        f"💎 Gram: <b>{u['gram_balance']:,.2f} 💎</b>\n\n"
        f"Выберите раздел:"
    )
    await message.answer(text, reply_markup=kb_main(message.from_user.id), parse_mode="HTML")

@router.callback_query(F.data == "back_main")
async def cb_back_main(call: CallbackQuery, state: FSMContext):
    await state.clear()
    u = await get_or_create_user(call.from_user.id, call.from_user.full_name, call.from_user.username)
    text = f"💎 <b>RiseGram Ecosystem Core</b>\n\n⭐ Звёзды: <b>{u['risestars']:,} ⭐</b>"
    await call.message.edit_text(text, reply_markup=kb_main(call.from_user.id), parse_mode="HTML")

# ================= ЗАДАНИЯ =================
@router.callback_query(F.data == "menu_tasks")
async def cb_tasks(call: CallbackQuery):
    if not await is_feature_on("tasks_system"):
        return await call.answer("❌ Раздел заданий временно отключен администратором!", show_alert=True)
    
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        tasks = await (await db.execute("SELECT * FROM tasks WHERE active = 1")).fetchall()
    
    text = "⚡ <b>Активные задания RiseGram</b>\n\n"
    for t in tasks:
        text += f"🔹 {t['title']} — Награда: <b>{t['reward']} ⭐</b>\n"
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Проверить выполнение", callback_data="task_check")],
        [InlineKeyboardButton(text="↩️ Назад", callback_data="back_main")]
    ])
    await call.message.edit_text(text, reply_markup=kb, parse_mode="HTML")

@router.callback_query(F.data == "task_check")
async def cb_task_check(call: CallbackQuery):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET risestars = risestars + 100 WHERE tg_id = ?", (call.from_user.id,))
        await db.commit()
    await call.answer("🎉 Бонус 100 ⭐ успешно зачислен!", show_alert=True)

# ================= ИГРОВОЙ ЗАЛ И РАКЕТА (CRASH) =================
@router.callback_query(F.data == "menu_games")
async def cb_games(call: CallbackQuery, state: FSMContext):
    await state.clear()
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚀 Ракета (Crash)", callback_data="game_rocket")],
        [InlineKeyboardButton(text="🎲 Кубик", callback_data="game_dice_menu")],
        [InlineKeyboardButton(text="🪙 Орёл и Решка", callback_data="game_coin_menu")],
        [InlineKeyboardButton(text="↩️ Назад", callback_data="back_main")],
    ])
    await call.message.edit_text("🚀 <b>Игровой зал RiseGram</b>\n\nВыбирай игру:", reply_markup=kb, parse_mode="HTML")

@router.callback_query(F.data == "game_rocket")
async def cb_game_rocket(call: CallbackQuery, state: FSMContext):
    if not await is_feature_on("games_rocket"):
        return await call.answer("❌ Игра Ракета временно отключена!", show_alert=True)
    await state.set_state(GameStates.waiting_rocket_bet)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="menu_games")]])
    await call.message.edit_text("🚀 <b>Ракета Crash</b>\n\nВведите свою сумму ставки в звёздах цифрами:", reply_markup=kb, parse_mode="HTML")

@router.message(GameStates.waiting_rocket_bet)
async def process_rocket_bet(message: Message, state: FSMContext):
    try:
        bet = int(message.text.strip())
        if bet <= 0: raise ValueError()
    except ValueError:
        return await message.answer("❌ Введите корректное число:")
    
    u = await get_or_create_user(message.from_user.id, message.from_user.full_name, message.from_user.username)
    if u["risestars"] < bet:
        return await message.answer(f"❌ Недостаточно средств! У вас {u['risestars']:,} ⭐.")

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET risestars = risestars - ? WHERE tg_id = ?", (bet, message.from_user.id))
        await db.commit()

    # Точные шансы: 57.5% проигрыш (<1.99x), 42.5% победа (2.0x+)
    if random.random() < 0.575:
        max_mult = round(random.uniform(1.01, 1.99), 2)
    else:
        max_mult = round(random.uniform(2.0, 7.5), 2)

    await state.set_state(GameStates.playing_rocket)
    await state.update_data(bet=bet, max_mult=max_mult, current_mult=1.0, cashed_out=False)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🛑 Забрать выигрыш", callback_data="rocket_cashout")],
        [InlineKeyboardButton(text="↩️ Отмена", callback_data="rocket_abort")],
    ])

    msg = await message.answer(f"🚀 <b>Ракета летит!</b>\n\nСтавка: {bet} ⭐\nМножитель: x1.00", reply_markup=kb, parse_mode="HTML")
    await state.update_data(msg_id=msg.message_id)
    asyncio.create_task(run_rocket_animation(message.bot, message.from_user.id, state))

async def run_rocket_animation(bot: Bot, user_id: int, state: FSMContext):
    while True:
        await asyncio.sleep(0.1) # Анимация каждые 100мс
        data = await state.get_data()
        if await state.get_state() != GameStates.playing_rocket or data.get("cashed_out", False):
            break

        current = data.get("current_mult", 1.0)
        max_m = data.get("max_mult", 2.0)
        bet = data.get("bet", 100)
        msg_id = data.get("msg_id")

        if current >= max_m:
            await state.clear()
            kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔄 Ещё раз", callback_data="game_rocket"), InlineKeyboardButton(text="↩️ В меню", callback_data="menu_games")]])
            try:
                await bot.edit_message_text(chat_id=user_id, message_id=msg_id, text=f"💥 <b>Ракета взорвалась на x{max_m}</b>!\nПотеряно: {bet} ⭐.", reply_markup=kb, parse_mode="HTML")
            except Exception:
                pass
            break
        else:
            next_mult = round(current + random.uniform(0.03, 0.09), 2)
            await state.update_data(current_mult=next_mult)
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=f"🛑 Забрать ({int(bet * next_mult)} ⭐)", callback_data="rocket_cashout")],
                [InlineKeyboardButton(text="↩️ Отмена", callback_data="rocket_abort")],
            ])
            try:
                await bot.edit_message_text(chat_id=user_id, message_id=msg_id, text=f"🚀 <b>Ракета набирает высоту!</b>\nСтавка: {bet} ⭐\nМножитель: <b>x{next_mult:.2f}</b>", reply_markup=kb, parse_mode="HTML")
            except Exception:
                pass

@router.callback_query(F.data == "rocket_cashout", GameStates.playing_rocket)
async def cb_rocket_cashout(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if data.get("cashed_out", False): return await call.answer()
    
    await state.update_data(cashed_out=True)
    await state.clear()
    
    mult = data.get("current_mult", 1.0)
    bet = data.get("bet", 100)
    win = int(bet * mult)
    
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET risestars = risestars + ? WHERE tg_id = ?", (win, call.from_user.id))
        await db.commit()
        
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔄 Ещё раз", callback_data="game_rocket"), InlineKeyboardButton(text="↩️ В меню", callback_data="menu_games")]])
    await call.message.edit_text(f"🎉 <b>Успешный кэшаут на x{mult:.2f}!</b>\nВыиграно: <b>{win:,} ⭐</b>", reply_markup=kb, parse_mode="HTML")

@router.callback_query(F.data == "rocket_abort")
async def cb_rocket_abort(call: CallbackQuery, state: FSMContext):
    await state.clear()
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🚀 В игру", callback_data="game_rocket"), InlineKeyboardButton(text="↩️ В меню", callback_data="menu_games")]])
    await call.message.edit_text("❌ Игра отменена. Ставка сгорела.", reply_markup=kb, parse_mode="HTML")

# --- КУБИК И ОРЕЛ/РЕШКА ---
@router.callback_query(F.data == "game_dice_menu")
async def cb_game_dice_menu(call: CallbackQuery, state: FSMContext):
    await state.set_state(GameStates.waiting_dice_bet)
    await call.message.edit_text("🎲 <b>Кубик удачи</b>\n\nВведите ставку в звёздах:", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="menu_games")]]))

@router.message(GameStates.waiting_dice_bet)
async def process_dice_bet(message: Message, state: FSMContext):
    try:
        bet = int(message.text.strip())
        if bet <= 0: raise ValueError()
    except ValueError:
        return await message.answer("❌ Введите число!")
    u = await get_or_create_user(message.from_user.id, message.from_user.full_name, message.from_user.username)
    if u["risestars"] < bet:
        return await message.answer(f"❌ Недостаточно средств!")
    await state.clear()
    msg = await message.answer_dice(emoji="🎲")
    await asyncio.sleep(3)
    val = msg.dice.value
    async with aiosqlite.connect(DB_PATH) as db:
        if val >= 4:
            win = bet * 2
            await db.execute("UPDATE users SET risestars = risestars + ? WHERE tg_id = ?", (bet, message.from_user.id))
            res = f"🎲 Выпало: <b>{val}</b> (Победа!)\n🎉 Выиграно: <b>{win:,} ⭐</b>"
        else:
            await db.execute("UPDATE users SET risestars = risestars - ? WHERE tg_id = ?", (bet, message.from_user.id))
            res = f"🎲 Выпало: <b>{val}</b> (Проигрыш...)\n💸 Потеряно: {bet} ⭐"
        await db.commit()
    await message.answer(res, reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔄 Ещё", callback_data="game_dice_menu"), InlineKeyboardButton(text="↩️ Меню", callback_data="menu_games")]]), parse_mode="HTML")

@router.callback_query(F.data == "game_coin_menu")
async def cb_game_coin_menu(call: CallbackQuery, state: FSMContext):
    await state.set_state(GameStates.waiting_coin_bet)
    await call.message.edit_text("🪙 <b>Орёл и Решка</b>\n\nВведите сумму ставки:", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="menu_games")]]))

@router.message(GameStates.waiting_coin_bet)
async def process_coin_bet(message: Message, state: FSMContext):
    try:
        bet = int(message.text.strip())
        if bet <= 0: raise ValueError()
    except ValueError:
        return await message.answer("❌ Введите число!")
    u = await get_or_create_user(message.from_user.id, message.from_user.full_name, message.from_user.username)
    if u["risestars"] < bet:
        return await message.answer("❌ Недостаточно средств!")
    await state.update_data(coin_bet=bet)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🟢 Орёл", callback_data="coin_flip:eagle"), InlineKeyboardButton(text="🔵 Решка", callback_data="coin_flip:tail")],
        [InlineKeyboardButton(text="↩️ Назад", callback_data="menu_games")]
    ])
    await message.answer(f"🪙 Ставка: <b>{bet} ⭐</b>. Выберите сторону:", reply_markup=kb, parse_mode="HTML")

@router.callback_query(F.data.startswith("coin_flip:"))
async def cb_coin_result(call: CallbackQuery, state: FSMContext):
    side = call.data.split(":")[1]
    data = await state.get_data()
    bet = data.get("coin_bet", 100)
    await state.clear()
    
    result = random.choice(["eagle", "tail"])
    won = (side == result)
    async with aiosqlite.connect(DB_PATH) as db:
        if won:
            await db.execute("UPDATE users SET risestars = risestars + ? WHERE tg_id = ?", (bet, call.from_user.id))
            res = f"🪙 Выпал {'Орёл 🟢' if result=='eagle' else 'Решка 🔵'}!\n🎉 Выиграно: <b>{bet*2} ⭐</b>"
        else:
            await db.execute("UPDATE users SET risestars = risestars - ? WHERE tg_id = ?", (bet, call.from_user.id))
            res = f"🪙 Выпал {'Орёл 🟢' if result=='eagle' else 'Решка 🔵'}.\n💸 Проигрыш: {bet} ⭐"
        await db.commit()
    await call.message.edit_text(res, reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔄 Ещё", callback_data="game_coin_menu"), InlineKeyboardButton(text="↩️ Меню", callback_data="menu_games")]]), parse_mode="HTML")

# ================= МАГАЗИН И ОПЛАТА ЗВЕЗДАМИ (XTR) =================
@router.message(Command("shop"))
@router.callback_query(F.data == "menu_shop")
async def show_shop(event: Message | CallbackQuery):
    u = await get_or_create_user(event.from_user.id, event.from_user.full_name, event.from_user.username)
    text = (
        "💎 <b>Магазин RiseGram</b>\n\n"
        "Покупка за Telegram Stars (XTR):\n\n"
        "🟢 <b>Стартовый пак</b> — 1 000 ⭐ (10 TG ⭐)\n"
        "🔵 <b>Верификация</b> — Галочка (50 TG ⭐)\n"
        "🔴 <b>Метка SCAM</b> — (15 TG ⭐)"
    )
    kb = kb_shop(u["starter_claimed"])
    if isinstance(event, CallbackQuery):
        await event.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    else:
        await event.answer(text, reply_markup=kb, parse_mode="HTML")

async def send_stars_invoice(bot: Bot, chat_id: int, title: str, desc: str, payload: str, stars: int):
    await bot.send_invoice(chat_id=chat_id, title=title, description=desc, payload=payload, currency="XTR", prices=[LabeledPrice(label=title, amount=stars)])

@router.callback_query(F.data == "buy_starter")
async def cb_buy_starter(call: CallbackQuery):
    u = await get_or_create_user(call.from_user.id, call.from_user.full_name, call.from_user.username)
    if u["starter_claimed"]:
        return await call.answer("❌ Пак уже получен!", show_alert=True)
    await call.answer()
    await send_stars_invoice(call.bot, call.from_user.id, "1 000 Звёзд", "Стартовый пак", "starter_1000", 10)

@router.callback_query(F.data == "buy_verify")
async def cb_buy_verify(call: CallbackQuery):
    await call.answer()
    await send_stars_invoice(call.bot, call.from_user.id, "Верификация", "Мажорный статус", "verify_major", 50)

@router.pre_checkout_query()
async def on_pre_checkout(query: PreCheckoutQuery):
    await query.answer(ok=True)

@router.message(F.successful_payment)
async def on_successful_payment(message: Message):
    pay = message.successful_payment
    payload = pay.invoice_payload
    stars_paid = pay.total_amount
    charge_id = pay.telegram_payment_charge_id
    tg_id = message.from_user.id

    async with aiosqlite.connect(DB_PATH) as db:
        if payload == "starter_1000":
            await db.execute("UPDATE users SET risestars = risestars + 1000, starter_claimed = 1 WHERE tg_id = ?", (tg_id,))
            title = "1 000 Звёзд"
        elif payload == "verify_major":
            await db.execute("UPDATE users SET verified = 1 WHERE tg_id = ?", (tg_id,))
            title = "Верификация"
        else:
            title = payload

        await db.execute("INSERT INTO purchases (tg_id, title, price_xtr, charge_id) VALUES (?, ?, ?, ?)", (tg_id, title, stars_paid, charge_id))
        await db.commit()

    await message.answer(f"✅ <b>Оплата прошла успешно!</b>\nВыдано: <b>{title}</b>", reply_markup=kb_main(tg_id), parse_mode="HTML")

# ================= КОМАНДА /who (ПРОБИВ ИГРОКА) =================
@router.message(Command("who"))
async def cmd_who(message: Message):
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        return await message.answer("⚠️ Использование: <code>/who @username</code>", parse_mode="HTML")
    target_uname = parts[1].strip()
    if not target_uname.startswith("@"): target_uname = f"@{target_uname}"

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        user = await (await db.execute("SELECT * FROM users WHERE username LIKE ?", (f"%{target_uname}%",))).fetchone()

    if not user:
        return await message.answer(f"❌ Игрок <b>{target_uname}</b> не найден.", parse_mode="HTML")

    badge = " ☑️" if user["verified"] else ""
    scam = " [SCAM]" if user["scam_mark"] else ""
    text = (
        f"👤 <b>Профиль игрока:</b>\n\n"
        f"• Имя: <b>{user['name']}{badge}{scam}</b>\n"
        f"• Rise ID: <code>{user['rise_id']}</code> (TG: <code>{user['tg_id']}</code>)\n"
        f"• Номер: <code>{user['phone']}</code>\n"
        f"• Юзернеймы: <code>{user['username']}</code>\n"
        f"• ⭐ Звёзды: <b>{user['risestars']:,} ⭐</b>\n"
        f"• 💎 Gram: <b>{user['gram_balance']:,.2f} 💎</b>"
    )
    await message.answer(text, parse_mode="HTML")

# ================= ЗАПУСК БОТА =================
async def main():
    await init_db()
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()
    
    dp.include_router(admin_router)
    dp.include_router(router)
    
    print("🔥 RiseGram Bot запущен и готов к работе!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
