"""Meal planning tools and keyed scheduling locks."""

import threading
from typing import Any
import requests
import sabor.config as config
import sabor.integrations.mealie as mealie
import sabor.tools.nutrition as nutrition
from sabor.server import mcp

# MEALIE — Meal Planner / Calendar
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@mcp.tool()
def get_mealplan_week(start_date: str, end_date: str) -> dict[str, Any]:
    """Obtiene el plan de comidas para un rango de fechas (YYYY-MM-DD)."""
    if err := mealie._check_mealie():
        return err
    try:
        r = requests.get(
            f"{config.MEALIE_BASE_URL}/api/households/mealplans",
            headers=mealie._mealie_headers(),
            params={"start_date": start_date, "end_date": end_date},
            timeout=30,
        )
        if not (200 <= r.status_code < 300):
            return {"ok": False, "status": r.status_code, "body": r.text[:1000]}
        entries = r.json().get("items") or []
        result = []
        for e in entries:
            recipe = e.get("recipe") or {}
            result.append(
                {
                    "id": e.get("id"),
                    "date": e.get("date"),
                    "entryType": e.get("entryType"),
                    "title": e.get("title") or recipe.get("name", ""),
                    "recipe_slug": recipe.get("slug"),
                    "recipe_id": recipe.get("id"),
                }
            )
        return {"ok": True, "total": len(result), "entries": result}
    except Exception as e:
        return {"ok": False, "error": f"get_mealplan_failed: {e}"}


@mcp.tool()
def set_mealplan_entry(
    date_str: str, entry_type: str, title: str, recipe_slug: str | None = None
) -> dict[str, Any]:
    """Anade una entrada al calendario de Mealie. entry_type: lunch|dinner|breakfast|side."""
    if err := mealie._check_mealie():
        return err
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
                rr = requests.get(
                    f"{config.MEALIE_BASE_URL}/api/recipes/{recipe_slug}",
                    headers=mealie._mealie_headers(), timeout=30,
                )
                if 200 <= rr.status_code < 300:
                    payload["recipeId"] = rr.json().get("id")
        key = (date_str, entry_type, recipe_slug or str(payload.get("recipeId") or ""))
        lock = (nutrition._keyed_lock(nutrition._MEALPLAN_LOCKS, key) if recipe_slug else threading.Lock())
        with lock:
            if recipe_slug and config.SABOR_NUTRITION_ENABLED:
                try:
                    lookup = requests.get(
                        f"{config.MEALIE_BASE_URL}/api/households/mealplans",
                        headers=mealie._mealie_headers(),
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
                        "has_recipe_link": True,
                    }
            r = requests.post(
                f"{config.MEALIE_BASE_URL}/api/households/mealplans",
                headers=mealie._mealie_headers(), json=payload, timeout=30,
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
            "scheduled": True,
            "created": True,
        }
    except Exception as e:
        return {"ok": False, "error": f"set_mealplan_failed: {e}"}


@mcp.tool()
def delete_mealplan_entry(entry_id: str) -> dict[str, Any]:
    """Elimina una entrada del plan de comidas por su ID."""
    if err := mealie._check_mealie():
        return err
    try:
        r = requests.delete(
            f"{config.MEALIE_BASE_URL}/api/households/mealplans/{entry_id}",
            headers=mealie._mealie_headers(),
            timeout=30,
        )
        return {
            "ok": 200 <= r.status_code < 300,
            "status": r.status_code,
            "entry_id": entry_id,
        }
    except Exception as e:
        return {"ok": False, "error": f"delete_mealplan_failed: {e}"}


@mcp.tool()
def clear_mealplan_week(start_date: str, end_date: str, confirm: bool = False) -> dict[str, Any]:
    """Preview entries removed in a date range; pass confirm=True to clear them."""
    if err := mealie._check_mealie():
        return err
    try:
        r = requests.get(
            f"{config.MEALIE_BASE_URL}/api/households/mealplans",
            headers=mealie._mealie_headers(),
            params={"start_date": start_date, "end_date": end_date},
            timeout=30,
        )
        if not (200 <= r.status_code < 300):
            return {"ok": False, "status": r.status_code, "body": r.text[:500]}
        entries = r.json().get("items") or []
        if not confirm:
            return {"ok": True, "preview": True, "items_to_remove": entries,
                    "count": len(entries), "range": f"{start_date} -> {end_date}"}
        if not entries:
            return {
                "ok": True,
                "deleted": 0,
                "message": "No habia entradas en ese rango",
            }
        deleted, errors = 0, []
        for entry in entries:
            eid = entry.get("id")
            if not eid:
                continue
            rd = requests.delete(
                f"{config.MEALIE_BASE_URL}/api/households/mealplans/{eid}",
                headers=mealie._mealie_headers(),
                timeout=30,
            )
            if 200 <= rd.status_code < 300:
                deleted += 1
            else:
                errors.append({"id": eid, "status": rd.status_code})
        return {
            "ok": True,
            "deleted": deleted,
            "errors": errors,
            "range": f"{start_date} -> {end_date}",
        }
    except Exception as e:
        return {"ok": False, "error": f"clear_mealplan_failed: {e}"}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
