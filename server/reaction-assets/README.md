# Official Telegram reaction media

Place the **complete export** from the upstream `stickerfetch` command here (only if authorized to use the assets). The actual Telegram user session remains on the operator's own machine; do not commit login credentials or session files.

```sh
git clone https://github.com/iamxvbaba/gramsrv.git
cd gramsrv
git checkout 18511c2227b3cf4f4006ea3fc63ac10910f55530
SESSION="$HOME/.tg-seed.session" go run ./cmd/giftfetch -login-qr
SESSION="$HOME/.tg-seed.session" go run ./cmd/stickerfetch /absolute/path/to/risegram/server/reaction-assets reactions
```

Use your own authorized Telegram account and API credentials if required. See https://github.com/iamxvbaba/gramsrv/blob/18511c2227b3cf4f4006ea3fc63ac10910f55530/docs/stickerfetch.md.

Expected directory:
```
telegram_reactions_export/
  global_json/available_reactions_raw.json
  reactions/<document-id>.tgs
  reactions/<document-id>.webp
  reactions/<document-id>_thumb1_PhotoSize_typem.jpg
```

The build validates all document roles, paired optional flags, file sizes, and thumbnails. Once present, the server replaces the placeholder mappings on startup without removing reaction history or changing the Android application. Until an actual export is provided, the previous fallback remains in place. A public archive of animated emoji alone is not an exact export of messages.getAvailableReactions.
