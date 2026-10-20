"""Offline registered pricing contracts; no network or credentials required."""
import asyncio
import json
import unittest
from dataclasses import replace
from unittest.mock import patch
from mcp.server.fastmcp import FastMCP
from sabor.core.config import load_settings
from sabor.core.context import build_context
from sabor.server import register_tools
from sabor.store.fake import FakeRecipeStore
from sabor.store import Recipe, Ingredient, NamedRef
from test_pricing_engine import Pricing
from sabor.pricing.provider import Product


class FakePricing(Pricing):
    operations = frozenset({'search_products', 'get_quote'})


class PricingToolTests(unittest.TestCase):
    def setUp(self):
        self.provider = FakePricing()
        self.store = FakeRecipeStore()
        self.store.create_recipe(Recipe(slug='rice', recipeIngredient=(
            Ingredient(quantity=300, unit=NamedRef(name='g'), food=NamedRef(name='rice')),)))
        self.server = self.server_for((self.provider,))

    def server_for(self, providers, with_store=True):
        context = build_context(load_settings({'SABOR_MERCADONA_ENABLED': 'true', 'SABOR_TOOLSETS': 'pricing'}),
            store_factory=(lambda s: self.store) if with_store else None,
            pricing_factories=tuple(lambda s, p=p: p for p in providers))
        server = FastMCP('pricing-test')
        with patch('sabor.server.build_context', return_value=context):
            register_tools(server, {'SABOR_MERCADONA_ENABLED': 'true', 'SABOR_TOOLSETS': 'pricing'})
        return server

    def call(self, name, server=None, **arguments):
        tool = (server or self.server)._tool_manager.get_tool(name)
        assert tool is not None
        return asyncio.run(tool.run(arguments))

    def test_warming_envelope(self):
        from sabor.pricing.mercadona import CatalogWarming
        with patch.object(self.provider, 'get_quote', side_effect=CatalogWarming):
            result = self.call('get_product_price', product_id='rice')
        self.assertEqual(result['error']['code'], 'rate_limited')
        self.assertEqual(result['data']['retry_after_seconds'], 120)
        self.assertEqual(result['error']['message'],
                         'Mercadona catalog is loading; retry in a few minutes')

    def test_default_provider_and_decimal_serialization(self):
        result = self.call('get_product_price', product_id='rice')
        self.assertTrue(result['ok'])
        self.assertEqual(result['data']['package_price'], '2')
        self.assertEqual(result['data']['location']['provider'], 'fake')
        json.dumps(result)
        self.assertTrue(self.call('search_pricing_products', query='rice')['ok'])

    def test_unknown_ambiguous_and_malformed_provider(self):
        other = FakePricing()
        other.id = 'other'
        for store in ({'provider': 'unknown'}, {}, {'provider': 'fake', 'url': 'secret'}):
            self.assertEqual(self.call('get_product_price', product_id='rice', store=store)['error']['code'], 'invalid_argument')
        server = self.server_for((self.provider, other))
        self.assertEqual(self.call('get_product_price', server=server, product_id='rice')['error']['code'], 'invalid_argument')

    def test_honest_matching_and_leading_quantity(self):
        self.provider.products = [Product('salsa', 'salsa', 'fixture')]
        source = {'kind': 'items', 'items': [{'name': 'sal', 'quantity': '1', 'unit': 'g'}]}
        self.assertIsNone(self.call('estimate_cost', source=source)['data']['consumption_subtotal'])
        self.provider.products = [Product('rice', 'rice', 'fixture')]
        source['items'] = [{'name': 'rice', 'text': '200 g de rice'}]
        self.assertEqual(self.call('estimate_cost', source=source)['data']['consumption_subtotal'], '0.40')

    def test_invalid_match_policy_before_provider(self):
        result = self.call('estimate_cost', source={'kind': 'items', 'items': []},
                           match_policy='invalid')
        self.assertEqual(result['error']['code'], 'invalid_argument')
        self.assertEqual(self.provider.calls, 0)

    def test_limit_bounds(self):
        for limit in (0, 51):
            self.assertEqual(self.call('search_pricing_products', query='rice', limit=limit)['error']['code'], 'invalid_argument')
        for limit in (1, 50):
            self.assertTrue(self.call('search_pricing_products', query='rice', limit=limit)['ok'])

    def test_recipe_end_to_end_and_missing_store(self):
        result = self.call('estimate_cost', source={'kind': 'recipe', 'slug': 'rice'})
        self.assertEqual(result['data']['consumption_subtotal'], '0.60')
        self.assertEqual(result['data']['procurement_subtotal'], '2.00')
        self.assertTrue(result['data']['complete'])
        self.assertIn('source_digest', result['data'])
        server = self.server_for((self.provider,), with_store=False)
        self.assertEqual(self.call('estimate_cost', server=server, source={'kind': 'recipe', 'slug': 'rice'})['error']['code'], 'provider_unconfigured')

    def test_no_secrets_and_no_missing_price_zero(self):
        with patch.object(self.provider, 'get_quote', side_effect=RuntimeError('private-secret-token')):
            result = self.call('get_product_price', product_id='rice')
        self.assertNotIn('private-secret-token', json.dumps(result))
        self.provider.quote = replace(self.provider.quote, package_price=None, normalized_unit_price=None)
        result = self.call('estimate_cost', source={'kind': 'recipe', 'slug': 'rice'})
        self.assertIsNone(result['data']['consumption_subtotal'])
        self.assertFalse(result['data']['complete'])
