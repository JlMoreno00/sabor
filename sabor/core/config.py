"""Pure startup configuration; no environment reads or provider probes."""
import os
from dataclasses import dataclass, field
from typing import Mapping
from urllib.parse import urlsplit
from .legacy_config import LegacyConfig, load_legacy_config
from .config_file import FILE_KEYS, SECRET_ENV

# Settings removed in 0.4.0 (#102): Sabor has no AI provider. They are ignored, never
# validated, and only reported (doctor) so an old configuration keeps starting.
REMOVED_SETTINGS = ('SABOR_LLM_BASE_URL', 'SABOR_LLM_MODEL', 'SABOR_LLM_API_KEY', 'GEMINI_API_KEY',
                    'VIDEO_RECIPE_GEMINI_MODEL', 'SABOR_VIDEO_PROVIDER')
TOOLSETS = ('core', 'import', 'shopping_sync', 'pricing', 'legacy_spoonacular',
            'legacy_pricing', 'legacy_memory', 'legacy_nutrition', 'legacy_catalog', 'preferences')


class ConfigError(ValueError):
    """Safe startup error, containing no operator-supplied values."""


@dataclass(frozen=True)
class Settings:
    requested_toolsets: tuple[str, ...]
    mealie_base_url: str = field(repr=False)
    mealie_api_token: str = field(repr=False)
    bring_email: str = field(repr=False)
    bring_password: str = field(repr=False)
    spoonacular_api_key: str = field(repr=False)
    mercadona_enabled: bool
    nutrition_enabled: bool
    memory_file: str = field(repr=False)

    legacy_config: LegacyConfig = field(default_factory=lambda: load_legacy_config({}), repr=False)
    store: str = 'sqlite'
    store_db: str = field(default='/data/sabor.db', repr=False)
    normalize_imports: bool = True
    ingredient_parser: str = 'openai'
    normalize_confidence: float = 0.75
    open_prices_enabled: bool = False
    config_source: str = 'defaults'
    language: str | None = None
    database_url: str = field(default='', repr=False)
    # Names (never values) of removed AI settings still present in the configuration.
    removed_settings: tuple[str, ...] = ()
    # Absolute directories local video files may come from; empty means the user's home.
    media_roots: tuple[str, ...] = ()
    # True only for the stdio command, where the server runs on the user's machine (#102).
    local_media: bool = False
    # faster-whisper model of the optional local transcriber (sabor-mcp[transcribe]).
    transcribe_model: str = 'base'

    @property
    def mealie_configured(self):
        return bool(self.mealie_base_url and self.mealie_api_token)


def load_settings(env: Mapping[str, str]) -> Settings:
    """Parse only the supplied mapping. Empty values count as absent."""
    def get(key, default=''):
        return env.get(key, default).strip()

    def url(key):
        value = get(key)
        if value:
            try:
                parsed = urlsplit(value)
                if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
                    raise ValueError
                parsed.port
            except ValueError:
                raise ConfigError(f'{key} requires an HTTP(S) service URL') from None
        return value

    language = get('SABOR_LANGUAGE')
    if language not in ('', 'es', 'en'):
        raise ConfigError('Invalid SABOR_LANGUAGE')
    requested = tuple(dict.fromkeys(x.strip() for x in
                      get('SABOR_TOOLSETS', 'core,import,shopping_sync').split(',') if x.strip()))
    if set(requested) - set(TOOLSETS):
        raise ConfigError('Unknown toolset in SABOR_TOOLSETS')
    mealie = url('MEALIE_BASE_URL')
    if bool(get('BRING_EMAIL')) != bool(get('BRING_PASSWORD')):
        raise ConfigError('Bring requires both email and password')
    store = get('SABOR_STORE') or ('mealie' if mealie else 'sqlite')
    if store not in ('sqlite', 'mealie', 'postgres'):
        raise ConfigError('Invalid SABOR_STORE')
    database_url = get('SABOR_DATABASE_URL')
    if store == 'postgres' and not database_url:
        raise ConfigError('Postgres store requires SABOR_DATABASE_URL')
    if store == 'postgres':
        try:
            parsed = urlsplit(database_url)
            if parsed.scheme not in ('postgresql', 'postgres'):
                raise ValueError
            parsed.port
        except ValueError:
            raise ConfigError('SABOR_DATABASE_URL requires a Postgres URL') from None
    media_roots = tuple(x.strip() for x in get('SABOR_MEDIA_ROOTS').split(',') if x.strip())
    # On Windows a root also needs its drive or share: '\\Videos' would depend on the
    # process's current drive.
    if any(not os.path.isabs(root) or (os.path.sep != '/' and not os.path.splitdrive(root)[0])
           for root in media_roots):
        raise ConfigError('SABOR_MEDIA_ROOTS requires absolute directory paths')
    transcribe_model = get('SABOR_TRANSCRIBE_MODEL') or 'base'
    if transcribe_model not in ('tiny', 'base', 'small'):
        raise ConfigError('SABOR_TRANSCRIBE_MODEL must be tiny, base or small')
    legacy = load_legacy_config(env)
    return Settings(requested, mealie, get('MEALIE_API_TOKEN'),
                    get('BRING_EMAIL'), get('BRING_PASSWORD'), get('SPOONACULAR_API_KEY'),
                    get('SABOR_MERCADONA_ENABLED').lower() == 'true',
                    legacy.SABOR_NUTRITION_ENABLED, str(legacy.MEMORY_FILE), legacy,
                    store, legacy.SABOR_STORE_DB,
                    legacy.SABOR_NORMALIZE_IMPORTS,
                    legacy.SABOR_INGREDIENT_PARSER,
                    legacy.SABOR_NORMALIZE_CONFIDENCE,
                    get('SABOR_OPEN_PRICES_ENABLED').lower() == 'true',
                    'env' if any(env.get(key) for key in (*FILE_KEYS.values(), *SECRET_ENV)) else 'defaults',
                    language or None, database_url,
                    tuple(sorted(key for key in REMOVED_SETTINGS if get(key))), media_roots,
                    transcribe_model=transcribe_model)
