"""Instance pantry contracts through registered tools, offline."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from sabor.server import register_tools
from sabor.core.context import build_context
from sabor.core.config import load_settings
from sabor.store import Recipe, Ingredient, NamedRef, MealEntryDraft
from sabor.store.fake import FakeRecipeStore
from sabor.store.sqlite import SqliteRecipeStore
from sabor.store.mealie import MealieRecipeStore
from test_pricing_tools import FakePricing


class Server:
    def __init__(self):
        self.tools = {}

    def add_tool(self, fn):
        self.tools[fn.__name__] = fn


class PantryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.pricing = FakePricing()
        self.store = FakeRecipeStore()
        self.bind(self.store)

    def bind(self, store):
        self.store = store
        env = {'SABOR_DATA_DIR': str(self.root), 'SABOR_TOOLSETS': 'core,pricing',
               'SABOR_MERCADONA_ENABLED': 'true'}
        ctx = build_context(load_settings(env), store_factory=lambda s: store,
                            pricing_factories=(lambda s: self.pricing,))
        self.server = Server()
        with patch('sabor.server.assemble_context', return_value=ctx):
            register_tools(self.server, env)

    def pantry(self, entries):
        from sabor.preferences import empty_preferences
        profile = empty_preferences()
        profile['pantry'] = entries
        (self.root / 'preferences.json').write_text(json.dumps({
            'schema_version': 1, 'profiles': {'default': profile}, 'updated_at': 'now'}))

    def call(self, name, **kw):
        return self.server.tools[name](**kw)

    def test_default_identical_and_false(self):
        source = {'kind': 'items', 'items': [{'name': 'rice', 'quantity': '300', 'unit': 'g'}]}
        before = self.call('estimate_cost', source=source)
        self.pantry(['rice'])
        self.assertEqual(before, self.call('estimate_cost', source=source))
        self.assertEqual(before, self.call('estimate_cost', source={**source, 'exclude_pantry': False}))

    def test_exclusions_no_pricing_and_no_substrings(self):
        self.pantry(['RÍCE', 'huevo', 'sal'])
        source = {'kind': 'items', 'exclude_pantry': True, 'items': [
            {'name': n, 'quantity': '100', 'unit': 'g'} for n in ('rice', 'huevos', 'salsa')]}
        data = self.call('estimate_cost', source=source)['data']
        self.assertEqual(data['pantry'], {'applied': True, 'matched': 2})
        self.assertEqual(data['coverage'], {'total': 3, 'costed': 0, 'excluded': 3})
        self.assertEqual([r['name'] for r in data['excluded_rows'] if 'in_pantry' in r['uncertainty']['reasons']], ['rice', 'huevos'])
        self.assertEqual(self.pricing.calls, 1)
        self.assertIsNone(data['consumption_subtotal'])
        self.assertIsNone(data['procurement_subtotal'])

    def test_all_source_kinds_and_invalid_type(self):
        self.store.create_recipe(Recipe(slug='rice', recipeIngredient=(
            Ingredient(quantity=300, unit=NamedRef(name='g'), food=NamedRef(name='rice')),)))
        listing = self.store.create_shopping_list('test')
        self.call('add_to_shopping_list', list_id=listing.id, items=['300 g rice'])
        self.store.set_mealplan_entry(MealEntryDraft('2026-01-01', 'dinner', 'rice', 'rice'))
        self.pantry(['rice'])
        for source in ({'kind': 'recipe', 'slug': 'rice'}, {'kind': 'shopping_list', 'list_id': listing.id},
                       {'kind': 'mealplan', 'start_date': '2026-01-01', 'end_date': '2026-01-01'}):
            with self.subTest(source=source):
                result = self.call('estimate_cost', source={**source, 'exclude_pantry': True})
                self.assertEqual(result['data']['pantry']['matched'], 1)
        for value in (1, 'true', None, []):
            self.assertEqual(self.call('estimate_cost', source={'kind': 'items', 'items': [],
                'exclude_pantry': value})['error']['code'], 'invalid_argument')

    def test_corruption_is_sanitized_and_opt_in(self):
        (self.root / 'preferences.json').write_text('private invalid JSON')
        self.assertTrue(self.call('estimate_cost', source={'kind': 'items', 'items': []})['ok'])
        result = self.call('estimate_cost', source={'kind': 'items', 'items': [], 'exclude_pantry': True})
        self.assertEqual(result['error']['message'], 'Unable to read preferences safely.')
        self.assertNotIn('private', str(result))

    def test_shopping_sources_sqlite_and_fake(self):
        for store in (SqliteRecipeStore(str(self.root / 'store.db')), FakeRecipeStore()):
            self.bind(store)
            store.create_recipe(Recipe(slug='eggs', recipeIngredient=(
                Ingredient(quantity=2, food=NamedRef(name='huevos')),)))
            store.set_mealplan_entry(MealEntryDraft('2026-01-01', 'dinner', 'eggs', 'eggs'))
            listing = store.create_shopping_list('test')
            for source in ({'items': ['2 huevos', 'salsa']}, {'recipe_slugs': ['eggs']},
                           {'mealplan_start': '2026-01-01', 'mealplan_end': '2026-01-01'}):
                self.pantry([])
                self.assertNotIn('pantry_matches', self.call('add_to_shopping_list', list_id=listing.id, **source)['data'])
                self.pantry(['huevo', 'sal'])
                data = self.call('add_to_shopping_list', list_id=listing.id, **source)['data']
                self.assertEqual(len(data['pantry_matches']), 1)
                self.assertEqual(data['pantry_matches'][0]['pantry_entry'], 'huevo')
                self.assertGreater(len(store.get_shopping_items(listing.id).items), 0)

    def test_matching_normalization_and_partial_cost(self):
        self.pantry(['  LIMONES  ', 'green   beans', 'sal'])
        source = {'kind': 'items', 'exclude_pantry': True, 'items': [
            {'name': n, 'quantity': '100', 'unit': 'g'}
            for n in ('limón', 'GREEN beans', 'rice')]}
        data = self.call('estimate_cost', source=source)['data']
        self.assertEqual(data['coverage'], {'total': 3, 'costed': 1, 'excluded': 2})
        self.assertEqual(data['consumption_subtotal'], '0.20')
        self.assertEqual(data['procurement_subtotal'], '2.00')
        listing = self.store.create_shopping_list('test')
        self.assertEqual(self.call('add_to_shopping_list', list_id=listing.id,
                                  items=['salsa'])['data']['pantry_matches'], [])
        # Pantry hints are advisory: unreadable preferences never block the write.
        (self.root / 'preferences.json').write_text('private broken')
        before = len(self.store.get_shopping_items(listing.id).items)
        result = self.call('add_to_shopping_list', list_id=listing.id, items=['salt'])
        self.assertTrue(result['ok'], result)
        self.assertNotIn('pantry_matches', result['data'])
        self.assertEqual(result['data']['pantry_warning'], 'preferences_unreadable')
        self.assertNotIn('private', str(result))
        self.assertEqual(len(self.store.get_shopping_items(listing.id).items), before + 1)

    def test_mealie_shopping_sources(self):
        listing = '00000000-0000-4000-8000-000000000001'
        rid = '00000000-0000-4000-8000-000000000002'
        session = Mock()
        def response(data):
            return Mock(status_code=200, json=Mock(return_value=data))
        session.get.side_effect = lambda url, **kw: response(
            {'items': [{'id': 1, 'recipe': {'id': rid, 'slug': 'eggs'}}], 'page': 1, 'total_pages': 1, 'total': 1, 'per_page': 100}
            if 'mealplans' in url else {'id': rid, 'slug': 'eggs', 'recipeIngredient': [{'quantity': 2, 'food': {'name': 'huevos'}}]})
        session.post.return_value = response({'createdItems': [{'id': 'added'}]})
        self.bind(MealieRecipeStore('https://test.invalid', 'test-token', session=session))
        for source in ({'items': ['2 huevos']}, {'recipe_slugs': ['eggs']},
                       {'mealplan_start': '2026-01-01', 'mealplan_end': '2026-01-01'}):
            self.pantry([])
            self.assertNotIn('pantry_matches', self.call('add_to_shopping_list', list_id=listing, **source)['data'])
            self.pantry(['huevo'])
            result = self.call('add_to_shopping_list', list_id=listing, **source)
            self.assertTrue(result['ok'], result)
            self.assertEqual(result['data']['pantry_matches'][0]['pantry_entry'], 'huevo')
