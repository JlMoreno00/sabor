"""Immutable startup snapshot. Factories must be local constructors, never probes."""
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Callable, Literal, Mapping, Protocol

from .config import ConfigError, Settings, TOOLSETS


class ImportProvider(Protocol):
    """Extract candidates without writing Mealie; orchestrator owns commit."""
    id: str
    kinds: frozenset[str]

    def preview(self, request: Mapping[str, Any]) -> Mapping[str, Any]: ...


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
    imports: tuple[ImportProvider, ...]
    syncs: tuple[ShoppingSyncProvider, ...]
    video_provider: str | None
    enabled_toolsets: tuple[str, ...]
    disabled_toolsets: Mapping[str, str]

    def __init__(self, settings: Settings, *,
                 import_factories: tuple[Callable[[Settings], ImportProvider], ...] = (),
                 sync_factories: tuple[Callable[[Settings], ShoppingSyncProvider], ...] = ()):
        # Caller supplies only configured factories; no dynamic plugin imports.
        imports = tuple(factory(settings) for factory in import_factories)
        syncs = tuple(factory(settings) for factory in sync_factories)
        ids, kinds = set(), set()
        for adapter in (*imports, *syncs):
            if adapter.id in ids:
                raise ConfigError('Duplicate adapter ID')
            ids.add(adapter.id)
        for adapter in imports:
            # Both video implementations may exist, but only one owns video.
            owned = adapter.kinds - {'video'}
            if kinds.intersection(owned):
                raise ConfigError('Duplicate import kind ownership')
            kinds.update(owned)
        available = {'gemini_native': settings.gemini_configured,
                     'openai_compatible': settings.llm_configured}
        video = settings.video_provider or next((p for p, ready in available.items() if ready), None)
        video = video if video is not None and available.get(video, False) else None
        video_owners = [a for a in imports if 'video' in a.kinds]
        if len(video_owners) > 1 and any(a.id not in available for a in video_owners):
            raise ConfigError('Duplicate import kind ownership')
        ready = dict(core=settings.mealie_configured,
                     import_=bool(settings.mealie_configured or settings.llm_configured or settings.gemini_configured),
                     shopping_sync=bool(settings.bring_email and settings.bring_password),
                     legacy_spoonacular=bool(settings.spoonacular_api_key),
                     legacy_pricing=settings.mercadona_enabled, legacy_memory=True,
                     legacy_nutrition=True, legacy_catalog=settings.mealie_configured)
        ready['import'] = ready.pop('import_')
        enabled = tuple(t for t in settings.requested_toolsets if ready[t])
        disabled = {t: ('not_requested' if t not in settings.requested_toolsets else 'missing_configuration')
                    for t in TOOLSETS if t not in enabled}
        for key, value in dict(settings=settings, imports=imports, syncs=syncs,
                               video_provider=video, enabled_toolsets=enabled,
                               disabled_toolsets=MappingProxyType(disabled)).items():
            object.__setattr__(self, key, value)
