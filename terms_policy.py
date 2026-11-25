"""Versioned public terms and explicit acceptance before login-code delivery."""
import asyncio
import hashlib
import html
import time
from aiohttp import web
import database as db
from ui import btn,kb,safe_send,esc

VERSION="2026-11-24.1"
URL="https://risegram-risegram.up.railway.app/terms"
TEXT="""ПОЛЬЗОВАТЕЛЬСКОЕ СОГЛАШЕНИЕ И ПОЛИТИКА КОНФИДЕНЦИАЛЬНОСТИ RISEGRAM
Редакция от 24 ноября 2026 года

1. Сервис и принятие условий
RiseGram — самостоятельный проект, не являющийся официальным Telegram и не связанный с ним. Сервис находится в разработке. Принятие условий добровольное; если вы не согласны, не продолжайте вход. Если по применимому законодательству требуется согласие законного представителя, получите его. Существенные изменения условий публикуются в новой редакции; для новых входов требуется повторное принятие. Официальные ресурсы: @rise_gram, @risegrambot, @chat. Связь с администрацией через «Поддержка» в @risegrambot или @support внутри RiseGram.

2. Аккаунт и безопасность
Используйте свой виртуальный номер RiseGram. Не передавайте коды входа, данные сессий и пароли. Запрещён доступ к чужим аккаунтам без разрешения, мошенничество, фишинг, вредоносный код, обход блокировок, травля, публикация чужих личных данных без законного основания, незаконный контент и нарушение работы сервиса. Сообщайте об уязвимостях в поддержку, не используйте их для получения выгоды.

3. Внутренние активы и покупки
RiseStars, Gram, виртуальные номера, NFT-юзернеймы и коллекционные подарки — внутренние виртуальные активы RiseGram, а не активы Telegram или гарантированные активы блокчейна TON. Они не являются денежным вкладом, инвестиционным продуктом или обещанием дохода либо вывода денег. Курс внутри проекта: 1 Gram = 100 RiseStars; он не означает курс обмена на реальные деньги.
Перед покупкой проверяйте товар, цену, валюту, получателя и условия. Внутренние покупки списывают RiseStars/Gram, а отдельные покупки через Telegram могут оплачиваться Telegram Stars. Оплата через Telegram также регулируется правилами соответствующей платформы. Платные метки, включая Hold, косметические: не подтверждают личность и не предоставляют права модератора или администратора.
Цены и доступность могут меняться для будущих покупок. При ошибочном, повторном списании или недоставленном товаре обратитесь в поддержку с номером операции; администрация проверит платёж и, при наличии оснований, предоставит исправление либо возврат доступным способом. Это не ограничивает обязательные права покупателя. Не отправляйте коды входа или платёжные секреты.
Администрация вправе исправлять ошибочные начисления и последствия эксплуатации уязвимостей, ограничивать операции при угрозе безопасности. Виртуальные активы могут изменяться при обновлениях, о существенных изменениях по возможности сообщается заранее.

4. Общение, модерация и жалобы
Соблюдайте правила групп и каналов. В официальном чате правила доступны по !правила. Модерация может ограничивать отправку сообщений, выдавать предупреждения и блокировать за нарушения. Автоматическая проверка спама использует частоту и повторяемость сообщений; спорные случаи рассматривают люди. Обжалование и исправление ошибок — через поддержку. Служебные метки могут быть отозваны при изменении роли. Запрещён обход ограничений.

5. Работа сервиса
Возможны ошибки, временные отключения, ограничения функциональности и обновления. Администрация стремится устранять ошибки и восстанавливать данные при сбоях, но не обещает непрерывную доступность. Не используйте экспериментальный сервис как единственное место хранения важных данных.

6. Какие данные обрабатываются
Для работы сервиса используются внутренний ID аккаунта, виртуальный номер, имя, юзернейм, сведения о профиле и привязке к Telegram-боту, записи авторизации и сессий, технические данные соединений и журналов, сообщения и медиа, данные групп и каналов, списки контактов и взаимодействий, балансы и история операций, обращения в поддержку и записи модерации. Публичные профили, группы и каналы могут быть видны другим пользователям согласно настройкам и типу пространства. Не публикуйте секретные и лишние персональные данные. Не следует считать все чаты сквозным шифрованием.

7. Для чего используются данные
Вход и восстановление аккаунта; доставка сообщений; работа профилей, групп, подарков, валюты и покупок; предотвращение мошенничества, спама и злоупотреблений; диагностика ошибок; поддержка пользователей и исполнение применимых обязательств. Запись принятия условий хранит внутренний идентификатор, версию и дату принятия.

8. Хранение, доступ и передача
Сервис использует серверную инфраструктуру, в том числе Railway и Neon, а связанный Telegram-бот используется для доставки кодов и отдельных функций. Данные могут обрабатываться инфраструктурными поставщиками и авторизованными сотрудниками в объёме, необходимом для работы и безопасности сервиса; сообщения, отправленные другим участникам, доступны получателям, а боты в группах могут обрабатывать доступные им сообщения. Возможна обработка за пределами страны пользователя. Доступ к служебным данным должен быть ограничен назначением и полномочиями. Не следует считать сервис анонимным.
Данные хранятся, пока нужны для работы аккаунта, безопасности, расчётов, разрешения споров и применимых обязательств. Резервные копии и записи операций могут сохраняться после удаления аккаунта. Точный срок зависит от категории данных и необходимости их сохранения.

9. Запросы пользователя и удаление
Через поддержку можно запросить сведения об обработке своих данных, исправление, удаление аккаунта и данных либо ограничение обработки, где это применимо. Для защиты от чужих запросов может потребоваться подтверждение владения аккаунтом без передачи кодов входа. Некоторые записи могут сохраняться, если это необходимо для безопасности, разрешения споров или соблюдения обязательных требований. Удаление аккаунта может привести к потере внутренних активов; уже отправленные сообщения у других участников могут сохраниться.

10. Изменения и контакты
Актуальная версия доступна по адресу https://risegram-risegram.up.railway.app/terms. По вопросам условий, конфиденциальности, платежей и обжалования обращайтесь в «Поддержка» в @risegrambot или @support внутри RiseGram. Условия не отменяют обязательные права, предусмотренные применимым законодательством.
"""
DIGEST=hashlib.sha256(TEXT.encode()).hexdigest()
PENDING={}
LOCKS=[asyncio.Lock() for _ in range(128)]
BASE="https://risegram-risegram.up.railway.app"
PRIVACY_URL=BASE+"/privacy"
PURCHASE_URL=BASE+"/purchase-terms"
PURCHASE_VERSION="2026-11-24.1"
# The separate purchase document uses the same published economic terms.
PURCHASE_TEXT=TEXT.split("3. Внутренние активы и покупки\n",1)[1].split("\n4. Общение",1)[0].strip()
PURCHASE_DIGEST=hashlib.sha256(PURCHASE_TEXT.encode()).hexdigest()
PROMPT=("💎 <b>Добро пожаловать в RiseGram!</b>\n\n"
        "Перед скачиванием и использованием приложения ознакомьтесь с пользовательским соглашением и политикой конфиденциальности.\n\n"
        "Нажимая «Принимаю», вы подтверждаете согласие с условиями пользовательского соглашения и ознакомление с политикой конфиденциальности.\n"
        "Редакция: "+VERSION)
