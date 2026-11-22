"""Structured pantry tools (#144): read, edit and fill from supermarket receipts.

Writes follow Sabor's preview-then-confirm rule. A call without confirm=True writes nothing
and returns the planned effects plus a preview_digest; the confirmed call must pass that
digest, and a pantry that changed in between is rejected as a conflict. Receipts are read
by the client agent; Sabor only normalizes the lines and calls no AI provider.
"""

import hashlib
import json
import os
from typing import Any

from sabor.core.envelope import fail, ok
from sabor.core.toolsets import current_context, tool
from sabor.pantry.model import PantryItem, format_amount
from sabor.pantry.receipt import parse_receipt_line
from sabor.pantry.store import PantryError, PantryStore, pantry_store_for

_MAX_LINES = 200
_MAX_LINE = 500


def _ready(settings) -> bool:
    # SQLite keeps the pantry in its database, Mealie in a local file; Postgres has none yet.
    return settings.store in ("sqlite", "mealie")


def _store() -> PantryStore:
    store = pantry_store_for(current_context().settings, os.environ)
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


def _digest(store: PantryStore, changes: list[dict]) -> str:
    """Binds a preview to these changes and to the pantry as it was when previewed."""
    snapshot = [
        (
            i.id,
            i.food,
            i.quantity is not None and format_amount(i.quantity),
            i.unit,
            i.expires_on,
            i.updated_at,
        )
        for i in store.list_items()
    ]
    payload = json.dumps(
        [changes, snapshot], sort_keys=True, default=str, ensure_ascii=False
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def _preview_or_apply(
    changes: list[dict],
    confirm: bool,
    preview_digest: str | None,
    extra: dict[str, Any],
) -> dict:
    if type(confirm) is not bool:
        return fail("invalid_argument", "confirm must be true or false.")
    try:
        store = _store()
        if not changes:
            return ok({"preview": not confirm, "effects": [], **extra})
        digest = _digest(store, changes)
        if not confirm:
            effects = store.preview(changes)
            return ok(
                {"preview": True, "effects": effects, "preview_digest": digest, **extra}
            )
        if not isinstance(preview_digest, str) or not preview_digest:
            return fail(
                "invalid_argument",
                "Confirming needs the preview_digest returned by the preview.",
            )
        if preview_digest != digest:
            return fail(
                "conflict",
                "The pantry changed since the preview; preview again before confirming.",
            )
        return ok({"preview": False, "effects": store.apply(changes), **extra})
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
