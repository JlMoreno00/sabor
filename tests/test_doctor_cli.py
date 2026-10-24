import contextlib
import io
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import requests
from test_setup_cli import FakeKeyring


class DoctorTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / 'config.toml'
        self.path.write_text('[store]\nbackend = "sqlite"\n')
        env = patch.dict(os.environ, {'SABOR_CONFIG': str(self.path),
                                     'SABOR_DATA_DIR': str(self.path.parent / 'data')}, clear=True)
        env.start()
        self.addCleanup(env.stop)
        backend = FakeKeyring()
        availability = patch.object(FakeKeyring, 'available', 1)
        availability.start()
        self.addCleanup(availability.stop)
        passwords = patch('keyring.get_password', side_effect=backend.get_password)
        passwords.start()
        self.addCleanup(passwords.stop)
        replacement = patch('keyring.get_keyring', return_value=backend)
        replacement.start()
        self.addCleanup(replacement.stop)
        self.backend = backend
        network = patch('requests.get', side_effect=AssertionError('Unexpected network'))
        self.get = network.start()
        self.addCleanup(network.stop)

    def run_cli(self, *args):
        from sabor.server import cli
        before = dict(os.environ)
        output, errors = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            with self.assertRaises(SystemExit) as result:
                cli(list(args))
        self.assertEqual(dict(os.environ), before)
        self.assertNotIn('PRIVATE_SENTINEL', output.getvalue() + errors.getvalue())
        return result.exception.code, json.loads(output.getvalue())

    def test_snapshot(self):
        code, result = self.run_cli('doctor', '--json')
        self.assertEqual(code, 0)
        self.assertEqual(list(result), ['ok', 'version', 'config_path', 'config', 'store', 'toolsets',
                                      'capabilities_summary', 'missing_secrets', 'warnings', 'next_steps'])
        self.assertEqual(result['config'], {'found': True, 'source': 'file', 'valid': True})
        self.assertEqual(list(result['store']), ['backend', 'reachable', 'schema_version'])
        self.assertEqual(result['store']['backend'], 'sqlite')
        self.assertTrue(result['store']['reachable'])
        self.assertIsInstance(result['store']['schema_version'], int)
        self.assertEqual(result['capabilities_summary'], {'recipe_store': ['sqlite'], 'pricing': [],
                                                        'import': [], 'shopping_sync': []})
        self.assertEqual(result['warnings'], [])
        self.assertEqual(result['missing_secrets'], [])
        from sabor.server import VERSION
        normalized = dict(result, config_path='<config>', version='<version>')
        self.assertEqual(result['version'], VERSION)
        self.assertEqual(normalized, dict(ok=True, version='<version>', config_path='<config>',
                         config={'found': True, 'source': 'file', 'valid': True},
                         store={'backend': 'sqlite', 'reachable': True, 'schema_version': 3},
                         toolsets=['core', 'import'],
                         capabilities_summary={'recipe_store': ['sqlite'], 'pricing': [], 'import': [], 'shopping_sync': []},
                         missing_secrets=[], warnings=[], next_steps=[]))

    def test_human_and_help(self):
        from sabor.server import cli
        for args in [('doctor',), ('config', 'show'), ('doctor', '--help'), ('config', 'show', '--help')]:
            with self.subTest(args=args), contextlib.redirect_stdout(io.StringIO()) as output:
                before = dict(os.environ)
                with self.assertRaises(SystemExit) as result:
                    cli(list(args))
                self.assertEqual(result.exception.code, 0)
                self.assertTrue(output.getvalue())
                self.assertEqual(dict(os.environ), before)

    @unittest.skipUnless(importlib.util.find_spec('psycopg'), 'requires psycopg')
    def test_postgres_failure_and_redaction(self):
        from sabor.store import StoreError
        self.path.write_text('[store]\nbackend = "postgres"\n')
        with patch.dict(os.environ, {'SABOR_DATABASE_URL': 'postgresql://u:PRIVATE_SENTINEL@localhost/db'}):
            with patch('sabor.store.postgres.PostgresRecipeStore.ensure_ready',
                       side_effect=StoreError('upstream_error', 'postgres')):
                code, result = self.run_cli('doctor', '--json')
            self.assertEqual(code, 3, result)
            self.assertFalse(result['store']['reachable'])
            self.assertEqual(result['capabilities_summary']['recipe_store'], ['postgres'])
            self.assertIn('sabor-mcp setup secret database_url', result['next_steps'][-1])
            code, result = self.run_cli('config', 'show', '--json')
            self.assertEqual(code, 0)
            row = next(r for r in result['keys'] if r['key'] == 'secrets.database_url')
            self.assertEqual(row['value'], '***')
        code, result = self.run_cli('doctor', '--json')
        self.assertIn('database_url', result['missing_secrets'])
        self.assertEqual(code, 3)

    def test_failures(self):
        with patch('sabor.store.sqlite.SqliteRecipeStore.ensure_ready', side_effect=PermissionError):
            code, result = self.run_cli('doctor', '--json')
        self.assertEqual(code, 3)
        self.assertFalse(result['store']['reachable'])
        self.assertIn('writable', result['next_steps'][0])
        self.path.write_text('[store]\nbackend = "mealie"\n[mealie]\nbase_url = "https://mealie.example"\n')
        self.get.side_effect = requests.ConnectionError('PRIVATE_SENTINEL')
        code, result = self.run_cli('doctor', '--json')
        self.assertEqual(code, 3)
        self.assertEqual(result['missing_secrets'], ['mealie_api_token'])
        self.get.assert_called_once_with('https://mealie.example/api/app/about', timeout=5)
        self.get.side_effect = None
        self.get.return_value.status_code = 503
        self.assertEqual(self.run_cli('doctor', '--json')[0], 3)
        self.get.return_value.status_code = 200
        self.assertEqual(self.run_cli('doctor', '--json')[0], 1)
        self.path.write_text('[store]\nbackend = "PRIVATE_SENTINEL"\n')
        for command in (('doctor',), ('config', 'show')):
            code, result = self.run_cli(*command, '--json')
            self.assertEqual(code, 2)
            self.assertFalse(result['config']['valid'])

    def test_origins_and_secret_redaction(self):
        self.path.write_text('[general]\nlanguage = "es"\n[secrets]\nmealie_api_token = "keyring"\nbring_email = "file"\n')
        secrets = self.path.with_name('secrets.toml')
        secrets.write_text('[secrets]\nbring_email = "PRIVATE_SENTINEL"\n')
        secrets.chmod(0o600)
        self.backend.values['sabor', 'mealie_api_token'] = 'PRIVATE_SENTINEL'
        with patch.dict(os.environ, {'GEMINI_API_KEY': 'PRIVATE_SENTINEL'}):
            code, result = self.run_cli('config', 'show', '--json')
            self.assertEqual(code, 0)
            rows = {row['key']: row for row in result['keys']}
            self.assertEqual(rows['general.language']['origin'], 'file')
            self.assertEqual(rows['store.backend']['origin'], 'default')
            for name, origin in [('mealie_api_token', 'keyring'), ('bring_email', 'file'), ('gemini_api_key', 'env')]:
                self.assertEqual(rows['secrets.' + name]['origin'], origin)
                self.assertEqual(rows['secrets.' + name]['value'], '***')
            with patch.dict(os.environ, {'SABOR_LANGUAGE': 'en'}):
                _, result = self.run_cli('config', 'show', '--json')
                self.assertEqual(result['keys'][0]['origin'], 'env')
            self.assertEqual(self.run_cli('doctor', '--json')[0], 1)
        FakeKeyring.available = 0
        _, result = self.run_cli('doctor', '--json')
        self.assertIn('Keyring is unavailable', result['warnings'])

    def test_missing_remote_and_video_secrets(self):
        self.path.write_text('[llm]\nbase_url = "https://llm.example/v1"\nmodel = "test"\n[import]\nvideo_provider = "gemini_native"\n')
        code, result = self.run_cli('doctor', '--json')
        self.assertEqual(code, 1)
        self.assertEqual(result['missing_secrets'], ['llm_api_key', 'gemini_api_key'])
        self.path.write_text('[llm]\nbase_url = "http://localhost:11434/v1"\nmodel = "test"\n')
        self.assertEqual(self.run_cli('doctor', '--json')[0], 0)
        self.path.write_text('[llm]\nbase_url = "http://[invalid"\n')
        self.assertEqual(self.run_cli('doctor', '--json')[0], 2)
