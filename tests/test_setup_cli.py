import contextlib
import io
import json
import os
from pathlib import Path
import re
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

    def test_secret_toml_unicode_and_controls(self):
        from sabor.secrets_store import resolve, store
        value = '😀\x7f"\\\n\t\x01'
        store(self.path, 'bring_password', value, 'file')
        self.assertEqual(resolve(self.path, 'bring_password', 'file'), value)
        with self.path.with_name('secrets.toml').open('rb') as stream:
            self.assertEqual(tomllib.load(stream)['secrets']['bring_password'], value)

    def test_atomic_write_cleans_failed_secrets(self):
        from sabor.secrets_store import atomic_write
        def fail_replace(source, target):
            if os.name == 'posix':
                self.assertEqual(Path(source).stat().st_mode & 0o777, 0o600)
            raise OSError('replace failed')
        with patch('sabor.secrets_store.os.replace', side_effect=fail_replace):
            with self.assertRaises(OSError):
                atomic_write(self.path, 'secret')
        self.assertEqual(list(self.path.parent.iterdir()), [])

    def test_malformed_llm_url_is_json_error(self):
        for source in ('flag', 'file'):
            with self.subTest(source=source):
                self.path.write_text('[llm]\nbase_url = "http://["\n' if source == 'file' else '')
                before = self.path.read_bytes()
                args = ('--llm', 'openai', '--llm-base-url', 'http://[') if source == 'flag' else ()
                code, result = self.run_setup('--non-interactive', '--json', *args)
                self.assertEqual(code, 2)
                self.assertIn('error', result)
                self.assertNotIn('http://[', result['error'])
                self.assertEqual(self.path.read_bytes(), before)

    def test_interactive_answers_are_validated_and_normalized(self):
        original = '[llm]\nbase_url = "https://example.com/v1"\nmodel = "model"\n'
        for index in range(5):
            with self.subTest(invalid_field=index):
                self.path.write_text(original)
                answers = ['en', 'sqlite', 'none', 'none', 'no']
                answers[index] = 'typo'
                with patch('builtins.input', side_effect=answers):
                    code, result = self.run_setup('--json', tty=True)
                self.assertEqual(code, 2, result)
                self.assertEqual(self.path.read_text(), original)
        for yes in (' YES ', 'y', 'sí', 'si', 's'):
            with self.subTest(yes=yes):
                self.path.write_text('')
                with patch('builtins.input', side_effect=[' EN ', ' SQLITE ', 'mercadona, open_prices', ' NONE ', yes]), patch('getpass.getpass', return_value=''):
                    code, result = self.run_setup('--json', tty=True)
                self.assertEqual(code, 0, result)
                document = tomllib.loads(self.path.read_text())
                self.assertTrue(document['pricing']['open_prices'])
                self.assertIn('bring_password', result['missing_secrets'])
        with patch('builtins.input', side_effect=['en', 'sqlite', 'none', 'none', ' N ']):
            self.assertEqual(self.run_setup('--json', tty=True)[0], 0)

    def test_pricing_preserves_custom_toolsets(self):
        for toolsets in ('["core","preferences","pricing"]', '"core,preferences,pricing"'):
            with self.subTest(toolsets=toolsets):
                original = '[general]\nlanguage = "en"\ntoolsets = ' + toolsets + '\n[pricing]\nopen_prices = true\n'
                self.path.write_text(original)
                self.assertEqual(self.run_setup('--non-interactive', '--json', '--lang', 'en')[0], 0)
                self.assertEqual(self.path.read_bytes(), original.encode())
                self.assertEqual(self.run_setup('--non-interactive', '--json', '--pricing', 'none')[0], 0)
                value = tomllib.loads(self.path.read_text())['general']['toolsets']
                self.assertEqual(value, ['core', 'preferences'] if toolsets.startswith('[') else 'core,preferences')
                self.assertEqual(self.run_setup('--non-interactive', '--json', '--pricing', 'open_prices')[0], 0)
                value = tomllib.loads(self.path.read_text())['general']['toolsets']
                self.assertEqual(value, ['core', 'preferences', 'pricing'] if toolsets.startswith('[') else 'core,preferences,pricing')

    def test_secret_permission_assertion_is_posix_only(self):
        import ast
        import inspect
        import textwrap
        tree = ast.parse(textwrap.dedent(inspect.getsource(self.test_secret_backends_and_resolution)))
        permission_checks = [node for node in ast.walk(tree) if isinstance(node, ast.If)
                             and ast.unparse(node.test) == "os.name == 'posix'"]
        self.assertTrue(any(isinstance(child, ast.Constant) and child.value == 0o600
                            for node in permission_checks for child in ast.walk(node)))

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

    def test_omitted_store_keeps_the_implicit_mealie_store(self):
        # Without --store, setup must not pin a backend over Mealie configured implicitly.
        self.path.write_text('[mealie]\nbase_url = "https://mealie.example"\n')
        code, result = self.run_setup('--non-interactive', '--json', '--lang', 'en')
        self.assertEqual(code, 0, result)
        self.assertEqual(result['store'], 'mealie')
        self.assertIn('mealie_api_token', result['missing_secrets'])
        self.assertNotIn('backend', tomllib.loads(self.path.read_text()).get('store', {}))
        self.path.unlink()
        with patch.dict(os.environ, {'MEALIE_BASE_URL': 'https://mealie.example'}):
            code, result = self.run_setup('--non-interactive', '--json', '--lang', 'en')
        self.assertEqual(code, 0, result)
        self.assertEqual(result['store'], 'mealie')
        self.assertNotIn('store', tomllib.loads(self.path.read_text()))
        code, result = self.run_setup('--non-interactive', '--json', '--store', 'sqlite')
        self.assertEqual((code, result['store']), (0, 'sqlite'))
        self.assertEqual(tomllib.loads(self.path.read_text())['store']['backend'], 'sqlite')

    def test_omitted_sqlite_path_uses_runtime_data_directory(self):
        before = dict(os.environ)
        code, result = self.run_setup('--non-interactive', '--json')
        self.assertEqual(code, 0, result)
        # No flags: nothing to pin, so config.toml may not even be created.
        document = tomllib.loads(self.path.read_text()) if self.path.exists() else {}
        self.assertNotIn('sqlite_path', document.get('store', {}))
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
        if os.name == 'posix':
            self.assertEqual(secrets.stat().st_mode & 0o777, 0o600)
            secrets.chmod(0o644)
            from sabor.core.config import ConfigError
            with self.assertRaisesRegex(ConfigError, 'only by its owner'):
                apply_config_file({'SABOR_CONFIG': str(self.path)})


