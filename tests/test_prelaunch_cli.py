"""Pre-launch polish of the installed command (0.4.0)."""
import contextlib
import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from sabor import server


def _run_module(*args, extra_env=None):
    """Run `python -m sabor` in a clean environment with isolated paths."""
    with tempfile.TemporaryDirectory() as directory:
        env = {'PATH': os.defpath, 'HOME': directory, 'SABOR_DATA_DIR': directory,
               'SABOR_CONFIG': str(Path(directory) / 'missing' / 'config.toml')}
        env.update(extra_env or {})
        return subprocess.run([sys.executable, '-m', 'sabor', *args], env=env,
                              capture_output=True, text=True, timeout=120)


def _cli(args):
    """Run cli() in-process and return (exit code, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            server.cli(args)
        except SystemExit as error:
            return error.code, out.getvalue(), err.getvalue()
    raise AssertionError('cli() did not exit')


class ServerVersionTests(unittest.TestCase):
    def test_initialize_announces_sabor_version(self):
        options = server.mcp._mcp_server.create_initialization_options()
        self.assertEqual(options.server_name, 'sabor')
        self.assertEqual(options.server_version, server.VERSION)


class CliTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.missing = str(Path(directory.name) / 'absent' / 'config.toml')
        env = mock.patch.dict(os.environ, {'SABOR_CONFIG': self.missing,
                                           'SABOR_DATA_DIR': directory.name}, clear=True)
        env.start()
        self.addCleanup(env.stop)

    def test_version_flag(self):
        code, out, _ = _cli(['--version'])
        self.assertEqual(code, 0)
        self.assertEqual(out.strip(), f'sabor-mcp {server.VERSION}')

    def test_help_with_missing_explicit_config(self):
        code, out, err = _cli(['--help'])
        self.assertEqual(code, 0)
        self.assertIn('usage: sabor-mcp', out)
        self.assertEqual(err, '')

    def test_help_lists_every_subcommand(self):
        _, out, _ = _cli(['--help'])
        for command in ('doctor', 'config', 'setup', 'catalog', 'preferences'):
            self.assertIn(command, out)
        self.assertIn('--version', out)
        # The advertised form must be runnable: import-file takes no PATH.
        self.assertIn('{import-file,import-memory} [--apply]', ' '.join(out.split()))
        self.assertNotIn('PATH', out)

    def test_preferences_help_with_missing_explicit_config(self):
        code, out, _ = _cli(['preferences', '--help'])
        self.assertEqual(code, 0)
        self.assertIn('import-file', out)

    def test_setup_and_doctor_help_with_missing_explicit_config(self):
        for args in (['setup', '--help'], ['doctor', '--help'], ['config', 'show', '--help']):
            with self.subTest(args=args):
                code, out, _ = _cli(args)
                self.assertEqual(code, 0)
                self.assertIn('usage:', out)

    def test_doctor_keeps_reporting_missing_explicit_config(self):
        code, out, _ = _cli(['doctor', '--json'])
        self.assertEqual(code, 2)
        self.assertIn('Explicit config.toml file is missing', out)

    def test_serving_with_missing_explicit_config_fails_without_traceback(self):
        with mock.patch.object(server, 'register_tools') as register, \
                mock.patch.object(server.mcp, 'run') as run:
            code, _, err = _cli([])
        self.assertEqual(code, 2)
        self.assertIn('Explicit config.toml file is missing', err)
        self.assertNotIn('Traceback', err)
        register.assert_not_called()
        run.assert_not_called()

    def test_preferences_import_file_still_dispatches_with_config(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'config.toml'
            path.write_text('[general]\ntoolsets = "core"\n')
            os.environ['SABOR_CONFIG'] = str(path)
            with mock.patch('sabor.preferences_cli.main', return_value=0) as main:
                code, _, _ = _cli(['preferences', 'import-file', '--apply'])
            self.assertEqual(code, 0)
            main.assert_called_once_with(['import-file', '--apply'])
            self.assertEqual(os.environ['SABOR_TOOLSETS'], 'core')


class WarningTests(unittest.TestCase):
    def test_help_prints_no_pydantic_settings_warning(self):
        result = _run_module('--help')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('IncompleteFieldDefinitionWarning', result.stderr)
        self.assertEqual(result.stderr, '')

    def test_doctor_prints_no_pydantic_settings_warning(self):
        result = _run_module('doctor', '--json', extra_env={'SABOR_CONFIG': ''})
        self.assertNotIn('IncompleteFieldDefinitionWarning', result.stderr)


if __name__ == '__main__':
    unittest.main()
