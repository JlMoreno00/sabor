"""Optional non-secret TOML configuration, applied only by entry points."""
from pathlib import Path
import sys
import tomllib

FILE_KEYS = {
    ('general', 'toolsets'): 'SABOR_TOOLSETS',
    ('general', 'data_dir'): 'SABOR_DATA_DIR',
    ('general', 'memory_file'): 'MEMORY_FILE',
    ('store', 'backend'): 'SABOR_STORE',
    ('store', 'sqlite_path'): 'SABOR_STORE_DB',
    ('mealie', 'base_url'): 'MEALIE_BASE_URL',
    ('llm', 'base_url'): 'SABOR_LLM_BASE_URL',
    ('llm', 'model'): 'SABOR_LLM_MODEL',
    ('import', 'video_provider'): 'SABOR_VIDEO_PROVIDER',
    ('import', 'gemini_model'): 'VIDEO_RECIPE_GEMINI_MODEL',
    ('import', 'video_workdir'): 'VIDEO_RECIPE_WORKDIR',
    ('import', 'normalize'): 'SABOR_NORMALIZE_IMPORTS',
    ('import', 'ingredient_parser'): 'SABOR_INGREDIENT_PARSER',
    ('import', 'normalize_confidence'): 'SABOR_NORMALIZE_CONFIDENCE',
    ('bring', 'catalog_dir'): 'BRING_CATALOG_DIR',
    ('pricing', 'mercadona'): 'SABOR_MERCADONA_ENABLED',
    ('pricing', 'mercadona_warehouse'): 'MERCADONA_WAREHOUSE',
    ('pricing', 'open_prices'): 'SABOR_OPEN_PRICES_ENABLED',
    **{('nutrition', key): 'SABOR_NUTRITION_' + key.upper() for key in (
        'enabled', 'db', 'provider', 'min_profile_confidence', 'min_publish_coverage',
        'min_publish_confidence', 'publish_estimates', 'profile_ttl_days',
        'not_found_ttl_days', 'source_language', 'default_cup_ml', 'auto_on_import', 'auto_publish')},
}
SECRET_ENV = frozenset({'MEALIE_API_TOKEN', 'SABOR_LLM_API_KEY', 'GEMINI_API_KEY',
                        'BRING_EMAIL', 'BRING_PASSWORD', 'SPOONACULAR_API_KEY'})
_SECRET_KEYS = {('mealie', 'api_token'), ('llm', 'api_key'), ('import', 'gemini_api_key'),
                ('bring', 'email'), ('bring', 'password'), ('nutrition', 'spoonacular_api_key')}


def config_path(env, platform=sys.platform):
    """Resolve a config path using only the supplied mapping and platform."""
    if env.get('SABOR_CONFIG', '').strip():
        return Path(env['SABOR_CONFIG'].strip())
    home = Path(env.get('HOME') or env.get('USERPROFILE') or '~')
    if platform == 'darwin':
        root = home / 'Library/Application Support'
    elif platform == 'win32':
        root = Path(env.get('APPDATA') or home / 'AppData/Roaming')
    else:
        root = Path(env.get('XDG_CONFIG_HOME') or home / '.config')
    return root / 'sabor/config.toml'


def read_config_file(path, *, explicit) -> dict[str, str] | None:
    """Read and validate without including file contents or paths in errors."""
    from .config import ConfigError
    try:
        with Path(path).open('rb') as stream:
            document = tomllib.load(stream)
    except FileNotFoundError:
        if not explicit:
            return None
        raise ConfigError('Explicit config.toml file is missing') from None
    except (OSError, ValueError):
        raise ConfigError('Unable to read valid config.toml') from None
    result = {}
    for section, entries in document.items():
        if section not in {s for s, _ in FILE_KEYS} or not isinstance(entries, dict):
            raise ConfigError('Unknown or invalid config.toml section')
        for key, value in entries.items():
            pair = (section, key)
            if pair in _SECRET_KEYS:
                raise ConfigError('Secrets do not belong in config.toml; set them in the environment')
            if pair not in FILE_KEYS:
                raise ConfigError('Unknown config.toml key')
            if isinstance(value, bool):
                converted = str(value).lower()
            elif isinstance(value, (str, int, float)):
                converted = str(value)
            elif pair == ('general', 'toolsets') and isinstance(value, list) and all(isinstance(x, str) for x in value):
                converted = ','.join(value)
            else:
                raise ConfigError('Invalid config.toml value type')
            result[FILE_KEYS[pair]] = converted
    return result


def apply_config_file(environ) -> str:
    """Fill absent/empty environment keys and report the configuration source."""
    values = read_config_file(config_path(environ), explicit=bool(environ.get('SABOR_CONFIG', '').strip()))
    if values is None:
        return 'env' if any(environ.get(key, '').strip() for key in (*FILE_KEYS.values(), *SECRET_ENV)) else 'defaults'
    for key, value in values.items():
        if not environ.get(key, '').strip():
            environ[key] = value
    return 'file'
