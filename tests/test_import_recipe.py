import json
import unittest
from unittest.mock import Mock, patch
from sabor.core.config import load_settings
from sabor.importing import tools as t


class ImportTests(unittest.TestCase):
    def context_patch(self):
        from sabor.core.context import build_context
        from sabor.store.mealie import MealieRecipeStore
        def context():
            cfg = t.settings()
            return build_context(cfg, store_factory=lambda s: MealieRecipeStore(
                s.mealie_base_url, s.mealie_api_token, session=t.requests))
        p = patch('sabor.core.context.default_context', side_effect=context)
        p.start()
        self.addCleanup(p.stop)

    def setUp(self):
        self.context_patch()
        self.settings = load_settings({'MEALIE_BASE_URL': 'http://mealie', 'MEALIE_API_TOKEN': 'test'})
        self.p = patch.object(t, 'settings', return_value=self.settings)
        self.p.start()
        self.addCleanup(self.p.stop)
        network = patch.object(t.requests, 'get', side_effect=AssertionError('Unexpected network'))
        network.start()
        self.addCleanup(network.stop)

    def response(self, data):
        return Mock(status_code=200, text=json.dumps(data), json=Mock(return_value=data))

    def test_detection(self):
        for source, kind in [('https://example.org/a', 'url'), ('soup', 'text'),
                             ('https://youtu.be/a', 'video'), ('https://x.org/a.mp4', 'video')]:
            self.assertEqual(t.detect(source), kind)

    def test_unsafe_scheme(self):
        self.assertEqual(t.import_recipe('file:///secret')['error']['code'], 'invalid_argument')

    @patch.object(t.requests, 'post')
    def test_preview_and_failure(self, post):
        post.return_value = self.response(json.dumps({'name': 'Soup', 'recipeIngredient': ['salt']}))
        self.assertTrue(t.import_recipe('https://example.org')['data']['preview'])
        self.assertTrue(all('create/' not in c.args[0] for c in post.call_args_list))
        post.return_value = self.response('recipe_scrapers was unable to scrape this URL')
        self.assertEqual(t.import_recipe('https://example.org')['error']['code'], 'not_found')

    @patch.object(t.requests, 'get')
    @patch.object(t.requests, 'post')
    def test_duplicate_and_commit(self, post, get):
        post.return_value = self.response({'name': ' Soup '})
        get.return_value = self.response({'items': [{'name': 'soup', 'slug': 'soup'}], 'total_pages': 1, 'total': 0, 'page': 1, 'per_page': 100})
        self.assertEqual(t.import_recipe('https://example.org', dry_run=False)['error']['code'], 'conflict')
        self.assertEqual(post.call_count, 1)
        get.return_value = self.response({'items': [], 'total_pages': 1, 'total': 0, 'page': 1, 'per_page': 100})
        post.side_effect = [self.response({'name': 'Soup'}), self.response('soup')]
        self.assertEqual(t.import_recipe('https://example.org', dry_run=False)['data']['created'], ['soup'])
        self.assertEqual(post.call_args.kwargs['json'], {'url': 'https://example.org', 'includeTags': True, 'includeCategories': True})

    def test_text_points_to_create_recipe(self):
        # #102: Sabor has no AI provider; the agent extracts text and calls create_recipe.
        with patch.object(t.requests, 'post', side_effect=AssertionError('Unexpected network')):
            result = t.import_recipe('soup')
        self.assertEqual(result['error']['code'], 'unsupported_operation')
        self.assertIn('create_recipe', result['error']['message'])

    @patch('yt_dlp.YoutubeDL')
    def test_video_and_image_point_to_create_recipe(self, youtube_dl):
        # #102: nothing is downloaded or sent anywhere; the agent extracts them itself.
        for source, kind in [('https://youtu.be/a', 'auto'), ('x', 'video'), ('https://x.org/a.png', 'image')]:
            for dry_run in (True, False):
                result = t.import_recipe(source, kind, dry_run)
                self.assertEqual(result['error']['code'], 'unsupported_operation')
                self.assertIn('create_recipe', result['error']['message'])
        youtube_dl.assert_not_called()

    def fetch_image(self, url):
        # The SSRF guard behind recipe pages and recipe images (#94), at the 10 MB image limit.
        with self.assertRaises(t.ImportErrorResult) as ctx:
            t.safe_bytes(url, 10, ('image/jpeg', 'image/png', 'image/webp', 'image/gif'), 'Image')
        return ctx.exception.result['error']['code']

    @patch.object(t.socket, 'getaddrinfo', return_value=[(2, 1, 6, '', ('127.0.0.1', 80))])
    def test_ssrf(self, dns):
        self.assertEqual(self.fetch_image('http://internal/a.png'), 'invalid_argument')

    @patch.object(t.socket, 'getaddrinfo', return_value=[(2, 1, 6, '', ('93.184.216.34', 443))])
    @patch.object(t, 'image_response', create=True)
    def test_image_size(self, get, dns):
        get.return_value = Mock(status=200, headers={'Content-Length': str(11 * 1024 * 1024)})
        self.assertEqual(self.fetch_image('https://example.org/a.png'), 'invalid_argument')

    @patch.object(t.requests, 'post')
    def test_nested_url_steps(self, post):
        post.return_value = self.response({'name': 'Soup', 'recipeInstructions': [
            'Start', {'@type': 'HowToStep', 'text': 'Mix'}, {'@type': 'HowToSection',
            'itemListElement': [{'@type': 'HowToSection', 'itemListElement': ['Cook']}]}]})
        self.assertEqual(t.import_recipe('https://example.org')['data']['candidates'][0]['steps'], ['Start', 'Mix', 'Cook'])

    @patch.object(t.socket, 'getaddrinfo', side_effect=[[(2, 1, 6, '', ('93.184.216.34', 443))], [(2, 1, 6, '', ('127.0.0.1', 443))]])
    @patch.object(t, 'image_response', create=True)
    def test_redirect_revalidation(self, fetch, dns):
        fetch.return_value = Mock(status=302, headers={'Location': 'https://internal/image'})
        self.assertEqual(self.fetch_image('https://example.org/a.png'), 'invalid_argument')
        self.assertEqual(dns.call_count, 2)
        self.assertEqual(fetch.call_count, 1)

    @patch.object(t.socket, 'getaddrinfo', return_value=[(2, 1, 6, '', ('93.184.216.34', 443))])
    @patch.object(t, 'image_response', create=True)
    def test_stream_overflow(self, fetch, dns):
        fetch.return_value = Mock(status=200, headers={'Content-Type': 'image/png'})
        fetch.return_value.stream.return_value = [b'x' * (10 * 1024 * 1024), b'x']
        self.assertEqual(self.fetch_image('https://example.org/a.png'), 'invalid_argument')
        fetch.return_value.close.assert_called_once()

    @patch.object(t.requests, 'post')
    def test_source_budget(self, post):
        self.assertEqual(t.import_recipe('x' * 20001)['error']['code'], 'invalid_argument')
        post.assert_not_called()

    def test_pinned_transport(self):
        import urllib3
        with patch.object(urllib3, 'HTTPSConnectionPool') as pool:
            t.image_response(t.http_url('https://example.org/image?q=1'), '93.184.216.34')
        self.assertEqual(pool.call_args.args[0], '93.184.216.34')
        self.assertEqual(pool.call_args.kwargs['server_hostname'], 'example.org')
        self.assertEqual(pool.call_args.kwargs['assert_hostname'], 'example.org')
        self.assertFalse(pool.return_value.urlopen.call_args.kwargs['redirect'])
        self.assertEqual(pool.return_value.urlopen.call_args.kwargs['headers']['Host'], 'example.org')

    def test_llm_settings_do_not_register_import(self):
        # #102: LLM settings no longer make import available; it needs a recipe store.
        from sabor.server import register_tools, _registered
        class Server:
            def __init__(self):
                self.tools = {}
            def add_tool(self, fn):
                self.tools[fn.__name__] = fn
        server = Server()
        register_tools(server, {'SABOR_STORE': 'mealie', 'SABOR_LLM_BASE_URL': 'http://localhost:11434/v1', 'SABOR_LLM_MODEL': 'test'})
        self.assertNotIn('import_recipe', server.tools)
        self.assertFalse(hasattr(_registered[server], 'imports'))


