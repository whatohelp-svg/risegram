"""Build a native reaction seed from Google Animated Noto Emoji (CC BY 4.0).
https://googlefonts.github.io/noto-emoji-animation/
Assets are converted to gzip TGS and static WebP; no Telegram session is used.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import urlopen, Request
import gzip
import hashlib
import io
import json
import sys
import time
from PIL import Image

ROOT=Path(sys.argv[1])/"telegram_reactions_export"
BIN=ROOT/"reactions"
META=ROOT/"global_json"
BIN.mkdir(parents=True,exist_ok=True)
META.mkdir(parents=True,exist_ok=True)
REACTIONS=[("👍","Thumbs Up"),("👎","Thumbs Down"),("❤️","Heart"),("🔥","Fire"),("🥰","Smiling with Hearts"),("👏","Clapping"),("😁","Grinning"),("🤔","Thinking"),("🤯","Mind Blown"),("😱","Screaming"),("🤬","Angry"),("😢","Crying"),("🎉","Party"),("🤩","Star Struck"),("🤮","Vomiting"),("💩","Poop"),("🙏","Praying"),("👌","OK"),("🕊️","Dove"),("🤡","Clown"),("🥱","Yawning"),("🥴","Woozy"),("😍","Heart Eyes"),("🐳","Whale"),("❤‍🔥","Heart on Fire"),("🌚","New Moon"),("🌭","Hot Dog"),("💯","Hundred"),("🤣","Rolling Laugh"),("⚡","Lightning"),("🍌","Banana"),("🏆","Trophy"),("💔","Broken Heart"),("🤨","Raised Eyebrow"),("😐","Neutral"),("🍓","Strawberry"),("🍾","Champagne"),("💋","Kiss"),("🖕","Middle Finger"),("😈","Devil"),("😴","Sleeping"),("😭","Sobbing"),("😎","Cool"),("👀","Eyes"),("😂","Laughing"),("⭐","Star")]
BASE="https://fonts.gstatic.com/s/e/notoemoji/latest/"
REQUIRED={"👍","👎","❤️","🔥","😢","😁","🎉"}

def fetch(url):
    for attempt in range(3):
        try:
            with urlopen(Request(url,headers={"User-Agent":"RiseGram reaction seed"}),timeout=18) as response:
                raw=response.read(4*1024*1024+1)
                if not raw or len(raw)>4*1024*1024: raise ValueError("invalid asset size")
                return raw
        except Exception:
            if attempt==2: raise
            time.sleep(attempt+1)

def webp(image,size):
    image=image.convert("RGBA")
    image.thumbnail((size,size),Image.Resampling.LANCZOS)
    canvas=Image.new("RGBA",(size,size))
    canvas.alpha_composite(image,((size-image.width)//2,(size-image.height)//2))
    buf=io.BytesIO()
    canvas.save(buf,format="WEBP",quality=85)
    return buf.getvalue()

def make(item):
    emoji,title=item
    code="_".join(f"{ord(c):x}" for c in emoji if ord(c)!=0xfe0f)
    # Noto multi-codepoint paths use underscores.
    try:
        raw=fetch(BASE+code+"/lottie.json")
        data=json.loads(raw)
        if not data.get("layers") or data.get("w",0)<=0 or data.get("h",0)<=0: raise ValueError("not a Lottie animation")
        if any(a.get("p") for a in data.get("assets",[])): raise ValueError("external raster assets unsupported")
        data["tgs"]=1
        animation=gzip.compress(json.dumps(data,separators=(",",":"),ensure_ascii=False).encode(),mtime=0)
        image=Image.open(io.BytesIO(fetch(BASE+code+"/512.webp")))
        image.seek(0)
        still=webp(image,512)
        thumb=webp(image,128)
        idx=REACTIONS.index(item)
        def doc(slot,body,mime,w,h):
            did=8860000000000000000+idx*10+slot
            digest=hashlib.sha256(body).hexdigest()
            ext=".tgs" if mime=="application/x-tgsticker" else ".webp"
            (BIN/(str(did)+ext)).write_bytes(body)
            (BIN/(str(did)+"_thumb1_PhotoSize_typem.jpg")).write_bytes(thumb)
            return {"id":did,"access_hash":int(digest[:15],16),"file_reference":digest[:32],"date":"2026-11-25T00:00:00Z","mime_type":mime,"size":len(body),"dc_id":2,
                "attributes":[{"_":"DocumentAttributeImageSize","w":w,"h":h}],
                "thumbs":[{"_":"PhotoSize","type":"m","w":128,"h":128,"size":len(thumb)}]}
        row={"reaction":emoji,"title":title,"inactive":False,"premium":False,"static_icon":doc(1,still,"image/webp",512,512)}
        for i,field in enumerate(("appear_animation","select_animation","activate_animation","effect_animation","center_icon"),2):
            row[field]=doc(i,animation,"application/x-tgsticker",data["w"],data["h"])
        # TL availableReaction uses a shared flag for center_icon and around_animation.
        # Both fields must be present or neither may be encoded.
        row["around_animation"]=row["effect_animation"]
        # Keep a stable visible WebP in message bubbles after the animated effect.
        row["center_icon"]=row["static_icon"]
        return row
    except Exception as exc:
        print("Noto reaction unavailable",emoji,type(exc).__name__,str(exc)[:120],flush=True)
        return None

with ThreadPoolExecutor(max_workers=8) as pool:
    rows=[r for r in pool.map(make,REACTIONS) if r]
missing=REQUIRED-{r["reaction"] for r in rows}
if missing: raise SystemExit("Required reaction assets missing: "+repr(missing))
(META/"available_reactions_raw.json").write_text(json.dumps({"result":{"reactions":rows}},ensure_ascii=False))
(ROOT/"NOTICE.txt").write_text("Animated Noto Emoji by Google, CC BY 4.0 https://creativecommons.org/licenses/by/4.0/\nhttps://googlefonts.github.io/noto-emoji-animation/\nConverted to TGS and static WebP for RiseGram.\n")
print("Prepared",len(rows),"native reactions")
