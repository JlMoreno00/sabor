"""Subtracting cooked recipes and planned meals from the pantry (#144 phase 2)."""

import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

from sabor.core.config import load_settings
from sabor.core.context import build_context, sqlite_store_factory
from sabor.core.toolsets import use_context
from sabor.pantry.consume import plan_consumption
from sabor.pantry.model import PantryItem
from sabor.store import Ingredient, MealEntryDraft, NamedRef, Recipe
from sabor.tools.pantry import consume_from_pantry, edit_pantry, get_pantry


def _item(food, quantity, unit, name=None):
    return PantryItem(
        "id-" + food + (unit or ""),
        name or food,
        food,
        None,
        None if quantity is None else Decimal(quantity),
        unit,
        None,
        "manual",
        None,
        "t",
    )


def _ing(text, quantity=None, unit=None, food=None):
    return Ingredient(
        quantity=quantity,
        unit=NamedRef(name=unit) if unit else None,
        food=NamedRef(name=food) if food else None,
        originalText=text,
    )


class PlanConsumptionTests(unittest.TestCase):
    def test_scaled_measured_ingredients_become_subtractions(self):
        items = [_item("arroz", "1000", "g"), _item("huevo", "6", "ud")]
        changes, report = plan_consumption(
            [
                _ing("300 g de arroz", 300, "g", "arroz"),
                _ing("2 huevos", 2, None, "huevos"),
            ],
            Decimal("1.5"),
            items,
        )
        self.assertEqual(
            changes,
            [
                {"op": "subtract", "name": "arroz", "quantity": "450", "unit": "g"},
                {"op": "subtract", "name": "huevo", "quantity": "3", "unit": "ud"},
            ],
        )
        self.assertEqual([r["status"] for r in report], ["subtract", "subtract"])

    def test_same_food_twice_is_one_subtraction(self):
        changes, _ = plan_consumption(
            [
                _ing("100 g arroz", 100, "g", "arroz"),
                _ing("50 g arroz", 50, "g", "arroz"),
            ],
            Decimal(1),
            [_item("arroz", "1000", "g")],
        )
        self.assertEqual(
            changes,
            [{"op": "subtract", "name": "arroz", "quantity": "150", "unit": "g"}],
        )

    def test_nothing_is_guessed(self):
        items = [
            _item("sal", None, None),
            _item("leche", "1000", "ml"),
            _item("pimienta", "2", "ud"),
        ]
        changes, report = plan_consumption(
            [
                _ing("sal al gusto", food="sal"),  # not measured: the pantry keeps it
                _ing(
                    "100 g de leche", 100, "g", "leche"
                ),  # grams of an item kept in millilitres
                _ing("1 cebolla", 1, None, "cebolla"),  # not in the pantry
                _ing("5 g de pimienta", 5, "g", "pimienta"),
            ],
            Decimal(1),
            items,
        )
        self.assertEqual(changes, [])
        self.assertEqual(
            [r["status"] for r in report],
            ["not_measured", "incompatible_unit", "not_in_pantry", "incompatible_unit"],
        )

    def test_unknown_pantry_amount_is_left_alone(self):
        changes, report = plan_consumption(
            [_ing("200 g de queso", 200, "g", "queso")],
            Decimal(1),
            [_item("queso", None, None)],
        )
        self.assertEqual((changes, report[0]["status"]), ([], "pantry_amount_unknown"))


class ConsumeToolTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        env = {"SABOR_DATA_DIR": tmp.name, "SABOR_TOOLSETS": "core,pantry"}
        self.context = build_context(load_settings(env), store_factory=sqlite_store_factory)
        binding = use_context(self.context)
        binding.__enter__()
        self.addCleanup(binding.__exit__, None, None, None)
        slug = self.store.create_recipe(
            Recipe(
                name="Arroz con huevo",
                recipeIngredient=(
                    _ing("200 g de arroz", 200, "g", "arroz"),
                    _ing("2 huevos", 2, None, "huevo"),
                ),
            )
        ).slug
        assert slug is not None
        self.slug: str = slug
        self.apply(
            [
                {"op": "add", "name": "arroz", "quantity": 1, "unit": "kg"},
                {"op": "add", "name": "huevo", "quantity": 6, "unit": "ud"},
            ]
        )

    @property
    def store(self):
        store = self.context.store
        assert store is not None
        return store

    def apply(self, changes):
        digest = edit_pantry(changes)["data"]["preview_digest"]
        self.assertTrue(edit_pantry(changes, confirm=True, preview_digest=digest)["ok"])

    def stock(self):
        return {i["food"]: i["quantity"] for i in get_pantry()["data"]["items"]}

    def confirm(self, **source: Any):
        preview = consume_from_pantry(**source)
        self.assertTrue(preview["ok"], preview)
        result = consume_from_pantry(
            **source, confirm=True, preview_digest=preview["data"]["preview_digest"]
        )
        self.assertTrue(result["ok"], result)
        return preview, result

    def test_a_cooked_recipe_is_previewed_then_subtracted_scaled(self):
        preview = consume_from_pantry(recipe_slugs=[self.slug], servings_multiplier=2)
        self.assertTrue(preview["data"]["preview"])
        self.assertEqual(self.stock(), {"arroz": "1000", "huevo": "6"})
        self.assertEqual(
            [r["status"] for r in preview["data"]["ingredients"]],
            ["subtract", "subtract"],
        )
        _, result = self.confirm(recipe_slugs=[self.slug], servings_multiplier=2)
        self.assertFalse(result["data"]["preview"])
        self.assertEqual(self.stock(), {"arroz": "600", "huevo": "2"})

    def test_confirm_needs_the_digest_and_rejects_a_stale_one(self):
        digest = consume_from_pantry(recipe_slugs=[self.slug])["data"]["preview_digest"]
        self.assertEqual(
            consume_from_pantry(recipe_slugs=[self.slug], confirm=True)["error"][
                "code"
            ],
            "invalid_argument",
        )
        self.apply([{"op": "add", "name": "sal"}])
        stale = consume_from_pantry(
            recipe_slugs=[self.slug], confirm=True, preview_digest=digest
        )
        self.assertEqual(stale["error"]["code"], "conflict")
        self.assertEqual(self.stock()["arroz"], "1000")

    def test_a_planned_week_is_subtracted_once(self):
        store = self.store
        store.set_mealplan_entry(
            MealEntryDraft("2026-11-23", "lunch", "Arroz con huevo", self.slug)
        )
        store.set_mealplan_entry(MealEntryDraft("2026-11-24", "dinner", "Sopa", None))
        week: dict[str, Any] = {"mealplan_start": "2026-11-23", "mealplan_end": "2026-11-29"}
        preview, _ = self.confirm(**week)
        self.assertEqual(
            [s["reason"] for s in preview["data"]["skipped"]], ["text_only_entry"]
        )
        self.assertEqual(self.stock(), {"arroz": "800", "huevo": "4"})
        # The same week again: the entry was already subtracted and is skipped.
        again = consume_from_pantry(**week)
        self.assertEqual(
            sorted(s["reason"] for s in again["data"]["skipped"]),
            ["already_consumed", "text_only_entry"],
        )
        self.assertEqual(again["data"]["effects"], [])
        self.confirm(**week)
        self.assertEqual(self.stock(), {"arroz": "800", "huevo": "4"})

    def test_two_confirmations_of_one_week_preview_subtract_once(self):
        store = self.store
        store.set_mealplan_entry(
            MealEntryDraft("2026-11-23", "lunch", "Arroz con huevo", self.slug)
        )
        week: dict[str, Any] = {"mealplan_start": "2026-11-23", "mealplan_end": "2026-11-23"}
        digest = consume_from_pantry(**week)["data"]["preview_digest"]
        first = consume_from_pantry(**week, confirm=True, preview_digest=digest)
        second = consume_from_pantry(**week, confirm=True, preview_digest=digest)
        self.assertTrue(first["ok"])
        self.assertEqual(second["error"]["code"], "conflict")
        self.assertEqual(self.stock(), {"arroz": "800", "huevo": "4"})

    def test_invalid_sources_write_nothing(self):
        for source in (
            {},
            {
                "recipe_slugs": [self.slug],
                "mealplan_start": "2026-11-23",
                "mealplan_end": "2026-11-23",
            },
            {"recipe_slugs": []},
            {"mealplan_start": "2026-11-30", "mealplan_end": "2026-11-23"},
            {"mealplan_start": "23/11/2026", "mealplan_end": "2026-11-23"},
            {"recipe_slugs": [self.slug], "servings_multiplier": 0},
            {"recipe_slugs": [self.slug], "servings_multiplier": 21},
            {"recipe_slugs": [self.slug], "confirm": cast(Any, "yes")},
        ):
            with self.subTest(source=source):
                self.assertEqual(
                    consume_from_pantry(**cast(Any, source))["error"]["code"], "invalid_argument"
                )
        self.assertEqual(self.stock(), {"arroz": "1000", "huevo": "6"})

    def test_unknown_recipe_is_reported(self):
        result = consume_from_pantry(recipe_slugs=["no-existe"])
        self.assertFalse(result["ok"])
        self.assertEqual(self.stock(), {"arroz": "1000", "huevo": "6"})


class ConsumedEntriesStoreTests(unittest.TestCase):
    def test_marked_entries_survive_and_block_a_second_apply(self):
        from sabor.pantry.store import PantryConflict, PantryStore

        with tempfile.TemporaryDirectory() as directory:
            store = PantryStore(Path(directory) / "pantry.db")
            store.apply([{"op": "add", "name": "arroz", "quantity": 1, "unit": "kg"}])
            changes = [
                {"op": "subtract", "name": "arroz", "quantity": 100, "unit": "g"}
            ]
            store.apply(changes, consumed_entries=["e1"])
            self.assertEqual(store.consumed_entries(["e1", "e2"]), {"e1"})
            with self.assertRaises(PantryConflict):
                store.apply(changes, consumed_entries=["e1"])
            self.assertEqual(store.list_items()[0].quantity, Decimal("900"))


if __name__ == "__main__":
    unittest.main()
