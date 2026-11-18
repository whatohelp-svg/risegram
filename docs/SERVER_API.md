# RiseGram server API

The RiseGram service is an independent HTTP backend. It is not a Telegram MTProto server and does not implement Telegram's private server protocol.

## Runtime

- Python 3.12
- aiohttp HTTP API
- aiosqlite database
- Persistent SQLite file configured with `DB_PATH`
- Persistent media directory configured with `MEDIA_PATH`
- Private Telegram bot is currently used only to deliver one-time login codes to linked accounts

On Railway, mount a persistent volume at `/data` and configure:

- `DB_PATH=/data/risegram.db`
- `MEDIA_PATH=/data/risegram-media`

The API automatically creates the media directory. Keep the API and bot on the same database. Run a single API instance while SQLite and in-process SSE delivery are in use.

## Authentication

Clients send the RiseGram session token in:

```http
Authorization: Bearer <session-token>
```

Obtain a session through `POST /api/auth/request` and `POST /api/auth/verify`. Login delivery currently depends on a linked private bot chat. Telegram IDs are not RiseGram account IDs.

## Direct chats

- `GET /api/chats`
- `GET /api/chats/{rise_id}/messages`
- `POST /api/chats/{rise_id}/messages`

Send text with `{"text":"hello"}`. To send uploaded files, include `media_ids` with up to 10 media IDs created by the same account. The message text may be empty when at least one media ID is attached.

## Media

- `POST /api/media` — authenticated multipart upload; the file field must be named `file`
- `GET /api/media/{media_id}` — authenticated download

Current limits are 25 MiB per file and 250 MiB per account. Supported formats include common JPEG/PNG/WebP/GIF images, MP4/WebM/MOV video, MP3/OGG/Opus/WAV/M4A audio, PDF, TXT, ZIP and common Office Open XML documents.

The service stores random storage names, original sanitized filenames, MIME type, byte size and SHA-256 digest. A file download is allowed for its uploader or a participant in a direct/space message containing it. Uploaded-but-unattached files remain visible only to their uploader.

## Groups and channels

- `GET /api/spaces`
- `POST /api/spaces`
- `POST /api/spaces/join`
- `GET /api/spaces/{space_id}/messages`
- `POST /api/spaces/{space_id}/messages`
- `GET /api/spaces/{space_id}/members`
- `POST /api/spaces/{space_id}/members/{rise_id}/role` — owner assigns `admin` or `member`
- `DELETE /api/spaces/{space_id}/members/{rise_id}` — owner/admin moderation, with owner protection
- `POST /api/spaces/{space_id}/owner/{rise_id}` — owner transfers ownership to another member
- `POST /api/spaces/{space_id}/invite/rotate` — owner invalidates the previous invite
- `POST /api/spaces/{space_id}/leave` — an owner must transfer ownership first

Only owners and admins can publish to channels. Regular channel members are read-only.

## Live events and limitations

- `GET /api/events` — authenticated Server-Sent Events stream for account changes

The current SSE queues are process-local. They are suitable for a single service instance, not horizontally scaled deployments. Before scaling to multiple instances, move event fan-out to Redis Pub/Sub or an equivalent shared broker. SQLite media storage also assumes one persistent volume; distributed production storage should use object storage.

The Telegram Android source submodule still speaks Telegram's MTProto API. It does not automatically use this HTTP API. A RiseGram client adapter must be implemented in the Android data layer before that client can log in to RiseGram and use these endpoints.
