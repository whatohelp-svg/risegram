# RiseGram — Telegram-themed collectible gift drops and Mini App

This implementation extends the **live Railway source branch** `feature/market-economy-sync` without replacing existing actual Telegram Bot API gifting.

## Scope

- Owner/admin: `/owner` (owner) or `/admin` (authorized admins) → **🧩 NFT RiseGram · 50/стр**
- Choose from a 5-column × 10-row numbered inline keyboard, with collection titles listed above it. Telegram button colors supported here: blue, green, red.
- Navigate catalog with ‹ and ›; enter total supply, one-gift RiseStars price, whether cosmetic upgrades are permitted, and whether to publish now or at a scheduled UTC+5 time.
- Owner/admin may pause and resume a drop and refresh collection catalog from the public dataset.
- Buyers: bot menu → **🎁 NFT Mini App**; enter their RiseGram virtual phone number; Telegram bot sends a one-time code to the linked Telegram account; Mini App displays live shop and inventory.
- All balances and purchases use **server-side authenticated account IDs**; inventory and stock are stored on the existing Railway SQLite database mounted at `DB_PATH`, not in client-side state.
- `BEGIN IMMEDIATE` serializes purchases to prevent oversells, balance decrements and item issuance are in one atomic transaction, and order keys are idempotent.

## Important distinction

These are **RiseGram-local digital collectibles based on Telegram collection names**, not actual Telegram collectible tokens, Fragment assets, NFTs on TON, or transferable on-chain ownership.

The project has a separate existing Telegram gift-delivery flow at `/api/gifts`, which is unchanged. This new implementation uses separate `rg_gift_*` database tables and `/api/local-gifts/*` routes to avoid mixing the two.

## Source and attribution

The initial collection list contains 123 collection names/slugs from Gift Club's [Telegram Gifts Dataset](https://gift-club.github.io/telegram-gifts-dataset/) (captured 2026-11-08). Dataset copyright its contributors, provided under **Creative Commons Attribution 4.0 (CC BY 4.0)**. The admin refresh action fetches new entries from the same dataset; it does not enumerate every uniquely numbered NFT on Telegram. It cannot guarantee same-day coverage of a newly released Telegram collection if the source dataset has not yet updated.

No Telegram or third-party collectible image files are copied into this project. The Mini App currently renders emoji placeholder artwork.

## Activation

1. Review PR #4 and ensure the automated checks pass before merging it into `feature/market-economy-sync`.
2. The Railway `risegram` service is configured from that branch and runs behind a domain such as `https://risegram-risegram.up.railway.app`. Verify the deployment is successful after merging.
3. Check `DB_PATH` is on the persistent Railway `/data` volume and `BOT_TOKEN`, `WEBAPP_URL` are set. Never put `BOT_TOKEN` or `API_SECRET` into a client.
4. Set `WEBAPP_URL` to the HTTPS app origin if necessary: the bot's **NFT Mini App** button opens the origin's `/miniapp` path. Telegram requires an HTTPS Mini App URL.
5. Enter the admin screen and publish a trial drop (e.g. 2 units, low RiseStars price) to validate the first purchase in a test account.
6. Test that a second user can purchase, over-limit purchases are rejected, and no duplicate balance deduction occurs for a repeated request ID.

## Public endpoints

- `GET /miniapp` — responsive Telegram Mini App storefront
- `POST /api/webapp/auth/request` — signed Telegram initData + virtual phone; request code
- `POST /api/webapp/auth/verify` — initData + virtual phone + bot-delivered code → bearer token
- `GET /api/local-gifts/catalog?page=0` — 50 collections per page
- `GET /api/local-gifts/drops` — active, stocked, started drops
- `GET /api/local-gifts/inventory` — authenticated inventory
- `POST /api/local-gifts/buy` — authenticated `{ "drop_id": 1, "request_id": "gift_unique_id" }`
- `POST /api/local-gifts/upgrade` — authenticated `{ "instance_id": 1 }`
- `GET /api/inventory` also includes a nonbreaking `local_gifts` field for native client integration.

## Known limits

- Native Android client gift-store UI is **not** added in this pull request. It can use the new API, but must implement its own screens.
- Upgrades are currently free cosmetic RiseGram upgrades. They do not mint, withdraw or convert gifts into Telegram/TON NFTs.
- No paid roulette, chance-based game purchases or withdrawal mechanism are provided.
- Source and UI tests do not replace testing the actual Telegram bot menu and purchase flow after live deployment.
