"""Channel-owner removal of administrative paid-reaction grants only.

Never erase genuine paid reactions or create refunds from unverified history.
Ownership is revalidated for each action, and the grant receipt bounds removals.
"""
from __future__ import annotations
import logging
import re
import secrets
import time

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery
from psycopg.types.json import Jsonb

import config
import native_economy as economy
from support_console import native_for_local
from ui import btn, kb, esc

log=logging.getLogger("risegram.channel_stars")
router=Router(name="channel_stars_owner")
router.message.filter(F.chat.type=="private")
router.callback_query.filter(F.message.chat.type=="private")
PENDING={}
LINK=re.compile(r"(?:(?:https?://)?(?:t\.me|risegram\.me)/|@)([A-Za-z][A-Za-z0-9_]{3,31})/([1-9][0-9]{0,9})",re.I)


async def identity(event,user):
    if event.from_user.id==config.OWNER_TELEGRAM_ID:
        # The project's actual native owner is distinct from the support peer.
        async with economy.transaction() as conn:
            row=await (await conn.execute("""SELECT u.id FROM users u JOIN peer_usernames p ON p.peer_id=u.id
              AND p.peer_type='user' WHERE u.id=1780243200 AND p.username_lower='owner' AND p.active
              AND u.deleted_at IS NULL AND NOT u.is_bot AND NOT u.support""")).fetchone()
            return int(row["id"]) if row else None
    return await native_for_local(int(user["tg_id"]))


async def grants(conn,cid,mid):
    rows=await (await conn.execute("""SELECT key,movements FROM risegram_bot_receipts
        WHERE jsonb_typeof(movements)='object'
          AND movements->>'kind'='administrative_virtual_star_grant'
          AND movements->>'channel_id'=%s AND movements->>'message_id'=%s
        ORDER BY key""",(str(cid),str(mid)))).fetchall()
    out=[]
    for row in rows:
        m=row["movements"]
        if type(m.get("amount")) is not int or type(m.get("actor_user_id")) is not int:
            raise ValueError("Некорректная запись начисления")
        used=await (await conn.execute("""SELECT coalesce(sum((movements->>'amount')::bigint),0) AS n
            FROM risegram_bot_receipts WHERE movements->>'kind'='admin_paid_reaction_removal'
              AND movements->>'grant_key'=%s""",(row["key"],))).fetchone()
        left=m["amount"]-int(used["n"])
        if left>0:
            out.append((row["key"],m["actor_user_id"],left))
    return out


async def find(conn,name,mid,uid):
    row=await (await conn.execute("""SELECT DISTINCT c.id,c.title FROM channels c
      LEFT JOIN peer_usernames p ON p.peer_type='channel' AND p.peer_id=c.id AND p.active
      JOIN channel_messages m ON m.channel_id=c.id AND m.id=%s
      WHERE NOT c.deleted AND c.broadcast AND NOT c.megagroup AND NOT m.deleted AND m.post
        AND c.creator_user_id=%s AND (p.username_lower=%s OR lower(c.username)=%s)""",(mid,uid,name,name))).fetchall()
    if len(row)!=1:
        raise ValueError("Пост не найден или ты не владелец этого канала")
    return row[0]


@router.message(Command("poststars","removepoststars"))
async def preview(message:Message,user:dict,*,menu_args=None):
    args=menu_args if menu_args is not None else (message.text or "").split()
    if len(args) not in (2,3):
        return await message.answer("⭐ Управление звёздами поста\n/poststars t.me/канал/2\n/removepoststars t.me/канал/2 1000\n\nУдаляются только административные начисления. Чужие платные реакции остаются.")
    match=LINK.fullmatch(args[1])
    if not match or int(match[2])>2147483647:
        return await message.answer("Укажи ссылку на пост RiseGram, например t.me/rise_gram/2")
    if len(args)==3 and (not args[2].isascii() or not args[2].isdigit() or not 1<=len(args[2])<=10 or not 0<int(args[2])<=2147483647):
        return await message.answer("Укажи положительное целое количество звёзд")
    try:
        uid=await identity(message,user)
        if not uid:
            return await message.answer("Не удалось подтвердить связь с аккаунтом RiseGram. Войди в клиент и открой бота заново")
        name,mid=match[1].lower(),int(match[2])
        async with economy.transaction() as conn:
            c=await find(conn,name,mid,uid)
            cid=int(c["id"])
            available=sum(g[2] for g in await grants(conn,cid,mid))
            total=await (await conn.execute("SELECT coalesce(sum(stars),0) AS n FROM channel_message_paid_reactions WHERE channel_id=%s AND message_id=%s",(cid,mid))).fetchone()
            balance=await (await conn.execute("SELECT balance FROM channel_stars_balances WHERE channel_id=%s",(cid,))).fetchone()
        limit=min(available,int(balance["balance"]) if balance else 0)
        amount=int(args[2]) if len(args)==3 else limit
        text=f"⭐ <b>@{esc(name)}/{mid}</b>\nВсего: {int(total['n'])} ⭐\nАдминистративные: {available} ⭐\nДоступно к снятию: {limit} ⭐"
        if not limit or not 0<amount<=limit:
            return await message.answer(text+"\n\nНельзя списать больше доступного. Платежи пользователей защищены.")
        now=time.monotonic()
        for key,v in list(PENDING.items()):
            if v[0]<now:
                PENDING.pop(key,None)
        token=secrets.token_hex(12)
        PENDING[token]=(now+180,message.from_user.id,uid,cid,mid,amount)
        await message.answer(text+f"\n\nСнять {amount} ⭐ с поста и баланса канала? Чужие реакции сохранятся.",
            reply_markup=kb([btn("✅ Снять звёзды","poststars:yes:"+token)],[btn("Отмена","poststars:no:"+token)]))
    except ValueError as exc:
        await message.answer(esc(str(exc)))
    except Exception:
        log.exception("Post stars preview failed")
        await message.answer("Не удалось проверить пост")


