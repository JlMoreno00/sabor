"""Shopping lists and the structured pantry (#144, José's decision on question 1).

What the pantry covers in full is marked as bought (checked) and stays on the list; what it
covers only in part stays unchecked with the amount still missing. Store-agnostic: it uses
only the store's get/check shopping item calls, so SQLite and Mealie behave the same.
"""
import unittest
from decimal import Decimal
from pathlib import Path
from typing import Any

import test_pantry_subtraction as _base
from sabor.pantry.model import PantryItem
from sabor.pantry.shopping import cover_items
from sabor.pantry.store import PantryStore
from sabor.store import NamedRef, ShoppingItem


def _pantry(food, quantity, unit):
    return PantryItem('p-' + food + (unit or ''), food, food, None,
                      None if quantity is None else Decimal(quantity), unit, None, 'manual', None, 't')


def _item(item_id, note=None, quantity=None, unit=None, food=None):
    """Shaped like Mealie's structured items (food/unit/quantity) or a free-text note."""
    return ShoppingItem(note=note, quantity=quantity, unit=NamedRef(name=unit) if unit else None,
                        food=NamedRef(name=food) if food else None, id=item_id, list_id='l')


class CoverItemsTests(unittest.TestCase):
    def test_full_partial_and_uncovered(self):
        rows = cover_items(
            [_item('1', quantity=300, unit='g', food='arroz'),         # 1 kg at home
             _item('2', quantity=6, food='huevos'),                    # 4 at home
             _item('3', quantity=1, unit='l', food='leche'),            # none at home
             _item('4', note='2 tomates')],                            # free text, 5 at home
            [_pantry('arroz', '1000', 'g'), _pantry('huevo', '4', 'ud'), _pantry('tomate', '5', 'ud')])
        self.assertEqual([(r['item'], r['status']) for r in rows],
                         [('1', 'covered'), ('2', 'partial'), ('3', 'not_in_pantry'), ('4', 'covered')])
        self.assertEqual(rows[1]['missing'], {'quantity': '2', 'unit': 'ud'})

    def test_nothing_is_guessed(self):
        rows = cover_items(
            [_item('1', note='sal'),                                   # no amount to compare
             _item('2', quantity=200, unit='g', food='queso'),         # pantry amount unknown
             _item('3', quantity=500, unit='ml', food='leche')],       # pantry kept in grams
            [_pantry('sal', '1000', 'g'), _pantry('queso', None, None), _pantry('leche', '500', 'g')])
        self.assertEqual([r['status'] for r in rows], ['not_measured', 'pantry_amount_unknown', 'incompatible_unit'])


class ShoppingPantryToolTests(unittest.TestCase):
    setUp = _base.PantryTests.setUp
    bind = _base.PantryTests.bind
    call = _base.PantryTests.call
    pantry = _base.PantryTests.pantry
    # Set by the borrowed PantryTests.setUp.
    root: Path
    store: Any

    def stock(self, *changes):
        PantryStore(self.root / 'sabor.db').apply(list(changes))

    def items(self, list_id):
        return {i.note: i.checked for i in self.store.get_shopping_items(list_id).items}

    def test_covered_items_are_checked_and_partial_ones_report_what_is_missing(self):
        self.stock({'op': 'add', 'name': 'arroz', 'quantity': '1', 'unit': 'kg'},
                   {'op': 'add', 'name': 'huevo', 'quantity': '2', 'unit': 'ud'})
        listing = self.store.create_shopping_list('semana')
        result = self.call('add_to_shopping_list', list_id=listing.id,
                           items=['300 g arroz', '6 huevos', '1 l leche'])
        self.assertTrue(result['ok'], result)
        self.assertEqual(self.items(listing.id), {'300 g arroz': True, '6 huevos': False, '1 l leche': False})
        coverage = result['data']['pantry_coverage']
        self.assertEqual([(r['name'], r['status']) for r in coverage['items']],
                         [('arroz', 'covered'), ('huevos', 'partial'), ('leche', 'not_in_pantry')])
        self.assertEqual(coverage['items'][1]['missing'], {'quantity': '4', 'unit': 'ud'})
        self.assertEqual(len(coverage['checked']), 1)
        # The pantry itself is not changed by building a list.
        self.assertEqual(len(PantryStore(self.root / 'sabor.db').list_items()), 2)

    def test_subtract_pantry_false_keeps_the_previous_behaviour(self):
        self.stock({'op': 'add', 'name': 'arroz', 'quantity': '1', 'unit': 'kg'})
        listing = self.store.create_shopping_list('semana')
        result = self.call('add_to_shopping_list', list_id=listing.id, items=['300 g arroz'],
                           subtract_pantry=False)
        self.assertTrue(result['ok'], result)
        self.assertNotIn('pantry_coverage', result['data'])
        self.assertEqual(self.items(listing.id), {'300 g arroz': False})

    def test_items_already_on_the_list_are_left_alone(self):
        listing = self.store.create_shopping_list('semana')
        self.call('add_to_shopping_list', list_id=listing.id, items=['300 g arroz'], subtract_pantry=False)
        self.stock({'op': 'add', 'name': 'arroz', 'quantity': '1', 'unit': 'kg'})
        result = self.call('add_to_shopping_list', list_id=listing.id, items=['1 l leche'])
        self.assertEqual(self.items(listing.id), {'300 g arroz': False, '1 l leche': False})
        self.assertEqual([r['name'] for r in result['data']['pantry_coverage']['items']], ['leche'])

    def test_no_pantry_file_means_no_coverage_and_nothing_created(self):
        listing = self.store.create_shopping_list('semana')
        result = self.call('add_to_shopping_list', list_id=listing.id, items=['300 g arroz'])
        self.assertTrue(result['ok'], result)
        self.assertNotIn('pantry_coverage', result['data'])
        self.assertFalse((self.root / 'sabor.db').exists())

    def test_invalid_subtract_pantry_writes_nothing(self):
        listing = self.store.create_shopping_list('semana')
        result = self.call('add_to_shopping_list', list_id=listing.id, items=['arroz'], subtract_pantry='yes')
        self.assertEqual(result['error']['code'], 'invalid_argument')
        self.assertEqual(self.items(listing.id), {})


if __name__ == '__main__':
    unittest.main()
