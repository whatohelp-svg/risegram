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

# ================= НАСТРОЙКИ =================
BOT_TOKEN = "9054546076:AAEMkzzt4bTZDScM6a4NM2eg-GOvpl6_vgE"
ADMIN_ID = 7206852053
DB_PATH = "risegram.db"

logging.basicConfig(level=logging.INFO)
router = Router()


# ================= СОСТОЯНИЯ (FSM) =================
class ShopStates(StatesGroup):
    waiting_nft_username = State()
    waiting_custom_888 = State()
    waiting_scam_target = State()


class AdminStates(StatesGroup):
    waiting_target = State()
    waiting_custom_value = State()


class GameStates(StatesGroup):
    waiting_dice_bet = State()


# ================= БАЗА ДАННЫХ =================
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                tg_id INTEGER PRIMARY KEY,
                rise_id INTEGER UNIQUE,
                name TEXT,
                username TEXT,
                phone TEXT,
                risestars INTEGER DEFAULT 10000,
                gram_balance REAL DEFAULT 100.0,
                starter_claimed INTEGER DEFAULT 0,
                verified INTEGER DEFAULT 0,
                scam_mark INTEGER DEFAULT 0,
                price_mode TEXT DEFAULT 'rub',
                auth_code TEXT DEFAULT NULL
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS purchases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tg_id INTEGER,
                title TEXT,
                price_xtr INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.commit()


def gen_phone() -> str:
    return f"+7 982 {random.randint(100, 999)}-{random.randint(10, 99)}-{random.randint(10, 99)}"


async def get_or_create_user(tg_id: int, full_name: str, username: str | None):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM users WHERE tg_id = ?", (tg_id,))
        user = await cur.fetchone()
        if not user:
            rise_id = random.randint(100000000, 999999999)
            phone = gen_phone()
            uname = f"@{username}" if username else f"@user_{tg_id}"
            await db.execute(
                "INSERT INTO users (tg_id, rise_id, name, username, phone) VALUES (?, ?, ?, ?, ?)",
                (tg_id, rise_id, full_name, uname, phone),
            )
            await db.commit()
            cur = await db.execute("SELECT * FROM users WHERE tg_id = ?", (tg_id,))
            user = await cur.fetchone()
        return dict(user)


# ================= ИНТЕРФЕЙС И КЛАВИАТУРЫ (ЗЕЛЕНЫЙ СТИЛЬ) =================
def kb_main_menu(user_id: int) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(text="👥 Смотреть анкеты", callback_data="menu_code"),
            InlineKeyboardButton(text="👤 Профиль", callback_data="menu_phone"),
        ],
        [
            InlineKeyboardButton(text="🌐 Лента", callback_data="menu_purchases"),
            InlineKeyboardButton(text="⚡ Задания", callback_data="menu_help"),
        ],
        [
            InlineKeyboardButton(text="⭐ Магазин", callback_data="menu_shop"),
        ],
        [
            InlineKeyboardButton(text="🚀 Игры (Gram & Stars)", callback_data="menu_games"),
            InlineKeyboardButton(text="⚙️ Настройки", callback_data="menu_settings"),
        ],
    ]
    if user_id == ADMIN_ID:
        rows.append([InlineKeyboardButton(text="👑 Панель создателя", callback_data="open_admin")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_shop_menu(starter_claimed: int) -> InlineKeyboardMarkup:
    starter_text = "🟢 Звёзды (Получено)" if starter_claimed else "🟢 Звёзды (1000 ⭐ • 10 TG ⭐)"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=starter_text, callback_data="buy_starter")],
        [
            InlineKeyboardButton(text="🔵 Верификация (50 ⭐)", callback_data="buy_verify"),
            InlineKeyboardButton(text="🔴 Метка SCAM (15 ⭐)", callback_data="buy_scam"),
        ],
        [
            InlineKeyboardButton(text="📞 Номера +888 (от 25 ⭐)", callback_data="shop_888"),
            InlineKeyboardButton(text="🏷 NFT-юзернеймы", callback_data="shop_nft"),
        ],
        [InlineKeyboardButton(text="↩️ Назад", callback_data="back_main")],
    ])


