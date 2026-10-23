"""Registered creation retains import normalization with instance configuration."""
import unittest
import requests
from unittest.mock import Mock, patch
from mcp.server.fastmcp import FastMCP
from sabor.server import register_tools


class NormalizationContext(unittest.TestCase):
    def server(self, name, enabled="true", confidence="0.75"):
        server = FastMCP(name)
        register_tools(server, {"SABOR_TOOLSETS": "core", "MEALIE_BASE_URL": f"https://{name}.invalid",
            "MEALIE_API_TOKEN": name, "SABOR_NORMALIZE_IMPORTS": enabled,
            "SABOR_INGREDIENT_PARSER": name, "SABOR_NORMALIZE_CONFIDENCE": confidence})
        return server

    def test_parity_and_nutrition_bridge(self):
        from sabor.tools import recipes as r
        tool = self.server("one")._tool_manager.get_tool("create_recipe")
        assert tool is not None
        created = Mock(status_code=201, text='"soup"')
        created.json.return_value = "soup"
        read = Mock(status_code=503)
        with patch("requests.Session.post", return_value=created), \
             patch("requests.Session.get", return_value=read), \
             patch.object(requests, "post", return_value=created), \
             patch.object(requests, "get", return_value=read), \
             patch.object(r.config, "MEALIE_API_TOKEN", "one"), \
             patch.object(r.config, "SABOR_NORMALIZE_IMPORTS", True), \
             patch.object(r.mealie.nutrition, "_nutrition_after_import", side_effect=lambda result, slug: result) as hook:
            self.assertEqual(tool.fn("Soup", ingredients=["salt"]),
                             r.create_recipe("Soup", ingredients=["salt"]))
            self.assertEqual(hook.call_count, 2)
            tool.fn("Skeleton")
            self.assertEqual(hook.call_count, 2)

    def test_instances_and_disabled(self):
        servers = [self.server("one"), self.server("two", confidence="0.95"),
                   self.server("off", "false")]
        created = Mock(status_code=201, text='"soup"')
        created.json.return_value = "soup"
        parsed = Mock(status_code=200)
        parsed.json.return_value = [{"ingredient": {"food": {"id": "food"}}, "confidence": 0.8}]
        recipe = Mock(status_code=200)
        recipe.json.return_value = {"recipeIngredient": [{"originalText": "salt"}]}
        with patch("requests.Session.post", side_effect=lambda url, **kw: parsed if "/parser/" in url else created) as post, \
             patch("requests.Session.get", return_value=recipe), \
             patch("requests.Session.patch", return_value=Mock(status_code=200)) as patcher:
            results = []
            for server in servers:
                tool = server._tool_manager.get_tool("create_recipe")
                assert tool is not None
                results.append(tool.fn("Soup", ingredients=["salt"]))
        self.assertEqual(results[0]["normalization"]["accepted"], 1)
        self.assertEqual(results[1]["normalization"]["status"], "retained_originals")
        self.assertNotIn("normalization", results[2])
        calls = [c for c in post.call_args_list if "/parser/" in c.args[0]]
        self.assertEqual([c.args[0] for c in calls], [f"https://{n}.invalid/api/parser/ingredients" for n in ("one", "two")])
        self.assertEqual([c.kwargs["headers"]["Authorization"] for c in calls], ["Bearer one", "Bearer two"])
        self.assertEqual([c.kwargs["json"]["parser"] for c in calls], ["one", "two"])
        self.assertEqual(patcher.call_count, 1)
