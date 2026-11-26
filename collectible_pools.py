"""Complete native gift upgrade pools, with the source probabilities in permille."""
import asyncio
import json
import re
import zipfile
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).parent / "data/pools"
CDN = "https://cdn.changes.tg/gifts/"
NAMES = {re.sub(r"[^a-z0-9]", "", n.lower()): n for n in json.loads((ROOT / "names.json").read_text()).values()}
CRAFT_TIERS = {"uncommon", "rare", "epic", "legendary"}


def model_metadata(item):
    tier = str(item.get("rarityTier") or "").lower()
    if item.get("crafted") is True or tier in CRAFT_TIERS:
        if tier not in CRAFT_TIERS:
            raise ValueError("Unknown crafted rarity tier: " + str(item.get("name")))
        return {"crafted": True, "rarity_kind": tier, "rarity_permille": 0}
    return {"rarity_permille": int(item["rarityPermille"])}


async def fresh_model_catalog(session, slug):
    title = NAMES[slug]
    async with session.get("https://api.changes.tg/models/" + quote(title)) as response:
        response.raise_for_status()
        rows = await response.json(content_type=None)
        if not isinstance(rows, list) or not rows or not all(
            isinstance(row, dict) and row.get("name") for row in rows
        ):
            raise ValueError("Invalid live model catalog for " + title)
        return rows



async def pool_material(session, slug):
    title = NAMES[slug]
    local = ROOT / (title + ".zip")
    # A local archived pool may predate the crafted-only models. Refresh it
    # from the source metadata, and use the current source artwork when needed.
    live_models = await fresh_model_catalog(session, slug)
    has_craft = any(model_metadata(item).get("crafted") for item in live_models)
    if local.exists() and not has_craft:
        with zipfile.ZipFile(local) as archive:
            catalog = json.loads(archive.read("catalog.json"))
            files = {name[:-4]: archive.read(name) for name in archive.namelist() if name.endswith(".tgs")}
    else:
        limit = asyncio.Semaphore(6)

        async def fetch(path):
            async with limit:
                for variant in dict.fromkeys([path, path.replace("’", "'").replace("\u00a0", " ")]):
                    async with session.get(CDN + quote(variant)) as response:
                        if response.status == 404:
                            continue
                        response.raise_for_status()
                        return await response.read()
                raise ValueError(f"Missing collectible asset {path}")

        catalog = {kind: json.loads(await fetch(f"{kind}/{title}/{kind}.json"))
                   for kind in ("patterns", "backdrops")}
        catalog["models"] = live_models
        requests = {f"{kind}{index}": fetch(f"{kind}/{title}/{item['name']}.tgs")
                    for kind in ("models", "patterns") for index, item in enumerate(catalog[kind])}
        files = dict(zip(requests, await asyncio.gather(*requests.values()), strict=True))
    fields = {}
    for kind in ("models", "patterns", "backdrops"):
        rows = []
        for index, item in enumerate(catalog[kind]):
            row = dict(name=item["name"], sort_order=index)
            row.update(model_metadata(item) if kind == "models" else {"rarity_permille": item["rarityPermille"]})
            if kind == "backdrops":
                row.update(backdrop_id=item.get("backdropId", index), center_color=item["centerColor"],
                           edge_color=item["edgeColor"], pattern_color=item["patternColor"], text_color=item["textColor"])
            else:
                row["file_key"] = f"{kind}{index}"
            rows.append(row)
        fields[kind] = rows
    return fields, files