# ================= ГЛАВНОЕ МЕНЮ И КОМАНДЫ =================
@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    u = await get_or_create_user(message.from_user.id, message.from_user.full_name, message.from_user.username)
    badge = " ☑️" if u["verified"] else ""
    scam = " [SCAM]" if u["scam_mark"] else ""
    text = (
        f"💎 <b>RiseGram Ecosystem</b>\n\n"
        f"📞 Номер: <code>{u['phone']}</code>\n"
        f"👤 Аккаунт: <b>{u['name']}{badge}{scam}</b> • ID <code>{u['rise_id']}</code>\n"
        f"🏷 Юзернеймы: <code>{u['username']}</code>\n"
        f"⭐ Звёзды: <b>{u['risestars']:,} ⭐</b>\n"
        f"💎 Баланс Gram: <b>{u['gram_balance']:,.2f} 💎</b>\n\n"
        f"Выберите действие ниже:"
    )
    await message.answer(text, reply_markup=kb_main_menu(message.from_user.id), parse_mode="HTML")


@router.callback_query(F.data == "back_main")
async def cb_back_main(call: CallbackQuery, state: FSMContext):
    await state.clear()
    u = await get_or_create_user(call.from_user.id, call.from_user.full_name, call.from_user.username)
    badge = " ☑️" if u["verified"] else ""
    scam = " [SCAM]" if u["scam_mark"] else ""
    text = (
        f"💎 <b>RiseGram Ecosystem</b>\n\n"
        f"📞 Номер: <code>{u['phone']}</code>\n"
        f"👤 Аккаунт: <b>{u['name']}{badge}{scam}</b> • ID <code>{u['rise_id']}</code>\n"
        f"🏷 Юзернеймы: <code>{u['username']}</code>\n"
        f"⭐ Звёзды: <b>{u['risestars']:,} ⭐</b>\n"
        f"💎 Баланс Gram: <b>{u['gram_balance']:,.2f} 💎</b>\n\n"
        f"Выберите действие ниже:"
    )
    await call.message.edit_text(text, reply_markup=kb_main_menu(call.from_user.id), parse_mode="HTML")


# ================= РАЗДЕЛ ИГР =================
@router.callback_query(F.data == "menu_games")
async def cb_games(call: CallbackQuery, state: FSMContext):
    await state.clear()
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚀 Ракета (Crash Game)", callback_data="game_rocket")],
        [InlineKeyboardButton(text="🎲 Бросок кубика на ставку", callback_data="game_dice_menu")],
        [InlineKeyboardButton(text="🪙 Орёл и Решка", callback_data="game_coin")],
        [InlineKeyboardButton(text="↩️ Назад", callback_data="back_main")],
    ])
    await call.message.edit_text(
        "🚀 <b>Игровой зал RiseGram</b>\n\nСтавь кристаллы или звёзды, умножай баланс прямо в боте!",
        reply_markup=kb, parse_mode="HTML"
    )


@router.callback_query(F.data == "game_rocket")
async def cb_game_rocket(call: CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🟢 Ставка 100 ⭐", callback_data="rocket_bet:100")],
        [InlineKeyboardButton(text="🟢 Ставка 500 ⭐", callback_data="rocket_bet:500")],
        [InlineKeyboardButton(text="🟢 Ставка 1 000 ⭐", callback_data="rocket_bet:1000")],
        [InlineKeyboardButton(text="↩️ К играм", callback_data="menu_games")],
    ])
    await call.message.edit_text("🚀 <b>Ракета Crash</b>\n\nВыберите сумму ставки в звёздах:", reply_markup=kb, parse_mode="HTML")


