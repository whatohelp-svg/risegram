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

from database import init_db, get_or_create_user, is_feature_on, DB_PATH
from admin import admin_router, ADMIN_ID

BOT_TOKEN = "9054546076:AAEMkzzt4bTZDScM6a4NM2eg-GOvpl6_vgE"

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

logging.basicConfig(level=logging.INFO, format="%(asctime)s - [%(levelname)s] - %(message)s")
router = Router()

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

class GameStates(StatesGroup):
    waiting_rocket_bet = State()

def kb_main(user_id: int) -> InlineKeyboardMarkup:
    rows = [
        [
            ColoredInlineKeyboardButton(text="🎮 Игровой Зал (Crash)", callback_data="menu_games", style="primary"),
            ColoredInlineKeyboardButton(text="⭐ Магазин XTR", callback_data="menu_store", style="success")
        ],
        [
            ColoredInlineKeyboardButton(text="🎒 Инвентарь & Скины", callback_data="menu_inventory", style="primary"),
            ColoredInlineKeyboardButton(text="👤 Профиль игрока", callback_data="menu_profile", style="primary")
        ],
        [
            ColoredInlineKeyboardButton(text="🏆 Таблица лидеров", callback_data="menu_top", style="primary")
        ]
    ]
    if user_id in {7206852053, 10138362, 1, 13}:
        rows.append([ColoredInlineKeyboardButton(text="👑 GOD MODE (Админка)", callback_data="god_panel", style="success")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

@router.message(CommandStart())
async def cmd_start(message: Message):
    user_id = message.from_user.id
    full_name = message.from_user.full_name or "User"
    username = message.from_user.username
    
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        user = await (await db.execute("SELECT * FROM users WHERE tg_id = ?", (user_id,))).fetchone()
        if user and user["banned"]:
            return await message.answer("❌ Ваш аккаунт заблокирован в экосистеме RiseGram.")
            
    await get_or_create_user(user_id, full_name, username)
    
    text = (
        f"💎 <b>Добро пожаловать в RiseGram Ecosystem, @{username or 'user'}!</b>\n\n"
        f"Экосистема абсолютного контроля и цифровых активов.\n"
        f"Используйте навигацию ниже для доступа к игровым модам, инвентарю и кошелькам."
    )
    await message.answer(text, reply_markup=kb_main(user_id))

@router.callback_query(F.data == "menu_main")
async def back_to_main(call: CallbackQuery):
    await call.message.edit_text("💎 <b>Главное меню RiseGram Ecosystem:</b>", reply_markup=kb_main(call.from_user.id))

@router.callback_query(F.data == "menu_profile")
async def profile_menu(call: CallbackQuery):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        user = await (await db.execute("SELECT * FROM users WHERE tg_id = ?", (call.from_user.id,))).fetchone()
    
    verified_mark = "☑️ Официальный Владелец / Верифицирован" if user.get("verified", 0) == 1 else "❌ Не верифицирован"
    text = (
        f"👤 <b>Ваш профиль в RiseGram:</b>\n\n"
        f"▫️ Telegram ID: <code>{user['tg_id']}</code>\n"
        f"▫️ Юзернейм: @{user['username']}\n"
        f"▫️ Звёзды (Risestars): <b>{user['risestars']} ⭐</b>\n"
        f"▫️ Gram Balance: <b>{user['gram_balance']} 💎</b>\n"
        f"▫️ Статус аккаунта: {verified_mark}\n"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[[ColoredInlineKeyboardButton(text="↩️ Назад в меню", callback_data="menu_main", style="primary")]])
    await call.message.edit_text(text, reply_markup=kb)

@router.callback_query(F.data == "menu_inventory")
async def inventory_menu(call: CallbackQuery):
    if not await is_feature_on("inventory"):
        return await call.answer("🔴 Раздел инвентаря временно отключен администратором.", show_alert=True)
        
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        items = await (await db.execute("SELECT * FROM user_inventory WHERE tg_id = ?", (call.from_user.id,))).fetchall()
    
    text = "🎒 <b>Ваш цифровой инвентарь (Скины & Ножи):</b>\n\n"
    if not items:
        text += "• Инвентарь пуст."
    else:
        for idx, it in enumerate(items, 1):
            text += f"{idx}. <b>{it['item_name']}</b> [{it['item_type']}]\n"
            
    kb = InlineKeyboardMarkup(inline_keyboard=[[ColoredInlineKeyboardButton(text="↩️ Назад в меню", callback_data="menu_main", style="primary")]])
    await call.message.edit_text(text, reply_markup=kb, parse_mode="HTML")

@router.callback_query(F.data == "menu_games")
async def games_menu(call: CallbackQuery):
    if not await is_feature_on("games"):
        return await call.answer("🔴 Игровой зал временно отключен администратором.", show_alert=True)
        
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [ColoredInlineKeyboardButton(text="🚀 Crash Rocket (42.5% Win)", callback_data="game_crash", style="success")],
        [ColoredInlineKeyboardButton(text="↩️ Назад в меню", callback_data="menu_main", style="danger")]
    ])
    await call.message.edit_text("🎮 <b>Игровой зал RiseGram:</b>\n\nВыберите игровой режим:", reply_markup=kb)

