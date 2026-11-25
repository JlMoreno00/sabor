"""Pantry MCP tools: read, edit and receipt ingestion with preview and digest (#144)."""

import tempfile
import unittest
from pathlib import Path
from typing import Any, cast

from sabor.core.config import load_settings
from sabor.core.context import build_context
from sabor.core.toolsets import catalog, use_context
from sabor.tools.pantry import add_pantry_from_receipt, edit_pantry, get_pantry


class PantryToolTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        env = {"SABOR_DATA_DIR": str(self.root), "SABOR_TOOLSETS": "pantry"}
        binding = use_context(build_context(load_settings(env)))
        binding.__enter__()
        self.addCleanup(binding.__exit__, None, None, None)

    def foods(self):
        return [
            (i["food"], i["quantity"], i["unit"]) for i in get_pantry()["data"]["items"]
        ]

    def test_edit_previews_then_applies_only_with_the_digest(self):
        changes = [{"op": "add", "name": "Arroz", "quantity": 1, "unit": "kg"}]
        preview = edit_pantry(changes)
        self.assertTrue(preview["ok"])
        self.assertTrue(preview["data"]["preview"])
        self.assertEqual(preview["data"]["effects"][0]["action"], "create")
        self.assertEqual(self.foods(), [])
        missing = edit_pantry(changes, confirm=True)
        self.assertEqual(missing["error"]["code"], "invalid_argument")
        self.assertIn("preview_digest", missing["error"]["message"])
        self.assertEqual(self.foods(), [])
        result = edit_pantry(
            changes, confirm=True, preview_digest=preview["data"]["preview_digest"]
        )
        self.assertTrue(result["ok"])
        self.assertFalse(result["data"]["preview"])
        self.assertEqual(self.foods(), [("arroz", "1000", "g")])

    def test_stale_digest_is_rejected_without_writing(self):
        changes = [{"op": "add", "name": "sal", "quantity": 1, "unit": "kg"}]
        digest = edit_pantry(changes)["data"]["preview_digest"]
        edit_pantry(
            [{"op": "add", "name": "sal", "quantity": 1, "unit": "kg"}],
            confirm=True,
            preview_digest=digest,
        )
        stale = edit_pantry(changes, confirm=True, preview_digest=digest)
        self.assertEqual(stale["error"]["code"], "conflict")
        self.assertEqual(self.foods(), [("sal", "1000", "g")])

    def test_invalid_changes_fail_with_invalid_argument(self):
        result = edit_pantry([{"op": "explode", "name": "x"}])
        self.assertEqual(result["error"]["code"], "invalid_argument")
        self.assertEqual(
            edit_pantry(cast(Any, "not a list"))["error"]["code"], "invalid_argument"
        )

    def test_receipt_preview_lists_items_ignored_lines_and_uncertainty(self):
        lines = [
            "2 LECHE SEMI 1L 0,89 1,78",
            "PAN BARRA 0,60",
            "BOLSA PLASTICO 0,15",
            "TOTAL 2,53",
        ]
        preview = add_pantry_from_receipt(lines)
        data = preview["data"]
        self.assertTrue(data["preview"])
        self.assertEqual(
            [(i["name"], i["quantity"], i["unit"]) for i in data["items"]],
            [("leche semidesnatada", "2000", "ml"), ("pan barra", None, None)],
        )
        self.assertEqual(data["items"][1]["uncertain"], ["quantity_unknown"])
        self.assertEqual(
            [i["line"] for i in data["ignored"]], ["BOLSA PLASTICO 0,15", "TOTAL 2,53"]
        )
        self.assertEqual(self.foods(), [])
        result = add_pantry_from_receipt(
            lines, confirm=True, preview_digest=data["preview_digest"]
        )
        self.assertTrue(result["ok"])
        self.assertEqual(
            self.foods(),
            [("leche semidesnatada", "2000", "ml"), ("pan barra", None, None)],
        )
        self.assertEqual(
            {i["source"] for i in get_pantry()["data"]["items"]}, {"receipt"}
        )

    def test_receipt_with_only_ignored_lines_changes_nothing(self):
        result = add_pantry_from_receipt(["TOTAL 2,53"])
        self.assertTrue(result["ok"])
        self.assertEqual((result["data"]["items"], result["data"]["effects"]), ([], []))

    def test_receipt_rejects_bad_input(self):
        for lines in ([], "TOTAL", [1], ["x" * 501], ["a"] * 201):
            with self.subTest(lines=str(lines)[:20]):
                self.assertEqual(
                    add_pantry_from_receipt(cast(Any, lines))["error"]["code"],
                    "invalid_argument",
                )

    def test_get_pantry_filters(self):
        edit_pantry(
            [
                {"op": "add", "name": "yogur", "expires_on": "2000-01-01"},
                {"op": "add", "name": "sal"},
            ],
            confirm=True,
            preview_digest=edit_pantry(
                [
                    {"op": "add", "name": "yogur", "expires_on": "2000-01-01"},
                    {"op": "add", "name": "sal"},
                ]
            )["data"]["preview_digest"],
        )
        self.assertEqual(
            [i["food"] for i in get_pantry(food="Yogur")["data"]["items"]], ["yogur"]
        )
        self.assertEqual(
            [i["food"] for i in get_pantry(expiring_within_days=0)["data"]["items"]],
            ["yogur"],
        )
        self.assertEqual(
            get_pantry(expiring_within_days=-1)["error"]["code"], "invalid_argument"
        )


