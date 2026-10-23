"""SQLite assembly is inert; startup and first use initialize explicitly."""
from concurrent.futures import ThreadPoolExecutor
import importlib
import sqlite3
from unittest.mock import patch

import pytest
from mcp.server.fastmcp import FastMCP

from sabor.core.config import ConfigError
from sabor.server import register_tools, start_runtime_services
from sabor.store import RecipeSearch
from sabor.store.sqlite import SqliteRecipeStore
from sabor.store.sqlite_schema import MIGRATIONS, migrate


def test_import_and_registration_do_not_open_database(tmp_path):
    blocked = tmp_path / 'blocked'
    blocked.write_text('not a directory')
    env = {'SABOR_STORE': 'sqlite', 'SABOR_STORE_DB': str(blocked / 'store.db')}
    with patch.dict('os.environ', env), patch('pathlib.Path.mkdir', side_effect=AssertionError('mkdir')), patch(
            'sqlite3.connect', side_effect=AssertionError('connect')):
        import sabor_mcp
        importlib.reload(sabor_mcp)
        register_tools(FastMCP('inert'), env)
        SqliteRecipeStore(env['SABOR_STORE_DB'])


def test_startup_failure_is_sanitized(tmp_path):
    path = tmp_path / 'private-parent'
    path.write_text('not a directory')
    server = FastMCP('blocked')
    register_tools(server, {'SABOR_STORE_DB': str(path / 'store.db')})
    with pytest.raises(ConfigError) as error:
        start_runtime_services(server)
    assert str(error.value) == 'Unable to open SQLite recipe store'


@pytest.mark.parametrize('failure', ['parent', 'database'])
def test_tool_failure_is_sanitized(tmp_path, failure):
    path = tmp_path / 'private-parent'
    path.write_text('not a directory')
    server = FastMCP('blocked-tool')
    register_tools(server, {'SABOR_STORE_DB': str(path / 'store.db' if failure == 'parent' else path)})
    tool = server._tool_manager.get_tool('search_recipes')
    assert tool is not None
    result = tool.fn(query='Soup')
    assert result['ok'] is False
    assert result['error']['provider'] == 'sqlite'
    assert str(path) not in str(result)
    assert 'not a directory' not in str(result).lower()


def test_lazy_first_use_migrates_once_concurrently(tmp_path):
    path = tmp_path / 'nested' / 'store.db'
    store = SqliteRecipeStore(path)
    assert not path.parent.exists()
    with patch('sabor.store.sqlite.migrate', wraps=migrate) as migration:
        with ThreadPoolExecutor(max_workers=8) as pool:
            assert list(pool.map(lambda _: store.search_recipes(RecipeSearch()).total, range(16))) == [0] * 16
        store.ensure_ready()
        assert migration.call_count == 1
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT version FROM schema_version ORDER BY version').fetchall() == [
            (version,) for version, _ in MIGRATIONS]
