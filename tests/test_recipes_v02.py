"""Recipe contract tests with no upstream I/O."""
import unittest
from unittest.mock import Mock, patch
import requests
from tests.support import install_runtime_dependency_stubs

install_runtime_dependency_stubs()
from sabor.tools import recipes as r


class RecipeContracts(unittest.TestCase):
    def setUp(self):
        self.get = patch.object(r.requests, 'get').start()
        self.patch = patch.object(r.requests, 'patch').start()
        self.put = patch.object(r.requests, 'put').start()
        patch.object(r.config, 'MEALIE_API_TOKEN', 'test').start()
        self.addCleanup(patch.stopall)
        self.get.return_value = Mock(status_code=200)
        self.get.return_value.json.return_value = {}
        self.patch.return_value = Mock(status_code=204)

    def test_mapping_and_pagination(self):
        self.get.return_value.json.return_value = dict(items=[dict(id='r', name='Soup', slug='s')], total=51, page=2, per_page=10, total_pages=6)
        result = r.search_recipes('s', 2, 10, ['t'], ['c'], ['f'], ['u'], 'book', True)
        self.assertEqual(result['data'], dict(items=[dict(id='r', name='Soup', slug='s')], total=51, page=2, per_page=10, total_pages=6))
        self.assertEqual(self.get.call_args.kwargs['params'], dict(search='s', page=2, perPage=10, tags=['t'], categories=['c'], foods=['f'], tools=['u'], cookbook='book', requireAllTags=True, requireAllCategories=True, requireAllFoods=True, requireAllTools=True, orderBy='name', orderDirection='asc'))

    def test_validation(self):
        for args in [dict(page=0), dict(per_page=0), dict(per_page=101), dict(direction='bad'), dict(sort='unknown')]:
            with self.subTest(args=args):
                self.assertEqual(r.search_recipes(**args)['error']['code'], 'invalid_argument')
        self.get.assert_not_called()

    def test_structured_nulls(self):
        ingredient = dict(quantity=None, unit=None, food=dict(id='f', name='Salt'), note=None, display='Salt', originalText=None, referenceId='ref', title='Base')
        self.get.return_value.json.return_value = dict(recipeIngredient=[ingredient], recipeInstructions=[dict(id='i', title=None, text='Cook')])
        data = r.get_recipe('s')['data']
        self.assertEqual(data['recipeIngredient'][0], {**ingredient, 'heading': None})
        self.assertEqual(data['recipeInstructions'], [dict(id='i', title=None, text='Cook')])
        for key in ['tags', 'recipeCategory', 'recipeServings', 'recipeYield', 'totalTime', 'prepTime', 'cookTime']:
            self.assertIsNone(data[key])

    def test_invalid_patch(self):
        for value in [{}, {'slug': 'bad'}, None]:
            self.assertEqual(r.update_recipe('s', value)['error']['code'], 'invalid_argument')
        self.get.assert_not_called()
        self.patch.assert_not_called()

    def test_preview(self):
        self.get.return_value.json.return_value = {'tags': ['old']}
        data = r.update_recipe('s', {'tags': []})['data']
        self.assertTrue(data['preview'])
        self.assertEqual(data['changes']['tags'], dict(before=['old'], after=[], full_replacement=True))
        self.patch.assert_not_called()
        self.put.assert_not_called()

    def test_confirm_patch_only(self):
        result = r.update_recipe('s', {'name': 'New', 'tags': []}, True)
        self.assertEqual(result, {'ok': True, 'data': {'slug': 's', 'changed_fields': ['name', 'tags']}})
        self.assertEqual(self.patch.call_args.kwargs['json'], {'name': 'New', 'tags': []})
        self.get.assert_not_called()
        self.put.assert_not_called()

    def test_suggestions(self):
        self.get.return_value.json.return_value = {'items': [{'recipe': dict(id='r', name='Soup', slug='s'), 'missingFoods': [{'id': 'f', 'name': 'Salt'}], 'missingTools': []}]}
        data = r.suggest_recipes(['food'], ['tool'])['data']
        self.assertEqual(data['items'][0], dict(id='r', name='Soup', slug='s', missingFoods=[{'id': 'f', 'name': 'Salt'}], missingTools=[]))
        self.assertEqual(self.get.call_args.kwargs['params'], dict(foods=['food'], tools=['tool'], maxMissingFoods=2, maxMissingTools=0, limit=10, includeFoodsOnHand=True))

    def test_safe_errors(self):
        calls = [lambda: r.search_recipes(), lambda: r.get_recipe('s'), lambda: r.update_recipe('s', {'name': 'n'}), lambda: r.suggest_recipes([])]
        for fn in calls:
            self.get.return_value.status_code = 500
            self.get.return_value.text = 'SECRET'
            self.assertEqual(fn()['error']['code'], 'upstream_error')
            self.get.side_effect = requests.Timeout('SECRET')
            result = fn()
            self.assertEqual(result['error']['code'], 'timeout')
            self.assertNotIn('SECRET', str(result))
            self.get.side_effect = None
        self.patch.return_value.status_code = 422
        self.assertEqual(r.update_recipe('s', {'name': 'n'}, True)['error']['code'], 'invalid_argument')

    def test_invalid_response(self):
        self.get.return_value.json.return_value = {'items': 'invalid'}
        self.assertEqual(r.search_recipes()['error']['code'], 'invalid_response')
        self.assertEqual(r.suggest_recipes([])['error']['code'], 'invalid_response')