@router.callback_query(F.data.startswith("poststars:"))
async def apply(call:CallbackQuery,user:dict):
    parts=(call.data or "").split(":")
    if len(parts)!=3:
        return await call.answer("Неверное действие",show_alert=True)
    op,token=parts[1:]
    item=PENDING.get(token)
    if not item or item[0]<time.monotonic() or item[1]!=call.from_user.id:
        return await call.answer("Подтверждение истекло",show_alert=True)
    PENDING.pop(token,None)
    if op!="yes":
        return await call.message.edit_text("Отменено")
    _,_,uid,cid,mid,amount=item
    await call.answer("Проверяю")
    try:
        if await identity(call,user)!=uid:
            raise ValueError("Связь с аккаунтом изменилась")
        async with economy.transaction() as conn:
            await conn.execute("SET LOCAL lock_timeout='5s'")
            c=await (await conn.execute("SELECT id FROM channels WHERE id=%s AND creator_user_id=%s AND broadcast AND NOT deleted FOR UPDATE",(cid,uid))).fetchone()
            m=await (await conn.execute("SELECT id FROM channel_messages WHERE channel_id=%s AND id=%s AND NOT deleted AND post FOR UPDATE",(cid,mid))).fetchone()
            if not c or not m:
                raise ValueError("Пост или право владения изменились")
            rows=await grants(conn,cid,mid)
            if sum(g[2] for g in rows)<amount:
                raise ValueError("Начисление уже изменилось")
            left=amount
            for i,(grant,actor,available) in enumerate(rows):
                if not left:
                    break
                take=min(left,available)
                r=await (await conn.execute("SELECT stars FROM channel_message_paid_reactions WHERE channel_id=%s AND message_id=%s AND reactor_user_id=%s FOR UPDATE",(cid,mid,actor))).fetchone()
                # A grant must still be identifiable in its original row.
                if not r or int(r["stars"])<available:
                    raise ValueError("История начисления не совпадает с реакциями")
                if int(r["stars"])==take:
                    await conn.execute("DELETE FROM channel_message_paid_reactions WHERE channel_id=%s AND message_id=%s AND reactor_user_id=%s",(cid,mid,actor))
                else:
                    await conn.execute("UPDATE channel_message_paid_reactions SET stars=stars-%s WHERE channel_id=%s AND message_id=%s AND reactor_user_id=%s",(take,cid,mid,actor))
                await conn.execute("INSERT INTO risegram_bot_receipts(key,movements) VALUES(%s,%s)",("poststars-remove-"+token+"-"+str(i),Jsonb({"kind":"admin_paid_reaction_removal","grant_key":grant,"amount":take,"channel_id":cid,"message_id":mid,"owner_id":uid})))
                left-=take
            debit=await (await conn.execute("UPDATE channel_stars_balances SET balance=balance-%s,updated_at=now() WHERE channel_id=%s AND balance>=%s RETURNING balance",(amount,cid,amount))).fetchone()
            if not debit:
                raise ValueError("На балансе канала недостаточно звёзд. Ничего не списано")
            await conn.execute("""INSERT INTO channel_stars_transactions(channel_id,actor_user_id,amount,reason,peer_type,peer_id,date)
              VALUES(%s,%s,%s,'adjust','user',%s,%s)""",(cid,uid,-amount,uid,int(time.time())))
            total=await (await conn.execute("SELECT coalesce(sum(stars),0) AS n FROM channel_message_paid_reactions WHERE channel_id=%s AND message_id=%s",(cid,mid))).fetchone()
        await call.message.edit_text(f"✅ Снято {amount} ⭐\nНа посте осталось {int(total['n'])} ⭐\nПлатные реакции пользователей сохранены")
    except ValueError as exc:
        await call.message.edit_text("❌ "+esc(str(exc)))
    except Exception:
        log.exception("Post star removal failed")
        await call.message.edit_text("Операция не подтверждена. Проверь /poststars перед повтором")
