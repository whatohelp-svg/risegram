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


PUBLIC_STANDARD = set(GIFT_LABELS)
PUBLIC_SEASONAL = {"halloween-bear", "soldier-bear", "christmas-tree", "valentine-heart", "football-bear", "easter-bear"}


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


async def populate_seasonal_catalog():
    import asyncio
    import json
    import logging
    from pathlib import Path
    from aiohttp import ClientSession, ClientTimeout, FormData
    import config
    import database as db

    entries = json.loads((Path(__file__).parent / "data/seasonal_catalog.json").read_text())
    standard_prices = [15, 15, 25, 25, 50, 50, 50, 50, 100, 100, 100]
    entries.extend({"key": key, "title": title, "path": "standard/" + key + ".tgs", "stars": price}
                   for (key, title), price in zip(GIFT_LABELS.items(), standard_prices, strict=True))
    log = logging.getLogger("risegram.seasonal")
    timeout = ClientTimeout(total=30)
    async with ClientSession(timeout=timeout, headers={
        "Authorization": "Bearer " + config.MTPROTO_ADMIN_API_TOKEN
    }) as service:
        while True:
            pending = 0
            hidden = [(f"native_gift_id:{key}", key) for key in GIFT_LABELS if key not in PUBLIC_STANDARD]
            hidden.extend(("risegram-seasonal-" + item["key"], item["key"])
                          for item in entries if item["key"] not in PUBLIC_SEASONAL | PUBLIC_STANDARD)
            for mapping, source in hidden:
                gid = await db.get_meta(mapping)
                if not gid or await db.get_meta("drop-only:" + mapping) == str(gid):
                    continue
                try:
                    async with service.post(config.MTPROTO_ADMIN_API_URL + "/v1/gifts/set-enabled", json={
                        "command_id": "drop-only-" + str(gid), "actor": "risegram-catalog",
                        "reason": "Available through administrator drops", "gift_id": int(gid), "enabled": False,
                    }) as response:
                        result = await response.json()
                        if response.status != 200 or result.get("status") != "completed":
                            raise ValueError(str(result.get("error") or response.status)[:150])
                    await db.set_meta("drop-only:" + mapping, str(gid))
                except Exception as exc:
                    pending += 1
                    log.warning("Moving %s to drop catalog: %s", source, str(exc)[:150])
            for index, item in enumerate(entries):
                if item["key"] not in PUBLIC_SEASONAL | PUBLIC_STANDARD:
                    continue
                standard = item["key"] in PUBLIC_STANDARD
                key = ("native_gift_id:" if standard else "risegram-seasonal-") + item["key"]
                gid = await db.get_meta(key)
                revision = key.replace(":", "-") + "-catalog-v3"
                if gid and await db.get_meta(revision) == str(gid):
                    continue
                try:
                    image = (Path(__file__).parent / "data" / item["path"]).read_bytes()
                    price = item["stars"]
                    form = FormData()
                    form.add_field("metadata", json.dumps({
                        "command_id": revision, "gift_id": int(gid or 0), "actor": "risegram-catalog", "reason": "Seasonal gift catalog",
                        "title": item["title"], "stars": price, "convert_stars": (price * 85 + 50) // 100,
                        "enabled": True, "sort_order": list(GIFT_LABELS).index(item["key"]) if standard else 1000 + index,
                    }))
                    form.add_field("file", image, filename=item["key"]+".tgs", content_type="application/x-tgsticker")
                    async with service.post(config.MTPROTO_ADMIN_API_URL+"/v1/gifts/import", data=form) as response:
                        result = await response.json()
                        if response.status != 200 or result.get("status") != "completed":
                            raise ValueError(str(result.get("error") or response.status)[:150])
                        gid = str(int(result["details"]["gift_id"]))
                        await db.set_meta(key, gid)
                        await db.set_meta(revision, gid)
                except Exception as exc:
                    pending += 1
                    log.warning("Seasonal gift %s pending: %s", item["key"], str(exc)[:180])
            if not pending:
                log.info("Gift visibility updated: %d public seasonal templates, %d drop-only mappings", len(PUBLIC_SEASONAL), len(hidden))
                return
            await asyncio.sleep(90)
