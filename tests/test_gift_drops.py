"""Regression tests for RiseGram gift drops: no double billing or overselling."""
import os
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor

import config
import gift_drops as gifts

class GiftDropsTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.old_path=config.DB_PATH
        config.DB_PATH=os.path.join(self.tmp.name,"risegram.sqlite3")
        with sqlite3.connect(config.DB_PATH) as conn:
            conn.execute("""CREATE TABLE users(
                tg_id INTEGER PRIMARY KEY, risestars REAL NOT NULL,
                banned INTEGER NOT NULL DEFAULT 0)""")
            conn.execute("INSERT INTO users(tg_id,risestars) VALUES(1,1000),(2,1000)")
        gifts.setup()

    def tearDown(self):
        config.DB_PATH=self.old_path
        self.tmp.cleanup()

    def balance(self,user=1):
        with sqlite3.connect(config.DB_PATH) as conn:
            return conn.execute("SELECT risestars FROM users WHERE tg_id=?",(user,)).fetchone()[0]

    def test_catalog_seed(self):
        rows,count=gifts.catalog()
        self.assertGreaterEqual(count,123)
        self.assertLessEqual(len(rows),50)
        self.assertTrue(gifts.gift_info(rows[0]["gift_id"]))

    def test_admin_drop_pagination_and_publisher_all(self):
        for i in range(29):
            gifts.create_drop("plushpepe", i + 1, 1)
        self.assertEqual(gifts.count_admin_drops(), 29)
        page1 = gifts.list_admin_drops(20, 0)
        page2 = gifts.list_admin_drops(20, 20)
        self.assertEqual(len(page1), 20)
        self.assertEqual(len(page2), 9)
        self.assertEqual(page1[0]["id"], 29)
        self.assertEqual(page2[-1]["id"], 1)
        self.assertEqual(len(gifts.list_admin_drops(-1)), 29)

    def test_atomic_idempotent_purchase(self):
        drop=gifts.create_drop("plushpepe",100,2,can_upgrade=True)
        one=gifts.purchase(1,drop,"purchase_000001")
        self.assertEqual(one,gifts.purchase(1,drop,"purchase_000001"))
        self.assertEqual(self.balance(),900)
        self.assertEqual(len(gifts.inventory(1)),1)
        self.assertTrue(gifts.upgrade(1,one))
        self.assertFalse(gifts.upgrade(1,one))
        self.assertTrue(gifts.inventory(1)[0]["upgraded"])

    def test_stock_never_oversells(self):
        drop=gifts.create_drop("plushpepe",25,2)
        def run(i):
            try:return gifts.purchase(1,drop,f"unique_purchase_{i:06}")
            except ValueError:return None
        with ThreadPoolExecutor(max_workers=8) as pool:
            results=list(pool.map(run,range(8)))
        self.assertEqual(len([x for x in results if x]),2)
        self.assertEqual(self.balance(),950)
        self.assertEqual(len(gifts.inventory(1)),2)

    def test_no_charge_on_errors(self):
        drop=gifts.create_drop("plushpepe",900,1,can_upgrade=False)
        with self.assertRaises(ValueError):
            gifts.purchase(999,drop,"missing_user_123")
        self.assertEqual(self.balance(),1000)
        item=gifts.purchase(1,drop,"valid_purchase_1")
        self.assertFalse(gifts.upgrade(1,item))
        self.assertEqual(self.balance(),100)
        with self.assertRaises(ValueError):
            gifts.purchase(1,drop,"new_purchase_abc")
        self.assertEqual(self.balance(),100)
        with self.assertRaises(ValueError):
            gifts.purchase(2,drop,"valid_purchase_1")

    def test_future_drop_not_for_sale(self):
        drop=gifts.create_drop("plushpepe",100,2,starts_at="2099-01-01 00:00:00")
        self.assertFalse(any(x["id"]==drop for x in gifts.list_live_drops()))
        with self.assertRaises(ValueError):
            gifts.purchase(1,drop,"future_purchase_1")
        self.assertEqual(self.balance(),1000)

if __name__=="__main__":
    unittest.main()
