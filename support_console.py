"""Private RiseGram support console. No access to passwords, OTPs or auth keys.
Native writes use the existing authenticated MTProto admin API. Staff are
explicitly delegated by the owner, never inferred from usernames/tester roles.
"""
from __future__ import annotations

import asyncio
import logging
import re
import secrets
import time

import psycopg
from psycopg.rows import dict_row
from aiogram import F, Router
from aiogram.filters import Command, Filter, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery

import account_recovery as recovery
import config
import database as db
import freeze_appeals
from ui import btn, esc, kb, safe_send, show

log = logging.getLogger("risegram.support")
support_router = Router(name="support_console")
P = "adm:sup:"
SUPPORT_NATIVE_ID = 1780243212
_pending = {}
_ticket_lock = asyncio.Lock()


class StaffOnly(Filter):
    async def __call__(self, event, user: dict = None):
        if not event.from_user:
            return False
        if event.from_user.id == config.OWNER_TELEGRAM_ID:
            return True
        uid = int((user or {}).get("tg_id") or 0)
        if not uid:
            return False
        staff = (await db.get_meta("support_staff", "") or "").split(",")
        return str(uid) in staff


support_router.message.filter(F.chat.type == "private", StaffOnly())
support_router.callback_query.filter(F.message.chat.type == "private", StaffOnly())


class Work(StatesGroup):
    text = State()


def owner(event):
    return event.from_user.id == config.OWNER_TELEGRAM_ID


def back():
    return [btn("🛟 Панель поддержки", P+"home")]


def button(label, key, ident=None):
    return btn(label, P+key+(":"+str(ident) if ident is not None else ""))


def render(rows):
    if not rows:
        return "Нет записей"
    lines = []
    for row in rows:
        lines.append(" · ".join(esc(str(v if v is not None else "—")[:140]) for v in row.values()))
    return "\n".join(lines)[:3300]


async def sql(query, args=()):
    async with await psycopg.AsyncConnection.connect(
        recovery.ledger_dsn(), connect_timeout=6, row_factory=dict_row
    ) as conn:
        await conn.execute("SET TRANSACTION READ ONLY")
        await conn.execute("SET LOCAL statement_timeout='5s'")
        return await (await conn.execute(query, args)).fetchall()


async def setup():
    await db.execute("""CREATE TABLE IF NOT EXISTS support_console_meta (
      ticket_id INTEGER PRIMARY KEY, priority INTEGER NOT NULL DEFAULT 0,
      note TEXT NOT NULL DEFAULT '', assigned_to INTEGER NOT NULL DEFAULT 0)""")


async def log_action(event, text):
    await db.log_admin_action(config.OWNER_ID, "SUPPORT "+str(event.from_user.id)+" "+text)


async def home(event):
    await setup()
    count = await db.scalar("SELECT COUNT(*) FROM support_tickets WHERE status='open'")
    rows = [
        [button("📥 Новые ("+str(count)+")", "open"), button("✅ Архив", "archive")],
        [button("🚨 Приоритетные", "prioritylist"), button("👤 Мои тикеты", "mine")],
        [button("🔎 Найти тикет", "findticket"), button("🔍 Найти аккаунт", "finduser")],
        [button("❄️ Старые апелляции", "appeals"), button("📊 Статистика", "stats")],
        [button("📜 Журнал поддержки", "logs")],
    ]
    if owner(event):
        rows += [[button("👥 Сотрудники", "staff"), btn("👑 Админка", "adm:home")]]
    rows += [[btn("🏠 Главное меню", "menu:main")]]
    await show(event, "🛟 <b>Поддержка RiseGram</b>\n\nТикеты, безопасность, диагностика аккаунтов и NFT\nДействия над аккаунтом требуют подтверждения", kb(*rows))


@support_router.message(Command("supportpanel"))
async def cmd_panel(message: Message, state: FSMContext):
    await state.clear()
    await home(message)


