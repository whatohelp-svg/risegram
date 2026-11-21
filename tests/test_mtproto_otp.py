import asyncio
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
            "UPDATE users SET phone='+888 0123 4567', phone_digits='88801234567' WHERE tg_id=?",
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
            "recipient": "+888 0123 4567",
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

    async def test_concurrent_http_redemption_persists_one_session(self):
        code = await db.issue_auth_code(self.user["tg_id"])
        responses = await asyncio.gather(
            api.core_verify(self.user["phone"], code),
            api.core_verify(self.user["phone"], code),
        )
        self.assertEqual(sorted(status for status, _ in responses), [200, 401])
        token = next(body["token"] for status, body in responses if status == 200)
        restored = await db.get_session_user(token)
        self.assertEqual(restored["tg_id"], self.user["tg_id"])
        self.assertEqual(await db.scalar("SELECT COUNT(*) FROM sessions"), 1)

    async def test_failed_session_write_leaves_the_code_redeemable(self):
        code = await db.issue_auth_code(self.user["tg_id"])
        with patch("database._insert_session", side_effect=RuntimeError("session write failed")):
            with self.assertRaisesRegex(RuntimeError, "session write failed"):
                await api.core_verify(self.user["phone"], code)
        status, _ = await api.core_verify(self.user["phone"], code)
        self.assertEqual(status, 200)

    async def test_concurrent_webhook_delivery_sends_once(self):
        raw = json.dumps(self.payload("otp_parallel")).encode()
        headers = self.signed_headers(raw)
        started = asyncio.Event()
        release = asyncio.Event()

        async def deliver(*args):
            started.set()
            await release.wait()
            return True

        with patch("api.safe_send", side_effect=deliver) as send:
            first = asyncio.create_task(self.client.post("/internal/mtproto/otp", data=raw, headers=headers))
            await asyncio.wait_for(started.wait(), timeout=5)
            try:
                duplicate = await self.client.post("/internal/mtproto/otp", data=raw, headers=headers)
                self.assertEqual(duplicate.status, 409)
            finally:
                release.set()
                response = await first
            self.assertEqual(response.status, 200)
            send.assert_awaited_once()

    async def test_personal_number_is_not_an_otp_alias(self):
        payload = self.payload("otp_personal_phone")
        payload["recipient"] = "+7 771 234 567"
        raw = json.dumps(payload, separators=(",", ":")).encode()
        with patch("api.safe_send", new=AsyncMock(return_value=True)) as send:
            response = await self.client.post(
                "/internal/mtproto/otp", data=raw, headers=self.signed_headers(raw)
            )
        self.assertEqual(response.status, 404)
        send.assert_not_awaited()

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
