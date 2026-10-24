"""Optional TOML configuration never consults the operator's real config."""
import os
from pathlib import Path
import tempfile
import subprocess
import sys
import unittest
from unittest import mock

from sabor.core.config import ConfigError, load_settings
from sabor.core.config_file import apply_config_file, config_path, read_config_file


class ConfigFileTests(unittest.TestCase):
    def test_compat_import_does_not_read_config(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'config.toml'
            path.write_text('INVALID TOML')
            result = subprocess.run(
                [sys.executable, '-c', 'import os; before = dict(os.environ); import sabor_mcp; assert dict(os.environ) == before'],
                env=dict(os.environ, SABOR_CONFIG=str(path)), capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_script_applies_config(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'config.toml'
            path.write_text('[general]\ntoolsets = "core"\n')
            code = ('import os, runpy; from unittest.mock import patch; '
                    'import sabor.server as server; '
                    'p = patch.object(server, "main"); main = p.start(); '
                    'runpy.run_module("sabor_mcp", run_name="__main__"); '
                    'main.assert_called_once_with(); '
                    'assert os.environ["SABOR_TOOLSETS"] == "core"; '
                    'assert server._contexts[server.mcp].settings.config_source == "file"')
            env = {'PATH': os.defpath, 'SABOR_CONFIG': str(path)}
            result = subprocess.run([sys.executable, '-c', code], env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_cli_config_precedes_command_branches(self):
        from sabor import server
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'config.toml'
            path.write_text('[general]\ntoolsets = "core"\n')
            for args, target in [(['preferences'], 'sabor.preferences_cli.main'),
                                 (['catalog', 'status'], 'sabor.catalog_cli.run_catalog')]:
                with self.subTest(args=args), mock.patch.dict(os.environ, {'SABOR_CONFIG': str(path)}, clear=True), \
                        mock.patch(target, side_effect=lambda *args: int(os.environ['SABOR_TOOLSETS'] != 'core')):
                    with self.assertRaises(SystemExit) as caught:
                        server.cli(args)
                    self.assertEqual(caught.exception.code, 0)

    def test_paths(self):
        for platform, suffix in [('darwin', 'Library/Application Support/sabor/config.toml'),
                                 ('linux', '.config/sabor/config.toml'),
                                 ('win32', 'AppData/Roaming/sabor/config.toml')]:
            self.assertEqual(config_path({'HOME': '/fake'}, platform), Path('/fake') / suffix)
        self.assertEqual(config_path({'XDG_CONFIG_HOME': '/xdg'}, 'linux'), Path('/xdg/sabor/config.toml'))
        self.assertEqual(config_path({'APPDATA': '/app'}, 'win32'), Path('/app/sabor/config.toml'))
        self.assertEqual(config_path({'SABOR_CONFIG': '/explicit'}, 'linux'), Path('/explicit'))
        self.assertEqual(config_path({'SABOR_CONFIG': '  ', 'XDG_CONFIG_HOME': '/xdg'}, 'linux'),
                         Path('/xdg/sabor/config.toml'))

    def test_conversion_and_precedence(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'config.toml'
            path.write_text('[general]\ntoolsets = ["core", "import"]\n[pricing]\nopen_prices = true\n[nutrition]\ndefault_cup_ml = 250\nmin_profile_confidence = 0.8\n')
            expected = {'SABOR_TOOLSETS': 'core,import', 'SABOR_OPEN_PRICES_ENABLED': 'true',
                        'SABOR_NUTRITION_DEFAULT_CUP_ML': '250', 'SABOR_NUTRITION_MIN_PROFILE_CONFIDENCE': '0.8'}
            self.assertEqual(read_config_file(path, explicit=True), expected)
            env = {'SABOR_CONFIG': str(path), 'SABOR_TOOLSETS': 'core', 'SABOR_OPEN_PRICES_ENABLED': '',
                   'SABOR_NUTRITION_DEFAULT_CUP_ML': '  '}
            self.assertEqual(apply_config_file(env), 'file')
            self.assertEqual(env['SABOR_TOOLSETS'], 'core')
            self.assertEqual(env['SABOR_OPEN_PRICES_ENABLED'], 'true')
            # Whitespace-only counts as absent, as in load_settings.
            self.assertEqual(env['SABOR_NUTRITION_DEFAULT_CUP_ML'], '250')

    def test_invalid_files_are_redacted(self):
        sentinel = 'PRIVATE_SENTINEL'
        cases = ['broken = ["PRIVATE_SENTINEL"', '[PRIVATE_SENTINEL]\nx = 1',
                 '[general]\nPRIVATE_SENTINEL = "PRIVATE_SENTINEL"',
                 '[general]\ntoolsets = ["PRIVATE_SENTINEL", 1]',
                 '[general]\ndata_dir = {x = "PRIVATE_SENTINEL"}',
                 # Each key accepts only its effective type (no strings where numbers/booleans go).
                 '[nutrition]\nprofile_ttl_days = "PRIVATE_SENTINEL"',
                 '[nutrition]\nprofile_ttl_days = 1.5',
                 '[nutrition]\ndefault_cup_ml = "PRIVATE_SENTINEL"',
                 '[import]\nnormalize_confidence = true',
                 '[pricing]\nmercadona = "PRIVATE_SENTINEL"',
                 '[store]\nbackend = 1']
        for section, key in [('mealie', 'api_token'), ('llm', 'api_key'),
                             ('import', 'gemini_api_key'), ('bring', 'email'),
                             ('bring', 'password'), ('nutrition', 'spoonacular_api_key')]:
            cases.append(f'[{section}]\n{key} = "{sentinel}"')
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / sentinel
            for content in cases:
                with self.subTest(content=content):
                    path.write_text(content)
                    with self.assertRaises(ConfigError) as caught:
                        read_config_file(path, explicit=True)
                    self.assertNotIn(sentinel, str(caught.exception))
            with mock.patch('pathlib.Path.open', side_effect=PermissionError(sentinel)):
                with self.assertRaises(ConfigError) as caught:
                    read_config_file(path, explicit=True)
                self.assertNotIn(sentinel, str(caught.exception))
            missing = path.with_name('missing_PRIVATE_SENTINEL')
            self.assertIsNone(read_config_file(missing, explicit=False))
            with self.assertRaises(ConfigError) as caught:
                read_config_file(missing, explicit=True)
            self.assertNotIn(sentinel, str(caught.exception))

    def test_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            env = {'HOME': directory, 'XDG_CONFIG_HOME': directory, 'APPDATA': directory}
            with mock.patch('sabor.core.config_file.config_path', return_value=Path(directory) / 'missing'):
                self.assertEqual(apply_config_file(env), 'defaults')
                env['MEALIE_API_TOKEN'] = 'token'
                self.assertEqual(apply_config_file(env), 'env')
        self.assertEqual(load_settings({}).config_source, 'defaults')
        self.assertEqual(load_settings({'SABOR_TOOLSETS': 'core'}).config_source, 'env')

    def test_cli_and_capabilities(self):
        from sabor import server
        from sabor.core.capabilities import describe
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'config.toml'
            path.write_text('[general]\ntoolsets = "core"\n')
            with mock.patch.dict(os.environ, {'SABOR_CONFIG': str(path)}, clear=True), \
                    mock.patch.object(server, 'register_tools') as register, \
                    mock.patch.object(server, 'start_runtime_services'), mock.patch.object(server.mcp, 'run'):
                server.cli([])
                register.assert_called_once_with(config_source='file')
                self.assertEqual(os.environ['SABOR_TOOLSETS'], 'core')
            from mcp.server.fastmcp import FastMCP
            instance = FastMCP('test')
            server.register_tools(instance, {'SABOR_TOOLSETS': ''}, config_source='file')
            self.assertEqual(describe(server._contexts[instance], [], 'test')['data']['config_source'], 'file')
