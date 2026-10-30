"""Instance-owned unofficial catalog adapter; no legacy cache is accessed.

Provenance format: mercadona:<warehouse>:tienda.mercadona.es-catalog.
Product summaries and quotes share the last successful catalog observation.
"""
import json
import math
from contextvars import copy_context
import os
from pathlib import Path
import time
import warnings
from datetime import datetime, timezone
from decimal import Decimal
from threading import RLock, Thread
from typing import Any, Literal

import requests

from sabor.integrations.mercadona import _mercadona_cost_query
from sabor.pricing.matching import covers, processed, same_word, words
from sabor.pricing.provider import PriceQuote, Product, ProductQuery, RetailContext
from sabor.pricing.quantities import package_from_mercadona, parse_text


class CatalogWarming(RuntimeError):
    pass


# Reference denominators observed in the real catalog, mapped to canonical ones:
# a multiplier applied to reference_price and the canonical reference_format.
_REFERENCE_DENOMINATORS = {'dz': (Decimal(1) / 12, 'ud'), 'dc': (Decimal(1) / 12, 'ud'),
                           '100 ml': (Decimal(10), 'l'), '100 g': (Decimal(10), 'kg')}


def _normalize_reference(instructions):
    """Rewrite dozen and per-100 reference prices to per-unit, per-l or per-kg."""
    if not isinstance(instructions, dict):
        return instructions
    mapping = _REFERENCE_DENOMINATORS.get(str(instructions.get('reference_format') or '').lower())
    if mapping is None:
        return instructions
    try:
        price = Decimal(str(instructions['reference_price'])) * mapping[0]
    except (KeyError, ArithmeticError, ValueError):
        return instructions
    if not price.is_finite():
        return instructions
    # Six decimals keep sub-cent unit prices exact without 28-digit tails.
    price = price.quantize(Decimal('0.000001')).normalize()
    return dict(instructions, reference_format=mapping[1], reference_price=str(price))


