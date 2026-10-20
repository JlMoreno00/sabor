"""Instance-owned dependencies; factories must construct locally, without I/O.

This is not yet a multi-tenant runtime: legacy tools still use sabor.config.
Injected factories must return fresh services, never shared singleton caches.
"""
from dataclasses import dataclass
from typing import Callable, cast

from sabor.store import RecipeStore
from sabor.pricing.provider import PricingProvider
from .config import Settings
from .registry import Registry, ImportProvider, ShoppingSyncProvider


@dataclass(frozen=True)
class AppContext:
    settings: Settings
    registry: Registry
    store: RecipeStore | None
    pricing: tuple[PricingProvider, ...]


def mealie_store_factory(settings: Settings) -> RecipeStore:
    from sabor.store.mealie import MealieRecipeStore
    return cast(RecipeStore, MealieRecipeStore(settings.mealie_base_url, settings.mealie_api_token))


def default_context() -> AppContext:
    """Compatibility for direct Python calls; registered tools never use this."""
    import sabor.config as config
    import requests
    from .config import load_settings
    settings = load_settings({"MEALIE_BASE_URL": config.MEALIE_BASE_URL,
                              "MEALIE_API_TOKEN": config.MEALIE_API_TOKEN})
    # Retain existing requests.get patch sites for direct callers only.
    from sabor.store.mealie import MealieRecipeStore
    return build_context(settings, store_factory=lambda s: cast(RecipeStore, MealieRecipeStore(
        s.mealie_base_url, s.mealie_api_token, session=requests)))


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
