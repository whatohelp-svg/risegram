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
            response.raise_for_status = MagicMock()
            response.status = 200
            response.json.return_value = {"status": "completed"}
            await admin.grant_channel_username(message)
            payload = session.post.call_args.kwargs["json"]
            self.assertEqual(payload["owner_channel_id"], "123456")
            self.assertEqual(payload["username"], "giftname")
            self.assertEqual(payload["actor"], "risegram-admin-21")
            self.assertIn("выдан", message.answer.await_args.args[0])

    async def test_username_grants_use_resolved_risegram_peer(self):
        for command, endpoint in [
            ("/grantverify @group official", "/channels/set-verified"),
            ("/grantverify @group major", "/botverification/marks/grant"),
            ("/grantverify @group risegram", "/botverification/marks/grant"),
            ("/grantnft @group @second", "/collectible-usernames/mint"),
        ]:
            with self.subTest(command=command), patch.object(admin.config, "MTPROTO_ADMIN_API_URL", "https://example.invalid"), \
                 patch.object(admin.config, "MTPROTO_ADMIN_API_TOKEN", "test"), patch.object(admin, "ClientSession") as client:
                session = MagicMock()
                client.return_value.__aenter__.return_value = session
                read = session.get.return_value.__aenter__.return_value
                read.raise_for_status = MagicMock()
                read.json.side_effect = [
                    {"found": True, "channel_id": "123", "megagroup": True, "broadcast": False},
                    {"rows": [{"Enabled": True, "CompanyName": command.split()[-1], "BotID": "7"}]},
                ]
                write = session.post.return_value.__aenter__.return_value
                write.raise_for_status = MagicMock()
                write.json.return_value = {"status": "completed"}
                await admin.grant_channel_username(self.message(command))
                self.assertTrue(session.post.call_args.args[0].endswith(endpoint))
                payload = session.post.call_args.kwargs["json"]
                self.assertEqual(str(next(payload[k] for k in ("peer_id", "channel_id", "owner_channel_id") if k in payload)), "123")

    async def test_revoke_verification_on_group_without_deleting_chat(self):
        for kind, endpoint in [
            ("official", "/v1/channels/set-verified"),
            ("risegram", "/v1/botverification/marks/revoke"),
            ("major", "/v1/botverification/marks/revoke"),
        ]:
            with self.subTest(kind=kind), patch.object(admin.config, "MTPROTO_ADMIN_API_URL", "https://example.invalid"), \
                 patch.object(admin.config, "MTPROTO_ADMIN_API_TOKEN", "test"), \
                 patch.object(admin, "audit", new_callable=AsyncMock), \
                 patch.object(admin, "ClientSession") as client:
                session = MagicMock()
                client.return_value.__aenter__.return_value = session
                read = session.get.return_value.__aenter__.return_value
                read.raise_for_status = MagicMock()
                read.json.side_effect = [
                    {"found": True, "channel_id": "456", "megagroup": True, "broadcast": False},
                    {"rows": [{"Enabled": True, "CompanyName": kind, "BotID": "7"}]},
                ]
                write = session.post.return_value.__aenter__.return_value
                write.raise_for_status = MagicMock()
                write.json.return_value = {"status": "completed"}
                message = self.message(f"/revokeverify @group {kind}")
                await admin.grant_channel_username(message)
                self.assertTrue(session.post.call_args.args[0].endswith(endpoint))
                payload = session.post.call_args.kwargs["json"]
                self.assertNotIn("delete_account", payload)
                if kind == "official":
                    self.assertIs(payload["verified"], False)
                else:
                    self.assertEqual(payload["peer_type"], "channel")
                    self.assertEqual(payload["verifier_bot_id"], 7)
                    self.assertNotIn("description", payload)
                self.assertIn("снята", message.answer.await_args.args[0])

    async def test_group_verification_description_and_channel_grammar(self):
        for info, expected in [
            ({"megagroup": True, "broadcast": False}, "Группа верифицирована"),
            ({"megagroup": False, "broadcast": True}, "Канал верифицирован"),
        ]:
            with self.subTest(info=info), patch.object(admin.config, "MTPROTO_ADMIN_API_URL", "https://example.invalid"), \
                 patch.object(admin.config, "MTPROTO_ADMIN_API_TOKEN", "test"), \
                 patch.object(admin, "audit", new_callable=AsyncMock), \
                 patch.object(admin, "ClientSession") as client:
                session = MagicMock()
                client.return_value.__aenter__.return_value = session
                read = session.get.return_value.__aenter__.return_value
                read.raise_for_status = MagicMock()
                read.json.side_effect = [
                    {"found": True, "channel_id": "123", **info},
                    {"rows": [{"Enabled": True, "CompanyName": "RiseGram", "BotID": "17"}]},
                ]
                write = session.post.return_value.__aenter__.return_value
                write.raise_for_status = MagicMock()
                write.json.return_value = {"status": "completed"}
                await admin.grant_channel_username(self.message("/grantverify @group risegram"))
                payload = session.post.call_args.kwargs["json"]
                self.assertTrue(payload["description"].startswith(expected))
                self.assertIn("RiseGram", payload["description"])

    async def test_numeric_group_requires_type_before_granting_mark(self):
        with patch.object(admin.config, "MTPROTO_ADMIN_API_URL", "https://example.invalid"), \
             patch.object(admin.config, "MTPROTO_ADMIN_API_TOKEN", "test"), \
             patch.object(admin, "ClientSession") as client:
            session = MagicMock()
            client.return_value.__aenter__.return_value = session
            message = self.message("/grantverify -100123456 risegram")
            await admin.grant_channel_username(message)
            self.assertIn("group", message.answer.await_args.args[0])
            session.post.assert_not_called()

    async def test_basic_group_rejected_before_mint(self):
        with patch.object(admin, "ClientSession") as client:
            message = self.message("/grantnft -12345 @test")
            await admin.grant_channel_username(message)
            client.assert_not_called()
            message.answer.assert_awaited_once()
