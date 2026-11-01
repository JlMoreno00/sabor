"""Secret references and owner-only local fallback storage."""
import json
import os
from pathlib import Path
import tempfile
import tomllib

from sabor.core.config import ConfigError

SECRET_NAMES = dict(zip(
    ('mealie_api_token', 'bring_email', 'bring_password', 'spoonacular_api_key', 'database_url'),
    ('MEALIE_API_TOKEN', 'BRING_EMAIL', 'BRING_PASSWORD', 'SPOONACULAR_API_KEY', 'SABOR_DATABASE_URL')))
# Removed in 0.4.0 (#102): config.toml references to them are accepted and ignored.
REMOVED_SECRET_NAMES = frozenset({'llm_api_key', 'gemini_api_key'})


def required_secrets(env, *, bring=False):
    required = []
    if env.get('SABOR_STORE', '').strip() == 'postgres':
        required.append('database_url')
    if env.get('SABOR_STORE', '').strip() == 'mealie' or (
            not env.get('SABOR_STORE', '').strip() and env.get('MEALIE_BASE_URL', '').strip()):
        required.append('mealie_api_token')
    if bring or any(env.get(key, '').strip() for key in ('BRING_EMAIL', 'BRING_PASSWORD')):
        required.extend(('bring_email', 'bring_password'))
    return required


def serialize(document):
    return ''.join(f'[{section}]\n' + ''.join(
        f'{key} = {str(value).lower() if isinstance(value, bool) else json.dumps(value, ensure_ascii=False).replace(chr(127), r'\u007f')}\n'
        for key, value in entries.items()) + '\n' for section, entries in document.items())


def atomic_write(path, content):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, delete=False) as stream:
            temporary = stream.name  # NamedTemporaryFile creates the file owner-only.
            stream.write(content)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            Path(temporary).unlink(missing_ok=True)


def file_values(path):
    path = Path(path).with_name('secrets.toml')
    try:
        if os.name == 'posix' and path.stat().st_mode & 0o077:
            raise ConfigError('secrets.toml must be readable only by its owner')
        with path.open('rb') as stream:
            return tomllib.load(stream).get('secrets', {})
    except ConfigError:
        raise
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        raise ConfigError('Unable to read secrets.toml') from None


def resolve(path, name, backend):
    if backend == 'file':
        value = file_values(path).get(name)
        if value is not None and not isinstance(value, str):
            raise ConfigError('Invalid secrets.toml value')
        return value
    import keyring
    from keyring.errors import KeyringError
    try:
        if keyring.get_keyring().priority < 1:
            return None
        return keyring.get_password('sabor', name)
    except (KeyringError, RuntimeError):
        return None


def store(path, name, value, backend):
    if backend != 'file':
        import keyring
        from keyring.errors import KeyringError
        try:
            if keyring.get_keyring().priority < 1:
                raise RuntimeError
            keyring.set_password('sabor', name, value)
            return 'keyring'
        except (KeyringError, RuntimeError):
            if backend == 'keyring':
                raise OSError('Secret store unavailable') from None
    values = file_values(path)
    values[name] = value
    atomic_write(Path(path).with_name('secrets.toml'), serialize({'secrets': values}))
    return 'file'
