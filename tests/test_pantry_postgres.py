"""Structured pantry on Postgres/Supabase with RLS (#144 phase 3, point 8)."""

import os
import unittest
from decimal import Decimal
from uuid import uuid4

from typing import Any, cast

from sabor.core.config import load_settings
from sabor.core.registry import Registry

try:
    import psycopg
    from psycopg import sql
except ImportError:  # the optional extra is not installed
    psycopg = cast(Any, None)
    sql = cast(Any, None)

DSN = os.environ.get("SABOR_TEST_DATABASE_URL", "")
PG_ENV = {
    "SABOR_STORE": "postgres",
    "SABOR_DATABASE_URL": "postgresql://u@localhost/db",
    "SABOR_TOOLSETS": "core,pantry",
}


class PostgresPantryReadinessTests(unittest.TestCase):
    """No database needed: readiness is decided from the settings."""

    def test_the_pantry_toolset_is_ready_with_postgres(self):
        registry = Registry(
            load_settings(PG_ENV), store_factory=lambda settings: cast(Any, None)
        )
        self.assertIn("pantry", registry.enabled_toolsets)

    def test_without_a_postgres_recipe_store_there_is_no_pantry(self):
        from sabor.pantry.store import pantry_store_for

        self.assertIsNone(pantry_store_for(load_settings(PG_ENV)))

    def test_without_a_postgres_recipe_store_estimates_see_no_pantry(self):
        from types import SimpleNamespace
        from sabor.pricing.pantry import load_stock

        context = SimpleNamespace(settings=load_settings(PG_ENV), store=None)
        self.assertEqual(load_stock(context), [])

