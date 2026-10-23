"""Reusable port contract: override make_store for an HTTP-backed adapter fixture."""
import unittest
from dataclasses import FrozenInstanceError
from typing import TYPE_CHECKING

ContractAssertions = unittest.TestCase if TYPE_CHECKING else object

from sabor.store import (Recipe, RecipeSearch, RecipePatch, Ingredient, Instruction,
                         ItemDraft, MealEntryDraft, DateRange, RecipeOccurrence,
                         StoreError, store_error_envelope)
from sabor.store.fake import FakeRecipeStore


class StoreContractMixin(ContractAssertions):
    def make_store(self):
        raise NotImplementedError

    def setUp(self):
        self.store = self.make_store()
        self.ref = self.store.create_recipe(Recipe(
            id="r", slug="soup", name="Soup", description="Keep",
            recipeIngredient=(Ingredient(quantity=None),),
            recipeInstructions=(Instruction(text="Cook"),)))

    def assert_code(self, code, call):
        with self.assertRaises(StoreError) as caught:
            call()
        self.assertEqual(caught.exception.code, code)

    def test_pagination(self):
        for page, size in ((0, 1), (1, 0), (1, 101), (True, 1)):
            self.assert_code("invalid_argument", lambda: self.store.search_recipes(
                RecipeSearch(page=page, per_page=size)))
        self.assertEqual(self.store.search_recipes(RecipeSearch(per_page=1)).total, 1)
        self.assertEqual(self.store.search_recipes(RecipeSearch(page=2)).items, ())

    def test_missing(self):
        self.assert_code("not_found", lambda: self.store.get_recipe("absent"))
        self.assert_code("not_found", lambda: self.store.get_shopping_items("absent"))

    def test_nulls_and_patch(self):
        before = self.store.get_recipe(self.ref.slug)
        self.assertIsNone(before.recipeIngredient[0].quantity)
        self.assertIsNone(before.recipeIngredient[0].food)
        with self.assertRaises(FrozenInstanceError):
            setattr(before, "name", "Changed")
        self.store.update_recipe(self.ref.slug, RecipePatch({"description": None}))
        after = self.store.get_recipe(self.ref.slug)
        self.assertIsNone(after.description)
        self.assertEqual(after.recipeInstructions, before.recipeInstructions)
        self.store.update_recipe(self.ref.slug, RecipePatch({"recipeIngredient": ()}))
        self.assertEqual(self.store.get_recipe(self.ref.slug).recipeIngredient, ())

    def test_ownership_atomic_check(self):
        a = self.store.create_shopping_list("A").id
        b = self.store.create_shopping_list("B").id
        self.store.add_shopping_items(a, [ItemDraft(note="salt")])
        item = self.store.get_shopping_items(a).items[0]
        self.assert_code("invalid_argument", lambda: self.store.check_shopping_items(b, [item.id], True))
        self.assert_code("invalid_argument", lambda: self.store.remove_shopping_items(b, [item.id]))
        self.store.check_shopping_items(a, [item.id], True)
        checked = self.store.get_shopping_items(a).items[0]
        self.assertTrue(checked.checked)
        self.assertEqual(checked.note, item.note)

    def test_repeated_occurrences(self):
        for day in ("2026-01-01", "2026-01-02"):
            self.store.set_mealplan_entry(MealEntryDraft(day, "dinner", "Soup", self.ref.slug))
        entries = self.store.get_mealplan(DateRange("2026-01-01", "2026-01-02"))
        self.assertEqual(len(entries), 2)
        target = self.store.create_shopping_list("Week").id
        result = self.store.add_recipe_occurrences(target, [RecipeOccurrence(self.ref.slug, 2) for e in entries])
        self.assertEqual(len(result.changed_ids), 2)
        self.assertEqual(len(self.store.get_shopping_items(target).items), 2)


class FakeStoreContract(StoreContractMixin, unittest.TestCase):
    def make_store(self):
        return FakeRecipeStore()

    def test_tags_comments_and_events(self):
        tag = self.store.get_or_create_tag("Dinner")
        self.assertEqual(self.store.get_or_create_tag("Dinner").id, tag.id)
        self.store.tag_recipe(self.ref.slug, ["Dinner"])
        self.assertEqual(self.store.get_recipe(self.ref.slug).tags[0].id, tag.id)
        self.store.tag_recipe(self.ref.slug, ["Lunch", "Dinner"])
        self.assertEqual(
            [tag.name for tag in self.store.get_recipe(self.ref.slug).tags],
            ["Dinner", "Lunch"],
        )
        comment = self.store.add_recipe_comment(self.ref.slug, "Good")
        event = self.store.log_cooking_event(self.ref.slug, "Cooked", None)
        self.assertEqual(self.store.comments[comment], (self.ref.slug, "Good"))
        self.assertEqual(
            self.store.cooking_events[event], (self.ref.slug, "Cooked", None)
        )
        self.assert_code(
            "not_found", lambda: self.store.add_recipe_comment("absent", "No")
        )
        self.assert_code(
            "not_found", lambda: self.store.log_cooking_event("absent", "No", None)
        )

    def test_preview_and_clear(self):
        target = self.store.create_shopping_list("Week").id
        self.store.add_shopping_items(target, [ItemDraft(note="salt")])
        preview = self.store.shopping_list_preview(target)
        self.assertEqual(preview["name"], "Week")
        self.assertEqual(preview["count"], 1)
        self.assertEqual(len(self.store.get_shopping_items(target).items), 1)
        self.assertEqual(self.store.clear_shopping_list_items(target)["deleted"], 1)
        self.assertEqual(self.store.clear_shopping_list_items(target)["deleted"], 0)
        self.assert_code(
            "not_found", lambda: self.store.shopping_list_preview("absent")
        )

    def test_error_translation(self):
        error = store_error_envelope(StoreError("not_found", "fake"))
        self.assertEqual(error["error"]["provider"], "fake")
        with self.assertRaises(ValueError):
            StoreError("arbitrary upstream secret", "fake")
