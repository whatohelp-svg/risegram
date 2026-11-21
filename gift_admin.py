"""Owner-only Telegram collectible drop editor for the RiseGram bot."""
from __future__ import annotations

import asyncio
import math
from datetime import datetime, timedelta, timezone

import aiohttp
from aiogram.types import CallbackQuery, Message

import config
import database as db
import gift_drops as gifts
from ui import btn, esc, kb, show

ALMATY = timezone(timedelta(hours=5))
PAGE = 50

async def screen(event, page=0):
    rows, count = await asyncio.to_thread(gifts.catalog, max(0, page), PAGE)
    pages=max(1, math.ceil(count/PAGE))
    page=min(max(0,page),pages-1)
    if not rows and count:
        rows, count = await asyncio.to_thread(gifts.catalog,page,PAGE)
    start=page*PAGE
    text=(f"🎁 <b>Telegram NFT — каталог RiseGram</b>\n"
          f"Коллекций: <b>{count}</b> · страница {page+1}/{pages}\n\n"
          "Нажми номер подарка. 5 столбцов × 10 строк.\n\n")
    text+="\n".join(f"<code>{start+i+1:03}</code> {esc(r['title'])}" for i,r in enumerate(rows))
    grid=[]
    for i in range(0,len(rows),5):
        grid.append([btn(f"{start+j+1:03}",f"adm:gift:pick:{rows[j]['gift_id']}",
                         style=("primary","success","danger")[(start+j)%3])
                     for j in range(i,min(i+5,len(rows)))])
    grid.append([btn("‹",f"adm:gifts:{page-1}",style="primary")] if page else []+
                [btn(f"{page+1}/{pages}","adm:noop")]+
                ([btn("›",f"adm:gifts:{page+1}",style="primary")] if page+1<pages else []))
    # Rebuild pagination explicitly to keep first/last pages consistent.
    nav=[]
    if page>0: nav.append(btn("‹",f"adm:gifts:{page-1}",style="primary"))
    nav.append(btn(f"{page+1}/{pages}","adm:noop"))
    if page+1<pages:nav.append(btn("›",f"adm:gifts:{page+1}",style="primary"))
    grid[-1]=nav
    grid += [[btn("🔄 Обновить список", "adm:gift:refresh"),
              btn("📦 Управлять дропами","adm:gift:list")],
             [btn("↩️ Админка","adm:home")]]
    await show(event,text,kb(*grid))

async def my_drops(event):
    items=await asyncio.to_thread(gifts.list_admin_drops)
    text="📦 <b>Дропы RiseGram</b>\n\n"
    if not items:text+="Пока дропов нет."
    rows=[]
    for g in items:
        enabled=bool(g["enabled"])
        text+=(f"#{g['id']} {esc(g['title'])} · {g['sold']}/{g['supply']} · "
               f"{g['price_stars']} RiseStars · {'🟢' if enabled else '🔴'}\n")
        rows.append([btn(f"{'🔴 Остановить' if enabled else '🟢 Возобновить'} #{g['id']}",
                         f"adm:gift:toggle:{g['id']}",style="danger" if enabled else "success")])
    await show(event,text,kb(*rows,[btn("🎁 Каталог","adm:gifts:0")],[btn("↩️ Админка","adm:home")]))

async def choose_upgrade(event,state,allow):
    data=await state.get_data()
    if not data.get("gift_id") or "qty" not in data or "price" not in data:
        return await show(event,"Сессия устарела. Начни заново.",kb([btn("Каталог","adm:gifts:0")]))
    await state.update_data(upgrade=bool(allow))
    await show(event,
         "🕐 <b>Когда открыть продажи?</b>\nВремя по Петропавловску и Алматы (UTC+5)",
         kb([btn("🚀 Сразу","adm:gift:when:now",style="success"),
             btn("🗓 По расписанию","adm:gift:when:later",style="primary")],
            [btn("✖️ Отмена","adm:gifts:0",style="danger")]))

async def confirmation(event,state):
    data=await state.get_data()
    gift=await asyncio.to_thread(gifts.gift_info,data.get("gift_id",""))
    if not gift:return await show(event,"Подарок не найден",kb([btn("Назад","adm:gifts:0")]))
    start=data.get("starts_at")
    human="сразу" if not start else datetime.strptime(start,"%Y-%m-%d %H:%M:%S").replace(
       tzinfo=timezone.utc).astimezone(ALMATY).strftime("%d.%m.%Y %H:%M")
    await show(event,
      f"📋 <b>Проверь дроп</b>\n\n"
      f"🎁 {esc(gift['title'])}\n"
      f"📦 Количество: <b>{data['qty']}</b>\n"
      f"⭐ Цена: <b>{data['price']}</b> RiseStars\n"
      f"✨ Улучшение: <b>{'разрешено' if data.get('upgrade') else 'запрещено'}</b>\n"
      f"🕐 Старт: <b>{human}</b>\n\n"
      "⚠️ Это внутренние подарки RiseGram, не NFT сети TON",
      kb([btn("✅ Создать дроп","adm:gift:publish",style="success")],
         [btn("✖️ Отмена","adm:gifts:0",style="danger")]))

