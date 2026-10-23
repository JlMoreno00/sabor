"""Registered calls must never consult compatibility globals."""
from concurrent.futures import ThreadPoolExecutor
from contextvars import ContextVar
import json
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import Mock

import pytest

from sabor.core.config import TOOLSETS, load_settings
from sabor.server import register_tools, _contexts


class Server:
    def __init__(self):
        self.tools = {}

    def add_tool(self, fn):
        self.tools[fn.__name__] = fn


def environment(tmp_path, name):
    return dict(MEALIE_BASE_URL=f'https://{name}.example', MEALIE_API_TOKEN=name,
                GEMINI_API_KEY=f'gemini-{name}', BRING_EMAIL=f'{name}@example.com',
                BRING_PASSWORD=f'password-{name}', BRING_CATALOG_DIR=str(tmp_path / name),
                SABOR_NUTRITION_ENABLED='true', SABOR_NUTRITION_DB=str(tmp_path / f'{name}.db'),
                MEMORY_FILE=str(tmp_path / f'{name}.md'), SPOONACULAR_API_KEY=f'spoon-{name}',
                SABOR_TOOLSETS=','.join(TOOLSETS), SABOR_NORMALIZE_IMPORTS='false',
                SABOR_MERCADONA_ENABLED='true', SABOR_LLM_BASE_URL='http://localhost:11434/v1',
                SABOR_LLM_MODEL='test')


@pytest.mark.parametrize('env', [{}, {'MEALIE_BASE_URL': 'http://example.com/',
    'SABOR_NUTRITION_ENABLED': 'yes', 'SABOR_NORMALIZE_IMPORTS': 'on',
    'MEMORY_FILE': '/sample/memory', 'VIDEO_RECIPE_GEMINI_MODEL': 'sample-model'}])
def test_legacy_configuration_parity(env):
    # Evaluate the original compatibility module in an isolated process/environment.
    script = ('import json, sabor.config as c; print(json.dumps({k: str(v) for k,v '
              'in vars(c).items() if k.isupper() or k.startswith("_GEMINI")}))')
    expected = json.loads(subprocess.check_output([sys.executable, '-c', script],
                                                env={**env, 'PATH': os.defpath}, text=True))
    cfg = load_settings(env).legacy_config
    assert {k: str(getattr(cfg, k)) for k in expected} == expected
    if not env:
        assert str(cfg.MEMORY_FILE) == "/data/MEMORY.md"
        assert str(cfg.SABOR_NUTRITION_DB) == "/data/nutrition.db"
        assert cfg.MEALIE_BASE_URL == "http://localhost:9925"
    with pytest.raises((AttributeError, TypeError)):
        cfg.MEALIE_BASE_URL = 'changed'


def test_interleaved_and_concurrent_servers(tmp_path, monkeypatch):
    from sabor.core.toolsets import use_context
    from sabor.tools import nutrition
    servers = [Server(), Server()]
    envs = [environment(tmp_path, n) for n in ('a', 'b')]
    for server, env in zip(servers, envs):
        register_tools(server, env)
    contexts = [_contexts[s] for s in servers]
    services = []
    for ctx in contexts:
        with use_context(ctx):
            services.append(nutrition._nutrition_service())
    assert services[0] is not services[1]
    assert all(Path(e['SABOR_NUTRITION_DB']).exists() for e in envs)
    assert contexts[0].state.recipe_locks[0] is not contexts[1].state.recipe_locks[0]
    assert contexts[0].state.mealplan_locks[0] is not contexts[1].state.mealplan_locks[0]
    assert contexts[0].state.correction_lock is not contexts[1].state.correction_lock
    assert contexts[0].state.provider_locks[0] is not contexts[1].state.provider_locks[0]
    assert contexts[0].pricing[0] is not contexts[1].pricing[0]
    for index, ctx in enumerate(contexts):
        with use_context(ctx):
            nutrition._nutrition_stage_correction('same-slug', {'owner': index})
    assert contexts[0].state.correction_stages['same-slug']['candidate'] == {'owner': 0}
    assert contexts[1].state.correction_stages['same-slug']['candidate'] == {'owner': 1}

    expected_name = ContextVar("expected_name")

    def response(method, url, **kwargs):
        owner = expected_name.get()
        if 'spoonacular' in url:
            assert kwargs['params']['apiKey'] == f'spoon-{owner}'
            body = {'results': [], 'totalResults': 0}
        else:
            name = url.split('//')[1].split('.')[0]
            assert name == owner
            assert kwargs['headers']['Authorization'] == f'Bearer {name}'
            body = {'id': 'recipe', 'slug': 'recipe', 'name': 'Recipe', 'tags': [],
                    'items': [], 'total_pages': 1, 'page': 1, 'per_page': 25, 'total': 0}
        return Mock(status_code=200, json=lambda: body)

    monkeypatch.setattr('requests.get', lambda url, **kw: response('GET', url, **kw))
    monkeypatch.setattr('requests.put', lambda url, **kw: response('PUT', url, **kw))
    monkeypatch.setattr('requests.sessions.Session.request',
                        lambda self, method, url, **kw: response(method, url, **kw))

    # Legacy promotion catalog ownership is explicitly outside this task; avoid
    # its network crawl while smoke-testing that selected toolset's registration.
    monkeypatch.setattr('sabor.integrations.mercadona._mercadona_ensure_catalog', lambda: None)
    monkeypatch.setattr('sabor.pricing.mercadona.MercadonaPricing.start_background_refresh', lambda self: None)

    def exercise(index):
        expected_name.set(('a', 'b')[index])
        server = servers[index]
        assert server.tools['get_recipe']('recipe')['ok']
        assert server.tools['tag_recipe']('recipe', [])['ok']
        assert server.tools['spoonacular_search_recipes']('rice')['ok']
        assert server.tools['spanish_catalog_status']()['ok']
        assert server.tools['mercadona_get_new_arrivals']()['ok']
        assert not server.tools['search_pricing_products']('rice')['ok']
        assert server.tools['save_feedback'](f'only-{index}', rating=5)['ok']

    exercise(0)
    exercise(1)
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(exercise, (1, 0)))
    for i, env in enumerate(envs):
        content = Path(env['MEMORY_FILE']).read_text()
        assert f'only-{i}' in content and f'only-{1-i}' not in content


