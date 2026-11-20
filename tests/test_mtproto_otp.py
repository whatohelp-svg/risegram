import hashlib
import hmac
import json
import os
import tempfile
import time
import unittest
from unittest.mock import AsyncMock, patch

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

import api
import config
import database as db


class MtprotoOtpWebhookTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tempdir = tempfile.TemporaryDirectory(prefix="risegram-otp-")
        self.old_db_path = db.DB_PATH
        self.old_secret = config.MTPROTO_OTP_SECRET
        db.DB_PATH = os.path.join(self.tempdir.name, "test.db")
        config.MTPROTO_OTP_SECRET = "unit-test-secret"
        await db.init_db()

        self.telegram_chat_id = 910000001
        self.user = await db.get_or_create_user(self.telegram_chat_id, "OTP tester", "otp_test")
        await db.execute(
            "UPDATE users SET phone='+7771234567', phone_digits='7771234567' WHERE tg_id=?",
            (self.user["tg_id"],),
        )
        self.user = await db.get_user(self.user["tg_id"])

        app = web.Application()
        app["bot"] = object()
        app.router.add_post("/internal/mtproto/otp", api.h_mtproto_otp_delivery)
        self.client = TestClient(TestServer(app))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        db.DB_PATH = self.old_db_path
        config.MTPROTO_OTP_SECRET = self.old_secret
        self.tempdir.cleanup()

    def signed_headers(self, raw: bytes, timestamp: str | None = None):
        timestamp = timestamp or str(int(time.time()))
        signature = hmac.new(
            b"unit-test-secret",
            timestamp.encode("ascii") + b"." + raw,
            hashlib.sha256,
        ).hexdigest()
        return {
            "X-Telesrv-Timestamp": timestamp,
            "X-Telesrv-Signature": "sha256=" + signature,
            "Content-Type": "application/json",
        }

    @staticmethod
    def payload(delivery_id="otp_test_001"):
        return {
            "version": "1",
            "delivery_id": delivery_id,
            "purpose": "login_sms",
            "channel": "sms",
            "recipient": "+7 771 234 567",
            "code": "482913",
            "expires_at": "2030-01-01T00:00:00Z",
            "expires_in": 300,
            "locale": "ru",
        }

    async def test_signed_delivery_routes_to_linked_bot_and_is_idempotent(self):
        raw = json.dumps(self.payload(), separators=(",", ":")).encode()
        headers = self.signed_headers(raw)
        with patch("api.safe_send", new=AsyncMock(return_value=True)) as send:
            first = await self.client.post("/internal/mtproto/otp", data=raw, headers=headers)
            self.assertEqual(first.status, 200)
            self.assertTrue((await first.json())["accepted"])
            self.assertEqual(send.await_count, 1)
            self.assertEqual(send.await_args.args[1], self.user["tg_id"])
            self.assertEqual(await db.telegram_chat_id(send.await_args.args[1]), self.telegram_chat_id)
            self.assertIn("482913", send.await_args.args[2])

            duplicate = await self.client.post("/internal/mtproto/otp", data=raw, headers=headers)
            self.assertEqual(duplicate.status, 200)
            self.assertTrue((await duplicate.json())["accepted"])
            self.assertEqual(send.await_count, 1)

        row = await db.fetchone(
            "SELECT status FROM rg_otp_deliveries WHERE delivery_id='otp_test_001'"
        )
        self.assertEqual(row["status"], "sent")

    async def test_regular_login_delivery_passes_internal_user_id_to_safe_send(self):
        api._last_request.clear()
        api._last_request[db.digits_of(self.user["phone"])] = time.monotonic() - api.REQUEST_COOLDOWN - 1
        with patch("api.safe_send", new=AsyncMock(return_value=True)) as send:
            status, body = await api.core_request_code(object(), self.user["phone"])
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(send.await_args.args[1], self.user["tg_id"])
        self.assertEqual(await db.telegram_chat_id(send.await_args.args[1]), self.telegram_chat_id)
        self.assertIn("Код входа", send.await_args.args[2])

    async def test_mtproto_otp_uses_explicitly_shared_personal_telegram_phone(self):
        self.assertTrue(await db.link_telegram_phone(self.user["tg_id"], "+7771234567"))
        raw = json.dumps(self.payload("otp_personal_phone"), separators=(",", ":")).encode()
        with patch("api.safe_send", new=AsyncMock(return_value=True)) as send:
            response = await self.client.post(
                "/internal/mtproto/otp", data=raw, headers=self.signed_headers(raw)
            )
        self.assertEqual(response.status, 200)
        self.assertTrue((await response.json())["accepted"])
        self.assertEqual(send.await_args.args[1], self.user["tg_id"])
        self.assertIn("482913", send.await_args.args[2])

    async def test_personal_phone_cannot_be_claimed_by_two_bot_accounts(self):
        other = await db.get_or_create_user(910000002, "Other tester", "other_test")
        self.assertTrue(await db.link_telegram_phone(self.user["tg_id"], "+7771234567"))
        self.assertFalse(await db.link_telegram_phone(other["tg_id"], "+7771234567"))

    async def test_invalid_hmac_is_rejected_before_any_delivery(self):
        raw = json.dumps(self.payload(), separators=(",", ":")).encode()
        with patch("api.safe_send", new=AsyncMock(return_value=True)) as send:
            response = await self.client.post(
                "/internal/mtproto/otp",
                data=raw,
                headers={
                    "X-Telesrv-Timestamp": str(int(time.time())),
                    "X-Telesrv-Signature": "sha256=" + ("0" * 64),
                    "Content-Type": "application/json",
                },
            )
        self.assertEqual(response.status, 401)
        send.assert_not_awaited()

    async def test_unlinked_number_is_rejected_without_leaking_code(self):
        payload = self.payload("otp_unknown")
        payload["recipient"] = "+7 700 000 0000"
        raw = json.dumps(payload, separators=(",", ":")).encode()
        with patch("api.safe_send", new=AsyncMock(return_value=True)) as send:
            response = await self.client.post(
                "/internal/mtproto/otp", data=raw, headers=self.signed_headers(raw)
            )
        self.assertEqual(response.status, 404)
        send.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
