"""`sabor-mcp pantry import-memory` (#144 point 7): «Despensa Conversacional» to the pantry."""
import contextlib
import io
import json
import os
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

DSN = os.environ.get('SABOR_TEST_DATABASE_URL', '')
try:
    import psycopg
except ImportError:  # the optional extra is not installed
    psycopg = None

MEMORY = """# Memoria

## Despensa Conversacional
(Actualizado: 2026-11-01)
- 2 kg de patatas
- 6 huevos
- 1 l de leche
- arroz
- un paquete de pasta

## Preferencias Aprendidas
- Cenas rápidas
"""


class PantryImportMemoryTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.memory = self.root / 'MEMORY.md'
        self.memory.write_text(MEMORY, encoding='utf-8')
        self.env = {'SABOR_STORE': 'sqlite', 'SABOR_STORE_DB': str(self.root / 'sabor.db'),
                    'SABOR_DATA_DIR': str(self.root / 'data'), 'MEMORY_FILE': str(self.memory)}

    def run_cli(self, *args, env=None):
        from sabor.pantry_cli import main
        with patch.dict('os.environ', env or self.env, clear=True), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            code = main(list(args))
        return code, json.loads(output.getvalue())

    def items(self):
        from sabor.pantry.store import PantryStore
        return [(i.food, i.quantity, i.unit, i.source)
                for i in PantryStore(self.root / 'sabor.db').read_items()]

    def test_preview_writes_nothing_and_lists_uncertain_lines(self):
        code, result = self.run_cli('import-memory')
        self.assertEqual(code, 0)
        data = result['data']
        self.assertTrue(data['preview'])
        self.assertEqual([(i['name'], i['quantity'], i['unit']) for i in data['items']],
                         [('patatas', '2000', 'g'), ('huevos', '6', 'ud'), ('leche', '1000', 'ml')])
        self.assertEqual(data['uncertain'], [{'line': 'arroz', 'reason': 'quantity_unknown'},
                                             {'line': 'un paquete de pasta', 'reason': 'quantity_unknown'}])
        self.assertEqual(len(data['effects']), 3)
        self.assertEqual(self.items(), [])
        self.assertEqual(self.memory.read_text(encoding='utf-8'), MEMORY)

    def test_apply_imports_confident_lines_once_and_keeps_memory(self):
        code, result = self.run_cli('import-memory', '--apply')
        self.assertEqual(code, 0)
        self.assertFalse(result['data']['preview'])
        self.assertEqual(sorted(self.items()), [
            ('huevos', Decimal('6'), 'ud', 'migration'),
            ('leche', Decimal('1000'), 'ml', 'migration'),
            ('patatas', Decimal('2000'), 'g', 'migration')])
        # A second run never adds the same bullets again.
        code, result = self.run_cli('import-memory', '--apply')
        self.assertEqual(code, 0)
        self.assertEqual(result['data']['items'], [])
        self.assertEqual(result['data']['already_imported'], ['2 kg de patatas', '6 huevos', '1 l de leche'])
        self.assertEqual(len(self.items()), 3)
        self.assertEqual(self.memory.read_text(encoding='utf-8'), MEMORY)

    def test_errors(self):
        code, result = self.run_cli('import-memory', env={**self.env, 'MEMORY_FILE': str(self.root / 'none')})
        self.assertEqual((code, result['error']['code']), (1, 'invalid_response'))
        # Postgres has a pantry since #152; an unreachable database is a store error, not a crash.
        postgres = {**self.env, 'SABOR_STORE': 'postgres', 'SABOR_DATABASE_URL': 'postgresql://u@127.0.0.1:1/db'}
        code, result = self.run_cli('import-memory', env=postgres)
        self.assertEqual((code, result['error']['code']), (1, 'upstream_error'))
        from sabor.pantry_cli import main
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main(['unknown']), 2)

    @unittest.skipUnless(DSN and psycopg, 'requires SABOR_TEST_DATABASE_URL and psycopg')
    def test_postgres_store_gets_the_same_pantry_as_the_tools(self):
        from types import SimpleNamespace
        from psycopg import sql
        from sabor.pantry.store import pantry_store_for
        from sabor.core.config import load_settings
        from sabor.store.postgres import PostgresRecipeStore
        schema = 'test_' + uuid4().hex
        recipes = PostgresRecipeStore(DSN, schema=schema)

        def drop():
            recipes.close()
            with psycopg.connect(DSN, autocommit=True) as connection:
                connection.execute(sql.SQL('DROP SCHEMA IF EXISTS {} CASCADE').format(sql.Identifier(schema)))
        self.addCleanup(drop)
        env = {**self.env, 'SABOR_STORE': 'postgres', 'SABOR_DATABASE_URL': DSN}
        with patch('sabor.core.context.assemble_context', return_value=SimpleNamespace(store=recipes)):
            code, result = self.run_cli('import-memory', '--apply', env=env)
            self.assertEqual(code, 0, result)
            code, again = self.run_cli('import-memory', '--apply', env=env)
        self.assertEqual(again['data']['items'], [])
        pantry = pantry_store_for(load_settings(env), recipe_store=recipes)
        assert pantry is not None
        self.assertEqual(sorted((i.food, i.source) for i in pantry.list_items()),
                         [('huevos', 'migration'), ('leche', 'migration'), ('patatas', 'migration')])

    def test_the_installed_command_dispatches_it(self):
        from sabor.server import cli
        with patch('sabor.pantry_cli.main', return_value=0) as command, \
                patch('sabor.server._apply_config_or_exit'):
            with self.assertRaises(SystemExit) as exited:
                cli(['pantry', 'import-memory'])
        self.assertEqual(exited.exception.code, 0)
        command.assert_called_once_with(['import-memory'])


if __name__ == '__main__':
    unittest.main()
