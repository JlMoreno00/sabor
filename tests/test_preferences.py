"""Structured household preferences and migration behavior."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from typing import cast
from unittest.mock import patch

from sabor.core.config import load_settings
from sabor.core.context import build_context
from sabor.core.toolsets import use_context
from sabor.tools.preferences import get_preferences, update_preferences
from sabor.preferences_cli import main


class PreferencesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'data'
        self.context = build_context(load_settings({'SABOR_DATA_DIR': str(self.root),
                                                   'SABOR_TOOLSETS': 'preferences'}))
        self.binding = use_context(self.context)
        self.binding.__enter__()
        self.addCleanup(self.binding.__exit__, None, None, None)

    def test_preview_confirm_and_schema(self):
        changes = {'set': {'diet': [' Vegetarian ', 'vegetarian'], 'dislikes': ['fish'],
                           'household_size': 2, 'pantry': ['Rice'], 'notes': ['Quick meals']}}
        preview = update_preferences(changes)
        self.assertTrue(preview['ok'])
        self.assertFalse(self.root.exists())
        result = update_preferences(changes, True)
        self.assertEqual(result['data']['preferences'], preview['data']['preferences'])
        self.assertEqual(get_preferences()['data']['preferences']['diet'], ['Vegetarian'])
        update_preferences({'add': {'pantry': ['rice', 'Beans']}, 'remove': {'pantry': ['RICE']}}, True)
        self.assertEqual(get_preferences()['data']['preferences']['pantry'], ['Beans'])
        stored = json.loads((self.root / 'preferences.json').read_text())
        self.assertEqual(stored['schema_version'], 1)
        self.assertIn('default', stored['profiles'])

    def test_invalid_values_never_write(self):
        for field in ('diet', 'dislikes', 'pantry', 'notes'):
            for value in ('text', [''], ['x' * 201], ['x'] * 201, [1]):
                self.assertEqual(update_preferences({'set': {field: value}}, True)['error']['code'], 'invalid_argument')
        for value in (True, 0, 21, 1.5, '2'):
            self.assertFalse(update_preferences({'set': {'household_size': value}}, True)['ok'])
        for changes in ({'bad': {}}, {'set': {'unknown': []}}, {'add': {'household_size': 2}}, []):
            self.assertFalse(update_preferences(cast(dict, changes), True)['ok'])
        self.assertTrue(update_preferences({'set': {'household_size': None}})['ok'])
        self.assertFalse(self.root.exists())

    def test_corruption_atomic_failure_and_isolation(self):
        update_preferences({'add': {'pantry': ['Rice']}}, True)
        path = self.root / 'preferences.json'
        original = path.read_bytes()
        with patch('sabor.preferences.os.replace', side_effect=OSError):
            self.assertFalse(update_preferences({'set': {'pantry': []}}, True)['ok'])
        self.assertEqual(path.read_bytes(), original)
        other = build_context(load_settings({'SABOR_DATA_DIR': str(self.root / 'other')}))
        with use_context(other):
            self.assertEqual(get_preferences()['data']['preferences']['pantry'], [])
        path.write_text('{broken')
        self.assertFalse(get_preferences()['ok'])
        self.assertFalse(update_preferences({'set': {'pantry': []}}, True)['ok'])
        self.assertEqual(path.read_text(), '{broken')

    def test_bounded_merge_and_schema_errors(self):
        items = [str(i) for i in range(200)]
        self.assertTrue(update_preferences({'set': {'pantry': items}}, True)['ok'])
        self.assertTrue(update_preferences({'add': {'pantry': items}}, True)['ok'])
        self.assertFalse(update_preferences({'add': {'pantry': ['extra']}}, True)['ok'])
        path = self.root / 'preferences.json'
        for raw in ('[]', 'null', '{"schema_version": 2}', '{"schema_version": true}'):
            path.write_text(raw)
            self.assertFalse(get_preferences()['ok'])
            self.assertFalse(update_preferences({}, True)['ok'])
            self.assertEqual(path.read_text(), raw)
        with patch('sabor.preferences.Path.read_text', side_effect=PermissionError):
            self.assertFalse(get_preferences()['ok'])

    def test_cli_dispatch_and_invalid_config(self):
        from sabor.server import cli
        with patch('sabor.preferences_cli.main', return_value=0) as command:
            with self.assertRaises(SystemExit) as exited:
                cli(['preferences', 'import-memory'])
            self.assertEqual(exited.exception.code, 0)
            command.assert_called_once_with(['import-memory'])
        with patch.dict('os.environ', {'SABOR_TOOLSETS': 'unknown'}, clear=True), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(['import-memory']), 1)

    def test_migration_cli(self):
        fixture = Path(__file__).parent / 'fixtures/preferences/memory_sample.md'
        env = {'SABOR_DATA_DIR': str(self.root), 'MEMORY_FILE': str(fixture)}
        with patch.dict('os.environ', env, clear=True), contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(['import-memory']), 0)
            self.assertFalse(self.root.exists())
            self.assertEqual(json.loads(output.getvalue())['data']['preferences']['pantry'], ['Arroz'])
            self.assertEqual(main(['import-memory', '--apply']), 0)
            self.assertEqual(main(['import-memory', '--apply']), 0)
        self.assertEqual(get_preferences()['data']['preferences']['notes'], ['Cenas rápidas'])
        with patch.dict('os.environ', {'MEMORY_FILE': str(self.root / 'missing')}, clear=True), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(['import-memory']), 1)
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main(['unknown']), 2)

    def test_toolset_is_opt_in_and_discoverable(self):
        from mcp.server.fastmcp import FastMCP
        from sabor.server import register_tools, _registered
        from sabor.core.config import TOOLSETS
        self.assertIn('preferences', TOOLSETS)
        for selected in ('', 'preferences'):
            server = FastMCP('test')
            register_tools(server, {'SABOR_TOOLSETS': selected, 'SABOR_DATA_DIR': str(self.root)})
            self.assertEqual('preferences' in _registered[server].enabled_toolsets, bool(selected))
            names = server._tool_manager._tools
            self.assertEqual('get_preferences' in names, bool(selected))
            discovery = names['sabor_capabilities'].fn()
            self.assertIn('preferences', str(discovery))
        self.assertFalse(self.root.exists())
