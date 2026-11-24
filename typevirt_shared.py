"""Authoritative native RiseStars for TypeVirt instant games.
The wager is parsed from the actual native bot update, never from the bot's
claimed amount. One message can settle at most once; ledger and receipt are atomic.
"""
import hmac
import logging
import re
import secrets
import time
from decimal import Decimal, InvalidOperation
from aiohttp import web
from psycopg.types.json import Jsonb
import native_economy as economy

BOT_ID=1780243214
MAX=2**63-1
RNG=secrets.SystemRandom()
log=logging.getLogger("risegram.typevirt_shared")

async def setup():
    async with economy.transaction() as conn:
        await conn.execute("""CREATE TABLE IF NOT EXISTS risegram_typevirt_shared_receipts(
            update_id bigint PRIMARY KEY,user_id bigint NOT NULL,result jsonb NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now())""")
        await conn.execute("""CREATE TABLE IF NOT EXISTS risegram_typevirt_hilo(
            token text PRIMARY KEY,user_id bigint NOT NULL,chat_id bigint NOT NULL,
            stake bigint NOT NULL CHECK(stake>0),card integer NOT NULL,mult integer NOT NULL,
            expires_at timestamptz NOT NULL,result jsonb)""")

def response(body,status=200):
    return web.json_response(body,status=status,headers={"Cache-Control":"no-store"})

def stake(raw,balance):
    s=raw.lower().replace("ё","е")
    if s in ("все","all"):
        value=balance
    else:
        m=re.fullmatch(r"([0-9]{1,19}(?:\.[0-9]{1,8})?)(кккк|ккк|кк|к|kkkk|kkk|kk|k|млрд|млн|м)?",s)
        if not m:
            raise ValueError("Укажи целую сумму или сокращение: 100, 10к, все")
        mul={None:1,"к":1000,"k":1000,"кк":10**6,"kk":10**6,"м":10**6,"млн":10**6,
             "ккк":10**9,"kkk":10**9,"млрд":10**9,"кккк":10**12,"kkkk":10**12}[m[2]]
        d=Decimal(m[1])*mul
        if d!=d.to_integral_value():
            raise ValueError("Ставка должна быть целым числом")
        value=int(d)
    if not 1<=value<=balance or value>MAX:
        raise ValueError("Недостаточно RiseStars или неверная ставка")
    return value

