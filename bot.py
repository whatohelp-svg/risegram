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
    WebAppInfo,
)

# ================= НАСТРОЙКИ =================
BOT_TOKEN = "9054546076:AAEMkzzt4bTZDScM6a4NM2eg-GOvpl6_vgE"
ADMIN_ID = 7206852053
WEBAPP_URL = "https://whatphelp-svg.github.io/risegram/"
DB_PATH = "risegram.db"

logging.basicConfig(level=logging.INFO)
router = Router()


class ShopStates(StatesGroup):
    waiting_nft_username = State()
    waiting_custom_888 = State()
    waiting_scam_target = State()
    waiting_admin_grant = State()

class AdminStates(StatesGroup):
    waiting_grant_target = State()



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
                risestars INTEGER DEFAULT 0,
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


# ================= КЛАВИАТУРЫ =================
def kb_main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🔒 Код входа", callback_data="menu_code"),
            InlineKeyboardButton(text="📞 Мой номер", callback_data="menu_phone"),
        ],
        [
            InlineKeyboardButton(text="💎 Донат", callback_data="menu_shop"),
        ],
        [
            InlineKeyboardButton(
                text="📱 Открыть приложение",
                web_app=WebAppInfo(url=WEBAPP_URL)
            )
        ],
        [
            InlineKeyboardButton(text="🕒 Мои покупки", callback_data="menu_purchases"),
            InlineKeyboardButton(text="ℹ️ Как это работает", callback_data="menu_help"),
        ],
        [
            InlineKeyboardButton(text="⚙️ Настройки", callback_data="menu_settings"),
        ],
    ])


def kb_shop_menu(starter_claimed: int) -> InlineKeyboardMarkup:
    starter_text = (
        "✅ RiseStars (1000 ⭐ • Получено 1/1)"
        if starter_claimed
        else "⭐ RiseStars (1000 ⭐ • 10 TG ⭐ [0/1])"
    )
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=starter_text, callback_data="buy_starter")],
        [
            InlineKeyboardButton(text="☑️ Верификация (50 ⭐)", callback_data="buy_verify"),
            InlineKeyboardButton(text="⚠️ Метка SCAM (15 ⭐)", callback_data="buy_scam"),
        ],
        [
            InlineKeyboardButton(text="📞 Номера +888 (от 25 ⭐)", callback_data="shop_888"),
            InlineKeyboardButton(text="🏷 NFT-юзернеймы", callback_data="shop_nft"),
        ],
        [
            InlineKeyboardButton(
                text="📱 Открыть приложение",
                web_app=WebAppInfo(url=WEBAPP_URL)
            )
        ],
        [InlineKeyboardButton(text="↩️ Назад", callback_data="back_main")],
    ])


# ================= ГЛАВНОЕ МЕНЮ И КОМАНДЫ =================
@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    u = await get_or_create_user(
        message.from_user.id,
        message.from_user.full_name,
        message.from_user.username,
    )
    badge = " ☑️" if u["verified"] else ""
    scam = " [SCAM]" if u["scam_mark"] else ""
    text = (
        f"📱 <b>RiseGram</b>\n\n"
        f"📞 Номер: <code>{u['phone']}</code>\n"
        f"👤 Аккаунт: <b>{u['name']}{badge}{scam}</b> • ID <code>{u['rise_id']}</code>\n"
        f"⭐ Баланс: <b>{u['risestars']:,} RiseStars</b>\n\n"
        f"Выберите действие ниже или откройте приложение."
    )
    await message.answer(text, reply_markup=kb_main_menu(), parse_mode="HTML")


@router.callback_query(F.data == "back_main")
async def cb_back_main(call: CallbackQuery, state: FSMContext):
    await state.clear()
    u = await get_or_create_user(
        call.from_user.id,
        call.from_user.full_name,
        call.from_user.username,
    )
    badge = " ☑️" if u["verified"] else ""
    scam = " [SCAM]" if u["scam_mark"] else ""
    text = (
        f"📱 <b>RiseGram</b>\n\n"
        f"📞 Номер: <code>{u['phone']}</code>\n"
        f"👤 Аккаунт: <b>{u['name']}{badge}{scam}</b> • ID <code>{u['rise_id']}</code>\n"
        f"⭐ Баланс: <b>{u['risestars']:,} RiseStars</b>\n\n"
        f"Выберите действие ниже или откройте приложение."
    )
    await call.message.edit_text(text, reply_markup=kb_main_menu(), parse_mode="HTML")


