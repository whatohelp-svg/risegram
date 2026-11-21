import json
import unittest
from unittest.mock import AsyncMock, patch

from aiohttp import web
from aiohttp.test_utils import TestServer, make_mocked_request

import api


class NativeWalletTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.wallet = {"stars": "1025", "gram_nano": "10000000000"}
        self.calls = []
        self.account = {"phone": "+70000000001", "rise_id": 12345678, "risestars": 500.70, "gram_balance": 0}

        async def ledger(request):
            self.calls.append(await request.json())
            return web.json_response({"found": True, **self.wallet})

        service = web.Application()
        service.router.add_post("/v1/accounts/client-balances", ledger)
        service.router.add_post("/v1/accounts/exchange-gram", ledger)
        server = TestServer(service)
        await server.start_server()
        self.addAsyncCleanup(server.close)
        self.enterContext(patch.object(api.config, "MTPROTO_ADMIN_API_URL", str(server.make_url("/")).rstrip("/")))
        self.enterContext(patch.object(api.config, "MTPROTO_ADMIN_API_TOKEN", "mock"))
        self.enterContext(patch.object(api, "_current_user", AsyncMock(return_value=self.account)))

    async def test_bot_and_self_profile_use_native_values(self):
        self.assertEqual(await api.client_wallet_line(self.account), "⭐ 1 025 RiseStars · 💎 10 GRAM")
        self.wallet.update(stars="1024", gram_nano="10001000000")
        profile = await api.self_profile(self.account)
        self.assertEqual((profile["risestars"], profile["gram_balance"]), (1024, 10.001))
        self.assertEqual(len(self.calls), 2)

    async def test_native_failure_never_shows_local_balance(self):
        with patch.object(api, "native_wallet", side_effect=TimeoutError):
            self.assertIsNone((await api.self_profile(self.account))["risestars"])
            self.assertIn("недоступен", await api.client_wallet_line(self.account))

    async def test_miniapp_and_browser_cannot_exchange(self):
        for headers in ({"Authorization": "Bearer web-token"},
                        {"Authorization": "Bearer client.token", "Origin": "https://example.org"}):
            req = make_mocked_request("POST", "/api/exchange", headers=headers)
            self.assertIn((await api.h_exchange(req)).status, (403, 410))
        self.assertEqual(self.calls, [])

    async def test_exchange_one_hundredth_of_gram(self):
        req = make_mocked_request("POST", "/api/exchange", headers={"Authorization": "Bearer client.mock"})
        with patch.object(api, "_json", AsyncMock(return_value={"direction":"gram_to_stars", "amount":"0.01"})):
            res = await api.h_exchange(req)
        self.assertEqual(res.status, 200)
        self.assertEqual(self.calls[0]["stars_delta"], 1)
        self.assertEqual(json.loads(res.text)["user"]["risestars"], 1025)