@unittest.skipUnless(DSN and psycopg, "requires SABOR_TEST_DATABASE_URL and psycopg")
class PostgresPantryTests(unittest.TestCase):
    def setUp(self):
        from sabor.pantry.store import pantry_store_for
        from sabor.store.postgres import PostgresRecipeStore

        self.schema = "test_" + uuid4().hex
        self.recipes = PostgresRecipeStore(DSN, schema=self.schema)
        self.addCleanup(self.drop)
        self.recipes.ensure_ready()
        store = pantry_store_for(load_settings(PG_ENV), recipe_store=self.recipes)
        assert store is not None
        self.store = store

    def drop(self):
        self.recipes.close()
        with psycopg.connect(DSN, autocommit=True) as connection:
            connection.execute(
                sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(
                    sql.Identifier(self.schema)
                )
            )

    def query(self, statement, args=()):
        with psycopg.connect(DSN, autocommit=True) as connection:
            return connection.execute(statement, args).fetchall()

    def test_migration_4_creates_the_tables_with_row_level_security(self):
        versions = [
            r[0]
            for r in self.query(
                sql.SQL("SELECT version FROM {}.schema_version ORDER BY 1").format(
                    sql.Identifier(self.schema)
                )
            )
        ]
        self.assertEqual(versions[-1], 4)
        rows = dict(
            self.query(
                "SELECT c.relname, c.relrowsecurity FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                "WHERE n.nspname=%s AND c.relname IN ('pantry_items', 'pantry_consumed_entries')",
                (self.schema,),
            )
        )
        self.assertEqual(rows, {"pantry_items": True, "pantry_consumed_entries": True})

    def test_add_merge_subtract_and_shortfall(self):
        self.store.apply(
            [
                {
                    "op": "add",
                    "name": "Arroz",
                    "quantity": "1",
                    "unit": "kg",
                    "source": "receipt",
                }
            ]
        )
        self.store.apply(
            [{"op": "add", "name": "arroz", "quantity": "500", "unit": "g"}]
        )
        [item] = self.store.list_items()
        self.assertEqual(
            (item.food, item.quantity, item.unit, item.source),
            ("arroz", Decimal("1500"), "g", "receipt"),
        )
        effects = self.store.apply(
            [{"op": "subtract", "name": "arroz", "quantity": "2", "unit": "kg"}]
        )
        self.assertEqual(effects[0]["shortfall"], {"quantity": "500", "unit": "g"})
        self.assertEqual(self.store.list_items(), [])

    def test_preview_writes_nothing_and_a_stale_digest_is_a_conflict(self):
        from sabor.pantry.store import PantryConflict

        changes = [{"op": "add", "name": "sal", "quantity": "1", "unit": "kg"}]
        effects, digest = self.store.preview_with_digest(changes)
        self.assertEqual(effects[0]["action"], "create")
        self.assertEqual(self.store.list_items(), [])
        self.store.apply(changes, expected_digest=digest)
        with self.assertRaises(PantryConflict):
            self.store.apply(changes, expected_digest=digest)
        self.assertEqual(self.store.list_items()[0].quantity, Decimal("1000"))

    def test_invalid_changes_roll_back_the_whole_batch(self):
        from sabor.pantry.store import PantryError

        with self.assertRaises(PantryError):
            self.store.apply([{"op": "add", "name": "pan"}, {"op": "explode"}])
        self.assertEqual(self.store.list_items(), [])

    def test_consumed_entries_are_remembered(self):
        from sabor.pantry.store import PantryConflict

        self.store.apply(
            [{"op": "add", "name": "arroz", "quantity": "1", "unit": "kg"}]
        )
        changes = [{"op": "subtract", "name": "arroz", "quantity": "100", "unit": "g"}]
        self.store.apply(changes, consumed_entries=["e1"])
        self.assertEqual(self.store.consumed_entries(["e1", "e2"]), {"e1"})
        with self.assertRaises(PantryConflict):
            self.store.apply(changes, consumed_entries=["e1"])
        self.assertEqual(self.store.list_items()[0].quantity, Decimal("900"))

    def test_a_preview_reads_one_snapshot(self):
        # CodeRabbit on #152: a commit from another connection between the digest read and
        # the plan read must not mix two states in one preview.
        from unittest.mock import patch
        from sabor.pantry.store import PantryStore
        changes = [{"op": "add", "name": "sal", "quantity": "1", "unit": "kg"}]
        empty_digest = self.store.digest(changes)
        other = psycopg.connect(DSN, autocommit=True)
        self.addCleanup(other.close)
        real_plan = PantryStore._plan

        def plan_after_a_concurrent_commit(store, connection, planned):
            other.execute(
                sql.SQL("INSERT INTO {}.pantry_items VALUES (%s, %s, %s, NULL, %s, %s, NULL, %s, NULL, %s)")
                .format(sql.Identifier(self.schema)),
                ("x", "sal", "sal", "500", "g", "manual", "t"))
            return real_plan(store, connection, planned)

        with patch.object(PantryStore, "_plan", plan_after_a_concurrent_commit):
            effects, digest = self.store.preview_with_digest(changes)
        self.assertEqual(digest, empty_digest)
        self.assertEqual(effects[0]["action"], "create")

    def test_a_lost_connection_is_upstream_error_and_the_next_call_reconnects(self):
        # CodeRabbit on #152: _session closes a broken shared connection and reopens it later.
        from sabor.store import StoreError

        self.store.apply([{"op": "add", "name": "arroz", "quantity": "1", "unit": "kg"}])
        backend = self.recipes._connection.info.backend_pid
        self.query("SELECT pg_terminate_backend(%s)", (backend,))
        with self.assertRaises(StoreError) as caught:
            self.store.list_items()
        self.assertEqual(caught.exception.code, "upstream_error")
        [item] = self.store.list_items()
        self.assertEqual((item.food, item.quantity), ("arroz", Decimal("1000")))
        self.assertNotEqual(self.recipes._connection.info.backend_pid, backend)

    def stock(self, recipes):
        from types import SimpleNamespace
        from sabor.pricing.pantry import load_stock

        context = SimpleNamespace(settings=load_settings({**PG_ENV, "SABOR_DATABASE_URL": DSN}), store=recipes)
        return [(s.name, s.quantity, s.unit) for s in load_stock(context)]

    def test_estimates_read_the_postgres_pantry(self):
        # #144 point 5 on Postgres: exclude_pantry counts the structured pantry here too.
        self.store.apply([{"op": "add", "name": "arroz", "quantity": "1", "unit": "kg"}])
        self.assertEqual(self.stock(self.recipes), [("arroz", Decimal("1000"), "g")])

    def test_estimates_never_create_or_migrate_the_schema(self):
        # CodeRabbit on #152: the estimate read is read-only, so no migrations run.
        from sabor.store.postgres import PostgresRecipeStore

        schema = "test_" + uuid4().hex
        fresh = PostgresRecipeStore(DSN, schema=schema)
        self.addCleanup(fresh.close)
        self.assertEqual(self.stock(fresh), [])
        self.assertEqual(self.query("SELECT 1 FROM pg_namespace WHERE nspname=%s", (schema,)), [])

    def test_a_lost_connection_is_reopened_for_the_estimate_read(self):
        from sabor.store import StoreError

        self.store.apply([{"op": "add", "name": "sal", "quantity": "1", "unit": "kg"}])
        self.query("SELECT pg_terminate_backend(%s)", (self.recipes._connection.info.backend_pid,))
        self.assertEqual(self.stock(self.recipes), [("sal", Decimal("1000"), "g")])
        broken = type(self.recipes)("postgresql://u@127.0.0.1:1/db", schema=self.schema)
        with self.assertRaises(StoreError) as caught:
            self.stock(broken)
        self.assertEqual(caught.exception.code, "upstream_error")

    def test_the_pantry_tools_work_through_the_postgres_store(self):
        from sabor.core.context import build_context
        from sabor.core.toolsets import use_context
        from sabor.tools.pantry import edit_pantry, get_pantry

        env = {**PG_ENV, "SABOR_DATABASE_URL": DSN}
        context = build_context(
            load_settings(env), store_factory=lambda settings: self.recipes
        )
        with use_context(context):
            changes = [{"op": "add", "name": "leche", "quantity": 1, "unit": "l"}]
            digest = edit_pantry(changes)["data"]["preview_digest"]
            self.assertTrue(
                edit_pantry(changes, confirm=True, preview_digest=digest)["ok"]
            )
            self.assertEqual(
                [
                    (i["food"], i["quantity"], i["unit"])
                    for i in get_pantry()["data"]["items"]
                ],
                [("leche", "1000", "ml")],
            )


if __name__ == "__main__":
    unittest.main()
