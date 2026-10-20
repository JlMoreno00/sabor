"""Instance-owned unofficial catalog adapter; no legacy cache is accessed.

Provenance format: mercadona:<warehouse>:tienda.mercadona.es-catalog.
Product summaries and quotes share the last successful catalog observation.
"""
import time
import warnings
from datetime import datetime, timezone
from decimal import Decimal
from threading import RLock
from typing import Any, Literal

import requests

from sabor.integrations.mercadona import (
    _mercadona_cost_query, _mercadona_cost_score, _mercadona_normalize,
)
from sabor.pricing.provider import PriceQuote, Product, ProductQuery, RetailContext
from sabor.pricing.quantities import package_from_mercadona


class MercadonaPricing:
    id = 'mercadona'
    operations = frozenset({'search_products', 'get_quote'})

    def __init__(self, warehouse='mad1', session=None, ttl_seconds=86400, clock=time.time):
        self.warehouse = warehouse
        self.session = session if session is not None else requests.Session()
        self.ttl_seconds = ttl_seconds
        self.clock = clock
        self._catalog: dict[str, dict[str, Any]] | None = None
        self._loaded_at: float | None = None
        self._lock = RLock()
        self._provenance = f'mercadona:{warehouse}:tienda.mercadona.es-catalog'

    def _validate(self, store):
        if (store.provider != self.id or store.location_id not in (None, self.warehouse)
                or store.currency not in (None, 'EUR')):
            raise ValueError('Incompatible pricing context')

    def _get(self, path):
        response = self.session.get(
            f'https://tienda.mercadona.es/api{path}',
            params={'lang': 'es', 'wh': self.warehouse}, timeout=20,
            headers={'User-Agent': 'Mozilla/5.0'})
        response.raise_for_status()
        return response.json()

    def _fetch(self):
        root = self._get('/categories/')
        categories = root if isinstance(root, list) else root.get('results', root.get('categories'))
        if not isinstance(categories, list) or not categories:
            raise ValueError('Invalid catalog categories')
        catalog = {}

        def collect(node):
            if not isinstance(node, dict):
                raise ValueError('Invalid category')
            for product in node.get('products', []):
                if not isinstance(product, dict):
                    continue
                pid = product.get('id')
                if isinstance(pid, (str, int)) and not isinstance(pid, bool) and str(pid):
                    catalog[str(pid)] = product
            for child in node.get('categories', []):
                collect(child)

        for category in categories:
            if not isinstance(category, dict):
                raise ValueError('Invalid category')
            for sub in category.get('categories', []):
                if not isinstance(sub, dict) or not isinstance(sub.get('id'), (str, int)):
                    raise ValueError('Invalid category id')
                collect(self._get(f"/categories/{sub['id']}/"))
        if not catalog:
            raise ValueError('Empty catalog')
        return catalog

    def _snapshot(self):
        with self._lock:
            stale = False
            if self._loaded_at is None or self.clock() - self._loaded_at >= self.ttl_seconds:
                try:
                    catalog = self._fetch()
                    loaded_at = self.clock()
                except (requests.RequestException, OSError, ValueError, TypeError, AttributeError, KeyError):
                    if self._catalog is None:
                        raise RuntimeError('Mercadona catalog unavailable') from None
                    warnings.warn('Using stale Mercadona catalog after refresh failure', RuntimeWarning,
                                  stacklevel=3)
                    stale = True
                else:
                    self._catalog, self._loaded_at = catalog, loaded_at
            assert self._catalog is not None and self._loaded_at is not None
            return self._catalog, datetime.fromtimestamp(self._loaded_at, timezone.utc), stale

    def _quote(self, pid, product, observed, stale):
        package = package_from_mercadona(product.get('price_instructions'))
        q = package.quantity
        factors = {'g': Decimal('.001'), 'kg': Decimal(1),
                   'ml': Decimal('.001'), 'l': Decimal(1), 'ud': Decimal(1)}
        units: dict[object, Literal['kg', 'l', 'unit']] = {
            'g': 'kg', 'kg': 'kg', 'ml': 'l', 'l': 'l', 'ud': 'unit'}
        amount = q.amount * factors[q.unit] if q.amount is not None and q.unit in factors else None
        normalized = (package.reference_price / factors[package.reference_unit]
                      if package.reference_price is not None and package.reference_unit in factors else None)
        reasons = list(package.ambiguity)
        name = product.get('display_name')
        if (not isinstance(name, str) or not name or package.package_price is None
                or amount is None or normalized is None
                or units.get(q.unit) != units.get(package.reference_unit)
                or any(r in reasons for r in ('invalid_metadata', 'unknown_unit', 'invalid_amount'))):
            reasons.append('unknown')
        if units.get(q.unit) != units.get(package.reference_unit):
            normalized = None
        if stale:
            reasons.append('stale_catalog')
        return PriceQuote(pid, name if isinstance(name, str) else '', package.package_price,
                          'EUR', amount, units.get(q.unit), normalized, observed, observed,
                          RetailContext(self.id, self.warehouse, 'EUR'), self._provenance,
                          uncertainty_reasons=tuple(dict.fromkeys(reasons)))

    def get_quote(self, product_id: str, store: RetailContext) -> PriceQuote:
        self._validate(store)
        catalog, observed, stale = self._snapshot()
        if product_id not in catalog:
            raise LookupError('Mercadona product not found')
        return self._quote(product_id, catalog[product_id], observed, stale)

    def search_products(self, query: ProductQuery, store: RetailContext) -> list[Product]:
        self._validate(store)
        if not isinstance(query.query, str) or not 1 <= query.limit <= 50:
            raise ValueError('Invalid product query')
        text = _mercadona_cost_query(query.query)
        if not text:
            return []
        catalog, observed, stale = self._snapshot()
        scored = []
        for pid, product in catalog.items():
            name = product.get('display_name')
            if not isinstance(name, str):
                continue
            score = _mercadona_cost_score(_mercadona_normalize(name), text)
            if score >= 0:
                quote = self._quote(pid, product, observed, stale)
                scored.append((score, Product(pid, name, self._provenance, observed,
                                               quote.uncertainty_reasons)))
        scored.sort(key=lambda row: (-row[0], row[1].name, row[1].id))
        return [product for _, product in scored[:query.limit]]