# ================= КОД ВХОДА И НОМЕР =================
@router.message(Command("code"))
@router.callback_query(F.data == "menu_code")
async def show_code(event: Message | CallbackQuery):
    tg_id = event.from_user.id
    u = await get_or_create_user(tg_id, event.from_user.full_name, event.from_user.username)
    code_status = (
        f"🔑 Ваш код входа: <code>{u['auth_code']}</code>"
        if u["auth_code"]
        else "Кода пока нет. Запросите вход по этому номеру в приложении — код придёт сюда."
    )
    text = f"🔒 <b>Код входа</b>\n\nНомер: <code>{u['phone']}</code>\n{code_status}"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🔄 Код входа", callback_data="menu_code"),
            InlineKeyboardButton(text="📞 Мой номер", callback_data="menu_phone"),
        ],
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
    text = f"📞 <b>Ваш номер</b>\n\nНомер: <code>{u['phone']}</code>"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔒 Код входа", callback_data="menu_code")],
        [InlineKeyboardButton(text="🏠 Меню", callback_data="back_main")],
    ])
    await call.message.edit_text(text, reply_markup=kb, parse_mode="HTML")


# ================= ДОНАТ-ВИТРИНА (TELEGRAM STARS) =================
@router.message(Command("shop"))
@router.callback_query(F.data == "menu_shop")
async def show_shop(event: Message | CallbackQuery):
    u = await get_or_create_user(
        event.from_user.id,
        event.from_user.full_name,
        event.from_user.username,
    )
    text = (
        "💎 <b>Донат RiseGram</b>\n\n"
        "Оплата звёздами Telegram, выдача — сразу после оплаты.\n\n"
        "⭐ <b>RiseStars</b> — стартовый пак 1 000 ⭐ (1 раз на аккаунт)\n"
        "☑️ <b>Верификация</b> — Мажор для аккаунта или канала (50 TG ⭐)\n"
        "⚠️ <b>Метка SCAM</b> — пометить мошенника (15 TG ⭐)\n"
        "📞 <b>Номера +888</b> — анонимный номер (от 25 TG ⭐)\n"
        "🏷 <b>NFT-юзернеймы</b> — коллекционный @username (от 10 TG ⭐)"
    )
    kb = kb_shop_menu(u["starter_claimed"])
    if isinstance(event, CallbackQuery):
        await event.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    else:
        await event.answer(text, reply_markup=kb, parse_mode="HTML")


async def send_stars_invoice(bot: Bot, chat_id: int, title: str, desc: str, payload: str, stars: int):
    await bot.send_invoice(
        chat_id=chat_id,
        title=title,
        description=desc,
        payload=payload,
        currency="XTR",  # Официальная валюта Telegram Stars
        prices=[LabeledPrice(label=title, amount=stars)],
    )


@router.callback_query(F.data == "buy_starter")
async def cb_buy_starter(call: CallbackQuery):
    u = await get_or_create_user(call.from_user.id, call.from_user.full_name, call.from_user.username)
    if u["starter_claimed"]:
        return await call.answer("❌ Вы уже забрали стартовый пак RiseStars (1/1)!", show_alert=True)
    await call.answer()
    await send_stars_invoice(
        call.bot,
        call.from_user.id,
        "1 000 RiseStars (Стартовый пак)",
        "Одноразовое пополнение баланса на 1 000 RiseStars (100 = 1 TG Star)",
        "starter_1000",
        10,
    )


@router.callback_query(F.data == "buy_verify")
async def cb_buy_verify(call: CallbackQuery):
    await call.answer()
    await send_stars_invoice(
        call.bot,
        call.from_user.id,
        "Верификация • Мажор",
        "Официальная галочка верификации для вашего аккаунта RiseGram",
        "verify_major",
        50,
    )


@router.callback_query(F.data == "buy_scam")
async def cb_buy_scam(call: CallbackQuery, state: FSMContext):
    await state.set_state(ShopStates.waiting_scam_target)
    await call.message.edit_text(
        "⚠️ <b>Метка SCAM • 15 TG ⭐</b>\n\n"
        "Отправьте @username или ID аккаунта/канала, который нужно пометить:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отмена", callback_data="menu_shop")]
        ]),
        parse_mode="HTML",
    )


@router.message(ShopStates.waiting_scam_target)
async def process_scam_target(message: Message, state: FSMContext):
    target = message.text.strip()
    await state.clear()
    await send_stars_invoice(
        message.bot,
        message.from_user.id,
        f"Метка SCAM на {target}",
        f"Пометка мошенника для {target} (проверка модерацией)",
        f"scam:{target}",
        15,
    )


