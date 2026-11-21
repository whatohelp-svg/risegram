import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import admin


class ChannelGrantTests(unittest.IsolatedAsyncioTestCase):
    def message(self, text):
        return SimpleNamespace(text=text, chat=SimpleNamespace(id=21),
                               from_user=SimpleNamespace(id=21), message_id=17,
                               date=datetime.now(timezone.utc), answer=AsyncMock())

    async def test_grant_normalizes_channel_and_records_admin(self):
        message = self.message("/grantnft -100123456 @GiftName")
        with patch.object(admin.config, "MTPROTO_ADMIN_API_URL", "https://example.invalid"), \
             patch.object(admin.config, "MTPROTO_ADMIN_API_TOKEN", "test"), \
             patch.object(admin, "ClientSession") as client:
            session = MagicMock()
            client.return_value.__aenter__.return_value = session
            response = session.post.return_value.__aenter__.return_value
            response.status = 200
            response.json.return_value = {"status": "completed"}
            await admin.grant_channel_username(message)
            payload = session.post.call_args.kwargs["json"]
            self.assertEqual(payload["owner_channel_id"], "123456")
            self.assertEqual(payload["username"], "giftname")
            self.assertEqual(payload["actor"], "risegram-admin-21")
            self.assertIn("выдан", message.answer.await_args.args[0])

    async def test_basic_group_rejected_before_mint(self):
        with patch.object(admin, "ClientSession") as client:
            message = self.message("/grantnft -12345 @test")
            await admin.grant_channel_username(message)
            client.assert_not_called()
            message.answer.assert_awaited_once()
