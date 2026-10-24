"""Instance-owned dependencies; factories must construct locally, without I/O.

Injected factories must return fresh services, never shared singleton caches.
"""
from dataclasses import dataclass, field
import threading
from typing import Callable, cast

from sabor.store import RecipeStore
from sabor.pricing.provider import PricingProvider
from .config import Settings
from .registry import Registry, ImportProvider, ShoppingSyncProvider


@dataclass
class RuntimeState:
    """Mutable resources owned by one registration, initialized without I/O."""
    nutrition_service: object = None
    service_lock: object = field(default_factory=threading.Lock)
    recipe_locks: tuple = field(default_factory=lambda: tuple(threading.Lock() for _ in range(64)))
    mealplan_locks: tuple = field(default_factory=lambda: tuple(threading.Lock() for _ in range(64)))
    provider_locks: tuple = field(default_factory=lambda: tuple(threading.Lock() for _ in range(64)))
    preferences_lock: object = field(default_factory=threading.Lock)
    correction_lock: object = field(default_factory=threading.Lock)
    correction_stages: dict = field(default_factory=dict)


@dataclass(frozen=True)
class AppContext:
    settings: Settings
    registry: Registry
    store: RecipeStore | None
    pricing: tuple[PricingProvider, ...]
    state: RuntimeState = field(default_factory=RuntimeState)


def mealie_store_factory(settings: Settings) -> RecipeStore:
    from sabor.store.mealie import MealieRecipeStore
    return cast(RecipeStore, MealieRecipeStore(settings.mealie_base_url, settings.mealie_api_token, settings=settings))


def sqlite_store_factory(settings: Settings) -> RecipeStore:
    from sabor.store.sqlite import SqliteRecipeStore
    return SqliteRecipeStore(settings.store_db)


def default_context() -> AppContext:
    """Compatibility for direct Python calls; registered tools never use this."""
    import sabor.config as config
    import requests
    from .config import load_settings
    import os
    # Compatibility globals remain patchable; environment-only provider settings
    # supplement them, but registered servers never enter this path.
    env = dict(os.environ)
    for key in vars(config):
        if key == 'SABOR_STORE':
            continue
        if key.isupper() and isinstance(getattr(config, key), (str, int, float, bool)):
            env[key] = str(getattr(config, key))
    for key in ("MEMORY_FILE", "SABOR_NUTRITION_DB"):
        env[key] = str(getattr(config, key))
    env["VIDEO_RECIPE_GEMINI_MODEL"] = getattr(config, "GEMINI_MODEL")
    settings = load_settings(env)
    from sabor.store.mealie import MealieRecipeStore
    return assemble_context(settings, store_factory=(lambda s: cast(RecipeStore, MealieRecipeStore(
        s.mealie_base_url, s.mealie_api_token, session=requests, settings=s)))
        if settings.store == 'mealie' else None)


def build_context(settings: Settings, *,
                  store_factory: Callable[[Settings], RecipeStore] | None = None,
                  pricing_factories: tuple[Callable[[Settings], PricingProvider], ...] = (),
                  import_factories: tuple[Callable[[Settings], ImportProvider], ...] = (),
                  sync_factories: tuple[Callable[[Settings], ShoppingSyncProvider], ...] = ()) -> AppContext:
    """Pure dependency assembly; no environment reads, probes or global writes."""
    registry = Registry(settings, store_factory=store_factory,
                        pricing_factories=pricing_factories,
                        import_factories=import_factories, sync_factories=sync_factories)
    return AppContext(settings, registry, registry.recipe_store, registry.pricings)


def assemble_context(settings: Settings, *, store_factory=None) -> AppContext:
    """Share provider assembly between registration and direct Python calls."""
    from sabor.importing.tools import OpenAIImportProvider
    from sabor.importing.video import GeminiNativeVideo
    from sabor.sync.bring import BringSyncProvider
    from sabor.pricing.mercadona import MercadonaPricing
    from sabor.integrations.mercadona import MERCADONA_WAREHOUSE
    from sabor.server import _mercadona_cache_path
    return build_context(
        settings,
        store_factory=store_factory if store_factory is not None else (
            sqlite_store_factory if settings.store == 'sqlite' else
            mealie_store_factory if settings.mealie_configured else None),
        pricing_factories=(lambda s: MercadonaPricing(
            warehouse=MERCADONA_WAREHOUSE,
            cache_path=_mercadona_cache_path(MERCADONA_WAREHOUSE, s.legacy_config.SABOR_DATA_DIR)),) if settings.mercadona_enabled else (),
        import_factories=((OpenAIImportProvider,) if settings.llm_configured else ())
        + ((GeminiNativeVideo,) if settings.gemini_configured else ()),
        sync_factories=(BringSyncProvider,) if settings.bring_email and settings.bring_password else ())
