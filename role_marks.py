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

# Shared white rosette with genuine transparent negative-space glyphs.
# Each glyph is drawn as a handful of broad closed contours for 14–20px use.
GLYPHS={
    "developer":[
        [[-110,0],[-53,-62],[-31,-41],[-69,0],[-31,41],[-53,62]],
        [[110,0],[53,-62],[31,-41],[69,0],[31,41],[53,62]],
        [[13,-83],[40,-74],[-13,83],[-40,74]],
    ],
    "tester":[
        [[-48,-91],[48,-91],[48,-65],[26,-65],[26,-20],[82,67],[78,85],[60,98],[-60,98],[-78,85],[-82,67],[-26,-20],[-26,-65],[-48,-65]],
    ],
    "admin":[
        [[-102,-3],[-76,-28],[-25,23],[76,-78],[102,-51],[-25,76]],
    ],
    "headadmin":[
        [[-94,-20],[0,-105],[94,-20],[73,6],[0,-59],[-73,6]],
        [[-94,54],[0,-31],[94,54],[73,80],[0,15],[-73,80]],
    ],
    "support":[
        [[-97,8],[-97,-27],[-87,-58],[-65,-82],[-34,-96],[0,-100],[34,-96],[65,-82],[87,-58],[97,-27],[97,51],[87,78],[63,96],[16,96],[16,70],[58,70],[69,61],[69,47],[43,47],[43,-12],[69,-12],[69,-27],[61,-49],[45,-65],[22,-73],[0,-75],[-22,-73],[-45,-65],[-61,-49],[-69,-27],[-69,-12],[-43,-12],[-43,47],[-97,47]],
    ],
    "hold":[[[0,-92],[24,-25],[92,0],[24,25],[0,92],[-24,25],[-92,0],[-24,-25]]],
}

def rosette(role):
    import math
    points=[]
    tangents=[]
    for i in range(32):
        a=-math.pi/2+i*math.pi/16
        r=168+13*math.cos(8*a)
        dr=-104*math.sin(8*a)
        points.append([round(r*math.cos(a),3),round(r*math.sin(a),3)])
        tangents.append([round((dr*math.cos(a)-r*math.sin(a))*math.pi/48,3),
                         round((dr*math.sin(a)+r*math.cos(a))*math.pi/48,3)])
    def shape(vertices,ins,outs):
        return {"ty":"sh","ks":{"a":0,"k":{"i":ins,"o":outs,"v":vertices,"c":True}}}
    paths=[shape(points,[[-x,-y] for x,y in tangents],tangents)]
    for vertices in GLYPHS[role]:
        zeros=[[0,0] for _ in vertices]
        paths.append(shape(vertices,zeros,zeros))
    return {"ty":"gr","it":paths+[{"ty":"fl","c":{"a":0,"k":[1,1,1,1]},"o":{"a":0,"k":100},"r":2},
        {"ty":"tr","p":{"a":0,"k":[0,0]},"a":{"a":0,"k":[0,0]},"s":{"a":0,"k":[100,100]},"r":{"a":0,"k":0},"o":{"a":0,"k":100}}]}


def make_badge(role,path):
    shapes=[rosette(role)]
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
                        existing=await (await conn.execute("SELECT bot_id,icon_document_id,version FROM bot_verifier_settings WHERE company_name=%s AND enabled",(label,))).fetchone()
                        meta_key="native_hold_rosette_v2" if role=="hold" else "native_role_rosette_v3:"+role
                        if existing and await db.get_meta(meta_key)==str(existing["icon_document_id"]):
                            continue
                        bot=await (await conn.execute("SELECT u.id FROM users u JOIN bots b ON b.bot_user_id=u.id WHERE lower(u.username)=%s AND b.owner_user_id=%s AND u.deleted_at IS NULL",("rg_"+role+"_mark_bot",OWNER))).fetchone()
                    if existing:
                        bid=int(existing["bot_id"])
                    elif bot:
                        bid=int(bot["id"])
                    else:
                        bot=await command(session,"/v1/bots/create",prefix+"-bot",{"owner_user_id":OWNER,"name":label,"username":"rg_"+role+"_mark_bot"})
                        bid=int(bot["bot_user_id"])
                    prefix="rg-role-v2-hold" if role=="hold" else "rg-role-v3-"+role
                    path=Path(tmp)/(role+".tgs")
                    make_badge(role,path)
                    pack=await command(session,"/v1/stickers/create",prefix+"-pack",{"title":label+" Mark","short_name":"rg_hold_rosette_v2" if role=="hold" else "rg_"+role+"_rosette_v3","kind":"emoji","emoji":"☑️"},path)
                    if "document_id" not in pack:
                        pack=await command(session,"/v1/stickers/add",prefix+"-doc",{"set_id":str(pack["set_id"]),"emoji":"☑️"},path)
                    did=int(pack["document_id"])
                    await command(session,"/v1/botverification/icons/upsert",prefix+"-icon",{"document_id":did,"name":label,"owner_bot_id":bid})
                    fields={"bot_id":bid,"icon_document_id":did,"company_name":label,"default_description":description,"can_modify_custom_description":True}
                    if existing:
                        fields["version"]=int(existing["version"])
                    if not existing or int(existing["icon_document_id"])!=did:
                        await command(session,"/v1/botverification/verifiers/grant",prefix+"-verifier-"+str(fields.get("version",0)),fields)
                    # Preserve existing recipients, descriptions and permissions.
                    async with economy.transaction() as conn:
                        await conn.execute("""UPDATE custom_verifications SET icon_document_id=%s,updated_at=now(),version=version+1
                            WHERE verifier_bot_id=%s AND icon_document_id<>%s""",(did,bid,did))
                    await db.set_meta(meta_key,str(did))
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