@router.callback_query(F.data.startswith("rocket_bet:"))
async def cb_rocket_play(call: CallbackQuery):
    bet = int(call.data.split(":")[1])
    u = await get_or_create_user(call.from_user.id, call.from_user.full_name, call.from_user.username)
    if u["risestars"] < bet:
        return await call.answer(f"❌ Недостаточно средств! Нужно {bet} ⭐", show_alert=True)
    mult = round(random.uniform(1.1, 6.0), 2)
    won = mult >= 2.0
    async with aiosqlite.connect(DB_PATH) as db:
        if won:
            gain = int(bet * mult)
            await db.execute("UPDATE users SET risestars = risestars + ? WHERE tg_id = ?", (gain - bet, call.from_user.id))
            res = f"🚀 Ракета долетела до x<b>{mult}</b>!\n🎉 Вы выиграли <b>{gain:,} ⭐</b>!"
        else:
            await db.execute("UPDATE users SET risestars = risestars - ? WHERE tg_id = ?", (bet, call.from_user.id))
            res = f"💥 Ракета взорвалась на x<b>{mult}</b>...\n💸 Вы потеряли {bet} ⭐."
        await db.commit()
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Играть снова", callback_data="game_rocket")],
        [InlineKeyboardButton(text="↩️ К играм", callback_data="menu_games")],
    ])
    await call.message.edit_text(res, reply_markup=kb, parse_mode="HTML")


@router.callback_query(F.data == "game_dice_menu")
async def cb_game_dice_menu(call: CallbackQuery, state: FSMContext):
    await state.set_state(GameStates.waiting_dice_bet)
    await call.message.edit_text(
        "🎲 <b>Кубик удачи</b>\n\nВведите сумму ставки в звёздах (цифрами):",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="menu_games")]]), parse_mode="HTML"
    )


@router.message(GameStates.waiting_dice_bet)
async def process_dice_bet(message: Message, state: FSMContext):
    try:
        bet = int(message.text.strip())
        if bet <= 0: raise ValueError()
    except ValueError:
        return await message.answer("❌ Введите корректное число для ставки:")
    u = await get_or_create_user(message.from_user.id, message.from_user.full_name, message.from_user.username)
    if u["risestars"] < bet:
        return await message.answer(f"❌ У вас всего {u['risestars']:,} ⭐. Введите меньше:")
    await state.clear()
    msg = await message.answer_dice(emoji="🎲")
    await asyncio.sleep(3)
    val = msg.dice.value
    async with aiosqlite.connect(DB_PATH) as db:
        if val >= 4:
            win = bet * 2
            await db.execute("UPDATE users SET risestars = risestars + ? WHERE tg_id = ?", (bet, message.from_user.id))
            res = f"🎲 Выпало: <b>{val}</b> (Победа от 4 и выше!)\n🎉 Вы выиграли <b>{win:,} ⭐</b>!"
        else:
            await db.execute("UPDATE users SET risestars = risestars - ? WHERE tg_id = ?", (bet, message.from_user.id))
            res = f"🎲 Выпало: <b>{val}</b> (Проигрыш...)\n💸 Вы потеряли {bet:,} ⭐."
        await db.commit()
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎲 Бросить ещё раз", callback_data="game_dice_menu")],
        [InlineKeyboardButton(text="↩️ К играм", callback_data="menu_games")],
    ])
    await message.answer(res, reply_markup=kb, parse_mode="HTML")


@router.callback_query(F.data == "game_coin")
async def cb_game_coin(call: CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🟢 Орёл (x2)", callback_data="coin_flip:eagle")],
        [InlineKeyboardButton(text="🔵 Решка (x2)", callback_data="coin_flip:tail")],
        [InlineKeyboardButton(text="↩️ К играм", callback_data="menu_games")],
    ])
    await call.message.edit_text("🪙 <b>Орёл и Решка</b>\n\nСтавка: 200 ⭐. Выберите сторону:", reply_markup=kb, parse_mode="HTML")


