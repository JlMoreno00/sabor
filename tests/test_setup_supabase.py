"""`sabor-mcp setup supabase` (#69): validation, no-TTY, and role creation on a local Postgres."""
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from test_setup_cli import FakeKeyring

from sabor.core.config import ConfigError

DSN = os.environ.get('SABOR_TEST_DATABASE_URL', '')
SENTINEL = 'PRIVATE_SENTINEL'
RECOVERY = ('The sabor_app password was already rotated, so the stored connection string no longer works: '
            'fix the cause and run `sabor-mcp setup supabase --create-role` again')
try:
    import psycopg
    from psycopg import sql
except ImportError:
    psycopg = None
    sql = None


class ValidationTests(unittest.TestCase):
    def check(self, dsn):
        from sabor.supabase_setup import validate_dsn
        return validate_dsn(dsn)

    def test_supabase_hosts_require_ssl_and_the_session_pooler(self):
        pooler = f'postgresql://postgres.abcd:{SENTINEL}@aws-0-eu-west-1.pooler.supabase.com:5432/postgres'
        dsn, warnings = self.check(pooler)
        self.assertIn('sslmode=require', dsn)
        self.assertEqual(warnings, [])
        dsn, _ = self.check(pooler + '?sslmode=verify-full')
        self.assertIn('sslmode=verify-full', dsn)
        self.assertNotIn('sslmode=require', dsn)
        _, warnings = self.check(f'postgresql://postgres:{SENTINEL}@db.abcd.supabase.co:5432/postgres')
        self.assertEqual(len(warnings), 1)
        self.assertIn('IPv6', warnings[0])

    def test_rejections_never_echo_the_string(self):
        for bad in (f'postgresql://postgres.abcd:{SENTINEL}@aws-0-eu.pooler.supabase.com:6543/postgres',
                    # libpq reads ?port= after the authority port: that is the effective port.
                    f'postgresql://postgres.abcd:{SENTINEL}@aws-0-eu.pooler.supabase.com:5432/postgres?port=6543',
                    f'postgresql://postgres.abcd:{SENTINEL}@aws-0-eu.pooler.supabase.com/postgres?port=5432,6543',
                    f'mysql://u:{SENTINEL}@host/db', f'postgresql://u:{SENTINEL}@', SENTINEL, ''):
            with self.subTest(bad=bad.replace(SENTINEL, '***')), self.assertRaises(ConfigError) as caught:
                self.check(bad)
            self.assertNotIn(SENTINEL, str(caught.exception))
            self.assertNotIn('supabase.com', str(caught.exception))

    def test_app_dsn_uses_the_pooler_user_format(self):
        from sabor.supabase_setup import app_dsn
        dsn = app_dsn(f'postgresql://postgres.abcd:{SENTINEL}@aws-0-eu.pooler.supabase.com:5432/postgres'
                      '?sslmode=require', 'newpass')
        self.assertEqual(dsn, 'postgresql://sabor_app.abcd:newpass@aws-0-eu.pooler.supabase.com:5432/postgres'
                              '?sslmode=require')
        # libpq applies ?user=/?password= after the authority: never carry the admin ones over.
        dsn = app_dsn(f'postgresql://aws-0-eu.pooler.supabase.com:5432/postgres?user=postgres.abcd'
                      f'&password={SENTINEL}&sslmode=require&PASSWORD=x', 'newpass')
        self.assertEqual(dsn, 'postgresql://sabor_app.abcd:newpass@aws-0-eu.pooler.supabase.com:5432/postgres'
                              '?sslmode=require')
        local = app_dsn(f'postgresql://postgres:{SENTINEL}@/postgres?host=/tmp/x&port=5', 'p')
        self.assertEqual(local, 'postgresql://sabor_app:p@/postgres?host=/tmp/x&port=5')

    def test_supabase_warnings_for_doctor(self):
        from sabor.supabase_setup import supabase_warnings
        self.assertEqual(supabase_warnings('postgresql://u:p@localhost/db'), [])
        warnings = supabase_warnings(f'postgresql://u:{SENTINEL}@db.abcd.supabase.co:5432/postgres')
        self.assertEqual(len(warnings), 3)
        self.assertTrue(any('pause' in w for w in warnings))
        self.assertTrue(any('backups' in w for w in warnings))
        self.assertFalse(any(SENTINEL in w for w in warnings))


class CommandTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / 'config.toml'
        env = patch.dict(os.environ, {'SABOR_CONFIG': str(self.path),
                                     'SABOR_DATA_DIR': str(self.path.parent / 'data')}, clear=True)
        env.start()
        self.addCleanup(env.stop)
        self.backend = FakeKeyring()
        for target in ('keyring.get_keyring', 'keyring.core.get_keyring'):
            replacement = patch(target, return_value=self.backend)
            replacement.start()
            self.addCleanup(replacement.stop)

    def run_setup(self, *args, tty=True, secret=''):
        from sabor.setup_cli import main
        output, errors = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors), \
                patch('sys.stdin.isatty', return_value=tty), patch('getpass.getpass', return_value=secret):
            code = main(['supabase', *args, '--json'])
        text = output.getvalue() + errors.getvalue()
        return code, json.loads(output.getvalue()), text

    def test_requires_an_interactive_terminal(self):
        code, result, _ = self.run_setup('--create-role', tty=False)
        self.assertEqual(code, 2)
        self.assertIn('sabor-mcp setup supabase --create-role', result['error'])
        self.assertFalse(self.path.exists())

    def test_invalid_string_exits_2_and_writes_nothing(self):
        code, result, text = self.run_setup('--create-role', secret=f'mysql://u:{SENTINEL}@h/db')
        self.assertEqual(code, 2)
        self.assertNotIn(SENTINEL, text)
        self.assertFalse(self.path.exists())


