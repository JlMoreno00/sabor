"""estimate_cost(exclude_pantry=True) also reads the structured pantry (#144 point 5, prices only)."""
import unittest
from pathlib import Path
from typing import Any

from sabor.pantry.store import PantryStore
import test_pantry_subtraction as _base


class StructuredPantryPricingTests(unittest.TestCase):
    setUp = _base.PantryTests.setUp
    bind = _base.PantryTests.bind
    call = _base.PantryTests.call
    pantry = _base.PantryTests.pantry
    # Set by the borrowed PantryTests.setUp.
    root: Path
    store: Any
    server: Any
    pricing: Any

    def stock(self, *changes):
        PantryStore(self.root / 'sabor.db').apply(list(changes))

    def estimate(self, *names_quantities, exclude=True):
        items = [{'name': n, 'quantity': q, 'unit': u} for n, q, u in names_quantities]
        return self.call('estimate_cost', source={'kind': 'items', 'items': items,
                                                  'exclude_pantry': exclude})['data']

    def in_pantry(self, data):
        return [r['name'] for r in data['excluded_rows'] if 'in_pantry' in r['uncertainty']['reasons']]

    def test_an_item_in_the_structured_pantry_is_excluded(self):
        self.stock({'op': 'add', 'name': 'arroz'})  # amount unknown: there is some
        data = self.estimate(('arroz', '300', 'g'), ('pasta', '100', 'g'))
        self.assertEqual(self.in_pantry(data), ['arroz'])
        self.assertEqual(data['pantry'], {'applied': True, 'matched': 1})

    def test_enough_stock_excludes_the_line(self):
        self.stock({'op': 'add', 'name': 'arroz', 'quantity': '1', 'unit': 'kg'})
        self.assertEqual(self.in_pantry(self.estimate(('arroz', '300', 'g'))), ['arroz'])

    def test_too_little_stock_keeps_the_line_priced(self):
        # Excluding it would understate the cost: 100 g at home, 300 g needed.
        self.stock({'op': 'add', 'name': 'rice', 'quantity': '100', 'unit': 'g'})
        data = self.estimate(('rice', '300', 'g'))
        self.assertEqual(self.in_pantry(data), [])
        self.assertEqual(data['pantry'], {'applied': True, 'matched': 0})
        self.assertEqual(data['coverage']['costed'], 1)

    def test_a_matching_item_without_a_comparable_amount_still_excludes_by_name(self):
        # CodeRabbit on #151: «rice» with no amount next to an insufficient 100 g row keeps
        # the by-name rule; stock is compared only when every matching row is comparable.
        self.stock({'op': 'add', 'name': 'rice'}, {'op': 'add', 'name': 'rice', 'quantity': '100', 'unit': 'g'})
        self.assertEqual(self.in_pantry(self.estimate(('rice', '300', 'g'))), ['rice'])
        self.stock({'op': 'add', 'name': 'egg', 'quantity': '1', 'unit': 'ud'},
                   {'op': 'add', 'name': 'egg', 'quantity': '200', 'unit': 'g'})
        self.assertEqual(self.in_pantry(self.estimate(('egg', '6', 'ud'))), ['egg'])

    def test_an_estimate_never_writes_the_pantry_file(self):
        # CodeRabbit on #151: pricing is read-only, also for a pantry file without tables.
        import sqlite3
        mealie = {'MEALIE_BASE_URL': 'http://mealie', 'MEALIE_API_TOKEN': 't'}
        path = self.root / 'pantry.db'
        sqlite3.connect(path).close()  # an empty file: no pantry tables
        before = path.read_bytes()
        with self.patched_env(mealie):
            data = self.estimate(('rice', '300', 'g'))
        self.assertEqual(self.in_pantry(data), [])
        self.assertEqual(path.read_bytes(), before)
        with sqlite3.connect(path) as connection:
            self.assertEqual(connection.execute("SELECT name FROM sqlite_master").fetchall(), [])
        # A filled SQLite pantry is read without being rewritten either.
        self.stock({'op': 'add', 'name': 'rice', 'quantity': '1', 'unit': 'kg'})
        store_db = self.root / 'sabor.db'
        before = store_db.read_bytes()
        self.assertEqual(self.in_pantry(self.estimate(('rice', '300', 'g'))), ['rice'])
        self.assertEqual(store_db.read_bytes(), before)

    def patched_env(self, extra):
        """Rebind the tools with the Mealie store setting (pantry in <data dir>/pantry.db)."""
        from contextlib import contextmanager
        from unittest.mock import patch
        from sabor.core.config import load_settings
        from sabor.core.context import build_context
        from sabor.server import register_tools

        @contextmanager
        def bound():
            env = {'SABOR_DATA_DIR': str(self.root), 'SABOR_TOOLSETS': 'core,pricing',
                   'SABOR_MERCADONA_ENABLED': 'true', **extra}
            ctx = build_context(load_settings(env), store_factory=lambda s: self.store,
                                pricing_factories=(lambda s: self.pricing,))
            previous = self.server
            self.server = _base.Server()
            with patch('sabor.server.assemble_context', return_value=ctx):
                register_tools(self.server, env)
            try:
                yield
            finally:
                self.server = previous
        return bound()

    def test_plural_and_accent_match_like_the_profile_pantry(self):
        self.stock({'op': 'add', 'name': 'Limón', 'quantity': '2', 'unit': 'ud'})
        self.assertEqual(self.in_pantry(self.estimate(('limones', '1', 'ud'))), ['limones'])

    def test_the_profile_pantry_still_applies(self):
        self.pantry(['sal'])
        self.stock({'op': 'add', 'name': 'arroz', 'quantity': '1', 'unit': 'kg'})
        self.assertEqual(self.in_pantry(self.estimate(('sal', '5', 'g'), ('arroz', '300', 'g'))),
                         ['sal', 'arroz'])

    def test_without_exclude_pantry_nothing_changes(self):
        self.stock({'op': 'add', 'name': 'arroz', 'quantity': '1', 'unit': 'kg'})
        data = self.estimate(('arroz', '300', 'g'), exclude=False)
        self.assertNotIn('pantry', data)
        self.assertEqual(self.in_pantry(data), [])

    def test_shopping_list_hints_ignore_the_structured_pantry(self):
        # add_to_shopping_list is out of scope here: its advisory hints stay on the profile.
        self.stock({'op': 'add', 'name': 'arroz', 'quantity': '1', 'unit': 'kg'})
        listing = self.store.create_shopping_list('test')
        result = self.call('add_to_shopping_list', list_id=listing.id, items=['arroz'])
        self.assertTrue(result['ok'], result)
        self.assertNotIn('pantry_matches', result['data'])


if __name__ == '__main__':
    unittest.main()
