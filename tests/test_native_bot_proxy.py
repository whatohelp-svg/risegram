import json
import unittest
from unittest import mock

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

import native_bot_proxy


class NativeBotGatewayTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        async def fake_method(request):
            payload = await request.read()
            return web.json_response({
                "ok": True,
                "result": {
                    "path": request.path,
                    "query": dict(request.query),
                    "content_type": request.content_type,
                    "payload": payload.decode("utf-8", errors="replace"),
                },
            })

        async def fake_file(request):
            return web.Response(body=b"native-file-bytes", content_type="application/octet-stream")

        upstream = web.Application()
        upstream.add_routes([
            web.get("/bot{token}/{method}", fake_method),
            web.post("/bot{token}/{method}", fake_method),
            web.get("/file/bot{token}/{file_path:.*}", fake_file),
        ])
        self.upstream = TestServer(upstream)
        await self.upstream.start_server()
        self.patch = mock.patch.object(native_bot_proxy, "_NATIVE_BASE",
                                       str(self.upstream.make_url("")).rstrip("/"))
        self.patch.start()

        app = web.Application(client_max_size=27 * 1024 * 1024)
        app.on_startup.append(native_bot_proxy.install)
        app.on_cleanup.append(native_bot_proxy.cleanup)
        app.add_routes(native_bot_proxy.routes())
        self.client = TestClient(TestServer(app))
        await self.client.start_server()
        self.token = "123456789:" + "A" * 32

    async def asyncTearDown(self):
        await self.client.close()
        self.patch.stop()
        await self.upstream.close()

    async def test_get_me_reaches_real_bot_gateway(self):
        response = await self.client.get(
            "/bot" + self.token + "/getMe", params={"a": "b"},
        )
        self.assertEqual(response.status, 200)
        result = (await response.json())["result"]
        self.assertEqual(result["path"], "/bot" + self.token + "/getMe")
        self.assertEqual(result["query"], {"a": "b"})

    async def test_post_json_and_form_content_types_preserved(self):
        for name, kw in (
            ("json", {"json": {"text": "Привет"}}),
            ("form", {"data": {"text": "hello"}}),
        ):
            with self.subTest(name=name):
                response = await self.client.post(
                    "/bot" + self.token + "/sendMessage", **kw,
                )
                self.assertEqual(response.status, 200)
                result = (await response.json())["result"]
                self.assertIn("hello" if name == "form" else "Привет",
                              result["payload"] if name == "form" else json.loads(result["payload"])["text"])

    async def test_file_download_and_bad_tokens(self):
        r = await self.client.get(
            "/file/bot" + self.token + "/photos/photo_01.jpg",
        )
        self.assertEqual(r.status, 200)
        self.assertEqual(await r.read(), b"native-file-bytes")

        invalid = await self.client.get("/botbad/getMe")
        self.assertEqual(invalid.status, 401)
        invalid_file = await self.client.get(
            "/file/bot" + self.token + "/../secrets",
        )
        self.assertNotEqual(invalid_file.status, 200)

    async def test_proxy_failure_is_503_not_fake_bot_success(self):
        await self.upstream.close()
        r = await self.client.get("/bot" + self.token + "/getMe")
        self.assertEqual(r.status, 503)
        self.assertEqual((await r.json())["description"], "NATIVE_BOT_API_UNAVAILABLE")


if __name__ == "__main__":
    unittest.main()
