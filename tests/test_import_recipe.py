import json
import unittest
from unittest.mock import Mock, patch
from sabor.core.config import load_settings
from sabor.importing import tools as t


class ImportTests(unittest.TestCase):
    def context_patch(self):
        from sabor.core.context import build_context
        from sabor.store.mealie import MealieRecipeStore
        from sabor.importing.video import GeminiNativeVideo
        def context():
            cfg = t.settings()
            return build_context(cfg, store_factory=lambda s: MealieRecipeStore(
                s.mealie_base_url, s.mealie_api_token, session=t.requests),
                import_factories=(t.OpenAIImportProvider, GeminiNativeVideo))
        p = patch('sabor.core.context.default_context', side_effect=context)
        p.start()
        self.addCleanup(p.stop)

    def setUp(self):
        self.context_patch()
        self.settings = load_settings({'MEALIE_BASE_URL': 'http://mealie', 'MEALIE_API_TOKEN': 'test',
            'SABOR_LLM_BASE_URL': 'http://localhost:11434/v1', 'SABOR_LLM_MODEL': 'test'})
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

    def test_text_requires_llm(self):
        with patch.object(t, 'settings', return_value=load_settings({})):
            self.assertEqual(t.import_recipe('soup')['error']['code'], 'provider_unconfigured')

    @patch.object(t.requests, 'post')
    def test_text_validation_filter_and_headers(self, post):
        candidate = dict(name='Soup', ingredients=['salt'], steps=['Mix'], servings='2', notes='', confidence=.8)
        post.return_value = self.response({'choices': [{'message': {'content': json.dumps({'recipes': [candidate, dict(candidate, confidence=.1)]})}}]})
        result = t.import_recipe('some soup')
        self.assertEqual(len(result['data']['candidates']), 1)
        self.assertEqual(result['data']['dropped'], 1)
        self.assertNotIn('Authorization', post.call_args.kwargs['headers'])
        post.return_value = self.response({'choices': [{'message': {'content': 'SECRET raw output'}}]})
        result = t.import_recipe('soup')
        self.assertEqual(result['error']['code'], 'invalid_response')
        self.assertNotIn('SECRET', str(result))

    @patch.object(t.requests, 'get')
    @patch.object(t.requests, 'post')
    def test_jsonld(self, post, get):
        candidate = dict(name='Soup', ingredients=['salt'], steps=['Mix'], servings='2', notes='', confidence=.8)
        post.side_effect = [self.response({'choices': [{'message': {'content': json.dumps({'recipes': [candidate]})}}]}), self.response('soup')]
        get.return_value = self.response({'items': [], 'total_pages': 1, 'total': 0, 'page': 1, 'per_page': 100})
        self.assertTrue(t.import_recipe('soup', dry_run=False)['ok'])
        payload = json.loads(post.call_args.kwargs['json']['data'])
        self.assertEqual(payload['recipeIngredient'], ['salt'])
        self.assertEqual(payload['@type'], 'Recipe')

    def test_video_and_image_preview(self):
        for source, kind in [('https://youtu.be/a', 'auto'), ('x', 'video'), ('https://x.org/a.png', 'image')]:
            self.assertEqual(t.import_recipe(source, kind)['error']['code'], 'unsupported_operation')

    @patch.object(t.socket, 'getaddrinfo', return_value=[(2, 1, 6, '', ('127.0.0.1', 80))])
    def test_ssrf(self, dns):
        self.assertEqual(t.import_recipe('http://internal/a.png', 'image', False)['error']['code'], 'invalid_argument')

    @patch.object(t.socket, 'getaddrinfo', return_value=[(2, 1, 6, '', ('93.184.216.34', 443))])
    @patch.object(t, 'image_response', create=True)
    def test_image_size(self, get, dns):
        get.return_value = Mock(status=200, headers={'Content-Length': str(11 * 1024 * 1024)})
        self.assertEqual(t.import_recipe('https://example.org/a.png', 'image', False)['error']['code'], 'invalid_argument')

    @patch.object(t.requests, 'post')
    def test_nested_url_steps(self, post):
        post.return_value = self.response({'name': 'Soup', 'recipeInstructions': [
            'Start', {'@type': 'HowToStep', 'text': 'Mix'}, {'@type': 'HowToSection',
            'itemListElement': [{'@type': 'HowToSection', 'itemListElement': ['Cook']}]}]})
        self.assertEqual(t.import_recipe('https://example.org')['data']['candidates'][0]['steps'], ['Start', 'Mix', 'Cook'])

    @patch.object(t.requests, 'post')
    def test_format_retry_and_fenced_json(self, post):
        post.side_effect = [Mock(status_code=400), self.response({'choices': [{'message': {'content': '```json\n{"recipes": []}\n```'}}]})]
        self.assertTrue(t.import_recipe('soup')['ok'])
        self.assertEqual(post.call_count, 2)
        self.assertNotIn('response_format', post.call_args.kwargs['json'])

    @patch.object(t.requests, 'post')
    def test_retry_is_bounded(self, post):
        post.return_value = Mock(status_code=400)
        self.assertFalse(t.import_recipe('soup')['ok'])
        self.assertEqual(post.call_count, 2)

    @patch.object(t.socket, 'getaddrinfo', return_value=[(2, 1, 6, '', ('93.184.216.34', 443))])
    @patch.object(t, 'image_response', create=True)
    @patch.object(t.requests, 'post')
    def test_image_multipart(self, post, fetch, dns):
        fetch.return_value = Mock(status=200, headers={'Content-Type': 'image/png'})
        fetch.return_value.stream.return_value = [b'png']
        # OpenAPI 201 schema is {}; string/list shapes need live verification.
        post.return_value = self.response('soup')
        post.return_value.status_code = 201
        result = t.import_recipe('https://example.org/a.png', 'image', False, language='es')
        self.assertEqual(result['data']['created'], ['soup'])
        self.assertEqual(post.call_args.kwargs['files'][0][0], 'images')
        self.assertEqual(post.call_args.kwargs['params'], {'translateLanguage': 'es'})

    @patch.object(t.socket, 'getaddrinfo', side_effect=[[(2, 1, 6, '', ('93.184.216.34', 443))], [(2, 1, 6, '', ('127.0.0.1', 443))]])
    @patch.object(t, 'image_response', create=True)
    def test_redirect_revalidation(self, fetch, dns):
        fetch.return_value = Mock(status=302, headers={'Location': 'https://internal/image'})
        result = t.import_recipe('https://example.org/a.png', 'image', False)
        self.assertEqual(result['error']['code'], 'invalid_argument')
        self.assertEqual(dns.call_count, 2)
        self.assertEqual(fetch.call_count, 1)

    @patch.object(t.socket, 'getaddrinfo', return_value=[(2, 1, 6, '', ('93.184.216.34', 443))])
    @patch.object(t, 'image_response', create=True)
    def test_stream_overflow(self, fetch, dns):
        fetch.return_value = Mock(status=200, headers={'Content-Type': 'image/png'})
        fetch.return_value.stream.return_value = [b'x' * (10 * 1024 * 1024), b'x']
        self.assertEqual(t.import_recipe('https://example.org/a.png', 'image', False)['error']['code'], 'invalid_argument')
        fetch.return_value.close.assert_called_once()

    @patch.object(t.requests, 'post')
    def test_invalid_candidate_type(self, post):
        c = dict(name='Soup', ingredients=[42], steps=['Mix'], servings='2', notes='', confidence=.8)
        post.return_value = self.response({'choices': [{'message': {'content': json.dumps({'recipes': [c]})}}]})
        self.assertEqual(t.import_recipe('soup')['error']['code'], 'invalid_response')

    @patch.object(t.requests, 'post')
    def test_source_budget(self, post):
        self.assertEqual(t.import_recipe('x' * 20001)['error']['code'], 'invalid_argument')
        post.assert_not_called()

    @patch.object(t.requests, 'get')
    @patch.object(t.requests, 'post')
    def test_partial_commit(self, post, get):
        c = dict(name='Soup', ingredients=['salt'], steps=['Mix'], servings='2', notes='', confidence=.8)
        post.side_effect = [self.response({'choices': [{'message': {'content': json.dumps({'recipes': [c, dict(c, name='Stew')]})}}]}), self.response('soup'), Mock(status_code=500)]
        get.return_value = self.response({'items': [], 'total_pages': 1, 'total': 0, 'page': 1, 'per_page': 100})
        result = t.import_recipe('two recipes', dry_run=False)
        self.assertEqual(result['error']['code'], 'partial_failure')
        self.assertEqual(result['data']['completed'], ['soup'])

    def test_pinned_transport(self):
        import urllib3
        with patch.object(urllib3, 'HTTPSConnectionPool') as pool:
            t.image_response(t.http_url('https://example.org/image?q=1'), '93.184.216.34')
        self.assertEqual(pool.call_args.args[0], '93.184.216.34')
        self.assertEqual(pool.call_args.kwargs['server_hostname'], 'example.org')
        self.assertEqual(pool.call_args.kwargs['assert_hostname'], 'example.org')
        self.assertFalse(pool.return_value.urlopen.call_args.kwargs['redirect'])
        self.assertEqual(pool.return_value.urlopen.call_args.kwargs['headers']['Host'], 'example.org')

    def test_llm_only_registration(self):
        from sabor.server import register_tools, _registered
        class Server:
            def __init__(self):
                self.tools = {}
            def add_tool(self, fn):
                self.tools[fn.__name__] = fn
        server = Server()
        register_tools(server, {'SABOR_LLM_BASE_URL': 'http://localhost:11434/v1', 'SABOR_LLM_MODEL': 'test'})
        self.assertIn('import_recipe', server.tools)
        self.assertNotIn('search_recipes', server.tools)
        self.assertEqual(_registered[server].imports[0].id, 'openai_compatible')
        adapters = server.tools['sabor_capabilities']()['data']['capabilities']['import']['adapters']
        self.assertIn('openai_compatible', [a['id'] for a in adapters])


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
    def test_text_commit_normalizes(self, post, get):
        candidate = dict(name='Soup', ingredients=['salt'], steps=['Mix'], servings='2', notes='', confidence=.8)
        post.side_effect = [self.response({'choices': [{'message': {'content': json.dumps({'recipes': [candidate]})}}]}),
                            self.response('soup')]
        get.return_value = self.response(self.EMPTY)
        self.assert_hooks(t.import_recipe('soup', dry_run=False), 'soup')

    @patch.object(t.requests, 'get')
    @patch.object(t.requests, 'post')
    def test_url_commit_normalizes(self, post, get):
        post.side_effect = [self.response({'name': 'Soup'}), self.response('soup')]
        get.return_value = self.response(self.EMPTY)
        self.assert_hooks(t.import_recipe('https://example.org', dry_run=False), 'soup')

    @patch.object(t.socket, 'getaddrinfo', return_value=[(2, 1, 6, '', ('93.184.216.34', 443))])
    @patch.object(t, 'image_response', create=True)
    @patch.object(t.requests, 'post')
    def test_image_commit_normalizes(self, post, fetch, dns):
        fetch.return_value = Mock(status=200, headers={'Content-Type': 'image/png'})
        fetch.return_value.stream.return_value = [b'png']
        post.return_value = self.response('soup')
        post.return_value.status_code = 201
        self.assert_hooks(t.import_recipe('https://example.org/a.png', 'image', False), 'soup')

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
