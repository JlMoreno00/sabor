"""Spanish catalog command contracts, without a live Mealie server."""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from sabor import server


@contextlib.contextmanager
def _empty_config():
    """A closed empty config.toml; Windows cannot reopen an open NamedTemporaryFile."""
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / 'config.toml'
        path.touch()
        yield str(path)


class CatalogCliTests(unittest.TestCase):
    def invoke(self, command, env):
        out, err = io.StringIO(), io.StringIO()
        with _empty_config() as config, \
                patch.dict(os.environ, dict(env, SABOR_CONFIG=config), clear=True), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            with self.assertRaises(SystemExit) as caught:
                server.cli(command)
        return caught.exception.code, out.getvalue(), err.getvalue()

    def test_status_and_apply_instance_credentials(self):
        manifest = {'schema_version': 1, 'catalog_version': 'test', 'items': [
            {'name': 'gramo'}, {'name': 'cucharadita'}]}
        for action in ('status', 'apply'):
            for host in ('one', 'two'):
                env = {'MEALIE_BASE_URL': f'https://{host}.example', 'MEALIE_API_TOKEN': host}
                response = Mock(status_code=200)
                response.json.return_value = {'items': [{'id': 'g', 'name': 'gramo'}]}
                with patch('sabor.integrations.mealie.requests.get', return_value=response) as get, \
                        patch('sabor.integrations.mealie.requests.post', return_value=Mock(status_code=201)) as post, \
                        patch('sabor.integrations.mealie.requests.put') as put, \
                        patch('sabor.integrations.mealie.requests.patch') as patch_http, \
                        patch('sabor.integrations.mealie.requests.delete') as delete, \
                        patch('sabor.integrations.mealie._load_catalog_manifest', return_value=manifest), \
                        patch.object(server, 'register_tools') as register:
                    code, out, err = self.invoke(['catalog', action], env)
                self.assertEqual(code, 0)
                self.assertEqual(err, '')
                result = json.loads(out)
                self.assertTrue(result['ok'])
                self.assertEqual(result['apply'], action == 'apply')
                self.assertIn('cucharadita', out)
                self.assertEqual(get.call_count, 2)
                for call in get.call_args_list:
                    self.assertTrue(call.args[0].startswith(env['MEALIE_BASE_URL']))
                    self.assertEqual(call.kwargs['headers']['Authorization'], f'Bearer {host}')
                self.assertEqual(post.call_count, int(action == 'apply'))
                if action == 'apply':
                    self.assertEqual(post.call_args.args[0], env['MEALIE_BASE_URL'] + '/api/units')
                    self.assertEqual(post.call_args.kwargs['json']['name'], 'cucharadita')
                put.assert_not_called()
                patch_http.assert_not_called()
                delete.assert_not_called()
                register.assert_not_called()

    def test_missing_mealie(self):
        for env in ({}, {'MEALIE_BASE_URL': 'https://mealie.example'}, {'MEALIE_API_TOKEN': 'secret'}):
            with patch('requests.get') as get, patch('requests.post') as post:
                code, out, err = self.invoke(['catalog', 'status'], env)
            self.assertEqual(code, 1)
            self.assertEqual(out, '')
            self.assertIn('Mealie', err)
            self.assertNotIn('secret', err)
            get.assert_not_called()
            post.assert_not_called()

    def test_result_and_safe_error(self):
        env = {'MEALIE_BASE_URL': 'https://mealie.example', 'MEALIE_API_TOKEN': 'secret'}
        with patch('requests.get', side_effect=RuntimeError('secret Authorization: Bearer secret')):
            code, out, err = self.invoke(['catalog', 'status'], env)
        self.assertEqual(code, 1)
        self.assertFalse(json.loads(out)['ok'])
        self.assertNotIn('secret', out + err)

    def test_usage(self):
        for args, expected in [(['catalog', 'invalid'], 2), (['catalog'], 2),
                               (['--help'], 0), (['catalog', '--help'], 0)]:
            code, out, err = self.invoke(args, {})
            self.assertEqual(code, expected)
            self.assertIn('usage:', out + err)

    def test_module_entrypoint(self):
        with _empty_config() as config:
            result = subprocess.run(args=[sys.executable, '-m', 'sabor', 'catalog', 'status'],
                                    env={'PATH': os.defpath, 'PYTHONDONTWRITEBYTECODE': '1',
                                         'SABOR_CONFIG': config},
                                    capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 1)
        self.assertIn('Mealie', result.stderr)
        self.assertNotIn('Traceback', result.stderr)
