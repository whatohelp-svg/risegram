"""HTTPS appeal form for native RiseGram accounts frozen in MTProto.

The stock Android freeze sheet opens links in a WebView and cannot handle
tg:// URLs. Use one unguessable, per-freeze capability URL, validated against
the active freeze in Neon. This capability can ONLY submit a support ticket;
it cannot log in, lift restrictions, or read private profile data.
"""
from __future__ import annotations

import asyncio
import hashlib
import html
import logging
import os
import re
import secrets
from datetime import datetime, timezone

import psycopg
from psycopg.rows import dict_row
from aiohttp import web

import config
from ui import safe_send

log = logging.getLogger("risegram.freeze_appeals")
BASE = "https://risegram-risegram.up.railway.app/appeal"
TICKET = re.compile(r"^[A-Za-z0-9_-]{40,64}$")
HEADERS = {
    "Cache-Control": "no-store, private",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'",
}


def make_url() -> str:
    return BASE + "?t=" + secrets.token_urlsafe(32)


def _valid_token(value: str) -> bool:
    return isinstance(value, str) and TICKET.fullmatch(value) is not None


def _dsn() -> str:
    dsn = os.environ.get("RISEGRAM_LEDGER_DSN", "")
    if not dsn:
        raise RuntimeError("Missing native Neon connection")
    return dsn


def _page(title: str, body: str, status: int = 200) -> web.Response:
    document = (
        '<!doctype html><html lang="ru"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta name="theme-color" content="#17232f"><title>' + html.escape(title) +
        '</title><style>body{box-sizing:border-box;margin:0;min-height:100vh;'
        'background:#15212e;color:#fff;font:16px/1.55 system-ui,Arial,sans-serif;'
        'display:grid;place-items:center;padding:20px}main{max-width:500px;width:100%;'
        'background:#223243;border-radius:18px;padding:22px;box-sizing:border-box}'
        'h1{font-size:24px;margin-top:0}p{color:#d4dee7}textarea{display:block;'
        'box-sizing:border-box;width:100%;min-height:165px;resize:vertical;'
        'margin:12px 0;color:#fff;background:#12202e;border:1px solid #5b7388;'
        'border-radius:12px;padding:12px;font:inherit}button{background:#278fe1;'
        'color:white;border:0;border-radius:12px;padding:13px;width:100%;'
        'font-size:17px;font-weight:600}small{color:#b9c4cf}</style></head>'
        '<body><main><h1>' + html.escape(title) + '</h1>' + body +
        '</main></body></html>'
    )
    return web.Response(text=document, status=status,
                        content_type="text/html", charset="utf-8", headers=HEADERS)


async def _active_user(ticket: str, conn) -> int | None:
    address = BASE + "?t=" + ticket
    row = await (await conn.execute("""
        SELECT r.user_id FROM account_restrictions r
        JOIN users u ON u.id=r.user_id
        WHERE r.appeal_url=%s AND r.frozen=true AND u.deleted_at IS NULL
          AND (r.frozen_until IS NULL OR r.frozen_until>now())
    """, (address,))).fetchone()
    return int(row["user_id"]) if row else None


async def h_appeal_get(request: web.Request) -> web.Response:
    ticket = request.query.get("t", "")
    if not _valid_token(ticket):
        return _page("Обращение в RiseGram", "<p>Откройте «Оспорить» в замороженном аккаунте RiseGram — там будет персональная ссылка на обращение</p>", 400)
    try:
        async with await psycopg.AsyncConnection.connect(
            _dsn(), connect_timeout=8, row_factory=dict_row
        ) as conn:
            user_id = await _active_user(ticket, conn)
    except Exception:
        log.exception("Cannot validate freeze appeal")
        return _page("Сервис временно недоступен", "<p>Не удалось проверить аккаунт — попробуйте позже</p>", 503)
    if user_id is None:
        return _page("Ссылка недействительна", "<p>Заморозка уже снята или ссылка на обращение устарела</p>", 403)
    return _page("❄️ Оспорить заморозку", (
        "<p>Сообщение будет передано администрации поддержки RiseGram (@youtube)"
        " — даже если из приложения нельзя писать</p>"
        '<form method="post" action="/appeal">'
        '<input type="hidden" name="t" value="' + html.escape(ticket, quote=True) + '">'
        '<label for="message">Причина обращения</label>'
        '<textarea id="message" name="message" minlength="10" maxlength="1200"'
        ' placeholder="Опишите проблему и почему ограничение следует пересмотреть" required></textarea>'
        '<button type="submit">Отправить обращение</button></form>'
        "<p><small>Максимум 3 обращения за 24 часа — не передавайте эту персональную ссылку другим людям"
        "</small></p>"
    ))


