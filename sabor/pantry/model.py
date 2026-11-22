"""Pantry item model and the food key used to match items (#144)."""
import unicodedata
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from sabor.pricing.quantities import parse_text, to_base

BaseUnit = Literal['g', 'ml', 'ud']
SOURCES = ('manual', 'receipt', 'cooked', 'migration')


@dataclass(frozen=True)
class PantryItem:
    id: str
    name: str
    food: str
    food_id: str | None
    quantity: Decimal | None
    unit: BaseUnit | None
    expires_on: str | None
    source: str
    note: str | None
    updated_at: str


def food_key(name: str) -> str:
    """Lowercase, accent-free and single-spaced: «Limón» and «limon» are the same food."""
    text = unicodedata.normalize('NFKD', name)
    text = ''.join(c for c in text if not unicodedata.combining(c))
    return ' '.join(text.lower().split())


def to_base_quantity(amount: object, unit: object) -> tuple[Decimal, BaseUnit] | None:
    """«1 kg» -> (1000, 'g') with the shared quantity model; None when the unit is unknown."""
    if not isinstance(unit, str) or not unit.strip():
        return None
    q = to_base(parse_text(f'{format_amount(amount)} {unit.strip()}'))
    if q.amount is None or q.unit not in ('g', 'ml', 'ud'):
        return None
    return q.amount, q.unit


def format_amount(value: object) -> str:
    """Decimal text without exponent or trailing zeros (1000, 0.5)."""
    if isinstance(value, Decimal):
        text = format(value.normalize(), 'f')
        return text
    return str(value).strip().replace(',', '.')