@router.callback_query(F.data.startswith("coin_flip:"))
async def cb_coin_result(call: CallbackQuery):
    side = call.data.split(":")[1]
    bet = 200
    u = await get_or_create_user(call.from_user.id, call.from_user.full_name, call.from_user.username)
    if u["risestars"] < bet:
        return await call.answer("❌ Нужен баланс от 200 ⭐", show_alert=True)
    result = random.choice(["eagle", "tail"])
    won = (side == result)
    async with aiosqlite.connect(DB_PATH) as db:
        if won:
            await db.execute("UPDATE users SET risestars = risestars + ? WHERE tg_id = ?", (bet, call.from_user.id))
            res = f"🪙 Выпал {'Орёл 🟢' if result=='eagle' else 'Решка 🔵'}!\n🎉 Вы выиграли <b>{bet*2} ⭐</b>!"
        else:
            await db.execute("UPDATE users SET risestars = risestars - ? WHERE tg_id = ?", (bet, call.from_user.id))
            res = f"🪙 Выпал {'Орёл 🟢' if result=='eagle' else 'Решка 🔵'}.\n💸 Вы проиграли {bet} ⭐."
        await db.commit()
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Ещё раз", callback_data="game_coin")],
        [InlineKeyboardButton(text="↩️ К играм", callback_data="menu_games")],
    ])
    await call.message.edit_text(res, reply_markup=kb, parse_mode="HTML")


# ================= КОД ВХОДА И НОМЕР =================
@router.message(Command("code"))
@router.callback_query(F.data == "menu_code")
async def show_code(event: Message | CallbackQuery):
    tg_id = event.from_user.id
    u = await get_or_create_user(tg_id, event.from_user.full_name, event.from_user.username)
    code_status = f"🔑 Ваш код входа: <code>{u['auth_code']}</code>" if u["auth_code"] else "Кода пока нет."
    text = f"🔒 <b>Анкеты и авторизация</b>\n\nНомер: <code>{u['phone']}</code>\n{code_status}"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔵 Обновить", callback_data="menu_code"), InlineKeyboardButton(text="📞 Мой номер", callback_data="menu_phone")],
        [InlineKeyboardButton(text="🏠 Меню", callback_data="back_main")],
    ])
    if isinstance(event, CallbackQuery):
        await event.answer("Обновлено")
        await event.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    else:
        await event.answer(text, reply_markup=kb, parse_mode="HTML")


@router.callback_query(F.data == "menu_phone")
async def show_phone(call: CallbackQuery):
    u = await get_or_create_user(call.from_user.id, call.from_user.full_name, call.from_user.username)
    text = f"📞 <b>Ваш профиль</b>\n\nНомер: <code>{u['phone']}</code>\nЮзернеймы: <code>{u['username']}</code>"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔵 Код входа", callback_data="menu_code")],
        [InlineKeyboardButton(text="🏠 Меню", callback_data="back_main")],
    ])
    await call.message.edit_text(text, reply_markup=kb, parse_mode="HTML")


# ================= ДОНАТ-ВИТРИНА =================
@router.message(Command("shop"))
@router.callback_query(F.data == "menu_shop")
async def show_shop(event: Message | CallbackQuery):
    u = await get_or_create_user(event.from_user.id, event.from_user.full_name, event.from_user.username)
    text = (
        "💎 <b>Магазин RiseGram</b>\n\n"
        "Оплата звёздами Telegram (XTR).\n\n"
        "🟢 <b>Звёзды</b> — 1 000 ⭐ (10 TG ⭐)\n"
        "🔵 <b>Верификация</b> — Мажор (50 TG ⭐)\n"
        "🔴 <b>Метка SCAM</b> — (15 TG ⭐)\n"
        "📞 <b>Номера +888</b> — (от 25 TG ⭐)\n"
        "🏷 <b>NFT-юзернеймы</b> — (от 10 TG ⭐)"
    )
    kb = kb_shop_menu(u["starter_claimed"])
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
        return await call.answer("❌ Вы уже забрали стартовый пак!", show_alert=True)
    await call.answer()
    await send_stars_invoice(call.bot, call.from_user.id, "1 000 Звёзд", "Стартовый пак", "starter_1000", 10)


@router.callback_query(F.data == "buy_verify")
async def cb_buy_verify(call: CallbackQuery):
    await call.answer()
    await send_stars_invoice(call.bot, call.from_user.id, "Верификация • Мажор", "Галочка", "verify_major", 50)


@router.callback_query(F.data == "buy_scam")
async def cb_buy_scam(call: CallbackQuery, state: FSMContext):
    await state.set_state(ShopStates.waiting_scam_target)
    await call.message.edit_text("🔴 <b>Метка SCAM • 15 TG ⭐</b>\n\nОтправьте @username:", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="menu_shop")]]), parse_mode="HTML")