class ImportPostCommitTests(unittest.TestCase):
    """Committed imports run the same normalization and nutrition hooks as create_recipe."""
    EMPTY = {'items': [], 'total_pages': 1, 'total': 0, 'page': 1, 'per_page': 100}
    context_patch = ImportTests.context_patch
    response = ImportTests.response

    def setUp(self):
        ImportTests.setUp(self)
        from sabor.store.mealie import MealieRecipeStore
        normalize = patch.object(MealieRecipeStore, 'normalize_imported', autospec=True,
                                 return_value={'ok': True, 'status': 'normalized'})
        self.normalize = normalize.start()
        self.addCleanup(normalize.stop)
        nutrition = patch('sabor.tools.nutrition._nutrition_after_import',
                          side_effect=lambda result, slug: dict(result, nutrition={'ok': True}))
        self.nutrition = nutrition.start()
        self.addCleanup(nutrition.stop)

    def assert_hooks(self, result, slug):
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['data']['normalization'], {slug: {'ok': True, 'status': 'normalized'}})
        self.assertEqual(result['data']['nutrition'], {slug: {'ok': True}})
        self.assertEqual(result['data']['warnings'], [])
        self.assertEqual(self.normalize.call_args.args[1], slug)
        self.assertEqual(self.nutrition.call_args.args[1], slug)

    @patch.object(t.requests, 'get')
    @patch.object(t.requests, 'post')
    def test_url_commit_normalizes(self, post, get):
        post.side_effect = [self.response({'name': 'Soup'}), self.response('soup')]
        get.return_value = self.response(self.EMPTY)
        self.assert_hooks(t.import_recipe('https://example.org', dry_run=False), 'soup')

    def test_partial_failure_runs_hooks_on_created(self):
        from sabor.core.context import default_context
        result = t.partial(default_context(), ['soup'], 'mealie')
        self.assertFalse(result['ok'])
        self.assertEqual(result['error']['code'], 'partial_failure')
        self.assertEqual(result['data'], {
            'completed': ['soup'], 'failed': ['remaining'],
            'normalization': {'soup': {'ok': True, 'status': 'normalized'}},
            'nutrition': {'soup': {'ok': True}}, 'warnings': []})
        self.assertEqual([call.args[1] for call in self.normalize.call_args_list], ['soup'])
        self.assertEqual([call.args[1] for call in self.nutrition.call_args_list], ['soup'])

    def test_partial_failure_hook_crash_keeps_partial(self):
        from sabor.core.context import default_context
        self.nutrition.side_effect = RuntimeError('boom')
        result = t.partial(default_context(), ['soup'], 'mealie')
        self.assertEqual(result['error']['code'], 'partial_failure')
        self.assertEqual(result['data'], {
            'completed': ['soup'], 'failed': ['remaining'], 'normalization': {}, 'nutrition': {},
            'warnings': [{'slug': None, 'code': 'post_commit_hooks_failed', 'warning': 'post_commit_hooks_failed'}]})

    HOOKS_FAILED = {'normalization': {}, 'nutrition': {}, 'warnings': [
        {'slug': None, 'code': 'post_commit_hooks_failed', 'warning': 'post_commit_hooks_failed'}]}

    @patch.object(t.requests, 'get')
    @patch.object(t.requests, 'post')
    def test_complete_import_hook_crash_runs_hooks_once_and_stays_ok(self, post, get):
        post.side_effect = [self.response({'name': 'Soup'}), self.response('soup')]
        get.return_value = self.response(self.EMPTY)
        self.nutrition.side_effect = RuntimeError('boom')
        result = t.import_recipe('https://example.org', dry_run=False)
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['data']['created'], ['soup'])
        self.assertEqual({k: result['data'][k] for k in self.HOOKS_FAILED}, self.HOOKS_FAILED)
        self.assertEqual(self.normalize.call_count, 1)
        self.assertEqual(self.nutrition.call_count, 1)
        self.assertEqual(post.call_count, 2)

    @patch.object(t.requests, 'get')
    @patch.object(t.requests, 'post')
    def test_normalization_warning_keeps_import(self, post, get):
        self.normalize.return_value = {'ok': False, 'warning': 'normalization_parser_http_500'}
        self.nutrition.side_effect = lambda result, slug: dict(
            result, nutrition={'ok': False}, nutrition_warning={'code': 'nutrition_calculation_failed'})
        post.side_effect = [self.response({'name': 'Soup'}), self.response('soup')]
        get.return_value = self.response(self.EMPTY)
        result = t.import_recipe('https://example.org', dry_run=False)
        self.assertEqual(result['data']['created'], ['soup'])
        self.assertEqual(result['data']['warnings'], [
            {'slug': 'soup', 'code': 'normalization_warning', 'warning': 'normalization_parser_http_500'},
            {'slug': 'soup', 'code': 'nutrition_warning', 'warning': {'code': 'nutrition_calculation_failed'}}])

    @patch.object(t.requests, 'get')
    @patch.object(t.requests, 'post')
    def test_normalization_disabled_and_preview(self, post, get):
        post.return_value = self.response({'name': 'Soup'})
        self.assertNotIn('normalization', t.import_recipe('https://example.org')['data'])
        from dataclasses import replace
        self.p.stop()
        with patch.object(t, 'settings', return_value=replace(self.settings, normalize_imports=False)):
            post.side_effect = [self.response({'name': 'Soup'}), self.response('soup')]
            get.return_value = self.response(self.EMPTY)
            result = t.import_recipe('https://example.org', dry_run=False)
        self.assertEqual(result['data']['normalization'], {})
        self.normalize.assert_not_called()
        self.assertEqual(self.nutrition.call_count, 1)
        self.p.start()
