# Telegram Android fork base

This repository pins the upstream Telegram Android client as a Git submodule at `Telegram-Android`.

- Upstream: https://github.com/TelegramMessenger/Telegram-Android
- Pinned upstream revision: `21f7d109da6ab3506c9e9577f98c6a38771ab335`
- Upstream license: GNU GPL-2.0-or-later; preserve the license and publish corresponding source changes when distributing binaries.

## Checkout

Clone RiseGram with submodules:

```sh
git clone --recurse-submodules https://github.com/whatohelp-svg/risegram.git
```

If RiseGram is already cloned:

```sh
git submodule update --init --recursive
```

## Important server boundary

The upstream Android app is a Telegram MTProto client. Changing `BuildVars.APP_ID` or `APP_HASH` does not point it at an arbitrary RiseGram REST server. Telegram's production server implementation is not published as a self-hostable server package, and Telegram states that its service does not support running independent Telegram clouds.

RiseGram's existing backend remains the independent server and database. To make this client use RiseGram, the Telegram UI must be connected to a RiseGram client adapter that implements RiseGram authentication, conversations, groups, media, uploads, events, contacts, and push delivery. Merely changing hostnames would be incorrect and would still connect to Telegram's protocol.

## Integration rule

Keep the Telegram client source as the UI/code base, and keep RiseGram's backend as the authoritative service. Do not configure the fork with Telegram's production API credentials or claim that it uses RiseGram servers until the MTProto-dependent controllers have been replaced or adapted and an end-to-end test proves the traffic reaches the RiseGram backend.
