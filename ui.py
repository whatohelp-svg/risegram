"""ui.py — общие кнопки, форматирование и безопасная отправка сообщений."""
from __future__ import annotations

import html
import logging
from typing import Iterable

from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    WebAppInfo,
)

log = logging.getLogger("risegram.ui")


def esc(value) -> str:
    return html.escape("" if value is None else str(value))


def fmt_num(x: float | int | None, decimals: int | None = None) -> str:
    """1234567.5 -> '1 234 567,5'. Целые без дробной части."""
    x = float(x or 0)
    if decimals is None:
        decimals = 0 if abs(x - round(x)) < 1e-9 else 2
    s = f"{x:,.{decimals}f}".replace(",", " ")
    return s.replace(".", ",")


def btn(
    text: str,
    cb: str | None = None,
    *,
    url: str | None = None,
    web_app: str | None = None,
    style: str | None = None,
    icon: str | None = None,
) -> InlineKeyboardButton:
    """Кнопка. style: 'primary' (синяя) | 'success' (зелёная) | 'danger' (красная).

    Поля style / icon_custom_emoji_id — нативные поля Bot API; aiogram пропускает лишние поля,
    поэтому отдельный класс-обёртка не нужен и работает на любой версии aiogram 3.
    """
    kw: dict = {"text": text}
    if cb is not None:
        kw["callback_data"] = cb
    if url:
        kw["url"] = url
    if web_app:
        kw["web_app"] = WebAppInfo(url=web_app)
    if style:
        kw["style"] = style
    if icon:
        kw["icon_custom_emoji_id"] = icon
    return InlineKeyboardButton(**kw)


def kb(*rows: Iterable[InlineKeyboardButton]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[list(r) for r in rows if r])


def back_row(cb: str = "menu:main", text: str = "↩️ Назад") -> list[InlineKeyboardButton]:
    return [btn(text, cb)]


def cancel_kb(cb: str = "menu:main") -> InlineKeyboardMarkup:
    return kb([btn("✖️ Отмена", cb, style="danger")])


def title(emoji: str, text: str) -> str:
    return f"{emoji} <b>{esc(text)}</b>\n\n"


async def show(event: Message | CallbackQuery, text: str, markup: InlineKeyboardMarkup | None = None) -> Message | None:
    """Редактирует сообщение при нажатии кнопки, иначе отправляет новое."""
    if isinstance(event, CallbackQuery):
        msg = event.message
        if isinstance(msg, Message):
            try:
                return await msg.edit_text(text, reply_markup=markup)
            except TelegramBadRequest as e:
                if "message is not modified" in str(e):
                    return msg
                log.debug("edit failed (%s), sending new message", e)
            try:
                return await msg.answer(text, reply_markup=markup)
            except (TelegramBadRequest, TelegramForbiddenError):
                return None
        return await event.bot.send_message(event.from_user.id, text, reply_markup=markup)
    return await event.answer(text, reply_markup=markup)


async def safe_send(bot, chat_id: int, text: str, markup: InlineKeyboardMarkup | None = None) -> bool:
    try:
        await bot.send_message(chat_id, text, reply_markup=markup)
        return True
    except (TelegramForbiddenError, TelegramBadRequest) as e:
        log.info("cannot send to %s: %s", chat_id, e)
        return False
