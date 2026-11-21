"""Tests for synchronized, prepaid Telegram NFT gift drops."""
from __future__ import annotations

import os
import tempfile
import unittest

import database as db


class GiftCatalogDropTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_db_path = db.DB_PATH
        db.DB_PATH = os.path.join(self.temp_dir.name, "risegram-test.sqlite3")
        async with db.connect() as conn:
            await conn.executescript(
                """
                CREATE TABLE users (
                    tg_id INTEGER PRIMARY KEY,
                    risestars REAL NOT NULL DEFAULT 0,
                    banned INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE gift_drops (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    telegram_gift_id TEXT NOT NULL,
                    gift_name TEXT NOT NULL,
                    emoji TEXT NOT NULL DEFAULT '🎁',
                    telegram_stars INTEGER NOT NULL DEFAULT 0,
                    upgrade_stars INTEGER NOT NULL DEFAULT 0,
                    pay_for_upgrade INTEGER NOT NULL DEFAULT 0,
                    price_risestars REAL NOT NULL,
                    stock_total INTEGER NOT NULL,
                    stock_remaining INTEGER NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT
                );
                CREATE TABLE user_gifts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tg_id INTEGER NOT NULL,
                    drop_id INTEGER NOT NULL,
                    telegram_gift_id TEXT NOT NULL,
                    gift_name TEXT NOT NULL,
                    emoji TEXT NOT NULL DEFAULT '🎁',
                    serial_no INTEGER NOT NULL,
                    price_paid REAL NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    received_at TEXT
                );
                """
            )
            await conn.commit()

    async def asyncTearDown(self):
        db.DB_PATH = self.original_db_path
        self.temp_dir.cleanup()

    async def test_sync_is_idempotent_and_purchase_pays_for_nft_upgrade(self):
        catalog = [{
            "id": "telegram-gift-nft-1",
            "name": "Test Collectible",
            "emoji": "🎁",
            "stars": 10,
            "upgrade_stars": 20,
            "can_upgrade": True,
            "personal_remaining_count": 4,
            "remaining_count": 50,
            "total_count": 100,
        }]

        first = await db.sync_gift_catalog_drops(catalog)
        second = await db.sync_gift_catalog_drops(catalog)
        drops = await db.list_gift_drops()

        self.assertEqual(first["created"], 1)
        self.assertEqual(second["created"], 0)
        self.assertEqual(second["already_live"], 1)
        self.assertEqual(len(drops), 1)
        self.assertEqual(drops[0]["stock_total"], 4)
        self.assertEqual(drops[0]["stock_remaining"], 4)
        self.assertEqual(drops[0]["price_risestars"], 3000)
        self.assertEqual(drops[0]["pay_for_upgrade"], 1)

        await db.execute(
            "INSERT INTO users(tg_id,risestars,banned) VALUES(?,?,0)",
            (42, 5000),
        )
        ok, message, reserved = await db.reserve_gift_drop(42, drops[0]["id"])
        self.assertTrue(ok, message)
        self.assertTrue(reserved["pay_for_upgrade"])
        self.assertEqual(reserved["upgrade_stars"], 20)

        user = await db.fetchone("SELECT risestars FROM users WHERE tg_id=?", (42,))
        drop = await db.fetchone("SELECT stock_remaining FROM gift_drops WHERE id=?", (drops[0]["id"],))
        self.assertEqual(user["risestars"], 2000)
        self.assertEqual(drop["stock_remaining"], 3)

        self.assertTrue(await db.rollback_gift_purchase(reserved["instance_id"]))
        user = await db.fetchone("SELECT risestars FROM users WHERE tg_id=?", (42,))
        drop = await db.fetchone("SELECT stock_remaining FROM gift_drops WHERE id=?", (drops[0]["id"],))
        self.assertEqual(user["risestars"], 5000)
        self.assertEqual(drop["stock_remaining"], 4)

        ok, message, reserved = await db.reserve_gift_drop(42, drops[0]["id"])
        self.assertTrue(ok, message)
        self.assertTrue(await db.confirm_gift_purchase(reserved["instance_id"]))
        inventory = await db.list_user_gifts(42)
        self.assertEqual(len(inventory), 1)
        self.assertEqual(inventory[0]["serial_no"], 2)

    async def test_legacy_drop_is_upgraded_and_price_floor_is_enforced(self):
        drop_id = await db.create_gift_drop(
            "telegram-gift-nft-2",
            "Legacy Gift",
            "💎",
            1,
            5,
            10,
        )
        result = await db.sync_gift_catalog_drops([{
            "id": "telegram-gift-nft-2",
            "name": "Legacy Gift",
            "emoji": "💎",
            "stars": 10,
            "upgrade_stars": 20,
            "can_upgrade": True,
            "remaining_count": 8,
        }])
        drop = await db.fetchone("SELECT * FROM gift_drops WHERE id=?", (drop_id,))

        self.assertEqual(result["upgraded"], 1)
        self.assertEqual(drop["telegram_stars"], 10)
        self.assertEqual(drop["upgrade_stars"], 20)
        self.assertEqual(drop["pay_for_upgrade"], 1)
        self.assertEqual(drop["price_risestars"], 3000)
        self.assertEqual(drop["stock_remaining"], 10)


if __name__ == "__main__":
    unittest.main()
