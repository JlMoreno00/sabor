"""Read-only pricing port. Decimal operands are rounded only at presentation."""
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal, Protocol


@dataclass(frozen=True)
class RetailContext:
    provider: str
    location_id: str | None = None
    currency: str | None = None


@dataclass(frozen=True)
class ProductQuery:
    query: str
    limit: int = 10
    # Whether a leading number in `query` is an amount. None lets the provider read it
    # ('2 huevos' is two eggs); the estimate engine, which strips amounts itself, sends
    # False so a kept name number ('4 tomates' for 300 g) is never re-read as one (#49).
    lead_is_amount: bool | None = None


@dataclass(frozen=True)
class Product:
    id: str
    name: str
    provenance: str
    observed_at: datetime | None = None
    uncertainty_reasons: tuple[str, ...] = ()
    food: bool | None = None


@dataclass(frozen=True)
class PriceQuote:
    product_id: str
    product_name: str
    package_price: Decimal | None
    currency: str
    package_quantity: Decimal | None
    package_unit: Literal['kg', 'l', 'unit'] | None
    normalized_unit_price: Decimal | None
    observed_at: datetime | None
    fetched_at: datetime
    location: RetailContext
    provenance: str
    availability: bool | None = None
    uncertainty_reasons: tuple[str, ...] = ()
    food: bool | None = None


class PricingProvider(Protocol):
    id: str
    operations: frozenset[str]

    def search_products(self, query: ProductQuery, store: RetailContext) -> list[Product]: ...
    def get_quote(self, product_id: str, store: RetailContext) -> PriceQuote: ...