class SetupHelpTests(unittest.TestCase):
    def help_text(self, *args):
        from sabor.setup_cli import main
        output = io.StringIO()
        with contextlib.redirect_stdout(output), self.assertRaises(SystemExit) as result:
            main([*args, '--help'])
        self.assertEqual(result.exception.code, 0)
        return output.getvalue()

    def test_every_option_is_described(self):
        for args, prog, options in [
                ((), 'sabor-mcp setup', ('--json', '--non-interactive', '--lang', '--store', '--llm',
                                         '--pricing', '--sqlite-path', '--mealie-url',
                                         '--mercadona-warehouse', '--llm-base-url', '--llm-model',
                                         '--gemini-model', '--bring')),
                (('secret',), 'sabor-mcp setup secret', ('--json', '--backend', 'name'))]:
            with self.subTest(prog=prog):
                text = self.help_text(*args)
                self.assertTrue(text.startswith('usage: ' + prog + ' '), text.splitlines()[0])
                lines = text.splitlines()
                start = next(i for i, line in enumerate(lines) if line in ('positional arguments:', 'options:'))
                for option in options:
                    row = next(i for i, line in enumerate(lines) if i > start
                               and line.startswith('  ') and line.strip().startswith(option))
                    # argparse puts help after a 2+ space gap, or on the next deeply indented line.
                    same_line = re.match(r'\s+\S.*?\s{2,}\S', lines[row])
                    next_line = row + 1 < len(lines) and re.match(r'\s{20,}[^-\s]', lines[row + 1])
                    self.assertTrue(same_line or next_line, option)


class SetupPreferencesToolsetTests(unittest.TestCase):
    setUp = SetupTests.setUp
    run_setup = SetupTests.run_setup

    def test_new_setup_enables_preferences_and_respects_custom_lists(self):
        code, result = self.run_setup('--non-interactive', '--json', '--lang', 'es')
        self.assertEqual(code, 0, result)
        self.assertEqual(tomllib.loads(self.path.read_text())['general']['toolsets'],
                         ['core', 'import', 'shopping_sync', 'preferences'])
        before = self.path.read_bytes()
        self.assertFalse(self.run_setup('--non-interactive', '--json', '--lang', 'es')[1]['changed'])
        self.assertEqual(before, self.path.read_bytes())
        self.run_setup('--non-interactive', '--json', '--pricing', 'open_prices')
        self.assertEqual(tomllib.loads(self.path.read_text())['general']['toolsets'],
                         ['core', 'import', 'shopping_sync', 'preferences', 'pricing'])
        self.path.write_text('[general]\ntoolsets = "core,import"\n')
        self.run_setup('--non-interactive', '--json', '--lang', 'en')
        self.assertEqual(tomllib.loads(self.path.read_text())['general']['toolsets'], 'core,import')
