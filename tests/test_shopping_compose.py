"""Offline composition contracts; never call Mealie."""
import unittest
from unittest.mock import Mock, patch
from sabor.tools import shopping_items as mod

LIST = "00000000-0000-4000-8000-000000000001"
RID = "00000000-0000-4000-8000-000000000002"


def response(data=None, status=200):
    return Mock(status_code=status, json=Mock(return_value=data), text="SECRET BODY")


def page(entries, number=1, total=1, count=None):
    return response(dict(items=entries, page=number, per_page=max(1, len(entries)),
                         total=len(entries) if count is None else count, total_pages=total))


class ComposeTests(unittest.TestCase):
    def setUp(self):
        self.get = patch.object(mod.requests, "get").start()
        self.post = patch.object(mod.requests, "post").start()
        self.addCleanup(patch.stopall)
        self.post.return_value = response({"createdItems": [{"id": "item"}]})

    def call(self, **kw):
        return mod.add_to_shopping_list(LIST, **kw)

    def test_source_exclusivity(self):
        for kw in ({}, {"items": []}, {"items": ["x"], "recipe_slugs": ["r"]},
                   {"items": ["x"], "mealplan_start": "2026-01-01"}):
            with self.subTest(kw=kw):
                self.assertEqual(self.call(**kw)["error"]["code"], "invalid_argument")
        self.post.assert_not_called()

    def test_validation(self):
        for kw in ({"servings_multiplier": x, "items": ["x"]} for x in (0, 21, float("nan"), True)):
            self.assertFalse(self.call(**kw)["ok"])
        for start, end in (("2026-02-30", "2026-03-01"), ("2026-02-02", "2026-02-01")):
            self.assertFalse(self.call(mealplan_start=start, mealplan_end=end)["ok"])
        self.assertFalse(mod.add_to_shopping_list("bad", items=["x"])["ok"])
        self.get.assert_not_called()
        self.post.assert_not_called()

    def test_items_payload(self):
        self.assertTrue(self.call(items=["2 kg rice"])["ok"])
        self.assertEqual(self.post.call_args.kwargs["json"],
                         [{"shoppingListId": LIST, "note": "2 kg rice", "quantity": 1}])
        self.assertTrue(self.post.call_args.args[0].endswith("/items/create-bulk"))

    def test_unresolved_slug_prevents_writes(self):
        self.get.side_effect = [response({"id": RID}), response(status=404)]
        result = self.call(recipe_slugs=["rice", "missing"])
        self.assertEqual(result["data"]["unresolved_slugs"], ["missing"])
        self.post.assert_not_called()

    def test_recipe_bulk_multiplier(self):
        self.get.return_value = response({"id": RID})
        result = self.call(recipe_slugs=["rice"], servings_multiplier=2.5)
        self.assertTrue(result["ok"])
        self.assertEqual(self.post.call_args.kwargs["json"],
                         [{"recipeId": RID, "recipeIncrementQuantity": 2.5}])
        self.assertTrue(self.post.call_args.args[0].endswith(f"/{LIST}/recipe"))

    def mealplan(self):
        return self.call(mealplan_start="2026-01-01", mealplan_end="2026-01-07")

    def test_mealplan_pagination(self):
        entry = {"id": 1, "recipe": {"id": RID, "slug": "rice"}}
        self.get.side_effect = [page([entry], 1, 2, 2), page([entry], 2, 2, 2)]
        self.assertTrue(self.mealplan()["ok"])
        self.assertEqual(self.get.call_args.kwargs["params"]["page"], 2)
        self.assertEqual(self.get.call_args.kwargs["params"]["start_date"], "2026-01-01")

    def test_text_entry_skipping(self):
        self.get.return_value = page([{"id": 4, "recipe": None}])
        result = self.mealplan()["data"]
        self.assertEqual(result["skipped"], [{"entry": 4, "reason": "text_only_entry"}])
        self.post.assert_not_called()

    def test_repeated_occurrences(self):
        entry = {"id": 1, "recipe": {"id": RID, "slug": "rice"}}
        self.get.return_value = page([entry, {**entry, "id": 2}])
        result = self.mealplan()["data"]
        self.assertEqual(result["recipes"][0]["occurrences"], 2)
        self.assertEqual(len(self.post.call_args.kwargs["json"]), 2)

    def test_safe_errors(self):
        for status in (401, 429, 500):
            self.post.return_value = response(status=status)
            result = self.call(items=["x"])
            self.assertFalse(result["ok"])
            self.assertNotIn("SECRET", str(result))
        self.post.side_effect = ValueError("SECRET BODY")
        self.assertNotIn("SECRET", str(self.call(items=["x"])))

    def test_partial_item_result(self):
        result = self.call(items=["rice", "salt"])
        self.assertEqual(result["error"]["code"], "partial_failure")
        self.assertEqual(result["data"]["completed"], ["item"])

    def test_later_page_failure_prevents_write(self):
        entry = {"id": 1, "recipe": {"id": RID, "slug": "rice"}}
        self.get.side_effect = [page([entry], 1, 2, 2), response(status=500)]
        self.assertEqual(self.mealplan()["error"]["code"], "upstream_error")
        self.post.assert_not_called()

    def test_invalid_pagination_no_write(self):
        self.get.return_value = page([], 2, 3)
        self.assertEqual(self.mealplan()["error"]["code"], "invalid_response")
        self.post.assert_not_called()