@router.callback_query(F.data == "shop_888")
async def cb_shop_888(call: CallbackQuery):
    text = (
        "📞 <b>Анонимные номера +888</b>\n\n"
        "Купленный номер станет вашим текущим номером в боте и приложении.\n"
        "Выберите формат или задайте свои цифры:"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💎 +888 8 XXX • 50 ⭐", callback_data="buy_888_vip")],
        [InlineKeyboardButton(text="📞 +888 0XXX XXXX • 25 ⭐", callback_data="buy_888_std")],
        [InlineKeyboardButton(text="📝 Свой номер • 75 ⭐", callback_data="buy_888_custom")],
        [InlineKeyboardButton(text="↩️ Назад", callback_data="menu_shop")],
    ])
    await call.message.edit_text(text, reply_markup=kb, parse_mode="HTML")


@router.callback_query(F.data.in_({"buy_888_vip", "buy_888_std"}))
async def cb_buy_888_preset(call: CallbackQuery):
    await call.answer()
    if call.data == "buy_888_vip":
        num = f"+888 8 {random.randint(100, 999)}"
        price = 50
    else:
        num = f"+888 0{random.randint(100, 999)} {random.randint(1000, 9999)}"
        price = 25
    await send_stars_invoice(
        call.bot,
        call.from_user.id,
        f"Номер {num}",
        "Анонимный виртуальный номер +888 в экосистеме RiseGram",
        f"num888:{num}",
        price,
    )


@router.callback_query(F.data == "buy_888_custom")
async def cb_buy_888_custom(call: CallbackQuery, state: FSMContext):
    await state.set_state(ShopStates.waiting_custom_888)
    await call.message.edit_text(
        "📝 Введите желаемые цифры после <code>+888</code> (например: <code>777 7777</code>):",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отмена", callback_data="shop_888")]
        ]),
        parse_mode="HTML",
    )


@router.message(ShopStates.waiting_custom_888)
async def process_custom_888(message: Message, state: FSMContext):
    digits = message.text.strip().replace("+888", "").strip()
    num = f"+888 {digits}"
    await state.clear()
    await send_stars_invoice(
        message.bot,
        message.from_user.id,
        f"Кастомный номер {num}",
        "Индивидуальный анонимный номер +888",
        f"num888:{num}",
        75,
    )


@router.callback_query(F.data == "shop_nft")
async def cb_shop_nft(call: CallbackQuery, state: FSMContext):
    await state.set_state(ShopStates.waiting_nft_username)
    await call.message.edit_text(
        "🏷 <b>NFT-юзернеймы</b>\n\n"
        "Коллекционный @username на ваш аккаунт.\n"
        "4 буквы — <b>14 ⭐</b>\n"
        "5 и больше — <b>10 ⭐</b>\n\n"
        "Отправьте желаемый юзернейм, например <code>@rise</code>:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отмена", callback_data="menu_shop")]
        ]),
        parse_mode="HTML",
    )


@router.message(ShopStates.waiting_nft_username)
async def process_nft_username(message: Message, state: FSMContext):
    raw = message.text.strip().lstrip("@")
    if len(raw) < 4:
        return await message.answer("⚠️ Минимум 4 символа! Попробуйте ещё раз:")
    price = 14 if len(raw) == 4 else 10
    await state.clear()
    await send_stars_invoice(
        message.bot,
        message.from_user.id,
        f"NFT-юзернейм @{raw}",
        f"Коллекционный юзернейм @{raw} для аккаунта RiseGram",
        f"nft:@{raw}",
        price,
    )


# ================= ОБРАБОТКА ОПЛАТЫ TELEGRAM STARS =================
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
            await db.execute(
                "UPDATE users SET risestars = risestars + 1000, starter_claimed = 1 WHERE tg_id = ?",
                (tg_id,),
            )
            title = "1 000 RiseStars (Стартовый пак)"
        elif payload == "verify_major":
            await db.execute("UPDATE users SET verified = 1 WHERE tg_id = ?", (tg_id,))
            title = "Верификация (Мажор)"
        elif payload.startswith("scam:"):
            target = payload.split(":", 1)[1]
            await db.execute("UPDATE users SET scam_mark = 1 WHERE username = ?", (target,))
            title = f"Метка SCAM ({target})"
        elif payload.startswith("num888:"):
            num = payload.split(":", 1)[1]
            await db.execute("UPDATE users SET phone = ? WHERE tg_id = ?", (num, tg_id))
            title = f"Номер {num}"
        elif payload.startswith("nft:"):
            uname = payload.split(":", 1)[1]
            await db.execute("UPDATE users SET username = ? WHERE tg_id = ?", (uname, tg_id))
            title = f"NFT-юз {uname}"
        else:
            title = payload

        await db.execute(
            "INSERT INTO purchases (tg_id, title, price_xtr) VALUES (?, ?, ?)",
            (tg_id, title, stars_paid),
        )
        await db.commit()

    await message.answer(
        f"✅ <b>Оплата прошла успешно!</b>\nВыдано: <b>{title}</b> ({stars_paid} TG ⭐)",
        reply_markup=kb_main_menu(),
        parse_mode="HTML",
    )