@support_router.message(Command("supportstaff"))
async def cmd_staff(message: Message):
    if not owner(message):
        return await message.answer("Только владелец")
    parts = (message.text or "").split()
    if len(parts) != 3 or parts[1] not in ("add", "remove") or not parts[2].isdigit():
        return await message.answer("Формат: /supportstaff add|remove ID аккаунта из бота")
    uid = int(parts[2])
    if not await db.get_user(uid) or uid == int(config.OWNER_ID):
        return await message.answer("Укажи существующий ID сотрудника, не владельца")
    staff = set(filter(None, (await db.get_meta("support_staff", "") or "").split(",")))
    if parts[1] == "add":
        staff.add(str(uid))
    else:
        staff.discard(str(uid))
    await db.set_meta("support_staff", ",".join(sorted(staff)))
    await log_action(message, "staff "+parts[1]+" "+str(uid))
    await message.answer("✅ Права обновлены. Вход сотрудника: /supportpanel")


async def local_for_native(nid):
    link = await db.fetchone("SELECT account_id,phone_digits FROM verified_peer_links WHERE peer_id=?", (nid,))
    if not link:
        return None
    local = await db.get_user(int(link["account_id"]))
    if not local:
        return None
    native = await recovery.native_info(str(link["phone_digits"]))
    if not native or int(native["id"]) != nid or db.digits_of(str(local.get("phone_digits") or "")) != str(link["phone_digits"]):
        return None
    return local


async def native_for_local(uid):
    link = await db.fetchone("SELECT peer_id FROM verified_peer_links WHERE account_id=?", (uid,))
    if not link:
        return None
    nid = int(link["peer_id"])
    local = await local_for_native(nid)
    return nid if local and int(local["tg_id"]) == uid else None


async def resolve(query):
    query = query.strip()
    if query.startswith("local:") and query[6:].isdigit():
        return await native_for_local(int(query[6:]))
    if query.isdigit() and len(query) <= 18:
        rows = await sql("SELECT id FROM users WHERE id=%s AND deleted_at IS NULL", (int(query),))
    elif re.fullmatch(r"@?[A-Za-z][A-Za-z0-9_]{3,31}", query):
        name = query.lstrip("@").lower()
        rows = await sql("""SELECT DISTINCT u.id FROM users u LEFT JOIN peer_usernames p
          ON p.peer_id=u.id AND p.peer_type='user' AND p.active
          WHERE u.deleted_at IS NULL AND (lower(u.username)=%s OR p.username_lower=%s)""", (name,name))
    else:
        return None
    return int(rows[0]["id"]) if len(rows)==1 else None


async def profile(nid):
    rows = await sql("""SELECT id,first_name,last_name,username,verified,support,scam,fake,
      is_bot,created_at,premium_expires_at,deleted_at FROM users WHERE id=%s""", (nid,))
    return rows[0] if rows else None


async def account(event,nid):
    u = await profile(nid)
    if not u or u["deleted_at"]:
        return await show(event,"Аккаунт не найден",kb(back()))
    name = (u["first_name"]+" "+u["last_name"]).strip()
    rows = [
        [button("👤 Профиль","profile",nid),button("🔐 Безопасность","security",nid)],
        [button("💎 NFT и валюта","assets",nid),button("⚖️ Модерация","moderation",nid)],
        [button("📨 Тикеты аккаунта","usertickets",nid)],back()
    ]
    await show(event, "👤 <b>"+esc(name)+"</b>\nNative ID: <code>"+str(nid)+"</code>\n"+("@" +esc(u["username"]) if u["username"] else "NFT / без обычного username"), kb(*rows))


