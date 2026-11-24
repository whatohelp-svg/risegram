"""Native moderation bridge for RiseIris. Only authenticated, original group
commands from actual group moderators can change member restrictions."""
import hmac
import logging
import re
import time
from aiohttp import web
from psycopg.types.json import Jsonb
import native_economy as economy

BOT=1780243215
LOG=logging.getLogger("risegram.iris")
ACTIONS={"мут":"mute","mute":"mute","бан":"ban","ban":"ban","варн":"warn","warn":"warn",
         "размут":"unmute","unmute":"unmute","разбан":"unban","unban":"unban",
         "анварн":"unwarn","unwarn":"unwarn","clearwarn":"clearwarn","снять варны":"clearwarn",
         "варны":"warnings","warnings":"warnings"}
SEND_KEYS="SendMessages SendMedia SendStickers SendGifs SendGames SendInline EmbedLinks SendPolls SendPhotos SendVideos SendRoundvideos SendAudios SendVoices SendDocs SendPlain SendReactions".split()
UNITS={"s":1,"сек":1,"с":1,"m":60,"м":60,"мин":60,"h":3600,"ч":3600,"час":3600,"d":86400,"д":86400,"дн":86400,"w":604800,"н":604800,"нед":604800}

async def setup():
    async with economy.transaction() as conn:
        await conn.execute("""CREATE TABLE IF NOT EXISTS riseiris_receipts(
            update_id bigint PRIMARY KEY, result jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now())""")
        await conn.execute("""CREATE TABLE IF NOT EXISTS riseiris_restrictions(
            channel_id bigint NOT NULL,user_id bigint NOT NULL,kind text NOT NULL,
            rights jsonb NOT NULL,previous_rights jsonb NOT NULL,previous_status text NOT NULL,
            previous_left integer NOT NULL,expires integer NOT NULL,
            PRIMARY KEY(channel_id,user_id))""")
        await conn.execute("""CREATE TABLE IF NOT EXISTS riseiris_warnings(
            update_id bigint PRIMARY KEY,channel_id bigint NOT NULL,user_id bigint NOT NULL,
            actor_id bigint NOT NULL,reason text NOT NULL,expires integer NOT NULL,
            revoked boolean NOT NULL DEFAULT false)""")

def result(body,status=200):
    return web.json_response(body,status=status,headers={"Cache-Control":"no-store"})

def manager(member):
    return member and member["status"]=="active" and (member["role"]=="creator" or
        (member["role"]=="admin" and member["admin_rights"].get("BanUsers") is True))

def parse(text):
    parts=text.strip().split()
    if not parts:
        raise ValueError("Пустая команда")
    cmd=parts.pop(0).lstrip("/").lower()
    if "@" in cmd:
        cmd,suffix=cmd.split("@",1)
        if suffix!="riseirisbot":
            raise ValueError("Команда адресована другому боту")
    action=ACTIONS.get(cmd)
    if not action:
        raise ValueError("Неизвестная команда")
    name=None
    if parts and parts[0].startswith("@"):
        name=parts.pop(0)[1:].lower()
        if not re.fullmatch(r"[a-z][a-z0-9_]{2,31}",name):
            raise ValueError("Неверный @юзернейм")
    duration=None
    if action in ("mute","ban","warn") and parts:
        m=re.fullmatch(r"([0-9]{1,8})([a-zа-я]+)",parts[0].lower())
        if m and m[2] in UNITS:
            duration=int(m[1])*UNITS[m[2]]
            parts.pop(0)
        elif len(parts)>=2 and parts[0].isdigit() and parts[1].lower() in UNITS:
            duration=int(parts.pop(0))*UNITS[parts.pop(0).lower()]
        elif parts[0].isdigit():
            duration=int(parts.pop(0))*60
        elif parts[0].lower() in ("навсегда","perm","forever"):
            duration=0
            parts.pop(0)
    if duration is None:
        duration=3600 if action=="mute" else 0
    if duration<0 or duration>366*86400:
        raise ValueError("Срок от 1 секунды до 366 дней либо навсегда")
    return action,name,duration," ".join(parts).strip()[:500] or "Не указана"

