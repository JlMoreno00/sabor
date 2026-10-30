import random
import unittest
from dataclasses import replace
from decimal import Decimal as D
from datetime import datetime, timezone
from test_pricing_engine import Pricing, CTX, items, rice
from sabor.pricing.engine import estimate, _checkout_cost
from sabor.pricing.provider import Product


class GroupSelectionTests(unittest.TestCase):
    def run_case(self, amounts, big_price='.90', **kwargs):
        pricing = Pricing()
        pricing.quote = replace(pricing.quote, fetched_at=datetime(2026, 1, 1, tzinfo=timezone.utc))
        pricing.products = [Product(pid, 'rice', 'fixture') for pid in ('small', 'big')]
        quotes = {pid: replace(pricing.quote, product_id=pid, package_quantity=size,
                               package_price=price, normalized_unit_price=price / size)
                  for pid, size, price in [('small', D('.5'), D('.60')),
                                           ('big', D(1), D(big_price))]}
        pricing.get_quote = lambda product_id, store: quotes[product_id]
        return estimate(items(*(rice(str(a)) for a in amounts)), CTX, None, pricing, **kwargs), quotes

    def test_combined_quantity_saves_package(self):
        result, _ = self.run_case([300, 300])
        self.assertEqual(result.procurement_subtotal, D('.90'))
        for line in result.lines:
            assert line.quote is not None
            self.assertEqual(line.quote.product_id, 'big')
            self.assertIn('auto_selected_cheapest_group', line.uncertainty.reasons)
            self.assertIn('auto_selected_cheapest', line.uncertainty.reasons)
            self.assertIn('grouped_selection', line.match_evidence)
            self.assertEqual(line.alternatives[0]['checkout_cost'], D('1.20'))
            self.assertEqual(line.consumption, D('.27'))

    def test_written_and_digit_numerals_group_together(self):
        # CodeRabbit on #113: 'cuatro quesos' and '4 quesos' are one product name, so the
        # combined 600 g buys one 1 kg package instead of two 500 g ones.
        pricing = Pricing()
        pricing.products = [Product(pid, 'Pizza 4 quesos', 'fixture') for pid in ('small', 'big')]
        quotes = {pid: replace(pricing.quote, product_id=pid, package_quantity=size,
                               package_price=price, normalized_unit_price=price / size)
                  for pid, size, price in [('small', D('.5'), D('.60')), ('big', D(1), D('.90'))]}
        pricing.get_quote = lambda product_id, store: quotes[product_id]
        rows = [dict(name='cuatro quesos', quantity='300', unit='g'), dict(name='4 quesos', quantity='300', unit='g')]
        result = estimate(items(*rows), CTX, None, pricing)
        self.assertEqual(result.procurement_subtotal, D('.90'))
        self.assertEqual([line.name for line in result.lines], ['cuatro quesos', '4 quesos'])

    def test_keep_mixed_selection(self):
        result, _ = self.run_case([200, 900], '1.00')
        self.assertEqual(result.procurement_subtotal, D('1.60'))
        self.assertEqual([line.quote.product_id for line in result.lines if line.quote], ['small', 'big'])
        self.assertNotIn('grouped_selection', result.lines[0].match_evidence)

    def test_exclusions(self):
        self.assertEqual(self.run_case([300])[0].procurement_subtotal, D('.60'))
        result, _ = self.run_case([300, 300], product_overrides={'items:0': 'small'})
        self.assertEqual(result.procurement_subtotal, D('1.20'))
        self.assertIsNone(self.run_case([300, 300], match_policy='strict')[0].procurement_subtotal)

    def test_ties_keep_original_lines(self):
        grouped, _ = self.run_case([300, 300], '1.20')
        single, _ = self.run_case([300], '1.20')
        self.assertEqual(grouped.lines[0], single.lines[0])
        self.assertEqual(grouped.procurement_subtotal, D('1.20'))

    def test_missing_prices_never_zero(self):
        pricing = Pricing()
        pricing.quote = replace(pricing.quote, package_price=None, normalized_unit_price=None)
        result = estimate(items(rice(), rice()), CTX, None, pricing)
        self.assertIsNone(result.procurement_subtotal)
        self.assertIsNone(result.consumption_subtotal)
        self.assertEqual(result.coverage['excluded'], 2)

    def test_never_more_expensive(self):
        rng = random.Random(9)
        for _ in range(60):
            amounts = [rng.randint(1, 2000) for _ in range(3)]
            price = str(D(rng.randint(40, 200)) / 100)
            result, quotes = self.run_case(amounts, price)
            totals = {}
            for amount in amounts:
                quote = min(quotes.values(), key=lambda q: (_checkout_cost(D(amount), 'g', q),
                                                            q.normalized_unit_price, q.product_id))
                totals[quote.product_id] = totals.get(quote.product_id, D(0)) + D(amount)
            old = D(0)
            for pid, amount in totals.items():
                checkout = _checkout_cost(amount, 'g', quotes[pid])
                assert checkout is not None
                old += checkout
            assert result.procurement_subtotal is not None
            self.assertLessEqual(result.procurement_subtotal, old)


class GroupSelectionSnapshotTests(unittest.TestCase):
    """Grouped selection reuses one consistent snapshot and adds no extra I/O."""

    def test_one_read_per_query_and_quote_even_when_a_group_changes(self):
        searches, quote_reads, resolves = [], [], []
        pricing = Pricing()
        pricing.products = [Product(pid, 'rice', 'fixture') for pid in ('small', 'big')]
        prices = {'small': (D('.5'), D('.60')), 'big': (D(1), D('.90'))}

        def search_products(query, store):
            searches.append(query.query)
            return pricing.products

        def get_quote(product_id, store):
            quote_reads.append(product_id)
            size, price = prices[product_id]
            # A later read would see a refreshed catalog: it must never be used.
            if quote_reads.count(product_id) > 1:
                price = price * 10
            return replace(pricing.quote, product_id=product_id, package_quantity=size,
                           package_price=price, normalized_unit_price=price / size,
                           fetched_at=datetime(2026, 1, 1, tzinfo=timezone.utc))

        pricing.search_products, pricing.get_quote = search_products, get_quote

        class Store:
            def get_recipe(self, slug):
                resolves.append(slug)
                raise AssertionError('items sources never read the store')

        result = estimate(items(rice('300'), rice('300')), CTX, Store(), pricing)
        self.assertEqual(result.procurement_subtotal, D('.90'))
        self.assertEqual([line.quote.product_id for line in result.lines if line.quote], ['big', 'big'])
        self.assertEqual(len(searches), 1)
        self.assertEqual(sorted(quote_reads), ['big', 'small'])
        self.assertEqual(resolves, [])
