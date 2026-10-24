"""Terminal diagnostics without MCP registration or environment mutation."""
import argparse
import json
import os
from pathlib import Path
import sqlite3
import tomllib
from typing import Any

import requests

from sabor.core.capabilities import describe
from sabor.core.config import ConfigError, load_settings
from sabor.core.config_file import FILE_KEYS, apply_config_file, config_path
from sabor.core.context import assemble_context
from sabor.core.redaction import redact
from sabor.secrets_store import SECRET_NAMES, required_secrets
from sabor.store import StoreError


def main(argv=None):
    parser = argparse.ArgumentParser(description='Inspect Sabor configuration and store health')
    commands = parser.add_subparsers(dest='command', required=True)
    doctor = commands.add_parser('doctor', help='Check configuration and store reachability')
    doctor.add_argument('--json', action='store_true', help='Print stable diagnostic JSON')
    config = commands.add_parser('config', help='Inspect effective configuration')
    show = config.add_subparsers(dest='action', required=True).add_parser('show', help='Show values and origins; secrets masked')
    show.add_argument('--json', action='store_true', help='Print values and origins as JSON')
    args = parser.parse_args(argv)
    from sabor.server import VERSION
    original = dict(os.environ)
    effective = dict(original)
    path = config_path(original)
    result: dict[str, Any] = dict(ok=False, version=VERSION, config_path=str(path),
                  config=dict(found=path.is_file(), source='file' if path.is_file() else 'defaults', valid=False),
                  store=dict(backend=None, reachable=False, schema_version=None), toolsets=[],
                  capabilities_summary={name: [] for name in ('recipe_store', 'pricing', 'import', 'shopping_sync')},
                  missing_secrets=[], warnings=[], next_steps=[])
    code = 0
    try:
        source = apply_config_file(effective)
        result['config']['source'] = source
        refs = {}
        if path.is_file():
            with path.open('rb') as stream:
                refs = tomllib.load(stream).get('secrets', {})
        missing = [name for name in required_secrets(effective)
                   if not effective.get(SECRET_NAMES[name], '').strip()]
        # Validate the rest of the configuration independently of absent credentials.
        validation = dict(effective)
        for name in missing:
            validation[SECRET_NAMES[name]] = 'postgres:///missing-secret' if name == 'database_url' else 'missing-secret'
        load_settings(validation)
        result['config']['valid'] = True
        if args.command == 'config':
            rows = []
            for (section, name), env in FILE_KEYS.items():
                origin = 'env' if original.get(env, '').strip() else 'file' if env in effective else 'default'
                rows.append(dict(key=section + '.' + name, env=env,
                                 value=effective.get(env) if origin != 'default' else None, origin=origin))
            for name, env in SECRET_NAMES.items():
                value = effective.get(env, '').strip()
                origin = 'env' if original.get(env, '').strip() else refs.get(name, 'default') if value else 'default'
                rows.append(dict(key='secrets.' + name, env=env, value='***' if value else None, origin=origin))
            result = dict(config_path=str(path), source=source, keys=rows)
        else:
            settings = load_settings(validation)
            # Do not advertise adapters enabled only by validation placeholders.
            from dataclasses import replace
            settings = replace(settings, **{name: '' for name in missing})
            context = assemble_context(settings)
            metadata = describe(context, [], VERSION)['data']
            result['toolsets'] = list(context.registry.enabled_toolsets)
            result['capabilities_summary'] = {
                name: list(dict.fromkeys(row['id'] for row in capability['adapters']))
                for name, capability in metadata['capabilities'].items()}
            result['missing_secrets'] = missing
            result['next_steps'] = ['sabor-mcp setup secret ' + name for name in missing]
            if 'keyring' in refs.values():
                import keyring
                from keyring.errors import KeyringError
                try:
                    available = keyring.get_keyring().priority >= 1
                except (KeyringError, RuntimeError):
                    available = False
                if not available:
                    result['warnings'].append('Keyring is unavailable')
            if path.with_name('secrets.toml').is_file():
                result['warnings'].append('secrets.toml is present; keep it owner-only')
            result['store']['backend'] = settings.store
            try:
                if settings.store == 'sqlite':
                    from sabor.store.sqlite import SqliteRecipeStore
                    assert isinstance(context.store, SqliteRecipeStore)
                    context.store.ensure_ready()
                    with sqlite3.connect(settings.store_db) as connection:
                        result['store']['schema_version'] = connection.execute('SELECT MAX(version) FROM schema_version').fetchone()[0]
                elif settings.store == 'postgres':
                    from sabor.store.postgres import PostgresRecipeStore
                    assert isinstance(context.store, PostgresRecipeStore)
                    if 'database_url' in missing:
                        raise StoreError('upstream_error', 'postgres')
                    try:
                        context.store.ensure_ready()
                        result['store']['schema_version'] = context.store.schema_version()
                    finally:
                        context.store.close()
                else:
                    response = requests.get(settings.mealie_base_url.rstrip('/') + '/api/app/about', timeout=5)
                    if not 200 <= response.status_code < 300:
                        raise requests.ConnectionError
                result['store']['reachable'] = True
            except (OSError, sqlite3.Error, requests.RequestException, StoreError):
                code = 3
                result['next_steps'].append(
                    f'SQLite directory {Path(settings.store_db).parent}: check that it exists and is writable, or set `[store] sqlite_path`'
                    if settings.store == 'sqlite' else
                    'Check the database URL and that Postgres accepts connections (`sabor-mcp setup secret database_url`)'
                    if settings.store == 'postgres' else 'Check that Mealie is running at the configured base_url')
            if code == 0 and missing:
                code = 1
            result['ok'] = code == 0
    except ConfigError as exc:
        code = 2
        result['config']['valid'] = False
        result['error'] = str(exc)
        result['next_steps'] = ['Fix config.toml or run `sabor-mcp setup`']

    def safe(value) -> Any:
        if isinstance(value, dict):
            return {key: safe(item) for key, item in value.items()}
        if isinstance(value, list):
            return [safe(item) for item in value]
        if isinstance(value, str):
            for env in SECRET_NAMES.values():
                secret = effective.get(env, '').strip()
                if secret:
                    value = value.replace(secret, '***')
            return redact(value)
        return value

    result = safe(result)
    if args.json:
        print(json.dumps(result))
    elif args.command == 'config' and code == 0:
        for row in result['keys']:
            print(f"{row['key']:<40} {str(row['value']):<30} {row['origin']}")
    else:
        print(json.dumps(result, indent=2))
    return code
