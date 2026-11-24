"""Plan which pantry amounts a cooked recipe uses (#144 phase 2).

Each recipe ingredient is matched to pantry items of the same food and the same base unit.
Its amount is scaled by the servings multiplier and turned into `subtract` changes for the
pantry store. Nothing is guessed: an ingredient without a measurable amount, in another
dimension than the pantry item (grams of milk kept in millilitres), not in the pantry, or
matching an item whose amount is unknown is only reported and leaves the pantry unchanged.
"""

from collections.abc import Iterable
from decimal import Decimal
from typing import Any

from sabor.pricing.quantities import from_structured, ingredient_name, to_base
from sabor.store import Ingredient

from .model import PantryItem, food_key, format_amount


def _forms(food: str) -> set[str]:
    """«huevos» and «huevo», «tomates» and «tomate», «nueces» and «nuez» are the same food."""
    forms = {food}
    for suffix in ("es", "s"):
        if food.endswith(suffix) and len(food) > len(suffix) + 1:
            forms.add(food[: -len(suffix)])
    if food.endswith("ces") and len(food) > 4:
        forms.add(food[:-3] + "z")  # «nueces» -> «nuez», «perdices» -> «perdiz»
    return forms


def _same_food(a: str, b: str) -> bool:
    return bool(_forms(a) & _forms(b))


def _text(ingredient: Ingredient) -> str:
    return ingredient.originalText or ingredient.display or ingredient.note or ""


def _base_amount(ingredient: Ingredient, name: str):
    """(amount, base unit) of the ingredient, or None when it has no measurable amount."""
    unit = ingredient.unit.name if ingredient.unit and ingredient.unit.name else None
    if ingredient.quantity and unit is None:
        unit = "ud"  # «2 huevos»: a bare count is pieces.
    q = to_base(from_structured(ingredient.quantity, unit, name, _text(ingredient)))
    if q.amount is None or q.unit not in ("g", "ml", "ud"):
        return None
    return q.amount, q.unit


def plan_consumption(
    ingredients: Iterable[Ingredient], multiplier: Decimal, items: list[PantryItem]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Subtract changes for the measured ingredients found in the pantry, plus a report.

    The report has one row per ingredient with `status`: `subtract`, `not_measured`,
    `not_in_pantry`, `incompatible_unit` or `pantry_amount_unknown`.
    """
    # Demand per group of equivalent pantry foods kept in one base unit: «huevo» and «huevos»
    # rows are one stock of eggs.
    needed: dict[tuple[tuple[str, ...], str], Decimal] = {}
    report: list[dict[str, Any]] = []
    for ingredient in ingredients:
        text = _text(ingredient)
        name = (
            ingredient.food.name
            if ingredient.food and ingredient.food.name
            else ingredient_name(text)
        )
        row: dict[str, Any] = {
            "ingredient": text or name,
            "food": food_key(name) if name else None,
        }
        report.append(row)
        if not name:
            row["status"] = "not_measured"
            continue
        food = food_key(name)
        candidates = [i for i in items if _same_food(i.food, food)]
        base = _base_amount(ingredient, name)
        if not candidates:
            row["status"] = "not_in_pantry"
            continue
        if base is None:
            row["status"] = "not_measured"
            continue
        amount, unit = base[0] * multiplier, base[1]
        targets = tuple(sorted({i.food for i in candidates if i.unit == unit}))
        if not targets:
            known = [i for i in candidates if i.unit is not None]
            row["status"] = "incompatible_unit" if known else "pantry_amount_unknown"
            continue
        key = (targets, unit)
        needed[key] = needed.get(key, Decimal(0)) + amount
        row.update(status="subtract", quantity=format_amount(amount), unit=unit)
    return _spread(needed, items), report


def _spread(needed, items: list[PantryItem]) -> list[dict[str, Any]]:
    """Subtract changes that draw each demand from every equivalent food in stock.

    Foods are used in name order; when the stock is not enough, the rest goes to the last
    one, where the pantry store reports it as a shortfall.
    """
    changes: list[dict[str, Any]] = []
    for (foods, unit), demand in needed.items():
        for index, food in enumerate(foods):
            matching = [i for i in items if i.food == food and i.unit == unit]
            stock = sum((i.quantity or Decimal(0) for i in matching), Decimal(0))
            take = demand if index == len(foods) - 1 else min(demand, stock)
            if take > 0:
                changes.append({"op": "subtract", "name": matching[0].name,
                                "quantity": format_amount(take), "unit": unit})
            demand -= take
            if demand <= 0:
                break
    return changes
