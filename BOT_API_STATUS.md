# RiseGram Bot API — tested private-chat MVP

This branch implements independent RiseGram bot accounts and a subset of Telegram-compatible Bot API endpoints designed for aiogram long polling. No Telegram BotFather token is accepted.

## Users and bot owners

- RiseGram Desktop Profile → RiseGram Bots lets any signed-in RiseGram user register bots (up to 20 per owner), inspect their bots, and rotate tokens.
- Bot usernames must end in `bot`. The name `@typevirtbot` is reserved for the configured RiseGram owner.
- Tokens look like `900000001:<secret>` so aiogram accepts them, but they authenticate against **RiseGram**, never Telegram.
- Bots appear in desktop search and private conversations (client internally uses negative bot-specific IDs).
- Users must initiate conversation with a bot before that bot can message them.

## Implemented Bot API endpoints

- `GET /bot<TOKEN>/getMe`
- `POST /bot<TOKEN>/getUpdates` with offset, limit, and bounded long polling
- `POST /bot<TOKEN>/sendMessage` with plain text, basic HTML stripping, inline callback/HTTPS URL buttons, and text reply keyboards
- `POST /bot<TOKEN>/editMessageText`, `editMessageReplyMarkup`, `deleteMessage`, `sendDice`
- `POST /bot<TOKEN>/answerCallbackQuery`, `deleteWebhook`, `getWebhookInfo`

Authentication: `Authorization: Bearer <RISEGRAM_USER_SESSION>` for the bot owner endpoints `/api/bots/create`, `/api/bots/mine`, `/api/bots/<id>/rotate-token`.

The client callback endpoint validates the callback against a button in a message belonging to that conversation.

## Known limitations

- **Not yet** complete Telegram Bot API compatibility. No group/channel bot membership, moderation, file/video/voice sending, Telegram webhooks, Telegram user/chat mentions, commands config, chat actions, replies/forwards or full message entity rendering.
- Android native MTProto bot support is not implemented by the web backend. The working chat interface here is **RiseGram Desktop**.
- TypeVirt source is separately adapted in `whatohelp-svg/Hello-There`, but requires a registered bot token and a separately deployed runtime. Its group-specific handlers still cannot work.
- The source of the unofficial Iris clone `Virus667/iris` is not available in that repository (README and LICENSE only). There is nothing there to execute or port.

Do not publish a token in a git repository or distribute it in client code. Use a secret manager/ Railway environment variables and persistent bot database storage.