def evaluate(text,balance):
    parts=text.lower().split()
    if not parts:
        raise ValueError("Нет команды")
    cmd=parts[0].split("@")[0].lstrip("/")
    if cmd in ("ор","кубик","спин","слот","слоты","slot","slots","р","рыбалка") and len(parts)==2:
        bet=stake(parts[1],balance)
        if cmd=="ор":
            won=RNG.randrange(2)==0
            return bet,bet*2 if won else 0,"Орёл или решка", "Победа" if won else "Проигрыш"
        if cmd=="кубик":
            a,b=RNG.randint(1,6),RNG.randint(1,6)
            return bet,bet*(2 if a>b else 1 if a==b else 0),"Кубики",f"Ты: {a} · Бот: {b}"
        if cmd=="спин":
            n=RNG.choices([0,50,150,200,300,500,1000],weights=[30,25,20,12,8,4,1])[0]
            return bet,bet*n//100,"Колесо фортуны",f"×{n/100:g}"
        if cmd in ("р","рыбалка"):
            # Use the published 0–5x fishing range; no real-money conversion.
            n=RNG.choice([0,0,1,2,5])
            return bet,bet*n,"Рыбалка",f"Улов ×{n}"
        reels=RNG.choices(["🍒","🍋","🍊","🍇","💎","7️⃣"],weights=[30,25,20,12,8,5],k=3)
        mult={"🍒":200,"🍋":250,"🍊":300,"🍇":400,"💎":600,"7️⃣":1000}
        if len(set(reels))==1:
            payout=bet*mult[reels[0]]//100
        elif any(reels.count(x)==2 for x in ("🍒","🍋")):
            payout=bet*130//100
        else:
            payout=0
        return bet,payout,"Слоты"," | ".join(reels)
    if cmd=="рулетка" and len(parts)==3 and parts[1]!="руж":
        bet=stake(parts[1],balance)
        target=parts[2]
        n=RNG.randint(0,30)
        if target=="1-15":
            pay=bet*145//100 if 1<=n<=15 else 0
        elif target=="15-30":
            pay=bet*145//100 if 16<=n<=30 else 0
        elif re.fullmatch(r"[0-9]{1,2}",target) and int(target)<=30:
            pay=bet*10 if int(target)==n else 0
        else:
            raise ValueError("Рулетка: ставка и число 0–30 либо 1-15 / 15-30")
        return bet,pay,"Рулетка",f"Выпало {n}, ставка на {target}"
    if cmd=="угадай" and len(parts)==3 and re.fullmatch(r"[0-9]{1,2}",parts[1]) and 1<=int(parts[1])<=10:
        bet=stake(parts[2],balance)
        n=RNG.randint(1,10)
        diff=abs(n-int(parts[1]))
        # Original game credited a net 8x on exact hit, half on near miss.
        pay=9*bet if diff==0 else bet+bet//2 if diff==1 else 0
        return bet,pay,"Угадай число",f"Выпало {n}, ты выбрал {parts[1]}"
    if cmd=="кости" and len(parts)==3 and re.fullmatch(r"[0-9]{1,2}",parts[2]) and 2<=int(parts[2])<=12:
        bet=stake(parts[1],balance)
        a,b=RNG.randint(1,6),RNG.randint(1,6)
        d=abs(a+b-int(parts[2]))
        return bet,bet*5 if d==0 else bet*3//2 if d==1 else 0,"Кости",f"{a} + {b} = {a+b}"
    if cmd=="скачки" and len(parts)==3 and parts[2] in ("1","2","3","4","5"):
        bet=stake(parts[1],balance)
        scores=[sum(RNG.randint(0,2) for _ in range(10)) for _ in range(5)]
        winner=scores.index(max(scores))+1
        pay=bet*RNG.choice([200,250,300,500])//100 if winner==int(parts[2]) else 0
        return bet,pay,"Скачки",f"Победила лошадь №{winner}"
    raise ValueError("Проверь команду и сумму ставки")

