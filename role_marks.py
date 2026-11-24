"""Role marks are native custom emoji, not bundled Android drawables.
They are cosmetic role labels; they do not grant administrative permissions."""
import asyncio
import gzip
import json
import logging
import tempfile
import time
from pathlib import Path

from aiohttp import ClientSession, ClientTimeout
import config
import database as db
import native_economy as economy
from issuer_marks import command

LOG=logging.getLogger("risegram.roles")
OWNER=1780243200
ROLES={
    "developer":("RiseGram Developer","Официальный аккаунт разработчика «RiseGram»."),
    "tester":("RiseGram Tester","Официальный аккаунт тестера «RiseGram»."),
    "admin":("RiseGram Admin","Официальный аккаунт администратора «RiseGram»."),
    "headadmin":("RiseGram Head Admin","Официальный аккаунт главного администратора «RiseGram»."),
    "support":("RiseGram Support","Официальный аккаунт поддержки «RiseGram»."),
    "hold":("Hold","Аккаунт верифицирован организацией «Hold»."),
}

def polygon(points,color,opacity=100):
    zeros=[[0,0] for _ in points]
    return {"ty":"gr","it":[{"ty":"sh","ks":{"a":0,"k":{"i":zeros,"o":zeros,"v":points,"c":True}}},
      {"ty":"fl","c":{"a":0,"k":[*color,1]},"o":{"a":0,"k":opacity},"r":1},
      {"ty":"tr","p":{"a":0,"k":[0,0]},"a":{"a":0,"k":[0,0]},"s":{"a":0,"k":[100,100]},"r":{"a":0,"k":0},"o":{"a":0,"k":100}}]}

def make_badge(role,path):
    white=[1,1,1]
    colors={"developer":[.31,.34,.94],"tester":[.1,.64,.83],"admin":[.2,.67,.48],"headadmin":[.83,.55,.14],"support":[.52,.36,.88],"hold":[0,0,0]}
    # Heavy silhouettes and few interior strokes stay legible at 14px.
    shell=[[-162,-113],[-85,-177],[85,-177],[162,-113],[162,92],[85,163],[0,197],[-85,163],[-162,92]]
    glyphs={
      "developer":[[[-110,10],[-50,-68],[-20,-45],[-64,10],[-20,65],[-50,88]],[[110,10],[50,-68],[20,-45],[64,10],[20,65],[50,88]],[[5,-97],[39,-83],[-5,103],[-39,89]]],
      "tester":[[[-65,-101],[65,-101],[65,-66],[32,-66],[32,-24],[100,91],[80,120],[-80,120],[-100,91],[-32,-24],[-32,-66],[-65,-66]]],
      "admin":[[[-91,5],[-24,71],[101,-60],[126,-34],[-24,116],[-117,31]]],
      "headadmin":[[[-118,-43],[-57,13],[0,-91],[57,13],[118,-43],[87,92],[-87,92]],[[-87,112],[87,112],[87,135],[-87,135]]],
      "support":[[[-106,5],[-106,-46],[-71,-94],[0,-114],[71,-94],[106,-46],[106,5],[70,5],[70,-41],[46,-66],[0,-78],[-46,-66],[-70,-41],[-70,5]],[[-113,-3],[-69,-3],[-69,69],[-113,69]],[[69,-3],[113,-3],[113,69],[69,69]],[[84,58],[112,70],[93,112],[18,112],[18,88],[70,88]]],
      "hold":[],
    }
    shapes=[polygon(shell,colors[role],0 if role=="hold" else 100)]+[polygon(v,white) for v in glyphs[role]]
    layer={"ty":4,"ind":1,"nm":role,"sr":1,"ks":{"o":{"a":0,"k":100},"r":{"a":0,"k":0},"p":{"a":0,"k":[256,256,0]},"a":{"a":0,"k":[0,0,0]},"s":{"a":0,"k":[100,100,100]}},"shapes":shapes,"ip":0,"op":60,"st":0,"bm":0}
    animation={"v":"5.7.4","fr":60,"ip":0,"op":60,"w":512,"h":512,"nm":"RiseGram "+role,"ddd":0,"assets":[],"layers":[layer],"tgs":1}
    path.write_bytes(gzip.compress(json.dumps(animation,separators=(",",":")).encode(),mtime=0))

async def lookup(target):
    name=target.strip().lstrip("@").lower()
    async with economy.transaction() as conn:
        rows=await (await conn.execute("""SELECT DISTINCT p.peer_type,p.peer_id FROM peer_usernames p
            WHERE p.username_lower=%s AND p.active""",(name,))).fetchall()
        if len(rows)!=1:
            raise ValueError("Активный @юзернейм не найден")
        r=rows[0]
        if r["peer_type"]=="user":
            entity=await (await conn.execute("SELECT id,is_bot FROM users WHERE id=%s AND deleted_at IS NULL",(r["peer_id"],))).fetchone()
        else:
            entity=await (await conn.execute("SELECT id,megagroup FROM channels WHERE id=%s AND NOT deleted",(r["peer_id"],))).fetchone()
        if not entity:
            raise ValueError("Аккаунт или чат удалён")
        return r["peer_type"],int(r["peer_id"]),entity

