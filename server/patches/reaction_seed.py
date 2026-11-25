"""Import Telegram-origin emoji and effect assets from pinned public exports.

This is an approximation of the official seven-field reaction catalog; the
original emoji and effects are reused for the phases lacking a public export.
The native official catalog is preferred when supplied by stickerfetch.
Source artwork belongs to Telegram. See NOTICE.txt.
"""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.request import urlopen, Request
from urllib.parse import quote
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
ROWS=[["👍","Thumbs Up","tgs/animated/U+1F44D_1.tgs","webp/animated/U+1F44D_1.webp","tgs/U+1F44D/0.tgs"],["👎","Thumbs Down","tgs/animated/U+1F44E_1.tgs","webp/animated/U+1F44E_1.webp","tgs/U+1F44E/0.tgs"],["❤️","Heart","tgs/animated/U+2764_1.tgs","webp/animated/U+2764_1.webp","tgs/U+2764/0.tgs"],["🔥","Fire","tgs/animated/U+1F525_1.tgs","webp/animated/U+1F525_1.webp","tgs/U+1F525/0.tgs"],["🥰","Smiling with Hearts","tgs/animated/U+1F970_1.tgs","webp/animated/U+1F970_1.webp","tgs/U+1F970/0.tgs"],["👏","Clapping","tgs/animated/U+1F44F_1.tgs","webp/animated/U+1F44F_1.webp","tgs/U+1F44F/0.tgs"],["😁","Grinning","tgs/animated/U+1F601_1.tgs","webp/animated/U+1F601_1.webp","tgs/U+1F601/0.tgs"],["🤔","Thinking","tgs/animated/U+1F914_1.tgs","webp/animated/U+1F914_1.webp","tgs/U+1F914/0.tgs"],["🤯","Mind Blown","tgs/animated/U+1F92F_1.tgs","webp/animated/U+1F92F_1.webp","tgs/U+1F92F/0.tgs"],["😱","Screaming","tgs/animated/U+1F631_1.tgs","webp/animated/U+1F631_1.webp","tgs/U+1F631/0.tgs"],["🤬","Angry","tgs/animated/U+1F92C_1.tgs","webp/animated/U+1F92C_1.webp","tgs/U+1F92C/0.tgs"],["😢","Crying","tgs/animated/U+1F622_1.tgs","webp/animated/U+1F622_1.webp","tgs/U+1F622/0.tgs"],["🎉","Party","tgs/animated/U+1F389_1.tgs","webp/animated/U+1F389_1.webp","tgs/U+1F389/0.tgs"],["🤩","Star Struck","tgs/animated/U+1F929_1.tgs","webp/animated/U+1F929_1.webp","tgs/U+1F929/0.tgs"],["🤮","Vomiting","tgs/animated/U+1F92E_1.tgs","webp/animated/U+1F92E_1.webp","tgs/U+1F92E/0.tgs"],["💩","Poop","tgs/animated/U+1F4A9_1.tgs","webp/animated/U+1F4A9_1.webp","tgs/U+1F4A9/0.tgs"],["🙏","Praying","tgs/animated/U+1F64F_1.tgs","webp/animated/U+1F64F_1.webp","tgs/U+1F64F/0.tgs"],["👌","OK","tgs/animated/U+1F44C_1.tgs","webp/animated/U+1F44C_1.webp","tgs/U+1F44C/0.tgs"],["🕊️","Dove","tgs/animated/U+1F54A_1.tgs","webp/animated/U+1F54A_1.webp","tgs/U+1F54A/0.tgs"],["🤡","Clown","tgs/animated/U+1F921_1.tgs","webp/animated/U+1F921_1.webp","tgs/U+1F921/0.tgs"],["🥱","Yawning","tgs/animated/U+1F971_1.tgs","webp/animated/U+1F971_1.webp","tgs/U+1F971/0.tgs"],["🥴","Woozy","tgs/animated/U+1F974_1.tgs","webp/animated/U+1F974_1.webp","tgs/U+1F974/0.tgs"],["😍","Heart Eyes","tgs/animated/U+1F60D_1.tgs","webp/animated/U+1F60D_1.webp","tgs/U+1F60D/0.tgs"],["🐳","Whale","tgs/animated/U+1F433_1.tgs","webp/animated/U+1F433_1.webp","tgs/U+1F433/0.tgs"],["❤‍🔥","Heart on Fire","tgs/animated/U+2764_U+200D_U+1F525_1.tgs","webp/animated/U+2764_U+200D_U+1F525_1.webp","tgs/U+2764_U+200D_U+1F525/0.tgs"],["🌚","New Moon","tgs/animated/U+1F31A_1.tgs","webp/animated/U+1F31A_1.webp","tgs/U+1F31A/0.tgs"],["🌭","Hot Dog","tgs/animated/U+1F32D_1.tgs","webp/animated/U+1F32D_1.webp","tgs/U+1F32D/0.tgs"],["💯","Hundred","tgs/animated/U+1F4AF_1.tgs","webp/animated/U+1F4AF_1.webp","tgs/U+1F4AF/0.tgs"],["🤣","Rolling Laugh","tgs/animated/U+1F923_1.tgs","webp/animated/U+1F923_1.webp","tgs/U+1F923/0.tgs"],["⚡","Lightning","tgs/animated/U+26A1_1.tgs","webp/animated/U+26A1_1.webp","tgs/U+26A1/0.tgs"],["🍌","Banana","tgs/animated/U+1F34C_1.tgs","webp/animated/U+1F34C_1.webp","tgs/U+1F34C/0.tgs"],["🏆","Trophy","tgs/animated/U+1F3C6_1.tgs","webp/animated/U+1F3C6_1.webp","tgs/U+1F3C6/0.tgs"],["💔","Broken Heart","tgs/animated/U+1F494_1.tgs","webp/animated/U+1F494_1.webp","tgs/U+1F494/0.tgs"],["🤨","Raised Eyebrow","tgs/animated/U+1F928_1.tgs","webp/animated/U+1F928_1.webp","tgs/U+1F928/0.tgs"],["😐","Neutral","tgs/animated/U+1F610_1.tgs","webp/animated/U+1F610_1.webp","tgs/U+1F610/0.tgs"],["🍓","Strawberry","tgs/animated/U+1F353_1.tgs","webp/animated/U+1F353_1.webp","tgs/U+1F353/0.tgs"],["🍾","Champagne","tgs/animated/U+1F37E_1.tgs","webp/animated/U+1F37E_1.webp","tgs/U+1F37E/0.tgs"],["💋","Kiss","tgs/animated/U+1F48B_1.tgs","webp/animated/U+1F48B_1.webp","tgs/U+1F48B/0.tgs"],["🖕","Middle Finger","tgs/animated/U+1F595_1.tgs","webp/animated/U+1F595_1.webp","tgs/U+1F595/0.tgs"],["😈","Devil","tgs/animated/U+1F608_1.tgs","webp/animated/U+1F608_1.webp","tgs/U+1F608/0.tgs"],["😴","Sleeping","tgs/animated/U+1F634_1.tgs","webp/animated/U+1F634_1.webp","tgs/U+1F634/0.tgs"],["😭","Sobbing","tgs/animated/U+1F62D_1.tgs","webp/animated/U+1F62D_1.webp","tgs/U+1F62D/0.tgs"],["😎","Cool","tgs/animated/U+1F60E_1.tgs","webp/animated/U+1F60E_1.webp","tgs/U+1F60E/0.tgs"],["👀","Eyes","tgs/animated/U+1F440_1.tgs","webp/animated/U+1F440_1.webp","tgs/U+1F440/0.tgs"],["😂","Laughing","tgs/animated/U+1F602_1.tgs","webp/animated/U+1F602_1.webp","tgs/U+1F602/0.tgs"]]
ANIM="https://raw.githubusercontent.com/saeedtahmtan/telemoji/4f45fca016c71de9d2a9e15c07ae1fe3d8327a28/"
WEBP="https://media.githubusercontent.com/media/saeedtahmtan/telemoji/4f45fca016c71de9d2a9e15c07ae1fe3d8327a28/"
EFFECT="https://raw.githubusercontent.com/ilyhalight/telegram-emoji-effects/7b68fd20784d297a812f1345db48f28e3934b6e1/"
REQUIRED={"👍","👎","❤️","🔥","😢","😁","🎉"}

