import unittest
from typing import Any, cast
from unittest.mock import Mock
from sabor.core.config import load_settings
from sabor.core.context import build_context
from sabor.core.capabilities import describe
from sabor.core.toolsets import bind_context
from sabor.importing.tools import import_recipe
from sabor.store.mealie import MealieRecipeStore


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

    def test_two_instances_preview_and_duplicate(self):
        for host in ('http://one', 'http://two'):
            ctx, session = self.context(host)
            fn = cast(Any, bind_context(import_recipe, ctx))
            self.assertTrue(fn('https://example.org')['data']['preview'])
            self.assertEqual(session.post.call_count, 1)
            self.assertEqual(session.post.call_args.args[0], host + '/api/recipes/test-scrape-url')
            ctx, session = self.context(host, True)
            result = cast(Any, bind_context(import_recipe, ctx))('https://example.org', dry_run=False)
            self.assertEqual(result['error']['code'], 'conflict')
            self.assertEqual(session.post.call_count, 1)

    def test_direct_parity(self):
        from unittest.mock import patch
        ctx, session = self.context('http://one')
        with patch('sabor.core.context.default_context', return_value=ctx):
            self.assertEqual(import_recipe('https://example.org'),
                             cast(Any, bind_context(import_recipe, ctx))('https://example.org'))
        self.assertEqual(session.post.call_count, 2)

    def test_native_discovery(self):
        ctx, _ = self.context('http://one')
        rows = describe(ctx, [], 'test')['data']['capabilities']['import']['adapters']
        self.assertTrue(any('url' in r['kinds'] and 'preview' in r['operations'] for r in rows))
        self.assertTrue(any('image' in r['kinds'] and 'preview' not in r['operations'] for r in rows))
