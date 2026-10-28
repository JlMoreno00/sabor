"""Offline tests for the bounded Mealie read-adapter slice."""
import unittest
from datetime import datetime, timezone
from unittest.mock import Mock, call as mock_call, patch
import asyncio
import inspect
import json
from typing import Any
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

    def test_random_without_seed_uses_daily_utc_seed_across_pages(self):
        self.response.json.return_value = dict(items=[], total=0, page=1, per_page=25, total_pages=0)
        with patch('sabor.store.mealie.datetime', create=True) as clock:
            clock.now.return_value = datetime(2026, 1, 2, 0, 1, tzinfo=timezone.utc)
            for number in (1, 2):
                self.store.search_recipes(RecipeSearch(sort='random', page=number))
            for call in self.session.get.call_args_list:
                params = call.kwargs['params']
                self.assertEqual(params['paginationSeed'], '2026-01-02')
                self.assertEqual((params['orderBy'], params['orderDirection']), ('random', 'asc'))
            self.assertEqual([call.kwargs['params']['page'] for call in self.session.get.call_args_list], [1, 2])
            self.assertEqual(clock.now.call_args_list, [mock_call(timezone.utc)] * 2)

    def test_explicit_seed_is_preserved_and_other_sorts_do_not_send_it(self):
        self.response.json.return_value = dict(items=[], total=0, page=1, per_page=25, total_pages=0)
        for seed in ('', '0', 'explicit-seed'):
            with self.subTest(seed=seed), patch('sabor.store.mealie.datetime') as clock:
                self.store.search_recipes(RecipeSearch(sort='random', seed=seed))
                self.assertEqual(self.session.get.call_args.kwargs['params']['paginationSeed'], seed)
                clock.now.assert_not_called()
        self.store.search_recipes(RecipeSearch(sort='name', seed='unused'))
        params = self.session.get.call_args.kwargs['params']
        self.assertNotIn('paginationSeed', params)
        self.assertEqual((params['orderBy'], params['orderDirection']), ('name', 'asc'))

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


class RecipeRouting(unittest.TestCase):
    def test_old_projection_parity(self):
        from sabor.tools import recipes as r
        # Frozen pre-routing projections, evaluated against the same HTTP fixtures.
        source: dict[str, Any] = dict(id="r", name="Soup", slug="soup", tags=[{"name": "hot", "extra": 1}],
                      recipeIngredient=[dict(quantity=2, unit={"name": "g", "fraction": True},
                                             food={"id": "f", "name": "salt", "aliases": ["sal"]})],
                      recipeInstructions=[dict(text="Cook", ignored=True)])
        old_recipe = {key: source.get(key) for key in (
            "id", "name", "slug", "description", "recipeYield", "recipeServings",
            "totalTime", "prepTime", "cookTime", "tags", "recipeCategory")}
        for field, keys in (("recipeIngredient", ("quantity", "unit", "food", "note", "display",
                                                 "originalText", "referenceId", "title", "heading")),
                            ("recipeInstructions", ("id", "title", "text"))):
            old_recipe[field] = [{key: row.get(key) for key in keys} for row in source[field]]
        summary = dict(id="r", name="Soup", slug="soup")
        search = dict(items=[source], total=1, page=1, per_page=25, total_pages=1)
        suggestions = {"items": [{"recipe": source, "missingFoods": None},
                                 {"recipe": source, "missingTools": [{"name": "pot", "extra": 2}]}]}
        cases = [(r.get_recipe, ("soup",), source, old_recipe),
                 (r.search_recipes, (), search, {**search, "items": [summary]}),
                 (r.suggest_recipes, ([],), suggestions,
                  {"items": [{**summary, "missingFoods": None},
                             {**summary, "missingTools": [{"name": "pot", "extra": 2}]}]})]
        with patch.object(r.config, "MEALIE_API_TOKEN", "test"), patch.object(requests, "get") as get:
            get.return_value.status_code = 200
            for fn, args, fixture, expected in cases:
                get.return_value.json.return_value = fixture
                self.assertEqual(json.dumps(fn(*args)), json.dumps({"ok": True, "data": expected}))

    def test_two_servers_bind_distinct_stores_and_identical_schemas(self):
        from mcp.server.fastmcp import FastMCP
        from sabor.server import register_tools
        from sabor.tools import recipes as r
        servers = [FastMCP("one"), FastMCP("two")]
        for server, name in zip(servers, ("one", "two")):
            register_tools(server, {"SABOR_TOOLSETS": "core", "MEALIE_BASE_URL": f"https://{name}.invalid",
                                    "MEALIE_API_TOKEN": f"{name}-token"})
        response = Mock(status_code=200)
        response.json.return_value = {"slug": "soup"}
        with patch("requests.Session.get", return_value=response) as get, \
                patch("sabor.core.context.default_context", side_effect=AssertionError("global fallback")):
            for server in (*servers, servers[0]):
                tool = server._tool_manager.get_tool("get_recipe")
                assert tool is not None
                self.assertEqual(inspect.signature(tool.fn), inspect.signature(r.get_recipe))
                self.assertEqual(tool.fn.__doc__, r.get_recipe.__doc__)
                asyncio.run(server.call_tool("get_recipe", {"slug": "soup"}))
            self.assertEqual([call.args[0] for call in get.call_args_list],
                             ["https://one.invalid/api/recipes/soup", "https://two.invalid/api/recipes/soup",
                              "https://one.invalid/api/recipes/soup"])
            self.assertEqual([call.kwargs["headers"]["Authorization"] for call in get.call_args_list],
                             ["Bearer one-token", "Bearer two-token", "Bearer one-token"])
        for name in ("get_recipe", "search_recipes", "suggest_recipes"):
            tools = [server._tool_manager.get_tool(name) for server in servers]
            assert all(tool is not None for tool in tools)
            schemas = [tool.parameters for tool in tools if tool is not None]
            self.assertEqual(schemas[0], schemas[1])
            self.assertNotIn("ctx", schemas[0]["properties"])


