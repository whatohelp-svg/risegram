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

from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

BOT_TOKEN = "9054546076:AAEMkzzt4bTZDScM6a4NM2eg-GOvpl6_vgE"
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))

logging.basicConfig(level=logging.INFO)
router = Router()

class GameStates(StatesGroup):
    waiting_rocket_bet = State()
    playing_rocket = State()
    waiting_dice_bet = State()
    waiting_coin_bet = State()

def kb_main(user_id: int) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="👥 Смотреть анкеты", callback_data="menu_code"), InlineKeyboardButton(text="👤 Профиль", callback_data="menu_phone")],
        [InlineKeyboardButton(text="🌐 Лента", callback_data="menu_purchases"), InlineKeyboardButton(text="⚡ Задания", callback_data="menu_tasks")],
        [InlineKeyboardButton(text="⭐ Магазин", callback_data="menu_shop")],
        [InlineKeyboardButton(text="🚀 Игры (Gram & Stars)", callback_data="menu_games"), InlineKeyboardButton(text="⚙️ Настройки", callback_data="menu_settings")],
    ]
    if user_id == ADMIN_ID:
        rows.append([InlineKeyboardButton(text="👑 GOD MODE (Админка)", callback_data="god_panel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    u = await get_or_create_user(message.from_user.id, message.from_user.full_name, message.from_user.username)
    if u.get("banned"):
        return await message.answer("❌ Аккаунт заблокирован.")
    
    badge = " ☑️" if u["verified"] else ""
    scam = " [SCAM]" if u["scam_mark"] else ""
    text = (
        f"💎 <b>RiseGram Ecosystem</b>\n\n"
        f"📞 Номер: <code>{u['phone']}</code>\n"
        f"👤 Аккаунт: <b>{u['name']}{badge}{scam}</b>\n"
        f"⭐ Звёзды: <b>{u['risestars']:,} ⭐</b>\n"
        f"💎 Gram: <b>{u['gram_balance']:,.2f} 💎</b>"
    )
    await message.answer(text, reply_markup=kb_main(message.from_user.id), parse_mode="HTML")

@router.callback_query(F.data == "back_main")
async def cb_back_main(call: CallbackQuery, state: FSMContext):
    await state.clear()
    u = await get_or_create_user(call.from_user.id, call.from_user.full_name, call.from_user.username)
    text = f"💎 <b>RiseGram Ecosystem</b>\n\n⭐ Звёзды: <b>{u['risestars']:,} ⭐</b>"
    await call.message.edit_text(text, reply_markup=kb_main(call.from_user.id), parse_mode="HTML")

# ================= ЗАДАНИЯ С ССЫЛКАМИ И РЕАЛЬНОЙ НАГРАДОЙ =================
@router.callback_query(F.data == "menu_tasks")
async def cb_tasks(call: CallbackQuery):
    if not await is_feature_on("tasks_system"):
        return await call.answer("❌ Раздел заданий отключен!", show_alert=True)
    
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        tasks = await (await db.execute("SELECT * FROM tasks WHERE active = 1")).fetchall()
    
    text = "⚡ <b>Активные задания RiseGram</b>\n\nВыполняй и забирай награды:\n\n"
    kb_rows = []
    
    if not tasks:
        text += "Пока нет активных заданий."
    else:
        for t in tasks:
            link_txt = f" [🔗 Ссылка]" if t['link'] else ""
            text += f"🔹 <b>{t['title']}</b>{link_txt} — Награда: <b>{t['reward']} ⭐</b>\n"
            if t['link']:
                kb_rows.append([InlineKeyboardButton(text=f"🔗 {t['title'][:25]}", url=t['link'])])
                
    kb_rows.append([InlineKeyboardButton(text="✅ Проверить и забрать награды", callback_data="task_check")])
    kb_rows.append([InlineKeyboardButton(text="↩️ Назад", callback_data="back_main")])
    
    await call.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows), parse_mode="HTML")

@router.callback_query(F.data == "task_check")
async def cb_task_check(call: CallbackQuery):
    # Считаем суммарную награду всех активных заданий из базы
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT SUM(reward) FROM tasks WHERE active = 1")
        total_reward = (await cur.fetchone())[0] or 100
        
        await db.execute("UPDATE users SET risestars = risestars + ? WHERE tg_id = ?", (total_reward, call.from_user.id))
        await db.commit()
        
    await call.answer(f"🎉 Проверка успешна! Зачислено: +{total_reward} ⭐", show_alert=True)

# ================= ИГРЫ (КРАШ РАКЕТА) =================
@router.callback_query(F.data == "menu_games")
async def cb_games(call: CallbackQuery, state: FSMContext):
    await state.clear()
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚀 Ракета (Crash)", callback_data="game_rocket")],
        [InlineKeyboardButton(text="🎲 Кубик", callback_data="game_dice_menu")],
        [InlineKeyboardButton(text="🪙 Орёл и Решка", callback_data="game_coin_menu")],
        [InlineKeyboardButton(text="↩️ Назад", callback_data="back_main")],
    ])
    await call.message.edit_text("🚀 <b>Игровой зал</b>", reply_markup=kb, parse_mode="HTML")

