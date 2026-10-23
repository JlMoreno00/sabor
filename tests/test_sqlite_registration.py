"""Default SQLite selection through real registration, without Mealie."""
from unittest.mock import Mock, patch

import pytest
from mcp.server.fastmcp import FastMCP

from sabor.core.config import ConfigError, load_settings
from sabor.server import register_tools, start_runtime_services


@pytest.mark.parametrize('env,store,path', [
    ({}, 'sqlite', '/data/sabor.db'),
    ({'MEALIE_BASE_URL': 'http://mealie.test'}, 'mealie', '/data/sabor.db'),
    ({'SABOR_STORE': 'mealie'}, 'mealie', '/data/sabor.db'),
    ({'SABOR_STORE': 'sqlite', 'MEALIE_BASE_URL': 'http://mealie.test'}, 'sqlite', '/data/sabor.db'),
    ({'SABOR_STORE_DB': '/custom/recipes.db'}, 'sqlite', '/custom/recipes.db'),
])
def test_store_settings(env, store, path):
    settings = load_settings(env)
    assert settings.store == store
    assert settings.store_db == path


def test_invalid_store_is_safe():
    with pytest.raises(ConfigError, match='Invalid SABOR_STORE') as error:
        load_settings({'SABOR_STORE': 'private-operator-value'})
    assert 'private-operator-value' not in str(error.value)


def registered(env):
    server = FastMCP('sqlite')
    register_tools(server, env)
    return {tool.name: tool.fn for tool in server._tool_manager.list_tools()}


def test_registration_isolation_and_discovery(tmp_path):
    a, b = [registered({'SABOR_STORE_DB': str(tmp_path / name),
                        'SABOR_TOOLSETS': 'core,legacy_nutrition'})
            for name in ('a.db', 'b.db')]
    a['create_recipe'](name='Soup', ingredients=['200 g harina'], instructions=['Mix'])
    assert a['search_recipes'](query='Soup')['data']['items']
    assert not b['search_recipes'](query='Soup')['data']['items']
    data = a['sabor_capabilities']()['data']
    assert data['capabilities']['recipe_store']['adapters'][0]['id'] == 'sqlite'
    assert data['capabilities']['recipe_store']['adapters'][0]['operations']
    assert {'name': 'legacy_nutrition', 'reason': 'missing_configuration'} in data['disabled_toolsets']
    assert data['mealie']['configured'] is False


@pytest.mark.parametrize('source', ['recipe', 'mealplan'])
def test_without_mealie_bring_preview(tmp_path, source):
    tools = registered({'SABOR_STORE_DB': str(tmp_path / 'sabor.db'),
                        'BRING_EMAIL': 'test@example.com', 'BRING_PASSWORD': 'password'})
    assert tools['create_recipe'](name='Soup', ingredients=['200 g harina'], instructions=['Mix'])['slug']
    slug = tools['search_recipes'](query='harina')['data']['items'][0]['slug']
    assert tools['set_mealplan_entry'](date_str='2026-01-01', entry_type='dinner',
                                      title='Soup', recipe_slug=slug)['ok']
    list_id = tools['create_shopping_list'](name='Week')['id']
    args = {'recipe_slugs': [slug]} if source == 'recipe' else {
        'mealplan_start': '2026-01-01', 'mealplan_end': '2026-01-07'}
    assert tools['add_to_shopping_list'](list_id=list_id, **args)['ok']
    def http(self, method, path, **kwargs):
        return Mock(json=lambda: {'lists': [{'listUuid': 'b1', 'name': 'Home'}]}
                    if path.endswith('/lists') else {'purchase': []})
    with patch('sabor.sync.bring.BringSyncProvider._bring_ensure_auth',
               lambda self: self.auth.update(uuid='user')), patch(
               'sabor.sync.bring.BringSyncProvider._bring_req', autospec=True, side_effect=http) as request:
        result = tools['sync_shopping_list'](mealie_list_id=list_id,
            target={'provider': 'bring', 'list_id': 'b1'}, confirm=False)
    assert result['ok'], result
    assert result['data']['preview'] is True
    assert 'harina' in str(result['data']['add'])
    assert all(call.args[1] == 'GET' for call in request.call_args_list)


def test_sqlite_explicit_with_mealie_and_native_import_limits(tmp_path):
    tools = registered({'SABOR_STORE': 'sqlite', 'SABOR_STORE_DB': str(tmp_path / 'sabor.db'),
                        'MEALIE_BASE_URL': 'http://mealie.test', 'MEALIE_API_TOKEN': 'token',
                        'SABOR_TOOLSETS': 'core,import,legacy_nutrition,legacy_catalog'})
    data = tools['sabor_capabilities']()['data']
    assert data['mealie']['configured'] is True
    assert data['capabilities']['recipe_store']['adapters'][0]['id'] == 'sqlite'
    assert not data['capabilities']['import']['adapters']
    assert 'nutrition_recipe_readiness' not in tools
    assert 'spanish_catalog_status' in tools
    for kind in ('url', 'image'):
        result = tools['import_recipe'](source='https://example.org/recipe', kind=kind)
        assert result['error']['code'] == 'unsupported_operation'


def test_sqlite_duplicate_search(tmp_path):
    from sabor.importing.tools import store_duplicate
    from sabor.store import RecipeDraft, Ingredient
    from sabor.store.sqlite import SqliteRecipeStore
    store = SqliteRecipeStore(tmp_path / 'sabor.db')
    ref = store.create_recipe(RecipeDraft(name='Sopa de harina', recipeIngredient=(
        Ingredient(originalText='200 g harina'),)))
    assert store_duplicate('SOPA DE HARINA', store) == ref.slug
    assert store_duplicate('Harina', store) is None


def test_database_open_failure_is_safe(tmp_path):
    path = tmp_path / 'private-operator-path'
    path.write_text('not a SQLite database')
    server = FastMCP('invalid-database')
    register_tools(server, {'SABOR_STORE_DB': str(path)})
    with pytest.raises(ConfigError, match='Unable to open SQLite recipe store') as error:
        start_runtime_services(server)
    assert str(path) not in str(error.value)


def test_mealie_nutrition_unchanged():
    tools = registered({'MEALIE_BASE_URL': 'http://mealie.test', 'MEALIE_API_TOKEN': 'token',
                        'SABOR_TOOLSETS': 'legacy_nutrition'})
    assert 'nutrition_recipe_readiness' in tools
