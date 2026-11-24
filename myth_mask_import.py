"""Import Myth Mask from public GiftChanges assets through the native gift-pack
validator. Prices and stock are RiseGram-local, not Telegram market values."""
import asyncio
import gzip
import hashlib
import io
import json
import logging
import zipfile
from urllib.parse import quote

from aiohttp import ClientSession, ClientTimeout, FormData
import config
import native_economy as economy

LOG=logging.getLogger("risegram.myth_mask")
BASE="https://cdn.changes.tg/gifts/"
TITLE="Myth Mask"
SOURCE_ID="5775966332847654507"
# Local release parameters; match the 25-star upgrade used by recent RG gifts.
PRICE=500
REDEEM=425
UPGRADE=25
SUPPLY=100
CRAFT={}
for tier,names in {
    "legendary":"Bitcoinicle|Fatality",
    "epic":"Golden Taurus|Golden Ram|Mystic Kitsune|Frontman|The Seeker",
    "rare":"Gold Golem|Red Phoenix|Wise Goddess|Pharaoh|Cyber Wolf|Apollo|Black Knight|Holy Crusader|Fire Demon|Golden Ra|Venus|Sun God",
    "uncommon":"Sacred Glass|Dark Masquerade|Ancient Rune|Crystal Crown|Frostbite|Temptress|Stone Warrior|Midas Bear|Helios|Steel Wisdom|Mayan Mask|Panther|Star Seeker|Shadow Rome",
}.items():
    for name in names.split("|"):
        CRAFT[name]=tier


async def catalog_status():
    async with economy.transaction() as conn:
        row=await (await conn.execute("""SELECT c.gift_id,c.enabled,c.collectible_revision_id FROM star_gift_catalog c
            JOIN star_gift_catalog_revisions r ON r.id=c.active_revision_id
            WHERE lower(r.title)='myth mask' LIMIT 1""")).fetchone()
        return row


