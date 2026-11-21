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


async def pool_material(session, slug):
    title = NAMES[slug]
    local = ROOT / (title + ".zip")
    if local.exists():
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
                   for kind in ("models", "patterns", "backdrops")}
        requests = {f"{kind}{index}": fetch(f"{kind}/{title}/{item['name']}.tgs")
                    for kind in ("models", "patterns") for index, item in enumerate(catalog[kind])}
        files = dict(zip(requests, await asyncio.gather(*requests.values()), strict=True))
    fields = {}
    for kind in ("models", "patterns", "backdrops"):
        rows = []
        for index, item in enumerate(catalog[kind]):
            row = dict(name=item["name"], rarity_permille=item["rarityPermille"], sort_order=index)
            if kind == "backdrops":
                row.update(backdrop_id=item.get("backdropId", index), center_color=item["centerColor"],
                           edge_color=item["edgeColor"], pattern_color=item["patternColor"], text_color=item["textColor"])
            else:
                row["file_key"] = f"{kind}{index}"
            rows.append(row)
        fields[kind] = rows
    return fields, files
