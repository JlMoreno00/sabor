"""Configuration-only registration, with isolated real FastMCP instances."""
import json
import unittest

from mcp.server.fastmcp import FastMCP
from sabor.core.config import ConfigError, TOOLSETS
from sabor.server import register_tools

MEALIE = {'MEALIE_BASE_URL': 'https://private-mealie.example',
          'MEALIE_API_TOKEN': 'secret-mealie'}
GEMINI = {'GEMINI_API_KEY': 'secret-gemini',
          'VIDEO_RECIPE_GEMINI_MODEL': 'private-model'}
BRING = {'BRING_EMAIL': 'private@example.com', 'BRING_PASSWORD': 'secret-bring'}


class RegistrationTests(unittest.TestCase):
    def register(self, env):
        server = FastMCP('test')
        env = {'SABOR_STORE': 'mealie', **env}
        register_tools(server, env)
        tools = {t.name: t for t in server._tool_manager.list_tools()}
        before = list(tools)
        register_tools(server, env)
        self.assertEqual(before, [t.name for t in server._tool_manager.list_tools()])
        discovery = tools['sabor_capabilities'].fn()
        self.assertEqual(set(discovery['data']['registered_tools']), set(tools))
        return tools, discovery

    def test_full_configuration(self):
        env = {**MEALIE, **GEMINI, **BRING, 'SPOONACULAR_API_KEY': 'secret-spoon',
               'SABOR_MERCADONA_ENABLED': 'true', 'SABOR_TOOLSETS': ','.join(TOOLSETS)}
        tools, discovery = self.register(env)
        self.assertEqual(len(tools), 53)
        encoded = json.dumps(discovery)
        for key in (*MEALIE, *GEMINI, *BRING, 'SPOONACULAR_API_KEY'):
            self.assertNotIn(env[key], encoded)

    def test_pricing_gates_and_legacy_promotions(self):
        for enabled, selected, count in [(False, 'pricing', 0), (True, '', 0),
                                          (True, 'pricing', 3), (True, 'legacy_pricing', 2)]:
            tools, _ = self.register({'SABOR_MERCADONA_ENABLED': str(enabled).lower(),
                                      'SABOR_TOOLSETS': selected})
            self.assertEqual(len(tools) - 1, count)

    def test_mealie_defaults(self):
        tools, discovery = self.register(MEALIE)
        from sabor.core.toolsets import catalog
        core = {fn.__name__ for group, fn, requires in catalog if group == 'core'}
        self.assertEqual(len(core), 22)
        self.assertEqual(set(tools), core | {'import_recipe', 'sabor_capabilities'})

    def test_mealie_and_gemini_defaults(self):
        tools, _ = self.register({**MEALIE, **GEMINI})
        self.assertEqual(len(tools), 24)
        self.assertIn('import_recipe', tools)

    def test_empty_selection(self):
        tools, _ = self.register({**MEALIE, 'SABOR_TOOLSETS': ''})
        self.assertEqual(set(tools), {'sabor_capabilities'})

    def test_unknown_selection(self):
        with self.assertRaises(ConfigError):
            self.register({'SABOR_TOOLSETS': 'unknown'})

    def test_gemini_only(self):
        tools, discovery = self.register(GEMINI)
        self.assertEqual(set(tools), {'sabor_capabilities', 'import_recipe'})

    def test_bring_only(self):
        tools, _ = self.register(BRING)
        self.assertEqual(set(tools), {'sabor_capabilities'})

    def test_removed_names_never_registered(self):
        removed = ["mercadona_search_products", "mercadona_get_product_price", "mercadona_estimate_recipe_cost", "mercadona_estimate_shopping_list_cost", "scrape_recipe","import_recipe_to_mealie","analyze_video_recipes","import_video_recipes_to_mealie","add_items_to_shopping_list","add_recipe_ingredients_to_list","bring_get_lists","bring_sync_list","bring_add_items","spoonacular_search_by_nutrients"]
        # Exhaust all toolset selections with every provider configured.
        from itertools import combinations
        for size in range(len(TOOLSETS) + 1):
            for selected in combinations(TOOLSETS, size):
                with self.subTest(selected=selected):
                    tools, _ = self.register({**MEALIE, **GEMINI, **BRING,
                        'SPOONACULAR_API_KEY': 'secret-spoon',
                        'SABOR_MERCADONA_ENABLED': 'true',
                        'SABOR_TOOLSETS': ','.join(selected)})
                    self.assertFalse(set(removed) & tools.keys())