class MealieWrites(unittest.TestCase):
    def test_patch_allowlist_and_no_put(self):
        from sabor.store import RecipePatch
        session = Mock()
        session.patch.return_value.status_code = 204
        store = MealieRecipeStore("https://one.invalid", "token", session)
        store.update_recipe("s", RecipePatch({"tags": [], "description": None}))
        self.assertEqual(session.patch.call_args.kwargs["json"], {"tags": [], "description": None})
        session.put.assert_not_called()
        with self.assertRaises(StoreError):
            store.update_recipe("s", RecipePatch({"slug": "bad"}))
        self.assertEqual(session.patch.call_count, 1)


    def test_two_instances_create_preview_confirm(self):
        from mcp.server.fastmcp import FastMCP
        from sabor.server import register_tools
        servers = [FastMCP("one"), FastMCP("two")]
        for server, name in zip(servers, ("one", "two")):
            register_tools(server, {"SABOR_TOOLSETS": "core",
                "MEALIE_BASE_URL": f"https://{name}.invalid", "MEALIE_API_TOKEN": name,
                "SABOR_NORMALIZE_IMPORTS": "false"})
        created = Mock(status_code=201, text='"soup"')
        created.json.return_value = "soup"
        read = Mock(status_code=200)
        read.json.return_value = {"name": "Old", "tags": [{"id": "old", "name": "Old"}]}
        with patch("requests.Session.post", return_value=created) as post, \
             patch("requests.Session.get", return_value=read), \
             patch("requests.Session.patch", return_value=Mock(status_code=204)) as patcher, \
             patch("requests.Session.put") as put, \
             patch("sabor.integrations.mealie._normalization_result",
                   side_effect=AssertionError("global normalization")):
            for server in servers:
                tool = server._tool_manager.get_tool("create_recipe")
                assert tool is not None
                result = tool.fn(name="Soup", ingredients=["1 tomato"], tags=["hot"])
                self.assertEqual(result, {"ok": True, "status": 201, "body": '"soup"', "slug": "soup"})
                payload = json.loads(post.call_args.kwargs["json"]["data"])
                self.assertEqual(payload["recipeIngredient"], ["1 tomato"])
                self.assertEqual(payload["keywords"], ["hot"])
                update = server._tool_manager.get_tool("update_recipe")
                assert update is not None
                result = update.fn("soup", {"tags": []})
                self.assertEqual(result["data"]["changes"]["tags"],
                    {"before": [{"id": "old", "name": "Old"}], "after": [], "full_replacement": True})
            patcher.assert_not_called()
            for server in servers:
                update = server._tool_manager.get_tool("update_recipe")
                assert update is not None
                self.assertEqual(update.fn("soup", {"name": "New", "tags": []}, True),
                    {"ok": True, "data": {"slug": "soup", "changed_fields": ["name", "tags"]}})
            self.assertEqual([c.args[0] for c in post.call_args_list],
                ["https://one.invalid/api/recipes/create/html-or-json",
                 "https://two.invalid/api/recipes/create/html-or-json"])
            self.assertEqual([c.kwargs["headers"]["Authorization"] for c in patcher.call_args_list],
                ["Bearer one", "Bearer two"])
            self.assertTrue(all(c.kwargs["json"] == {"name": "New", "tags": []}
                                for c in patcher.call_args_list))
            put.assert_not_called()

    def test_write_errors_are_typed(self):
        from sabor.store import RecipeDraft, RecipePatch
        session = Mock()
        store = MealieRecipeStore("https://one.invalid", "token", session)
        session.post.return_value.status_code = 401
        with self.assertRaises(StoreError) as caught:
            store.create_recipe(RecipeDraft(name="Soup"))
        self.assertEqual(caught.exception.code, "authentication_failed")
        session.patch.side_effect = requests.Timeout("secret")
        with self.assertRaises(StoreError) as caught:
            store.update_recipe("s", RecipePatch({"name": "Soup"}))
        self.assertEqual(caught.exception.code, "timeout")


class LegacyErrorCodes(unittest.TestCase):
    """Legacy Mealie 404s map to not_found at the port; tools keep the legacy dict."""

    def setUp(self):
        self.session = Mock()
        self.session.get.return_value = Mock(status_code=404)
        self.store = MealieRecipeStore("https://one.invalid/", "one-token", self.session)

    def test_missing_recipe_is_not_found_and_keeps_legacy_result(self):
        for call in (lambda: self.store.add_recipe_comment("absent", "No"),
                     lambda: self.store.log_cooking_event("absent", "No", None)):
            with self.assertRaises(StoreError) as caught:
                call()
            self.assertEqual(caught.exception.code, "not_found")
            self.assertEqual(getattr(caught.exception, "result")["status"], 404)
        self.session.post.assert_not_called()

    def test_other_failures_stay_upstream_error(self):
        self.session.get.return_value = Mock(status_code=500)
        with self.assertRaises(StoreError) as caught:
            self.store.add_recipe_comment("soup", "No")
        self.assertEqual(caught.exception.code, "upstream_error")
