"""Offline catalog fixtures based on mercadona-api-facts.md."""
import copy
import unittest
from dataclasses import asdict
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import Mock, patch
from typing import Any

import requests

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
                                         clock=lambda: self.now, request_delay=0, sleep=Mock())

    def test_staples_before_derivatives(self):
        self.products = [item('condensed', 'Leche condensada Hacendado'),
                         item('whole', 'Leche entera Hacendado'),
                         item('semi', 'Leche semidesnatada Hacendado')]
        self.provider._refresh()
        self.assertEqual([p.id for p in self.provider.search_products(ProductQuery('leche'), self.store)],
                         ['whole', 'semi', 'condensed'])
        self.assertEqual([p.id for p in self.provider.search_products(ProductQuery('leche condensada'), self.store)],
                         ['condensed'])

    def fetch(self, url, **kwargs):
        response = Mock()
        response.json.return_value = ({'categories': [{'id': 1, 'categories': [{'id': 2}]}]}
                                     if url.endswith('/categories/') else
                                     {'categories': [{'products': copy.deepcopy(self.products)}]})
        return response

    def quote(self, pid='oil'):
        self.provider._refresh()
        return self.provider.get_quote(pid, self.store)

    def test_realistic_basket_cheapest_strict_and_overrides(self):
        from sabor.pricing.engine import estimate
        # Real catalog shapes: nominal ud package with reference denominator dz.
        def food(pid, name, size, unit, price, reference, category=6, denominator=None):
            product = item(pid, name, unit_size=size, size_format=unit,
                           unit_price=price, reference_price=reference,
                           reference_format=denominator or unit)
            product['categories'] = [dict(id=category, name='fixture', level=0)]
            return product
        self.products = [
            food('10380', 'Leche desnatada Hacendado', 1, 'l', '.81', '.81'),
            food('10381', 'Leche semidesnatada Hacendado', 1, 'l', '.83', '.83'),
            food('cosmetic', 'Leche corporal Nivea', 1, 'l', '.10', '.10', 20),
            food('29100', 'Harina de trigo Hacendado', 1, 'kg', '.72', '.72', 5),
            food('29106', 'Harina de trigo grande Hacendado', 5, 'kg', '2.95', '.59', 5),
            food('eggs', 'Huevos', 12, 'ud', '2.64', '2.64', denominator='dz')]
        self.provider._refresh()
        source = dict(kind='items', items=[
            dict(name='leche', quantity=1, unit='l'),
            dict(name='harina de trigo', quantity=200, unit='g'),
            dict(name='huevos', quantity=6), dict(name='sal al gusto')])
        def run(**kw):
            return estimate(source, self.store, None, self.provider, **kw)
        result = run()
        milk, flour, eggs, salt = result.lines
        assert milk.quote is not None
        self.assertEqual(milk.quote.product_id, '10380')
        self.assertEqual(milk.alternatives[0]['name'], 'Leche semidesnatada Hacendado')
        self.assertEqual(milk.uncertainty.level, 'estimated')
        self.assertIn('auto_selected_cheapest', milk.uncertainty.reasons)
        # 200 g: the 1 kg bag (0.72 at checkout) beats the 5 kg bag (2.95), even
        # though the big bag is cheaper per kilo.
        assert flour.quote is not None
        self.assertEqual(flour.quote.product_id, '29100')
        self.assertEqual(flour.consumption, Decimal('.2') * Decimal('.72'))
        self.assertEqual(eggs.consumption, Decimal('1.32'))
        self.assertIn('assumed_count', eggs.uncertainty.reasons)
        self.assertIsNone(salt.consumption)
        self.assertEqual(result.coverage['costed'], 3)
        self.assertFalse(result.complete)
        self.assertEqual(result.procurement_subtotal, Decimal('.81') + Decimal('.72') + Decimal('2.64'))
        strict = run(match_policy='strict')
        self.assertIn('ambiguous_match', strict.lines[0].uncertainty.reasons)
        overridden = run(product_overrides={'items:0': '10381'}).lines[0].quote
        assert overridden is not None
        self.assertEqual(overridden.product_id, '10381')
        self.assertNotIn('cosmetic', [p.id for p in self.provider.search_products(ProductQuery('leche'), self.store)])
        self.assertFalse(self.provider.get_quote('cosmetic', self.store).food)

    def test_real_reference_denominators(self):
        # Real catalog formats (counted on the cached mad1 catalog): dc = dozen
        # (8 products), 100 ml (395) and 100 g (91) reference denominators.
        self.products = [
            item('eggs6', 'Huevos grandes L', unit_size=6.0, size_format='ud', unit_price='1.80',
                 reference_price='3.60', reference_format='dc'),
            item('juice', 'Zumo de naranja', unit_size=1.0, size_format='l', unit_price='1.95',
                 reference_price='0.195', reference_format='100 ml'),
            item('ham', 'Jamón cocido', unit_size=0.25, size_format='kg', unit_price='2.50',
                 reference_price='1.000', reference_format='100 g'),
            item('odd', 'Producto raro', unit_size=2.0, size_format='l', unit_price='3.00',
                 reference_price='9', reference_format='lv')]
        self.provider._refresh()
        eggs = self.provider.get_quote('eggs6', self.store)
        self.assertEqual((eggs.package_unit, eggs.normalized_unit_price), ('unit', Decimal('.30')))
        self.assertNotIn('unknown', eggs.uncertainty_reasons)
        juice = self.provider.get_quote('juice', self.store)
        self.assertEqual((juice.package_unit, juice.normalized_unit_price), ('l', Decimal('1.950')))
        ham = self.provider.get_quote('ham', self.store)
        self.assertEqual((ham.package_unit, ham.normalized_unit_price), ('kg', Decimal('10.000')))
        # Unknown denominators fall back to package price / package amount, flagged.
        odd = self.provider.get_quote('odd', self.store)
        self.assertEqual(odd.normalized_unit_price, Decimal('1.5'))
        self.assertIn('derived_unit_price', odd.uncertainty_reasons)

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
        self.provider._refresh()
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
                self.provider = MercadonaPricing(session=self.session, sleep=Mock())
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
                self.provider = MercadonaPricing(session=self.session, sleep=Mock())
                quote = self.quote()
                self.assertIsNone(quote.package_price)
                self.assertIn('unknown', quote.uncertainty_reasons)

    def test_instances_do_not_share_cache(self):
        self.quote()
        self.products = [item(unit_price='22')]
        other = MercadonaPricing(warehouse='other', session=self.session)
        other._refresh()
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

    def many_subcategories(self, failing, status=403, recover=False):
        """20 subcategories; `failing` ids answer `status` (once if `recover`)."""
        seen = {}

        def fetch(url, **kwargs):
            response = Mock()
            if url.endswith('/categories/'):
                response.json.return_value = {'categories': [
                    {'id': 1, 'categories': [{'id': n} for n in range(2, 22)]}]}
                return response
            sub = int(url.rstrip('/').rsplit('/', 1)[1])
            seen[sub] = seen.get(sub, 0) + 1
            if sub in failing and not (recover and seen[sub] > 1):
                response.status_code = status
                response.raise_for_status.side_effect = requests.HTTPError(response=response)
                return response
            response.status_code = 200
            response.json.return_value = {'categories': [{'products': [item(f'p{sub}', f'Producto {sub}')]}]}
            return response
        self.session.get.side_effect = fetch
        self.provider = MercadonaPricing(session=self.session, ttl_seconds=10,
                                         clock=lambda: self.now, retry_delay=0,
                                         request_delay=0)
        return seen

    def test_one_blocked_subcategory_gives_partial_catalog(self):
        self.many_subcategories({7})
        quote = self.quote('p3')
        self.assertEqual(quote.package_price, Decimal('17.25'))
        self.assertIn('catalog_partial', quote.uncertainty_reasons)
        with self.assertRaises(LookupError):
            self.quote('p7')

    def test_blocked_subcategory_is_retried_once(self):
        seen = self.many_subcategories({7}, status=429, recover=True)
        quote = self.quote('p7')
        self.assertEqual(seen[7], 2)
        self.assertNotIn('catalog_partial', quote.uncertainty_reasons)

    def test_too_many_failed_subcategories_is_unavailable(self):
        self.many_subcategories(set(range(2, 6)))  # 4 of 20 = 20% > 10%
        with self.assertRaises(RuntimeError):
            self.quote('p10')

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

    def test_warming_is_nonblocking(self):
        from sabor.pricing.mercadona import CatalogWarming
        with patch.object(self.provider, 'start_background_refresh') as start:
            with self.assertRaises(CatalogWarming):
                self.provider.get_quote('oil', self.store)
            start.assert_called_once()
        self.session.get.assert_not_called()
        self.provider._refresh()
        self.assertEqual(self.quote().package_price, Decimal('17.25'))

    def test_pacing_backoff_and_early_stop(self):
        seen = self.many_subcategories(set(range(2, 22)))
        self.provider.request_delay = .5
        self.provider.retry_delay = 2
        self.provider.sleep = Mock()
        with self.assertRaises(RuntimeError):
            self.quote()
        self.assertEqual(list(seen.values()), [3, 3, 3, 3])
        self.assertEqual([c.args[0] for c in self.provider.sleep.call_args_list],
                         [2, 4, .5, 2, 4, .5, 2, 4, .5, 2, 4])

    def test_cache_roundtrip_atomic_ttl_and_garbled(self):
        import json
        from pathlib import Path
        self.provider.cache_path = Path('/cache/catalog.json')
        with patch.object(Path, 'write_text') as write, patch('sabor.pricing.mercadona.os.replace') as rename:
            self.provider._refresh()
        payload = write.call_args.args[0]
        rename.assert_called_once_with(Path('/cache/catalog.json.tmp'), Path('/cache/catalog.json'))
        with patch.object(Path, 'read_text', return_value=payload):
            other = MercadonaPricing(cache_path='/cache/catalog.json', clock=lambda: self.now,
                                      ttl_seconds=10, sleep=Mock())
        with patch.object(other, 'start_background_refresh'):
            self.assertNotIn('stale_catalog', other.get_quote('oil', self.store).uncertainty_reasons)
            self.now += 10
            self.assertIn('stale_catalog', other.get_quote('oil', self.store).uncertainty_reasons)
        for invalid in ('garbled', '[]', json.dumps({'loaded_at': 'bad'})):
            with patch.object(Path, 'read_text', return_value=invalid):
                other = MercadonaPricing(cache_path='/cache/catalog.json')
            self.assertIsNone(other._catalog)

    def test_timeout_backoff(self):
        self.provider.sleep = Mock()
        self.provider._get = Mock(side_effect=[requests.Timeout(), requests.Timeout(), {}])
        self.assertEqual(self.provider._get_subcategory(2), {})
        self.assertEqual([c.args[0] for c in self.provider.sleep.call_args_list], [2, 4])

    def test_start_idempotent(self):
        with patch('sabor.pricing.mercadona.Thread') as thread:
            thread.return_value.is_alive.return_value = True
            self.provider.start_background_refresh()
            self.provider.start_background_refresh()
            thread.assert_called_once()
            self.assertTrue(thread.call_args.kwargs['daemon'])

    def test_unknown_product(self):
        with self.assertRaises(LookupError):
            self.quote('missing')