GROUPS = {
    "security": [("📱 Активные устройства","devices"),("🚪 Завершить все сессии","revoke"),("🔑 Статус 2FA","twofa"),("🔢 Активные коды бота","codes"),("♻️ Аннулировать коды бота","invalidate"),("🗑 Ожидаемое удаление","deletion")],
    "assets": [("💰 Балансы","balances"),("⭐ История Stars","stars"),("💎 Операции Gram","gram"),("🏷 Юзернеймы","names"),("🖼 NFT-юзернеймы","nfts"),("🎁 NFT-подарки","gifts"),("🎀 Обычные подарки","ordinary"),("🔄 История NFT-юзов","nfthistory"),("🛒 Продажи подарков","sales"),("🤝 Предложения подарков","offers")],
    "moderation": [("❄️ Статус ограничений","flags"),("❄️ Заморозить на 24 часа","freeze"),("☀️ Разморозить","unfreeze"),("☑️ Статус верификации","verification"),("💎 Статус Premium","premium"),("📜 Журнал действий","audit"),("📨 Апелляции аккаунта","userappeals")],
}
QUERIES = {
    "profile": ("Профиль", "SELECT id,first_name,last_name,username,created_at,last_seen_at FROM users WHERE id=%s"),
    "devices": ("Устройства (без IP и ключей)", "SELECT device_model,platform,system_version,app_version,created_at,active_at FROM authorizations WHERE user_id=%s ORDER BY active_at DESC LIMIT 12"),
    "twofa": ("Двухфакторная защита", "SELECT has_password,has_recovery,password_changed_at FROM account_passwords WHERE user_id=%s"),
    "deletion": ("Запросы удаления", "SELECT id,state,requested_at,execute_at FROM account_deletion_requests WHERE user_id=%s ORDER BY id DESC LIMIT 10"),
    "flags": ("Ограничения", "SELECT frozen,reason,frozen_since,frozen_until FROM account_restrictions WHERE user_id=%s"),
    "verification": ("Верификация и метки", "SELECT verified,support,scam,fake FROM users WHERE id=%s"),
    "premium": ("Premium", "SELECT premium_expires_at FROM users WHERE id=%s"),
    "names": ("Юзернеймы", "SELECT username,active,editable FROM peer_usernames WHERE peer_type='user' AND peer_id=%s ORDER BY sort_order LIMIT 30"),
    "nfts": ("NFT-юзернеймы", "SELECT username,status,amount,currency FROM collectible_usernames WHERE owner_peer_type='user' AND owner_peer_id=%s AND status='owned' LIMIT 30"),
    "gifts": ("NFT-подарки", "SELECT title,slug,num FROM unique_star_gifts WHERE owner_peer_type='user' AND owner_peer_id=%s AND NOT burned ORDER BY id DESC LIMIT 20"),
    "ordinary": ("Обычные подарки", "SELECT gift_id,gift_date,lifecycle_status FROM peer_star_gifts WHERE owner_peer_type='user' AND owner_peer_id=%s AND unique_gift_id IS NULL AND NOT converted ORDER BY id DESC LIMIT 20"),
    "stars": ("История Stars", "SELECT amount,reason,date FROM stars_transactions WHERE user_id=%s ORDER BY date DESC LIMIT 20"),
    "gram": ("История Gram (нанограмы)", "SELECT kind,amount_nanoton,created_at FROM wallet_gram_commands WHERE from_user_id=%s OR to_user_id=%s ORDER BY id DESC LIMIT 20"),
    "nfthistory": ("Передачи NFT-юзов", "SELECT c.username,t.kind,t.created_at FROM collectible_username_transfers t JOIN collectible_usernames c ON c.id=t.collectible_id WHERE (t.from_peer_type='user' AND t.from_peer_id=%s) OR (t.to_peer_type='user' AND t.to_peer_id=%s) ORDER BY t.id DESC LIMIT 20"),
    "sales": ("Продажи подарков", "SELECT unique_gift_id,currency,amount,sold_at FROM star_gift_sales WHERE (seller_peer_type='user' AND seller_peer_id=%s) OR (buyer_peer_type='user' AND buyer_peer_id=%s) ORDER BY id DESC LIMIT 20"),
    "offers": ("Предложения подарков", "SELECT unique_gift_id,currency,amount,status,created_at FROM star_gift_offers WHERE buyer_user_id=%s OR (owner_peer_type='user' AND owner_peer_id=%s) ORDER BY id DESC LIMIT 20"),
    "audit": ("Действия администрации", "SELECT action,status,created_at FROM admin_audit_logs WHERE target_user_id=%s AND NOT dry_run ORDER BY id DESC LIMIT 20"),
    "userappeals": ("Старые апелляции", "SELECT id,status,created_at FROM risegram_freeze_appeals WHERE user_id=%s ORDER BY id DESC LIMIT 20"),
}


