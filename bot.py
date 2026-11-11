# ============================================================================
# RISEGRAM ECOSYSTEM - ABSOLUTE MAIN BOT CORE (PRODUCTION READY 2000+ LINES ENGINE)
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

from database import init_db, get_or_create_user, is_feature_on, DB_PATH
from admin import admin_router, ADMIN_ID

BOT_TOKEN = "9054546076:AAEMkzzt4bTZDScM6a4NM2eg-GOvpl6_vgE"

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

logging.basicConfig(level=logging.INFO, format="%(asctime)s - [%(levelname)s] - %(message)s")
router = Router()

# ==================== КЛАСС ЦВЕТНЫХ КНОПОК TELEGRAM API ====================
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

# ==================== FSM СОСТОЯНИЯ ИГР И СИСТЕМЫ ====================
class GameStates(StatesGroup):
    waiting_rocket_bet = State()
    playing_rocket = State()
    waiting_dice_bet = State()
    waiting_coin_bet = State()
    waiting_profile_custom = State()

class WalletStates(StatesGroup):
    transfer_target = State()
    transfer_amount = State()

# ==================== ИГРОВЫЕ И СИСТЕМНЫЕ МЕНЮ ====================
def kb_main(user_id: int) -> InlineKeyboardMarkup:
    rows = [
        [
            ColoredInlineKeyboardButton(text="🎮 Игровой Зал (Crash / Dice)", callback_data="menu_games", style="primary"),
            ColoredInlineKeyboardButton(text="⭐ Магазин XTR", callback_data="menu_store", style="success")
        ],
        [
            ColoredInlineKeyboardButton(text="🎒 Инвентарь & Скины", callback_data="menu_inventory", style="primary"),
            ColoredInlineKeyboardButton(text="👤 Профиль игрока", callback_data="menu_profile", style="primary")
        ],
        [
            ColoredInlineKeyboardButton(text="🏆 Таблица лидеров", callback_data="menu_top", style="primary"),
            ColoredInlineKeyboardButton(text="⚙️ Настройки аккаунта", callback_data="menu_settings", style="primary")
        ]
    ]
    # Доступ к абсолютной админке только для владельцев
    if user_id in {7206852053, 10138362, 1, 13}:
        rows.append([ColoredInlineKeyboardButton(text="👑 GOD MODE (Панель Управления)", callback_data="god_panel", style="success")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

# ==================== ОБРАБОТЧИК СТАРТА ====================
@router.message(CommandStart())
async def cmd_start(message: Message):
    user_id = message.from_user.id
    username = message.from_user.username or f"user_{user_id}"
    
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        user = await (await db.execute("SELECT * FROM users WHERE tg_id = ?", (user_id,))).fetchone()
        if user and user["banned"]:
            return await message.answer("❌ Ваш аккаунт заблокирован в экосистеме RiseGram.")
            
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
            
    # Передаем все 3 аргумента, которые требует твоя база данных
    await get_or_create_user(user_id, full_name, username)
    
    text = (
        f"💎 <b>Добро пожаловать в RiseGram Ecosystem, @{username or 'user'}!</b>\n\n"
        f"Экосистема абсолютного контроля и цифровых активов.\n"
        f"Используйте навигацию ниже для доступа к игровым модам, инвентарю и кошелькам."
    )
    await message.answer(text, reply_markup=kb_main(user_id))


    
    text = (
        f"💎 <b>Добро пожаловать в RiseGram Ecosystem, @{username}!</b>\n\n"
        f"Экосистема абсолютного контроля и цифровых активов.\n"
        f"Используйте навигацию ниже для доступа к игровым модам, инвентарю и кошелькам."
    )
    await message.answer(text, reply_markup=kb_main(user_id))

@router.callback_query(F.data == "menu_main")
async def back_to_main(call: CallbackQuery):
    await call.message.edit_text("💎 <b>Главное меню RiseGram Ecosystem:</b>", reply_markup=kb_main(call.from_user.id))

# ==================== ПРОФИЛЬ ИГРОКА ====================
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
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [ColoredInlineKeyboardButton(text="💸 Перевод средств", callback_data="wallet_transfer", style="success")],
        [ColoredInlineKeyboardButton(text="↩️ Назад в меню", callback_data="menu_main", style="primary")]
    ])
    await call.message.edit_text(text, reply_markup=kb)

# ==================== ИНВЕНТАРЬ И СКИНЫ ====================
@router.callback_query(F.data == "menu_inventory")
async def inventory_menu(call: CallbackQuery):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        items = await (await db.execute("SELECT * FROM user_inventory WHERE tg_id = ?", (call.from_user.id,))).fetchall()
    
    text = "🎒 <b>Ваш цифровой инвентарь (Скины & Ножи):</b>\n\n"
    if not items:
        text += "• Инвентарь пуст. Приобретайте предметы в магазинах или получайте от администраторов."
    else:
        for idx, it in enumerate(items, 1):
            text += f"{idx}. <b>{it['item_name']}</b> [{it['item_type']}]\n"
            
    kb = InlineKeyboardMarkup(inline_keyboard=[[ColoredInlineKeyboardButton(text="↩️ Назад в меню", callback_data="menu_main", style="primary")]])
    await call.message.edit_text(text, reply_markup=kb, parse_mode="HTML")

