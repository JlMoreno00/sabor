"""Recipe comments and cooking logs."""

from typing import Any
import requests
from sabor.core.runtime import config
import sabor.integrations.mealie as mealie
from sabor.core.toolsets import tool

# MEALIE — Comments & Cooking Log
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@tool("core")
def add_recipe_comment(slug: str, text: str) -> dict[str, Any]:
    """Anade un comentario a una receta en Mealie (rating, notas, feedback)."""
    if err := mealie._check_mealie():
        return err
    try:
        rr = requests.get(
            f"{config.MEALIE_BASE_URL}/api/recipes/{slug}",
            headers=mealie._mealie_headers(),
            timeout=30,
        )
        if not (200 <= rr.status_code < 300):
            return {
                "ok": False,
                "status": rr.status_code,
                "error": f"recipe_not_found: {slug}",
            }
        recipe_id = rr.json().get("id")
        if not recipe_id:
            return {"ok": False, "error": f"recipe_id_missing_for: {slug}"}
        r = requests.post(
            f"{config.MEALIE_BASE_URL}/api/comments",
            headers=mealie._mealie_headers(),
            json={"recipeId": recipe_id, "text": text},
            timeout=30,
        )
        if not (200 <= r.status_code < 300):
            return {"ok": False, "status": r.status_code, "body": r.text[:500]}
        return {
            "ok": True,
            "comment_id": r.json().get("id"),
            "slug": slug,
            "text": text,
        }
    except Exception as e:
        return {"ok": False, "error": f"add_comment_failed: {e}"}


@tool("core")
def log_cooking_event(
    slug: str, subject: str, message: str | None = None
) -> dict[str, Any]:
    """Registra un evento en el timeline de una receta (cooking log / historial)."""
    if err := mealie._check_mealie():
        return err
    try:
        rr = requests.get(
            f"{config.MEALIE_BASE_URL}/api/recipes/{slug}",
            headers=mealie._mealie_headers(),
            timeout=30,
        )
        if not (200 <= rr.status_code < 300):
            return {
                "ok": False,
                "status": rr.status_code,
                "error": f"recipe_not_found: {slug}",
            }
        recipe_id = rr.json().get("id")
        if not recipe_id:
            return {"ok": False, "error": f"recipe_id_missing_for: {slug}"}
        payload: dict[str, Any] = {
            "recipeId": recipe_id,
            "subject": subject,
            "eventType": "info",
        }
        if message:
            payload["eventMessage"] = message
        r = requests.post(
            f"{config.MEALIE_BASE_URL}/api/recipes/timeline/events",
            headers=mealie._mealie_headers(),
            json=payload,
            timeout=30,
        )
        if not (200 <= r.status_code < 300):
            return {"ok": False, "status": r.status_code, "body": r.text[:500]}
        return {
            "ok": True,
            "event_id": r.json().get("id"),
            "slug": slug,
            "subject": subject,
        }
    except Exception as e:
        return {"ok": False, "error": f"log_cooking_event_failed: {e}"}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