@router.message(ShopStates.waiting_scam_target)
async def process_scam_target(message: Message, state: FSMContext):
    target = message.text.strip()
    await state.clear()
    await send_stars_invoice(message.bot, message.from_user.id, f"Метка SCAM на {target}", "Мошенник", f"scam:{target}", 15)


@router.callback_query(F.data == "shop_888")
async def cb_shop_888(call: CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🟢 +888 8 XXX • 50 ⭐", callback_data="buy_888_vip")],
        [InlineKeyboardButton(text="🔵 +888 0XXX XXXX • 25 ⭐", callback_data="buy_888_std")],
        [InlineKeyboardButton(text="🟡 Свой номер • 75 ⭐", callback_data="buy_888_custom")],
        [InlineKeyboardButton(text="↩️ Назад", callback_data="menu_shop")],
    ])
    await call.message.edit_text("📞 <b>Анонимные номера +888</b>", reply_markup=kb, parse_mode="HTML")


@router.callback_query(F.data.in_({"buy_888_vip", "buy_888_std"}))
async def cb_buy_888_preset(call: CallbackQuery):
    await call.answer()
    num = f"+888 8 {random.randint(100, 999)}" if call.data == "buy_888_vip" else f"+888 0{random.randint(100, 999)} {random.randint(1000, 9999)}"
    await send_stars_invoice(call.bot, call.from_user.id, f"Номер {num}", "Номер +888", f"num888:{num}", 50 if call.data == "buy_888_vip" else 25)


@router.callback_query(F.data == "buy_888_custom")
async def cb_buy_888_custom(call: CallbackQuery, state: FSMContext):
    await state.set_state(ShopStates.waiting_custom_888)
    await call.message.edit_text("📝 Введите цифры после <code>+888</code>:", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="shop_888")]]), parse_mode="HTML")


@router.message(ShopStates.waiting_custom_888)
async def process_custom_888(message: Message, state: FSMContext):
    num = f"+888 {message.text.strip().replace('+888', '').strip()}"
    await state.clear()
    await send_stars_invoice(message.bot, message.from_user.id, f"Номер {num}", "Кастомный +888", f"num888:{num}", 75)


@router.callback_query(F.data == "shop_nft")
async def cb_shop_nft(call: CallbackQuery, state: FSMContext):
    await state.set_state(ShopStates.waiting_nft_username)
    await call.message.edit_text("🏷 <b>NFT-юзернеймы</b>\n\nОтправьте юзернейм:", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="menu_shop")]]), parse_mode="HTML")


@router.message(ShopStates.waiting_nft_username)
async def process_nft_username(message: Message, state: FSMContext):
    raw = message.text.strip().lstrip("@")
    if len(raw) < 4:
        return await message.answer("⚠️ Минимум 4 символа!")
    price = 14 if len(raw) == 4 else 10
    await state.clear()
    await send_stars_invoice(message.bot, message.from_user.id, f"NFT @{raw}", "NFT юзернейм", f"nft:@{raw}", price)


@router.pre_checkout_query()
async def on_pre_checkout(query: PreCheckoutQuery):
    await query.answer(ok=True)


