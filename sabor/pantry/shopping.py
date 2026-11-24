"""What the structured pantry covers on a shopping list (#144, José's decision on question 1).

An item the pantry covers in full is reported `covered` (the tool marks it as bought, so it
stays on the list, crossed out); one covered in part is `partial`, with the amount still
`missing`. Nothing is guessed: an item without a measurable amount, a pantry item with an
unknown amount or in another dimension is only reported and never marked.
"""
from collections.abc import Iterable
from decimal import Decimal
from typing import Any

from sabor.pricing.quantities import from_structured, ingredient_name, parse_text, to_base
from sabor.store import ShoppingItem

from .consume import _same_food
from .model import PantryItem, food_key, format_amount


def _need(item: ShoppingItem, name: str):
    """(amount, base unit) the list item asks for, or None when it has no measurable amount."""
    text = item.note or item.display or ''
    unit = item.unit.name if item.unit and item.unit.name else None
    if item.food and item.food.name and item.quantity:
        # Structured item (Mealie, recipe lines): a bare count is pieces.
        q = to_base(from_structured(item.quantity, unit or 'ud', name, text))
    else:
        # Free-text note («300 g arroz», «6 huevos»): read its own leading amount.
        q = to_base(parse_text(text))
    if q.amount is None or q.unit not in ('g', 'ml', 'ud'):
        return None
    return q.amount, q.unit


def cover_items(items: Iterable[ShoppingItem], pantry: list[PantryItem]) -> list[dict[str, Any]]:
    """One row per list item: `covered`, `partial` (with `missing`), `not_in_pantry`,
    `not_measured`, `pantry_amount_unknown` or `incompatible_unit`."""
    rows: list[dict[str, Any]] = []
    for item in items:
        text = item.note or item.display or ''
        name = item.food.name if item.food and item.food.name else ingredient_name(text)
        row: dict[str, Any] = {'item': item.id, 'name': food_key(name) if name else text}
        rows.append(row)
        matching = [p for p in pantry if name and _same_food(p.food, food_key(name))]
        if not matching:
            row['status'] = 'not_in_pantry'
            continue
        need = _need(item, name)
        if need is None:
            row['status'] = 'not_measured'
            continue
        amount, unit = need
        if any(p.quantity is None for p in matching) and not any(p.unit == unit for p in matching):
            row['status'] = 'pantry_amount_unknown'
            continue
        comparable = [p for p in matching if p.unit == unit and p.quantity is not None]
        if not comparable:
            row['status'] = 'incompatible_unit'
            continue
        stock = sum((p.quantity or Decimal(0) for p in comparable), Decimal(0))
        if stock >= amount:
            row['status'] = 'covered'
        else:
            row.update(status='partial', missing={'quantity': format_amount(amount - stock), 'unit': unit})
    return rows