# ================= ПОКУПКИ, НАСТРОЙКИ И СПРАВКА =================
@router.callback_query(F.data == "menu_purchases")
async def cb_purchases(call: CallbackQuery):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT title, price_xtr, created_at FROM purchases WHERE tg_id = ? ORDER BY id DESC LIMIT 10",
            (call.from_user.id,),
        )
        rows = await cur.fetchall()

    if not rows:
        text = "🕒 <b>Мои покупки</b>\n\nПокупок пока нет."
    else:
        lines = [f"• {r[0]} — <b>{r[1]} ⭐</b>" for r in rows]
        text = "🕒 <b>Мои покупки</b>\n\n" + "\n".join(lines)

    await call.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="↩️ Назад", callback_data="back_main")]
        ]),
        parse_mode="HTML",
    )


@router.callback_query(F.data == "menu_settings")
async def cb_settings(call: CallbackQuery):
    u = await get_or_create_user(call.from_user.id, call.from_user.full_name, call.from_user.username)
    mode = u["price_mode"]
    mode_name = "Настоящие рубли (100 ⭐ = 1,39 ₽)" if mode == "rub" else "Как в Telegram (100 ⭐ ≈ 132 ₽)"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text=("✓ Как в Telegram" if mode == "tg" else "Как в Telegram"),
                callback_data="set_mode_tg",
            ),
            InlineKeyboardButton(
                text=("✓ Настоящие рубли" if mode == "rub" else "Настоящие рубли"),
                callback_data="set_mode_rub",
            ),
        ],
        [InlineKeyboardButton(text="↩️ Назад", callback_data="back_main")],
    ])
    await call.message.edit_text(
        f"⚙️ <b>Настройки</b>\n\nЦенность подарков\nСейчас: <b>{mode_name}</b>",
        reply_markup=kb,
        parse_mode="HTML",
    )


@router.callback_query(F.data.in_({"set_mode_tg", "set_mode_rub"}))
async def cb_toggle_mode(call: CallbackQuery):
    new_mode = "tg" if call.data == "set_mode_tg" else "rub"
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET price_mode = ? WHERE tg_id = ?", (new_mode, call.from_user.id))
    await call.answer("Курс отображения обновлён!")
    await cb_settings(call)


@router.callback_query(F.data == "menu_help")
async def cb_help(call: CallbackQuery):
    text = (
        "ℹ️ <b>Как это работает</b>\n\n"
        "1. Откройте приложение RiseGram по кнопке в меню.\n"
        "2. Введите свой номер из бота — код подтверждения придёт сюда.\n"
        "3. В разделе «Донат» доступны RiseStars, верификация, метки SCAM, номера +888 и NFT-юзернеймы.\n\n"
        "/start — меню\n"
        "/shop — донат\n"
        "/code — код входа\n"
        "/admin7206852053 — панель создателя"
    )
    await call.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="↩️ Назад", callback_data="back_main")]
        ]),
        parse_mode="HTML",
    )


# ================= СКРЫТАЯ АДМИНКА (/admin7206852053) =================
@router.message(Command("admin7206852053"))
async def cmd_secret_admin(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    await state.clear()
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Статистика базы", callback_data="adm_stats")],
        [InlineKeyboardButton(text="🎁 Выдать награду / предмет", callback_data="adm_grant_menu")],
    ])
    await message.answer(
        "👑 <b>Панель создателя RiseGram</b>\n\nВыбери нужный раздел:",
        reply_markup=kb,
        parse_mode="HTML"
    )

@router.callback_query(F.data == "adm_stats")
async def cb_adm_stats(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        return
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT COUNT(*) FROM users")
        users_count = (await cur.fetchone())[0]
        cur = await db.execute("SELECT SUM(price_xtr) FROM purchases")
        total_xtr = (await cur.fetchone())[0] or 0
    
    await call.message.edit_text(
        f"📊 <b>Статистика RiseGram</b>\n\n"
        f"👥 Пользователей в базе: <b>{users_count}</b>\n"
        f"⭐ Всего заработано XTR: <b>{total_xtr} ⭐</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="↩️ В админку", callback_data="back_admin")]
        ], parse_mode="HTML")
    )

