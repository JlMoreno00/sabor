"""Mealie-only scheduling transport and nutrition gate."""

import threading
from typing import Any
import requests
from sabor.core.runtime import config
import sabor.tools.nutrition as nutrition


def _schedule(date_str, entry_type, title, recipe_slug, session, base_url, headers):
    """Schedule using bound instance nutrition state, or direct-call defaults."""
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
        lock = (nutrition._keyed_lock(nutrition._runtime_state("mealplan_locks", nutrition._MEALPLAN_LOCKS), key) if recipe_slug else threading.Lock())
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
