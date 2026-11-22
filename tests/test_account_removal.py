import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import admin
import database as db


class AccountRemovalTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.enterContext(patch.object(db, "DB_PATH", os.path.join(self.temp.name, "test.db")))
        await db.init_db()
        self.target = await db.get_or_create_user(81234567, "Example", None)
        self.state = AsyncMock()
        self.state.get_state.return_value = admin.Adm.delete_confirm.state
        self.state.get_data.return_value = dict(key="confirm", native_id=19,
                                                local_id=self.target["tg_id"], phone=self.target["phone_digits"])
        self.call = SimpleNamespace(from_user=SimpleNamespace(id=123), answer=AsyncMock())
        self.native = self.enterContext(patch.object(admin, "native_account_request", new_callable=AsyncMock))
        self.enterContext(patch.object(admin, "show", new_callable=AsyncMock))
        self.enterContext(patch.object(admin, "audit", new_callable=AsyncMock))

    async def test_confirmation_removes_test_identity_and_sessions(self):
        token = await db.create_session(self.target["tg_id"], native=True)
        self.native.side_effect = [{"found": True, "user_id": 19}, {"status": "completed"}]
        await admin.confirm_account_deletion(self.call, self.state, "confirm")
        self.assertEqual(self.native.await_args.args[0], "delete")
        self.assertEqual(self.native.await_args.args[1]["user_id"], 19)
        self.assertIsNone(await db.get_session_user(token))
        self.assertIsNone(await db.get_by_telegram(81234567))

    async def test_reassigned_number_requires_new_confirmation(self):
        self.native.return_value = {"found": True, "user_id": 20}
        await admin.confirm_account_deletion(self.call, self.state, "confirm")
        self.native.assert_awaited_once()
        self.assertIsNotNone(await db.get_user(self.target["tg_id"]))

    async def test_unconfirmed_native_deletion_preserves_local_identity(self):
        self.native.side_effect = [{"found": True, "user_id": 19}, {"status": "failed"}]
        await admin.confirm_account_deletion(self.call, self.state, "confirm")
        self.assertIsNotNone(await db.get_by_telegram(81234567))

    async def test_stale_button_cannot_delete(self):
        await admin.confirm_account_deletion(self.call, self.state, "expired")
        self.native.assert_not_awaited()
