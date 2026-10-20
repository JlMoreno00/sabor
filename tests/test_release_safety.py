"""Offline v0.1.1 tool-level regression tests."""
import unittest
from unittest.mock import Mock, patch
from support import install_runtime_dependency_stubs
install_runtime_dependency_stubs()
from sabor.integrations import mercadona
from sabor.tools import shopping, mealplan


def response(data):
    result = Mock(status_code=200)
    result.json.return_value = data
    return result


class MealieSafetyTests(unittest.TestCase):
    def setUp(self):
        patch.object(shopping.mealie, '_check_mealie', return_value=None).start()
        self.http = patch.object(shopping, 'requests').start()
        patch.object(mealplan, 'requests', self.http).start()
        self.http.get.return_value = response({'listItems': [{'id': 'item', 'note': 'Milk'}], 'items': [{'id': 'entry', 'title': 'Soup'}]})
        self.http.delete.return_value = response({})
        self.addCleanup(patch.stopall)

    def check_preview(self, function, *args):
        result = function(*args)
        self.assertTrue(result['preview'])
        self.assertEqual(result['count'], 1)
        self.assertEqual(len(result['items_to_remove']), 1)
        for method in ('post', 'put', 'delete', 'patch'):
            getattr(self.http, method).assert_not_called()

    def test_clear_shopping_preview(self):
        self.check_preview(shopping.clear_shopping_list, 'list')

    def test_delete_shopping_preview(self):
        self.check_preview(shopping.delete_shopping_list, 'list')

    def test_clear_mealplan_preview(self):
        self.check_preview(mealplan.clear_mealplan_week, '2026-10-19', '2026-10-25')

    def test_confirm_preserves_deletions(self):
        for function, args in [(shopping.clear_shopping_list, ('list',)), (shopping.delete_shopping_list, ('list',)), (mealplan.clear_mealplan_week, ('2026-10-19', '2026-10-25'))]:
            with self.subTest(function=function.__name__):
                self.http.delete.reset_mock()
                self.assertTrue(function(*args, confirm=True)['ok'])
                self.http.delete.assert_called_once()


class CostTests(unittest.TestCase):
    def estimate(self, ingredients):
        catalog = [{'name': name, 'unit_price': '2.00'} for name in ['Salsa de tomate', 'Salmón', 'Sal fina', 'Huevos frescos L', 'Harina de trigo', 'Patatas', 'Cebolla']]
        with patch.object(mercadona, '_mercadona_ensure_catalog', return_value=None), patch.object(mercadona, '_mercadona_catalog', catalog), patch.object(mercadona, 'requests') as http:
            results = [tool(ingredients) for tool in (mercadona.mercadona_estimate_recipe_cost, mercadona.mercadona_estimate_shopping_list_cost)]
            http.get.assert_not_called()
            return results

    def test_whole_words_not_prefixes(self):
        for result in self.estimate(['sal']):
            self.assertEqual(result['breakdown'][0]['matched_product'], 'Sal fina')

    def test_leading_quantities_units_and_connectors(self):
        for result in self.estimate(['2 huevos', '200 g harina', '1 kg de patatas', 'una cebolla']):
            self.assertEqual(result['not_found'], [])

    def test_prefix_only_catalog_is_not_found(self):
        with patch.object(mercadona, '_mercadona_ensure_catalog', return_value=None), patch.object(mercadona, '_mercadona_catalog', [{'name': 'Salsa de tomate', 'unit_price': '1'}, {'name': 'Salmón', 'unit_price': '8'}]):
            for tool in (mercadona.mercadona_estimate_recipe_cost, mercadona.mercadona_estimate_shopping_list_cost):
                self.assertEqual(tool(['sal'])['not_found'], ['sal'])

    def test_search_ranking_still_allows_prefixes(self):
        with patch.object(mercadona, '_mercadona_ensure_catalog', return_value=None), patch.object(mercadona, '_mercadona_catalog', [{'name': 'Salsa de tomate', 'unit_price': '1'}]):
            self.assertEqual(mercadona.mercadona_search_products('sal')['total_matches'], 1)

    def test_unmatched_is_not_found(self):
        for result in self.estimate(['azafran']):
            self.assertEqual(result['not_found'], ['azafran'])

    def test_full_package_basis_is_explicit(self):
        for result in self.estimate(['sal']):
            self.assertEqual(result['cost_basis'], 'full_package_price')
            self.assertTrue(result['note'])
            self.assertEqual(result['total_full_package_cost'], result['total_estimated_cost'])
