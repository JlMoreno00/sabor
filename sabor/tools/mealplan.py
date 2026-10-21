"""Meal planning tools and keyed scheduling locks."""

import threading
from typing import Any
import requests
import sabor.integrations.mealie as _mealie
import sabor.config as config
import sabor.tools.nutrition as nutrition
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


def _schedule(date_str, entry_type, title, recipe_slug, session, base_url, headers):
    """Legacy nutrition bridge: config flag, nutrition services and locks remain global."""
    arguments = {"date_str": date_str, "entry_type": entry_type, "title": title,
                 "recipe_slug": recipe_slug}
    continuation = nutrition._nutrition_continuation("set_mealplan_entry", arguments)
    try:
        payload: dict[str, Any] = {
            "date": date_str,
            "entryType": entry_type,
            "title": title,
            "text": "",
        }
        expected_revision = None
        if recipe_slug:
            if config.SABOR_NUTRITION_ENABLED:
                ensured = nutrition.ensure_recipe_nutrition(recipe_slug, continuation)
                if not ensured.get("ok"):
                    return {**ensured, "scheduled": False, "continuation": continuation}
                expected_revision = ensured.get("revision")
            else:
                rr = session.get(
                    f"{base_url}/api/recipes/{recipe_slug}",
                    headers=headers, timeout=30,
                )
                if 200 <= rr.status_code < 300:
                    payload["recipeId"] = rr.json().get("id")
        key = (date_str, entry_type, recipe_slug or str(payload.get("recipeId") or ""))
        lock = (nutrition._keyed_lock(nutrition._MEALPLAN_LOCKS, key) if recipe_slug else threading.Lock())
        with lock:
            if recipe_slug and config.SABOR_NUTRITION_ENABLED:
                try:
                    lookup = session.get(
                        f"{base_url}/api/households/mealplans",
                        headers=headers,
                        params={"start_date": date_str, "end_date": date_str}, timeout=30,
                    )
                except requests.exceptions.RequestException:
                    return {
                        **nutrition._nutrition_failure(
                            "nutrition_schedule_duplicate_lookup_failed",
                            "Meal-plan duplicate lookup failed.", stage="schedule_duplicate_lookup",
                            continuation=continuation, retryable=True,
                        ),
                        "scheduled": False,
                    }
                if not 200 <= lookup.status_code < 300:
                    return {
                        **nutrition._nutrition_failure(
                            "nutrition_schedule_duplicate_lookup_failed",
                            "Meal-plan duplicate lookup failed.", stage="schedule_duplicate_lookup",
                            continuation=continuation, retryable=True,
                        ),
                        "scheduled": False,
                    }
                recipe = nutrition._nutrition_fetch_recipe(recipe_slug)
                current_revision = nutrition._nutrition_recipe_revision(recipe)
                if current_revision != expected_revision:
                    return {
                        **nutrition._nutrition_failure(
                            "nutrition_revision_conflict",
                            "Recipe changed before meal-plan scheduling.",
                            stage="schedule_revision", continuation=continuation,
                            details={"expectedRevision": expected_revision,
                                     "currentRevision": current_revision},
                        ),
                        "scheduled": False,
                    }
                payload["recipeId"] = recipe.get("id")
                existing = next((entry for entry in (lookup.json() or {}).get("items") or [] if (
                    entry.get("date") == date_str
                    and entry.get("entryType") == entry_type
                    and str((entry.get("recipe") or {}).get("id")) == str(payload["recipeId"])
                )), None)
                if existing:
                    return {
                        "ok": True, "code": "mealplan_entry_existing",
                        "message": "Matching meal-plan entry already exists.",
                        "retryable": False, "scheduled": True, "created": False,
                        "id": existing.get("id"), "date": date_str,
                        "entry_type": entry_type, "title": title,
                        "has_recipe_link": True, "_recipe_id": payload.get("recipeId"),
                    }
            r = session.post(
                f"{base_url}/api/households/mealplans",
                headers=headers, json=payload, timeout=30,
            )
        if not (200 <= r.status_code < 300):
            return {"ok": False, "status": r.status_code, "body": r.text[:500]}
        data = r.json()
        return {
            "ok": True,
            "id": data.get("id"),
            "date": date_str,
            "entry_type": entry_type,
            "title": title,
            "has_recipe_link": "recipeId" in payload,
            "_recipe_id": payload.get("recipeId"),
            "scheduled": True,
            "created": True,
        }
    except Exception as e:
        return {"ok": False, "error": f"set_mealplan_failed: {e}"}

