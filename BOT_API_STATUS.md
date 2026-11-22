# RiseGram Bot API — prototype

This is not yet a working port of Telegram Bot API or aiogram

Current endpoints:
- POST /api/bots/create — authenticated RiseGram session, JSON username and name, returns an independent rg_ token
- POST /bot<TOKEN>/getMe — basic bot identity
- POST /bot<TOKEN>/getUpdates — empty update queue until inbound routing is implemented
- POST /bot<TOKEN>/sendMessage — HTTP 501, deliberately not pretending to deliver messages

Hello-There cannot run on this API yet. Its aiogram handlers, inline keyboards, callbacks and chat moderation require inbound event delivery and real bot messages. Do not replace TELEGRAM_BOT_TOKEN and deploy yet.

Telegram BotFather tokens are not supported. Do not commit secrets. This branch is for development and tests only.