@router.message(F.successful_payment)
async def on_successful_payment(message: Message):
    pay = message.successful_payment
    payload = pay.invoice_payload
    stars_paid = pay.total_amount
    tg_id = message.from_user.id

    async with aiosqlite.connect(DB_PATH) as db:
        if payload == "starter_1000":
            await db.execute("UPDATE users SET risestars = risestars + 1000, starter_claimed = 1 WHERE tg_id = ?", (tg_id,))
            title = "1 000 Звёзд"
        elif payload == "verify_major":
            await db.execute("UPDATE users SET verified = 1 WHERE tg_id = ?", (tg_id,))
            title = "Верификация (Мажор)"
        elif payload.startswith("scam:"):
            target = payload.split(":", 1)[1]
            await db.execute("UPDATE users SET scam_mark = 1 WHERE username LIKE ?", (f"%{target}%",))
            title = f"Метка SCAM ({target})"
        elif payload.startswith("num888:"):
            num = payload.split(":", 1)[1]
            await db.execute("UPDATE users SET phone = ? WHERE tg_id = ?", (num, tg_id))
            title = f"Номер {num}"
        elif payload.startswith("nft:"):
            uname = payload.split(":", 1)[1]
            cur = await db.execute("SELECT username FROM users WHERE tg_id = ?", (tg_id,))
            row = await cur.fetchone()
            old_unames = row[0] if row and row[0] else ""
            if uname not in old_unames:
                new_unames = f"{old_unames}, {uname}" if old_unames else uname
            else:
                new_unames = old_unames
            await db.execute("UPDATE users SET username = ? WHERE tg_id = ?", (new_unames, tg_id))
            title = f"NFT-юз {uname}"
        else:
            title = payload

        await db.execute("INSERT INTO purchases (tg_id, title, price_xtr) VALUES (?, ?, ?)", (tg_id, title, stars_paid))
        await db.commit()

    await message.answer(f"✅ <b>Оплата прошла успешно!</b>\nВыдано: <b>{title}</b>", reply_markup=kb_main_menu(tg_id), parse_mode="HTML")


@router.callback_query(F.data == "menu_purchases")
async def cb_purchases(call: CallbackQuery):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT title, price_xtr, created_at FROM purchases WHERE tg_id = ? ORDER BY id DESC LIMIT 10", (call.from_user.id,))
        rows = await cur.fetchall()
    text = "🌐 <b>Лента активности</b>\n\n" + ("Покупок пока нет." if not rows else "\n".join([f"• {r[0]} — <b>{r[1]} ⭐</b>" for r in rows]))
    await call.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="↩️ Назад", callback_data="back_main")]]), parse_mode="HTML")


@router.callback_query(F.data == "menu_settings")
async def cb_settings(call: CallbackQuery):
    u = await get_or_create_user(call.from_user.id, call.from_user.full_name, call.from_user.username)
    mode_name = "Рубли" if u["price_mode"] == "rub" else "Telegram"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✓ Рубли" if u["price_mode"] == "rub" else "Рубли", callback_data="set_mode_rub"),
         InlineKeyboardButton(text="✓ Telegram" if u["price_mode"] == "tg" else "Telegram", callback_data="set_mode_tg")],
        [InlineKeyboardButton(text="↩️ Назад", callback_data="back_main")]
    ])
    await call.message.edit_text(f"⚙️ <b>Настройки</b>\n\nРежим: <b>{mode_name}</b>", reply_markup=kb, parse_mode="HTML")


@router.callback_query(F.data.in_({"set_mode_tg", "set_mode_rub"}))
async def cb_toggle_mode(call: CallbackQuery):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET price_mode = ? WHERE tg_id = ?", ("tg" if call.data == "set_mode_tg" else "rub", call.from_user.id))
        await db.commit()
    await call.answer("Сохранено!")
    await cb_settings(call)


@router.callback_query(F.data == "menu_help")
async def cb_help(call: CallbackQuery):
    text = "⚡ <b>Задания и квесты</b>\n\nВыполняй задания внутри экосистемы RiseGram и получай бонусы."
    await call.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="↩️ Назад", callback_data="back_main")]]), parse_mode="HTML")


# ================= ПАНЕЛЬ СОЗДАТЕЛЯ (АДМИНКА) =================
@router.message(Command("admin7206852053"))
@router.callback_query(F.data == "open_admin")
async def cmd_secret_admin(event: Message | CallbackQuery, state: FSMContext):
    if event.from_user.id != ADMIN_ID: return
    await state.clear()
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Статистика", callback_data="adm_stats"), InlineKeyboardButton(text="👥 Список юзеров", callback_data="adm_users")],
        [InlineKeyboardButton(text="🟢 Выдать Звёзды / Кристаллы Gram", callback_data="adm_grant_menu")],
    ])
    text = "👑 <b>Панель создателя RiseGram</b>\n\nВыбирай нужное действие:"
    if isinstance(event, CallbackQuery):
        await event.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    else:
        await event.answer(text, reply_markup=kb, parse_mode="HTML")


