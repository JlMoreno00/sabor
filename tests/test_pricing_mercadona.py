"""Offline catalog fixtures based on mercadona-api-facts.md."""
import copy
import unittest
from dataclasses import asdict
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import Mock
from typing import Any

from sabor.pricing.mercadona import MercadonaPricing
from sabor.pricing.provider import ProductQuery, RetailContext


def item(pid='oil', name='Aceite de oliva', **changes) -> dict[str, Any]:
    pi = dict(unit_price='17.25', unit_size=5.0, size_format='l',
              reference_price='3.450', reference_format='L', approx_size=False,
              selling_method=0, is_pack=False, pack_size=None, total_units=None,
              drained_weight=None)
    pi.update(changes)
    return dict(id=pid, display_name=name, price_instructions=pi)


class MercadonaTests(unittest.TestCase):
    def setUp(self):
        self.now = 1000
        self.products = [item(), item('salt', 'Sal fina'), item('sauce', 'Salsa')]
        self.session = Mock()
        self.session.get.side_effect = self.fetch
        self.store = RetailContext('mercadona')
        self.provider = MercadonaPricing(session=self.session, ttl_seconds=10,
                                         clock=lambda: self.now)

    def fetch(self, url, **kwargs):
        response = Mock()
        response.json.return_value = ({'categories': [{'id': 1, 'categories': [{'id': 2}]}]}
                                     if url.endswith('/categories/') else
                                     {'categories': [{'products': copy.deepcopy(self.products)}]})
        return response

    def quote(self, pid='oil'):
        return self.provider.get_quote(pid, self.store)

    def test_ttl_refresh(self):
        self.assertEqual(self.quote().package_price, Decimal('17.25'))
        self.products[0]['price_instructions']['unit_price'] = '20'
        self.now = 1009
        self.assertEqual(self.quote().package_price, Decimal('17.25'))
        self.now = 1010
        self.assertEqual(self.quote().package_price, Decimal('20'))

    def test_stale_fallback_warns_and_preserves_observation(self):
        first = self.quote()
        self.now += 10
        self.session.get.side_effect = OSError('offline')
        with self.assertWarnsRegex(RuntimeWarning, 'stale'):
            quote = self.quote()
        self.assertEqual(quote.observed_at, first.observed_at)
        self.assertEqual(quote.package_price, first.package_price)
        self.assertIn('stale_catalog', quote.uncertainty_reasons)

    def test_whole_word_search_and_quantity_prefix(self):
        products = self.provider.search_products(ProductQuery('100 g de sal'), self.store)
        self.assertEqual([p.id for p in products], ['salt'])

    def test_normalization_and_metadata(self):
        quote = self.quote()
        self.assertEqual((quote.package_quantity, quote.package_unit,
                          quote.normalized_unit_price), (Decimal(5), 'l', Decimal('3.450')))
        self.assertEqual(quote.currency, 'EUR')
        self.assertEqual(quote.location, RetailContext('mercadona', 'mad1', 'EUR'))
        self.assertEqual(quote.observed_at, datetime.fromtimestamp(1000, timezone.utc))
        self.assertEqual(quote.provenance, 'mercadona:mad1:tienda.mercadona.es-catalog')
        self.assertEqual(quote.uncertainty_reasons, ())

    def test_gram_ml_and_count_normalization(self):
        for unit, canonical, price in [('g', 'kg', '3000'), ('ml', 'l', '3000'),
                                        ('ud', 'unit', '3')]:
            with self.subTest(unit=unit):
                self.products = [item(unit_size=500, size_format=unit,
                                      reference_price='3', reference_format=unit)]
                self.provider = MercadonaPricing(session=self.session)
                quote = self.quote()
                self.assertEqual(quote.package_quantity, Decimal(500) if unit == 'ud' else Decimal('.5'))
                self.assertEqual(quote.package_unit, canonical)
                self.assertEqual(quote.normalized_unit_price, Decimal(price))

    def test_approx_size(self):
        self.products = [item(approx_size=True)]
        self.assertIn('approx_size', self.quote().uncertainty_reasons)

    def test_selling_method(self):
        self.products = [item(approx_size=True, selling_method=1)]
        self.assertIn('selling_method', self.quote().uncertainty_reasons)

    def test_pack(self):
        self.products = [item(is_pack=True, pack_size=6, total_units=6)]
        self.assertIn('pack', self.quote().uncertainty_reasons)

    def test_drained_weight(self):
        self.products = [item(drained_weight='0.3')]
        self.assertIn('drained_weight', self.quote().uncertainty_reasons)

    def test_unknown_size_format(self):
        self.products = [item(size_format='bottle')]
        quote = self.quote()
        self.assertIsNone(quote.package_quantity)
        self.assertIn('unknown', quote.uncertainty_reasons)

    def test_garbled_and_missing_fields(self):
        for pi in [None, [], {'unit_price': 'NaN'}, {'unit_price': 0},
                   {'unit_price': {}}, {'unit_price': '-1'}]:
            with self.subTest(pi=pi):
                self.products = [dict(id='oil', display_name=[], price_instructions=pi), None]
                self.provider = MercadonaPricing(session=self.session)
                quote = self.quote()
                self.assertIsNone(quote.package_price)
                self.assertIn('unknown', quote.uncertainty_reasons)

    def test_instances_do_not_share_cache(self):
        self.quote()
        self.products = [item(unit_price='22')]
        other = MercadonaPricing(warehouse='other', session=self.session)
        self.assertEqual(other.get_quote('oil', RetailContext('mercadona')).package_price, Decimal(22))
        self.assertEqual(self.quote().package_price, Decimal('17.25'))

    def test_no_floats_in_quote(self):
        def check(value):
            self.assertNotIsInstance(value, float)
            if isinstance(value, dict):
                for child in value.values():
                    check(child)
            elif isinstance(value, (tuple, list)):
                for child in value:
                    check(child)
        check(asdict(self.quote()))

    def test_reject_context_mismatch_before_network(self):
        for store in [RetailContext('other'), RetailContext('mercadona', 'other'),
                      RetailContext('mercadona', currency='USD')]:
            with self.assertRaises(ValueError):
                self.provider.get_quote('oil', store)
        self.session.get.assert_not_called()

    def test_failed_initial_or_partial_fetch_does_not_cache(self):
        self.session.get.side_effect = OSError('offline')
        with self.assertRaises(RuntimeError):
            self.quote()
        self.session.get.side_effect = self.fetch
        self.assertEqual(self.quote().package_price, Decimal('17.25'))

    def test_partial_refresh_preserves_last_complete_catalog(self):
        first = self.quote()
        self.now += 10
        original_fetch = self.fetch
        def partial(url, **kwargs):
            if url.endswith('/categories/2/'):
                raise OSError('subcategory failed')
            return original_fetch(url, **kwargs)
        self.session.get.side_effect = partial
        with self.assertWarns(RuntimeWarning):
            self.assertEqual(self.quote().observed_at, first.observed_at)

    def test_unknown_product(self):
        with self.assertRaises(LookupError):
            self.quote('missing')
