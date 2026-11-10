import unittest
from typing import Any, cast
from unittest.mock import Mock, patch
from sabor.core.config import load_settings
from sabor.core.context import build_context
from sabor.core.capabilities import describe
from sabor.core.toolsets import bind_context
from sabor.importing.tools import import_recipe
from sabor.store.mealie import MealieRecipeStore

PAGE = ('<script type="application/ld+json">{"@context": "https://schema.org", "@type": "Recipe", '
        '"name": "Soup", "recipeIngredient": ["salt"], "recipeInstructions": ["Mix"]}</script>').encode()


class ImportContextTests(unittest.TestCase):
    def context(self, host, duplicate=False):
        cfg = load_settings({'MEALIE_BASE_URL': host, 'MEALIE_API_TOKEN': 'test'})
        session = Mock()
        session.post.return_value = Mock(status_code=200, json=lambda: {'name': 'Soup'})
        session.get.return_value = Mock(status_code=200, json=lambda: {
            'items': [{'name': ' Soup ', 'slug': 'old'}] if duplicate else [],
            'total': int(duplicate), 'page': 1, 'per_page': 100, 'total_pages': 1})
        ctx = build_context(cfg, store_factory=lambda s: MealieRecipeStore(host, 'test', session=session))
        return ctx, session

    @patch('sabor.importing.tools.html_bytes', return_value=(PAGE, 'text/html'))
    def test_two_instances_preview_and_duplicate(self, _page):
        # Sabor's own extractor reads the page; Mealie only receives CRUD writes (#102).
        for host in ('http://one', 'http://two'):
            ctx, session = self.context(host)
            fn = cast(Any, bind_context(import_recipe, ctx))
            self.assertTrue(fn('https://example.org')['data']['preview'])
            session.post.assert_not_called()
            session.post.return_value = Mock(status_code=201, json=lambda: 'soup', text='"soup"')
            session.patch.return_value = Mock(status_code=200)
            self.assertEqual(fn('https://example.org', dry_run=False)['data']['created'], ['soup'])
            self.assertEqual(session.post.call_args.args[0], host + '/api/recipes')
            ctx, session = self.context(host, True)
            result = cast(Any, bind_context(import_recipe, ctx))('https://example.org', dry_run=False)
            self.assertEqual(result['data']['skips'], [{'name': 'Soup', 'slug': 'old', 'reason': 'duplicate'}])
            session.post.assert_not_called()

    @patch('sabor.importing.tools.html_bytes', return_value=(PAGE, 'text/html'))
    def test_direct_parity(self, _page):
        ctx, session = self.context('http://one')
        with patch('sabor.core.context.default_context', return_value=ctx):
            self.assertEqual(import_recipe('https://example.org'),
                             cast(Any, bind_context(import_recipe, ctx))('https://example.org'))
        session.post.assert_not_called()

    def test_import_discovery(self):
        ctx, _ = self.context('http://one')
        rows = describe(ctx, [], 'test')['data']['capabilities']['import']['adapters']
        self.assertTrue(any('url' in r['kinds'] and 'preview' in r['operations'] for r in rows))
        # #102: Mealie's image import needed Mealie's OpenAI configuration; it is gone.
        self.assertFalse(any('image' in r['kinds'] for r in rows))
        # With Mealie too, Sabor's own importers are the only ones (#102).
        self.assertEqual(rows, [{'id': 'sabor', 'kinds': ['url'], 'operations': ['commit', 'preview'],
                                 'limitations': []},
                                {'id': 'sabor', 'kinds': ['video'], 'operations': ['bundle'],
                                 'limitations': ['local_files_require_stdio']}])
