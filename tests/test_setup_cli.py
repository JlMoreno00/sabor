import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import tomllib
import unittest
from unittest.mock import patch

from keyring.backend import KeyringBackend
from keyring.compat import properties

from sabor.core.config_file import apply_config_file


class FakeKeyring(KeyringBackend):
    @properties.classproperty
    def priority(cls) -> float:
        return float(cls.available)

    available = 1

    def __init__(self):
        self.values = {}

    def get_password(self, service, username):
        return self.values.get((service, username))

    def set_password(self, service, username, password):
        self.values[service, username] = password


class SetupTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'config.toml'
        self.data_dir = self.path.parent / 'data'
        self.env = patch.dict(os.environ, {'SABOR_CONFIG': str(self.path),
                                           'SABOR_DATA_DIR': str(self.data_dir)}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        backend = FakeKeyring()
        for target in ('keyring.get_keyring', 'keyring.core.get_keyring'):
            replacement = patch(target, return_value=backend)
            replacement.start()
            self.addCleanup(replacement.stop)

    def run_setup(self, *args, tty=False):
        from sabor.setup_cli import main
        output = io.StringIO()
        with contextlib.redirect_stdout(output), patch('sys.stdin.isatty', return_value=tty):
            code = main(list(args))
        return code, json.loads(output.getvalue())

    def test_idempotence_and_validation(self):
        args = ('--non-interactive', '--json', '--sqlite-path', str(self.path.parent / 'recipes.db'))
        code, first = self.run_setup(*args)
        self.assertEqual(code, 0)
        self.assertTrue(first['changed'])
        before = self.path.read_bytes()
        code, second = self.run_setup(*args)
        self.assertFalse(second['changed'])
        self.assertEqual(before, self.path.read_bytes())
        self.assertFalse(self.path.with_suffix('.toml.bak').exists())
        code, error = self.run_setup(*args, '--mealie-url', 'PRIVATE_SENTINEL')
        self.assertEqual(code, 2)
        self.assertNotIn('PRIVATE_SENTINEL', str(error))
        self.assertEqual(before, self.path.read_bytes())

    def test_omitted_sqlite_path_uses_runtime_data_directory(self):
        before = dict(os.environ)
        code, result = self.run_setup('--non-interactive', '--json')
        self.assertEqual(code, 0, result)
        document = tomllib.loads(self.path.read_text())
        self.assertNotIn('sqlite_path', document['store'])
        self.assertTrue((self.data_dir / 'sabor.db').is_file())
        self.assertFalse((self.path.parent / 'sabor.db').exists())
        self.assertEqual(dict(os.environ), before)

    def test_validation_environment_overrides_file_sqlite_path(self):
        file_db = self.path.parent / 'file.db'
        self.path.write_text('[store]\nbackend = "sqlite"\nsqlite_path = ' + json.dumps(str(file_db)) + '\n')
        env_db = self.data_dir / 'environment.db'
        with patch.dict(os.environ, {'SABOR_STORE_DB': str(env_db)}):
            before = dict(os.environ)
            code, result = self.run_setup('--non-interactive', '--json')
            self.assertEqual(code, 0, result)
            self.assertTrue(env_db.is_file())
            self.assertFalse(file_db.exists())
            self.assertEqual(tomllib.loads(self.path.read_text())['store']['sqlite_path'], str(file_db))
            self.assertEqual(dict(os.environ), before)

    def test_empty_environment_uses_file_sqlite_path(self):
        file_db = self.path.parent / 'file.db'
        with patch.dict(os.environ, {'SABOR_STORE_DB': '  '}):
            code, result = self.run_setup('--non-interactive', '--json', '--sqlite-path', str(file_db))
        self.assertEqual(code, 0, result)
        self.assertTrue(file_db.is_file())

    def test_json_example_and_repeat(self):
        args = ('--non-interactive', '--json', '--store', 'sqlite', '--sqlite-path', str(self.path.parent / 'recipes.db'))
        for _ in range(2):
            code, result = self.run_setup(*args)
            self.assertEqual(code, 0)
            print(json.dumps(result))

    def test_language_and_providers(self):
        from sabor.core.config import ConfigError, load_settings
        self.assertEqual(load_settings({'SABOR_LANGUAGE': 'en'}).language, 'en')
        with self.assertRaisesRegex(ConfigError, 'Invalid SABOR_LANGUAGE'):
            load_settings({'SABOR_LANGUAGE': 'PRIVATE_SENTINEL'})
        code, result = self.run_setup('--non-interactive', '--json', '--store', 'mealie',
                                      '--mealie-url', 'https://mealie.example', '--lang', 'es',
                                      '--llm', 'gemini', '--bring', '--pricing', 'open_prices')
        self.assertEqual(code, 0)
        self.assertEqual(result['missing_secrets'], ['mealie_api_token', 'gemini_api_key', 'bring_email', 'bring_password'])
        self.assertEqual(result['next_steps'], ['sabor-mcp setup secret ' + name for name in result['missing_secrets']])
        self.assertEqual(self.run_setup('--non-interactive', '--json', '--llm', 'ollama')[0], 2)
        self.assertEqual(self.run_setup('--non-interactive', '--json', '--llm', 'ollama', '--llm-model', 'local')[0], 0)
        self.assertTrue(self.path.with_name('config.toml.bak').exists())
        self.path.write_text('[secrets]\nmealie_api_token = "PRIVATE_SENTINEL"\n')
        code, result = self.run_setup('--non-interactive', '--json')
        self.assertEqual(code, 2)
        self.assertNotIn('PRIVATE_SENTINEL', str(result))

    def test_no_tty_and_store_failure(self):
        self.assertEqual(self.run_setup('--json')[0], 2)
        code, result = self.run_setup('secret', 'mealie_api_token', '--json')
        self.assertEqual(code, 2)
        self.assertIn('sabor-mcp setup secret mealie_api_token', result['error'])
        self.assertEqual(self.run_setup('--non-interactive', '--json', '--sqlite-path', str(self.path.parent))[0], 3)

    def test_secret_backends_and_resolution(self):
        for backend in ('keyring', 'file', 'auto'):
            with self.subTest(backend=backend):
                if backend == 'auto':
                    FakeKeyring.available = 0
                    self.addCleanup(setattr, FakeKeyring, 'available', 1)
                with patch('getpass.getpass', return_value='PRIVATE_SENTINEL'):
                    code, result = self.run_setup('secret', 'mealie_api_token', '--backend', backend, '--json', tty=True)
                self.assertEqual(code, 0)
                self.assertNotIn('PRIVATE_SENTINEL', str(result) + self.path.read_text())
                env = {'SABOR_CONFIG': str(self.path)}
                apply_config_file(env)
                self.assertEqual(env['MEALIE_API_TOKEN'], 'PRIVATE_SENTINEL')
                apply_config_file(dict(env, MEALIE_API_TOKEN='override'))
        secrets = self.path.with_name('secrets.toml')
        self.assertEqual(secrets.stat().st_mode & 0o777, 0o600)
        if os.name == 'posix':
            secrets.chmod(0o644)
            from sabor.core.config import ConfigError
            with self.assertRaisesRegex(ConfigError, 'only by its owner'):
                apply_config_file({'SABOR_CONFIG': str(self.path)})
