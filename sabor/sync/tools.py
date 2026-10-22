"""Confirmed synchronization from the instance store to its sync providers."""
from dataclasses import asdict
from functools import wraps
import hashlib
import hmac
import json
import secrets
from types import SimpleNamespace
import requests

from sabor.core import errors
from sabor.core.envelope import fail, ok, preview
from sabor.core.toolsets import current_context, bring_ready, mealie_ready, tool, use_context
from sabor.store import StoreError, store_error_envelope
from .bring import BringSyncProvider

_DIRECT_SCOPE_KEY = secrets.token_bytes(32)


def _ready(settings):
    return bring_ready(settings) and mealie_ready(settings)


def _direct_context():
    """Assemble compatibility dependencies for direct Python calls."""
    import sabor.config as config
    from sabor.core.config import load_settings
    from sabor.core.context import default_context
    ctx = default_context()
    if ctx.registry.syncs:
        return ctx
    settings = load_settings({'BRING_EMAIL': config.BRING_EMAIL,
                              'BRING_PASSWORD': config.BRING_PASSWORD})
    adapter = BringSyncProvider(settings)
    # Direct calls rebuild dependencies; bind their previews to credential identity.
    adapter.scope = hmac.new(_DIRECT_SCOPE_KEY, json.dumps(
        [settings.bring_email, settings.bring_password]).encode(), 'sha256').hexdigest()
    return SimpleNamespace(store=ctx.store, registry=SimpleNamespace(syncs=(adapter,)))


def _sync_tool(collected):
    """Use startup context when bound; assemble compatibility dependencies otherwise."""
    fn = collected.__wrapped__
    @wraps(collected)
    def direct(*args, **kwargs):
        with use_context(factory=_direct_context):
            return fn(*args, **kwargs)
    # bind_context invokes __wrapped__, bypassing compatibility assembly entirely.
    direct.__wrapped__ = fn
    return direct


def _error(exc, provider):
    if isinstance(exc, StoreError):
        return store_error_envelope(exc)
    if isinstance(exc, requests.HTTPError) and exc.response is not None:
        return errors.from_http(exc.response.status_code, provider)
    if isinstance(exc, (KeyError, TypeError, AttributeError)):
        exc = ValueError()
    return errors.from_exception(exc, provider)


@_sync_tool
@tool('shopping_sync', requires=_ready, uses_context=True)
def get_shopping_sync_targets(provider: str | None = None) -> dict:
    """List available shopping destinations without modifying them."""
    return _targets(current_context(), provider)


def _targets(ctx, provider):
    adapters = [x for x in ctx.registry.syncs if provider is None or x.id == provider]
    if not adapters:
        return fail('provider_unconfigured', 'Shopping provider is not configured.')
    targets = []
    for adapter in adapters:
        try:
            targets.extend(adapter.targets())
        except Exception as exc:
            return _error(exc, adapter.id)
    return ok({'targets': targets, 'total': len(targets)})


@_sync_tool
@tool('shopping_sync', requires=_ready, uses_context=True)
def sync_shopping_list(mealie_list_id: str, target: dict, mode: str = 'append',
                       confirm: bool = False, preview_digest: str | None = None) -> dict:
    """Preview unchecked store items without writes; confirm rebuilds and rechecks the plan.

    Pass the preview_digest from the approved preview to reject changed source,
    target or actions with a fresh conflict preview and zero writes. Conflicts
    always block confirmation; replace requires its own preview. Omitting the
    optional digest retains current-snapshot confirmation (digest_checked=false).
    Remote edits during apply cannot be made atomic across systems.
    """
    return _sync(current_context(), mealie_list_id, target, mode, confirm, preview_digest)


def _digest(plan, provider, target_id, mode):
    payload = {'provider_scope': plan.get('provider_scope'), 'provider': provider, 'target_id': target_id, 'mode': mode,
               'source': plan.get('source'), 'target_snapshot': plan.get('target_snapshot'),
               'actions': {k: plan.get(k, []) for k in ('add', 'remove', 'conflicts', 'skipped')}}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':'),
                                    ensure_ascii=False, allow_nan=False).encode('utf-8')).hexdigest()


def _sync(ctx, mealie_list_id, target, mode, confirm, preview_digest):
    if (mode not in ('append', 'replace') or type(confirm) is not bool
            or (preview_digest is not None and not isinstance(preview_digest, str))
            or not isinstance(target, dict)
            or set(target) not in ({'provider', 'list_id'}, {'provider', 'list_name'})
            or any(not isinstance(v, str) or not v.strip() for v in target.values())):
        return fail('invalid_argument', 'Require a provider, one list ID or name, and a supported mode.')
    adapter = next((x for x in ctx.registry.syncs if x.id == target['provider']), None)
    if adapter is None or ctx.store is None:
        return fail('provider_unconfigured', 'Store or shopping provider is not configured.')
    provider = adapter.id
    try:
        targets = adapter.targets()
        matches = [x for x in targets if (x['list_id'] == target['list_id'] if 'list_id' in target
                   else x['name'].casefold() == target['list_name'].casefold())]
        if len(matches) != 1:
            return fail('ambiguous_target' if matches else 'not_found',
                        'Select exactly one available shopping target.', provider,
                        {'available_targets': [x['name'] for x in targets]})
        resolved = matches[0]
        def build():
            nonlocal provider
            provider = 'recipe_store'
            source = {'list_id': mealie_list_id,
                      'items': [asdict(x) for x in ctx.store.get_shopping_items(mealie_list_id).items]}
            provider = adapter.id
            plan = dict(adapter.plan(source, resolved, mode))
            plan['preview_digest'] = _digest(plan, provider, resolved['list_id'], mode)
            plan['digest_checked'] = confirm and preview_digest is not None
            return plan
        plan = build()
        if not confirm:
            return preview(plan)
        fresh = build()
        if (fresh['preview_digest'] != plan['preview_digest'] or fresh.get('conflicts')
                or (preview_digest is not None and preview_digest != fresh['preview_digest'])):
            return fail('conflict', 'The plan changed or has conflicts; approve a fresh preview.',
                        provider, {'preview': {**fresh, 'preview': True},
                                   'conflicts': fresh.get('conflicts', [])})
        return adapter.apply(fresh)
    except Exception as exc:
        return _error(exc, provider)
