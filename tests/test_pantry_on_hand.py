"""#144 point 9: mark Mealie foods "on hand" from the structured pantry (José's decision).

Mealie 3.22 keeps "on hand" per household: a food is on hand when the household slug is in
its householdsWithIngredientFood. Sabor marks the foods its pantry holds and unmarks only
the ones it marked itself; marks set by hand in Mealie are never removed.
"""
import tempfile
import unittest
from pathlib import Path

from fakes.mealie_api import MealieAPI
from sabor.core.config import load_settings
from sabor.core.context import build_context
from sabor.core.toolsets import use_context
from sabor.store.mealie import MealieRecipeStore


def food(food_id, name, plural=None, aliases=(), on_hand=(), extras=None):
    return {"id": food_id, "name": name, "pluralName": plural, "description": f"{name} desc",
            "extras": dict(extras or {}), "labelId": "label-1",
            "aliases": [{"name": a} for a in aliases],
            "householdsWithIngredientFood": list(on_hand)}


class OnHandSyncTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.api = MealieAPI()
        self.api.foods = {
            "f-rice": food("f-rice", "arroz"),
            "f-egg": food("f-egg", "huevo", plural="huevos"),
            "f-milk": food("f-milk", "leche entera", aliases=("leche",)),
            "f-salt": food("f-salt", "sal", on_hand=("family", "other")),
            "f-oil": food("f-oil", "aceite"),
        }
        store = MealieRecipeStore("https://mealie.invalid", "token", self.api)
        env = {"MEALIE_BASE_URL": "https://mealie.invalid", "MEALIE_API_TOKEN": "token",
               "SABOR_TOOLSETS": "core,pantry", "SABOR_DATA_DIR": directory.name}
        self.context = build_context(load_settings(env), store_factory=lambda settings: store)

    def call(self, function, *args, **kwargs):
        with use_context(self.context):
            return function(*args, **kwargs)

    def pantry(self, changes):
        from sabor.tools.pantry import edit_pantry
        digest = self.call(edit_pantry, changes)["data"]["preview_digest"]
        result = self.call(edit_pantry, changes, confirm=True, preview_digest=digest)
        self.assertTrue(result["ok"], result)

    def sync(self):
        from sabor.tools.pantry import sync_pantry_on_hand
        preview = self.call(sync_pantry_on_hand)
        self.assertTrue(preview["ok"], preview)
        result = self.call(sync_pantry_on_hand, confirm=True, preview_digest=preview["data"]["preview_digest"])
        self.assertTrue(result["ok"], result)
        return preview, result

    def on_hand(self):
        return {f["id"] for f in self.api.foods.values() if "family" in f["householdsWithIngredientFood"]}

    def test_preview_matches_pantry_foods_and_writes_nothing(self):
        from sabor.tools.pantry import sync_pantry_on_hand
        self.pantry([{"op": "add", "name": "arroz", "quantity": 1, "unit": "kg"},
                     {"op": "add", "name": "huevos", "quantity": 6, "unit": "ud"},
                     {"op": "add", "name": "leche", "quantity": 1, "unit": "l"},
                     {"op": "add", "name": "sal"},
                     {"op": "add", "name": "azafrán", "quantity": 1, "unit": "g"},
                     {"op": "add", "name": "aceite de girasol", "food_id": "f-oil"}])
        preview = self.call(sync_pantry_on_hand)
        data = preview["data"]
        self.assertTrue(data["preview"])
        self.assertEqual(data["household"], "family")
        # Matched by name, plural, alias and the item's own food_id; salt is already on hand.
        self.assertEqual([f["food_id"] for f in data["mark"]], ["f-oil", "f-rice", "f-egg", "f-milk"])
        self.assertEqual(data["unmark"], [])
        self.assertEqual(data["unmatched"], [{"name": "azafrán", "reason": "not_in_mealie"}])
        self.assertEqual(self.api.food_puts, [])
        self.assertEqual(self.on_hand(), {"f-salt"})

    def test_confirm_marks_and_keeps_every_other_food_field(self):
        self.pantry([{"op": "add", "name": "huevos", "quantity": 6, "unit": "ud"}])
        _, result = self.sync()
        self.assertFalse(result["data"]["preview"])
        self.assertEqual(self.on_hand(), {"f-egg", "f-salt"})
        egg = self.api.foods["f-egg"]
        self.assertEqual((egg["name"], egg["pluralName"], egg["description"], egg["labelId"]),
                         ("huevo", "huevos", "huevo desc", "label-1"))
        self.assertEqual(egg["extras"], {"sabor_on_hand": "family"})
        # Nothing left to do: a second sync writes nothing.
        before = len(self.api.food_puts)
        preview, _ = self.sync()
        self.assertEqual((preview["data"]["mark"], preview["data"]["unmark"]), ([], []))
        self.assertEqual(len(self.api.food_puts), before)

    def test_only_foods_sabor_marked_are_unmarked(self):
        self.pantry([{"op": "add", "name": "huevos", "quantity": 6, "unit": "ud"},
                     {"op": "add", "name": "sal"}])
        self.sync()
        from sabor.tools.pantry import get_pantry
        for item in self.call(get_pantry)["data"]["items"]:
            self.pantry([{"op": "remove", "id": item["id"]}])
        preview, _ = self.sync()
        # The eggs were marked by Sabor; the salt was on hand already (marked by hand).
        self.assertEqual([f["food_id"] for f in preview["data"]["unmark"]], ["f-egg"])
        self.assertEqual(self.on_hand(), {"f-salt"})
        self.assertNotIn("sabor_on_hand", self.api.foods["f-egg"]["extras"])
        self.assertEqual(self.api.foods["f-salt"]["householdsWithIngredientFood"], ["family", "other"])

    def test_a_stale_preview_is_a_conflict(self):
        from sabor.tools.pantry import sync_pantry_on_hand
        self.pantry([{"op": "add", "name": "arroz", "quantity": 1, "unit": "kg"}])
        digest = self.call(sync_pantry_on_hand)["data"]["preview_digest"]
        self.pantry([{"op": "add", "name": "huevos", "quantity": 6, "unit": "ud"}])
        result = self.call(sync_pantry_on_hand, confirm=True, preview_digest=digest)
        self.assertEqual(result["error"]["code"], "conflict")
        self.assertEqual(self.api.food_puts, [])
        result = self.call(sync_pantry_on_hand, confirm=True)
        self.assertEqual(result["error"]["code"], "invalid_argument")


class OnHandOnlyWithMealieTests(unittest.TestCase):
    """Without the Mealie store the sync is not registered at all; nothing else changes."""

    def test_sqlite_and_postgres_do_not_register_it(self):
        from mcp.server.fastmcp import FastMCP
        from sabor.server import register_tools, _registered
        with tempfile.TemporaryDirectory() as directory:
            for env in ({"SABOR_STORE": "sqlite", "SABOR_STORE_DB": str(Path(directory) / "s.db")},
                        {"SABOR_STORE": "postgres", "SABOR_DATABASE_URL": "postgresql://u@localhost/db"}):
                server = FastMCP("test")
                register_tools(server, {**env, "SABOR_TOOLSETS": "core,pantry", "SABOR_DATA_DIR": directory})
                tools = set(server._tool_manager._tools)
                self.assertIn("get_pantry", tools, env)
                self.assertNotIn("sync_pantry_on_hand", tools, env)
                self.assertIn("pantry", _registered[server].enabled_toolsets)


if __name__ == "__main__":
    unittest.main()