async def ask(event,state,key,ident=None):
    await state.set_state(Work.text)
    await state.set_data({"support_action":key,"ident":ident})
    prompts = {
        "finduser":"Введите @username, native ID или local:ID из бота",
        "findticket":"Введите номер тикета",
        "reply":"Введите текст ответа пользователю (до 3000 символов)",
        "note":"Введите внутреннюю заметку (до 1000 символов)",
        "reason":"Укажите причину действия (минимум 10 символов). Сотрудник: начните с номера открытого тикета и пробела",
    }
    await show(event,prompts[key]+"\n/cancel — отмена",kb(back()))


async def ticket(event,tid):
    t = await db.get_support_ticket(tid)
    if not t:
        return await show(event,"Тикет не найден",kb(back()))
    m = await db.fetchone("SELECT * FROM support_console_meta WHERE ticket_id=?", (tid,)) or {}
    rows = [[button("✍️ Ответить","reply",tid),button("📝 Заметка","note",tid)],
            [button("🚨 Приоритет","priority",tid),button("👤 Назначить себе","assign",tid)],
            [button("✅ Закрыть","close",tid),button("♻️ Переоткрыть","reopen",tid)],
            [button("👤 Аккаунт заявителя","ticketuser",tid)],back()]
    text = ("<b>Тикет #"+str(tid)+"</b> · "+esc(t["status"])+"\nID бота: "+str(t["tg_id"])+
            "\nПриоритет: "+("высокий" if m.get("priority") else "обычный")+
            "\nНазначен: "+str(m.get("assigned_to") or "—")+"\n\n"+esc(t["message"][:2300])+
            "\n\nЗаметка: "+esc((m.get("note") or "—")[:500]))
    await show(event,text,kb(*rows))


async def tickets(event,key,uid=None,nid=None):
    where = {"open":"t.status='open'","archive":"t.status<>'open'","prioritylist":"t.status='open' AND m.priority=1","mine":"t.status='open' AND m.assigned_to=?","usertickets":"t.tg_id=?"}[key]
    params = (uid,) if key in ("mine","usertickets") else ()
    rows = await db.fetchall("SELECT t.id,t.status,t.message FROM support_tickets t LEFT JOIN support_console_meta m ON m.ticket_id=t.id WHERE "+where+" ORDER BY t.id DESC LIMIT 20",params)
    keys = [[button("#"+str(t["id"])+" · "+t["status"]+" · "+t["message"].replace("\n"," ")[:35],"ticket",t["id"])] for t in rows]
    if nid:
        keys.append([button("👤 Аккаунт","account",nid)])
    await show(event,"📨 <b>Обращения</b>\n"+("Выберите тикет" if rows else "Нет обращений"),kb(*keys,back()))


async def protected(nid):
    u = await profile(nid)
    if not u or u["deleted_at"] or u["is_bot"] or u["support"] or nid==SUPPORT_NATIVE_ID:
        return True
    # Fail closed if owner mapping cannot be verified.
    owner_id = await native_for_local(int(config.OWNER_ID))
    if owner_id is None:
        local = await db.get_user(int(config.OWNER_ID))
        if local and local.get("phone_digits"):
            info = await recovery.native_info(str(local["phone_digits"]))
            owner_id = int(info["id"]) if info else None
    return owner_id is None or nid==owner_id