async def run():
    if not config.MTPROTO_ADMIN_API_TOKEN or not config.MTPROTO_ADMIN_API_URL:
        LOG.warning("RG_MYTH_MASK_BLOCKED admin API unavailable")
        return
    try:
        if await catalog_status():
            LOG.info("RG_MYTH_MASK_ALREADY_PRESENT")
            return
        sem=asyncio.Semaphore(12)
        async with ClientSession(timeout=ClientTimeout(total=35)) as source:
            async def get(path,limit=2<<20):
                async with sem:
                    async with source.get(BASE+path,allow_redirects=False) as resp:
                        if resp.status!=200:
                            raise ValueError("source HTTP "+str(resp.status)+" for "+path[:150])
                        chunks=[]
                        total=0
                        async for chunk in resp.content.iter_chunked(65536):
                            total+=len(chunk)
                            if total>limit:
                                raise ValueError("source asset too large")
                            chunks.append(chunk)
                        raw=b"".join(chunks)
                        if not raw:
                            raise ValueError("empty source asset")
                        return raw
            gift_dir=quote(TITLE,safe="")
            ids,models,patterns,backdrops=await asyncio.gather(
                get("id-to-name.json"),get("models/"+gift_dir+"/models.json"),
                get("patterns/"+gift_dir+"/patterns.json"),get("backdrops/"+gift_dir+"/backdrops.json"))
            if json.loads(ids).get(SOURCE_ID)!=TITLE:
                raise ValueError("source ID mismatch")
            models,patterns,backdrops=map(json.loads,(models,patterns,backdrops))
            if not (100<=len(models)<=200 and 200<=len(patterns)<=350 and 50<=len(backdrops)<=150):
                raise ValueError("incomplete source collection")
            if any(len({x["name"].casefold() for x in rows})!=len(rows) for rows in (models,patterns,backdrops)):
                raise ValueError("duplicate source attribute")
            def rarity(x):
                n=x["rarityPermille"]
                if type(n) is not int or not 0<=n<=1000:
                    raise ValueError("invalid rarity")
                return n
            def attr(x,kind,index):
                row={"name":x["name"],"animation":f"{kind}/{index}.tgs","permille":rarity(x)}
                if x.get("crafted"):
                    if kind!="models" or x["name"] not in CRAFT or row["permille"]!=0:
                        raise ValueError("unverified crafting tier for "+x["name"])
                    row.update(crafted=True,rarity=CRAFT[x["name"]])
                return row
            model_specs=[attr(x,"models",i) for i,x in enumerate(models)]
            pattern_specs=[attr(x,"patterns",i) for i,x in enumerate(patterns)]
            def color(x,key):
                n=x[key]
                if type(n) is not int or not 0<=n<=0xffffff:
                    raise ValueError("invalid backdrop color")
                return f"#{n:06x}"
            backdrop_specs=[{"name":x["name"],"center":color(x,"centerColor"),"edge":color(x,"edgeColor"),
                "pattern":color(x,"patternColor"),"text":color(x,"textColor"),"permille":rarity(x)} for x in backdrops]
            manifest={"pack_name":"RiseGram Myth Mask","author":"GiftChanges public asset catalog","gifts":[{
                "id_slug":"myth-mask","title":TITLE,"stars":PRICE,"convert_stars":REDEEM,"base_animation":"base.tgs",
                "limited":True,"availability_total":SUPPLY,"upgrade":{"upgrade_stars":UPGRADE,"supply_total":SUPPLY,
                "slug_prefix":"MythMask","models":model_specs,"patterns":pattern_specs,"backdrops":backdrop_specs}}]}
            LOG.info("RG_MYTH_MASK_ASSETS models=%d patterns=%d backdrops=%d",len(models),len(patterns),len(backdrops))
            assets=[("base.tgs","originals/"+SOURCE_ID+"/Original.tgs")]
            for kind,rows,specs in (("models",models,model_specs),("patterns",patterns,pattern_specs)):
                for x,spec in zip(rows,specs):
                    assets.append((spec["animation"],kind+"/"+gift_dir+"/"+quote(x["name"],safe="")+".tgs"))
            if len(assets)+1>512:
                raise ValueError("pack exceeds native asset count")
            async def animation(path):
                raw=await get(path)
                if raw[:2]!=b"\x1f\x8b":
                    raise ValueError("invalid tgs asset "+path[:150])
                with gzip.GzipFile(fileobj=io.BytesIO(raw)) as gz:
                    obj=json.loads(gz.read((8<<20)+1))
                if not isinstance(obj,dict) or "layers" not in obj or "fr" not in obj:
                    raise ValueError("invalid animation")
                return raw
            payloads=await asyncio.gather(*(animation(path) for _,path in assets))
        buf=io.BytesIO()
        with zipfile.ZipFile(buf,"w",compression=zipfile.ZIP_STORED) as z:
            z.writestr("pack.json",json.dumps(manifest,ensure_ascii=False,separators=(",",":")))
            for (name,_),raw in zip(assets,payloads):
                z.writestr(name,raw)
        data=buf.getvalue()
        if len(data)>32<<20:
            raise ValueError("pack too large")
        digest=hashlib.sha256(data).hexdigest()
        async with ClientSession(headers={"Authorization":"Bearer "+config.MTPROTO_ADMIN_API_TOKEN},
                timeout=ClientTimeout(total=300)) as api:
            for dry in (True,False):
                if not dry and await catalog_status():
                    return
                form=FormData()
                meta={"command_id":"rg-myth-mask-"+digest[:20]+("-dry" if dry else ""),
                    "actor":"risegram-owner","reason":"Owner requested complete Myth Mask release","dry_run":dry}
                form.add_field("metadata",json.dumps(meta),content_type="application/json")
                form.add_field("file",data,filename="myth-mask.zip",content_type="application/zip")
                async with api.post(config.MTPROTO_ADMIN_API_URL+"/v1/gifts/import-pack",data=form,allow_redirects=False) as resp:
                    res=await resp.json(content_type=None)
                    if resp.status!=200 or res.get("status")!="completed" or (dry and res.get("dry_run") is not True):
                        LOG.error("RG_MYTH_MASK_IMPORT_REJECTED dry=%s HTTP=%s message=%s",dry,resp.status,str(res.get("error") or res.get("message") or "")[:240])
                        return
                    LOG.info("RG_MYTH_MASK_IMPORT_RESULT dry=%s result=%s",dry,str(res.get("details",{}).get("gifts"))[:240])
        if await catalog_status():
            LOG.info("RG_MYTH_MASK_READY")
        else:
            LOG.warning("RG_MYTH_MASK_NOT_CONFIRMED")
    except Exception as exc:
        LOG.warning("RG_MYTH_MASK_FAILED %s: %s",type(exc).__name__,str(exc)[:200])
