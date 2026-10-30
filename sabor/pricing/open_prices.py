"""Read-only crowd observations from Open Prices, never a retailer catalog."""
from collections import OrderedDict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from threading import RLock
import time
from typing import Literal

import requests

from sabor.store import StoreError
from sabor.core.errors import from_http
from .provider import PriceQuote, Product, RetailContext
from .quantities import from_structured, to_base


def _substring_query(text):
    """product_name__like is a substring filter: a one-word plural ('guisantes') drops
    its trailing 's' so the same request also finds singular names ('Guisante fino').
    Multi-word queries stay as given, since a shortened inner word breaks the substring."""
    words = text.split()
    if len(words) == 1 and len(words[0]) > 3 and words[0].lower().endswith('s'):
        return words[0][:-1]
    return text


class OpenPricesPricing:
    id = 'open_prices'
    operations = frozenset({'search_products', 'get_quote'})
    attribution = 'Open Prices (Open Food Facts), ODbL'

    def __init__(self, session=None, ttl_seconds=600, clock=time.time):
        self.session = session if session is not None else requests.Session()
        self.ttl_seconds = ttl_seconds
        self.clock = clock
        self._cache = OrderedDict()
        self._lock = RLock()

    def _context(self, store):
        country = (store.location_id or 'ES').upper()
        currency = (store.currency or 'EUR').upper()
        if (store.provider != self.id or len(country) != 2 or not country.isascii()
                or not country.isalpha() or len(currency) != 3
                or not currency.isascii() or not currency.isalpha()):
            raise ValueError('Incompatible pricing context')
        return country, currency

    def _get(self, path, params):
        key = (path, tuple(sorted(params.items())))
        with self._lock:
            cached = self._cache.get(key)
            if cached is not None and self.clock() - cached[0] < self.ttl_seconds:
                return cached[1], cached[0]
            try:
                response = self.session.get(
                    f'https://prices.openfoodfacts.org/api/v1/{path}', params=params,
                    headers={'User-Agent': 'Sabor OpenPricesPricing (read-only pricing)'}, timeout=20)
                response.raise_for_status()
            except requests.Timeout:
                raise StoreError('timeout', self.id) from None
            except requests.HTTPError as exc:
                status = getattr(exc.response, 'status_code', 503)
                code = from_http(status, self.id)['error']['code']
                raise StoreError(code, self.id) from None
            except requests.RequestException:
                raise StoreError('upstream_error', self.id) from None
            try:
                data = response.json()
                if not isinstance(data, dict) or not isinstance(data.get('items'), list):
                    raise ValueError
            except (ValueError, TypeError):
                raise RuntimeError('Open Prices response unavailable') from None
            stamp = self.clock()
            self._cache[key] = (stamp, data['items'])
            self._cache.move_to_end(key)
            while len(self._cache) > 128:
                self._cache.popitem(last=False)
            return data['items'], stamp

    @staticmethod
    def _name(product):
        return ' '.join(str(product.get(key) or '') for key in ('product_name', 'brands')).strip()

    def search_products(self, query, store):
        self._context(store)
        limit = min(query.limit, 10)
        items, _ = self._get('products', dict(product_name__like=_substring_query(query.query),
                                              order_by='-price_count', size=limit))
        return [Product(str(p['code']), self._name(p), self.attribution)
                for p in items if isinstance(p, dict) and p.get('code')
                and p.get('price_count', 0) > 0][:limit]

    def get_quote(self, product_id, store):
        country, currency = self._context(store)
        items, fetched = self._get('prices', dict(product_code=product_id,
            currency=currency, type='PRODUCT', order_by='-date', size=50))
        observations = []
        for item in items:
            if not isinstance(item, dict):
                continue
            location = item.get('location')
            if (not isinstance(location, dict)
                    or str(location.get('osm_address_country_code') or '').upper() != country
                    or item.get('currency') != currency or item.get('type') != 'PRODUCT'
                    or item.get('product_code') != product_id):
                continue
            try:
                observed = datetime.strptime(item['date'], '%Y-%m-%d').replace(tzinfo=timezone.utc)
                price = Decimal(str(item['price']))
                identifier = int(item['id'])
                if not price.is_finite() or price <= 0:
                    continue
            except (KeyError, ValueError, TypeError, InvalidOperation):
                continue
            observations.append((observed, identifier, price, item))
        if not observations:
            raise LookupError('No Open Prices observation in requested country')
        observed, _, price, item = max(observations, key=lambda row: row[:2])
        product = item.get('product') or {}
        quantity = to_base(from_structured(product.get('product_quantity'),
            product.get('product_quantity_unit'), None, None))
        units: dict[str, Literal['kg', 'l', 'unit']] = {'g': 'kg', 'ml': 'l', 'ud': 'unit'}
        unit = units.get(quantity.unit or '')
        amount = quantity.amount if unit else None
        if amount is not None and unit != 'unit':
            amount /= Decimal(1000)
        reasons = ['observation']
        if self.clock() - observed.timestamp() > 180 * 86400:
            reasons.append('stale_observation')
        if item.get('price_is_discounted'):
            reasons.append('discounted')
        if len(observations) == 1:
            reasons.append('single_observation')
        if amount is None:
            reasons.append('unknown_unit')
        location = item['location']
        place = ' '.join(str(v) for v in (
            location.get('osm_brand') or location.get('osm_name'),
            location.get('osm_address_city')) if v)
        return PriceQuote(product_id, self._name(product), price, currency, amount, unit,
            price / amount if amount is not None else None, observed,
            datetime.fromtimestamp(fetched, timezone.utc), RetailContext(self.id, country, currency),
            f'{self.attribution}; {observed.date().isoformat()}; {place}',
            uncertainty_reasons=tuple(reasons))
