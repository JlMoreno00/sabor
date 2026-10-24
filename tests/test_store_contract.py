"""Reusable port contract: override make_store for an HTTP-backed adapter fixture."""
import unittest
from dataclasses import FrozenInstanceError
from typing import TYPE_CHECKING
from functools import wraps

ContractAssertions = unittest.TestCase if TYPE_CHECKING else object

from sabor.store import (Recipe, RecipeSearch, RecipePatch, Ingredient, Instruction,
                         ItemDraft, MealEntryDraft, DateRange, RecipeOccurrence,
                         StoreError, store_error_envelope)
from sabor.store.fake import FakeRecipeStore


CAPABILITIES = frozenset((
    "recipes_read", "recipes_write", "search_sort", "mealplan", "shopping",
    "activity", "suggestions", "rating_sort", "occurrence_ids",
))


def requires(*groups, optional=()):
    def decorate(test):
        @wraps(test)
        def run(self):
            for group in groups:
                if group not in self.capabilities:
                    self.skipTest(f"capability {group} not supported by {self.store.id}")
            return test(self)
        setattr(run, "required_capabilities", frozenset(groups))
        setattr(run, "optional_capabilities", frozenset(optional))
        return run
    return decorate


class CapabilityVocabulary(unittest.TestCase):
    def test_vocabulary(self):
        used = frozenset(group for name in dir(StoreContractMixin)
                         if name.startswith("test_")
                         for attribute in ("required_capabilities", "optional_capabilities")
                         for group in getattr(getattr(StoreContractMixin, name), attribute, ()))
        self.assertEqual(used, CAPABILITIES)
        for name in dir(StoreContractMixin):
            if name.startswith("test_"):
                self.assertTrue(getattr(getattr(StoreContractMixin, name),
                                        "required_capabilities", ()), name)


