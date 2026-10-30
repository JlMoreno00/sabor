"""Shopping sync safety rules, with no live services."""
import unittest
from unittest.mock import patch, Mock

from sabor.sync import tools
from sabor.sync.bring import BringSyncProvider
from sabor.store import ShoppingItem, ShoppingSnapshot, NamedRef


class ShoppingSyncTests(unittest.TestCase):
    def setUp(self):
        self.req = patch.object(BringSyncProvider, '_bring_req').start()
        patch.object(BringSyncProvider, '_bring_ensure_auth', lambda self: self.auth.update(uuid='user')).start()
        self.read = patch('sabor.store.mealie.MealieRecipeStore.get_shopping_items').start()
        self.addCleanup(patch.stopall)
        self.items = [{'id': 's1', 'food': {'name': 'Milk'}, 'quantity': 2,
                       'unit': {'name': 'litre'}, 'checked': False}]
        self.read.side_effect = lambda _: ShoppingSnapshot('source', tuple(ShoppingItem(**{
            **row, 'food': NamedRef('', row['food']['name']),
            'unit': NamedRef('', row['unit']['name'])}) for row in self.items))
        self.lists = [{'listUuid': 'b1', 'name': 'Home'}]
        self.purchase = []
        self.req.side_effect = self.http

    def http(self, method, path, **kwargs):
        return Mock(json=lambda: {'lists': self.lists} if path.endswith('/lists')
                    else {'purchase': self.purchase})

    def sync(self, **kwargs):
        return tools.sync_shopping_list('source', kwargs.pop('target', {'provider': 'bring', 'list_id': 'b1'}), **kwargs)

    def test_targets(self):
        self.assertEqual(tools.get_shopping_sync_targets()['data']['targets'],
                         [{'provider': 'bring', 'list_id': 'b1', 'name': 'Home'}])

    def test_unknown_never_falls_back(self):
        self.assertEqual(self.sync(target={'provider': 'bring', 'list_name': 'Other'})['error']['code'], 'not_found')
        self.assertFalse(any('/v2/' in c.args[1] for c in self.req.call_args_list))

    def test_ambiguous(self):
        self.lists.append({'listUuid': 'b2', 'name': 'HOME'})
        self.assertEqual(self.sync(target={'provider': 'bring', 'list_name': 'home'})['error']['code'], 'ambiguous_target')

    def test_checked_skipped(self):
        self.items[0]['checked'] = True
        data = self.sync()['data']
        self.assertEqual(data['skipped'][0]['reason'], 'checked')
        self.assertEqual(data['add'], [])

    def test_append_never_removes(self):
        self.purchase = [{'itemId': 'Bread', 'spec': ''}]
        self.assertEqual(self.sync()['data']['remove'], [])

    def test_free_text_items_drop_mealie_default_quantity(self):
        # Mealie stores a free-text item with quantity 1 and builds its display as
        # "1 <note>"; Bring! must get the note, not "1 600 g calabaza" (Paleta, 29-oct).
        rows = [ShoppingItem(id='t1', note='600 g calabaza', display='1 600 g calabaza', quantity=1),
                ShoppingItem(id='t2', note='4 huevos', display='1 4 huevos', quantity=1.0),
                ShoppingItem(id='t3', note='500 ml caldo de pollo', display='1 500 ml caldo de pollo',
                             quantity=1),
                # A real quantity on a free-text item is kept as Mealie displays it.
                ShoppingItem(id='t4', note='limones', display='3 limones', quantity=3),
                ShoppingItem(id='t5', note='sal', display='sal', quantity=0)]
        self.read.side_effect = lambda _: ShoppingSnapshot('source', tuple(rows))
        added = {row['source_ids'][0]: (row['itemId'], row['spec']) for row in self.sync()['data']['add']}
        self.assertEqual(added, {'t1': ('600 g calabaza', '600 g calabaza'), 't2': ('4 huevos', '4 huevos'),
                                 't3': ('500 ml caldo de pollo', '500 ml caldo de pollo'),
                                 # Bring!'s catalog still names a known item ("Sal").
                                 't4': ('3 limones', '3 limones'), 't5': ('Sal', 'sal')})

    def test_identical_skipped(self):
        self.purchase = [{'itemId': 'Milk', 'spec': '2 litre'}]
        self.assertEqual(self.sync()['data']['skipped'][0]['reason'], 'identical')

    def test_conflict_not_overwritten(self):
        self.purchase = [{'itemId': 'Milk', 'spec': '1 litre'}]
        data = self.sync()['data']
        self.assertEqual(len(data['conflicts']), 1)
        self.assertEqual(data['add'], [])

    def test_replace_plan(self):
        self.purchase = [{'itemId': 'Bread', 'spec': ''}]
        data = self.sync(mode='replace')['data']
        self.assertEqual(data['remove'][0]['operation'], 'TO_RECENTLY')
        self.assertEqual(data['add'][0]['spec'], '2 litre')

    def test_preview_zero_writes_or_parser(self):
        with patch('sabor.sync.bring_client.requests.post', side_effect=AssertionError):
            self.assertTrue(self.sync()['data']['preview'])
        self.assertTrue(all(c.args[0] == 'GET' for c in self.req.call_args_list))

    def test_confirm_rereads_then_applies(self):
        result = self.sync(confirm=True)
        self.assertTrue(result['ok'])
        self.assertEqual(self.read.call_count, 2)
        self.assertEqual(sum(c.args[0] == 'GET' and '/v2/' in c.args[1] for c in self.req.call_args_list), 2)
        self.assertEqual(sum(c.args[0] == 'PUT' for c in self.req.call_args_list), 1)

    def test_partial_failure(self):
        self.purchase = [{'itemId': 'Bread', 'spec': ''}]
        def http(method, path, **kwargs):
            if method == 'PUT' and kwargs['json']['changes'][0]['operation'] == 'TO_PURCHASE':
                raise RuntimeError('private@example.com')
            return self.http(method, path, **kwargs)
        self.req.side_effect = http
        result = self.sync(mode='replace', confirm=True)
        self.assertEqual(result['error']['code'], 'partial_failure')
        self.assertEqual(len(result['data']['completed']), 1)
        self.assertEqual(len(result['data']['failed']), 1)

    def test_email_never_echoed(self):
        self.req.side_effect = RuntimeError('private@example.com')
        self.assertNotIn('private@example.com', str(self.sync()))

    def test_registration_requires_both(self):
        from sabor.core.toolsets import catalog
        for _, fn, ready in catalog:
            if fn.__module__ == tools.__name__ and ready is not None:
                for bring, mealie in [(False, True), (True, False), (False, False), (True, True)]:
                    settings = Mock(bring_email='x' if bring else '', bring_password='x' if bring else '', mealie_configured=mealie)
                    self.assertEqual(ready(settings), bring and mealie)

    def test_collision_preserves_specs(self):
        self.items.append({**self.items[0], 'id': 's2', 'quantity': 3})
        data = self.sync()['data']
        self.assertTrue(data['warnings'])
        self.assertIn('2 litre', data['add'][0]['spec'])
        self.assertIn('3 litre', data['add'][0]['spec'])

    def test_changed_confirmation_requires_preview(self):
        self.read.side_effect = [self.read('source'), ShoppingSnapshot('source', ())]
        self.assertEqual(self.sync(confirm=True)['error']['code'], 'conflict')
        self.assertTrue(all(c.args[0] == 'GET' for c in self.req.call_args_list))

    def test_conflicting_confirm_blocks_all_writes(self):
        self.purchase = [{'itemId': 'Milk', 'spec': '1 litre'}]
        self.items.append({**self.items[0], 'id': 's2', 'food': {'name': 'Bread'}})
        self.assertEqual(self.sync(confirm=True)['error']['code'], 'conflict')
        self.assertTrue(all(c.args[0] == 'GET' for c in self.req.call_args_list))

    def test_target_change_and_replace_digest_are_not_authorized(self):
        digest = self.sync()['data']['preview_digest']
        self.purchase = [{'itemId': 'Bread', 'spec': ''}]
        self.assertEqual(self.sync(confirm=True, preview_digest=digest)['error']['code'], 'conflict')
        self.assertEqual(self.sync(mode='replace', confirm=True, preview_digest=digest)['error']['code'], 'conflict')
        self.assertTrue(all(c.args[0] == 'GET' for c in self.req.call_args_list))
        digest = self.sync(mode='replace')['data']['preview_digest']
        self.assertTrue(self.sync(mode='replace', confirm=True, preview_digest=digest)['ok'])
