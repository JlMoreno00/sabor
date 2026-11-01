"""Discovery projects static, non-sensitive metadata only, never adapter state."""
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from .context import AppContext

from .envelope import ok
from .registry import Registry


def describe(registry: 'Registry | AppContext', registered_tools, version: str) -> dict:
    """Names/version are trusted registration metadata, not user/provider output.

    Model IDs are deliberately omitted: operator-selected strings may themselves
    contain secrets or paths. No configuration strings are copied to discovery.
    """
    # Accept the application context while retaining Registry callers for 0.2.
    registry = cast(Registry, getattr(registry, 'registry', registry))
    capabilities = {}
    for name, adapters in (('recipe_store', (registry.recipe_store,) if registry.recipe_store else ()),
                           ('pricing', registry.pricings)):
        capabilities[name] = {
            'configured': bool(adapters),
            'adapters': [{'id': a.id, 'operations': sorted(a.operations),
                          'limitations': []} for a in adapters],
        }

    for name in ('import', 'shopping_sync'):
        enabled = name in registry.enabled_toolsets
        rows: list[dict[str, Any]] = []
        if enabled and name == 'shopping_sync':
            rows = [{'id': adapter.id, 'kinds': ['shopping_list'], 'operations': ['targets', 'plan', 'apply']}
                    for adapter in registry.syncs]
        if enabled and name == 'import':
            # Mealie's native recipe page import; Sabor has no AI provider adapter (#102).
            native = getattr(registry.recipe_store, 'native_import_kinds', frozenset())
            rows = [{'id': 'mealie', 'kinds': [kind], 'operations': ['preview', 'commit'],
                     'limitations': []} for kind in sorted(native)]
        capability: dict[str, Any] = {'configured': enabled, 'adapters': rows}
        if not enabled:
            capability['reason'] = registry.disabled_toolsets[name]
        capabilities[name] = capability
    # Toolsets that only work with Mealie, with the reason they are unavailable here.
    mealie_store = registry.settings.store == 'mealie' and (
        registry.recipe_store is None or registry.recipe_store.id == 'mealie')
    mealie_only = []
    if not mealie_store:
        mealie_only.append({'toolset': 'legacy_nutrition', 'reason': 'requires_mealie_store',
                            'detail': 'Nutrition is indexed by Mealie food and recipe IDs; select the Mealie store.'})
    if not registry.settings.mealie_configured:
        mealie_only.append({'toolset': 'legacy_catalog', 'reason': 'requires_mealie',
                            'detail': 'The Spanish catalog seeds Mealie foods and units; configure Mealie.'})
    warnings: list[str] = []
    return ok({'version': version,
               'language': registry.settings.language,
               'config_source': registry.settings.config_source,
               'requested_toolsets': list(registry.settings.requested_toolsets),
               'registered_tools': list(registered_tools), 'capabilities': capabilities,
               'mealie': {'configured': registry.settings.mealie_configured},
               'disabled_toolsets': [{'name': name, 'reason': reason}
                                     for name, reason in registry.disabled_toolsets.items()],
               'mealie_only': mealie_only,
               'warnings': warnings})