class StoreContractMixin(ContractAssertions):
    capabilities: frozenset[str] = frozenset()

    def absent_id(self, kind) -> str:
        return "absent"

    def make_store(self):
        raise NotImplementedError

    def seed_ratings(self):
        raise NotImplementedError

    def setUp(self):
        self.store = self.make_store()
        if "recipes_write" in self.capabilities:
            self.seed()

    def seed(self):
        self.ref = self.store.create_recipe(Recipe(
            id="r", slug="soup", name="Soup", description="Keep",
            recipeIngredient=(Ingredient(quantity=None),),
            recipeInstructions=(Instruction(text="Cook"),)))

    def assert_code(self, code, call):
        with self.assertRaises(StoreError) as caught:
            call()
        self.assertEqual(caught.exception.code, code)

    @requires("recipes_read")
    def test_pagination(self):
        for page, size in ((0, 1), (1, 0), (1, 101), (True, 1)):
            self.assert_code("invalid_argument", lambda: self.store.search_recipes(
                RecipeSearch(page=page, per_page=size)))
        self.assertEqual(self.store.search_recipes(RecipeSearch(per_page=1)).total, 1)
        self.assertEqual(self.store.search_recipes(RecipeSearch(page=2)).items, ())

    @requires("recipes_read", "recipes_write", "search_sort", "activity", optional=("rating_sort",))
    def test_search_sort_orders(self):
        self.store.create_recipe(Recipe(slug="apple", name="Apple"))
        self.store.create_recipe(Recipe(slug="pear", name="Pear"))
        def slugs(**kwargs):
            return [r.slug for r in self.store.search_recipes(RecipeSearch(**kwargs)).items]
        self.assertEqual(slugs(sort="created_at"), ["soup", "apple", "pear"])
        self.assertEqual(slugs(sort="created_at", direction="desc"), ["pear", "apple", "soup"])
        self.store.update_recipe("soup", RecipePatch({"description": "Updated"}))
        self.assertEqual(slugs(sort="updated_at"), ["apple", "pear", "soup"])
        self.store.log_cooking_event("pear", "Cooked", None)
        self.store.log_cooking_event("soup", "Cooked", None)
        self.store.log_cooking_event("pear", "Again", None)
        self.assertEqual(slugs(sort="last_made"), ["soup", "pear", "apple"])
        self.assertEqual(slugs(sort="last_made", direction="desc"), ["pear", "soup", "apple"])
        if "rating_sort" in self.capabilities:
            self.seed_ratings()
            self.assertEqual(slugs(sort="rating", direction="desc"), ["pear", "soup", "apple"])
        else:
            self.assert_code("unsupported_operation", lambda: slugs(sort="rating"))
        self.assert_code("invalid_argument", lambda: slugs(sort="unknown"))
        order = slugs(sort="random", seed="2026-01-01")
        self.assertEqual(order, slugs(sort="random", seed="2026-01-01", direction="desc"))
        self.assertEqual(order, [slug for page in (1, 2, 3) for slug in
                         slugs(sort="random", seed="2026-01-01", page=page, per_page=1)])
        self.assertEqual(slugs(sort="random", seed="x", query="Pear"), ["pear"])

    @requires("recipes_read", "shopping")
    def test_missing(self):
        self.assert_code("not_found", lambda: self.store.get_recipe(self.absent_id("recipe")))
        self.assert_code("not_found", lambda: self.store.get_shopping_items(self.absent_id("list")))

    @requires("recipes_read", "recipes_write")
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

    @requires("shopping")
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

    @requires("recipes_read", "activity")
    def test_store_tags_and_activity(self):
        tag = self.store.get_or_create_tag("Dinner")
        self.assertEqual(self.store.get_or_create_tag("Dinner").id, tag.id)
        self.store.tag_recipe(self.ref.slug, ["Dinner", "Dinner"])
        self.assertEqual(len(self.store.get_recipe(self.ref.slug).tags), 1)
        self.assertTrue(self.store.add_recipe_comment(self.ref.slug, "Good"))
        self.assertTrue(self.store.log_cooking_event(self.ref.slug, "Cooked", None))
        self.assert_code("not_found", lambda: self.store.add_recipe_comment(self.absent_id("recipe"), "No"))

    @requires("suggestions")
    def test_store_suggestions(self):
        from sabor.store import SuggestionQuery
        self.assertEqual(len(self.store.suggest_recipes(SuggestionQuery(()))), 1)
        for limit in (0, True):
            self.assert_code("invalid_argument", lambda: self.store.suggest_recipes(
                SuggestionQuery((), limit=limit)))

    @requires("shopping")
    def test_store_preview_and_clear(self):
        target = self.store.create_shopping_list("Week").id
        self.store.add_shopping_items(target, [ItemDraft(note="salt")])
        self.assertEqual(self.store.shopping_list_preview(target)["count"], 1)
        self.assertEqual(self.store.clear_shopping_list_items(target)["deleted"], 1)
        self.assertEqual(self.store.clear_shopping_list_items(target)["deleted"], 0)
        self.assert_code("not_found", lambda: self.store.shopping_list_preview(self.absent_id("list")))

    @requires("mealplan", "shopping", "recipes_read", optional=("occurrence_ids",))
    def test_repeated_occurrences(self):
        for day in ("2026-01-01", "2026-01-02"):
            self.store.set_mealplan_entry(MealEntryDraft(day, "dinner", "Soup", self.ref.slug))
        entries = self.store.get_mealplan(DateRange("2026-01-01", "2026-01-02"))
        self.assertEqual(len(entries), 2)
        target = self.store.create_shopping_list("Week").id
        result = self.store.add_recipe_occurrences(target, [RecipeOccurrence(self.ref.slug, 2) for e in entries])
        if "occurrence_ids" in self.capabilities:
            self.assertEqual(len(result.changed_ids), 2)
        else:
            self.assertEqual(result.changed_ids, ())
        self.assertEqual(len(self.store.get_shopping_items(target).items), 2)


class SqliteStoreContract(StoreContractMixin, unittest.TestCase):
    capabilities = CAPABILITIES - {"rating_sort"}

    def make_store(self):
        from tempfile import TemporaryDirectory
        from pathlib import Path
        from sabor.store.sqlite import SqliteRecipeStore
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        return SqliteRecipeStore(Path(temporary.name) / "store.db")


class FakeStoreContract(StoreContractMixin, unittest.TestCase):
    capabilities = CAPABILITIES - {"rating_sort"}

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
            "not_found", lambda: self.store.add_recipe_comment(self.absent_id("recipe"), "No")
        )
        self.assert_code(
            "not_found", lambda: self.store.log_cooking_event(self.absent_id("recipe"), "No", None)
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
            "not_found", lambda: self.store.shopping_list_preview(self.absent_id("list"))
        )

    def test_error_translation(self):
        error = store_error_envelope(StoreError("not_found", "fake"))
        self.assertEqual(error["error"]["provider"], "fake")
        with self.assertRaises(ValueError):
            StoreError("arbitrary upstream secret", "fake")