async def handle(request):
    auth=request.headers.get("Authorization","")
    if not auth.startswith("Bearer ") or len(auth)>240:
        return response({"ok":False,"error":"Unauthorized"},401)
    try:
        body=await request.json()
        uid=body.get("user_id")
        action=body.get("action")
        chat=body.get("chat_id")
        mid=body.get("message_id")
        if type(uid) is not int or not 0<uid<=MAX or action not in ("balance","play","recover","hilo_start","hilo_finish"):
            raise ValueError()
        if action!="balance" and (type(chat) is not int or type(mid) is not int or not 0<mid<=2147483647):
            raise ValueError()
        if action=="hilo_finish":
            callback_id=body.get("callback_id","")
            choice=body.get("choice","")
            token=body.get("token","")
            if not isinstance(callback_id,str) or not re.fullmatch(r"-?[0-9]{1,19}",callback_id) or not -2**63<=int(callback_id)<=MAX or choice not in ("high","low") or not isinstance(token,str) or not re.fullmatch(r"[0-9a-f]{24}",token):
                raise ValueError()
    except (ValueError,TypeError,AttributeError):
        return response({"ok":False,"error":"Некорректный запрос"},400)
    try:
        async with economy.transaction() as conn:
            await conn.execute("SET LOCAL statement_timeout='8s'")
            bot=await (await conn.execute("""SELECT b.token_secret FROM bots b JOIN users u ON u.id=b.bot_user_id
                WHERE b.bot_user_id=%s AND u.is_bot AND u.deleted_at IS NULL""",(BOT_ID,))).fetchone()
            if not bot or not bot["token_secret"] or not hmac.compare_digest(auth[7:],str(BOT_ID)+":"+bot["token_secret"]):
                return response({"ok":False,"error":"Unauthorized"},401)
            exists=await (await conn.execute("SELECT id FROM users WHERE id=%s AND NOT is_bot AND deleted_at IS NULL",(uid,))).fetchone()
            if not exists:
                return response({"ok":False,"error":"Аккаунт RiseGram не найден"},404)
            if action=="balance":
                row=await (await conn.execute("SELECT balance FROM stars_balances WHERE user_id=%s",(uid,))).fetchone()
                legacy=await (await conn.execute("SELECT balance FROM risegram_typevirt_wallet WHERE user_id=%s",(uid,))).fetchone()
                return response({"ok":True,"balance":str(row["balance"] if row else 0),"legacy_reserve":str(legacy["balance"] if legacy else 0)})
            if action=="hilo_finish":
                peer_type="user" if chat==uid else "channel"
                peer_id=uid if chat==uid else -chat-1000000000000
                if peer_id<=0:
                    raise ValueError("Неверный чат")
                proof=await (await conn.execute("""SELECT 1 FROM bot_api_updates WHERE bot_user_id=%s
                    AND update_kind='callback_query' AND callback_query_id=%s AND callback_user_id=%s
                    AND peer_type=%s AND peer_id=%s AND message_id=%s AND callback_data=%s
                    AND created_at>now()-interval '10 minutes'""",
                    (BOT_ID,int(callback_id),uid,peer_type,peer_id,mid,f"tvhl:{token}:{choice}".encode()))).fetchone()
                if not proof:
                    raise ValueError("Выбор не подтверждён сервером")
                await conn.execute("SELECT pg_advisory_xact_lock(%s)",(uid,))
                game=await (await conn.execute("SELECT *,expires_at>now() AS active FROM risegram_typevirt_hilo WHERE token=%s AND user_id=%s AND chat_id=%s FOR UPDATE",(token,uid,chat))).fetchone()
                if not game:
                    raise ValueError("Это не твоя игра")
                if game["result"]:
                    return response(game["result"])
                if not game["active"]:
                    raise ValueError("Игра истекла, звёзды не списаны")
                row=await (await conn.execute("SELECT balance FROM stars_balances WHERE user_id=%s FOR UPDATE",(uid,))).fetchone()
                balance=int(row["balance"]) if row else 0
                bet=int(game["stake"])
                if balance<bet:
                    raise ValueError("Недостаточно RiseStars. Ставка не списана")
                card=game["card"]
                new=RNG.choice([n for n in range(1,21) if n!=card])
                won=(choice=="high" and new>card) or (choice=="low" and new<card)
                payout=bet*game["mult"]//100 if won else 0
                if payout>MAX or balance-bet+payout>MAX:
                    raise ValueError("Технический предел баланса")
                await economy.post(conn,"typevirt-hilo:"+token+":stake",[(uid,"stars",-bet)],"TypeVirt: Hi-Lo · ставка")
                if payout:
                    await economy.post(conn,"typevirt-hilo:"+token+":payout",[(uid,"stars",payout)],"TypeVirt: Hi-Lo · выплата")
                out={"ok":True,"balance":str(balance-bet+payout),"stake":str(bet),"payout":str(payout),"net":str(payout-bet),"old":card,"new":new,"won":won}
                await conn.execute("UPDATE risegram_typevirt_hilo SET result=%s WHERE token=%s",(Jsonb(out),token))
                return response(out)
            if chat==uid:
                updates=await (await conn.execute("""SELECT u.id,u.created_at,p.body FROM bot_api_updates u
                    JOIN private_messages p ON p.sender_user_id=%s AND p.recipient_user_id=u.bot_user_id
                    AND p.recipient_box_id=u.message_id
                    WHERE u.bot_user_id=%s AND u.update_kind='message' AND u.peer_type='user'
                    AND u.peer_id=%s AND u.message_id=%s ORDER BY u.id LIMIT 1""",(uid,BOT_ID,uid,mid))).fetchone()
            elif chat < -1000000000000 and action in ("play","hilo_start"):
                cid=-chat-1000000000000
                updates=await (await conn.execute("""SELECT u.id,u.created_at,m.body FROM bot_api_updates u
                    JOIN channel_messages m ON m.channel_id=u.peer_id AND m.id=u.message_id
                    WHERE u.bot_user_id=%s AND u.update_kind='message' AND u.peer_type='channel'
                    AND u.peer_id=%s AND u.message_id=%s AND m.sender_user_id=%s AND NOT m.deleted
                    ORDER BY u.id LIMIT 1""",(BOT_ID,cid,mid,uid))).fetchone()
            else:
                raise ValueError("Операция доступна только в личном чате")
            if not updates:
                raise ValueError("Сообщение не подтверждено сервером RiseGram")
            update_id=int(updates["id"])
            await conn.execute("SELECT pg_advisory_xact_lock(%s)",(uid,))
            old=await (await conn.execute("SELECT user_id,result FROM risegram_typevirt_shared_receipts WHERE update_id=%s",(update_id,))).fetchone()
            if old:
                if old["user_id"]!=uid:
                    raise ValueError("Несовпадение владельца операции")
                return response(old["result"])
            if updates["created_at"].timestamp()<time.time()-600:
                raise ValueError("Команда устарела. Отправь новую")
            row=await (await conn.execute("SELECT balance FROM stars_balances WHERE user_id=%s FOR UPDATE",(uid,))).fetchone()
            balance=int(row["balance"]) if row else 0
            key="typevirt-shared:"+str(update_id)
            if action=="hilo_start":
                parts=updates["body"].lower().split()
                if len(parts)!=2 or parts[0] not in ("хл","hilo","hi-lo"):
                    raise ValueError("Пример: хл 100 или хл все")
                bet=stake(parts[1],balance)
                card=RNG.randint(2,19)
                roll=RNG.randrange(100)
                mult=200 if roll<50 else RNG.randint(110,200) if roll<80 else RNG.randint(210,300) if roll<95 else RNG.randint(310,500)
                token=secrets.token_hex(12)
                await conn.execute("""INSERT INTO risegram_typevirt_hilo(token,user_id,chat_id,stake,card,mult,expires_at)
                    VALUES(%s,%s,%s,%s,%s,%s,now()+interval '10 minutes')""",(token,uid,chat,bet,card,mult))
                result={"ok":True,"token":token,"stake":str(bet),"card":card,"mult":mult,"balance":str(balance)}
            elif action=="recover":
                if not re.fullmatch(r"/recoverstars(?:@typevirtbot)?",updates["body"].strip(),re.I):
                    raise ValueError("Отправь /recoverstars в личном чате")
                legacy=await (await conn.execute("SELECT balance FROM risegram_typevirt_wallet WHERE user_id=%s FOR UPDATE",(uid,))).fetchone()
                amount=int(legacy["balance"]) if legacy else 0
                if not amount:
                    raise ValueError("Старый кошелёк пуст")
                if balance+amount>MAX:
                    raise ValueError("Технический предел баланса")
                await conn.execute("INSERT INTO stars_balances(user_id,balance,granted) VALUES(%s,0,true) ON CONFLICT DO NOTHING",(uid,))
                await economy.post(conn,key,[(uid,"stars",amount)],"TypeVirt: возврат старого депозита")
                await conn.execute("UPDATE risegram_typevirt_wallet SET balance=0,updated_at=now() WHERE user_id=%s",(uid,))
                result={"ok":True,"balance":str(balance+amount),"returned":str(amount)}
            else:
                bet,payout,title,detail=evaluate(updates["body"],balance)
                if payout>MAX or balance-bet+payout>MAX:
                    raise ValueError("Выигрыш превышает технический предел баланса. Уменьши ставку")
                await economy.post(conn,key+":stake",[(uid,"stars",-bet)],"TypeVirt: "+title+" · ставка")
                if payout:
                    await economy.post(conn,key+":payout",[(uid,"stars",payout)],"TypeVirt: "+title+" · выплата")
                result={"ok":True,"balance":str(balance-bet+payout),"stake":str(bet),"payout":str(payout),
                    "net":str(payout-bet),"game":title,"detail":detail}
            await conn.execute("INSERT INTO risegram_typevirt_shared_receipts(update_id,user_id,result) VALUES(%s,%s,%s)",(update_id,uid,Jsonb(result)))
        return response(result)
    except (ValueError,InvalidOperation,economy.WalletError) as exc:
        return response({"ok":False,"error":str(exc)},409)
    except Exception:
        log.exception("Native TypeVirt shared operation failed")
        return response({"ok":False,"error":"Операция не подтверждена. Проверь баланс"},503)