@router.callback_query(F.data == "adm_stats")
async def cb_adm_stats(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID: return
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT COUNT(*) FROM users")
        users_count = (await cur.fetchone())[0]
        cur = await db.execute("SELECT SUM(price_xtr) FROM purchases")
        total_xtr = (await cur.fetchone())[0] or 0
    await call.message.edit_text(f"📊 <b>Статистика</b>\n\n👥 Игроков: <b>{users_count}</b>\n⭐ Собрано XTR: <b>{total_xtr} ⭐</b>", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="↩️ В админку", callback_data="open_admin")]]), parse_mode="HTML")


@router.callback_query(F.data == "adm_users")
async def cb_adm_users(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID: return
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT tg_id, name, username, risestars, gram_balance FROM users ORDER BY tg_id DESC LIMIT 10")
        rows = await cur.fetchall()
    text = "👥 <b>Игроки в базе:</b>\n\n" + "\n".join([f"• <code>{r['username']}</code> | ⭐ {r['risestars']} | 💎 {r['gram_balance']}" for r in rows])
    await call.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="↩️ В админку", callback_data="open_admin")]]), parse_mode="HTML")


@router.callback_query(F.data == "adm_grant_menu")
async def cb_adm_grant_menu(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID: return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⭐ Звёзды (любое кол-во)", callback_data="grant:stars")],
        [InlineKeyboardButton(text="💎 Кристаллы Gram (💎)", callback_data="grant:gram")],
        [InlineKeyboardButton(text="🔵 Верификация", callback_data="grant:verify")],
        [InlineKeyboardButton(text="🔴 Метка SCAM", callback_data="grant:scam")],
        [InlineKeyboardButton(text="📞 Номер +888 (кастом)", callback_data="grant:num888")],
        [InlineKeyboardButton(text="🏷 NFT-юзернейм", callback_data="grant:nft")],
        [InlineKeyboardButton(text="↩️ В админку", callback_data="open_admin")],
    ])
    await call.message.edit_text("🎁 <b>Что выдать?</b>", reply_markup=kb, parse_mode="HTML")


@router.callback_query(F.data.startswith("grant:"))
async def cb_grant_select(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    action = call.data.split(":")[1]
    await state.update_data(grant_action=action)
    await state.set_state(AdminStates.waiting_target)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⭐ Выдать себе (@koyzzov)", callback_data="grant_to_me")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="open_admin")]
    ])
    await call.message.edit_text(
        f"🎯 Выбрано: <code>{action}</code>\n\n"
        "Введите <b>юзернейм ТГ</b> игрока (например <code>@koyzzov</code>) или нажмите кнопку ниже:",
        reply_markup=kb, parse_mode="HTML"
    )


@router.callback_query(F.data == "grant_to_me")
async def cb_grant_to_me(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT username FROM users WHERE tg_id = ?", (ADMIN_ID,))
        res = await cur.fetchone()
        uname = res["username"].split(",")[0].strip() if res and res["username"] else f"@user_{ADMIN_ID}"
    class FakeMsg:
        text = uname
        from_user = call.from_user
        bot = call.bot
        async def answer(self, *a, **kw): return await call.message.answer(*a, **kw)
    await process_grant_target(FakeMsg(), state)


@router.message(AdminStates.waiting_target)
async def process_grant_target(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID: return
    target_uname = message.text.strip()
    if not target_uname.startswith("@"):
        target_uname = f"@{target_uname}"
    await state.update_data(target_uname=target_uname)
    data = await state.get_data()
    action = data.get("grant_action")

    if action in ("stars", "gram"):
        await state.set_state(AdminStates.waiting_custom_value)
        curr_name = "Звёзд ⭐" if action == "stars" else "Кристаллов Gram 💎"
        return await message.answer(
            f"🎯 Цель: <b>{target_uname}</b>\n\nВведите <b>количество {curr_name}</b> для выдачи:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="open_admin")]]), parse_mode="HTML"
        )

    if action in ("num888", "nft"):
        await state.set_state(AdminStates.waiting_custom_value)
        prompt = (
            f"📞 Цель: <b>{target_uname}</b>\n\nВведите номер (например: <code>+888 8 9999</code>):"
            if action == "num888"
            else f"🏷 Цель: <b>{target_uname}</b>\n\nВведите юзернейм (например: <code>@rise</code>):"
        )
        return await message.answer(prompt, reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="open_admin")]]), parse_mode="HTML")

    await execute_final_grant(message, state, target_uname, None)