async def h_appeal_post(request: web.Request) -> web.Response:
    # Input size is capped before aiohttp parses it. Keep the secret off logs.
    if request.content_length is not None and request.content_length > 7000:
        return _page("Сообщение слишком длинное", "<p>До 1200 символов</p>", 413)
    try:
        form = await request.post()
    except (ValueError, web.HTTPException):
        return _page("Неверные данные", "<p>Откройте ссылку из приложения</p>", 400)
    ticket = str(form.get("t", ""))
    message = str(form.get("message", "")).strip()
    if not _valid_token(ticket):
        return _page("Ссылка недействительна", "<p>Откройте «Оспорить» заново</p>", 403)
    if not 10 <= len(message) <= 1200:
        return _page("Введите сообщение", "<p>Напишите от 10 до 1200 символов</p>", 400)
    if any(ord(ch) < 32 and ch not in "\n\t" for ch in message):
        return _page("Неверный текст", "<p>Уберите управляющие символы</p>", 400)
    try:
        async with await psycopg.AsyncConnection.connect(
            _dsn(), connect_timeout=8, row_factory=dict_row
        ) as conn:
            async with conn.transaction():
                user_id = await _active_user(ticket, conn)
                if user_id is None:
                    return _page("Ссылка недействительна", "<p>Срок обращения истёк или аккаунт разморожен</p>", 403)
                # Serialize submissions from one frozen account, including
                # two simultaneous WebView requests. No account changes.
                await conn.execute("SELECT pg_advisory_xact_lock(%s, %s)", (718, user_id))
                row = await (await conn.execute("""
                    SELECT COUNT(*)::int AS cnt, MAX(created_at) AS last_sent
                      FROM risegram_freeze_appeals
                     WHERE user_id=%s AND created_at>now()-interval '24 hours'
                """, (user_id,))).fetchone()
                if int(row["cnt"] or 0) >= 3:
                    return _page("Лимит обращений", "<p>Разрешено до 3 обращений за 24 часа</p>", 429)
                if row["last_sent"] and (datetime.now(timezone.utc) - row["last_sent"]).total_seconds() < 90:
                    return _page("Слишком часто", "<p>Повторите попытку через полторы минуты</p>", 429)
                sent = await (await conn.execute("""
                    INSERT INTO risegram_freeze_appeals
                        (user_id, ticket_hash, message, status)
                    VALUES (%s,%s,%s,'new') RETURNING id
                """, (user_id, hashlib.sha256(ticket.encode()).hexdigest(), message))).fetchone()
                appeal_id = int(sent["id"])
    except Exception:
        log.exception("Unable to submit freeze appeal")
        return _page("Ошибка отправки", "<p>Сервис временно недоступен — попробуйте позже</p>", 503)

    try:
        text = (f"❄️ <b>Обращение о заморозке #{appeal_id}</b>\n"
                f"RiseGram ID: <code>{user_id}</code>\n"
                f"<b>Текст:</b>\n{html.escape(message)}\n\n"
                "Ответьте пользователю из внутреннего аккаунта поддержки @youtube")
        await safe_send(request.app["bot"], config.OWNER_TELEGRAM_ID, text)
    except Exception:
        # The durable support inbox in Neon remains authoritative.
        log.exception("Appeal saved, staff Telegram notification failed")
    return _page("✅ Обращение принято", (
        "<p>Заявка №" + str(appeal_id) +
        " сохранена и передана в очередь поддержки RiseGram</p>"
        "<p>Аккаунт остаётся замороженным до решения модерации</p>"
    ))


async def sync_once() -> int:
    """Give existing and new frozen accounts private HTTPS appeal URLs."""
    updates = 0
    async with await psycopg.AsyncConnection.connect(
        _dsn(), connect_timeout=8, row_factory=dict_row
    ) as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS risegram_freeze_appeals (
                id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                user_id bigint NOT NULL,
                ticket_hash text NOT NULL,
                message text NOT NULL,
                status text NOT NULL DEFAULT 'new',
                created_at timestamptz NOT NULL DEFAULT now()
            )
        """)
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS ix_rg_freeze_appeals_user_date
              ON risegram_freeze_appeals(user_id,created_at DESC)
        """)
        rows = await (await conn.execute("""
            SELECT r.user_id FROM account_restrictions r
            JOIN users u ON u.id=r.user_id
            WHERE r.frozen=true AND u.deleted_at IS NULL
              AND (r.appeal_url IS NULL OR r.appeal_url NOT LIKE %s)
              AND (r.frozen_until IS NULL OR r.frozen_until>now())
            LIMIT 100
        """, (BASE + "?t=%",))).fetchall()
        for row in rows:
            address = make_url()
            result = await conn.execute("""
                UPDATE account_restrictions SET appeal_url=%s
                WHERE user_id=%s AND frozen=true
                  AND (appeal_url IS NULL OR appeal_url NOT LIKE %s)
            """, (address, int(row["user_id"]), BASE + "?t=%"))
            updates += result.rowcount
        await conn.commit()
    if updates:
        log.info("Configured private HTTPS freeze appeal pages for %s accounts", updates)
    return updates


async def worker() -> None:
    while True:
        try:
            await sync_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Could not initialize/sync HTTPS appeal pages")
        await asyncio.sleep(120)
