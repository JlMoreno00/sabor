"""Application ownership and static discovery, without live providers."""
import unittest
from dataclasses import FrozenInstanceError
from unittest.mock import patch
from typing import Any, cast

from sabor.core.config import load_settings
from sabor.core.context import build_context
from sabor.core.capabilities import describe


class Adapter:
    id = 'mercadona'
    operations = frozenset({'search_products', 'get_quote'})

    def __init__(self, settings):
        self.cache = {}


class ContextTests(unittest.TestCase):
    def test_construction_is_local_and_immutable(self):
        with patch('requests.sessions.Session.request', side_effect=AssertionError('network')):
            app = build_context(load_settings({}))
        self.assertIsNone(app.store)
        self.assertEqual(app.pricing, ())
        with self.assertRaises(FrozenInstanceError):
            setattr(app, 'store', object())

    def test_instances_own_providers(self):
        def make(token):
            return build_context(load_settings({'MEALIE_API_TOKEN': token,
                'SABOR_MERCADONA_ENABLED': 'true'}),
                store_factory=cast(Any, Adapter), pricing_factories=(cast(Any, Adapter),))
        first, second = make('first'), make('second')
        cast(Any, first.store).cache['private'] = 1
        cast(Any, first.pricing[0]).cache['private'] = 2
        self.assertEqual(cast(Any, second.store).cache, {})
        self.assertEqual(cast(Any, second.pricing[0]).cache, {})
        self.assertIs(first.registry.recipe_store, first.store)
        self.assertEqual(first.registry.pricings, first.pricing)

    def test_discovery_redacts_and_preserves_compatibility(self):
        app = build_context(load_settings({'MEALIE_BASE_URL': 'https://private.invalid',
            'MEALIE_API_TOKEN': 'secret-token'}))
        data = describe(app, ['sabor_capabilities'], 'test')['data']
        self.assertTrue(data['mealie']['configured'])
        self.assertFalse(data['capabilities']['recipe_store']['configured'])
        self.assertEqual(data['capabilities']['pricing']['adapters'], [])
        self.assertNotIn('secret-token', repr(data))
        self.assertNotIn('private.invalid', repr(data))

    def test_pricing_requires_adapter_and_mercadona_flag(self):
        for env, factories, enabled in [
            ({}, (), False),
            ({}, (Adapter,), False),
            ({'SABOR_MERCADONA_ENABLED': 'true'}, (), False),
            ({'SABOR_MERCADONA_ENABLED': 'true'}, (Adapter,), True),
        ]:
            env = dict(env, SABOR_TOOLSETS='pricing')
            app = build_context(load_settings(env), pricing_factories=factories)
            self.assertEqual('pricing' in app.registry.enabled_toolsets, enabled)
        self.assertNotIn('pricing', load_settings({}).requested_toolsets)

    def test_existing_registration_counts(self):
        from sabor.server import register_tools
        class Server:
            def __init__(self):
                self.tools = []
            def add_tool(self, fn):
                self.tools.append(fn)
        empty = Server()
        register_tools(empty, {})
        self.assertEqual(len(empty.tools), 1)
        mealie = Server()
        register_tools(mealie, {'MEALIE_BASE_URL': 'https://example.invalid',
                               'MEALIE_API_TOKEN': 'test'})
        self.assertEqual(len(mealie.tools), 24)