async def member(conn,cid,uid):
    return await (await conn.execute("""SELECT user_id,role,status,admin_rights,banned_rights,left_at
        FROM channel_members WHERE channel_id=%s AND user_id=%s FOR UPDATE""",(cid,uid))).fetchone()

async def update_member(conn,cid,uid,rights,status,left):
    await conn.execute("""UPDATE channel_members SET banned_rights=%s,status=%s,left_at=%s,updated_at=now()
        WHERE channel_id=%s AND user_id=%s AND role='member'""",(Jsonb(rights),status,left,cid,uid))
    await conn.execute("""UPDATE user_channel_member_index SET status=%s,left_at=%s,updated_at=now()
        WHERE channel_id=%s AND user_id=%s""",(status,left,cid,uid))
    await conn.execute("""UPDATE channels c SET participants_count=s.n,admins_count=s.a,kicked_count=s.k,
        banned_count=s.b,updated_at=now() FROM (
        SELECT count(*) FILTER(WHERE status='active') AS n,
        count(*) FILTER(WHERE status='active' AND role IN ('creator','admin')) AS a,
        count(*) FILTER(WHERE status='kicked') AS k,
        count(*) FILTER(WHERE status='active' AND EXISTS(SELECT 1 FROM jsonb_each(banned_rights) x WHERE x.value='true'::jsonb)) AS b
        FROM channel_members WHERE channel_id=%s) s WHERE c.id=%s""",(cid,cid))

async def restore(conn,cid,uid,r,m):
    if m and m["role"]=="member" and m["banned_rights"]==r["rights"]:
        # A ban never silently re-adds the person: they can rejoin after it ends.
        status="left" if r["kind"]=="ban" else m["status"]
        await update_member(conn,cid,uid,r["previous_rights"],status,0 if status=="left" else m["left_at"])
    else:
        raise ValueError("Ограничение изменено другим администратором")
    await conn.execute("DELETE FROM riseiris_restrictions WHERE channel_id=%s AND user_id=%s",(cid,uid))

async def expire(conn):
    rows=await (await conn.execute("""SELECT channel_id,user_id FROM riseiris_restrictions WHERE expires>0
        AND expires<=extract(epoch from now()) ORDER BY expires LIMIT 50""")).fetchall()
    for item in rows:
        cid,uid=item["channel_id"],item["user_id"]
        await conn.execute("SELECT id FROM channels WHERE id=%s FOR UPDATE",(cid,))
        r=await (await conn.execute("SELECT * FROM riseiris_restrictions WHERE channel_id=%s AND user_id=%s FOR UPDATE",(cid,uid))).fetchone()
        if not r or not 0<r["expires"]<=int(time.time()):
            continue
        m=await member(conn,cid,uid)
        if not m or m["role"]!="member" or m["banned_rights"]!=r["rights"]:
            await conn.execute("DELETE FROM riseiris_restrictions WHERE channel_id=%s AND user_id=%s",(cid,uid))
            continue
        await restore(conn,cid,uid,r,m)

