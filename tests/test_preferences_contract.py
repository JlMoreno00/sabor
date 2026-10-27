"""Preference persistence contract shared by local and remote stores."""

import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4
from typing import Any, TYPE_CHECKING, cast

from sabor.store import RecipeStore

from sabor.core.config import load_settings
from sabor.core.context import build_context
from sabor.preferences import empty_preferences, preferences_call, _write
from sabor.preferences_cli import main
from tests.test_store_contract_postgres import DSN, psycopg

from importlib import import_module

sql: Any = import_module("psycopg.sql") if psycopg else None

Base = unittest.TestCase if TYPE_CHECKING else object


class PreferencesContract(Base):
    source = "file"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store: Any = None
        if self.source == "postgres":
            from sabor.store.postgres import PostgresRecipeStore

            self.store = PostgresRecipeStore(DSN, schema="test_" + uuid4().hex)

            def cleanup():
                self.store.close()
                with psycopg.connect(DSN) as db:
                    db.execute(
                        sql.SQL("DROP SCHEMA {} CASCADE").format(
                            sql.Identifier(self.store.schema)
                        )
                    )

            self.addCleanup(cleanup)
            self.store.ensure_ready()
        self.context = build_context(
            load_settings({"SABOR_DATA_DIR": str(self.root)}),
            store_factory=(lambda s: cast(RecipeStore, self.store))
            if self.store
            else None,
        )

    def call(self, changes=None, confirm: Any = False):
        return preferences_call(self.context, changes, confirm)

    def test_roundtrip_preview_and_rejection(self):
        initial = self.call()
        self.assertTrue(initial["ok"])
        self.assertEqual(
            initial["data"],
            {
                "preferences": empty_preferences(),
                "source": "defaults",
                "updated_at": None,
            },
        )
        changes = {"set": {"household_size": 2}, "add": {"pantry": [" Rice ", "rice"]}}
        preview = self.call(changes)
        expected = {**empty_preferences(), "household_size": 2, "pantry": ["Rice"]}
        self.assertTrue(preview["ok"])
        self.assertEqual(preview["data"]["preferences"], expected)
        self.assertTrue(preview["data"]["preview"])
        self.assertEqual(preview["data"]["source"], "defaults")
        self.assertIsNone(preview["data"]["updated_at"])
        self.assertEqual(self.call(), initial)
        written = self.call(changes, True)
        self.assertTrue(written["ok"])
        self.assertFalse(written["data"]["preview"])
        self.assertEqual(written["data"]["preferences"], expected)
        self.assertEqual(written["data"]["source"], self.source)
        self.assertIsInstance(written["data"]["updated_at"], str)
        before = self.call()
        self.assertEqual(
            before["data"],
            {k: written["data"][k] for k in ("preferences", "source", "updated_at")},
        )
        for bad in (
            {"set": {"household_size": True}},
            {"add": {"pantry": [""]}},
            {"unknown": {}},
            [],
        ):
            result = self.call(bad, True)
            self.assertFalse(result["ok"])
            self.assertEqual(result["error"]["code"], "invalid_argument")
            self.assertEqual(self.call(), before)
        preview = self.call({"remove": {"pantry": ["RICE"]}})
        self.assertEqual(preview["data"]["preferences"]["pantry"], [])
        self.assertEqual(preview["data"]["source"], self.source)
        self.assertEqual(preview["data"]["updated_at"], before["data"]["updated_at"])
        self.assertEqual(self.call(), before)

    def test_all_fields_bounds_and_change_algebra(self):
        expected: dict[str, Any] = {
            key: ["A", "B"] for key in ("diet", "dislikes", "pantry", "notes")
        }
        expected["household_size"] = 20
        result = self.call({"set": expected}, True)
        self.assertTrue(result["ok"])
        self.assertEqual(result["data"]["preferences"], expected)
        self.assertEqual(result["data"]["source"], self.source)
        self.assertIsInstance(result["data"]["updated_at"], str)
        self.assertFalse(result["data"]["preview"])
        self.assertEqual(
            result["data"]["diff"],
            {
                key: {"before": empty_preferences()[key], "after": value}
                for key, value in expected.items()
            },
        )
        before = self.call()
        for field in ("diet", "dislikes", "pantry", "notes"):
            for value in ("text", [""], ["x" * 201], ["x"] * 201, [1]):
                result = self.call({"set": {field: value}}, True)
                self.assertFalse(result["ok"])
                self.assertEqual(result["error"]["code"], "invalid_argument")
                self.assertEqual(self.call(), before)
        for confirm in (None, 1, "true"):
            result = self.call({}, confirm)
            self.assertFalse(result["ok"])
            self.assertEqual(result["error"]["code"], "invalid_argument")
            self.assertEqual(self.call(), before)
        preview = self.call({"add": {"diet": ["a", "C"]}, "remove": {"diet": ["B"]}})
        self.assertTrue(preview["ok"])
        self.assertEqual(
            preview["data"]["preferences"], {**expected, "diet": ["A", "C"]}
        )
        self.assertEqual(
            preview["data"]["diff"],
            {"diet": {"before": ["A", "B"], "after": ["A", "C"]}},
        )
        self.assertEqual(preview["data"]["source"], self.source)
        self.assertEqual(preview["data"]["updated_at"], before["data"]["updated_at"])
        self.assertTrue(preview["data"]["preview"])
        self.assertEqual(self.call(), before)

    def test_import_file(self):
        path = self.root / "preferences.json"
        _write(path, {**empty_preferences(), "pantry": ["Rice"], "household_size": 3})
        original = path.read_bytes()

        def run(apply=False):
            output = io.StringIO()
            with (
                patch.dict(os.environ, {"SABOR_DATA_DIR": str(self.root)}, clear=True),
                patch("sabor.preferences_cli.build_context", return_value=self.context),
                contextlib.redirect_stdout(output),
            ):
                code = main(["import-file"] + (["--apply"] if apply else []))
            return code, json.loads(output.getvalue())

        before = self.call()
        code, preview = run()
        if self.source == "file":
            self.assertEqual(code, 1)
            self.assertFalse(preview["ok"])
            self.assertEqual(preview["error"]["code"], "invalid_argument")
        else:
            self.assertEqual(code, 0)
            self.assertTrue(preview["ok"])
            self.assertEqual(preview["data"]["preferences"]["pantry"], ["Rice"])
            self.assertEqual(preview["data"]["preferences"]["household_size"], 3)
            self.assertEqual(preview["data"]["source"], "defaults")
            self.assertIsNone(preview["data"]["updated_at"])
            self.assertTrue(preview["data"]["preview"])
            self.assertEqual(self.call(), before)
            self.call({"set": {"household_size": 2, "notes": ["Keep"]}}, True)
            for _ in range(2):
                code, result = run(True)
                self.assertEqual(code, 0)
                self.assertTrue(result["ok"])
                self.assertEqual(result["data"]["source"], "postgres")
                self.assertIsInstance(result["data"]["updated_at"], str)
                self.assertFalse(result["data"]["preview"])
                self.assertEqual(
                    result["data"]["preferences"],
                    {
                        **empty_preferences(),
                        "household_size": 2,
                        "pantry": ["Rice"],
                        "notes": ["Keep"],
                    },
                )
        self.assertEqual(path.read_bytes(), original)
        if self.source == "postgres":
            before = self.call()
            for raw in ("{broken", "[]", "null", '{"schema_version":2}'):
                path.write_text(raw)
                for apply in (False, True):
                    code, result = run(apply)
                    self.assertEqual(code, 1)
                    self.assertFalse(result["ok"])
                    self.assertEqual(result["error"]["code"], "invalid_response")
                    self.assertEqual(path.read_text(), raw)
                    self.assertEqual(self.call(), before)


