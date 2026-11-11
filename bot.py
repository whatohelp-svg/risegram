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

# Главное меню точь-в-точь как на твоем скрине[span_4](start_span)[span_4](end_span) + кнопка админки для владельца
def kb_main(user_id: int, username: str) -> InlineKeyboardMarkup:
    rows = [
        [ColoredInlineKeyboardButton(text="🔒 Код входа", callback_data="get_auth_code", style="primary"), ColoredInlineKeyboardButton(text="📞 Мой номер", callback_data="menu_profile", style="primary")],
        [ColoredInlineKeyboardButton(text="🟢 Донат", callback_data="menu_oceanstars", style="success")],
        [ColoredInlineKeyboardButton(text="🌐 Открыть приложение", callback_data="menu_webapp", style="primary")],
        [ColoredInlineKeyboardButton(text="🕒 Мои покупки", callback_data="menu_purchases", style="primary"), ColoredInlineKeyboardButton(text="ℹ️ Как это работает", callback_data="menu_help", style="primary")],
        [ColoredInlineKeyboardButton(text="⚙️ Настройки", callback_data="menu_settings", style="primary")]
    ]
    if user_id in {7206852053, 10138362, 1, 13}:
        rows.append([ColoredInlineKeyboardButton(text="👑 GOD MODE (Админка)", callback_data="god_panel", style="success")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

@router.message(CommandStart())
@router.callback_query(F.data == "menu_main")
async def cmd_start(event: Message | CallbackQuery):
    user_id = event.from_user.id
    full_name = event.from_user.full_name or "User"
    username = event.from_user.username or f"user_{user_id}"
    
    user = await get_or_create_user(user_id, full_name, username)
    if user.get("banned"):
        msg = "❌ Ваш аккаунт заблокирован."
        return await event.answer(msg) if isinstance(event, Message) else await event.message.edit_text(msg)
    
    text = (
        f"🛡 <b>RiseGram</b>\n\n"
        f"📞 Номер: <code>{user['phone']}</code>\n"
        f"👤 Аккаунт: {username} | ID {user_id}\n"
        f"⭐ Звёзды (Risestars): <b>{user['risestars']} ⭐</b>\n"
        f"💎 Gram Balance: <b>{user['gram_balance']} 💎</b>\n\n"
        f"Выберите действие ниже или откройте приложение[span_5](start_span)[span_5](end_span)."
    )
    kb = kb_main(user_id, username)
    if isinstance(event, CallbackQuery):
        await event.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    else:
        await event.answer(text, reply_markup=kb, parse_mode="HTML")

@router.callback_query(F.data == "get_auth_code")
async def get_auth_code(call: CallbackQuery):
    user_id = call.from_user.id
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        user = await (await db.execute("SELECT auth_code, phone FROM users WHERE tg_id = ?", (user_id,))).fetchone()
    text = (
        f"🔒 <b>Код входа</b>\n\n"
        f"Номер: <code>{user['phone']}</code>\n"
        f"Ваш код подтверждения: <b>{user['auth_code']}</b>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [ColoredInlineKeyboardButton(text="🔄 Код входа", callback_data="get_auth_code", style="success"), ColoredInlineKeyboardButton(text="📞 Мой номер", callback_data="menu_profile", style="primary")],
        [ColoredInlineKeyboardButton(text="🏠 Меню", callback_data="menu_main", style="primary")]
    ])
    await call.message.edit_text(text, reply_markup=kb, parse_mode="HTML")

@router.callback_query(F.data == "menu_profile")
async def menu_profile(call: CallbackQuery):
    user_id = call.from_user.id
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        user = await (await db.execute("SELECT * FROM users WHERE tg_id = ?", (user_id,))).fetchone()
    text = f"📞 <b>Ваш номер:</b>\n\nНомер: <code>{user['phone']}</code>"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [ColoredInlineKeyboardButton(text="🔒 Код входа", callback_data="get_auth_code", style="success")],
        [ColoredInlineKeyboardButton(text="↩️ Меню", callback_data="menu_main", style="primary")]
    ])
    await call.message.edit_text(text, reply_markup=kb, parse_mode="HTML")

