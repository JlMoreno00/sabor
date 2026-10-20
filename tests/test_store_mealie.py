"""Offline tests for the bounded Mealie read-adapter slice."""
import unittest
from unittest.mock import Mock
import requests

from sabor.store import RecipeSearch, StoreError, SuggestionQuery
from sabor.store.mealie import MealieRecipeStore


class MealieReads(unittest.TestCase):
    def setUp(self):
        self.session = Mock()
        self.store = MealieRecipeStore("https://one.invalid/", "one-token", self.session)
        self.response = self.session.get.return_value
        self.response.status_code = 200

    def test_pagination_and_filters(self):
        self.response.json.return_value = dict(
            items=[dict(id="r", name="Soup", slug="soup", private="ignored")],
            total=8, page=2, per_page=3, total_pages=3)
        page = self.store.search_recipes(RecipeSearch(
            page=2, per_page=3, tag_ids=("t",), category_ids=(),
            food_ids=("f",), tool_ids=("pot",), cookbook_id="book", require_all=True))
        self.assertEqual((page.total, page.page, page.per_page, page.total_pages), (8, 2, 3, 3))
        self.assertEqual(page.items[0].slug, "soup")
        params = self.session.get.call_args.kwargs["params"]
        self.assertEqual(params["tags"], ["t"])
        self.assertEqual(params["categories"], [])
        self.assertEqual(params["foods"], ["f"])
        self.assertEqual(params["tools"], ["pot"])
        self.assertEqual(params["cookbook"], "book")
        self.assertTrue(params["requireAllTools"])

    def test_invalid_pagination_no_io(self):
        for page, size in ((0, 1), (1, 0), (1, 101), (True, 1)):
            with self.assertRaises(StoreError) as caught:
                self.store.search_recipes(RecipeSearch(page=page, per_page=size))
            self.assertEqual(caught.exception.code, "invalid_argument")
        self.session.get.assert_not_called()

    def test_structured_recipe_and_nulls(self):
        self.response.json.return_value = dict(
            slug="soup", recipeServings=4, recipeYield="one pot",
            recipeIngredient=[dict(quantity=None, food=None, unit=dict(id="u", name="g"),
                                   originalText="salt", note="to taste")],
            recipeInstructions=[dict(text="Cook")])
        recipe = self.store.get_recipe("soup")
        assert recipe.recipeIngredient is not None
        assert recipe.recipeInstructions is not None
        assert recipe.recipeIngredient[0].unit is not None
        self.assertIsNone(recipe.recipeIngredient[0].quantity)
        self.assertIsNone(recipe.recipeIngredient[0].food)
        self.assertEqual(recipe.recipeIngredient[0].unit.name, "g")
        self.assertEqual(recipe.recipeIngredient[0].originalText, "salt")
        self.assertEqual(recipe.recipeInstructions[0].text, "Cook")
        self.assertEqual(recipe.recipeServings, 4)
        self.assertIsNone(recipe.tags)

    def test_suggestions(self):
        self.response.json.return_value = dict(items=[dict(
            recipe=dict(slug="soup"), missingFoods=[dict(id="f", name="salt")],
            missingTools=[])])
        rows = self.store.suggest_recipes(SuggestionQuery(("onion",), tools=("pot",)))
        assert rows[0].missingFoods is not None
        self.assertEqual(rows[0].missingFoods[0].name, "salt")
        self.assertEqual(rows[0].missingTools, ())
        self.assertEqual(self.session.get.call_args.args[0],
                         "https://one.invalid/api/recipes/suggestions")

    def test_http_mapping(self):
        for status, code in ((401, "authentication_failed"), (403, "authentication_failed"),
                             (404, "not_found"), (409, "conflict"),
                             (400, "invalid_argument"), (422, "invalid_argument"),
                             (429, "rate_limited"), (500, "upstream_error")):
            self.response.status_code = status
            with self.assertRaises(StoreError) as caught:
                self.store.get_recipe("absent")
            self.assertEqual(caught.exception.code, code)
            self.assertEqual(caught.exception.provider, "mealie")

    def test_exception_and_malformed_mapping(self):
        for exc, code in ((requests.Timeout("secret"), "timeout"),
                          (requests.ConnectionError("secret"), "upstream_error"),
                          (ValueError("secret"), "invalid_response")):
            self.session.get.side_effect = exc
            with self.assertRaises(StoreError) as caught:
                self.store.get_recipe("soup")
            self.assertEqual(str(caught.exception), code)
        self.session.get.side_effect = None
        self.response.json.return_value = dict(recipeIngredient="invalid")
        with self.assertRaises(StoreError) as caught:
            self.store.get_recipe("soup")
        self.assertEqual(caught.exception.code, "invalid_response")

    def test_default_sessions_are_instance_owned(self):
        other = MealieRecipeStore("https://two.invalid", "two-token")
        third = MealieRecipeStore("https://three.invalid", "three-token")
        self.assertIsNot(other.session, third.session)
        self.assertNotEqual(other._headers, third._headers)
        other.session.close()
        third.session.close()
