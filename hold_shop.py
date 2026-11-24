"""Hold cosmetic mark purchases in internal RiseStars, never Telegram Stars.
The debit and mark are in one transaction. Existing marks are never overwritten."""
import re
import native_economy as economy

PRICE=80
KINDS=("account","group","channel")
DESCRIPTION={"account":"Аккаунт","group":"Группа","channel":"Канал"}

async def actor(user):
    from support_console import native_for_local
    if not user or user.get("banned"):
        raise economy.WalletError("Аккаунт недоступен")
    uid=await native_for_local(int(user["tg_id"]))
    if not uid:
        raise economy.WalletError("Сначала свяжи аккаунт с клиентом RiseGram")
    return int(uid)

async def resolve(uid,kind,ref):
    if kind=="account":
        return uid
    if kind not in KINDS:
        raise economy.WalletError("Неверный тип")
    name=ref.strip().lstrip("@").lower()
    if not re.fullmatch(r"(?:[a-z][a-z0-9_]{0,31}|[а-яё][а-яё0-9_]{0,31})",name):
        raise economy.WalletError("Введите @юзернейм своего чата")
    async with economy.transaction() as conn:
        rows=await (await conn.execute("""SELECT c.id FROM channels c JOIN peer_usernames p
            ON p.peer_type='channel' AND p.peer_id=c.id AND p.active
            WHERE p.username_lower=%s AND c.creator_user_id=%s AND NOT c.deleted
              AND CASE WHEN %s='group' THEN c.megagroup ELSE c.broadcast AND NOT c.megagroup END""",(name,uid,kind))).fetchall()
    if len(rows)!=1:
        raise economy.WalletError("Чат не найден или ты не его создатель")
    return int(rows[0]["id"])

async def check(conn,uid,kind,pid):
    if kind not in KINDS or not 0<pid<2**63:
        raise economy.WalletError("Неверный объект")
    user=await (await conn.execute("SELECT id FROM users WHERE id=%s AND NOT is_bot AND deleted_at IS NULL FOR UPDATE",(uid,))).fetchone()
    if not user:
        raise economy.WalletError("Аккаунт недоступен")
    if kind=="account":
        if pid!=uid:
            raise economy.WalletError("Только собственный аккаунт")
        peer="user"
    else:
        c=await (await conn.execute("""SELECT id FROM channels WHERE id=%s AND creator_user_id=%s AND NOT deleted
            AND CASE WHEN %s='group' THEN megagroup ELSE broadcast AND NOT megagroup END FOR UPDATE""",(pid,uid,kind))).fetchone()
        if not c:
            raise economy.WalletError("Покупка только для создателя чата")
        peer="channel"
    issuer=await (await conn.execute("""SELECT bot_id,icon_document_id,version FROM bot_verifier_settings
        WHERE company_name='Hold' AND enabled FOR UPDATE""")).fetchone()
    if not issuer or int(issuer["version"])<2:
        raise economy.WalletError("Hold пока не опубликован")
    existing=await (await conn.execute("SELECT verifier_bot_id FROM custom_verifications WHERE peer_type=%s AND peer_id=%s FOR UPDATE",(peer,pid))).fetchone()
    if existing:
        raise economy.WalletError("Hold уже установлен" if existing["verifier_bot_id"]==issuer["bot_id"] else "У профиля уже есть другая метка. Покупка не заменяет её")
    balance=await (await conn.execute("SELECT balance FROM stars_balances WHERE user_id=%s FOR UPDATE",(uid,))).fetchone()
    if not balance or int(balance["balance"])<PRICE:
        raise economy.WalletError("Недостаточно RiseStars")
    return peer,issuer,int(balance["balance"])

async def preview(user,kind,pid):
    uid=await actor(user)
    async with economy.transaction() as conn:
        _,_,balance=await check(conn,uid,kind,pid)
    return balance

async def purchase(user,kind,pid,key):
    uid=await actor(user)
    receipt="hold-purchase:"+str(uid)+":"+key
    async with economy.transaction() as conn:
        # Serialize attempts from this account; replay cannot re-charge even if a
        # staff member subsequently revokes the purchased cosmetic mark.
        await conn.execute("SELECT pg_advisory_xact_lock(%s)",(uid,))
        old=await (await conn.execute("SELECT movements FROM risegram_bot_receipts WHERE key=%s",(receipt,))).fetchone()
        if old:
            return False
        peer,issuer,_=await check(conn,uid,kind,pid)
        verb="верифицирована" if kind=="group" else "верифицирован"
        description=f"{DESCRIPTION[kind]} {verb} организацией «Hold». Платная метка, не подтверждение личности."
        inserted=await (await conn.execute("""INSERT INTO custom_verifications
            (verifier_bot_id,peer_type,peer_id,icon_document_id,description,granted_by_user_id,created_at,updated_at)
            VALUES(%s,%s,%s,%s,%s,%s,now(),now()) ON CONFLICT(peer_type,peer_id) DO NOTHING RETURNING id""",
            (issuer["bot_id"],peer,pid,issuer["icon_document_id"],description,uid))).fetchone()
        if not inserted:
            raise economy.WalletError("Метка уже существует, звёзды не списаны")
        await economy.post(conn,receipt,[(uid,"stars",-PRICE)],"Hold · косметическая верификация")
    return True
