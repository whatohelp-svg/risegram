"""Regression tests for the 30-day recoverable deletion route.

Never issue live delete/freeze operations in tests.
"""
import os
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import admin
import account_recovery as recovery
import database as db


class AccountRemovalTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.enterContext(patch.object(db, "DB_PATH", os.path.join(self.temp.name, "test.db")))
        self.enterContext(patch.object(admin.config, "OWNER_TELEGRAM_ID", 123))
        await db.init_db()
        self.target = await db.get_or_create_user(81234567, "Example", None)
        self.state = AsyncMock()
        self.state.get_state.return_value = admin.Adm.delete_confirm.state
        self.state.get_data.return_value = {
            "key": "confirm", "native_id": 19,
            "local_id": self.target["tg_id"], "phone": self.target["phone_digits"],
            "actor": 123, "created": time.time(),
        }
        self.state.clear = AsyncMock()
        self.call = SimpleNamespace(
            from_user=SimpleNamespace(id=123), answer=AsyncMock()
        )
        self.render = self.enterContext(patch.object(admin, "show", new_callable=AsyncMock))
        self.enterContext(patch.object(admin, "audit", new_callable=AsyncMock))

    async def test_deletion_confirmation_only_schedules_and_keeps_identity(self):
        token = await db.create_session(self.target["tg_id"], native=True)
        schedule = self.enterContext(patch.object(
            admin.recovery, "schedule", new_callable=AsyncMock,
            return_value={"id": 3, "native_id": 19,
                          "due": "2026-12-24 12:00:00", "local_id": self.target["tg_id"]}
        ))
        await admin.confirm_account_deletion(self.call, self.state, "confirm")
        schedule.assert_awaited_once()
        self.assertEqual(schedule.await_args.kwargs["expected_native_id"], 19)
        self.assertIsNotNone(await db.get_session_user(token))
        self.assertIsNotNone(await db.get_by_telegram(81234567))

    async def test_expired_confirmation_cannot_delete(self):
        schedule = self.enterContext(patch.object(admin.recovery, "schedule", new_callable=AsyncMock))
        await admin.confirm_account_deletion(self.call, self.state, "expired")
        schedule.assert_not_awaited()
        self.assertIsNotNone(await db.get_user(self.target["tg_id"]))

    async def test_other_admin_cannot_confirm(self):
        schedule = self.enterContext(patch.object(admin.recovery, "schedule", new_callable=AsyncMock))
        self.call.from_user.id = 456
        await admin.confirm_account_deletion(self.call, self.state, "confirm")
        schedule.assert_not_awaited()

    async def test_restore_uses_original_peer_and_phone(self):
        self.state.get_state.return_value = admin.Adm.restore_confirm.state
        self.state.get_data.return_value = {
            "key": "restore", "phone": self.target["phone_digits"],
            "native_id": 19, "record_id": 3,
            "actor": 123, "created": time.time(),
        }
        restored = self.enterContext(patch.object(
            admin.recovery, "restore", new_callable=AsyncMock,
            return_value={"native_id": 19, "local_id": self.target["tg_id"],
                          "admin_cancelled": True, "client_cancelled": False}
        ))
        await admin.confirm_account_restoration(self.call, self.state, "restore")
        restored.assert_awaited_once_with(
            self.target["phone_digits"], 123, expected_record_id=3, expected_native_id=19
        )
        self.assertIsNotNone(await db.get_user(self.target["tg_id"]))

    async def test_only_pending_local_receipts_block_bot(self):
        self.assertFalse(await recovery.pending_for_local(self.target["tg_id"]))
        await db.execute("""
            INSERT INTO admin_account_deletions(
                phone_digits,local_id,native_id,status,
                requested_at,execute_at,actor_telegram_id,command_key)
            VALUES(?,?,19,'active','2026-11-24','2026-12-24',123,'fixture')
        """, (self.target["phone_digits"],self.target["tg_id"]))
        self.assertTrue(await recovery.pending_for_local(self.target["tg_id"]))
        await db.execute("UPDATE admin_account_deletions SET status='cancelled' WHERE command_key='fixture'")
        self.assertFalse(await recovery.pending_for_local(self.target["tg_id"]))

    async def test_cannot_restore_account_with_no_pending_request(self):
        fake = {"local": {"tg_id": self.target["tg_id"]},
                "native": {"id": 19, "phone": self.target["phone_digits"], "is_bot": False},
                "pending": None, "receipt": None, "phone": self.target["phone_digits"]}
        with patch.object(recovery, "preview", new_callable=AsyncMock, return_value=fake):
            with self.assertRaises(recovery.RecoveryError):
                await recovery.restore(self.target["phone_digits"], 123)
