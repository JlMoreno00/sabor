"""Read-only pricing tools bound to the application's providers."""
from dataclasses import fields, is_dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any
import hashlib
import json

from sabor.core.toolsets import current_context, tool
from sabor.core.envelope import ok, fail
from sabor.core import errors
from sabor.store import StoreError, store_error_envelope
from .mercadona import CatalogWarming
from .provider import RetailContext, ProductQuery
from .engine import estimate, present_money
from .pantry import load_pantry


def pricing_ready(settings):
    return settings.mercadona_enabled or settings.open_prices_enabled


def _public(value) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if is_dataclass(value):
        return {f.name: _public(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, dict):
        return {k: _public(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_public(v) for v in value]
    return value


def _digest(value):
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _run(ctx, store, operation, argument, overrides=None, match_policy='cheapest'):
    if not ctx.pricing:
        return fail('provider_unconfigured', 'Pricing is not configured.')
    if store is None:
        if len(ctx.pricing) != 1:
            return fail('invalid_argument', 'Select a configured pricing provider.')
        store = {'provider': ctx.pricing[0].id}
    if (not isinstance(store, dict) or 'provider' not in store
            or store.keys() - {'provider', 'location_id', 'currency'}
            or any(not isinstance(v, str) or not v.strip() for v in store.values())):
        return fail('invalid_argument', 'Invalid retail context.')
    providers = [p for p in ctx.pricing if p.id == store['provider']]
    if len(providers) != 1:
        return fail('invalid_argument', 'Select a configured pricing provider.')
    pricing, retail = providers[0], RetailContext(**store)
    try:
        if operation == 'estimate':
            if isinstance(argument, dict) and argument.get('kind') in {'recipe', 'shopping_list', 'mealplan'} and ctx.store is None:
                return fail('provider_unconfigured', 'Recipe store is not configured.')
            pantry = []
            if isinstance(argument, dict) and argument.get('exclude_pantry') is True:
                pantry, error = load_pantry(ctx)
                if error:
                    return error
            result = estimate(argument, retail, ctx.store, pricing, overrides, match_policy, pantry)
            data = _public(result)
            if isinstance(argument, dict) and argument.get('exclude_pantry') is True:
                data['pantry'] = {'applied': True, 'matched': sum(
                    'in_pantry' in line.uncertainty.reasons for line in result.lines)}
            for key in ('consumption_subtotal', 'procurement_subtotal'):
                data[key] = present_money(getattr(result, key))
            # source_digest identifies the resolved source only; quote_digest
            # changes with any price observation (catalog refresh).
            source = [{k: _public(getattr(line, k)) for k in
                       ('key', 'name', 'required_quantity', 'base_unit')}
                      for line in result.lines]
            data['source_digest'] = _digest(source)
            data['quote_digest'] = _digest(_public(result.lines))
        elif operation == 'search_products':
            items = []
            for product in pricing.search_products(argument, retail):
                try:
                    quote = pricing.get_quote(product.id, retail)
                except LookupError:
                    # No quote here (e.g. no Open Prices observation in the country):
                    # skip it rather than failing the whole search.
                    continue
                items.append(_public(product) | {'quote': _public(quote)})
            data = {'items': items}
        else:
            data = _public(pricing.get_quote(argument, retail))
        return ok(data)
    except CatalogWarming:
        return fail('rate_limited', 'Mercadona catalog is loading; retry in a few minutes',
                    pricing.id, data={'retry_after_seconds': 120})
    except StoreError as exc:
        return store_error_envelope(exc)
    except ValueError:
        return fail('invalid_argument', 'Invalid pricing request.')
    except LookupError:
        return fail('not_found', 'Product was not found.')
    except Exception as exc:
        return errors.from_exception(exc, pricing.id)


@tool('pricing', requires=pricing_ready, uses_context=True)
def search_pricing_products(query: str, store: dict | None = None, limit: int = 10) -> Any:
    """Read-only product search with quote provenance and uncertainty; missing prices are never zero. Decimals are JSON strings."""
    if not isinstance(query, str) or not query.strip() or type(limit) is not int or not 1 <= limit <= 50:
        return fail('invalid_argument', 'Provide a query and limit from 1 to 50.')
    return _run(current_context(), store, 'search_products', ProductQuery(query, limit))


@tool('pricing', requires=pricing_ready, uses_context=True)
def get_product_price(product_id: str, store: dict | None = None) -> Any:
    """Read-only package and unit quote with uncertainty; missing prices are never zero. Decimals are JSON strings."""
    if not isinstance(product_id, str) or not product_id.strip():
        return fail('invalid_argument', 'Provide a product ID.')
    return _run(current_context(), store, 'get_quote', product_id)


@tool('pricing', requires=pricing_ready, uses_context=True)
def estimate_cost(source: dict, store: dict | None = None, product_overrides: dict | None = None,
                  match_policy: str = 'cheapest') -> Any:
    """Read-only quantity-aware estimate with uncertainty and partial consumption/procurement subtotals; missing prices are never zero. Decimals are JSON strings.

    Source kinds: items (structured items), recipe (slug, multiplier=1),
    shopping_list (list_id), mealplan (start_date, end_date, multiplier=1).
    Product overrides map resolved line keys to product IDs; no retailer fallback.
    """
    if match_policy not in ('cheapest', 'strict'):
        return fail('invalid_argument', 'Use cheapest or strict match_policy.')
    if not isinstance(source, dict) or (product_overrides is not None and not isinstance(product_overrides, dict)):
        return fail('invalid_argument', 'Provide a tagged source and optional override mapping.')
    return _run(current_context(), store, 'estimate', source, product_overrides, match_policy)
