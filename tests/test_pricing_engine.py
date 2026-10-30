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


def rice(amount: object = '300', unit: str | None = 'g'):
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
    def test_shopping_list_free_text_items(self):
        # add_to_shopping_list(items=[...]) creates note-only Mealie items with a
        # placeholder quantity 1 and no food/unit; the note carries the amount.
        ref = self.store.create_shopping_list('notes')
        self.store.add_shopping_items(ref.id, [ItemDraft(quantity=1, note='300 g de rice'),
                                              ItemDraft(quantity=1, note='rice al gusto')])
        result = self.run_estimate(dict(kind='shopping_list', list_id=ref.id))
        first, second = result.lines
        self.assertEqual(first.name, 'rice')
        self.assertEqual(first.required_quantity, D(300))
        self.assertEqual(first.consumption, D('.6'))
        self.assertEqual(second.name, 'rice')
        self.assertIsNone(second.consumption)

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
        result = self.run_estimate(items(rice()), match_policy='strict')
        self.assertIn('ambiguous_match', result.lines[0].uncertainty.reasons)
        self.assertIsNone(result.consumption_subtotal)
        selected = self.run_estimate(items(rice()), product_overrides={result.lines[0].key: 'other'})
        self.assertEqual(selected.consumption_subtotal, D('.6'))
    def test_cheapest_food_and_count(self):
        self.pricing.products = [Product('cosmetic', 'rice', 'fixture', food=False),
                                 Product('other', 'rice', 'fixture'), Product('rice', 'rice', 'fixture')]
        result = self.run_estimate(items(rice()))
        line = result.lines[0]
        self.assertEqual(line.uncertainty.level, 'estimated')
        self.assertIn('auto_selected_cheapest', line.uncertainty.reasons)
        self.assertNotIn('cosmetic', line.candidates)
        self.assertTrue(line.alternatives)
        self.pricing.products = [Product('rice', 'rice', 'fixture')]
        self.pricing.quote = replace(self.pricing.quote, package_unit='unit',
                                     package_quantity=D(12), normalized_unit_price=D('.2'))
        line = self.run_estimate(items(rice('6', None))).lines[0]
        self.assertEqual(line.consumption, D('1.2'))
        self.assertIn('assumed_count', line.uncertainty.reasons)
        self.run_estimate(items(dict(name='rice', text='6 rice'))).lines[0]
        # Structured count inference must not invent weights for kitchen measures.
        self.assertIsNone(self.run_estimate(items(rice('6', 'cups'))).consumption_subtotal)
        with self.assertRaises(ValueError):
            self.run_estimate(items(rice()), match_policy='bad')

    def test_cheapest_minimizes_checkout_cost(self):
        # 1 l needed: a 6 l pack is cheaper per litre (0.81) but costs 4.86 at the
        # checkout; the 1 l bottle (0.95) is what the user should pay.
        quotes = {'bulk': (D('4.86'), D(6), D('.81')), 'bottle': (D('.95'), D(1), D('.95')),
                  'twin': (D('1.80'), D(2), D('.90'))}
        self.pricing.products = [Product(pid, 'leche', 'fixture') for pid in quotes]
        base = self.pricing.quote

        def quote(product_id, store):
            price, size, unit_price = quotes[product_id]
            return replace(base, product_id=product_id, package_price=price, package_quantity=size,
                           package_unit='l', normalized_unit_price=unit_price)
        setattr(self.pricing, 'get_quote', quote)
        result = self.run_estimate(items(dict(name='leche', quantity='1', unit='l')))
        line = result.lines[0]
        assert line.quote is not None
        self.assertEqual(line.quote.product_id, 'bottle')
        self.assertEqual(result.procurement_subtotal, D('.95'))
        self.assertEqual(result.consumption_subtotal, D('.95'))
        self.assertEqual([a['product_id'] for a in line.alternatives], ['twin', 'bulk'])
        self.assertEqual(line.alternatives[0]['checkout_cost'], D('1.80'))
        # Ties on checkout cost fall back to the lower unit price.
        quotes['twin'] = (D('.95'), D(2), D('.475'))
        tied = self.run_estimate(items(dict(name='leche', quantity='1', unit='l'))).lines[0].quote
        assert tied is not None
        self.assertEqual(tied.product_id, 'twin')

    def basket(self, prices, *names, package_unit='kg'):
        # Real Mercadona names; processed products are the cheaper ones, as in the catalog.
        self.pricing.products = [Product(name, name, 'fixture') for name in prices]
        base = self.pricing.quote

        def quote(product_id, store):
            # A None price is a candidate whose quote cannot be valued.
            price = None if prices[product_id] is None else D(prices[product_id])
            return replace(base, product_id=product_id, package_price=price,
                           package_quantity=D(1), package_unit=package_unit, normalized_unit_price=price)
        setattr(self.pricing, 'get_quote', quote)
        rows = (n if isinstance(n, dict) else dict(name=n, quantity='300', unit='g') for n in names)
        lines = self.run_estimate(items(*rows)).lines
        return [line.quote.product_id if line.quote else None for line in lines]

    def test_basic_ingredients_prefer_unprocessed_products(self):
        # Paleta (29-oct): 'merluza' picked croquetas and 'jengibre' chewing gum.
        prices = {'Croquetas de merluza Hacendado ultracongelados': '3', 'Filetes de merluza del Cabo': '9',
                  'Chicles gragea jengibre sin azúcar Hacendado': '1', 'Jengibre': '5',
                  'Sándwich atún y huevo Hacendado': '2', 'Atún claro al natural Hacendado': '8',
                  'Alubias a la jardinera Hacendado': '1', 'Alubia blanca Hacendado': '2',
                  'Caldo de pollo Hacendado': '1', 'Contramuslo de pollo': '4'}
        self.assertEqual(self.basket(prices, 'merluza', 'jengibre', 'atún', 'alubias', 'caldo de pollo'),
                         ['Filetes de merluza del Cabo', 'Jengibre', 'Atún claro al natural Hacendado',
                          'Alubia blanca Hacendado', 'Caldo de pollo Hacendado'])
        # A processed product is still used when it is the only match.
        self.assertEqual(self.basket({'Croquetas de merluza Hacendado ultracongelados': '3'}, 'merluza'),
                         ['Croquetas de merluza Hacendado ultracongelados'])
        # A requested modifier is never penalized.
        self.assertEqual(self.basket(prices, 'croquetas de merluza'),
                         ['Croquetas de merluza Hacendado ultracongelados'])

    def test_singular_and_plural_names_match(self):
        # Paleta (29-oct): zanahoria and puerro found nothing, the catalog sells them in plural.
        self.assertEqual(self.basket({'Zanahorias': '1', 'Puerros': '2', 'Guisante fino Hacendado': '3'},
                                     'zanahoria', 'puerro', 'guisantes'),
                         ['Zanahorias', 'Puerros', 'Guisante fino Hacendado'])
        # Still whole words only: 'sal' never matches 'salsa'.
        self.assertEqual(self.basket({'salsa': '1'}, 'sal'), [None])

    def test_query_numbers_must_match(self):
        # CodeRabbit on #105: digits are part of the query (85% is not 70%).
        self.assertEqual(self.basket({'Chocolate negro 70% Hacendado': '1', 'Chocolate negro 85% Hacendado': '2'},
                                     'chocolate negro 85%'), ['Chocolate negro 85% Hacendado'])
        # A decimal is one value: 0,5% is neither 5,0% nor 5%.
        fat = {'Leche 5,0% materia grasa': '1', 'Leche 0,5% materia grasa': '2'}
        self.assertEqual(self.basket(fat, 'leche 0,5%', 'leche 5,0%'),
                         ['Leche 0,5% materia grasa', 'Leche 5,0% materia grasa'])

    def test_leading_amount_in_a_name_is_not_a_required_number(self):
        # CodeRabbit on #105: a leading number equal to the line's quantity is that
        # amount ('2 huevos', 2), so 'Huevos' stays a candidate next to '2 huevos cocidos'.
        self.assertEqual(self.basket({'Huevos': '1', '2 huevos cocidos Hacendado': '3'},
                                     dict(name='2 huevos', quantity='2', unit='g'),
                                     dict(name='300 g de arroz', quantity='300', unit='g')), ['Huevos', None])
        # Any other leading number is part of the name ('4 quesos' for 300 g).
        self.assertEqual(self.basket({'Pizza 3 quesos Hacendado': '1', 'Pizza 4 quesos Hacendado': '2'},
                                     '4 quesos'), ['Pizza 4 quesos Hacendado'])

    def test_leading_amount_compares_the_resolved_quantity(self):
        # #49 follow-up (CodeRabbit on #105): the name's leading amount is compared with
        # the resolved quantity in base units, not with the raw quantity field.
        self.assertEqual(self.basket({'Arroz redondo Hacendado': '1'},
                                     dict(name='300 g de arroz', quantity='0.3', unit='kg'),
                                     dict(name='1 l de leche', quantity='1000', unit='ml')),
                         ['Arroz redondo Hacendado', None])
        # A zero placeholder takes the amount from the text: '2 huevos' is that amount.
        self.assertEqual(self.basket({'Huevos frescos': '2'},
                                     dict(name='2 huevos', quantity='0', text='2 huevos'), package_unit='unit'),
                         ['Huevos frescos'])
        # Different resolved amounts still keep the number in the name.
        self.assertEqual(self.basket({'Pizza 3 quesos Hacendado': '1', 'Pizza 4 quesos Hacendado': '2'},
                                     dict(name='4 quesos', quantity='0.3', unit='kg')),
                         ['Pizza 4 quesos Hacendado'])
        # CodeRabbit on #106: only the leading prefix is parsed, so later name numbers
        # ('85%') do not hide the unit of '500 g'.
        self.assertEqual(self.basket({'Chocolate negro 85% Hacendado': '2'},
                                     dict(name='0,5 kg de chocolate negro 85%', quantity='500', unit='g')),
                         ['Chocolate negro 85% Hacendado'])
        # A weight or volume of another dimension is not the line's amount: '300 ml' is
        # kept for 0.3 kg instead of being stripped down to 'leche'.
        self.assertEqual(self.basket({'Leche entera Hacendado': '1'},
                                     dict(name='300 ml de leche', quantity='0.3', unit='kg')), [None])

    def test_text_amount_with_a_bare_leading_number_prefers_products_carrying_it(self):
        # #49 follow-up 2 (CodeRabbit on #105): without a structured quantity the amount
        # comes from the text itself, so '4 quesos' may be 4 cheeses or a 4-cheese
        # product. Products carrying the number win; otherwise the number is dropped.
        quesos = {'Pizza 3 quesos Hacendado': '1', 'Pizza 4 quesos Hacendado': '2'}
        self.assertEqual(self.basket(quesos, dict(name='4 quesos', text='4 quesos'), package_unit='unit'),
                         ['Pizza 4 quesos Hacendado'])
        self.assertEqual(self.basket({'Quesos variados Hacendado': '2'}, dict(name='4 quesos', text='4 quesos'),
                                     package_unit='unit'), ['Quesos variados Hacendado'])
        # A countable food the parser reads as pieces ('2 huevos') is plainly an amount.
        self.assertEqual(self.basket({'Huevos frescos': '1', '2 huevos cocidos Hacendado': '3'},
                                     dict(name='2 huevos', text='2 huevos'), package_unit='unit'),
                         ['Huevos frescos'])

    def test_ambiguous_number_falls_back_to_a_search_without_it(self):
        # CodeRabbit on #108: a substring-filtering provider (Open Prices) returns nothing
        # for '4 quesos' when no product carries the 4; the name without it is searched too.
        queries = []

        def substring_search(query, store):
            queries.append(query.query)
            return [p for p in self.pricing.products if query.query.casefold() in p.name.casefold()]
        setattr(self.pricing, 'search_products', substring_search)
        self.assertEqual(self.basket({'Quesos variados Hacendado': '2'}, dict(name='4 quesos', text='4 quesos'),
                                     package_unit='unit'), ['Quesos variados Hacendado'])
        self.assertEqual(queries, ['4 quesos', 'quesos'])
        # CodeRabbit on #108: a '4 quesos' result that is not eligible (here non-food) must
        # not block the fallback; it depends on eligible candidates, not raw results.
        queries.clear()

        def ineligible_carrier(query, store):
            queries.append(query.query)
            return [replace(p, food=False) if '4' in p.name else p for p in self.pricing.products
                    if query.query.casefold() in p.name.casefold()]
        setattr(self.pricing, 'search_products', ineligible_carrier)
        self.assertEqual(self.basket({'Bolsa 4 quesos de tela': '1', 'Quesos variados Hacendado': '2'},
                                     dict(name='4 quesos', text='4 quesos'), package_unit='unit'),
                         ['Quesos variados Hacendado'])
        self.assertEqual(queries, ['4 quesos', 'quesos'])

    def test_provider_search_uses_the_decided_name(self):
        # #49 follow-up 3: the provider is queried with the name the engine matches on,
        # so a stripped amount never narrows the search and a name number is kept.
        queries = []
        search = self.pricing.search_products

        def record(query, store):
            queries.append(query.query)
            return search(query, store)
        setattr(self.pricing, 'search_products', record)
        self.basket({'Huevos': '1'}, dict(name='2 huevos', quantity='2', unit='g'), '4 quesos', 'arroz')
        self.assertEqual(queries, ['huevos', '4 quesos', 'arroz'])
        # Text-only '4 quesos' is ambiguous: the number stays in the query so the
        # provider can rank products carrying it first.
        queries.clear()
        self.basket({'Pizza 4 quesos Hacendado': '1'}, dict(name='4 quesos', text='4 quesos'), package_unit='unit')
        self.assertEqual(queries, ['4 quesos'])

    def test_alphanumeric_words_stay_whole(self):
        # CodeRabbit on #105: B12 is neither B nor 12.
        self.assertEqual(self.basket({'Leche vitamina B6 12 unidades': '1', 'Leche vitamina B12': '2'},
                                     'leche vitamina B12'), ['Leche vitamina B12'])

    def test_unvalued_basic_product_keeps_a_valued_processed_one(self):
        # CodeRabbit on #105: the preference must not leave the line uncosted.
        self.assertEqual(self.basket({'Filetes de merluza del Cabo': None,
                                      'Croquetas de merluza Hacendado ultracongelados': '3'}, 'merluza'),
                         ['Croquetas de merluza Hacendado ultracongelados'])

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
