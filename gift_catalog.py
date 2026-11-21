"""Telegram Bot API gift catalog normalization shared by bot, admin and HTTP API."""
from __future__ import annotations

# Human-readable labels for well-known Telegram gift IDs. Current IDs, emojis,
# prices and limited-stock counters are always read from getAvailableGifts.
GIFT_LABELS = {
    "5170145012310081615": "Love Heart",
    "5170233102089322756": "Teddy Bear",
    "5170250947678437525": "Gift Box",
    "5168103777563050263": "Red Rose",
    "5170144170496491616": "Birthday Cake",
    "5170314324215857265": "Bouquet of Flowers",
    "5170564780938756245": "Rocket",
    "6028601630662853006": "Champagne Bottle",
    "5168043875654172773": "Trophy",
    "5170690322832818290": "Diamond Ring",
    "5170521118301225164": "Gem",
}


def gift_field(gift, key: str, default=None):
    if isinstance(gift, dict):
        return gift.get(key, default)
    # aiogram versions that keep newer Bot API fields as Pydantic extras.
    extra = getattr(gift, "model_extra", None)
    if isinstance(extra, dict) and key in extra:
        return extra[key]
    raw = getattr(gift, "__dict__", None)
    if isinstance(raw, dict) and key in raw:
        return raw[key]
    return getattr(gift, key, default)


async def available_telegram_gifts(bot) -> list[dict]:
    result = await bot.get_available_gifts()
    out = []
    for gift in gift_field(result, "gifts", []) or []:
        gift_id = str(gift_field(gift, "id", "") or "")
        if not gift_id:
            continue
        sticker = gift_field(gift, "sticker")
        emoji = gift_field(sticker, "emoji", "🎁") or "🎁"
        upgrade_raw = gift_field(gift, "upgrade_star_count")
        variants = int(gift_field(gift, "unique_gift_variant_count", 0) or 0)
        upgrade_stars = max(0, int(upgrade_raw or 0))
        can_upgrade = upgrade_raw is not None or variants > 0
        out.append({
            "id": gift_id,
            "name": GIFT_LABELS.get(gift_id, f"Telegram Gift {gift_id[-4:]}"),
            "emoji": emoji,
            "stars": int(gift_field(gift, "star_count", 0) or 0),
            "upgrade_stars": upgrade_stars,
            "can_upgrade": can_upgrade,
            "unique_gift_variant_count": variants,
            "total_count": gift_field(gift, "total_count"),
            "remaining_count": gift_field(gift, "remaining_count"),
            "personal_total_count": gift_field(gift, "personal_total_count"),
            "personal_remaining_count": gift_field(gift, "personal_remaining_count"),
        })
    return out