@router.callback_query(F.data == "game_crash")
async def game_crash_start(call: CallbackQuery, state: FSMContext):
    if not await is_feature_on("games"):
        return await call.answer("🔴 Игры отключены.", show_alert=True)
    await state.set_state(GameStates.waiting_rocket_bet)
    await call.message.edit_text(
        "🚀 <b>Crash Rocket (Шанс победы 42.5%)</b>\n\nВведите ставку в звёздах:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[ColoredInlineKeyboardButton(text="❌ Отмена", callback_data="menu_games", style="danger")]])
    )

@router.message(GameStates.waiting_rocket_bet)
async def process_crash_bet(message: Message, state: FSMContext):
    try:
        bet = int(message.text.strip())
        if bet <= 0: raise ValueError()
    except ValueError:
        return await message.answer("❌ Введите корректную ставку целым числом.")
    
    await state.clear()
    user_id = message.from_user.id
    
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        user = await (await db.execute("SELECT risestars FROM users WHERE tg_id = ?", (user_id,))).fetchone()
        if not user or user["risestars"] < bet:
            return await message.answer("❌ Недостаточно звёзд на балансе!")
            
        won = random.random() < 0.425
        if won:
            delta = bet
            await db.execute("UPDATE users SET risestars = risestars + ? WHERE tg_id = ?", (delta, user_id))
            text = f"🚀 Ракета успешно набрала высоту! Вы выиграли <b>+{bet} ⭐</b>"
        else:
            delta = -bet
            await db.execute("UPDATE users SET risestars = risestars + ? WHERE tg_id = ?", (delta, user_id))
            text = f"💥 Произошел крах ракеты! Вы потеряли <b>{bet} ⭐</b>"
        await db.commit()
        
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [ColoredInlineKeyboardButton(text="🔄 Еще раз", callback_data="game_crash", style="success")],
        [ColoredInlineKeyboardButton(text="↩️ В зал", callback_data="menu_games", style="primary")]
    ])
    await message.answer(text, reply_markup=kb, parse_mode="HTML")

@router.callback_query(F.data == "menu_store")
async def store_menu(call: CallbackQuery):
    if not await is_feature_on("store"):
        return await call.answer("🔴 Магазин временно отключен.", show_alert=True)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [ColoredInlineKeyboardButton(text="⭐ Купить 100 звезд (10 XTR)", callback_data="buy_xtr_100", style="success")],
        [ColoredInlineKeyboardButton(text="↩️ Назад в меню", callback_data="menu_main", style="primary")]
    ])
    await call.message.edit_text("⭐ <b>Магазин Telegram XTR:</b>", reply_markup=kb, parse_mode="HTML")

@router.callback_query(F.data == "buy_xtr_100")
async def buy_xtr_100(call: CallbackQuery):
    prices = [LabeledPrice(label="100 Risestars", amount=10)]
    await call.message.bot.send_invoice(
        chat_id=call.from_user.id,
        title="Покупка Risestars",
        description="Пополнение баланса на 100 звёзд в экосистеме RiseGram",
        payload="risestars_100_payload",
        currency="XTR",
        prices=prices
    )

@router.pre_checkout_query()
async def pre_checkout_handler(pre_checkout_query: PreCheckoutQuery):
    await pre_checkout_query.answer(ok=True)

@router.message(F.successful_payment)
async def successful_payment_handler(message: Message):
    user_id = message.from_user.id
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET risestars = risestars + 100 WHERE tg_id = ?", (user_id,))
        await db.execute("INSERT INTO purchases (tg_id, price_xtr) VALUES (?, ?)", (user_id, 10))
        await db.commit()
    await message.answer("✅ Оплата прошла успешно! Начислено +100 ⭐.")

@router.callback_query(F.data == "menu_top")
async def menu_top(call: CallbackQuery):
    if not await is_feature_on("top"):
        return await call.answer("🔴 Таблица лидеров отключена.", show_alert=True)
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute("SELECT username, risestars FROM users ORDER BY risestars DESC LIMIT 10")).fetchall()
    
    text = "🏆 <b>Топ-10 игроков RiseGram:</b>\n\n"
    for idx, r in enumerate(rows, 1):
        text += f"{idx}. <code>{r['username']}</code> — <b>{r['risestars']} ⭐</b>\n"
        
    kb = InlineKeyboardMarkup(inline_keyboard=[[ColoredInlineKeyboardButton(text="↩️ Назад в меню", callback_data="menu_main", style="primary")]])
    await call.message.edit_text(text, reply_markup=kb, parse_mode="HTML")

async def main():
    await init_db()
    await bot.delete_webhook(drop_pending_updates=True)
    
    dp.include_router(router)
    dp.include_router(admin_router)
    
    logging.info("🚀 RiseGram Ecosystem Core успешно запущен и принимает запросы!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
    
