import unittest
from typing import Any, cast
from types import SimpleNamespace
from unittest.mock import Mock, patch

from sabor.store.mealie import MealieRecipeStore
from sabor.store import StoreError
from sabor.tools import shopping
from sabor.core.toolsets import bind_context


# MealieRecipeStore now includes MealieShoppingListsMixin directly.
Store = MealieRecipeStore


def response(data, status=200):
    return SimpleNamespace(status_code=status, json=lambda: data)


class ShoppingListsTests(unittest.TestCase):
    def setUp(self):
        self.session = Mock()
        self.store = Store('https://one', 'one', self.session)
        self.ctx = SimpleNamespace(store=self.store)

    def call(self, name, *args, **kwargs):
        return cast(Any, bind_context(getattr(shopping, name), self.ctx)(*args, **kwargs))

    def test_pagination(self):
        self.session.get.side_effect = [response(dict(items=[dict(id='a', name='A')], total=2, total_pages=2, page=1)), response(dict(items=[dict(id='b', name='B')], total=2, total_pages=2, page=2))]
        self.assertEqual([x.id for x in self.store.get_shopping_lists()], ['a', 'b'])

    def test_exact_lookup_and_ambiguity(self):
        self.session.get.return_value = response(dict(items=[dict(id='a', name='Week'), dict(id='b', name='week')]))
        self.assertEqual(self.call('get_or_create_shopping_list', 'Week')['id'], 'a')
        self.session.post.assert_not_called()
        self.session.get.return_value = response(dict(items=[dict(id='a', name='Week'), dict(id='b', name='Week')]))
        self.assertFalse(self.call('get_or_create_shopping_list', 'Week')['ok'])
        self.session.post.assert_not_called()

    def test_failed_lookup_never_creates(self):
        self.session.get.return_value = response({}, 500)
        self.assertFalse(self.call('get_or_create_shopping_list', 'Week')['ok'])
        self.session.post.assert_not_called()

    def test_preview_and_confirm(self):
        self.session.get.return_value = response(dict(name='Week', listItems=[dict(id='a/b', note='milk')]))
        self.session.delete.return_value = response({}, 204)
        for tool in ('delete_shopping_list', 'clear_shopping_list'):
            self.assertEqual(self.call(tool, 'list')['count'], 1)
        self.session.delete.assert_not_called()
        self.call('clear_shopping_list', 'list', confirm=True)
        self.assertEqual(self.session.delete.call_args.kwargs['params'], [('ids', 'a/b')])
        self.call('delete_shopping_list', 'list', confirm=True)
        self.assertEqual(self.session.delete.call_args.args[0], 'https://one/api/households/shopping/lists/list')

    def test_parity_and_isolation(self):
        self.session.get.return_value = response(dict(items=[]))
        other_session = Mock()
        other_session.get.return_value = response(dict(items=[dict(id='b', name='Other')]))
        other = SimpleNamespace(store=Store('https://two', 'two', other_session))
        for ctx in (self.ctx, other):
            with patch('sabor.core.context.default_context', return_value=ctx):
                self.assertEqual(shopping.get_shopping_lists(), bind_context(shopping.get_shopping_lists, ctx)())
        self.assertEqual(other_session.get.call_args.kwargs['headers']['Authorization'], 'Bearer two')
        self.assertTrue(other_session.get.call_args.args[0].startswith('https://two/'))

    def test_create_and_errors(self):
        self.session.post.return_value = response(dict(id='new', name='New'))
        self.assertEqual(self.call('create_shopping_list', 'New')['id'], 'new')
        self.session.delete.return_value = response({}, 403)
        with self.assertRaises(StoreError):
            self.store.delete_shopping_list('list')
