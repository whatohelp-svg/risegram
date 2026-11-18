# RiseGram

RiseGram is an independent messenger/economy backend and native Android client. The bot is a private delivery and account-linking interface; Telegram IDs are not RiseGram account identifiers.

## Account identity

- `users.user_id` is the stable UUID for a RiseGram account.
- `users.tg_id` is an internal numeric RiseGram database key used by existing economy relations.
- `telegram_links` maps an optional Telegram private chat ID to an internal RiseGram account. Bot login codes and notifications resolve through this table.
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

`API_SECRET` is not embedded in the APK and is not required for public client endpoints. Login requests are rate-limited and verified with one-time codes delivered to the linked private bot chat.

## Shared economy

The bot and Android client use the same Railway SQLite database. A bot game changes the shared balance immediately; the client refreshes the profile balance while open. Marketplace purchases atomically debit the buyer, credit the seller after fee, and transfer the NFT inventory record.

## Local validation

```bash
pip install -r requirements.txt
python -m compileall -q .
python -m unittest discover -s tests -v
```

Do not deploy a code change to the live Railway service until backend tests and the Android build pass.
