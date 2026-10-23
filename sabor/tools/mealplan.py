"""Meal planning tools and keyed scheduling locks."""

from typing import Any
import sabor.integrations.mealie as _mealie
from sabor.core.toolsets import current_context, tool
from sabor.store import DateRange, MealEntryDraft, StoreError, store_error_envelope

mealie = _mealie  # Retained direct-call patch site; never used by registered transport.


@tool("core", uses_context=True)
def get_mealplan_week(start_date: str, end_date: str) -> dict[str, Any]:
    """Obtiene el plan de comidas para un rango de fechas (YYYY-MM-DD)."""
    return _calendar(current_context(), "read", DateRange(start_date, end_date))


@tool("core", uses_context=True)
def set_mealplan_entry(
    date_str: str, entry_type: str, title: str, recipe_slug: str | None = None
) -> dict[str, Any]:
    """Anade una entrada al calendario de Mealie. entry_type: lunch|dinner|breakfast|side."""
    return _calendar(current_context(), "set", MealEntryDraft(date_str, entry_type, title, recipe_slug))


@tool("core", uses_context=True)
def delete_mealplan_entry(entry_id: str) -> dict[str, Any]:
    """Elimina una entrada del plan de comidas por su ID."""
    return _calendar(current_context(), "delete", entry_id)


@tool("core", uses_context=True)
def clear_mealplan_week(start_date: str, end_date: str, confirm: bool = False) -> dict[str, Any]:
    """Preview entries removed in a date range; pass confirm=True to clear them."""
    return _calendar(current_context(), "clear", DateRange(start_date, end_date), confirm)


def _calendar(ctx, action, argument, confirm=False):
    if ctx.store is None:
        return store_error_envelope(StoreError("provider_unconfigured", "mealie"))
    try:
        if action == "set":
            entry = ctx.store.set_mealplan_entry(argument)
            result = getattr(entry, "result", None)
            return result if result is not None else {
                "ok": True, "id": entry.id, "date": entry.date,
                "entry_type": entry.entry_type, "title": entry.title,
                "has_recipe_link": entry.recipe_id is not None, "scheduled": True, "created": True}
        if action == "delete":
            status = ctx.store.delete_mealplan_entry(argument)
            return {"ok": True, "status": status or 200, "entry_id": argument}
        entries = ctx.store.get_mealplan(argument)
        if action == "read":
            rows = [{"id": e.id, "date": e.date, "entryType": e.entry_type,
                     "title": e.title, "recipe_slug": e.recipe_slug, "recipe_id": e.recipe_id}
                    for e in entries]
            return {"ok": True, "total": len(rows), "entries": rows}
        date_range = f"{argument.start_date} -> {argument.end_date}"
        if not confirm:
            rows = [getattr(e, "raw", None) or {
                "id": e.id, "date": e.date, "entryType": e.entry_type, "title": e.title}
                for e in entries]
            return {"ok": True, "preview": True, "items_to_remove": rows,
                    "count": len(rows), "range": date_range}
        if not entries:
            return {"ok": True, "deleted": 0, "message": "No habia entradas en ese rango"}
        deleted, failures = 0, []
        for entry in entries:
            if not entry.id:
                continue
            try:
                ctx.store.delete_mealplan_entry(entry.id)
                deleted += 1
            except StoreError as exc:
                failures.append({"id": entry.id, "status": getattr(exc, "status", None)}
                                if hasattr(exc, "status") else
                                {"id": entry.id, "error": store_error_envelope(exc)["error"]})
        return {"ok": True, "deleted": deleted, "errors": failures, "range": date_range}
    except StoreError as exc:
        return store_error_envelope(exc)