async def security_change(event, key, nid, reason, command):
    if await protected(nid):
        raise ValueError("Защищённый аккаунт или не подтверждена привязка владельца")
    local = await local_for_native(nid)
    if not owner(event):
        match = re.match(r"^#?(\d+)\s+",reason)
        t = await db.get_support_ticket(int(match[1])) if match else None
        if not local or not t or t["status"]!="open" or int(t["tg_id"])!=int(local["tg_id"]):
            raise ValueError("Для сотрудника нужен открытый тикет этого подтверждённого аккаунта")
    actor = event.from_user.id
    await log_action(event,"requested "+key+" native="+str(nid)+" key="+command)
    if key == "revoke":
        rows = await sql("SELECT hash FROM authorizations WHERE user_id=%s", (nid,))
        for i,row in enumerate(rows):
            if not row["hash"]:
                raise ValueError("Сессия без идентификатора; требуется проверка владельцем")
            await recovery.native_action("revoke-sessions", command+"-"+str(i), actor,
                {"user_id":nid,"hash":int(row["hash"]),"reason":reason})
        after = await sql("SELECT count(*) AS n FROM authorizations WHERE user_id=%s",(nid,))
        if int(after[0]["n"]):
            raise ValueError("Не все native-сессии завершены; проверьте список устройств")
        if local:
            uid=int(local["tg_id"])
            async with db.connect() as conn:
                await conn.execute("DELETE FROM sessions WHERE tg_id=?", (uid,))
                await conn.execute("UPDATE auth_codes SET used=1 WHERE tg_id=? AND used=0",(uid,))
                await conn.execute("UPDATE users SET auth_code=NULL,auth_code_expires=NULL WHERE tg_id=?",(uid,))
                await conn.commit()
        result="Native-сессии завершены: "+str(len(rows))+". "+("Сессии и коды бота тоже аннулированы" if local else "Привязка к боту не подтверждена — локальные сессии не тронуты")
    elif key == "invalidate":
        if not local:
            raise ValueError("Нет подтверждённой связи с аккаунтом бота")
        uid=int(local["tg_id"])
        async with db.connect() as conn:
            await conn.execute("UPDATE auth_codes SET used=1 WHERE tg_id=? AND used=0",(uid,))
            await conn.execute("UPDATE users SET auth_code=NULL,auth_code_expires=NULL WHERE tg_id=?",(uid,))
            await conn.commit()
        result="Активные коды бота аннулированы"
    elif key in ("freeze","unfreeze"):
        if await recovery.native_pending(nid) or (local and await recovery.pending_for_local(int(local["tg_id"]))):
            raise ValueError("Аккаунт ожидает удаления — используйте процедуру восстановления")
        flags=await recovery.native_flags(nid)
        if key=="freeze" and flags and flags["frozen"]:
            raise ValueError("Аккаунт уже заморожен")
        if key=="unfreeze" and (not flags or not flags["frozen"]):
            raise ValueError("Аккаунт не заморожен")
        from datetime import timedelta
        fields={"user_id":nid,"frozen":key=="freeze","reason":reason}
        if key=="freeze":
            fields.update(freeze_until=(recovery.utc_now()+timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ"),freeze_appeal_url=freeze_appeals.make_url())
        await recovery.native_action("set-frozen",command,actor,fields)
        flags=await recovery.native_flags(nid)
        if bool(flags and flags["frozen"]) != (key=="freeze"):
            raise ValueError("Сервер не подтвердил изменение")
        result="Заморозка на 24 часа установлена" if key=="freeze" else "Заморозка снята"
    else:
        raise ValueError("Неизвестное действие")
    await log_action(event,"completed "+key+" native="+str(nid)+" key="+command)
    return result


@support_router.message(StateFilter(Work.text), F.text)
async def input_text(message: Message,state:FSMContext):
    data=await state.get_data()
    key=data.get("support_action")
    ident=data.get("ident")
    value=(message.text or "").strip()
    if value=="/cancel":
        await state.clear()
        return await home(message)
    if value.startswith("/"):
        return await message.answer("Введите данные или /cancel")
    await state.clear()
    try:
        if key=="finduser":
            nid=await resolve(value)
            if not nid:
                return await message.answer("Аккаунт не найден однозначно",reply_markup=kb(back()))
            return await account(message,nid)
        if key=="findticket":
            return await ticket(message,int(value)) if value.isdigit() and len(value)<=12 else await message.answer("Неверный ID",reply_markup=kb(back()))
        if key in ("reply","note"):
            tid=int(ident)
            await setup()
            if not 1<=len(value)<=(3000 if key=="reply" else 1000):
                return await message.answer("Неверная длина текста",reply_markup=kb(back()))
            async with _ticket_lock:
                t=await db.get_support_ticket(tid)
                if not t:
                    return await message.answer("Тикет не найден",reply_markup=kb(back()))
                if key=="reply":
                    if t["status"]!="open":
                        return await message.answer("Сначала переоткройте тикет",reply_markup=kb(back()))
                    if not await safe_send(message.bot,int(t["tg_id"]),"🆘 <b>Ответ поддержки #"+str(tid)+"</b>\n\n"+esc(value)):
                        return await message.answer("Ответ не доставлен; тикет открыт",reply_markup=kb(back()))
                    await db.close_support_ticket(tid)
                else:
                    await db.execute("INSERT INTO support_console_meta(ticket_id,note) VALUES(?,?) ON CONFLICT(ticket_id) DO UPDATE SET note=excluded.note",(tid,value))
            await log_action(message,key+" ticket="+str(tid))
            return await ticket(message,tid)
        if key=="reason":
            action,nid=ident
            nid=int(nid)
            if not 10<=len(value)<=400:
                return await message.answer("Причина должна содержать 10–400 символов",reply_markup=kb(back()))
            token=secrets.token_hex(8)
            now=time.monotonic()
            for old in list(_pending):
                if _pending[old][0]<now:
                    _pending.pop(old,None)
            _pending[token]=(now+180,message.from_user.id,action,nid,value)
            return await show(message,"⚠️ <b>Подтверждение</b>\n"+esc(dict(revoke="Завершить ВСЕ сессии",invalidate="Аннулировать коды",freeze="Заморозить на 24 часа",unfreeze="Разморозить")[action])+"\nNative ID: "+str(nid)+"\nПричина: "+esc(value)+"\nАккаунт и NFT не удаляются",kb([button("Подтвердить","confirm",token)],back()))
    except Exception:
        log.exception("Support input failed")
        await message.answer("Не удалось выполнить действие. Проверьте данные и сервер",reply_markup=kb(back()))


@support_router.callback_query(F.data.startswith(P))
async def callback(call:CallbackQuery,state:FSMContext,user:dict):
    await state.clear()
    try:
        parts=call.data[len(P):].split(":")
        key=parts[0]
        arg=parts[1] if len(parts)>1 else ""
        if key=="home":
            return await home(call)
        if key=="confirm":
            item=_pending.get(arg)
            if not item or item[0]<time.monotonic() or item[1]!=call.from_user.id:
                return await show(call,"Подтверждение истекло",kb(back()))
            _pending.pop(arg,None)
            await call.answer("Выполняется")
            try:
                result=await security_change(call,item[2],item[3],item[4],"rg-support-"+arg)
                return await show(call,"✅ "+esc(result),kb([button("👤 Аккаунт","account",item[3])],back()))
            except ValueError as e:
                return await show(call,"⚠️ "+esc(str(e)),kb(back()))
            except Exception:
                log.exception("Support security command failed")
                return await show(call,"⚠️ Операция не подтверждена или выполнена частично. Проверьте устройства и журнал перед повтором",kb(back()))
        if key in ("findticket","finduser"):
            return await ask(call,state,key)
        await setup()
        if key in ("open","archive","prioritylist","mine"):
            return await tickets(call,key,int(user["tg_id"]))
        if key=="stats":
            rows=await db.fetchall("SELECT status,COUNT(*) AS count FROM support_tickets GROUP BY status")
            return await show(call,"📊 <b>Статистика тикетов</b>\n"+render(rows),kb(back()))
        if key=="logs":
            rows=await db.fetchall("SELECT action_text,created_at FROM admin_logs WHERE action_text LIKE 'SUPPORT %' ORDER BY id DESC LIMIT 15")
            return await show(call,"📜 <b>Журнал поддержки</b>\n"+render(rows),kb(back()))
        if key=="staff":
            if not owner(call):
                return await call.answer("Только владелец",show_alert=True)
            return await show(call,"👥 Сотрудники: "+esc(await db.get_meta("support_staff","") or "нет")+"\n<code>/supportstaff add ID</code>\n<code>/supportstaff remove ID</code>",kb(back()))
        if key=="appeals":
            rows=await sql("SELECT id,user_id,status,created_at FROM risegram_freeze_appeals ORDER BY id DESC LIMIT 20")
            return await show(call,"❄️ <b>Старые апелляции</b>\n"+render(rows),kb(back()))
        if not arg.isdigit() or len(arg)>18:
            return await call.answer("Неверный ID",show_alert=True)
        ident=int(arg)
        if key=="ticket":
            return await ticket(call,ident)
        if key in ("reply","note"):
            return await ask(call,state,key,ident)
        if key in ("priority","assign","close","reopen","ticketuser"):
            t=await db.get_support_ticket(ident)
            if not t:
                return await show(call,"Тикет не найден",kb(back()))
            if key=="ticketuser":
                nid=await native_for_local(int(t["tg_id"]))
                if not nid:
                    return await show(call,"Нет подтверждённой привязки native аккаунта",kb([button("Тикет","ticket",ident)],back()))
                return await account(call,nid)
            async with _ticket_lock:
                if key=="priority":
                    await db.execute("INSERT INTO support_console_meta(ticket_id,priority) VALUES(?,1) ON CONFLICT(ticket_id) DO UPDATE SET priority=1-priority",(ident,))
                elif key=="assign":
                    await db.execute("INSERT INTO support_console_meta(ticket_id,assigned_to) VALUES(?,?) ON CONFLICT(ticket_id) DO UPDATE SET assigned_to=excluded.assigned_to",(ident,int(user["tg_id"])))
                elif key=="close":
                    await db.execute("UPDATE support_tickets SET status='closed' WHERE id=? AND status='open'",(ident,))
                else:
                    await db.execute("UPDATE support_tickets SET status='open',replied_at=NULL WHERE id=?",(ident,))
            await log_action(call,key+" ticket="+str(ident))
            return await ticket(call,ident)
        if key=="account":
            return await account(call,ident)
        if not await profile(ident):
            return await show(call,"Аккаунт не найден",kb(back()))
        if key in GROUPS:
            rows=[[button(label,action,ident)] for label,action in GROUPS[key]]
            return await show(call,"<b>"+dict(security="🔐 Безопасность",assets="💎 Активы",moderation="⚖️ Модерация")[key]+"</b>\nNative ID: "+str(ident),kb(*rows,[button("👤 Аккаунт","account",ident)],back()))
        if key in ("revoke","invalidate","freeze","unfreeze"):
            if await protected(ident):
                return await show(call,"Защищённый аккаунт или не подтверждён владелец",kb(back()))
            return await ask(call,state,"reason",[key,ident])
        if key=="usertickets":
            local=await local_for_native(ident)
            if not local:
                return await show(call,"Нет подтверждённой связи с ботом",kb(back()))
            return await tickets(call,key,int(local["tg_id"]),ident)
        if key=="codes":
            local=await local_for_native(ident)
            text="Нет подтверждённой связи с ботом"
            if local:
                count=await db.scalar("SELECT COUNT(*) FROM auth_codes WHERE tg_id=? AND used=0 AND expires_at>?",(local["tg_id"],db.now()))
                text="Активных кодов бота: "+str(count)+"\nЗначения кодов скрыты"
        elif key=="balances":
            stars=await sql("SELECT balance FROM stars_balances WHERE user_id=%s",(ident,))
            text="⭐ Stars: "+str(stars[0]["balance"] if stars else 0)
            local=await local_for_native(ident)
            if local:
                from api import native_wallet
                w=await native_wallet(local)
                if w:
                    text+="\n💎 Gram: "+esc(str(w.get("gram_balance","—")))
            else:
                text+="\nGram: нет подтверждённой связи с ботом"
        elif key in QUERIES:
            title,q=QUERIES[key]
            rows=await sql(q,(ident,)*q.count("%s"))
            text="<b>"+title+"</b>\n"+render(rows)
        else:
            return await call.answer("Неизвестная команда",show_alert=True)
        await show(call,text,kb([button("👤 Аккаунт","account",ident)],back()))
    except Exception:
        log.exception("Support callback failed")
        await show(call,"Не удалось выполнить запрос — проверьте доступность сервера",kb(back()))
