"""Discovery projects static, non-sensitive metadata only, never adapter state."""
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from .context import AppContext

from .envelope import ok
from .registry import ImportProvider
from .registry import Registry

VIDEO_LIMITATIONS = {
    'gemini_native': ['External whole-video upload processes audio and vision.'],
    'openai_compatible': ['Sampled frames and captions only; vision support is model-dependent.',
                          'Without captions, spoken quantities are unavailable.'],
}


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

    for name, adapters in (('import', registry.imports), ('shopping_sync', registry.syncs)):
        enabled = name in registry.enabled_toolsets
        rows = []
        for adapter in adapters if enabled else ():
            row: dict[str, Any] = {'id': adapter.id}
            if name == 'import':
                kinds = set(cast(ImportProvider, adapter).kinds)
                if adapter.id != registry.video_provider:
                    kinds.discard('video')
                if not kinds:
                    continue
                row.update(kinds=sorted(kinds), operations=['preview'])
                if registry.recipe_store is not None:
                    row['operations'].append('commit')
                if 'image' in kinds:
                    row['limitations'] = ['Image import uses Mealie-owned OpenAI configuration; preview is not supported.']
                if 'video' in kinds:
                    row['active_video'] = True
                    row['limitations'] = VIDEO_LIMITATIONS[registry.video_provider or 'openai_compatible']
            else:
                row.update(kinds=['shopping_list'], operations=['targets', 'plan', 'apply'])
            rows.append(row)
        if name == 'import' and enabled:
            native = getattr(registry.recipe_store, 'native_import_kinds', frozenset())
            for kind in sorted(native):
                rows.append({'id': 'mealie', 'kinds': [kind],
                             'operations': ['preview', 'commit'] if kind == 'url' else ['commit'],
                             'limitations': [] if kind == 'url' else [
                                 'Image import uses Mealie-owned OpenAI configuration; preview is not supported.']})
        capability = {'configured': enabled, 'adapters': rows}
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
    warnings = []
    if registry.settings.video_provider and registry.video_provider is None:
        warnings.append('Requested video provider is not configured; no fallback was selected.')
    return ok({'version': version,
               'config_source': registry.settings.config_source,
               'requested_toolsets': list(registry.settings.requested_toolsets),
               'registered_tools': list(registered_tools), 'capabilities': capabilities,
               'mealie': {'configured': registry.settings.mealie_configured},
               'disabled_toolsets': [{'name': name, 'reason': reason}
                                     for name, reason in registry.disabled_toolsets.items()],
               'mealie_only': mealie_only,
               'warnings': warnings})