async def mark(target,role,remove=False):
    if role not in ROLES:
        raise ValueError("Неизвестный статус")
    peer,pid,entity=await lookup(target)
    if role!="hold" and (peer!="user" or entity["is_bot"]):
        raise ValueError("Статусы сотрудников выдаются только личным аккаунтам")
    async with economy.transaction() as conn:
        row=await (await conn.execute("SELECT bot_id FROM bot_verifier_settings WHERE company_name=%s AND enabled",(ROLES[role][0],))).fetchone()
        if not row:
            raise ValueError("Метка ещё не опубликована")
        bid=int(row["bot_id"])
        old=await (await conn.execute("SELECT verifier_bot_id FROM custom_verifications WHERE peer_type=%s AND peer_id=%s",(peer,pid))).fetchone()
    if remove and (not old or old["verifier_bot_id"]!=bid):
        raise ValueError("У профиля нет этой метки")
    description=ROLES[role][1]
    if role=="hold":
        entity_label=("Бот верифицирован" if entity["is_bot"] else "Аккаунт верифицирован") if peer=="user" else ("Группа верифицирована" if entity["megagroup"] else "Канал верифицирован")
        description=entity_label+" организацией «Hold»."
    async with ClientSession(headers={"Authorization":"Bearer "+config.MTPROTO_ADMIN_API_TOKEN},timeout=ClientTimeout(total=20)) as session:
        await command(session,"/v1/botverification/marks/"+("revoke" if remove else "grant"),
          "rg-role-"+role+"-"+str(pid)+"-"+str(time.time_ns()),{"verifier_bot_id":bid,"peer_type":peer,"peer_id":pid,**({} if remove else {"description":description})})
    return description

async def provision():
    if not config.MTPROTO_ADMIN_API_TOKEN:
        return
    async with economy.transaction() as conn:
        owner=await (await conn.execute("""SELECT u.id FROM users u JOIN peer_usernames p ON p.peer_id=u.id
            AND p.peer_type='user' WHERE u.id=%s AND p.username_lower='owner' AND p.active AND NOT u.is_bot AND u.deleted_at IS NULL""",(OWNER,))).fetchone()
        if not owner:
            raise ValueError("Native owner mismatch")
    async with ClientSession(headers={"Authorization":"Bearer "+config.MTPROTO_ADMIN_API_TOKEN},timeout=ClientTimeout(total=25)) as session:
        with tempfile.TemporaryDirectory() as tmp:
            for role,(label,description) in ROLES.items():
                try:
                    prefix="rg-role-v1-"+role
                    async with economy.transaction() as conn:
                        existing=await (await conn.execute("SELECT bot_id FROM bot_verifier_settings WHERE company_name=%s AND enabled",(label,))).fetchone()
                        if existing:
                            continue
                        bot=await (await conn.execute("SELECT u.id FROM users u JOIN bots b ON b.bot_user_id=u.id WHERE lower(u.username)=%s AND b.owner_user_id=%s AND u.deleted_at IS NULL",("rg_"+role+"_mark_bot",OWNER))).fetchone()
                    if bot:
                        bid=int(bot["id"])
                    else:
                        bot=await command(session,"/v1/bots/create",prefix+"-bot",{"owner_user_id":OWNER,"name":label,"username":"rg_"+role+"_mark_bot"})
                        bid=int(bot["bot_user_id"])
                    path=Path(tmp)/(role+".tgs")
                    make_badge(role,path)
                    pack=await command(session,"/v1/stickers/create",prefix+"-pack",{"title":label+" Mark","short_name":"rg_"+role+"_mark","kind":"emoji","emoji":"☑️"},path)
                    if "document_id" not in pack:
                        pack=await command(session,"/v1/stickers/add",prefix+"-doc",{"set_id":str(pack["set_id"]),"emoji":"☑️"},path)
                    did=int(pack["document_id"])
                    await command(session,"/v1/botverification/icons/upsert",prefix+"-icon",{"document_id":did,"name":label,"owner_bot_id":bid})
                    await command(session,"/v1/botverification/verifiers/grant",prefix+"-verifier",{"bot_id":bid,"icon_document_id":did,"company_name":label,"default_description":description,"can_modify_custom_description":True})
                    LOG.info("RG_ROLE_READY %s",role)
                except Exception as exc:
                    LOG.warning("RG_ROLE_FAILED %s %s",role,str(exc)[:160])
    if not await db.get_meta("native_owner_developer_mark_v1"):
        await mark("@owner","developer")
        await db.set_meta("native_owner_developer_mark_v1","1")
        LOG.info("RG_OWNER_DEVELOPER_READY")

async def bootstrap():
    for delay in (5,30,90):
        await asyncio.sleep(delay)
        try:
            await provision()
            return
        except Exception as exc:
            LOG.warning("Role mark setup: %s",str(exc)[:160])