PURCHASE_PROMPT=("🛒 <b>Условия покупки в RiseGram</b>\n\n"
        "Перед первой покупкой ознакомьтесь с условиями оплаты, получения виртуальных товаров и возвратов.\n\n"
        "Нажимая «Принимаю», вы соглашаетесь с условиями покупок. Согласие сохраняется, повторно принимать его не нужно, пока редакция не изменится.")

def markup(uid=None):
    suffix = ":" + str(uid) if uid is not None else ""
    return kb([btn("📄 Пользовательское соглашение",url=URL)],
              [btn("🔐 Политика конфиденциальности",url=PRIVACY_URL)],
              [btn("✅ Принимаю","terms:accept:"+VERSION+suffix,style="success")],
              [btn("Не согласен","terms:decline:"+VERSION+suffix)])

def purchase_markup():
    return kb([btn("📄 Условия покупки",url=PURCHASE_URL)],
              [btn("✅ Принимаю","purchase_terms:accept:"+PURCHASE_VERSION,style="success")],
              [btn("Отмена","purchase_terms:decline:"+PURCHASE_VERSION)])

async def purchase_accepted(uid):
    return bool(await db.fetchone("SELECT 1 AS x FROM rg_terms_acceptance WHERE tg_id=? AND version=? AND document_hash=?",
                                  (uid,"purchase:"+PURCHASE_VERSION,PURCHASE_DIGEST)))

