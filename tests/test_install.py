"""Portable installation contracts."""
import ast
from pathlib import Path
import tomllib
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


class InstallTests(unittest.TestCase):
    def test_metadata(self):
        metadata = tomllib.loads((ROOT / 'pyproject.toml').read_text())
        version = next(ast.literal_eval(node.value) for node in ast.parse(
            (ROOT / 'sabor/server.py').read_text()).body
            if isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == 'VERSION' for target in node.targets))
        self.assertEqual(metadata['tool']['hatch']['version']['path'], 'sabor/server.py')
        self.assertEqual(version, '0.3.0')
        self.assertEqual(metadata['project']['name'], 'sabor-mcp')
        self.assertEqual(set(metadata['project']['dependencies']),
                         set((ROOT / 'requirements.txt').read_text().splitlines()))

    def test_catalog(self):
        from sabor.core.paths import catalog_dir
        self.assertEqual(catalog_dir(), ROOT / 'catalog')
        with patch('pathlib.Path.is_dir', return_value=False):
            self.assertEqual(catalog_dir(), ROOT / 'sabor/_catalog')

    def test_data_directory(self):
        from sabor.core.paths import data_dir
        from sabor.core.config import load_settings
        from sabor.core.legacy_config import load_legacy_config
        with patch('pathlib.Path.is_dir', return_value=True):
            self.assertEqual(data_dir({}), Path('/data'))
            self.assertEqual(data_dir({'SABOR_DATA_DIR': '/custom'}), Path('/custom'))
        with patch('pathlib.Path.is_dir', return_value=False), patch('pathlib.Path.home', return_value=Path('/home/test')):
            for system, env, expected in [
                ('linux', {}, '/home/test/.local/share/sabor'),
                ('linux', {'XDG_DATA_HOME': '/xdg'}, '/xdg/sabor'),
                ('darwin', {}, '/home/test/Library/Application Support/sabor'),
                ('win32', {'LOCALAPPDATA': '/local'}, '/local/sabor'),
            ]:
                with self.subTest(system=system), patch('sys.platform', system):
                    self.assertEqual(data_dir(env), Path(expected))
        env = {'SABOR_DATA_DIR': '/custom'}
        settings, legacy = load_settings(env), load_legacy_config(env)
        self.assertEqual(settings.store_db, '/custom/sabor.db')
        self.assertEqual(settings.memory_file, str(legacy.MEMORY_FILE))
        self.assertEqual(legacy.SABOR_NUTRITION_DB, Path('/custom/nutrition.db'))
        overrides = dict(env, MEMORY_FILE='/memory', SABOR_STORE_DB='/store', SABOR_NUTRITION_DB='/nutrition')
        self.assertEqual(load_settings(overrides).store_db, '/store')
        self.assertEqual(load_settings(overrides).memory_file, '/memory')
        self.assertEqual(load_legacy_config(overrides).SABOR_NUTRITION_DB, Path('/nutrition'))

    def test_cli(self):
        from sabor import server
        with patch.object(server, 'register_tools'), patch.object(server, 'start_runtime_services') as start, patch.object(server.mcp, 'run') as run:
            server.cli([])
            run.assert_called_once_with(transport='stdio')
            start.assert_called_once()
            server.cli(['--transport', 'http'])
            run.assert_called_with(transport='streamable-http')
            for args, code in [(['--help'], 0), (['--transport', 'invalid'], 2)]:
                with self.assertRaises(SystemExit) as error:
                    server.cli(args)
                self.assertEqual(error.exception.code, code)

    def test_missing_ffmpeg(self):
        from sabor.importing.video import frames
        from sabor.importing.tools import ImportErrorResult
        for missing in ('ffmpeg', 'ffprobe'):
            with patch('shutil.which', side_effect=lambda name: None if name == missing else '/bin/tool'), patch('subprocess.run') as run:
                with self.assertRaises(ImportErrorResult) as error:
                    frames(Path('/private/video.mp4'))
                self.assertEqual(error.exception.result['error']['code'], 'provider_unconfigured')
                self.assertNotIn('/private', str(error.exception.result))
                run.assert_not_called()
