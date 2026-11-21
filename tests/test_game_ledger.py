"""Integration test against a disposable PostgreSQL schema in a named test database."""
import asyncio
import os
import unittest
from unittest.mock import AsyncMock, patch
import uuid

import psycopg
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg import sql

import native_economy as ledger


@unittest.skipUnless(os.getenv("RISEGRAM_TEST_LEDGER_DSN"), "requires a test PostgreSQL database")
class GameLedgerTests(unittest.IsolatedAsyncioTestCase):
    async def test_stakes_losses_wins_and_duplicate_delivery(self):
        dsn = os.environ["RISEGRAM_TEST_LEDGER_DSN"]
        self.assertIn("test", conninfo_to_dict(dsn)["dbname"])
        schema = "game_test_" + uuid.uuid4().hex
        conn = await psycopg.AsyncConnection.connect(dsn, autocommit=True)
        async with conn:
            await conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
            try:
                scoped = make_conninfo(dsn, options="-csearch_path="+schema)
                with patch.dict(os.environ, RISEGRAM_LEDGER_DSN=scoped), patch.object(ledger, "account", AsyncMock(return_value=17)):
                    async with ledger.transaction() as tx:
                        for table in ("stars_balances", "stars_transactions", "ton_balances", "ton_transactions"):
                            await tx.execute(sql.SQL("CREATE TABLE {} (LIKE public.{} INCLUDING ALL)").format(
                                sql.Identifier(table), sql.Identifier(table)))
                        await tx.execute("INSERT INTO stars_balances(user_id,balance) VALUES(17,100)")
                        await tx.execute("INSERT INTO ton_balances(user_id,balance_nanoton) VALUES(17,2000000000)")
                    await ledger.setup()
                    user = {"tg_id": 11}
                    results = await asyncio.gather(*(ledger.start(user, "Crash", "stars", 30, "msg1") for _ in range(2)),
                                                   return_exceptions=True)
                    self.assertEqual(sum(isinstance(r, ledger.WalletError) for r in results), 1)
                    game = next(r for r in results if isinstance(r, int))
                    await ledger.settle(game, False, 0)
                    with self.assertRaises(ledger.WalletError):
                        await ledger.start(user, "Crash", "stars", 71, "msg2")
                    win = await ledger.start(user, "High-Low", "gram", 1.25, "msg3")
                    await asyncio.gather(*(ledger.settle(win, True, 2.50) for _ in range(2)))
                    async with ledger.transaction() as tx:
                        row = await (await tx.execute("SELECT balance FROM stars_balances")).fetchone()
                        self.assertEqual(row["balance"], 70)
                        row = await (await tx.execute("SELECT balance_nanoton FROM ton_balances")).fetchone()
                        self.assertEqual(row["balance_nanoton"], 3250000000)
                        rows = await (await tx.execute("SELECT amount_nanoton FROM ton_transactions ORDER BY id")).fetchall()
                        self.assertEqual([r["amount_nanoton"] for r in rows], [-1250000000, 2500000000])
            finally:
                await conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