def fetch(base,path):
    url=base+quote(path,safe="/")
    for attempt in range(3):
        try:
            with urlopen(Request(url,headers={"User-Agent":"RiseGram reaction seed"}),timeout=25) as response:
                raw=response.read(8*1024*1024+1)
                if not raw or len(raw)>8*1024*1024: raise ValueError("invalid asset size")
                return raw
        except Exception:
            if attempt==2: raise
            time.sleep(attempt+1)

def tgs(base,path):
    body=fetch(base,path)
    data=json.loads(gzip.decompress(body))
    if not data.get("layers") or data.get("w",0)<=0 or data.get("h",0)<=0: raise ValueError("invalid TGS")
    return body,data

def webp(image,size):
    image=image.convert("RGBA")
    image.thumbnail((size,size),Image.Resampling.LANCZOS)
    canvas=Image.new("RGBA",(size,size))
    canvas.alpha_composite(image,((size-image.width)//2,(size-image.height)//2))
    buf=io.BytesIO()
    canvas.save(buf,format="WEBP",quality=90)
    return buf.getvalue()

def make(item):
    emoji,title,emoji_path,preview_path,effect_path=item
    try:
        animation,meta=tgs(ANIM,emoji_path)
        effect,effect_meta=tgs(EFFECT,effect_path)
        try:
            raw=fetch(WEBP,preview_path)
            image=Image.open(io.BytesIO(raw))
        except Exception:
            image=Image.open(io.BytesIO(fetch("https://saeedtahmtan.github.io/telemoji/",preview_path)))
        # Prefer a visible final frame so the chat chip does not become blank.
        chosen=None
        for i in reversed(range(image.n_frames if hasattr(image,"n_frames") else 1)):
            image.seek(i)
            frame=image.convert("RGBA")
            if frame.getchannel("A").getbbox():
                chosen=frame
                break
        if chosen is None: raise ValueError("transparent preview")
        still=webp(chosen,512)
        thumb=webp(chosen,128)
        idx=ROWS.index(item)
        def doc(slot,body,mime,w,h):
            did=8860000000000010000+idx*10+slot
            digest=hashlib.sha256(body).hexdigest()
            ext=".tgs" if mime=="application/x-tgsticker" else ".webp"
            (BIN/(str(did)+ext)).write_bytes(body)
            (BIN/(str(did)+"_thumb1_PhotoSize_typem.jpg")).write_bytes(thumb)
            return {"id":did,"access_hash":int(digest[:15],16),"file_reference":digest[:32],"date":"2026-11-25T00:00:00Z","mime_type":mime,"size":len(body),"dc_id":2,
                "attributes":[{"_":"DocumentAttributeImageSize","w":w,"h":h}],
                "thumbs":[{"_":"PhotoSize","type":"m","w":128,"h":128,"size":len(thumb)}]}
        static=doc(1,still,"image/webp",512,512)
        anim=doc(2,animation,"application/x-tgsticker",meta["w"],meta["h"])
        fx=doc(3,effect,"application/x-tgsticker",effect_meta["w"],effect_meta["h"])
        return {"reaction":emoji,"title":title,"inactive":False,"premium":False,"static_icon":static,
                "appear_animation":anim,"select_animation":anim,"activate_animation":anim,
                "effect_animation":fx,"around_animation":fx,"center_icon":static}
    except Exception as exc:
        print("Telegram asset unavailable",emoji,type(exc).__name__,str(exc)[:160],flush=True)
        return None

with ThreadPoolExecutor(max_workers=8) as pool:
    rows=[r for r in pool.map(make,ROWS) if r]
missing={r[0] for r in ROWS}- {r["reaction"] for r in rows}
if missing: raise SystemExit("Telegram asset import incomplete: "+repr(missing))
if REQUIRED-{r["reaction"] for r in rows}: raise SystemExit("Required reaction assets missing")
(META/"available_reactions_raw.json").write_text(json.dumps({"result":{"reactions":rows}},ensure_ascii=False))
(ROOT/"NOTICE.txt").write_text("Telegram emoji artwork and effects: Telegram. Public exports: https://github.com/saeedtahmtan/telemoji and https://github.com/ilyhalight/telegram-emoji-effects. Use subject to Telegram terms.\n")
print("Prepared",len(rows),"Telegram-origin reactions")
