import unittest
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal as D
from sabor.pricing.engine import estimate
from sabor.pricing.provider import Product, PriceQuote, RetailContext
from sabor.store import Recipe, Ingredient, NamedRef, ItemDraft, MealEntryDraft
from sabor.store.fake import FakeRecipeStore

CTX = RetailContext('fake', 'here', 'EUR')


class Pricing:
    id = 'fake'
    def __init__(self):
        self.calls = 0
        self.products = [Product('rice', 'rice', 'fixture')]
        self.quote = PriceQuote('rice', 'rice', D('2'), 'EUR', D(1), 'kg',
                                D(2), None, datetime.now(timezone.utc), CTX, 'fixture', True)
    def search_products(self, query, store):
        self.calls += 1
        return self.products
    def get_quote(self, product_id, store):
        self.calls += 1
        return replace(self.quote, product_id=product_id)


def items(*rows):
    return {'kind': 'items', 'items': list(rows)}


def rice(amount: object = '300', unit='g'):
    return dict(name='rice', quantity=amount, unit=unit)


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.store, self.pricing = FakeRecipeStore(), Pricing()
        self.store.create_recipe(Recipe(slug='rice', recipeIngredient=(
            Ingredient(quantity=300, unit=NamedRef(name='g'), food=NamedRef(name='rice')),)))
    def run_estimate(self, source, **kw):
        return estimate(source, CTX, self.store, self.pricing, **kw)
    def test_inline_consumption_and_procurement(self):
        result = self.run_estimate(items(rice()))
        self.assertEqual((result.consumption_subtotal, result.procurement_subtotal), (D('.60'), D(2)))
        self.assertIsInstance(result.lines[0].required_quantity, D)
        self.assertTrue(result.complete)
    def test_group_before_rounding(self):
        result = self.run_estimate(items(rice(), rice()))
        self.assertEqual(result.procurement_subtotal, D(2))
        self.assertEqual(result.consumption_subtotal, D('1.2'))
    def test_recipe_multiplier(self):
        self.assertEqual(self.run_estimate(dict(kind='recipe', slug='rice', multiplier='2')).consumption_subtotal, D('1.2'))
    def test_shopping_list(self):
        ref = self.store.create_shopping_list('test')
        self.store.add_shopping_items(ref.id, [ItemDraft(quantity=300, food=NamedRef(name='rice'), unit=NamedRef(name='g'))])
        self.assertEqual(self.run_estimate(dict(kind='shopping_list', list_id=ref.id)).consumption_subtotal, D('.6'))
    def test_mealplan_occurrences_and_text(self):
        for day in ('01', '02'):
            self.store.set_mealplan_entry(MealEntryDraft('2026-01-'+day, 'dinner', 'rice', 'rice'))
        self.store.set_mealplan_entry(MealEntryDraft('2026-01-03', 'dinner', 'out'))
        result = self.run_estimate(dict(kind='mealplan', start_date='2026-01-01', end_date='2026-01-03', multiplier='2'))
        self.assertEqual(result.consumption_subtotal, D('2.4'))
        self.assertEqual(len(result.excluded_rows), 1)
        self.assertFalse(result.complete)
    def test_bounds_before_pricing(self):
        for source in [items(*([rice()]*501)), dict(kind='recipe', slug='rice', multiplier='21'),
                       dict(kind='recipe', slug='rice', multiplier='0'), dict(kind='recipe', slug='rice', multiplier='NaN'),
                       dict(kind='mealplan', start_date='2026-01-01', end_date='2026-02-01'),
                       dict(kind='items', items=[], slug='rice'), items(rice('-1')), items(rice('Infinity')),
                       items(rice(0.3))]:
            with self.subTest(source=str(source)[:70]), self.assertRaises(ValueError):
                self.run_estimate(source)
        self.assertEqual(self.pricing.calls, 0)
    def test_unknown_and_empty(self):
        for source in [items(rice('2', 'cups')), items()]:
            result = self.run_estimate(source)
            self.assertIsNone(result.consumption_subtotal)
            self.assertIsNone(result.procurement_subtotal)
        self.assertFalse(self.run_estimate(items(rice('2', 'cups'))).complete)
    def test_ambiguity_and_override(self):
        self.pricing.products.append(Product('other', 'rice', 'fixture'))
        result = self.run_estimate(items(rice()))
        self.assertIn('ambiguous_match', result.lines[0].uncertainty.reasons)
        self.assertIsNone(result.consumption_subtotal)
        selected = self.run_estimate(items(rice()), product_overrides={result.lines[0].key: 'other'})
        self.assertEqual(selected.consumption_subtotal, D('.6'))
    def test_whole_word_and_dimension(self):
        self.pricing.products = [Product('salsa', 'salsa', 'fixture')]
        self.assertIsNone(self.run_estimate(items(dict(name='sal', quantity='1', unit='g'))).consumption_subtotal)
        self.pricing.products = [Product('rice', 'rice', 'fixture')]
        self.assertIsNone(self.run_estimate(items(rice('1', 'l'))).consumption_subtotal)
    def test_partial_and_package_only(self):
        result = self.run_estimate(items(rice(), rice('2', 'cups')))
        self.assertEqual(result.consumption_subtotal, D('.6'))
        self.assertFalse(result.complete)
        self.pricing.quote = replace(self.pricing.quote, package_quantity=None)
        result = self.run_estimate(items(rice()))
        self.assertIsNone(result.consumption_subtotal)
        self.assertIsNone(result.procurement_subtotal)
    def test_currency_location_and_float_quotes(self):
        for change in [dict(currency='USD'), dict(location=RetailContext('fake', 'elsewhere', 'EUR')),
                       dict(package_price=2.0)]:
            self.pricing.quote = replace(Pricing().quote, **change)
            with self.assertRaises(ValueError):
                self.run_estimate(items(rice()))


if __name__ == '__main__':
    unittest.main()