def test_registered_paths_do_not_import_compatibility_configuration(tmp_path):
    script = '''
import sys
from unittest.mock import Mock, patch
from pathlib import Path
from sabor.core.config import TOOLSETS
from sabor.server import register_tools
class Server:
    def __init__(self): self.tools = {}
    def add_tool(self, fn): self.tools[fn.__name__] = fn
for name in ('a', 'b'):
    server = Server()
    root = Path(sys.argv[1])
    register_tools(server, {'MEALIE_BASE_URL': 'https://' + name + '.example',
                           'MEALIE_API_TOKEN': name, 'SABOR_TOOLSETS': ','.join(TOOLSETS),
                           'SABOR_NUTRITION_ENABLED': 'true',
                           'SABOR_NUTRITION_DB': str(root / (name + '.db')),
                           'MEMORY_FILE': str(root / (name + '.md')),
                           'SPOONACULAR_API_KEY': name, 'GEMINI_API_KEY': name,
                           'BRING_EMAIL': name + '@example.com', 'BRING_PASSWORD': name,
                           'SABOR_LLM_BASE_URL': 'http://localhost:11434/v1',
                           'SABOR_LLM_MODEL': 'test', 'SABOR_MERCADONA_ENABLED': 'true'})
    assert server.tools['save_feedback'](name, rating=5)['ok']
    assert server.tools['nutrition_unresolved_report']()['ok']
    with patch('requests.Session.get', return_value=Mock(status_code=200, json=lambda: {'tags': []})), \\
         patch('requests.Session.patch', return_value=Mock(status_code=200)):
        assert server.tools['tag_recipe']('recipe', [])['ok']
assert 'sabor.config' not in sys.modules
'''
    subprocess.run([sys.executable, '-c', script, str(tmp_path)], check=True)