@router.callback_query(F.data == "back_admin")
async def cb_back_admin(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID:
        return
    await state.clear()
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Статистика базы", callback_data="adm_stats")],
        [InlineKeyboardButton(text="🎁 Выдать награду / предмет", callback_data="adm_grant_menu")],
    ])
    await call.message.edit_text("👑 <b>Панель создателя RiseGram</b>", reply_markup=kb, parse_mode="HTML")

@router.callback_query(F.data == "adm_grant_menu")
async def cb_adm_grant_menu(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⭐ RiseStars (+1000)", callback_data="grant:stars:1000")],
        [InlineKeyboardButton(text="☑️ Верификация", callback_data="grant:verify:1")],
        [InlineKeyboardButton(text="⚠️ Метка SCAM", callback_data="grant:scam:1")],
        [InlineKeyboardButton(text="📞 Номер +888 (кастом)", callback_data="grant:num888")],
        [InlineKeyboardButton(text="🏷 NFT-юзернейм", callback_data="grant:nft")],
        [InlineKeyboardButton(text="↩️ В админку", callback_data="back_admin")],
    ])
    await call.message.edit_text("🎁 <b>Выберите, что именно выдать пользователю:</b>", reply_markup=kb, parse_mode="HTML")

@router.callback_query(F.data.startswith("grant:"))
async def cb_grant_select(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID:
        return
    parts = call.data.split(":")
    action = parts[1]
    val = parts[2] if len(parts) > 2 else "input"
    
    await state.update_data(grant_action=action, grant_val=val)
    await state.set_state(AdminStates.waiting_grant_target)
    
    await call.message.edit_text(
        f"🎯 <b>Выбран предмет для выдачи:</b> <code>{action}</code>\n\n"
        f"Теперь отправьте **Telegram ID** пользователя (например: <code>7206852053</code>):",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отмена", callback_data="back_admin")]
        ]),
        parse_mode="HTML"
    )

@router.message(AdminStates.waiting_grant_target)
async def process_grant_target(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    
    target_raw = message.text.strip()
    data = await state.get_data()
    action = data.get("grant_action")
    val = data.get("grant_val")
    await state.clear()

    try:
        target_id = int(target_raw)
    except ValueError:
        return await message.answer("❌ Ошибка! ID должен состоять только из цифр.")

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM users WHERE tg_id = ?", (target_id,))
        user = await cur.fetchone()
        if not user:
            return await message.answer(f"❌ Пользователь с ID <code>{target_id}</code> не найден в базе бота.", parse_mode="HTML")

        if action == "stars":
            await db.execute("UPDATE users SET risestars = risestars + 1000 WHERE tg_id = ?", (target_id,))
            msg_text = "🎁 Вам начислено <b>1 000 RiseStars</b> от администратора!"
        elif action == "verify":
            await db.execute("UPDATE users SET verified = 1 WHERE tg_id = ?", (target_id,))
            msg_text = "🎁 Ваш аккаунт получил официальную <b>верификацию</b>!"
        elif action == "scam":
            await db.execute("UPDATE users SET scam_mark = 1 WHERE tg_id = ?", (target_id,))
            msg_text = "⚠️ На ваш аккаунт была установлена <b>метка SCAM</b>."
        elif action == "num888":
            num = f"+888 {random.randint(1000, 9999)}"
            await db.execute("UPDATE users SET phone = ? WHERE tg_id = ?", (num, target_id))
            msg_text = f"🎁 Вам выдан новый анонимный номер: <b>{num}</b>"
        elif action == "nft":
            uname = f"@rise_{random.randint(100,999)}"
            await db.execute("UPDATE users SET username = ? WHERE tg_id = ?", (uname, target_id))
            msg_text = f"🎁 Вам выдан коллекционный NFT-юзернейм: <b>{uname}</b>"
        else:
            msg_text = "🎁 Администратор выдал вам предмет!"

        await db.commit()

    try:
        await message.bot.send_message(target_id, msg_text, parse_mode="HTML")
        notified = " (уведомление отправлено пользователю)"
    except Exception:
        notified = " (но бот не смог написать пользователю в ЛС)"

    await message.answer(
        f"✅ <b>Успешно выдано!</b>\nПользователь ID: <code>{target_id}</code>{notified}",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="👑 В админку", callback_data="back_admin")]
        ]),
        parse_mode="HTML"
    )

async def main():
    await init_db()
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()
    dp.include_router(router)
    print("Бот успешно запущен!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
    
async def main():
    await init_db()
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()
    dp.include_router(router)
    print("Бот успешно запущен!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())


async def main():
    await init_db()
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()
    dp.include_router(router)
    print("Бот успешно запущен!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
    
