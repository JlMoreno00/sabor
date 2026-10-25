"""Terminal-only onboarding; never registered as an MCP tool."""
import argparse
import copy
import getpass
import json
import os
import shutil
import sqlite3
import sys
import tomllib

from sabor.core.config import ConfigError, load_settings
from sabor.core.config_file import config_path, read_config_file
from sabor.secrets_store import SECRET_NAMES, atomic_write, required_secrets, resolve, serialize, store


def write_config(path, old, document):
    if old == document:
        return False
    if path.exists():
        shutil.copyfile(path, path.with_name(path.name + '.bak'))
    atomic_write(path, serialize(document))
    return True


def _effective_store(document):
    """Store the runtime would select: SABOR_STORE, then the file, then implicit Mealie."""
    explicit = os.environ.get('SABOR_STORE', '').strip() or document.get('store', {}).get('backend')
    mealie = os.environ.get('MEALIE_BASE_URL', '').strip() or document.get('mealie', {}).get('base_url')
    return explicit or ('mealie' if mealie else 'sqlite')


def main(argv=None):
    arguments = list(sys.argv[1:] if argv is None else argv)
    secret = arguments[:1] == ['secret']
    parser = argparse.ArgumentParser(
        prog='sabor-mcp setup secret' if secret else 'sabor-mcp setup',
        description=('Store one secret from hidden terminal input (keychain, or an owner-only secrets.toml)'
                     if secret else 'Configure Sabor (English / español); secrets stay out of config.toml'))
    parser.add_argument('--json', action='store_true', help='Print one JSON result')
    if secret:
        arguments = arguments[1:]
        parser.add_argument('name', help='mealie_api_token, llm_api_key, gemini_api_key, bring_email, '
                                         'bring_password or spoonacular_api_key')
        parser.add_argument('--backend', choices=('auto', 'keyring', 'file'), default='auto',
                            help='Where to store it; auto uses the keychain and falls back to secrets.toml')
    else:
        parser.add_argument('--non-interactive', action='store_true',
                            help='Use only the flags (no prompts); required without a terminal')
        for flag, choices, text in [
                ('lang', ('es', 'en'), 'Conversation language stored as [general] language'),
                ('store', ('sqlite', 'mealie'), 'Recipe store (default sqlite)'),
                ('llm', ('none', 'openai', 'gemini', 'ollama'), 'Model for text/video recipe import')]:
            parser.add_argument('--' + flag, choices=choices, help=text)
        parser.add_argument('--pricing', choices=('none', 'mercadona', 'open_prices'), action='append',
                            help='Price provider; repeat to enable several, none disables them')
        for flag, text in [('sqlite-path', 'SQLite database path (default: the OS data directory)'),
                           ('mealie-url', 'Mealie base URL (required with --store mealie)'),
                           ('mercadona-warehouse', 'Mercadona warehouse id (default mad1)'),
                           ('llm-base-url', 'OpenAI-compatible base URL'),
                           ('llm-model', 'Model name for --llm openai or ollama'),
                           ('gemini-model', 'Gemini model for --llm gemini')]:
            parser.add_argument('--' + flag, help=text)
        parser.add_argument('--bring', action=argparse.BooleanOptionalAction, default=None,
                            help='Sync shopping lists with Bring! (needs bring_email and bring_password)')
    args = parser.parse_args(arguments)
    path = config_path(os.environ)
    result = dict(ok=False, changed=False, config_path=str(path), store=None,
                  secrets_backend={}, missing_secrets=[], next_steps=[])
    code = 0
    try:
        if path.exists():
            read_config_file(path, explicit=True)
            with path.open('rb') as stream:
                old = tomllib.load(stream)
        else:
            old = {}
        document = copy.deepcopy(old)
        refs = document.setdefault('secrets', {})
        if secret:
            if args.name not in SECRET_NAMES:
                raise ConfigError('Unknown secret name')
            if not sys.stdin.isatty():
                raise ConfigError('Run this in an interactive terminal: sabor-mcp setup secret ' + args.name)
            value = getpass.getpass('Secret / secreto: ')
            if not value:
                raise ConfigError('Secret must not be empty')
            refs[args.name] = store(path, args.name, value, args.backend)
        else:
            if not args.non_interactive:
                if not sys.stdin.isatty():
                    raise ConfigError('Use --non-interactive without a terminal')
                for field, label, default in [('lang', 'Language / idioma', 'es'), ('store', 'Store / almacén', 'sqlite'),
                                              ('pricing', 'Pricing / precios', 'none'), ('llm', 'LLM', 'none'),
                                              ('bring', 'Bring (yes/no)', 'no')]:
                    if getattr(args, field) is None:
                        if field == 'lang':
                            default = document.get('general', {}).get('language', default)
                        elif field == 'store':
                            default = _effective_store(document)
                        elif field == 'pricing':
                            default = ','.join(p for p in ('mercadona', 'open_prices') if document.get('pricing', {}).get(p)) or default
                        elif field == 'llm':
                            default = 'gemini' if document.get('import', {}).get('video_provider') == 'gemini_native' else 'openai' if document.get('llm', {}).get('base_url') else default
                        elif field == 'bring' and 'bring_email' in refs:
                            default = 'yes'
                        answer = (input(f'{label} [{default}]: ').strip() or default).lower()
                        if field == 'bring':
                            if answer not in ('yes', 'y', 'sí', 'si', 's', 'no', 'n'):
                                raise ConfigError('Invalid bring choice')
                            value = answer not in ('no', 'n')
                        else:
                            choices = next(action.choices for action in parser._actions if action.dest == field) or ()
                            value = [item.strip() for item in answer.split(',')] if field == 'pricing' else answer
                            if any(item not in choices for item in (value if isinstance(value, list) else [value])):
                                raise ConfigError('Invalid ' + field + ' choice')
                        setattr(args, field, value)
                if args.store == 'mealie' and not args.mealie_url and not document.get('mealie', {}).get('base_url'):
                    args.mealie_url = input('Mealie URL: ').strip()
                if args.llm in ('ollama', 'openai') and not args.llm_model:
                    args.llm_model = input('LLM model: ').strip()
            def put(section, key, value):
                if value is not None:
                    document.setdefault(section, {})[key] = value
            put('general', 'language', args.lang)
            # An omitted --store never pins a backend: keep the implicit Mealie selection.
            put('store', 'backend', args.store)
            put('store', 'sqlite_path', args.sqlite_path)
            put('mealie', 'base_url', args.mealie_url)
            put('pricing', 'mercadona_warehouse', args.mercadona_warehouse)
            if args.pricing is not None:
                for provider in ('mercadona', 'open_prices'):
                    put('pricing', provider, provider in args.pricing)
                existing = document.get('general', {}).get('toolsets', ['core', 'import', 'shopping_sync'])
                toolsets = existing.split(',') if isinstance(existing, str) else existing
                toolsets = list(dict.fromkeys(item.strip() for item in toolsets if item.strip() != 'pricing'))
                if any(document['pricing'].get(provider) for provider in ('mercadona', 'open_prices')):
                    toolsets.append('pricing')
                put('general', 'toolsets', ','.join(toolsets) if isinstance(existing, str) else toolsets)
            if args.llm is not None:
                for section, keys in [('llm', ('base_url', 'model')), ('import', ('video_provider', 'gemini_model'))]:
                    for key in keys:
                        document.get(section, {}).pop(key, None)
                if args.llm in ('openai', 'ollama'):
                    put('llm', 'base_url', args.llm_base_url or ('http://localhost:11434/v1' if args.llm == 'ollama' else 'https://api.openai.com/v1'))
                    put('llm', 'model', args.llm_model)
                if args.llm == 'gemini':
                    put('import', 'video_provider', 'gemini_native')
                    put('import', 'gemini_model', args.gemini_model)
            if args.llm is None:
                put('llm', 'base_url', args.llm_base_url)
                put('llm', 'model', args.llm_model)
                put('import', 'gemini_model', args.gemini_model)
            if _effective_store(document) == 'mealie' and not (
                    document.get('mealie', {}).get('base_url') or os.environ.get('MEALIE_BASE_URL', '').strip()):
                raise ConfigError('Mealie store requires a base URL')
            from sabor.core.config_file import FILE_KEYS
            required_env = {envkey: str(document[section][key])
                            for (section, key), envkey in FILE_KEYS.items()
                            if key in document.get(section, {})}
            # Non-empty environment values win, exactly as at runtime.
            required_env.update({envkey: os.environ[envkey] for envkey in FILE_KEYS.values()
                                 if os.environ.get(envkey, '').strip()})
            required = required_secrets(required_env, bring=args.bring is True or (
                args.bring is None and any(n in refs for n in ('bring_email', 'bring_password'))))
            if args.bring is False:
                for name in ('bring_email', 'bring_password'):
                    refs.pop(name, None)
            missing = [name for name in required if not (os.environ.get(SECRET_NAMES[name], '').strip() or
                       (name in refs and resolve(path, name, refs[name])))]
            if not args.non_interactive:
                for name in missing[:]:
                    value = getpass.getpass(name + ': ')
                    if value:
                        refs[name] = store(path, name, value, 'auto')
                        missing.remove(name)
            result['missing_secrets'] = missing
            result['next_steps'] = ['sabor-mcp setup secret ' + name for name in missing]
            from sabor.core.config_file import FILE_KEYS
            env = dict(os.environ)
            for (section, key), envkey in FILE_KEYS.items():
                if key in document.get(section, {}) and not env.get(envkey, '').strip():
                    value = document[section][key]
                    env[envkey] = ','.join(value) if isinstance(value, list) else str(value)
            for name in set(required) | set(refs):
                if not env.get(SECRET_NAMES[name], '').strip():
                    env[SECRET_NAMES[name]] = 'placeholder'
            settings = load_settings(env)
            if settings.store == 'sqlite':
                from sabor.store.sqlite import SqliteRecipeStore
                SqliteRecipeStore(settings.store_db).ensure_ready()
        document = {section: entries for section, entries in document.items() if entries}
        result.update(ok=True, changed=write_config(path, old, document),
                      store=_effective_store(document), secrets_backend=dict(refs))
    except ConfigError as exc:
        code = 2
        result['error'] = str(exc)
    except (OSError, sqlite3.Error, RuntimeError):
        code = 3
        result['error'] = 'Configuration or secret store is inaccessible'
    except (EOFError, KeyboardInterrupt):
        code = 2
        result['error'] = 'Setup cancelled'
    print(json.dumps(result) if args.json else ('Setup complete' if result['ok'] else result['error']))
    return code