@router.message(AdminStates.waiting_custom_value)
async def process_custom_value(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID: return
    val = message.text.strip()
    data = await state.get_data()
    target_uname = data.get("target_uname")
    await execute_final_grant(message, state, target_uname, val)


async def execute_final_grant(message: Message, state: FSMContext, target_uname: str, val: str | None):
    data = await state.get_data()
    action = data.get("grant_action")
    await state.clear()

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM users WHERE username LIKE ?", (f"%{target_uname}%",))
        user = await cur.fetchone()
        
        if not user:
            target_id = random.randint(200000000, 999999999)
            rise_id = random.randint(100000000, 999999999)
            phone = gen_phone()
            await db.execute(
                "INSERT INTO users (tg_id, rise_id, name, username, phone) VALUES (?, ?, ?, ?, ?)",
                (target_id, rise_id, f"Игрок {target_uname}", target_uname, phone)
            )
            await db.commit()
            cur = await db.execute("SELECT * FROM users WHERE username LIKE ?", (f"%{target_uname}%",))
            user = await cur.fetchone()

        target_id = user["tg_id"]

        if action == "stars":
            try: amount = int(val)
            except ValueError: amount = 1000
            await db.execute("UPDATE users SET risestars = risestars + ? WHERE tg_id = ?", (amount, target_id))
            msg_text = f"🎁 Вам начислено <b>{amount:,} Звёзд ⭐</b> от создателя!"
        elif action == "gram":
            try: amount_gram = float(val)
            except ValueError: amount_gram = 10.0
            await db.execute("UPDATE users SET gram_balance = gram_balance + ? WHERE tg_id = ?", (amount_gram, target_id))
            msg_text = f"🎁 Вам начислено <b>{amount_gram:,.2f} 💎 Gram</b> от создателя!"
        elif action == "verify":
            await db.execute("UPDATE users SET verified = 1 WHERE tg_id = ?", (target_id,))
            msg_text = "🎁 Ваш аккаунт получил официальную <b>верификацию</b>!"
        elif action == "scam":
            await db.execute("UPDATE users SET scam_mark = 1 WHERE tg_id = ?", (target_id,))
            msg_text = "⚠️ На ваш аккаунт установлена <b>метка SCAM</b>."
        elif action == "num888":
            num = val if val.startswith("+888") else f"+888 {val}"
            await db.execute("UPDATE users SET phone = ? WHERE tg_id = ?", (num, target_id))
            msg_text = f"🎁 Вам выдан эксклюзивный номер: <b>{num}</b>"
        elif action == "nft":
            uname = val if val.startswith("@") else f"@{val}"
            old_unames = user["username"] if user["username"] else ""
            if uname not in old_unames:
                new_unames = f"{old_unames}, {uname}" if old_unames else uname
            else:
                new_unames = old_unames
            await db.execute("UPDATE users SET username = ? WHERE tg_id = ?", (new_unames, target_id))
            msg_text = f"🎁 Вам выдан NFT-юзернейм: <b>{uname}</b>\nВсе ваши юзы: <b>{new_unames}</b>"
        await db.commit()

    try:
        await message.bot.send_message(target_id, msg_text, parse_mode="HTML")
        notified = " (уведомление доставлено)"
    except Exception:
        notified = " (бот не в ЛС у юзера)"

    await message.answer(f"✅ <b>Успешно выдано для {target_uname}</b>{notified}", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="👑 В админку", callback_data="open_admin")]]), parse_mode="HTML")


async def main():
    await init_db()
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()
    dp.include_router(router)
    print("Бот RiseGram успешно запущен!")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
