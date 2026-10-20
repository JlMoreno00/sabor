"""Offline v0.1.1 tool-level regression tests."""
import unittest
from unittest.mock import Mock, patch
from support import install_runtime_dependency_stubs
install_runtime_dependency_stubs()
from sabor.tools import shopping, mealplan


def response(data):
    result = Mock(status_code=200)
    result.json.return_value = data
    return result


class MealieSafetyTests(unittest.TestCase):
    def setUp(self):
        # Since v0.3 the tools route through the instance store; direct calls
        # use the requests module as the store session, so intercept it there.
        import requests
        import sabor.config as config
        patch.object(config, 'MEALIE_BASE_URL', 'http://mealie.test').start()
        patch.object(config, 'MEALIE_API_TOKEN', 'test-token').start()
        self.http = Mock()
        for method in ('get', 'post', 'put', 'patch', 'delete', 'request'):
            patch.object(requests, method, getattr(self.http, method)).start()
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
