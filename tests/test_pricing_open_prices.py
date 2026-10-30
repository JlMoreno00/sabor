"""Offline Open Prices contracts, using sanitized recorded responses."""
import asyncio
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

import requests
from mcp.server.fastmcp import FastMCP
from sabor.core.config import load_settings
from sabor.core.context import assemble_context
from sabor.core.capabilities import describe
from sabor.pricing.open_prices import OpenPricesPricing
from sabor.pricing.provider import ProductQuery, RetailContext
from sabor.server import register_tools
from sabor.store import StoreError

FIXTURES = Path(__file__).parent / 'fixtures' / 'open_prices'


def recorded(name):
    return json.loads((FIXTURES / name).read_text())['response']


class OpenPricesTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 24, tzinfo=timezone.utc).timestamp()
        self.session = Mock(spec=requests.Session)
        self.products = recorded('products_leche_entera.json')
        self.prices = recorded('prices_8480024831088_es.json')
        self.session.get.side_effect = self.fetch
        self.provider = OpenPricesPricing(session=self.session, clock=lambda: self.now)
        self.store = RetailContext('open_prices')

    def fetch(self, url, **kwargs):
        response = Mock()
        response.json.return_value = deepcopy(self.products if url.endswith('/products') else self.prices)
        return response

    def quote(self):
        return self.provider.get_quote('8480024831088', self.store)

    def test_recorded_quote_and_cache(self):
        quote = self.quote()
        self.assertEqual(quote.package_quantity, Decimal(1))
        self.assertEqual(quote.package_unit, 'l')
        self.assertEqual(quote.normalized_unit_price, quote.package_price)
        assert quote.observed_at is not None
        self.assertEqual(quote.observed_at.isoformat(), '2024-01-24T00:00:00+00:00')
        self.assertEqual(quote.uncertainty_reasons, ('observation', 'stale_observation', 'single_observation'))
        self.assertIsNone(quote.availability)
        self.assertIn('Lupa', quote.provenance)
        self.assertIn('Villarcayo', quote.provenance)
        self.assertIn('Open Prices (Open Food Facts), ODbL', quote.provenance)
        self.assertEqual(quote.location, RetailContext('open_prices', 'ES', 'EUR'))
        self.quote()
        self.assertEqual(self.session.get.call_count, 1)
        self.now += 600
        self.quote()
        self.assertEqual(self.session.get.call_count, 2)
        call = self.session.get.call_args.kwargs
        self.assertEqual(call['params'], dict(product_code='8480024831088', currency='EUR', type='PRODUCT', order_by='-date', size=50))
        self.assertIn('Sabor', call['headers']['User-Agent'])
        self.assertGreater(call['timeout'], 0)
        other = OpenPricesPricing(session=self.session, clock=lambda: self.now)
        other.get_quote('8480024831088', self.store)
        self.assertEqual(self.session.get.call_count, 3)

    def test_search_filter_cap_and_cache(self):
        item = self.products['items'][0]
        self.products['items'] = [dict(item, price_count=0)] + [dict(item, code=str(n)) for n in range(20)]
        results = self.provider.search_products(ProductQuery('leche', 50), self.store)
        self.assertEqual(len(results), 10)
        self.assertEqual(results[0].id, '0')
        self.assertIn(item['brands'], results[0].name)
        self.assertIn('ODbL', results[0].provenance)
        self.provider.search_products(ProductQuery('leche', 50), self.store)
        self.assertEqual(self.session.get.call_count, 1)
        self.assertEqual(self.session.get.call_args.kwargs['params'], dict(product_name__like='leche', order_by='-price_count', size=10))

    def search_by_name(self, catalog):
        # Fake substring filter: each product_name__like gets the names containing it.
        item = self.products['items'][0]
        sent = []

        def fetch(url, **kwargs):
            like = kwargs['params']['product_name__like']
            sent.append(like)
            names = [n for n in catalog if like.casefold() in n.casefold()][:kwargs['params']['size']]
            response = Mock()
            response.json.return_value = {'items': [dict(item, code=n, product_name=n, brands='') for n in names]}
            return response
        self.session.get.side_effect = fetch
        return sent

    def test_plural_queries_also_find_singular_names(self):
        # #49 follow-up (CodeRabbit on #105): product_name__like is a substring filter, so
        # 'guisantes' missed 'Guisante fino'. The original query runs first and keeps its
        # results; only when it returns fewer than the limit does a one-word plural add the
        # results of its singular ('guisante'), without duplicates.
        sent = self.search_by_name(['Guisantes extra', 'Guisante fino'])
        found = self.provider.search_products(ProductQuery('guisantes', 10), self.store)
        self.assertEqual(([p.id for p in found], sent), (['Guisantes extra', 'Guisante fino'], ['guisantes', 'guisante']))
        # Multi-word queries and words without a plural 's' send one request, unchanged.
        for text in ('alubias blancas', 'leche', 'sal', 'gas', 'arroz'):
            with self.subTest(text=text):
                sent.clear()
                self.provider.search_products(ProductQuery(text, 10), self.store)
                self.assertEqual(sent, [text])

    def test_singular_never_displaces_original_matches(self):
        # CodeRabbit on #109: 'mora' also matches 'morada'; a full page for 'moras' is kept
        # exactly as before and no singular request is made.
        moras = [f'Moras {n}' for n in range(10)]
        sent = self.search_by_name([f'Col morada {n}' for n in range(10)] + moras)
        found = self.provider.search_products(ProductQuery('moras', 10), self.store)
        self.assertEqual(([p.id for p in found], sent), (moras, ['moras']))

    def test_failed_singular_request_keeps_original_results(self):
        # CodeRabbit on #109: the extra singular request must not lose original matches;
        # with none, the error still propagates.
        item = self.products['items'][0]

        def fetch(url, **kwargs):
            if kwargs['params']['product_name__like'] in ('guisante', 'zanahoria'):
                raise requests.Timeout()
            response = Mock()
            names = ['Guisantes extra'] if kwargs['params']['product_name__like'] == 'guisantes' else []
            response.json.return_value = {'items': [dict(item, code=n, product_name=n, brands='') for n in names]}
            return response
        self.session.get.side_effect = fetch
        found = self.provider.search_products(ProductQuery('guisantes', 10), self.store)
        self.assertEqual([p.id for p in found], ['Guisantes extra'])
        with self.assertRaises(StoreError):
            self.provider.search_products(ProductQuery('zanahorias', 10), self.store)
        self.session.get.side_effect = lambda url, **kw: (_ for _ in ()).throw(requests.Timeout())
        with self.assertRaises(StoreError):
            self.provider.search_products(ProductQuery('guisante', 10), self.store)

    def test_selection_country_currency_date_and_id_not_price(self):
        original = self.prices['items'][0]
        recent = dict(original, id=10, date='2026-10-24', price=2, price_is_discounted=True)
        tie = dict(recent, id=11, price=3)
        foreign = dict(recent, id=99, location={'osm_address_country_code': 'FR'})
        self.prices['items'] = [foreign, dict(recent, location=None), original, recent, tie,
                                dict(recent, currency='USD', id=100)]
        quote = self.quote()
        self.assertEqual(quote.package_price, Decimal(3))
        self.assertEqual(quote.uncertainty_reasons, ('observation', 'discounted'))
        self.assertEqual(quote.fetched_at.timestamp(), self.now)

    def test_unknown_unit_and_stale_boundary(self):
        item = self.prices['items'][0]
        item['product']['product_quantity_unit'] = 'bottle'
        item['date'] = '2026-04-27'
        quote = self.quote()
        self.assertIsNone(quote.package_quantity)
        self.assertIsNone(quote.package_unit)
        self.assertIsNone(quote.normalized_unit_price)
        self.assertIn('unknown_unit', quote.uncertainty_reasons)
        self.assertNotIn('stale_observation', quote.uncertainty_reasons)

    def test_units_and_invalid_amounts(self):
        for amount, unit, expected, canonical in ((500, 'g', '.5', 'kg'), (2, 'kg', '2', 'kg'),
                                                   (750, 'ml', '.75', 'l'), (2, 'l', '2', 'l'),
                                                   (6, 'ud', '6', 'unit')):
            with self.subTest(unit=unit):
                self.provider._cache.clear()
                self.prices['items'][0]['product'].update(product_quantity=amount, product_quantity_unit=unit)
                quote = self.quote()
                self.assertEqual(quote.package_quantity, Decimal(expected))
                self.assertEqual(quote.package_unit, canonical)
        for amount in (None, 0, -1, 'NaN'):
            self.provider._cache.clear()
            self.prices['items'][0]['product']['product_quantity'] = amount
            self.assertIsNone(self.quote().package_quantity)

    def test_cache_bound_and_http_failures_not_cached(self):
        for n in range(130):
            self.provider.search_products(ProductQuery(str(n)), self.store)
        self.assertEqual(len(self.provider._cache), 128)
        self.session.get.side_effect = requests.Timeout('PRIVATE')
        from sabor.store import StoreError
        for _ in range(2):
            with self.assertRaises(StoreError) as error:
                self.provider.search_products(ProductQuery('uncached'), self.store)
            self.assertNotIn('PRIVATE', str(error.exception))
        self.assertEqual(self.session.get.call_count, 132)

    def test_no_country_observation(self):
        for name in ('prices_3450970006767_fr.json', 'prices_7790742330703_empty_eur.json', 'prices_category_eur.json'):
            with self.subTest(name=name):
                self.prices = recorded(name)
                provider = OpenPricesPricing(session=self.session)
                with self.assertRaises(LookupError):
                    provider.get_quote('8480024831088', self.store)

    def test_private_fields_never_projected(self):
        self.prices['items'][0].update(owner='PRIVATE', proof={'secret': 'PRIVATE'}, osm_lat=123)
        self.assertNotIn('PRIVATE', repr(asdict(self.quote())))
        self.assertNotIn('owner', repr(asdict(self.quote())))

    def test_fixture_keys(self):
        forbidden = {'owner', 'proof', 'proof_id', 'owner_comment', 'creator', 'osm_lat', 'osm_lon', 'lat', 'lon', 'user_id', 'username'}
        def scan(value):
            if isinstance(value, dict):
                self.assertFalse(forbidden.intersection(value))
                for child in value.values():
                    scan(child)
            elif isinstance(value, list):
                for child in value:
                    scan(child)
        for path in FIXTURES.glob('*.json'):
            scan(json.loads(path.read_text()))

    def test_context_validation_before_http(self):
        for store in (RetailContext('other'), RetailContext('open_prices', 'Spain')):
            with self.assertRaises(ValueError):
                self.provider.get_quote('code', store)
        self.session.get.assert_not_called()

    def test_registration_discovery_estimate_and_error_mapping(self):
        self.products['items'] = [p for p in self.products['items'] if p['code'] == '8480024831088']
        env = {'SABOR_OPEN_PRICES_ENABLED': 'true', 'SABOR_TOOLSETS': 'pricing'}
        with patch('sabor.pricing.open_prices.requests.Session', return_value=self.session):
            context = assemble_context(load_settings(env), store_factory=lambda s: None)
        self.assertEqual([p.id for p in context.pricing], ['open_prices'])
        self.assertIn('pricing', context.registry.enabled_toolsets)
        self.assertIn('open_prices', repr(describe(context, [], 'test')))
        server = FastMCP('open-prices-test')
        with patch('sabor.server.assemble_context', return_value=context):
            register_tools(server, env)
        def call(name, **args):
            tool = server._tool_manager.get_tool(name)
            assert tool is not None
            return asyncio.run(tool.run(args))
        result = call('estimate_cost', source={'kind': 'items', 'items': [{'name': 'leche', 'quantity': 1, 'unit': 'l'}]}, store={'provider': 'open_prices', 'location_id': 'ES'})
        self.assertTrue(result['ok'], result)
        self.assertIn('ODbL', repr(result))
        provider = context.pricing[0]
        assert isinstance(provider, OpenPricesPricing)
        provider._cache.clear()
        self.prices = recorded('prices_7790742330703_empty_eur.json')
        self.assertEqual(call('get_product_price', product_id='missing')['error']['code'], 'not_found')
        for exception, code in ((requests.Timeout(), 'timeout'), (requests.HTTPError(response=Mock(status_code=429)), 'rate_limited'), (requests.HTTPError(response=Mock(status_code=503)), 'upstream_error')):
            provider._cache.clear()
            self.session.get.side_effect = exception
            self.assertEqual(call('get_product_price', product_id='missing')['error']['code'], code)

    def test_opt_in_and_mercadona_unchanged(self):
        from sabor.pricing.tools import pricing_ready
        for env, expected in (({}, False), ({'SABOR_OPEN_PRICES_ENABLED': 'true'}, True), ({'SABOR_MERCADONA_ENABLED': 'true'}, True)):
            settings = load_settings(env)
            self.assertEqual(pricing_ready(settings), expected)
        self.assertFalse(load_settings({}).open_prices_enabled)
        from sabor.pricing.mercadona import MercadonaPricing
        env = {'SABOR_MERCADONA_ENABLED': 'true', 'SABOR_TOOLSETS': 'pricing'}
        with patch.object(MercadonaPricing, '_load_cache'):
            context = assemble_context(load_settings(env), store_factory=lambda s: None)
        self.assertEqual([p.id for p in context.pricing], ['mercadona'])
        server = FastMCP('mercadona-only-test')
        with patch('sabor.server.assemble_context', return_value=context):
            register_tools(server, env)
        for name in ('estimate_cost', 'get_product_price', 'search_pricing_products'):
            self.assertIsNotNone(server._tool_manager.get_tool(name))