class FilePreferencesContract(PreferencesContract, unittest.TestCase):
    pass


@unittest.skipUnless(DSN and psycopg, "requires Postgres")
class PostgresPreferencesContract(PreferencesContract, unittest.TestCase):
    source = "postgres"

    def test_corruption_and_write_failure(self):
        from sabor.store.postgres_preferences import PostgresPreferences

        self.call({"add": {"pantry": ["Rice"]}}, True)
        self.assertFalse((self.root / "preferences.json").exists())
        before = self.call()
        with patch.object(
            PostgresPreferences, "write", side_effect=OSError("private DSN")
        ):
            result = self.call({"set": {"pantry": []}}, True)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "upstream_error")
        self.assertNotIn("private DSN", str(result))
        self.assertEqual(self.call(), before)
        with psycopg.connect(DSN) as db:
            db.execute(
                sql.SQL(
                    "ALTER TABLE {} ADD CHECK (payload->'pantry' <> '[]'::jsonb)"
                ).format(sql.Identifier(self.store.schema, "preferences"))
            )
        result = self.call({"set": {"pantry": []}}, True)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "upstream_error")
        self.assertEqual(self.call(), before)
        with psycopg.connect(DSN) as db:
            db.execute(
                sql.SQL("UPDATE {} SET payload=%s").format(
                    sql.Identifier(self.store.schema, "preferences")
                ),
                ('{"pantry":42}',),
            )
        for confirm in (False, True):
            result = self.call({"set": {"pantry": []}}, confirm)
            self.assertFalse(result["ok"])
            self.assertEqual(result["error"]["code"], "invalid_response")
        with psycopg.connect(DSN) as db:
            payload = db.execute(
                sql.SQL("SELECT payload FROM {}").format(
                    sql.Identifier(self.store.schema, "preferences")
                )
            ).fetchone()[0]
            self.assertEqual(payload, {"pantry": 42})

    def test_independent_devices_merge_without_lost_updates(self):
        from concurrent.futures import ThreadPoolExecutor
        from sabor.store.postgres import PostgresRecipeStore

        stores = [PostgresRecipeStore(DSN, schema=self.store.schema) for _ in range(8)]
        contexts = []
        for store in stores:
            self.addCleanup(store.close)
            contexts.append(
                build_context(
                    self.context.settings, store_factory=lambda s, store=store: store
                )
            )

        def write(pair):
            i, context = pair
            return preferences_call(context, {"add": {"notes": [str(i)]}}, True)

        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(write, enumerate(contexts)))
        for result in results:
            self.assertTrue(result["ok"])
            self.assertEqual(result["data"]["source"], "postgres")
            self.assertIsInstance(result["data"]["updated_at"], str)
            self.assertFalse(result["data"]["preview"])
        result = self.call()
        self.assertTrue(result["ok"])
        self.assertEqual(
            set(result["data"]["preferences"]["notes"]), {str(i) for i in range(8)}
        )
        self.assertEqual(result["data"]["source"], "postgres")
        self.assertIsInstance(result["data"]["updated_at"], str)
        self.assertFalse((self.root / "preferences.json").exists())