async def auto_check(conn,cid,mid,actor,update):
    key=update["id"]
    old=await (await conn.execute("SELECT 1 AS ok FROM riseiris_receipts WHERE update_id=%s",(key,))).fetchone()
    if old:
        return {"ok":True,"action":"none"}
    # Conservative automation is limited to the official @chat (native ID 3).
    # Never infer insults, intent, scam, personal data or illegal content.
    if cid!=3:
        return {"ok":True,"action":"none"}
    identity=await (await conn.execute("SELECT 1 AS ok FROM peer_usernames WHERE peer_type='channel' AND peer_id=%s AND username_lower='chat' AND active",(cid,))).fetchone()
    bm=await member(conn,cid,BOT)
    target=await member(conn,cid,actor)
    user=await (await conn.execute("SELECT username,first_name,is_bot FROM users WHERE id=%s AND deleted_at IS NULL",(actor,))).fetchone()
    if not identity or not manager(bm) or not target or target["role"]!="member" or target["status"]!="active" or not user or user["is_bot"] or any(target["banned_rights"].get(k) is True for k in SEND_KEYS+["ViewMessages"]):
        return {"ok":True,"action":"none"}
    if await (await conn.execute("SELECT 1 AS ok FROM riseiris_restrictions WHERE channel_id=%s AND user_id=%s",(cid,actor))).fetchone():
        return {"ok":True,"action":"none"}
    # Confirm live, nonempty text spam from the authoritative message store.
    # Non-message restrictions (EditRank etc.) do not prevent a mute.
    if update["created_at"].timestamp()<time.time()-60:
        return {"ok":True,"action":"none"}
    body=" ".join((update["body"] or "").casefold().split())
    if not body or body.startswith(("/","!")):
        return {"ok":True,"action":"none"}
    rows=await (await conn.execute("""SELECT body,message_date FROM channel_messages WHERE channel_id=%s AND sender_user_id=%s
        AND NOT deleted AND id<=%s AND message_date BETWEEN
        (SELECT message_date-15 FROM channel_messages WHERE channel_id=%s AND id=%s)
        AND (SELECT message_date FROM channel_messages WHERE channel_id=%s AND id=%s)
        ORDER BY id DESC LIMIT 32""",(cid,actor,mid,cid,mid,cid,mid))).fetchall()
    if not rows:
        return {"ok":True,"action":"none"}
    latest=rows[0]["message_date"]
    bodies=[(" ".join((r["body"] or "").casefold().split()),r["message_date"]) for r in rows]
    bodies=[(t,d) for t,d in bodies if t and not t.startswith(("/","!"))]
    repeated=sum(t==body and d>=latest-12 for t,d in bodies)>=6
    burst=len(bodies)>=12
    if not repeated and not burst:
        return {"ok":True,"action":"none"}
    reason="1.1 — спам (6 одинаковых сообщений за 12 секунд)" if repeated else "1.1 — флуд (12 сообщений за 15 секунд)"
    duration=600
    now=int(time.time())
    until=now+duration
    rights=dict(target["banned_rights"])
    rights.update({k:True for k in SEND_KEYS})
    rights["ViewMessages"]=False
    rights["UntilDate"]=until
    await conn.execute("""INSERT INTO riseiris_restrictions(channel_id,user_id,kind,rights,previous_rights,previous_status,previous_left,expires)
        VALUES(%s,%s,'mute',%s,%s,%s,%s,%s)""",(cid,actor,Jsonb(rights),Jsonb(target["banned_rights"]),target["status"],target["left_at"],until))
    await update_member(conn,cid,actor,rights,"active",target["left_at"])
    out={"ok":True,"action":"mute","duration":duration,"reason":reason,"target":"@"+user["username"] if user["username"] else user["first_name"] or "Участник"}
    await conn.execute("INSERT INTO riseiris_receipts(update_id,result) VALUES(%s,%s)",(key,Jsonb(out)))
    return out

async def scan_recent_spam(conn):
    # Fallback when polling has a conflict or a worker restarts. Inspect only
    # recent messages actually delivered to RiseIris in the official group.
    # Channel lock serializes this with manual moderation and live auto checks.
    c=await (await conn.execute("SELECT id FROM channels WHERE id=3 AND megagroup AND NOT deleted FOR UPDATE")).fetchone()
    if not c:
        return []
    rows=await (await conn.execute("""SELECT u.id,u.created_at,m.id AS mid,m.sender_user_id AS actor,m.body
        FROM bot_api_updates u JOIN channel_messages m ON m.channel_id=u.peer_id AND m.id=u.message_id
        JOIN channel_members cm ON cm.channel_id=m.channel_id AND cm.user_id=m.sender_user_id
        WHERE u.bot_user_id=%s AND u.update_kind='message' AND u.peer_type='channel' AND u.peer_id=3
          AND u.created_at>now()-interval '30 seconds' AND m.message_date>=extract(epoch from now())::bigint-30
          AND NOT m.deleted AND cm.role='member' AND cm.status='active'
          AND NOT EXISTS(SELECT 1 FROM riseiris_receipts r WHERE r.update_id=u.id)
        ORDER BY u.id DESC LIMIT 48""",(BOT,))).fetchall()
    results=[]
    for row in rows:
        try:
            async with conn.transaction():
                out=await auto_check(conn,3,int(row["mid"]),int(row["actor"]),row)
                if out.get("action")=="mute":
                    results.append(out)
        except Exception:
            LOG.exception("Iris fallback scan failed for update %s",row["id"])
    return results