async def on_callback(call,state,parts):
    route=parts[1]
    if route=="gifts":
        await state.clear()
        try:page=int(parts[2])
        except (ValueError,IndexError):page=0
        return await screen(call,page)
    sub=parts[2] if len(parts)>2 else ""
    if sub=="pick":
        slug=parts[3] if len(parts)>3 else ""
        gift=await asyncio.to_thread(gifts.gift_info,slug)
        if not gift:return await call.answer("Подарок не найден",show_alert=True)
        await state.set_state("Adm:input")
        await state.set_data({"act":"gift_qty","gift_id":slug})
        return await show(call,f"🎁 <b>{esc(gift['title'])}</b>\n\n"
             "📦 Введи количество подарков в дропе (1–1 000 000)",
             kb([btn("Отмена","adm:gifts:0",style="danger")]))
    if sub=="upgrade":
        return await choose_upgrade(call,state,len(parts)>3 and parts[3]=="1")
    if sub=="when":
        if len(parts)>3 and parts[3]=="now":
            await state.update_data(starts_at=None)
            return await confirmation(call,state)
        await state.update_data(act="gift_time")
        await state.set_state("Adm:input")
        return await show(call,"🗓 Введи дату начала в формате <code>2026-11-22 20:00</code>\n"
            "Часовой пояс UTC+5 (Алматы/Петропавловск)",
            kb([btn("Отмена","adm:gifts:0",style="danger")]))
    if sub=="publish":
        data=await state.get_data()
        if not all(k in data for k in ("gift_id","qty","price","upgrade","starts_at")):
            return await show(call,"Сессия устарела",kb([btn("Каталог","adm:gifts:0")]))
        try:
            drop_id=await asyncio.to_thread(gifts.create_drop,data["gift_id"],
                 int(data["price"]),int(data["qty"]),data["starts_at"],data["upgrade"])
        except (ValueError,TypeError) as e:
            return await call.answer(str(e),show_alert=True)
        await db.log_admin_action(config.OWNER_ID,f"Создан дроп #{drop_id}: {data['gift_id']}")
        await state.clear()
        return await show(call,f"✅ <b>Дроп #{drop_id} создан</b>\n"
            "Покупки доступны в Mini App RiseGram и API клиента",
            kb([btn("📦 Дропы","adm:gift:list")],[btn("🎁 Каталог","adm:gifts:0")]))
    if sub=="list":
        await state.clear()
        return await my_drops(call)
    if sub=="toggle":
        try:drop_id=int(parts[3])
        except (ValueError,IndexError):return
        ok=await asyncio.to_thread(gifts.toggle_drop,drop_id)
        if ok:await db.log_admin_action(config.OWNER_ID,f"Переключён дроп #{drop_id}")
        return await my_drops(call)
    if sub=="refresh":
        try:
            timeout=aiohttp.ClientTimeout(total=12)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(gifts.CATALOG_SOURCE) as response:
                    response.raise_for_status()
                    rows=await response.json(content_type=None)
            if not isinstance(rows,list) or len(rows)<100:
                raise ValueError("Каталог не прошёл проверку")
            count=await asyncio.to_thread(gifts.seed_catalog,rows)
        except (aiohttp.ClientError,asyncio.TimeoutError,ValueError,TypeError) as e:
            return await show(call,f"⚠️ Не удалось обновить каталог: {esc(str(e))}",
                 kb([btn("Каталог","adm:gifts:0")]))
        await db.log_admin_action(config.OWNER_ID,f"Обновлён каталог Telegram NFT: {count}")
        await state.clear()
        return await screen(call,0)

async def on_input(message,state,data):
    act=data.get("act")
    text=(message.text or "").strip()
    async def retry(error):
        await message.answer("❌ "+esc(error),reply_markup=kb([btn("Отмена","adm:gifts:0")]))
    if act=="gift_qty":
        if not text.isdigit() or not 1<=int(text)<=1000000:
            return await retry("Количество от 1 до 1 000 000")
        await state.update_data(act="gift_price",qty=int(text))
        return await message.answer("⭐ Введи цену одного подарка в RiseStars (1–1 000 000 000)",
             reply_markup=kb([btn("Отмена","adm:gifts:0")]))
    if act=="gift_price":
        if not text.isdigit() or not 1<=int(text)<=1000000000:
            return await retry("Цена от 1 до 1 000 000 000 RiseStars")
        await state.update_data(act="gift_upgrade",price=int(text))
        return await message.answer("✨ Разрешить улучшение купленного подарка?",
           reply_markup=kb([btn("✅ Можно","adm:gift:upgrade:1",style="success"),
                            btn("❌ Нельзя","adm:gift:upgrade:0",style="danger")]))
    if act=="gift_time":
        try:
            local=datetime.strptime(text,"%Y-%m-%d %H:%M").replace(tzinfo=ALMATY)
            dt=local.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            if dt < gifts.utc_now():
                raise ValueError("Дата уже прошла")
        except ValueError:
            return await retry("Нужна будущая дата формата 2026-11-22 20:00")
        await state.update_data(starts_at=dt,act="gift_confirm")
        return await confirmation(message,state)