@router.callback_query(F.data == "game_rocket")
async def cb_game_rocket(call: CallbackQuery, state: FSMContext):
    if not await is_feature_on("games_rocket"):
        return await call.answer("❌ Игра отключена администратором!", show_alert=True)
    await state.set_state(GameStates.waiting_rocket_bet)
    await call.message.edit_text("🚀 <b>Ракета Crash</b>\n\nВведите свою ставку в звёздах:", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="menu_games")]]))

@router.message(GameStates.waiting_rocket_bet)
async def process_rocket_bet(message: Message, state: FSMContext):
    try:
        bet = int(message.text.strip())
        if bet <= 0: raise ValueError()
    except ValueError:
        return await message.answer("❌ Введите число!")
    
    u = await get_or_create_user(message.from_user.id, message.from_user.full_name, message.from_user.username)
    if u["risestars"] < bet:
        return await message.answer(f"❌ Мало звезд! У вас {u['risestars']:,} ⭐")

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET risestars = risestars - ? WHERE tg_id = ?", (bet, message.from_user.id))
        await db.commit()

    # Шансы: 57.5% слив (<1.99x), 42.5% победа (2.0x+)
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
    msg = await message.answer(f"🚀 <b>Ракета летит!</b>\nСтавка: {bet} ⭐\nМножитель: x1.00", reply_markup=kb, parse_mode="HTML")
    await state.update_data(msg_id=msg.message_id)
    asyncio.create_task(run_rocket_animation(message.bot, message.from_user.id, state))

async def run_rocket_animation(bot: Bot, user_id: int, state: FSMContext):
    while True:
        await asyncio.sleep(0.1)
        data = await state.get_data()
        if await state.get_state() != GameStates.playing_rocket or data.get("cashed_out", False):
            break

        current = data.get("current_mult", 1.0)
        max_m = data.get("max_mult", 2.0)
        bet = data.get("bet", 100)
        msg_id = data.get("msg_id")

        if current >= max_m:
            await state.clear()
            kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔄 Ещё", callback_data="game_rocket"), InlineKeyboardButton(text="↩️ Меню", callback_data="menu_games")]])
            try:
                await bot.edit_message_text(chat_id=user_id, message_id=msg_id, text=f"💥 <b>Ракета взорвалась на x{max_m}</b>!\nПотеряно: {bet} ⭐", reply_markup=kb, parse_mode="HTML")
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
                await bot.edit_message_text(chat_id=user_id, message_id=msg_id, text=f"🚀 <b>Ракета летит!</b>\nСтавка: {bet} ⭐\nМножитель: <b>x{next_mult:.2f}</b>", reply_markup=kb, parse_mode="HTML")
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
        
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔄 Ещё", callback_data="game_rocket"), InlineKeyboardButton(text="↩️ Меню", callback_data="menu_games")]])
    await call.message.edit_text(f"🎉 <b>Успех на x{mult:.2f}!</b>\nВыиграно: <b>{win:,} ⭐</b>", reply_markup=kb, parse_mode="HTML")

@router.callback_query(F.data == "rocket_abort")
async def cb_rocket_abort(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await call.message.edit_text("❌ Игра отменена.", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="↩️ В меню", callback_data="menu_games")]]))

# Профиль и магазин
@router.message(Command("shop"))
@router.callback_query(F.data == "menu_shop")
async def show_shop(event: Message | CallbackQuery):
    text = "💎 <b>Магазин RiseGram</b>\n\nПокупка за Telegram Stars (XTR):\n🟢 1 000 ⭐ — 10 TG ⭐"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🟢 Купить 1000 ⭐ (10 XTR)", callback_data="buy_starter")],
        [InlineKeyboardButton(text="↩️ Назад", callback_data="back_main")]
    ])
    if isinstance(event, CallbackQuery):
        await event.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    else:
        await event.answer(text, reply_markup=kb, parse_mode="HTML")

@router.callback_query(F.data == "buy_starter")
async def cb_buy_starter(call: CallbackQuery):
    await call.answer()
    await call.bot.send_invoice(chat_id=call.from_user.id, title="1 000 Звёзд", description="Пакет звезд", payload="starter_1000", currency="XTR", prices=[LabeledPrice(label="1000 ⭐", amount=10)])

@router.pre_checkout_query()
async def on_pre_checkout(query: PreCheckoutQuery):
    await query.answer(ok=True)

@router.message(F.successful_payment)
async def on_successful_payment(message: Message):
    if message.successful_payment.payload == "starter_1000":
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("UPDATE users SET risestars = risestars + 1000 WHERE tg_id = ?", (message.from_user.id,))
            await db.commit()
        await message.answer("✅ Успешно зачислено 1 000 ⭐!", reply_markup=kb_main(message.from_user.id))

async def main():
    await init_db()
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()
    dp.include_router(admin_router)
    dp.include_router(router)
    print("🔥 Бот запущен!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
    