class MercadonaPricing:
    id = 'mercadona'
    operations = frozenset({'search_products', 'get_quote'})

    # The catalog is unofficial and rate-limited: a few blocked subcategories must
    # not discard the whole catalog. Above this failure ratio it is unavailable.
    MAX_FAILED_SUBCATEGORY_RATIO = Decimal('0.10')

    def __init__(self, warehouse='mad1', session=None, ttl_seconds=86400, clock=time.time,
                 retry_delay=2.0, request_delay=0.5, sleep=time.sleep,
                 cache_path=None, max_consecutive_blocks=3):
        self.sleep = sleep
        self.cache_path = Path(cache_path) if cache_path else None
        self.max_consecutive_blocks = max_consecutive_blocks
        self._thread = None
        self._next_refresh = 0
        self.warehouse = warehouse
        self.retry_delay = retry_delay
        self.request_delay = request_delay
        self._partial = False
        self.session = session if session is not None else requests.Session()
        self.ttl_seconds = ttl_seconds
        self.clock = clock
        self._catalog: dict[str, dict[str, Any]] | None = None
        self._loaded_at: float | None = None
        self._lock = RLock()
        self._provenance = f'mercadona:{warehouse}:tienda.mercadona.es-catalog'
        self._load_cache()

    @staticmethod
    def _food(product):
        categories = product.get('categories', [])
        if not categories:
            return None
        return not any(str(c.get('id')) in {'20', '21', '22', '23', '25', '26'}
                       for c in categories if c.get('level') == 0)

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

        subcategories = []
        for category in categories:
            if not isinstance(category, dict):
                raise ValueError('Invalid category')
            for sub in category.get('categories', []):
                if not isinstance(sub, dict) or not isinstance(sub.get('id'), (str, int)):
                    raise ValueError('Invalid category id')
                subcategories.append(sub['id'])
        failed = consecutive = 0
        for index, sub_id in enumerate(subcategories):
            if index and self.request_delay:
                self.sleep(self.request_delay)
            try:
                collect(self._get_subcategory(sub_id))
                consecutive = 0
            except (requests.RequestException, OSError, ValueError):
                failed += 1
                consecutive = consecutive + 1 if self._blocked else 0
                if consecutive > self.max_consecutive_blocks:
                    raise ValueError('Catalog crawl blocked')
        if subcategories and Decimal(failed) / Decimal(len(subcategories)) > self.MAX_FAILED_SUBCATEGORY_RATIO:
            raise ValueError('Too many unavailable catalog subcategories')
        if not catalog:
            raise ValueError('Empty catalog')
        self._fetched_partial = failed > 0
        return catalog

    def _get_subcategory(self, sub_id):
        self._blocked = False
        for attempt in range(3):
            try:
                return self._get(f'/categories/{sub_id}/')
            except (requests.HTTPError, requests.Timeout) as exc:
                self._blocked = (isinstance(exc, requests.Timeout) or
                                 getattr(exc.response, 'status_code', None) in (403, 429))
                if not self._blocked or attempt == 2:
                    raise
                self.sleep(self.retry_delay * 2 ** attempt)

    def _load_cache(self):
        if self.cache_path is None:
            return
        try:
            data = json.loads(self.cache_path.read_text())
            stamp, catalog = data['loaded_at'], data['catalog']
            if (data['warehouse'] != self.warehouse or type(stamp) not in (int, float)
                    or not math.isfinite(stamp) or not 0 <= stamp <= self.clock()
                    or type(data.get('partial')) is not bool
                    or not isinstance(catalog, dict) or not catalog
                    or any(not isinstance(k, str) or not isinstance(v, dict)
                           or str(v.get('id')) != k for k, v in catalog.items())):
                return
            self._catalog, self._loaded_at = catalog, stamp
            self._partial = data['partial']
        except (OSError, ValueError, KeyError, TypeError):
            pass

    def _save_cache(self):
        if self.cache_path is None:
            return
        temporary = self.cache_path.with_suffix('.json.tmp')
        try:
            payload = json.dumps(dict(warehouse=self.warehouse,
                loaded_at=self._loaded_at, partial=self._partial, catalog=self._catalog))
            try:
                temporary.write_text(payload)
            except FileNotFoundError:
                self.cache_path.parent.mkdir(parents=True, exist_ok=True)
                temporary.write_text(payload)
            os.replace(temporary, self.cache_path)
        except OSError:
            pass

    def start_background_refresh(self):
        with self._lock:
            if ((self._thread is not None and self._thread.is_alive())
                    or self.clock() < self._next_refresh
                    or (self._loaded_at is not None and
                        self.clock() - self._loaded_at < self.ttl_seconds)):
                return
            self._thread = Thread(target=copy_context().run, args=(self._background_refresh,), daemon=True)
            self._thread.start()

    def _background_refresh(self):
        try:
            self._refresh()
        except RuntimeError:
            pass

    def _refresh(self):
        if self._loaded_at is not None and self.clock() - self._loaded_at < self.ttl_seconds:
            return
        try:
            catalog = self._fetch()
        except (requests.RequestException, OSError, ValueError, TypeError, AttributeError, KeyError):
            with self._lock:
                self._next_refresh = self.clock() + 120
            if self._catalog is None:
                raise RuntimeError('Mercadona catalog unavailable') from None
            warnings.warn('Using stale Mercadona catalog after refresh failure', RuntimeWarning)
            return
        with self._lock:
            self._catalog, self._loaded_at = catalog, self.clock()
            self._partial = getattr(self, '_fetched_partial', False)
            self._save_cache()

    def _snapshot(self):
        self.start_background_refresh()
        with self._lock:
            if self._catalog is None or self._loaded_at is None:
                raise CatalogWarming('Mercadona catalog is loading; retry in a few minutes')
            stale = self.clock() - self._loaded_at >= self.ttl_seconds
            return self._catalog, datetime.fromtimestamp(self._loaded_at, timezone.utc), stale

    def _quote(self, pid, product, observed, stale):
        instructions = _normalize_reference(product.get('price_instructions'))
        package = package_from_mercadona(instructions)
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
        if (normalized is None and amount is not None and amount > 0 and package.package_price is not None
                and units.get(q.unit) is not None):
            # Unknown reference denominator: derive from the package itself.
            normalized = package.package_price / amount
            reasons = [r for r in reasons if r not in ('unknown', 'unknown_unit')]
            reasons.append('derived_unit_price')
        if stale:
            reasons.append('stale_catalog')
        if self._partial:
            reasons.append('catalog_partial')
        return PriceQuote(pid, name if isinstance(name, str) else '', package.package_price,
                          'EUR', amount, units.get(q.unit), normalized, observed, observed,
                          RetailContext(self.id, self.warehouse, 'EUR'), self._provenance,
                          uncertainty_reasons=tuple(dict.fromkeys(reasons)), food=self._food(product))

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
        # Numbers of the name that the cost query dropped ('4' in '4 quesos') rank
        # first; a leading amount the parser reads as one ('500 g', '2 huevos') does not.
        numbers = (set() if parse_text(query.query).dimension else
                   {w for w in words(query.query) if w[0].isdigit()} - words(text))
        catalog, observed, stale = self._snapshot()
        scored = []
        for pid, product in catalog.items():
            name = product.get('display_name')
            if not isinstance(name, str):
                continue
            if covers(text, name) and self._food(product) is not False:
                quote = self._quote(pid, product, observed, stale)
                wanted = words(text)
                extra = sum(1 for word in words(name) if not any(same_word(word, w) for w in wanted))
                rank = (len(numbers - words(name)), processed(name, text), extra)
                scored.append((rank, Product(pid, name, self._provenance, observed,
                                             quote.uncertainty_reasons, quote.food)))
        # Food-only browsing; name numbers, unprocessed, closest name, food, name and id.
        scored.sort(key=lambda row: (*row[0], row[1].food is not True, row[1].name, row[1].id))
        return [product for _, product in scored[:query.limit]]
