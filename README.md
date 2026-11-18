# RiseGram

RiseGram is an independent messenger/economy backend and native Android client. The bot is a private delivery and account-linking interface; Telegram IDs are not RiseGram account identifiers.

## Account identity and login

- `users.user_id` is the stable UUID for a RiseGram account.
- `users.rise_id` is a unique random 8-digit public Rise ID, separate from the login number.
- `users.tg_id` is a legacy-named internal database key, never a Telegram ID.
- `/start` in the existing `@risegrambot` creates the account and assigns its permanent login number.
- `telegram_links` maps the private bot chat to the internal account key only for delivery of one-time login codes and private bot messages.
- The login number stays fixed unless purchased or changed by an administrator; each login code is one-time and expires.
- Balances, games, purchases, transfers, NFT inventory, and market listings are owned by internal RiseGram account keys.
- The database initializer remaps legacy Telegram-keyed user rows and their relational references to internal keys, preserving Telegram delivery links.

## Railway variables

Required:
- `BOT_TOKEN`: token for the existing private bot.

Recommended:
- `DB_PATH=/data/risegram.db` (the Railway service has a persistent `/data` volume)
- `OWNER_ACCOUNT_ID=1`
- `OWNER_TELEGRAM_ID`: owner's Telegram numeric ID, used only for private delivery and owner verification
- `OWNER_USERNAME=koyyzzback`
- `STARTER_NFT_USERNAMES=koyyzz,owner,rise`

`API_SECRET` is not embedded in the APK and is not required for public client endpoints. Login requests use the permanent number issued by `/start`, are rate-limited, and are verified with one-time codes delivered to that account's private bot chat.

## Server API

The independent RiseGram HTTP backend supports direct messages, groups/channels with owner/admin roles, authenticated media uploads/downloads, and account event streams. See [docs/SERVER_API.md](docs/SERVER_API.md) for endpoints, limits, and current deployment constraints. The Telegram Android submodule still requires a RiseGram-specific client adapter; it is not connected to these REST endpoints yet.

## Shared economy

The bot and Android client use the same Railway SQLite database. A bot game changes the shared balance immediately; the client refreshes the profile balance while open. Marketplace purchases atomically debit the buyer, credit the seller after fee, and transfer the NFT inventory record.

## Local validation

```bash
pip install -r requirements.txt
python -m compileall -q .
python -m unittest discover -s tests -v
```

Do not deploy a code change to the live Railway service until backend tests and the Android build pass.
