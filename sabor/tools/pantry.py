"""Structured pantry tools (#144): read, edit and fill from supermarket receipts.

Writes follow Sabor's preview-then-confirm rule. A call without confirm=True writes nothing
and returns the planned effects plus a preview_digest; the confirmed call must pass that
digest, and a pantry that changed in between is rejected as a conflict. Receipts are read
by the client agent; Sabor only normalizes the lines and calls no AI provider.
"""

from datetime import date
from decimal import Decimal
from typing import Any

from sabor.core.envelope import fail, ok
from sabor.core.toolsets import current_context, tool
from sabor.pantry.consume import plan_consumption
from sabor.pantry.model import PantryItem, format_amount
from sabor.pantry.receipt import parse_receipt_line
from sabor.pantry.store import PantryConflict, PantryError, PantryStore, pantry_store_for

_MAX_LINES = 200
_MAX_LINE = 500


def _ready(settings) -> bool:
    # SQLite keeps the pantry in its database, Mealie in a local file, Postgres in its schema.
    return settings.store in ("sqlite", "mealie", "postgres")


def _store() -> PantryStore:
    # The instance's own settings decide where its pantry lives (#147 review).
    context = current_context()
    store = pantry_store_for(context.settings, recipe_store=context.store)
    if store is None:
        raise PantryError("This recipe store has no pantry yet.")
    return store


def _item(item: PantryItem) -> dict[str, Any]:
    return {
        "id": item.id,
        "name": item.name,
        "food": item.food,
        "food_id": item.food_id,
        "quantity": format_amount(item.quantity) if item.quantity is not None else None,
        "unit": item.unit,
        "expires_on": item.expires_on,
        "source": item.source,
        "note": item.note,
        "updated_at": item.updated_at,
    }


def _preview_or_apply(
    changes: list[dict],
    confirm: bool,
    preview_digest: str | None,
    extra: dict[str, Any],
    entries: list[str] | None = None,
) -> dict:
    if type(confirm) is not bool:
        return fail("invalid_argument", "confirm must be true or false.")
    try:
        store = _store()
        entries = entries or []
        if not confirm:
            effects, digest = store.preview_with_digest(changes, entries)
            return ok(
                {"preview": True, "effects": effects, "preview_digest": digest, **extra}
            )
        if not isinstance(preview_digest, str) or not preview_digest:
            return fail(
                "invalid_argument",
                "Confirming needs the preview_digest returned by the preview.",
            )
        # Quick check first; apply() checks again inside its write transaction.
        if preview_digest != store.digest(changes, entries):
            raise PantryConflict("stale preview")
        # Nothing to write still needs a fresh preview: a stale one is a conflict above.
        effects = (store.apply(changes, expected_digest=preview_digest, consumed_entries=entries)
                   if changes or entries else [])
        return ok({"preview": False, "effects": effects, **extra})
    except PantryConflict:
        return fail(
            "conflict",
            "The pantry changed since the preview; preview again before confirming.",
        )
    except PantryError as error:
        return fail("invalid_argument", str(error))


@tool("pantry", requires=_ready, uses_context=True)
def get_pantry(
    food: str | None = None, expiring_within_days: int | None = None
) -> dict:
    """List structured pantry items, optionally one food or those expiring within N days."""
    if food is not None and (not isinstance(food, str) or not food.strip()):
        return fail("invalid_argument", "food must be a non-empty text.")
    if expiring_within_days is not None and (
        type(expiring_within_days) is not int or not 0 <= expiring_within_days <= 3650
    ):
        return fail(
            "invalid_argument",
            "expiring_within_days must be an integer from 0 to 3650.",
        )
    try:
        items = _store().list_items(
            food=food, expiring_within_days=expiring_within_days
        )
    except PantryError as error:
        return fail("invalid_argument", str(error))
    return ok({"items": [_item(i) for i in items]})


@tool("pantry", requires=_ready, uses_context=True)
def edit_pantry(
    changes: list[dict], confirm: bool = False, preview_digest: str | None = None
) -> dict:
    """Preview pantry changes (add, set, subtract, remove); write only when confirmed with the preview's digest."""
    if not isinstance(changes, list) or not changes:
        return fail("invalid_argument", "changes must be a non-empty list.")
    return _preview_or_apply(changes, confirm, preview_digest, {})


