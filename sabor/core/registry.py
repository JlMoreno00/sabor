"""Immutable startup snapshot. Factories must be local constructors, never probes."""
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Callable, Literal, Mapping, Protocol

from .config import ConfigError, Settings, TOOLSETS
from sabor.store import RecipeStore
from sabor.pricing.provider import PricingProvider


class ShoppingSyncProvider(Protocol):
    """Plan is read-only; apply executes an orchestrator-confirmed current plan."""
    id: str

    def targets(self) -> list[Mapping[str, Any]]: ...
    def plan(self, source: Mapping[str, Any], target: Mapping[str, Any],
             mode: Literal['append', 'replace']) -> Mapping[str, Any]: ...
    def apply(self, plan: Mapping[str, Any]) -> Mapping[str, Any]: ...


@dataclass(frozen=True, init=False)
class Registry:
    settings: Settings
    recipe_store: RecipeStore | None
    pricings: tuple[PricingProvider, ...]
    syncs: tuple[ShoppingSyncProvider, ...]
    enabled_toolsets: tuple[str, ...]
    disabled_toolsets: Mapping[str, str]

    def __init__(self, settings: Settings, *,
                 store_factory: Callable[[Settings], RecipeStore] | None = None,
                 pricing_factories: tuple[Callable[[Settings], PricingProvider], ...] = (),
                 sync_factories: tuple[Callable[[Settings], ShoppingSyncProvider], ...] = ()):
        # Caller supplies only configured factories; no dynamic plugin imports.
        syncs = tuple(factory(settings) for factory in sync_factories)
        recipe_store = store_factory(settings) if store_factory else None
        # SABOR_MERCADONA_ENABLED is Mercadona readiness, not a health probe.
        pricings = tuple(factory(settings) for factory in pricing_factories)
        pricings = tuple(p for p in pricings if p.id != 'mercadona' or settings.mercadona_enabled)
        pricings = tuple(p for p in pricings if p.id != 'open_prices' or settings.open_prices_enabled)
        ids = set()
        for adapter in (*syncs, *pricings):
            if adapter.id in ids:
                raise ConfigError('Duplicate adapter ID')
            ids.add(adapter.id)
        ready = dict(core=recipe_store is not None or settings.mealie_configured, pricing=bool(pricings),
                     # Import writes recipes: it needs a store (no AI provider extracts, #102).
                     import_=recipe_store is not None,
                     shopping_sync=bool(settings.bring_email and settings.bring_password),
                     legacy_spoonacular=bool(settings.spoonacular_api_key),
                     legacy_pricing=settings.mercadona_enabled, legacy_memory=True, preferences=True,
                     legacy_nutrition=(settings.store == 'mealie' and
                                       (recipe_store is None or recipe_store.id == 'mealie')),
                     legacy_catalog=settings.mealie_configured)
        ready['import'] = ready.pop('import_')
        enabled = tuple(t for t in settings.requested_toolsets if ready[t])
        disabled = {t: ('not_requested' if t not in settings.requested_toolsets else 'missing_configuration')
                    for t in TOOLSETS if t not in enabled}
        for key, value in dict(settings=settings, syncs=syncs,
                               recipe_store=recipe_store, pricings=pricings,
                               enabled_toolsets=enabled,
                               disabled_toolsets=MappingProxyType(disabled)).items():
            object.__setattr__(self, key, value)