async def handle(request):
    auth=request.headers.get("Authorization","")
    if not auth.startswith("Bearer ") or len(auth)>240:
        return result({"ok":False,"error":"Unauthorized"},401)
    try:
        data=await request.json()
        async with economy.transaction() as conn:
            await conn.execute("SET LOCAL statement_timeout='12s'")
            bot=await (await conn.execute("SELECT token_secret FROM bots WHERE bot_user_id=%s",(BOT,))).fetchone()
            if not bot or not bot["token_secret"] or not hmac.compare_digest(auth[7:],str(BOT)+":"+bot["token_secret"]):
                return result({"ok":False,"error":"Unauthorized"},401)
            if data.get("action")=="expire":
                await expire(conn)
                return result({"ok":True,"auto_mutes":await scan_recent_spam(conn)})
            chat,mid,actor=data.get("chat_id"),data.get("message_id"),data.get("user_id")
            if type(chat) is not int or chat>=-1000000000000 or type(mid) is not int or not 0<mid<=2147483647 or type(actor) is not int or actor<=0:
                raise ValueError("Команда доступна в супергруппе RiseGram")
            cid=-chat-1000000000000
            c=await (await conn.execute("SELECT id FROM channels WHERE id=%s AND megagroup AND NOT deleted FOR UPDATE",(cid,))).fetchone()
            if not c:
                raise ValueError("Группа не найдена")
            update=await (await conn.execute("""SELECT u.id,u.created_at,m.body,m.reply_to_msg_id FROM bot_api_updates u
                JOIN channel_messages m ON m.channel_id=u.peer_id AND m.id=u.message_id
                WHERE u.bot_user_id=%s AND u.update_kind='message' AND u.peer_type='channel'
                  AND u.peer_id=%s AND u.message_id=%s AND m.sender_user_id=%s AND NOT m.deleted
                ORDER BY u.id LIMIT 1""",(BOT,cid,mid,actor))).fetchone()
            if not update or update["created_at"].timestamp()<time.time()-600:
                raise ValueError("Команда не подтверждена или устарела")
            key=update["id"]
            old=await (await conn.execute("SELECT result FROM riseiris_receipts WHERE update_id=%s",(key,))).fetchone()
            if old:
                return result({"ok":True,"action":"none"} if data.get("action")=="auto" else old["result"])
            if data.get("action")=="auto":
                return result(await auto_check(conn,cid,mid,actor,update))
            action,name,duration,reason=parse(update["body"])
            bot_member=await member(conn,cid,BOT)
            actor_member=await member(conn,cid,actor)
            if not manager(bot_member):
                raise ValueError("Выдай Iris права администратора: блокировка участников")
            if action!="warnings" and not manager(actor_member):
                raise ValueError("Требуются права блокировки участников в этой группе")
            if not actor_member or actor_member["status"]!="active":
                raise ValueError("Ты не участник этой группы")
            if name:
                targets=await (await conn.execute("""SELECT DISTINCT u.id FROM users u JOIN channel_members m ON m.user_id=u.id AND m.channel_id=%s
                    LEFT JOIN peer_usernames p ON p.peer_type='user' AND p.peer_id=u.id AND p.active
                    WHERE u.deleted_at IS NULL AND (lower(u.username)=%s OR p.username_lower=%s)""",(cid,name,name))).fetchall()
                if len(targets)!=1:
                    raise ValueError("Участник с таким @юзернеймом не найден")
                uid=int(targets[0]["id"])
            elif update["reply_to_msg_id"]:
                ref=await (await conn.execute("SELECT sender_user_id FROM channel_messages WHERE channel_id=%s AND id=%s AND NOT deleted",(cid,update["reply_to_msg_id"]))).fetchone()
                if not ref:
                    raise ValueError("Сообщение для реплая не найдено")
                uid=int(ref["sender_user_id"])
            elif action=="warnings":
                uid=actor
            else:
                raise ValueError("Укажи @юзернейм или ответь на сообщение участника")
            target=await member(conn,cid,uid)
            user=await (await conn.execute("SELECT username,first_name,is_bot FROM users WHERE id=%s AND deleted_at IS NULL",(uid,))).fetchone()
            if not target or not user:
                raise ValueError("Участник не найден")
            if action!="warnings" and (target["role"]!="member" or uid==actor or user["is_bot"]):
                raise ValueError("Нельзя наказать администратора, бота или себя")
            label="@"+user["username"] if user["username"] else (user["first_name"] or "Участник")
            if name:
                label="@"+name
            now=int(time.time())
            until=now+duration if duration else 0
            if action in ("warn","unwarn","clearwarn","warnings"):
                if action=="warn":
                    if target["status"]!="active":
                        raise ValueError("Участник не в группе")
                    await conn.execute("INSERT INTO riseiris_warnings(update_id,channel_id,user_id,actor_id,reason,expires) VALUES(%s,%s,%s,%s,%s,%s)",(key,cid,uid,actor,reason,until))
                elif action=="unwarn":
                    await conn.execute("""UPDATE riseiris_warnings SET revoked=true WHERE update_id=(
                      SELECT update_id FROM riseiris_warnings WHERE channel_id=%s AND user_id=%s AND NOT revoked
                      AND (expires=0 OR expires>%s) ORDER BY update_id DESC LIMIT 1)""",(cid,uid,now))
                elif action=="clearwarn":
                    await conn.execute("UPDATE riseiris_warnings SET revoked=true WHERE channel_id=%s AND user_id=%s",(cid,uid))
                count=await (await conn.execute("SELECT count(*) AS n FROM riseiris_warnings WHERE channel_id=%s AND user_id=%s AND NOT revoked AND (expires=0 OR expires>%s)",(cid,uid,now))).fetchone()
                out={"ok":True,"action":action,"target":label,"count":int(count["n"]),"duration":duration,"reason":reason}
            else:
                r=await (await conn.execute("SELECT * FROM riseiris_restrictions WHERE channel_id=%s AND user_id=%s FOR UPDATE",(cid,uid))).fetchone()
                if action in ("unmute","unban"):
                    if not r or r["kind"]!=action[2:]:
                        raise ValueError("Нет такого ограничения, выданного Iris")
                    await restore(conn,cid,uid,r,target)
                else:
                    if r:
                        if r["rights"]!=target["banned_rights"]:
                            raise ValueError("Ограничение изменено другим администратором")
                        raise ValueError("Ограничение уже действует. Сначала сними его")
                    if target["status"]!="active" or any(v is True for v in target["banned_rights"].values()):
                        raise ValueError("Участник уже ограничен или не в группе")
                    rights=dict(target["banned_rights"])
                    rights.update({k:True for k in SEND_KEYS})
                    rights["ViewMessages"]=action=="ban"
                    rights["UntilDate"]=until
                    await conn.execute("""INSERT INTO riseiris_restrictions(channel_id,user_id,kind,rights,previous_rights,previous_status,previous_left,expires)
                      VALUES(%s,%s,%s,%s,%s,%s,%s,%s)""",(cid,uid,action,Jsonb(rights),Jsonb(target["banned_rights"]),target["status"],target["left_at"],until))
                    await update_member(conn,cid,uid,rights,"kicked" if action=="ban" else "active",now if action=="ban" else target["left_at"])
                out={"ok":True,"action":action,"target":label,"duration":duration,"reason":reason}
            await conn.execute("INSERT INTO riseiris_receipts(update_id,result) VALUES(%s,%s)",(key,Jsonb(out)))
        return result(out)
    except ValueError as exc:
        return result({"ok":False,"error":str(exc)},409)
    except Exception:
        LOG.exception("Iris moderation command failed")
        return result({"ok":False,"error":"Сервер не подтвердил операцию"},503)
