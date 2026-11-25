"""Build a local native reaction catalog, without user sessions or Telegram API.
Artwork: Google's Animated Noto Emoji (CC BY 4.0), adapted to TGS and static WebP.
https://googlefonts.github.io/noto-emoji-files/?view=docs
"""
import concurrent.futures
import gzip
import io
import json
import hashlib
import sys
import time
import urllib.request
from pathlib import Path
from PIL import Image

ROOT=Path(sys.argv[1])/"telegram_reactions_export"
MEDIA=ROOT/"reactions"
META=ROOT/"global_json"
MEDIA.mkdir(parents=True,exist_ok=True)
META.mkdir(parents=True,exist_ok=True)
ITEMS=[
("👍","Thumbs Up"),("❤️","Red Heart"),("😂","Face With Tears of Joy"),
("😮","Face With Open Mouth"),("😢","Crying Face"),("🙏","Folded Hands"),
("👎","Thumbs Down"),("🔥","Fire"),("🥰","Smiling Face With Hearts"),
("👏","Clapping Hands"),("😁","Beaming Face"),("🤔","Thinking Face"),
("🤯","Exploding Head"),("😱","Face Screaming in Fear"),("🤬","Face With Symbols"),
("🤩","Star-Struck"),("🎉","Party Popper"),("😍","Heart Eyes"),("🤮","Vomiting Face"),
("💩","Pile of Poo"),("🤣","Rolling on the Floor Laughing"),("💔","Broken Heart"),
("😎","Sunglasses"),("🤡","Clown"),("🥱","Yawning Face"),("🥴","Woozy Face"),
("😴","Sleeping Face"),("💯","Hundred Points"),("⚡","High Voltage"),("🏆","Trophy"),
("😭","Loudly Crying Face"),("😐","Neutral Face"),("👀","Eyes"),("💋","Kiss Mark"),
("🗿","Moai"),("🤝","Handshake"),("👻","Ghost"),("💅","Nail Polish"),("🫡","Saluting Face"),
("🤗","Hugging Face"),("🆒","Cool"),("🍓","Strawberry"),("🍌","Banana"),("🌚","New Moon Face"),
("☃️","Snowman"),("🎅","Santa Claus"),("🎄","Christmas Tree"),("☕","Hot Beverage"),
("🦄","Unicorn"),("🐳","Spouting Whale"),("💘","Heart With Arrow"),("🙈","See-No-Evil Monkey"),
("😈","Smiling Face With Horns"),("🤓","Nerd Face"),("🤷‍♂️","Man Shrugging"),
("🤷‍♀️","Woman Shrugging"),("🤷","Person Shrugging"),("🥺","Pleading Face"),
("😇","Smiling Face With Halo"),("🤪","Zany Face"),("😡","Pouting Face"),("🫠","Melting Face"),
]
BASE="https://fonts.gstatic.com/s/e/notoemoji/17.0/"
def fetch(url):
    for attempt in range(3):
        try:
            with urllib.request.urlopen(url,timeout=20) as f:
                data=f.read(8*1024*1024+1)
            if not data or len(data)>8*1024*1024: raise ValueError("invalid asset size")
            return data
        except Exception:
            if attempt==2: raise
            time.sleep(attempt+1)
def make(item):
    emoji,title=item
    code="_".join(format(ord(c),"x") for c in emoji if ord(c)!=0xfe0f)
    base=BASE+code+"/"
    raw=fetch(base+"lottie.json")
    anim=json.loads(raw)
    if not anim.get("layers") or not (0<anim["w"]<=2048 and 0<anim["h"]<=2048):
        raise ValueError("invalid Lottie")
    # TGS containers use compressed vector Lottie. Keep source artwork unchanged.
    anim["tgs"]=1
    tgs=gzip.compress(json.dumps(anim,separators=(",",":")).encode(),mtime=0)
    try: preview=fetch(base+"512.webp")
    except Exception: preview=fetch(base+"512.gif")
    im=Image.open(io.BytesIO(preview))
    im.seek(0)
    im=im.convert("RGBA")
    im.thumbnail((512,512),Image.Resampling.LANCZOS)
    icon=io.BytesIO()
    im.save(icon,format="WEBP",lossless=True)
    static=icon.getvalue()
    im.thumbnail((128,128),Image.Resampling.LANCZOS)
    thumb=io.BytesIO()
    im.save(thumb,format="WEBP",lossless=True)
    thumb=thumb.getvalue()
    # Stable RiseGram-owned document IDs, independent of the external provider.
    number=int.from_bytes(hashlib.sha256(emoji.encode()).digest()[:6],"big")
    def doc(slot,data,mime,w,h):
        did=880000000000000000+number*10+slot
        ext="webp" if mime=="image/webp" else "tgs"
        (MEDIA/f"{did}.{ext}").write_bytes(data)
        (MEDIA/f"{did}_thumb1_PhotoSize_typem.jpg").write_bytes(thumb)
        return {"id":did,"access_hash":did+1,"file_reference":hashlib.sha256(data).hexdigest()[:32],
                "date":"2026-11-25T00:00:00Z","mime_type":mime,"size":len(data),"dc_id":2,
                "attributes":[{"_":"DocumentAttributeImageSize","w":w,"h":h}],
                "thumbs":[{"_":"PhotoSize","type":"m","w":im.width,"h":im.height,"size":len(thumb)}]}
    # Separate animation roles have independent identities; all use the same
    # source motion initially, rather than invalid empty placeholder documents.
    result={"reaction":emoji,"title":title}
    result["static_icon"]=doc(1,static,"image/webp",512,512)
    for slot,name in enumerate(("appear_animation","select_animation","activate_animation","effect_animation","center_icon"),2):
        result[name]=doc(slot,tgs,"application/x-tgsticker",anim["w"],anim["h"])
    # Center icon is optional and paired with an around animation in MTProto.
    result.pop("center_icon")
    return result
def safe(item):
    try:return make(item)
    except Exception as exc:
        print("reaction asset unavailable",item[0],type(exc).__name__,flush=True)
        return None
with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
    results=list(executor.map(safe,ITEMS))
if any(r is None for r in results[:6]):
    raise SystemExit("Required default reaction assets missing; refusing incomplete deployment")
rows=[r for r in results if r]
(META/"available_reactions_raw.json").write_text(json.dumps({"result":{"reactions":rows}},ensure_ascii=False),encoding="utf-8")
(ROOT/"ATTRIBUTION.txt").write_text("Google — Animated Noto Emoji, CC BY 4.0 https://creativecommons.org/licenses/by/4.0/ . Source: https://googlefonts.github.io/noto-emoji-files/ . Adapted by RiseGram: compressed Lottie and extracted WebP preview frames.\n")
print("RiseGram reaction seed ready:",len(rows),flush=True)
