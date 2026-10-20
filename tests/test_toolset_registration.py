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
        self.assertEqual(len(tools), 62)
        encoded = json.dumps(discovery)
        for key in (*MEALIE, *GEMINI, *BRING, 'SPOONACULAR_API_KEY'):
            self.assertNotIn(env[key], encoded)

    def test_mealie_defaults(self):
        tools, discovery = self.register(MEALIE)
        from sabor.core.toolsets import catalog
        core = {fn.__name__ for group, fn, requires in catalog if group == 'core'}
        self.assertEqual(len(core), 24)
        self.assertEqual(set(tools), core | {'scrape_recipe', 'import_recipe_to_mealie', 'import_recipe',
                                            'sabor_capabilities'})
        self.assertIn({'tool': 'analyze_video_recipes', 'reason': 'missing_configuration'},
                      discovery['data']['warnings'])

    def test_mealie_and_gemini_defaults(self):
        tools, _ = self.register({**MEALIE, **GEMINI})
        self.assertEqual(len(tools), 30)
        self.assertIn('analyze_video_recipes', tools)
        self.assertIn('import_video_recipes_to_mealie', tools)

    def test_empty_selection(self):
        tools, _ = self.register({**MEALIE, 'SABOR_TOOLSETS': ''})
        self.assertEqual(set(tools), {'sabor_capabilities'})

    def test_unknown_selection(self):
        with self.assertRaises(ConfigError):
            self.register({'SABOR_TOOLSETS': 'unknown'})

    def test_gemini_only(self):
        tools, discovery = self.register(GEMINI)
        self.assertEqual(set(tools), {'sabor_capabilities', 'analyze_video_recipes'})
        self.assertIn({'tool': 'import_video_recipes_to_mealie',
                       'reason': 'missing_configuration'}, discovery['data']['warnings'])

    def test_bring_only(self):
        tools, _ = self.register(BRING)
        self.assertEqual(set(tools), {'sabor_capabilities', 'bring_get_lists',
                                     'bring_sync_list', 'bring_add_items'})