class PantryReviewRegressionTests(unittest.TestCase):
    """CodeRabbit on #147: per-instance Mealie pantry and the digest checked inside the write."""

    def bind(self, env):
        binding = use_context(build_context(load_settings(env)))
        binding.__enter__()
        self.addCleanup(binding.__exit__, None, None, None)

    def test_mealie_pantries_follow_each_instance_data_dir(self):
        from unittest.mock import patch
        dirs = [tempfile.TemporaryDirectory() for _ in range(3)]
        for d in dirs:
            self.addCleanup(d.cleanup)
        # The process environment points elsewhere: it must not be used.
        with patch.dict('os.environ', {'SABOR_DATA_DIR': dirs[2].name}):
            for directory, food in ((dirs[0].name, 'arroz'), (dirs[1].name, 'pasta')):
                env = {'SABOR_DATA_DIR': directory, 'SABOR_TOOLSETS': 'pantry',
                       'MEALIE_BASE_URL': 'http://mealie', 'MEALIE_API_TOKEN': 't'}
                binding = use_context(build_context(load_settings(env)))
                with binding:
                    changes = [{'op': 'add', 'name': food}]
                    digest = edit_pantry(changes)['data']['preview_digest']
                    self.assertTrue(edit_pantry(changes, confirm=True, preview_digest=digest)['ok'])
                    self.assertEqual([i['food'] for i in get_pantry()['data']['items']], [food])
        self.assertTrue((Path(dirs[0].name) / 'pantry.db').exists())
        self.assertTrue((Path(dirs[1].name) / 'pantry.db').exists())
        self.assertFalse((Path(dirs[2].name) / 'pantry.db').exists())

    def test_a_change_between_the_check_and_the_write_is_a_conflict(self):
        # Two confirmations of one preview: the second must not apply the receipt again.
        from unittest.mock import patch
        from sabor.pantry.store import PantryStore
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.bind({'SABOR_DATA_DIR': tmp.name, 'SABOR_TOOLSETS': 'pantry'})
        lines = ['ARROZ BOMBA 1KG 2,35']
        digest = add_pantry_from_receipt(lines)['data']['preview_digest']
        real_apply = PantryStore.apply

        def racing_apply(store, changes, *args, **kwargs):
            # Another confirmation of the same preview wins the race just before this write.
            real_apply(store, changes)
            return real_apply(store, changes, *args, **kwargs)

        with patch.object(PantryStore, 'apply', racing_apply):
            result = add_pantry_from_receipt(lines, confirm=True, preview_digest=digest)
        self.assertEqual(result['error']['code'], 'conflict')
        self.assertEqual([(i['food'], i['quantity']) for i in get_pantry()['data']['items']],
                         [('arroz bomba', '1000')])


class PantryStoreErrorTests(unittest.TestCase):
    """CodeRabbit on #152: store failures (a lost Postgres connection) become the error envelope."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        env = {"SABOR_DATA_DIR": tmp.name, "SABOR_TOOLSETS": "pantry"}
        binding = use_context(build_context(load_settings(env)))
        binding.__enter__()
        self.addCleanup(binding.__exit__, None, None, None)

    def test_reads_previews_and_confirmations_return_the_store_error_envelope(self):
        from unittest.mock import patch
        from sabor.pantry.store import PantryStore
        from sabor.store import StoreError

        def broken(*args, **kwargs):
            raise StoreError("upstream_error", "postgres")

        changes = [{"op": "add", "name": "sal"}]
        digest = edit_pantry(changes)["data"]["preview_digest"]
        for method, call in (("list_items", lambda: get_pantry()),
                             ("preview_with_digest", lambda: edit_pantry(changes)),
                             ("apply", lambda: edit_pantry(changes, confirm=True, preview_digest=digest)),
                             ("preview_with_digest", lambda: add_pantry_from_receipt(["ARROZ 1KG 1,20"]))):
            with self.subTest(method=method), patch.object(PantryStore, method, broken):
                result = call()
                self.assertFalse(result["ok"])
                self.assertEqual(result["error"]["code"], "upstream_error")


class PantryRegistrationTests(unittest.TestCase):
    def test_pantry_tools_are_an_opt_in_toolset(self):
        from mcp.server.fastmcp import FastMCP
        from sabor.server import register_tools

        names = {fn.__name__ for group, fn, requires in catalog if group == "pantry"}
        self.assertEqual(
            names, {"get_pantry", "edit_pantry", "add_pantry_from_receipt", "consume_from_pantry"}
        )
        with tempfile.TemporaryDirectory() as directory:
            for toolsets, expected in (("core", set()), ("core,pantry", names)):
                server = FastMCP("t")
                register_tools(
                    server,
                    env={"SABOR_DATA_DIR": directory, "SABOR_TOOLSETS": toolsets},
                )
                registered = {t.name for t in server._tool_manager.list_tools()}
                self.assertEqual(registered & names, expected)

    def test_postgres_store_registers_pantry_tools(self):
        # #144 point 8: the pantry lives in the Postgres schema too.
        from mcp.server.fastmcp import FastMCP
        from sabor.server import register_tools

        with tempfile.TemporaryDirectory() as directory:
            server = FastMCP("t")
            register_tools(
                server,
                env={
                    "SABOR_DATA_DIR": directory,
                    "SABOR_TOOLSETS": "pantry",
                    "SABOR_STORE": "postgres",
                    "SABOR_DATABASE_URL": "postgresql://u@localhost/db",
                },
                store_factory=lambda settings: None,
            )
            self.assertTrue(
                {t.name for t in server._tool_manager.list_tools()} & {"get_pantry"}
            )

    def test_setup_enables_pantry_like_preferences(self):
        from sabor.setup_cli import SETUP_TOOLSETS

        self.assertIn("pantry", SETUP_TOOLSETS)


if __name__ == "__main__":
    unittest.main()
