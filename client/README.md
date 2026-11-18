# RiseGram Android client base

The full Telegram Android client source is included as a Git submodule at `client/telegram-android` (upstream commit `21f7d109da6ab3506c9e9577f98c6a38771ab335`). Clone with `git clone --recurse-submodules` or run `git submodule update --init --recursive` after cloning.

This is the client source only. Telegram does not publish its production server implementation. The upstream client speaks Telegram's MTProto service and is not connected to RiseGram's REST/SSE backend merely by changing a URL. RiseGram backend integration requires a dedicated client data-layer adapter and compatible authentication, messaging, media, group/channel, and push APIs. Do not represent this upstream client as an independent RiseGram messenger until that adapter is implemented and tested.

The Telegram Android client is licensed under GPL-2.0-or-later. Preserve its license and notices, publish corresponding source changes, use a distinct app name and branding, and configure RiseGram-owned application identifiers, API credentials, Firebase project, signing key, and notification infrastructure before release.
