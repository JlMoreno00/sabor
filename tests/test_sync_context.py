"""Instance routing and preview approval without live services."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from sabor.core.config import load_settings
from sabor.core.toolsets import bind_context
from sabor.store import ShoppingItem, ShoppingSnapshot
from sabor.sync import tools
from sabor.sync.bring import BringSyncProvider


class SyncContextTests(unittest.TestCase):
    def setUp(self):
        self.adapter = Mock(id='other')
        self.adapter.targets.return_value = [{'provider': 'other', 'list_id': 't', 'name': 'Test'}]
        self.adapter.plan.side_effect = lambda source, target, mode: {
            'source': source, 'target': target, 'mode': mode, 'target_snapshot': [],
            'add': source['items'], 'remove': [], 'conflicts': []}
        self.adapter.apply.side_effect = lambda plan: {'ok': True, 'data': plan}
        self.store = Mock()
        self.store.get_shopping_items.return_value = ShoppingSnapshot('s', (ShoppingItem(id='i', display='milk', quantity=2),))
        self.ctx = SimpleNamespace(store=self.store, registry=SimpleNamespace(syncs=(self.adapter,)))
        self.sync = bind_context(tools.sync_shopping_list, self.ctx)
        self.target = {'provider': 'other', 'list_id': 't'}

    def test_store_preview_and_digest_match(self):
        data = self.sync('s', self.target)['data']
        self.assertEqual(len(data['preview_digest']), 64)
        self.store.get_shopping_items.assert_called_once_with('s')
        self.adapter.apply.assert_not_called()
        result = self.sync('s', self.target, confirm=True, preview_digest=data['preview_digest'])
        self.assertTrue(result['ok'])
        self.assertTrue(result['data']['digest_checked'])
        self.adapter.apply.assert_called_once()

    def test_stale_source_and_target_zero_writes(self):
        digest = self.sync('s', self.target)['data']['preview_digest']
        self.store.get_shopping_items.return_value = ShoppingSnapshot('s', ())
        result = self.sync('s', self.target, confirm=True, preview_digest=digest)
        self.assertEqual(result['error']['code'], 'conflict')
        self.assertIn('preview_digest', result['data']['preview'])
        self.adapter.apply.assert_not_called()

    def test_conflicts_and_legacy_confirm(self):
        self.assertFalse(self.sync('s', self.target, confirm=True)['data']['digest_checked'])
        self.adapter.apply.reset_mock()
        self.adapter.plan.side_effect = lambda *args: {'conflicts': [{'itemId': 'milk'}]}
        self.assertEqual(self.sync('s', self.target, confirm=True)['error']['code'], 'conflict')
        self.adapter.apply.assert_not_called()

    def test_direct_parity(self):
        with patch('sabor.core.context.default_context', return_value=self.ctx):
            self.assertEqual(tools.sync_shopping_list('s', self.target), self.sync('s', self.target))
            self.assertEqual(tools.get_shopping_sync_targets(), bind_context(tools.get_shopping_sync_targets, self.ctx)())

    def test_bring_instances_own_session_auth_and_catalog(self):
        a, b = [BringSyncProvider(load_settings({'BRING_EMAIL': email, 'BRING_PASSWORD': 'p'})) for email in ('a', 'b')]
        self.assertIsNot(a.session, b.session)
        a.auth['access_token'] = 'a'
        self.assertEqual(b.auth, {})
        a.catalogs[0]['private'] = 'a'
        self.assertNotIn('private', b.catalogs[0])
        self.assertEqual((a.email, b.email), ('a', 'b'))
        for adapter in (a, b):
            adapter.session = Mock()
            adapter.session.post.return_value.json.return_value = {
                'uuid': adapter.email, 'access_token': adapter.email, 'expires_in': 3600}
            adapter._bring_req('GET', '/test')
            self.assertEqual(adapter.session.post.call_args.kwargs['data']['email'], adapter.email)
            self.assertEqual(adapter.session.request.call_args.kwargs['headers']['Authorization'],
                             'Bearer ' + adapter.email)
        self.assertEqual(a.auth['access_token'], 'a')
        self.assertEqual(b.auth['access_token'], 'b')
        self.assertNotEqual(a.scope, b.scope)
        plan = {'source': [], 'target_snapshot': [], 'provider_scope': a.scope}
        digest = tools._digest(plan, 'bring', 't', 'append')
        self.assertNotEqual(digest, tools._digest({**plan, 'provider_scope': b.scope}, 'bring', 't', 'append'))