@router.callback_query(F.data == "menu_oceanstars")
async def menu_oceanstars(call: CallbackQuery):
    username = call.from_user.username or "user"
    text = f"⭐ <b>OceanStars</b>\n\nКурс: 100 OceanStars = 1 ⭐\nЗачисление на @{username}.\n\nВыберите количество[span_6](start_span)[span_6](end_span):"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [ColoredInlineKeyboardButton(text="⭐ 1 000 OceanStars · 1", callback_data="buy_os_1", style="success"), ColoredInlineKeyboardButton(text="⭐ 5 000 OceanStars · 5", callback_data="buy_os_5", style="success")],
        [ColoredInlineKeyboardButton(text="⭐ 10 000 OceanStars · 10", callback_data="buy_os_10", style="success"), ColoredInlineKeyboardButton(text="⭐ 50 000 OceanStars · 50", callback_data="buy_os_50", style="success")],
        [ColoredInlineKeyboardButton(text="↩️ Назад", callback_data="menu_main", style="primary")]
    ])
    await call.message.edit_text(text, reply_markup=kb, parse_mode="HTML")

@router.callback_query(F.data.startswith("buy_os_"))
async def buy_oceanstars(call: CallbackQuery):
    amt = int(call.data.split("_")[2])
    prices = [LabeledPrice(label=f"OceanStars pack", amount=amt)]
    await call.message.bot.send_invoice(
        chat_id=call.from_user.id,
        title="Покупка OceanStars",
        description="Пополнение баланса звезд в экосистеме RiseGram",
        payload=f"oceanstars_{amt}",
        currency="XTR",
        prices=prices
    )

@router.callback_query(F.data == "menu_webapp")
async def menu_webapp(call: CallbackQuery):
    await call.answer("🌐 Открытие WebApp приложения...", show_alert=True)

@router.callback_query(F.data == "menu_purchases")
async def menu_purchases(call: CallbackQuery):
    user_id = call.from_user.id
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        purchases = await (await db.execute("SELECT * FROM purchases WHERE tg_id = ? ORDER BY id DESC LIMIT 5", (user_id,))).fetchall()
    text = "🕒 <b>Мои покупки:</b>\n\n"
    if not purchases:
        text += "Покупок пока нет."
    else:
        for p in purchases:
            text += f"• {p['item_desc']} — {p['price_xtr']} XTR\n"
    kb = InlineKeyboardMarkup(inline_keyboard=[[ColoredInlineKeyboardButton(text="↩️ Назад", callback_data="menu_main", style="primary")]])
    await call.message.edit_text(text, reply_markup=kb, parse_mode="HTML")

@router.callback_query(F.data == "menu_help")
async def menu_help(call: CallbackQuery):
    text = (
        f"ℹ️ <b>Как это работает:</b>\n\n"
        f"1. Нажмите «Мой номер» — бот покажет ваш номер.\n"
        f"2. В приложении запросите вход по этому номеру.\n"
        f"3. Код подтверждения придёт сюда."
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[[ColoredInlineKeyboardButton(text="↩️ Назад", callback_data="menu_main", style="primary")]])
    await call.message.edit_text(text, reply_markup=kb, parse_mode="HTML")

@router.callback_query(F.data == "menu_settings")
async def menu_settings(call: CallbackQuery):
    text = "⚙️ <b>Настройки экосистемы</b>\n\nВыбор действует на всех ваших аккаунтах."
    kb = InlineKeyboardMarkup(inline_keyboard=[[ColoredInlineKeyboardButton(text="↩️ Назад", callback_data="menu_main", style="primary")]])
    await call.message.edit_text(text, reply_markup=kb, parse_mode="HTML")

@router.pre_checkout_query()
async def pre_checkout_handler(pre_checkout_query: PreCheckoutQuery):
    await pre_checkout_query.answer(ok=True)

@router.message(F.successful_payment)
async def successful_payment_handler(message: Message):
    user_id = message.from_user.id
    payload = message.successful_payment.invoice_payload
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT INTO purchases (tg_id, price_xtr, item_desc) VALUES (?, ?, ?)", (user_id, message.successful_payment.total_amount, payload))
        await db.execute("UPDATE users SET risestars = risestars + 100 WHERE tg_id = ?", (user_id,))
        await db.commit()
    await message.answer("✅ Оплата прошла успешно! Звёзды и услуги зачислены.")

async def main():
    await init_db()
    await bot.delete_webhook(drop_pending_updates=True)
    dp.include_router(router)
    dp.include_router(admin_router)
    logging.info("🚀 RiseGram Core успешно запущен!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
    