def test_video_bring_and_after_import_bridge(tmp_path, monkeypatch):
    from sabor.core.toolsets import current_context
    from sabor.tools import nutrition

    expected = ContextVar('expected_server')
    servers, contexts, calls, bridge_services = [], [], [], []
    for name in ('a', 'b'):
        env = environment(tmp_path, name)
        env['SABOR_NUTRITION_AUTO_ON_IMPORT'] = 'true'
        directory = Path(env['BRING_CATALOG_DIR'])
        directory.mkdir()
        (directory / 'articles.es-ES.json').write_text(json.dumps({name: 'Unique item'}))
        (directory / 'articles.en-US.json').write_text('{}')
        server = Server()
        register_tools(server, env)
        servers.append(server)
        contexts.append(_contexts[server])

    def response(method, url, **kwargs):
        name = expected.get()
        calls.append((name, method, url))
        headers = {}
        if 'generativelanguage' in url:
            key = kwargs.get('params', {}).get('key')
            assert key == f'gemini-{name}' or f'key=gemini-{name}' in url
            if '/upload/' in url:
                headers = {'X-Goog-Upload-URL': f'https://upload.example/{name}'}
                body = {}
            elif ':generateContent' in url:
                candidate = dict(name='Video recipe', ingredients=['1 g salt'], steps=['Mix'],
                                 servings='2', notes='', confidence=.9)
                body = {'candidates': [{'content': {'parts': [{'text': json.dumps({'recipes': [candidate]})}]}}]}
            else:
                body = {'state': 'ACTIVE'}
        elif 'upload.example' in url:
            assert url.endswith('/' + name)
            body = {'file': {'name': f'files/{name}', 'uri': f'uri-{name}', 'state': 'PROCESSING'}}
        elif 'getbring.com' in url:
            if url.endswith('/bringauth'):
                assert kwargs['data'] == {'email': f'{name}@example.com', 'password': f'password-{name}'}
                body = {'uuid': name, 'access_token': f'bring-{name}', 'expires_in': 3600}
            else:
                assert kwargs['headers']['Authorization'] == f'Bearer bring-{name}'
                body = {'lists': [{'listUuid': name, 'name': name}]}
        elif 'spoonacular' in url:
            assert kwargs['params']['apiKey'] == f'spoon-{name}'
            body = {'results': []}
        else:
            assert url.startswith(f'https://{name}.example/')
            assert kwargs['headers']['Authorization'] == f'Bearer {name}'
            if '/create/html-or-json' in url:
                body = 'video-recipe'
            elif '/organizers/tags' in url:
                body = {'items': [{'id': 'tag', 'slug': 'video-import', 'name': 'video-import'}]}
            else:
                body = {'id': 'recipe', 'slug': 'recipe', 'name': 'Recipe', 'tags': [],
                        'items': [], 'total_pages': 1, 'page': 1, 'per_page': 25, 'total': 0}
        return Mock(status_code=200, json=lambda: body, headers=headers, text='{}')

    for method in ('get', 'post', 'patch', 'delete'):
        monkeypatch.setattr('requests.' + method,
                            lambda url, _method=method, **kw: response(_method.upper(), url, **kw))
    monkeypatch.setattr('requests.sessions.Session.request',
                        lambda self, method, url, **kw: response(method, url, **kw))

    def download(source, root):
        media = root / 'video.mp4'
        media.write_bytes(b'video')
        return media, ''

    monkeypatch.setattr('sabor.importing.video.download', download)

    def ensure(slug, trigger):
        # Keep the real bridge, service construction and recipe fetch; stub only
        # domain calculation, which has its own comprehensive domain tests.
        service = nutrition._nutrition_service()
        assert service is current_context().state.nutrition_service
        bridge_services.append((expected.get(), service))
        nutrition._nutrition_fetch_recipe(slug)
        service.resolver.provider.fetch_profile('salt')
        return {'ok': True}

    monkeypatch.setattr(nutrition, 'ensure_recipe_nutrition', ensure)

    def exercise(index):
        token = expected.set(('a', 'b')[index])
        try:
            server = servers[index]
            targets = server.tools['get_shopping_sync_targets']()['data']['targets']
            assert targets[0]['list_id'] == expected.get()
            adapter = contexts[index].registry.syncs[0]
            assert adapter.catalogs[0]['unique item'] == expected.get()
            result = server.tools['import_recipe']('https://youtu.be/test', kind='video', dry_run=False)
            assert result['ok'], result
            assert result['data']['created'] == ['video-recipe']
            assert result['data']['nutrition']['video-recipe']['ok']
        finally:
            expected.reset(token)

    exercise(0)
    exercise(1)
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(exercise, (1, 0)))
    assert bridge_services
    for name, service in bridge_services:
        assert service is contexts[('a', 'b').index(name)].state.nutrition_service
    for name in ('a', 'b'):
        assert any(owner == name and method == 'PATCH' and '/api/recipes/video-recipe' in url
                   for owner, method, url in calls)


def test_default_context_assembles_providers_and_stable_direct_sync_scope(tmp_path, monkeypatch):
    from sabor import config
    from sabor.core.context import default_context
    from sabor.sync.tools import _direct_context

    env = environment(tmp_path, 'direct')
    monkeypatch.setenv('SABOR_LLM_BASE_URL', env['SABOR_LLM_BASE_URL'])
    monkeypatch.setenv('SABOR_LLM_MODEL', env['SABOR_LLM_MODEL'])
    for key in ('MEALIE_BASE_URL', 'MEALIE_API_TOKEN', 'GEMINI_API_KEY',
                'BRING_EMAIL', 'BRING_PASSWORD', 'SPOONACULAR_API_KEY'):
        monkeypatch.setattr(config, key, env[key])
    direct = default_context()
    server = Server()
    register_tools(server, env)
    registered = _contexts[server]
    assert [p.id for p in direct.registry.imports] == [p.id for p in registered.registry.imports]
    assert [p.id for p in direct.registry.syncs] == ['bring']
    assert _direct_context().registry.syncs[0].scope == _direct_context().registry.syncs[0].scope