@unittest.skipUnless(DSN and psycopg, 'requires SABOR_TEST_DATABASE_URL and psycopg')
class CreateRoleTests(CommandTests):
    def setUp(self):
        assert psycopg is not None
        with psycopg.connect(DSN) as connection:
            if connection.execute("SELECT 1 FROM pg_roles WHERE rolname='sabor_app'").fetchone():
                self.skipTest('sabor_app already exists outside this test')
            if connection.execute("SELECT 1 FROM pg_namespace WHERE nspname='sabor'").fetchone():
                self.skipTest('schema sabor already exists outside this test')
        super().setUp()
        self.addCleanup(self.drop)

    def drop(self):
        assert psycopg is not None
        with psycopg.connect(DSN, autocommit=True) as connection:
            connection.execute('DROP SCHEMA IF EXISTS sabor CASCADE')
            if connection.execute("SELECT 1 FROM pg_roles WHERE rolname='sabor_app'").fetchone():
                connection.execute('DROP OWNED BY sabor_app')
                connection.execute('DROP ROLE sabor_app')

    def admin_with_sentinel_password(self):
        # Trust auth ignores the password: it stands in for the postgres password.
        return DSN.replace('postgresql://postgres@', f'postgresql://postgres:{SENTINEL}@', 1)

    def test_create_then_rotate_keeps_only_the_app_string(self):
        assert psycopg is not None and sql is not None
        code, result, text = self.run_setup('--create-role', secret=self.admin_with_sentinel_password())
        self.assertEqual(code, 0, result)
        self.assertEqual((result['store'], result['role']), ('postgres', 'created'))
        self.assertNotIn(SENTINEL, text)
        stored = self.backend.values['sabor', 'database_url']
        for place in (self.path.read_text(), stored, *[str(v) for v in self.backend.values.values()]):
            self.assertNotIn(SENTINEL, place)
        secrets_file = self.path.with_name('secrets.toml')
        if secrets_file.exists():
            self.assertNotIn(SENTINEL, secrets_file.read_text())
        self.assertIn('backend = "postgres"', self.path.read_text())
        with psycopg.connect(stored) as connection:
            self.assertEqual(connection.execute('SELECT current_user').fetchone(), ('sabor_app',))
            owner = connection.execute("SELECT pg_get_userbyid(nspowner) FROM pg_namespace WHERE nspname='sabor'").fetchone()
            self.assertEqual(owner, ('sabor_app',))
            self.assertEqual(connection.execute('SELECT MAX(version) FROM sabor.schema_version').fetchone(), (1,))
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                connection.execute('CREATE SCHEMA other_schema')
        code, result, _ = self.run_setup('--create-role', secret=self.admin_with_sentinel_password())
        self.assertEqual((code, result['role']), (0, 'rotated'))
        self.assertNotEqual(self.backend.values['sabor', 'database_url'], stored)

    def test_app_role_reopens_the_store_after_create_is_revoked(self):
        from sabor.store.postgres import PostgresRecipeStore
        code, result, _ = self.run_setup('--create-role', secret=self.admin_with_sentinel_password())
        self.assertEqual(code, 0, result)
        # What the server does on every start with the stored sabor_app string.
        store = PostgresRecipeStore(self.backend.values['sabor', 'database_url'])
        self.addCleanup(store.close)
        store.ensure_ready()
        self.assertEqual(store.schema_version(), 1)

    def test_failure_after_rotation_tells_how_to_recover(self):
        from sabor.supabase_setup import _Unreachable
        self.assertEqual(self.run_setup('--create-role', secret=self.admin_with_sentinel_password())[0], 0)
        stored = self.backend.values['sabor', 'database_url']
        config = self.path.read_text()
        for target, error, code in (('sabor.supabase_setup._check', _Unreachable, 3),
                                    ('sabor.secrets_store.store', OSError, 3)):
            with self.subTest(target=target), patch(target, side_effect=error):
                result = self.run_setup('--create-role', secret=self.admin_with_sentinel_password())[1]
            self.assertFalse(result['ok'])
            self.assertIn(RECOVERY, result['next_steps'])
            self.assertEqual(self.backend.values['sabor', 'database_url'], stored)
            self.assertEqual(self.path.read_text(), config)
        # A failure before any rotation does not claim the stored string broke.
        with patch('psycopg.connect', side_effect=psycopg.OperationalError):
            code, result, _ = self.run_setup('--create-role', secret=self.admin_with_sentinel_password())
        self.assertEqual(code, 3)
        self.assertNotIn(RECOVERY, result['next_steps'])

    def test_own_role_string_is_stored_without_creating_roles(self):
        code, result, _ = self.run_setup(secret=DSN)
        self.assertEqual(code, 0, result)
        self.assertIsNone(result['role'])
        self.assertEqual(self.backend.values['sabor', 'database_url'], DSN)
        assert psycopg is not None
        with psycopg.connect(DSN) as connection:
            self.assertIsNone(connection.execute("SELECT 1 FROM pg_roles WHERE rolname='sabor_app'").fetchone())


if __name__ == '__main__':
    unittest.main()
