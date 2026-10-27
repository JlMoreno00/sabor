"""`sabor-mcp setup supabase`: store a Postgres/Supabase connection without exposing it.

The connection string is read only from hidden terminal input. With `--create-role`
the pasted administrator string is used once, in memory, to create or rotate the
least-privilege `sabor_app` role; only the `sabor_app` string is stored.
"""
import argparse
import copy
import getpass
import json
import os
import secrets
import sys
import tomllib
from typing import Any
from urllib.parse import quote, unquote, urlsplit, urlunsplit

from sabor.core.config import ConfigError
from sabor.core.config_file import config_path, read_config_file

ROLE = 'sabor_app'
IPV6_WARNING = 'Direct connections are IPv6-only on the Supabase Free plan; prefer the Session pooler'
PAUSE_WARNING = 'Supabase Free projects pause after a week of inactivity; reactivate in the dashboard or use Pro'
BACKUP_WARNING = 'Supabase Free has no automatic backups'


RECOVERY = ('The sabor_app password was already rotated, so the stored connection string no longer works: '
            'fix the cause and run `sabor-mcp setup supabase --create-role` again')


class _Unreachable(Exception):
    """The database refused or could not be reached; never carries driver text."""

    def __init__(self, rotated=False):
        super().__init__()
        self.rotated = rotated


def _query(raw):
    """(key, value, segment) per query parameter, decoded like libpq: '+' stays literal."""
    pairs = []
    for segment in raw.split('&') if raw else ():
        key, _, value = segment.partition('=')
        pairs.append((unquote(key, errors='strict'), unquote(value, errors='strict'), segment))
    return pairs


def _params(raw):
    return [(key, value) for key, value, _ in _query(raw)]


def _hosts(parts, query):
    """Hosts libpq connects to: the last ?host= (a comma list) overrides the authority host."""
    raw = dict(query).get('host') or parts.hostname or ''
    return [host.strip().lower() for host in raw.split(',') if host.strip()]


def _is_supabase(hosts):
    return any(host.endswith(('.supabase.com', '.supabase.co')) for host in hosts)


def _is_direct(hosts):
    return any(host.startswith('db.') and host.endswith('.supabase.co') for host in hosts)


def validate_dsn(dsn):
    """Return (dsn, warnings). Errors never include any part of the string."""
    try:
        parts = urlsplit((dsn or '').strip())
        port = parts.port
        query = _params(parts.query)
    except ValueError:
        raise ConfigError('Invalid Postgres connection string') from None
    if parts.scheme not in ('postgresql', 'postgres'):
        raise ConfigError('The connection string must start with postgresql://')
    host = _hosts(parts, query)
    if not host:
        raise ConfigError('The connection string needs a host')
    warnings = []
    if _is_supabase(host):
        # libpq lets ?port= (a comma list for several hosts) override the authority port.
        ports = [str(port)] if port else []
        ports += [p.strip() for key, value in query if key == 'port' for p in value.split(',')]
        if '6543' in ports:
            raise ConfigError('Use the Session pooler connection string (port 5432), not the transaction pooler (6543)')
        modes = [value for key, value in query if key == 'sslmode']
        if any(mode not in ('require', 'verify-ca', 'verify-full') for mode in modes):
            raise ConfigError('Supabase requires sslmode=require, verify-ca or verify-full')
        if not modes:
            # Append without re-encoding: the rest of the query is kept byte for byte.
            parts = parts._replace(query='&'.join(filter(None, (parts.query, 'sslmode=require'))))
        if _is_direct(host):
            warnings.append(IPV6_WARNING)
    return urlunsplit(parts), warnings


def supabase_warnings(dsn):
    """Deterministic doctor warnings for Supabase hosts; empty for any other host."""
    try:
        parts = urlsplit(dsn or '')
        host = _hosts(parts, _params(parts.query))
    except ValueError:
        return []
    if not _is_supabase(host):
        return []
    return [PAUSE_WARNING, BACKUP_WARNING] + ([IPV6_WARNING] if _is_direct(host) else [])


def app_dsn(admin_dsn, password):
    """Same server, database and options; user `sabor_app` (`sabor_app.<ref>` on the pooler)."""
    parts = urlsplit(admin_dsn)
    query = _query(parts.query)
    # libpq connects as the last ?user=, which overrides the authority user.
    user = dict((key, value) for key, value, _ in query).get('user') or parts.username or ''
    app_user = ROLE + '.' + user.split('.', 1)[1] if user.startswith('postgres.') else ROLE
    host = parts.hostname or ''
    if ':' in host:
        host = f'[{host}]'
    port = f':{parts.port}' if parts.port else ''
    netloc = f'{quote(app_user, safe="")}:{quote(password, safe="")}@{host}{port}'
    # libpq applies ?user=/?password= after the authority: drop the administrator ones.
    kept = '&'.join(segment for key, _, segment in query if key.lower() not in ('user', 'password'))
    return urlunsplit(parts._replace(netloc=netloc, query=kept))