@tool("pantry", requires=_ready, uses_context=True)
def add_pantry_from_receipt(
    lines: list[str], confirm: bool = False, preview_digest: str | None = None
) -> dict:
    """Preview pantry additions from supermarket receipt lines you read; write only when confirmed with the preview's digest."""
    if (
        not isinstance(lines, list)
        or not 1 <= len(lines) <= _MAX_LINES
        or not all(isinstance(line, str) and len(line) <= _MAX_LINE for line in lines)
    ):
        return fail(
            "invalid_argument",
            f"lines must be a list of 1 to {_MAX_LINES} texts "
            f"of at most {_MAX_LINE} characters.",
        )
    items, ignored, changes = [], [], []
    for line in lines:
        parsed = parse_receipt_line(line)
        if parsed.ignored:
            ignored.append({"line": line, "reason": parsed.ignored})
            continue
        quantity = (
            format_amount(parsed.quantity) if parsed.quantity is not None else None
        )
        items.append(
            {
                "line": line,
                "name": parsed.name,
                "quantity": quantity,
                "unit": parsed.unit,
                "uncertain": list(parsed.uncertain),
            }
        )
        change = {"op": "add", "name": parsed.name, "source": "receipt"}
        if quantity is not None:
            change |= {"quantity": quantity, "unit": parsed.unit}
        changes.append(change)
    return _preview_or_apply(
        changes, confirm, preview_digest, {"items": items, "ignored": ignored}
    )


def _entry_id(entry) -> str | None:
    """The plan entry's id when it is a non-empty text, else None."""
    entry_id = getattr(entry, "id", None)
    return entry_id if isinstance(entry_id, str) and entry_id.strip() else None


def _consume_source(recipe_slugs, mealplan_start, mealplan_end, servings_multiplier) -> str | None:
    """The validation error, or None for exactly one valid source and multiplier."""
    if type(servings_multiplier) not in (int, float) or not 0 < servings_multiplier <= 20:
        return "servings_multiplier must be greater than 0 and at most 20."
    range_selected = mealplan_start is not None or mealplan_end is not None
    if (recipe_slugs is not None) == range_selected:
        return "Pass exactly one source: recipe_slugs, or mealplan_start and mealplan_end."
    if recipe_slugs is not None:
        if (not isinstance(recipe_slugs, list) or not 1 <= len(recipe_slugs) <= 50
                or any(not isinstance(x, str) or not x.strip() for x in recipe_slugs)):
            return "recipe_slugs must be a list of 1 to 50 recipe slugs."
        return None
    if not isinstance(mealplan_start, str) or not isinstance(mealplan_end, str):
        return "mealplan_start and mealplan_end must be ISO dates (YYYY-MM-DD)."
    try:
        start, end = date.fromisoformat(mealplan_start), date.fromisoformat(mealplan_end)
    except ValueError:
        return "mealplan_start and mealplan_end must be ISO dates (YYYY-MM-DD)."
    if start.isoformat() != mealplan_start or end.isoformat() != mealplan_end or start > end:
        return "mealplan_start and mealplan_end must be ISO dates with start <= end."
    if (end - start).days > 31:
        return "The meal plan range can cover at most 32 days."
    return None


@tool("pantry", requires=_ready, uses_context=True)
def consume_from_pantry(recipe_slugs: list[str] | None = None, mealplan_start: str | None = None,
                        mealplan_end: str | None = None, servings_multiplier: float = 1.0,
                        confirm: bool = False, preview_digest: str | None = None) -> dict:
    """Preview subtracting cooked recipes or planned meals from the pantry; write only when confirmed with the preview's digest."""
    from sabor.store import DateRange, StoreError, store_error_envelope

    error = _consume_source(recipe_slugs, mealplan_start, mealplan_end, servings_multiplier)
    if error:
        return fail("invalid_argument", error)
    ctx = current_context()
    occurrences, skipped, entries = [], [], []
    try:
        if recipe_slugs is not None:
            occurrences = [(None, slug) for slug in recipe_slugs]
        else:
            assert isinstance(mealplan_start, str) and isinstance(mealplan_end, str)
            planned = list(ctx.store.get_mealplan(DateRange(mealplan_start, mealplan_end)))
            done = _store().consumed_entries([e.id for e in planned if _entry_id(e)])
            for entry in planned:
                slug = entry.recipe_slug or entry.recipe_id
                if not slug:
                    skipped.append({"entry": entry.id, "reason": "text_only_entry"})
                elif not _entry_id(entry):
                    # Without an id the entry could not be remembered and would be subtracted
                    # again on every call (Mealie may return one); it is skipped instead.
                    skipped.append({"entry": entry.id, "reason": "missing_entry_id"})
                elif entry.id in done:
                    skipped.append({"entry": entry.id, "reason": "already_consumed"})
                else:
                    occurrences.append((entry.id, slug))
        ingredients, recipes = [], []
        for entry_id, slug in occurrences:
            recipe = ctx.store.get_recipe(slug)
            ingredients.extend(recipe.recipeIngredient or ())
            recipes.append({"slug": recipe.slug, "name": recipe.name, "entry": entry_id})
            if entry_id:
                entries.append(entry_id)
        changes, report = plan_consumption(
            ingredients, Decimal(str(servings_multiplier)), _store().list_items())
    except PantryError as exc:
        return fail("invalid_argument", str(exc))
    except StoreError as exc:
        return store_error_envelope(exc)
    extra = {"recipes": recipes, "ingredients": report, "skipped": skipped}
    return _preview_or_apply(changes, confirm, preview_digest, extra, entries)

