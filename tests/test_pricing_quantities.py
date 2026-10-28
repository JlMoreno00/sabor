"""Offline quantity contracts; ambiguous prose never supplies hidden weights."""
import unittest
from typing import Any, cast
from decimal import Decimal as D

from sabor.pricing.quantities import (
    Dimension, Quantity, from_structured, package_from_mercadona, parse_text, to_base,
)


class ParseTests(unittest.TestCase):
    def test_real_world_table(self):
        # Ranges deliberately have no amount, not an undocumented upper bound.
        rows = [
            ('200 g de harina', '200', 'g', 'mass', ()),
            ('1,5 kg patatas', '1.5', 'kg', 'mass', ()),
            ('2 huevos', '2', 'ud', 'count', ()),
            ('½ cebolla', '0.5', 'ud', 'count', ()),
            ('1/2 kg arroz', '0.5', 'kg', 'mass', ()),
            ('1 1/2 l caldo', '1.5', 'l', 'volume', ()),
            ('¼ litro leche', '0.25', 'l', 'volume', ()),
            ('¾ kg tomates', '0.75', 'kg', 'mass', ()),
            ('250 gr azúcar', '250', 'g', 'mass', ()),
            ('100 gramos mantequilla', '100', 'g', 'mass', ()),
            ('2 kilos naranjas', '2', 'kg', 'mass', ()),
            ('1 kilo arroz', '1', 'kg', 'mass', ()),
            ('30 ml aceite', '30', 'ml', 'volume', ()),
            ('5 cl vinagre', '5', 'cl', 'volume', ()),
            ('1 l leche', '1', 'l', 'volume', ()),
            ('dos litros agua', '2', 'l', 'volume', ()),
            ('3 ud huevos', '3', 'ud', 'count', ()),
            ('2 unidades limón', '2', 'ud', 'count', ()),
            ('1 u manzana', '1', 'ud', 'count', ()),
            ('tres piezas fruta', '3', 'ud', 'count', ()),
            ('1 docena de huevos', '12', 'ud', 'count', ()),
            ('una cebolla', '1', 'ud', 'count', ()),
            ('un huevo', '1', 'ud', 'count', ()),
            ('cuatro tomates', '4', 'ud', 'count', ()),
            ('2 eggs', '2', 'ud', 'count', ()),
            ('0.5 kg flour', '0.5', 'kg', 'mass', ()),
            ('1 cucharada de aceite', '1', None, None, ('kitchen_measure',)),
            ('2 cda miel', '2', None, None, ('kitchen_measure',)),
            ('1 cucharadita sal', '1', None, None, ('kitchen_measure',)),
            ('1 cdta sal', '1', None, None, ('kitchen_measure',)),
            ('1 taza arroz', '1', None, None, ('kitchen_measure',)),
            ('1 cup flour', '1', None, None, ('kitchen_measure',)),
            ('sal al gusto', None, None, None, ('unspecified',)),
            ('una pizca de sal', None, None, None, ('unspecified',)),
            ('aceite c/s', None, None, None, ('unspecified',)),
            ('una lata de tomate (400 g)', '1', None, None, ('package',)),
            ('3-4 dientes de ajo', None, None, None, ('range',)),
            ('2–3 huevos', None, None, None, ('range',)),
            ('harina', None, None, None, ('unparsed',)),
            ('0 g sal', None, None, None, ('invalid_amount',)),
            ('-2 kg arroz', None, None, None, ('invalid_amount',)),
            ('1/0 kg arroz', None, None, None, ('invalid_amount',)),
            ('2 oz harina', '2', None, None, ('unknown_unit',)),
            ('1 kg y 200 g harina', '1', None, None, ('compound',)),
        ]
        for text, amount, unit, dimension, ambiguity in rows:
            with self.subTest(text=text):
                q = parse_text(text)
                self.assertEqual((q.amount, q.unit, q.dimension, q.ambiguity),
                                 (D(amount) if amount else None, unit, dimension, ambiguity))
                self.assertEqual(q.source, 'text' if amount else 'unknown')

    def test_structured_preferred(self):
        q = from_structured('250', 'gramos', 'harina', '1 kg harina')
        self.assertEqual(q, Quantity(D('250'), 'g', 'mass', 'structured', ()))

    def test_placeholder_never_free(self):
        for value in (0, None, '0'):
            with self.subTest(value=value):
                self.assertIsNone(from_structured(value, 'g', 'sal', 'sal').amount)
                self.assertEqual(from_structured(value, 'g', 'sal', '2 g sal').amount, D(2))

    def test_unknown_structured_unit_not_overridden(self):
        q = from_structured('2', 'puñado', 'arroz', '200 g arroz')
        self.assertEqual((q.amount, q.dimension, q.source, q.ambiguity),
                         (D(2), None, 'structured', ('unknown_unit',)))
        self.assertIsNone(from_structured('2', None, 'harina', '').dimension)

    def test_bad_structured_values(self):
        for value in (-1, 'NaN', 'Infinity', True, [], {}, float('nan')):
            with self.subTest(value=repr(value)):
                self.assertIsNone(from_structured(value, 'kg', '', '1 kg arroz').amount)

    def test_word_boundaries_and_decimal_placeholders(self):
        self.assertIsNone(parse_text('unicornio').amount)
        for zero in (D('0E-3'), '0,0', '0.000'):
            self.assertEqual(from_structured(zero, 'g', '', '2 g sal').amount, D(2))

    def test_nontext(self):
        for value in (None, {}, 42, ''):
            self.assertIsNone(parse_text(value).amount)


