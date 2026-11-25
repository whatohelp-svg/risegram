"""Validate and package a complete operator-supplied Telegram stickerfetch export."""
import gzip
import hashlib
import json
from pathlib import Path
import shutil
import sys
from PIL import Image

src=Path(sys.argv[1])/"telegram_reactions_export"
dest=Path(sys.argv[2])/"telegram_reactions_export"
raw=src/"global_json"/"available_reactions_raw.json"
payload=raw.read_bytes()
rows=json.loads(payload)["result"]["reactions"]
assert len(rows)>=25, "Incomplete official reaction catalog"
assert len({r["reaction"] for r in rows})==len(rows), "Duplicate reactions"
required={"👍","👎","❤️","🔥","😢","😁","🎉"}
active={r["reaction"] for r in rows if not r.get("inactive")}
assert required<=active, "Missing core Telegram reactions"
files=src/"reactions"
seen={}
mandatory=("static_icon","appear_animation","select_animation","activate_animation","effect_animation")
for r in rows:
    assert all(r.get(k) for k in mandatory), ("Missing mandatory role",r["reaction"])
    assert bool(r.get("center_icon"))==bool(r.get("around_animation")), ("Invalid paired flags",r["reaction"])
    if r["reaction"] in required:
        assert r.get("center_icon") and r.get("around_animation"), ("No bubble animation",r["reaction"])
    for k in mandatory+("center_icon","around_animation"):
        d=r.get(k)
        if not d: continue
        did=int(d["id"])
        assert did>0 and did<2**63
        assert not 8860000000000000000<=did<8860000000000001000, "Synthetic Noto document"
        mime=d["mime_type"]
        ext={"image/webp":"webp","application/x-tgsticker":"tgs"}[mime]
        file=files/f"{did}.{ext}"
        assert file.is_file(), f"Missing document {did}"
        data=file.read_bytes()
        assert len(data)==int(d["size"]) and 0<len(data)<=4*1024*1024, f"Bad document size {did}"
        digest=hashlib.sha256(data).hexdigest()
        assert seen.setdefault(did,digest)==digest
        if ext=="tgs":
            assert data[:2]==b"\x1f\x8b"
            with gzip.open(file,"rb") as z:
                decoded=z.read(16*1024*1024+1)
            assert len(decoded)<=16*1024*1024
            anim=json.loads(decoded)
            assert anim.get("layers") and anim.get("op",0)>anim.get("ip",0)
        else:
            with Image.open(file) as im: im.verify()
        for t in d.get("thumbs",[]):
            if t.get("_")!="PhotoSize":continue
            matches=list(files.glob(f"{did}_thumb*_PhotoSize_type{t['type']}.jpg"))
            assert matches, f"Missing thumbnail for {did}"
            with Image.open(matches[0]) as im: im.verify()
shutil.copytree(src,dest,dirs_exist_ok=True)
(Path(sys.argv[2])/"official-reactions.ready").write_text(hashlib.sha256(payload).hexdigest())
print("Validated official reaction export:",len(rows),"reactions,",len(seen),"documents")
