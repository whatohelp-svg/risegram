import os
import tempfile
import unittest

import config
import database as db


class IdentityMigrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        handle, self.path = tempfile.mkstemp(prefix="risegram-test-", suffix=".db")
        os.close(handle)
        self.old_path = db.DB_PATH
        self.old_owner_id = config.OWNER_ID
        self.old_owner_telegram_id = config.OWNER_TELEGRAM_ID
        self.old_starter = config.STARTER_NFT_USERNAMES
        db.DB_PATH = self.path
        config.OWNER_ID = 1
        config.OWNER_TELEGRAM_ID = 987654321
        config.STARTER_NFT_USERNAMES = ("koyyzz", "owner", "rise")
        await db.init_db()

    async def asyncTearDown(self):
        db.DB_PATH = self.old_path
        config.OWNER_ID = self.old_owner_id
        config.OWNER_TELEGRAM_ID = self.old_owner_telegram_id
        config.STARTER_NFT_USERNAMES = self.old_starter
        try:
            os.unlink(self.path)
        except FileNotFoundError:
            pass

    async def test_telegram_is_only_a_delivery_link(self):
        owner = await db.get_by_telegram(config.OWNER_TELEGRAM_ID)
        self.assertIsNotNone(owner)
        self.assertEqual(owner["tg_id"], config.OWNER_ID)
        self.assertNotEqual(owner["tg_id"], config.OWNER_TELEGRAM_ID)
        self.assertTrue(owner["user_id"])
        self.assertEqual(await db.telegram_chat_id(owner["tg_id"]), config.OWNER_TELEGRAM_ID)

    async def test_new_bot_user_gets_internal_id_and_uuid(self):
        telegram_id = 123456789
        user = await db.get_or_create_user(telegram_id, "Test User", "testuser")
        self.assertNotEqual(user["tg_id"], telegram_id)
        self.assertTrue(user["user_id"])
        self.assertEqual(await db.telegram_chat_id(user["tg_id"]), telegram_id)
        self.assertEqual((await db.get_by_telegram(telegram_id))["user_id"], user["user_id"])

    async def test_starter_nfts_are_owner_inventory_assets(self):
        owner = await db.get_user(config.OWNER_ID)
        items = await db.list_nft(owner["tg_id"])
        names = {item["value"] for item in items if item["item_type"] == "username"}
        self.assertTrue({"koyyzz", "owner", "rise"}.issubset(names))
        self.assertFalse(config.is_reserved_username("owner"))
        self.assertFalse(config.is_reserved_username("rise"))


if __name__ == "__main__":
    unittest.main()