class ConversionTests(unittest.TestCase):
    def test_base_conversions(self):
        for text, amount, unit in [('1.5 kg arroz', '1500', 'g'),
                                   ('2 l agua', '2000', 'ml'),
                                   ('5 cl vino', '50', 'ml'),
                                   ('2 huevos', '2', 'ud'), ('50 g sal', '50', 'g')]:
            with self.subTest(text=text):
                q = to_base(parse_text(text))
                self.assertEqual((q.amount, q.unit), (D(amount), unit))

    def test_dimension_mismatch(self):
        for text, target in [('1 l leche', 'mass'), ('2 huevos', 'mass'),
                             ('200 g arroz', 'count')]:
            q = to_base(parse_text(text), target_dimension=cast(Dimension, target))
            self.assertIsNone(q.amount)
            self.assertIn('dimension_mismatch', q.ambiguity)

    def test_explicit_density_grams_per_ml(self):
        q = to_base(parse_text('2 l leche'), target_dimension='mass', density=D('1.03'))
        self.assertEqual((q.amount, q.unit), (D('2060'), 'g'))
        q = to_base(parse_text('103 g leche'), target_dimension='volume', density=D('1.03'))
        self.assertEqual(q.amount, D('100'))

    def test_explicit_piece_weight_grams(self):
        q = to_base(parse_text('2 huevos'), target_dimension='mass', piece_weight=D('60'))
        self.assertEqual(q.amount, D('120'))
        q = to_base(parse_text('120 g huevo'), target_dimension='count', piece_weight=D('60'))
        self.assertEqual(q.amount, D('2'))

    def test_bad_factors_and_unknowns(self):
        for factor in (D(0), D(-1), D('NaN'), True, 1.03):
            q = to_base(parse_text('1 l leche'), target_dimension='mass', density=cast(Any, factor))
            self.assertIsNone(q.amount)
        q = parse_text('1 taza arroz')
        self.assertEqual(to_base(q), q)


class PackageTests(unittest.TestCase):
    def oil(self) -> dict[str, Any]:
        return dict(unit_price='17.25', unit_size=5.0, size_format='l',
                    reference_price='3.450', reference_format='L', is_pack=False,
                    pack_size=None, total_units=None, drained_weight=None,
                    approx_size=False, selling_method=0)

    def test_five_litre_oil(self):
        p = package_from_mercadona(self.oil())
        self.assertEqual(p.quantity, Quantity(D(5), 'l', 'volume', 'structured', ()))
        self.assertEqual(p.package_price, D('17.25'))
        self.assertEqual((p.reference_price, p.reference_unit), (D('3.450'), 'l'))
        self.assertEqual(p.ambiguity, ())

    def test_formats(self):
        for unit, dimension in [('kg', 'mass'), ('g', 'mass'), ('ml', 'volume'), ('ud', 'count')]:
            p = package_from_mercadona(dict(unit_size='2', size_format=unit))
            self.assertEqual((p.quantity.amount, p.quantity.dimension), (D(2), dimension))
        p = package_from_mercadona(dict(unit_size=2, size_format='bag'))
        self.assertIsNone(p.quantity.dimension)
        self.assertIn('unknown_unit', p.ambiguity)

    def test_unverified_package_formats(self):
        for name in ('cl', 'kilos', 'cup', [], None):
            data = self.oil()
            data.update(size_format=name, reference_format=name)
            p = package_from_mercadona(data)
            self.assertIsNone(p.quantity.unit)
            self.assertIsNone(p.reference_unit)
            self.assertIn('unknown_unit', p.ambiguity)

    def test_uncertainties(self):
        for field, value, reason in [('approx_size', True, 'approx_size'),
                                     ('selling_method', 1, 'selling_method'),
                                     ('is_pack', True, 'pack'), ('pack_size', 6, 'pack'),
                                     ('total_units', 6, 'pack'),
                                     ('drained_weight', '0.2', 'drained_weight')]:
            data = self.oil()
            data[field] = value
            with self.subTest(field=field):
                self.assertIn(reason, package_from_mercadona(data).ambiguity)

    def test_defensive_types(self):
        for value in (None, [], 'bad', 1):
            self.assertIsNone(package_from_mercadona(value).quantity.amount)
        for value in (True, {}, [], 'NaN', 'Infinity', '-1', 0):
            data = self.oil()
            data.update(unit_size=value, unit_price=value, reference_price=value)
            with self.subTest(value=repr(value)):
                p = package_from_mercadona(data)
                self.assertIsNone(p.quantity.amount)
                self.assertIsNone(p.package_price)
                self.assertIsNone(p.reference_price)
        for field in ('approx_size', 'is_pack', 'selling_method'):
            data = self.oil()
            data[field] = 'false'
            self.assertIn('invalid_metadata', package_from_mercadona(data).ambiguity)
        data = self.oil()
        data['reference_format'] = []
        self.assertIsNone(package_from_mercadona(data).reference_unit)


