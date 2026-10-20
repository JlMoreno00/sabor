"""Offline item adapter and instance routing evidence."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from sabor.core.toolsets import bind_context
from sabor.store.mealie import MealieRecipeStore
from sabor.store.mealie_items import MealieShoppingItemsMixin  # pyright: ignore[reportMissingImports]
from sabor.store import ItemDraft, RecipeOccurrence, StoreError
from sabor.tools import shopping_items as tools


def item_store_class():
    if issubclass(MealieRecipeStore, MealieShoppingItemsMixin):
        return MealieRecipeStore
    return type('ItemStore', (MealieShoppingItemsMixin, MealieRecipeStore), {})


LIST = '11111111-1111-4111-8111-111111111111'
RID = '22222222-2222-4222-8222-222222222222'


class ItemStoreTests(unittest.TestCase):
    def store(self, host='one'):
        http = Mock()
        http.get.return_value = Mock(status_code=200)
        http.get.return_value.json.return_value = {'listItems': [
            {'id': 'a', 'shoppingListId': LIST, 'quantity': 2, 'checked': False,
             'extras': {'keep': [1]}, 'recipeReferences': [{'id': 'ref'}]}]}
        http.put.return_value.status_code = 200
        http.delete.return_value.status_code = 200
        return item_store_class()('https://' + host, host, session=http), http

    def test_lossless_and_foreign(self):
        store, http = self.store()
        store.check_shopping_items(LIST, ['a'], True)
        payload = http.put.call_args.kwargs['json'][0]
        self.assertEqual(payload['extras'], {'keep': [1]})
        self.assertEqual(payload['recipeReferences'], [{'id': 'ref'}])
        with self.assertRaises(StoreError):
            store.remove_shopping_items(LIST, ['foreign'])
        http.delete.assert_not_called()

    def test_instances_and_preview(self):
        one, first = self.store()
        two, second = self.store('two')
        for store in (one, two):
            ctx = SimpleNamespace(store=store)
            result = tools._run(ctx, tools._remove_shopping_list_items, LIST, ['a'], False)
            self.assertTrue(result['data']['preview'])
        self.assertTrue(first.get.call_args.args[0].startswith('https://one/'))
        self.assertEqual(second.get.call_args.kwargs['headers'], {'Authorization': 'Bearer two'})
        first.delete.assert_not_called()
        second.delete.assert_not_called()

    def test_repeated_occurrences_and_unresolved(self):
        store, http = self.store()
        store.get_recipe = Mock(return_value=SimpleNamespace(id=RID))
        http.post.return_value.status_code = 200
        store.add_recipe_occurrences(LIST, [RecipeOccurrence('rice', 2.5)] * 2)
        self.assertEqual(http.post.call_args.kwargs['json'], [
            {'recipeId': RID, 'recipeIncrementQuantity': 2.5}] * 2)
        http.post.reset_mock()
        store.get_recipe.side_effect = [SimpleNamespace(id=RID), StoreError('not_found', 'mealie')]
        with self.assertRaises(StoreError):
            store.add_recipe_occurrences(LIST, [RecipeOccurrence('rice'), RecipeOccurrence('missing')])
        http.post.assert_not_called()

    def test_direct_bound_parity(self):
        store, http = self.store()
        ctx = SimpleNamespace(store=store)
        http.post.return_value = Mock(status_code=200)
        http.post.return_value.json.return_value = {'createdItems': [{'id': 'new'}]}
        cases = [(tools.get_shopping_list_items, (LIST,), {}),
                 (tools.check_shopping_list_items, (LIST, ['a']), {}),
                 (tools.remove_shopping_list_items, (LIST, ['a']), {}),
                 (tools.remove_shopping_list_items, (LIST, ['a']), {'confirm': True}),
                 (tools.add_to_shopping_list, (LIST,), {'items': ['rice']})]
        for fn, args, kwargs in cases:
            with self.subTest(tool=fn.__name__, kwargs=kwargs):
                bound = bind_context(fn, ctx)(*args, **kwargs)
                with patch('sabor.core.context.default_context', return_value=ctx):
                    self.assertEqual(fn(*args, **kwargs), bound)

    def test_partial_item_result(self):
        store, http = self.store()
        http.post.return_value = Mock(status_code=200)
        http.post.return_value.json.return_value = {'createdItems': [{'id': 'a'}]}
        result = store.add_shopping_items(LIST, [ItemDraft(note='a'), ItemDraft(note='b')])
        self.assertEqual(result.changed_ids, ('a',))
