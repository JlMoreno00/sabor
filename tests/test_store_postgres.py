"""Postgres configuration and failure safety without a database."""
import importlib
import importlib.util
import json
import sys
import unittest
from unittest.mock import MagicMock, patch

from sabor.core.config import ConfigError, load_settings
from sabor.store import StoreError


class PostgresUnitTests(unittest.TestCase):
    def test_config(self):
        for dsn in ('postgresql://user:PRIVATE_SENTINEL@localhost/db', 'postgres:///db'):
            settings = load_settings({'SABOR_STORE': 'postgres', 'SABOR_DATABASE_URL': dsn})
            self.assertEqual(getattr(settings, 'database_url'), dsn)
            self.assertNotIn('PRIVATE_SENTINEL', repr(settings))
        for value in ('', 'http://PRIVATE_SENTINEL', 'postgresql://[PRIVATE_SENTINEL'):
            with self.assertRaises(ConfigError) as error:
                load_settings({'SABOR_STORE': 'postgres', 'SABOR_DATABASE_URL': value})
            self.assertNotIn('PRIVATE_SENTINEL', str(error.exception))
        # A stale or unrelated URL must not break the stores that never read it.
        for store in ('sqlite', 'mealie'):
            env = {'SABOR_STORE': store, 'SABOR_DATABASE_URL': 'http://PRIVATE_SENTINEL'}
            if store == 'mealie':
                env.update(MEALIE_BASE_URL='http://mealie:9000', MEALIE_API_TOKEN='t')
            self.assertEqual(load_settings(env).store, store)

    def test_missing_extra(self):
        module = importlib.import_module('sabor.store.postgres')
        with patch.dict(sys.modules, {'psycopg': None}):
            with self.assertRaisesRegex(ConfigError, r'install sabor-mcp\[postgres\]'):
                module.PostgresRecipeStore('postgres:///db')
            from sabor.core.context import assemble_context
            sqlite = assemble_context(load_settings({})).store
            assert sqlite is not None
            self.assertEqual(sqlite.id, 'sqlite')
            mealie = assemble_context(load_settings({'SABOR_STORE': 'mealie', 'MEALIE_BASE_URL': 'http://localhost',
                                                       'MEALIE_API_TOKEN': 'test'})).store
            assert mealie is not None
            self.assertEqual(mealie.id, 'mealie')

    def test_schema_validation(self):
        cls = importlib.import_module('sabor.store.postgres').PostgresRecipeStore
        for schema in ('bad-name', 'Bad', '', 'a' * 64, 'x; DROP SCHEMA public'):
            with self.assertRaises(ValueError):
                cls('postgres:///db', schema=schema)

    @unittest.skipUnless(importlib.util.find_spec('psycopg'), 'requires psycopg')
    def test_redaction_and_capabilities(self):
        import psycopg
        from sabor.core.context import assemble_context
        from sabor.core.capabilities import describe
        settings = load_settings({'SABOR_STORE': 'postgres',
                                  'SABOR_DATABASE_URL': 'postgresql://u:PRIVATE_SENTINEL@localhost/db'})
        context = assemble_context(settings)
        assert context.store is not None
        metadata = describe(context, [], 'test')
        self.assertEqual(metadata['data']['capabilities']['recipe_store']['adapters'][0]['id'], 'postgres')
        with patch.object(psycopg, 'connect', side_effect=psycopg.OperationalError('PRIVATE_SENTINEL')):
            with self.assertRaises(StoreError) as error:
                context.store.get_recipe('missing')
        self.assertNotIn('PRIVATE_SENTINEL', str(error.exception) + json.dumps(metadata))


    @unittest.skipUnless(importlib.util.find_spec('psycopg'), 'requires psycopg')
    def test_retry_only_reads_and_persistent_connection(self):
        import psycopg
        cls = importlib.import_module('sabor.store.postgres').PostgresRecipeStore
        store = cls('postgres:///unused')
        first, second = MagicMock(), MagicMock()
        for connection in (first, second):
            connection.closed = False
        first.execute.side_effect = psycopg.OperationalError('PRIVATE_SENTINEL')
        second.execute.return_value.fetchall.return_value = []
        store._connection, store._ready = first, True
        with patch.object(psycopg, 'connect', return_value=second) as connect:
            self.assertEqual(store.get_shopping_lists(), [])
            self.assertEqual(store.get_shopping_lists(), [])
            connect.assert_called_once_with('postgres:///unused', connect_timeout=10, autocommit=True)
        first.close.assert_called_once()
        self.assertEqual(second.transaction.call_count, 2)
        second.execute.side_effect = psycopg.OperationalError('PRIVATE_SENTINEL')
        with patch.object(psycopg, 'connect') as connect, self.assertRaises(StoreError):
            store.create_shopping_list('Week')
        connect.assert_not_called()
        second.close.assert_called_once()

    @unittest.skipUnless(importlib.util.find_spec('psycopg'), 'requires psycopg')
    def test_startup_redacts_driver_errors(self):
        import psycopg
        from mcp.server.fastmcp import FastMCP
        from sabor.server import register_tools, start_runtime_services
        server = FastMCP('postgres-test')
        register_tools(server, {'SABOR_STORE': 'postgres',
                               'SABOR_DATABASE_URL': 'postgresql://u:PRIVATE_SENTINEL@localhost/db'})
        with patch.object(psycopg, 'connect', side_effect=psycopg.OperationalError('PRIVATE_SENTINEL')):
            with self.assertRaisesRegex(ConfigError, '^Unable to open Postgres recipe store$'):
                start_runtime_services(server)

    @unittest.skipUnless(importlib.util.find_spec('psycopg'), 'requires psycopg')
    def test_unreachable_socket_does_not_log_dsn(self):
        from uuid import uuid4
        from sabor.store.postgres import PostgresRecipeStore
        store = PostgresRecipeStore('postgresql://u:PRIVATE_SENTINEL@/postgres?host=/tmp/sabor-missing-' + uuid4().hex)
        with patch('logging.Logger.handle') as logs, self.assertRaises(StoreError) as error:
            store.get_recipe('missing')
        self.assertNotIn('PRIVATE_SENTINEL', str(error.exception))
        self.assertNotIn('PRIVATE_SENTINEL', str(logs.call_args_list))
        store.close()