if __name__ == '__main__':
    unittest.main()


class IngredientNameTests(unittest.TestCase):
    def test_strips_leading_amount_unit_and_connector(self):
        from sabor.pricing.quantities import ingredient_name
        for text, expected in [('1 l leche', 'leche'), ('300 g de arroz', 'arroz'),
                               ('1,5 kg de patatas', 'patatas'), ('2 huevos', 'huevos'),
                               ('una cebolla', 'cebolla'), ('1 cucharada de aceite', 'aceite'),
                               ('sal al gusto', 'sal'), ('leche', 'leche'), (None, '')]:
            with self.subTest(text=text):
                self.assertEqual(ingredient_name(text), expected)

    def test_english_unit_words(self):
        # #49 decision: English unit words are stripped too, with every store (Mealie included).
        from sabor.pricing.quantities import ingredient_name
        for text, expected in [('300 grams of rice', 'rice'), ('1 gram salt', 'salt'),
                               ('2 kilograms of potatoes', 'potatoes'), ('1 kilogram of flour', 'flour'),
                               ('1 liter of milk', 'milk'), ('2 litres of water', 'water'),
                               ('500 milliliters of stock', 'stock'), ('500 millilitres of stock', 'stock'),
                               ('2 units of pepper', 'pepper'), ('3 pieces of bread', 'bread'),
                               ('1 piece of cake', 'cake'), ('1 dozen eggs', 'eggs'),
                               # Remaining singular and plural forms of the same unit words.
                               ('3 liters of juice', 'juice'), ('1 litre of oil', 'oil'),
                               ('250 milliliter cream', 'cream'), ('1 unit of lemon', 'lemon'),
                               ('2 dozens eggs', 'eggs')]:
            with self.subTest(text=text):
                self.assertEqual(ingredient_name(text), expected)

    def test_spanish_names_are_unchanged(self):
        # Frozen before the English units were added: Spanish lines must keep these names.
        from sabor.pricing.quantities import ingredient_name
        frozen = {
            '1 l leche': 'leche', '300 g de arroz': 'arroz', '1,5 kg de patatas': 'patatas',
            '2 huevos': 'huevos', 'una cebolla': 'cebolla', '1 cucharada de aceite': 'aceite',
            'sal al gusto': 'sal', 'leche': 'leche', '200 gr de harina': 'harina',
            '2 gramos de sal': 'sal', '1 kilo de naranjas': 'naranjas', '500 ml de caldo': 'caldo',
            '1 litro de agua': 'agua', '2 unidades de pimiento': 'pimiento', '3 piezas de pan': 'pan',
            '1 docena de huevos': 'huevos', '1 lata de tomate': 'tomate', '2 cdas de azúcar': 'azúcar',
            '1 taza de leche': 'leche', '50 cl de vino': 'vino', '1 ud de calabacín': 'calabacín',
            '2 dientes de ajo': 'dientes de ajo', '1 pizca de sal': 'pizca de sal',
            'medio limón': 'medio limón', 'aceite de oliva': 'aceite de oliva',
            'unas hojas de laurel': 'unas hojas de laurel', '100 g queso rallado': 'queso rallado',
            '2 tazas de harina de trigo': 'harina de trigo', '1 bote de garbanzos': 'garbanzos',
            '1 paquete de pasta': 'pasta',
        }
        for text, expected in frozen.items():
            with self.subTest(text=text):
                self.assertEqual(ingredient_name(text), expected)
