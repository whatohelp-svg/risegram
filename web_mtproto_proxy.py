"""Opt-in binary WebSocket bridge to the existing MTProto service.
Disabled by default. Do not enable until the MTProto origin allowlist is configured.
"""
import asyncio
import os
from aiohttp import ClientSession, ClientTimeout, WSMsgType, web

ORIGIN = "https://whatohelp-svg.github.io"
UPSTREAM = "http://risegram-mtproto.railway.internal:2398/apiws"
MAX_CONNECTIONS = 64
MAX_MESSAGE = 2**22

async def handle(request):
    if os.environ.get("RISEGRAM_WEB_ENABLED") != "1":
        raise web.HTTPServiceUnavailable(text="Web beta disabled")
    if request.headers.get("Origin") != ORIGIN:
        raise web.HTTPForbidden()
    ws = web.WebSocketResponse(protocols=("binary",), max_msg_size=MAX_MESSAGE, heartbeat=30)
    if not ws.can_prepare(request).ok:
        raise web.HTTPBadRequest(text="WebSocket upgrade required")
    slots = request.app["risegram_web_slots"]
    if slots.locked():
        raise web.HTTPServiceUnavailable()
    async with slots:
        async with ClientSession(timeout=ClientTimeout(total=None, sock_connect=10)) as session:
            try:
                async with session.ws_connect(UPSTREAM, protocols=("binary",), origin=ORIGIN, max_msg_size=MAX_MESSAGE, heartbeat=30) as upstream:
                    await ws.prepare(request)
                    async def relay(src, dst):
                        async for msg in src:
                            if msg.type == WSMsgType.BINARY:
                                await dst.send_bytes(msg.data)
                            elif msg.type == WSMsgType.TEXT:
                                await dst.close(code=1003)
                                break
                            else:
                                break
                    tasks = [asyncio.create_task(relay(ws, upstream)), asyncio.create_task(relay(upstream, ws))]
                    try:
                        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                    finally:
                        for task in tasks:
                            task.cancel()
                        await asyncio.gather(*tasks, return_exceptions=True)
                        await ws.close()
                    return ws
            except (OSError, asyncio.TimeoutError) as exc:
                if ws.prepared:
                    await ws.close(code=1011)
                    return ws
                raise web.HTTPBadGateway(text="MTProto unavailable") from exc

def install(app):
    app["risegram_web_slots"] = asyncio.Semaphore(MAX_CONNECTIONS)
    app.router.add_get("/apiws", handle)
