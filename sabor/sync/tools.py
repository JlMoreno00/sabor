"""Confirmed synchronization from Mealie to configured shopping providers."""
import requests

from sabor.core import errors
from sabor.core.envelope import fail, ok, preview
from sabor.core.toolsets import tool, bring_ready, mealie_ready
from sabor.tools import shopping_items
from .bring import BringSyncProvider


def _ready(settings):
    return bring_ready(settings) and mealie_ready(settings)


def _error(exc, provider):
    if isinstance(exc, requests.HTTPError) and exc.response is not None:
        return errors.from_http(exc.response.status_code, provider)
    if isinstance(exc, (KeyError, TypeError, AttributeError)):
        exc = ValueError()
    return errors.from_exception(exc, provider)


@tool('shopping_sync', requires=_ready)
def get_shopping_sync_targets(provider: str | None = None) -> dict:
    """List available shopping destinations without modifying them."""
    if provider not in (None, 'bring'):
        return fail('provider_unconfigured', 'Shopping provider is not configured.')
    try:
        targets = BringSyncProvider().targets()
        return ok({'targets': targets, 'total': len(targets)})
    except Exception as exc:
        return _error(exc, 'bring')


@tool('shopping_sync', requires=_ready)
def sync_shopping_list(mealie_list_id: str, target: dict, mode: str = 'append',
                       confirm: bool = False) -> dict:
    """Preview unchecked Mealie items; confirm to rebuild and apply the current plan."""
    if (mode not in ('append', 'replace') or type(confirm) is not bool
            or not isinstance(target, dict) or target.get('provider') != 'bring'
            or set(target) not in ({'provider', 'list_id'}, {'provider', 'list_name'})
            or any(not isinstance(v, str) or not v.strip() for v in target.values())):
        return fail('invalid_argument', 'Require a Bring target, one list ID or name, and a supported mode.')
    adapter = BringSyncProvider()
    provider = 'bring'
    try:
        targets = adapter.targets()
        matches = [x for x in targets if (x['list_id'] == target['list_id'] if 'list_id' in target
                   else x['name'].casefold() == target['list_name'].casefold())]
        if len(matches) != 1:
            return fail('ambiguous_target' if matches else 'not_found',
                        'Select exactly one available shopping target.', 'bring',
                        {'available_targets': [x['name'] for x in targets]})
        resolved = matches[0]
        def build():
            nonlocal provider
            provider = 'mealie'
            source = shopping_items._read(mealie_list_id)
            if not source['ok']:
                return source
            provider = 'bring'
            return ok(adapter.plan({'list_id': mealie_list_id, 'items': source['data']}, resolved, mode))
        result = build()
        if not result['ok']:
            return result
        if not confirm:
            return preview(result['data'])
        fresh = build()
        if not fresh['ok']:
            return fresh
        if fresh != result:
            return fail('conflict', 'The snapshot changed; request a fresh preview before confirming.',
                        'sabor', {'preview': fresh['data']})
        return adapter.apply(fresh['data'])
    except Exception as exc:
        return _error(exc, provider)
