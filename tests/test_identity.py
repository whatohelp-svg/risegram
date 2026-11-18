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
        self.assertRegex(str(owner["rise_id"]), r"^[1-9][0-9]{7}$")
        self.assertNotEqual(owner["tg_id"], config.OWNER_TELEGRAM_ID)
        self.assertTrue(owner["user_id"])
        self.assertEqual(await db.telegram_chat_id(owner["tg_id"]), config.OWNER_TELEGRAM_ID)

    async def test_new_bot_user_gets_internal_id_and_uuid(self):
        telegram_id = 123456789
        user = await db.get_or_create_user(telegram_id, "Test User", "testuser")
        self.assertNotEqual(user["tg_id"], telegram_id)
        self.assertTrue(user["user_id"])
        self.assertRegex(str(user["rise_id"]), r"^[1-9][0-9]{7}$")
        self.assertEqual(await db.telegram_chat_id(user["tg_id"]), telegram_id)
        self.assertEqual((await db.get_by_telegram(telegram_id))["user_id"], user["user_id"])

    async def test_legacy_telegram_keys_are_remapped_with_inventory(self):
        handle, legacy_path = tempfile.mkstemp(prefix="risegram-legacy-", suffix=".db")
        os.close(handle)
        try:
            import aiosqlite
            async with aiosqlite.connect(legacy_path) as legacy:
                await legacy.execute(
                    "CREATE TABLE users (tg_id INTEGER PRIMARY KEY, name TEXT, username TEXT, phone TEXT, "
                    "phone_digits TEXT, rise_id INTEGER, created_at TEXT)"
                )
                await legacy.execute(
                    "CREATE TABLE user_nft_inventory (id INTEGER PRIMARY KEY AUTOINCREMENT, tg_id INTEGER NOT NULL, "
                    "item_type TEXT NOT NULL DEFAULT 'username', value TEXT, price_stars INTEGER DEFAULT 0, created_at TEXT)"
                )
                await legacy.execute(
                    "INSERT INTO users (tg_id,name,username,phone,phone_digits,rise_id,created_at) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (config.OWNER_TELEGRAM_ID, "Old Owner", "oldowner", "+70000000000", "70000000000", 123456789, "2026-01-01"),
                )
                await legacy.execute(
                    "INSERT INTO user_nft_inventory (tg_id,item_type,value,price_stars,created_at) "
                    "VALUES (?,'username','legacyasset',0,'2026-01-01')",
                    (config.OWNER_TELEGRAM_ID,),
                )
                await legacy.commit()
            db.DB_PATH = legacy_path
            await db.init_db()
            owner = await db.get_user(config.OWNER_ID)
            self.assertIsNotNone(owner)
            self.assertNotEqual(owner["tg_id"], config.OWNER_TELEGRAM_ID)
            self.assertEqual(await db.telegram_chat_id(owner["tg_id"]), config.OWNER_TELEGRAM_ID)
            items = await db.list_nft(owner["tg_id"])
            self.assertIn("legacyasset", {x["value"] for x in items})
        finally:
            db.DB_PATH = self.path
            try:
                os.unlink(legacy_path)
            except FileNotFoundError:
                pass

    async def test_multiple_nft_usernames_can_be_active_at_once(self):
        owner = await db.get_user(config.OWNER_ID)
        uid = owner["tg_id"]
        self.assertTrue(await db.add_nft(uid, "username", "aliasone", 0))
        self.assertTrue(await db.add_nft(uid, "username", "aliastwo", 0))
        first = await db.fetchone("SELECT id FROM user_nft_inventory WHERE tg_id=? AND value='aliasone'", (uid,))
        second = await db.fetchone("SELECT id FROM user_nft_inventory WHERE tg_id=? AND value='aliastwo'", (uid,))
        self.assertTrue((await db.set_nft_username_active(uid, first["id"], True))[0])
        self.assertTrue((await db.set_nft_username_active(uid, second["id"], True))[0])
        active = await db.fetchall(
            "SELECT value FROM user_nft_inventory WHERE tg_id=? AND item_type='username' AND is_active=1",
            (uid,),
        )
        self.assertEqual({x["value"] for x in active}, {"aliasone", "aliastwo"})
        self.assertTrue((await db.set_nft_username_active(uid, first["id"], False))[0])
        active = await db.fetchall(
            "SELECT value FROM user_nft_inventory WHERE tg_id=? AND item_type='username' AND is_active=1",
            (uid,),
        )
        self.assertEqual({x["value"] for x in active}, {"aliastwo"})

    async def test_starter_nfts_are_owner_inventory_assets(self):
        owner = await db.get_user(config.OWNER_ID)
        items = await db.list_nft(owner["tg_id"])
        names = {item["value"] for item in items if item["item_type"] == "username"}
        self.assertTrue({"koyyzz", "owner", "rise"}.issubset(names))
        self.assertFalse(config.is_reserved_username("owner"))
        self.assertFalse(config.is_reserved_username("rise"))


if __name__ == "__main__":
    unittest.main()