def _check(dsn):
    """Open the store and run migrations; any failure is reported without details."""
    from sabor.store import StoreError
    from sabor.store.postgres import PostgresRecipeStore
    store = PostgresRecipeStore(dsn)
    try:
        store.ensure_ready()
    except StoreError:
        raise _Unreachable from None
    finally:
        store.close()


def _create_role(admin_dsn):
    try:
        import psycopg
        from psycopg import sql
    except ImportError:
        raise ConfigError('Postgres store requires the optional extra: install sabor-mcp[postgres]') from None
    password = secrets.token_urlsafe(32)
    role = sql.Identifier(ROLE)
    exists = rotated = False
    try:
        with psycopg.connect(admin_dsn, connect_timeout=10) as admin:
            exists = admin.execute('SELECT 1 FROM pg_roles WHERE rolname=%s', (ROLE,)).fetchone()
            admin.execute(sql.SQL('{} ROLE {} LOGIN PASSWORD {}').format(
                sql.SQL('ALTER' if exists else 'CREATE'), role, sql.Literal(password)))
            row = admin.execute('SELECT current_database()').fetchone()
            database = sql.Identifier(row[0] if row else '')
            # CREATE on the database only while sabor_app creates and owns its schema.
            admin.execute(sql.SQL('GRANT CONNECT, CREATE ON DATABASE {} TO {}').format(database, role))
            admin.commit()
            # From here the previously stored sabor_app string no longer authenticates.
            rotated = bool(exists)
            dsn = app_dsn(admin_dsn, password)
            try:
                _check(dsn)
            finally:
                admin.execute(sql.SQL('REVOKE CREATE ON DATABASE {} FROM {}').format(database, role))
                admin.commit()
    except (psycopg.Error, _Unreachable):
        raise _Unreachable(rotated) from None
    return dsn, 'rotated' if exists else 'created'


def main(argv):
    parser = argparse.ArgumentParser(
        prog='sabor-mcp setup supabase',
        description='Store a Postgres/Supabase connection typed in this terminal (hidden input). '
                    'Use the Supabase Session pooler string (port 5432).')
    parser.add_argument('--create-role', action='store_true',
                        help='Paste the postgres string once to create or rotate the least-privilege '
                             'sabor_app role; only the sabor_app string is stored')
    parser.add_argument('--json', action='store_true', help='Print one JSON result')
    args = parser.parse_args(argv)
    from sabor.secrets_store import store
    from sabor.setup_cli import write_config
    path = config_path(os.environ)
    result: dict[str, Any] = dict(ok=False, changed=False, config_path=str(path), store=None, role=None,
                                  warnings=[], next_steps=[])
    code = 0
    role = None
    try:
        if not sys.stdin.isatty():
            raise ConfigError('Run this in an interactive terminal: sabor-mcp setup supabase'
                              + (' --create-role' if args.create_role else ''))
        raw = getpass.getpass('Postgres connection string (hidden): ')
        if not raw.strip():
            raise ConfigError('Empty connection string')
        dsn, warnings = validate_dsn(raw)
        del raw
        if path.exists():
            read_config_file(path, explicit=True)
            with path.open('rb') as stream:
                old = tomllib.load(stream)
        else:
            old = {}
        if args.create_role:
            dsn, role = _create_role(dsn)
        else:
            _check(dsn)
        document = copy.deepcopy(old)
        document.setdefault('store', {})['backend'] = 'postgres'
        document.setdefault('secrets', {})['database_url'] = store(path, 'database_url', dsn, 'auto')
        del dsn
        result.update(ok=True, changed=write_config(path, old, document), store='postgres', role=role,
                      warnings=warnings, next_steps=['Run `sabor-mcp doctor --json`'])
    except ConfigError as exc:
        code = 2
        result['error'] = str(exc)
    except _Unreachable as exc:
        code = 3
        result['error'] = 'Unable to reach Postgres with that connection string'
        result['next_steps'] = ['Check the Session pooler string and that the project is not paused']
        if exc.rotated:
            result['next_steps'].append(RECOVERY)
    except OSError:
        code = 3
        result['error'] = 'Unable to store the connection string'
        if role == 'rotated':
            result['next_steps'] = [RECOVERY]
    if args.json:
        print(json.dumps(result))
    else:
        print(('Sabor Postgres store configured.' if result['ok'] else result['error']))
        for line in result['warnings'] + result['next_steps']:
            print('- ' + line)
    return code
