"""Shopping item contracts with no network access."""
import unittest
import requests
from typing import Any, cast
from test_store_shopping_items import item_store_class  # pyright: ignore[reportMissingImports]
from unittest.mock import Mock, patch

from sabor.tools import shopping_items as tools

LIST = "11111111-1111-4111-8111-111111111111"
ITEM = {"id": "a", "shoppingListId": LIST, "checked": False,
        "quantity": 2, "note": None, "display": "2 apples",
        "food": {"id": "f", "name": "apple", "extra": 1}, "unit": None,
        "position": 3, "foodId": "f", "recipeReferences": []}


class ShoppingItemsTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(patch.stopall)
        patch('sabor.store.mealie.MealieRecipeStore', item_store_class()).start()
        patch('sabor.config.MEALIE_BASE_URL', 'https://test.invalid').start()
        patch('sabor.config.MEALIE_API_TOKEN', 'test-token').start()
        self.patch = patch.multiple(requests, get=Mock(), put=Mock(), delete=Mock())
        self.patch.start()
        self.http = cast(Any, requests)
        self.addCleanup(self.patch.stop)
        self.http.get.return_value = Mock(status_code=200)
        self.http.get.return_value.json.return_value = {"listItems": [dict(ITEM)]}
        self.http.put.return_value.status_code = 200
        self.http.delete.return_value.status_code = 200

    def test_normalization_and_nulls(self):
        data = tools.get_shopping_list_items(LIST)["data"]
        self.assertEqual(data["items"], [{"id": "a", "list_id": LIST,
            "checked": False, "quantity": 2, "note": None, "display": "2 apples",
            "food": {"id": "f", "name": "apple"}, "unit": None,
            "recipe_references": []}])
        self.assertEqual(data["total"], 1)
        self.assertTrue(self.http.get.call_args.args[0].endswith("/lists/" + LIST))

    def test_checked_filter_before_paging(self):
        self.http.get.return_value.json.return_value["listItems"] += [dict(ITEM, id="b", checked=True)]
        data = tools.get_shopping_list_items(LIST, checked=True, per_page=1)["data"]
        self.assertEqual([x["id"] for x in data["items"]], ["b"])
        self.assertEqual((data["total"], data["total_pages"]), (1, 1))

    def test_uuid_validation(self):
        self.assertEqual(tools.get_shopping_list_items('bad" or true')["error"]["code"], "invalid_argument")
        self.http.get.assert_not_called()

    def test_ownership_zero_writes(self):
        for fn in (tools.check_shopping_list_items, tools.remove_shopping_list_items):
            result = fn(LIST, ["a", "foreign"])
            self.assertEqual(result["error"]["code"], "invalid_argument")
            self.assertIn("foreign", str(result))
        self.http.put.assert_not_called()
        self.http.delete.assert_not_called()

    def test_bulk_payload(self):
        result = tools.check_shopping_list_items(LIST, ["a"])
        self.assertEqual(result["data"], {"changed_ids": ["a"], "checked": True})
        payload = self.http.put.call_args.kwargs["json"]
        self.assertEqual(payload, [{k: v for k, v in dict(ITEM, checked=True).items()
                                    if k not in ("food", "unit")}])

    def test_preview_no_delete(self):
        data = tools.remove_shopping_list_items(LIST, ["a"])["data"]
        self.assertTrue(data["preview"])
        self.assertEqual(data["count"], 1)
        self.assertEqual(data["items"][0]["id"], "a")
        self.http.delete.assert_not_called()

    def test_confirm_exact_ids(self):
        result = tools.remove_shopping_list_items(LIST, ["a"], confirm=True)
        self.assertEqual(result["data"]["removed_ids"], ["a"])
        self.assertEqual(self.http.delete.call_args.kwargs["params"], {"ids": ["a"]})

    def test_errors_redacted(self):
        self.http.get.return_value.status_code = 500
        self.http.get.return_value.text = "SECRET upstream body"
        result = tools.get_shopping_list_items(LIST)
        self.assertEqual(result["error"]["code"], "upstream_error")
        self.assertNotIn("SECRET", str(result))
        self.http.get.side_effect = ValueError("SECRET exception")
        result = tools.get_shopping_list_items(LIST)
        self.assertEqual(result["error"]["code"], "invalid_response")
        self.assertNotIn("SECRET", str(result))

    def test_invalid_paging_empty_ids_and_foreign_response(self):
        for kwargs in ({"page": 0}, {"per_page": 101}):
            self.assertFalse(tools.get_shopping_list_items(LIST, **cast(Any, kwargs))["ok"])
        self.assertFalse(tools.check_shopping_list_items(LIST, [])["ok"])
        self.http.get.return_value.json.return_value = {"listItems": [dict(ITEM, shoppingListId="other")]}
        self.assertFalse(tools.check_shopping_list_items(LIST, ["a"])["ok"])
        self.http.put.assert_not_called()