async def accept_purchase(uid):
    await db.execute("""INSERT INTO rg_terms_acceptance(tg_id,version,document_hash,accepted_at)
        VALUES(?,?,?,?) ON CONFLICT(tg_id,version) DO NOTHING""",
        (uid,"purchase:"+PURCHASE_VERSION,PURCHASE_DIGEST,db.now()))

async def accepted(uid):
    return bool(await db.fetchone("SELECT 1 AS x FROM rg_terms_acceptance WHERE tg_id=? AND version=? AND document_hash=?",(uid,VERSION,DIGEST)))

async def accept(uid):
    await db.execute("""INSERT INTO rg_terms_acceptance(tg_id,version,document_hash,accepted_at)
        VALUES(?,?,?,?) ON CONFLICT(tg_id,version) DO NOTHING""",(uid,VERSION,DIGEST,db.now()))

def lock_for(uid):
    return LOCKS[uid % len(LOCKS)]

async def prompt(bot,uid,code_message=None):
    # Keep pending OTP only in volatile memory, never logs or persistent storage.
    # It must be a newly authenticated delivery from the OTP provider.
    now=time.monotonic()
    for k,v in list(PENDING.items()):
        if v[0]<now:
            PENDING.pop(k,None)
    if code_message is not None and len(PENDING)<10000:
        item=(now+120,code_message)
        PENDING[uid]=item
        def erase():
            if PENDING.get(uid) is item:
                PENDING.pop(uid,None)
        asyncio.get_running_loop().call_later(120,erase)
    sent=await safe_send(bot,uid,PROMPT,markup(uid))
    if not sent:
        PENDING.pop(uid,None)
    return sent

async def confirm(bot,uid):
    async with lock_for(uid):
        await accept(uid)
        pending=PENDING.pop(uid,None)
        if pending and pending[0]>=time.monotonic():
            return await safe_send(bot,uid,pending[1])
    return False

async def page(request):
    path=request.path
    if path=="/privacy":
        title="Политика конфиденциальности RiseGram"
        body_text="Редакция от 24 ноября 2026 года\n\n6. "+TEXT.split("\n6. ",1)[1]
    elif path=="/purchase-terms":
        title="Условия покупок RiseGram"
        body_text="Редакция от 24 ноября 2026 года\n\n"+PURCHASE_TEXT+"\n\nПо вопросам платежей и возвратов: «Поддержка» в @risegrambot."
    else:
        title="Пользовательское соглашение RiseGram"
        body_text=TEXT
    body="<!doctype html><html lang='ru'><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>"+html.escape(title)+"</title><style>body{max-width:760px;margin:32px auto;padding:0 20px;font:16px/1.6 system-ui;color:#25252a}h1{font-size:1.6rem}article{white-space:pre-wrap}a{color:#2563eb}</style><h1>"+html.escape(title)+"</h1><article>"+html.escape(body_text)+"</article></html>"
    return web.Response(text=body,content_type="text/html",headers={"Cache-Control":"no-store","X-Content-Type-Options":"nosniff","Content-Security-Policy":"default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'"})
