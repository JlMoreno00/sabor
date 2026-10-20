"""Pure startup configuration; no environment reads or provider probes."""
from dataclasses import dataclass, field
from typing import Mapping
from urllib.parse import urlsplit

TOOLSETS = ('core', 'import', 'shopping_sync', 'pricing', 'legacy_spoonacular',
            'legacy_pricing', 'legacy_memory', 'legacy_nutrition', 'legacy_catalog')


class ConfigError(ValueError):
    """Safe startup error, containing no operator-supplied values."""


@dataclass(frozen=True)
class Settings:
    requested_toolsets: tuple[str, ...]
    mealie_base_url: str = field(repr=False)
    mealie_api_token: str = field(repr=False)
    llm_base_url: str = field(repr=False)
    llm_model: str = field(repr=False)
    llm_api_key: str = field(repr=False)
    gemini_api_key: str = field(repr=False)
    video_recipe_gemini_model: str = field(repr=False)
    video_provider: str
    bring_email: str = field(repr=False)
    bring_password: str = field(repr=False)
    spoonacular_api_key: str = field(repr=False)
    mercadona_enabled: bool
    nutrition_enabled: bool
    memory_file: str = field(repr=False)

    normalize_imports: bool = True
    ingredient_parser: str = 'openai'
    normalize_confidence: float = 0.75

    @property
    def mealie_configured(self):
        return bool(self.mealie_base_url and self.mealie_api_token)

    @property
    def llm_configured(self):
        return bool(self.llm_base_url and self.llm_model)

    @property
    def gemini_configured(self):
        return bool(self.gemini_api_key and self.video_recipe_gemini_model)


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

    requested = tuple(dict.fromkeys(x.strip() for x in
                      get('SABOR_TOOLSETS', 'core,import,shopping_sync').split(',') if x.strip()))
    if set(requested) - set(TOOLSETS):
        raise ConfigError('Unknown toolset in SABOR_TOOLSETS')
    mealie, llm = url('MEALIE_BASE_URL'), url('SABOR_LLM_BASE_URL')
    if llm and (not get('SABOR_LLM_MODEL') or
                (urlsplit(llm).hostname not in ('localhost', '127.0.0.1') and not get('SABOR_LLM_API_KEY'))):
        raise ConfigError('LLM endpoint requires a model and remote endpoints require an API key')
    if bool(get('BRING_EMAIL')) != bool(get('BRING_PASSWORD')):
        raise ConfigError('Bring requires both email and password')
    provider = get('SABOR_VIDEO_PROVIDER')
    if provider not in ('', 'gemini_native', 'openai_compatible'):
        raise ConfigError('Invalid SABOR_VIDEO_PROVIDER')
    return Settings(requested, mealie, get('MEALIE_API_TOKEN'), llm,
                    get('SABOR_LLM_MODEL'), get('SABOR_LLM_API_KEY'),
                    get('GEMINI_API_KEY'),
                    # 0.1.x default kept so existing Gemini deployments keep video import.
                    get('VIDEO_RECIPE_GEMINI_MODEL') or 'gemini-2.5-flash-lite', provider,
                    get('BRING_EMAIL'), get('BRING_PASSWORD'), get('SPOONACULAR_API_KEY'),
                    get('SABOR_MERCADONA_ENABLED').lower() == 'true',
                    get('SABOR_NUTRITION_ENABLED').lower() == 'true', get('MEMORY_FILE', 'MEMORY.md'),
                    get('SABOR_NORMALIZE_IMPORTS', 'true').lower() == 'true',
                    get('SABOR_INGREDIENT_PARSER', 'openai'),
                    float(get('SABOR_NORMALIZE_CONFIDENCE', '0.75')))