# ==================== ИГРОВОЙ ЗАЛ ====================
@router.callback_query(F.data == "menu_games")
async def games_menu(call: CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [ColoredInlineKeyboardButton(text="🚀 Crash Rocket (42.5% Win)", callback_data="game_crash", style="success")],
        [ColoredInlineKeyboardButton(text="🎲 Бросок Костей (Dice)", callback_data="game_dice", style="primary")],
        [ColoredInlineKeyboardButton(text="🪙 Орёл и Решка (Coin)", callback_data="game_coin", style="primary")],
        [ColoredInlineKeyboardButton(text="↩️ Назад в меню", callback_data="menu_main", style="danger")]
    ])
    await call.message.edit_text("🎮 <b>Игровой зал RiseGram:</b>\n\nВыберите игровой режим для ставок:", reply_markup=kb)

@router.callback_query(F.data == "game_crash")
async def game_crash_start(call: CallbackQuery, state: FSMContext):
    await state.set_state(GameStates.waiting_rocket_bet)
    await call.message.edit_text(
        "🚀 <b>Crash Rocket (Шанс победы 42.5%)</b>\n\nВведите сумму ставки в звёздах (Risestars):",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[ColoredInlineKeyboardButton(text="❌ Отмена", callback_data="menu_games", style="danger")]])
    )

@router.message(GameStates.waiting_rocket_bet)
async def process_crash_bet(message: Message, state: FSMContext):
    try:
        bet = int(message.text.strip())
        if bet <= 0: raise ValueError()
    except ValueError:
        return await message.answer("❌ Ошибка! Введите корректную ставку целым числом.")
    
    await state.clear()
    user_id = message.from_user.id
    
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        user = await (await db.execute("SELECT risestars FROM users WHERE tg_id = ?", (user_id,))).fetchone()
        if not user or user["risestars"] < bet:
            return await message.answer("❌ Недостаточно звёзд на балансе для совершения ставки!")
            
        won = random.random() < 0.425
        if won:
            delta = bet
            await db.execute("UPDATE users SET risestars = risestars + ? WHERE tg_id = ?", (delta, user_id))
            text = f"🚀 Ракета успешно набрала высоту! Вы выиграли <b>+{bet} ⭐</b>"
        else:
            delta = -bet
            await db.execute("UPDATE users SET risestars = risestars + ? WHERE tg_id = ?", (delta, user_id))
            text = f"💥 Произошел крах ракеты! Вы потеряли ставку в размере <b>{bet} ⭐</b>"
        await db.commit()
        
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [ColoredInlineKeyboardButton(text="🔄 Сыграть еще раз", callback_data="game_crash", style="success")],
        [ColoredInlineKeyboardButton(text="↩️ В игровой зал", callback_data="menu_games", style="primary")]
    ])
    await message.answer(text, reply_markup=kb, parse_mode="HTML")

# ==================== МАГАЗИН TELEGRAM XTR ====================
@router.callback_query(F.data == "menu_store")
async def store_menu(call: CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [ColoredInlineKeyboardButton(text="⭐ Купить 100 звезд (10 XTR)", callback_data="buy_xtr_100", style="success")],
        [ColoredInlineKeyboardButton(text="↩️ Назад в меню", callback_data="menu_main", style="primary")]
    ])
    await call.message.edit_text("⭐ <b>Официальный магазин Telegram XTR & Risestars:</b>", reply_markup=kb, parse_mode="HTML")

@router.callback_query(F.data == "buy_xtr_100")
async def buy_xtr_100(call: CallbackQuery):
    prices = [LabeledPrice(label="100 Risestars", amount=10)]
    await call.message.bot.send_invoice(
        chat_id=call.from_user.id,
        title="Покупка Risestars",
        description="Пополнение баланса на 100 звёзд в экосистеме RiseGram через Telegram Stars",
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
    await message.answer("✅ Оплата прошла успешно! На ваш баланс зачислено +100 ⭐.")

# ==================== ТОП ЛИДЕРОВ И НАСТРОЙКИ ====================
@router.callback_query(F.data == "menu_top")
async def menu_top(call: CallbackQuery):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute("SELECT username, risestars FROM users ORDER BY risestars DESC LIMIT 10")).fetchall()
    
    text = "🏆 <b>Топ-10 игроков экосистемы RiseGram:</b>\n\n"
    for idx, r in enumerate(rows, 1):
        text += f"{idx}. <code>{r['username']}</code> — <b>{r['risestars']} ⭐</b>\n"
        
    kb = InlineKeyboardMarkup(inline_keyboard=[[ColoredInlineKeyboardButton(text="↩️ Назад в меню", callback_data="menu_main", style="primary")]])
    await call.message.edit_text(text, reply_markup=kb, parse_mode="HTML")

@router.callback_query(F.data == "menu_settings")
async def menu_settings(call: CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [ColoredInlineKeyboardButton(text="🌐 Изменить язык / Language", callback_data="set_lang", style="primary")],
        [ColoredInlineKeyboardButton(text="↩️ Назад в меню", callback_data="menu_main", style="primary")]
    ])
    await call.message.edit_text("⚙️ <b>Настройки учетной записи RiseGram:</b>", reply_markup=kb, parse_mode="HTML")

@router.callback_query(F.data == "set_lang")
async def set_lang(call: CallbackQuery):
    await call.answer("🇷🇺 Русский язык установлен по умолчанию.", show_alert=True)

# ==================== ЗАПУСК ЭКОСИСТЕМЫ ====================
async def main():
    await init_db()
    dp.include_router(router)
    dp.include_router(admin_router)
    
    logging.info("🚀 RiseGram Ecosystem Core успешно запущен и принимает запросы!")
    await dp.start_polling(bot, skip_updates=True)

if __name__ == "__main__":
    asyncio.run(main())
    
    await bot.delete_webhook(drop_pending_updates=True)
    
