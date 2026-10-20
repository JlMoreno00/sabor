"""Mealie shopping-list tools."""

from typing import Any
import requests
import sabor.config as config
import sabor.integrations.mealie as mealie
from sabor.core.toolsets import tool

# MEALIE — Shopping Lists
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@tool("core")
def get_shopping_lists() -> dict[str, Any]:
    """Devuelve todas las listas de compra del hogar con sus IDs y nombres."""
    if err := mealie._check_mealie():
        return err
    try:
        r = requests.get(
            f"{config.MEALIE_BASE_URL}/api/households/shopping/lists",
            headers=mealie._mealie_headers(),
            timeout=30,
        )
        if not (200 <= r.status_code < 300):
            return {"ok": False, "status": r.status_code, "body": r.text[:1000]}
        data = r.json()
        lists = [
            {
                "id": it.get("id"),
                "name": it.get("name"),
                "items_count": len(it.get("listItems") or []),
            }
            for it in (data.get("items") or [])
        ]
        return {"ok": True, "total": len(lists), "lists": lists}
    except Exception as e:
        return {"ok": False, "error": f"get_lists_failed: {e}"}


@tool("core")
def create_shopping_list(name: str) -> dict[str, Any]:
    """Crea una nueva lista de compra vacia con el nombre indicado."""
    if err := mealie._check_mealie():
        return err
    try:
        r = requests.post(
            f"{config.MEALIE_BASE_URL}/api/households/shopping/lists",
            headers=mealie._mealie_headers(),
            json={"name": name},
            timeout=30,
        )
        if not (200 <= r.status_code < 300):
            return {"ok": False, "status": r.status_code, "body": r.text[:1000]}
        data = r.json()
        return {"ok": True, "id": data.get("id"), "name": data.get("name")}
    except Exception as e:
        return {"ok": False, "error": f"create_list_failed: {e}"}


@tool("core")
def delete_shopping_list(list_id: str, confirm: bool = False) -> dict[str, Any]:
    """Preview deletion of a shopping list; pass confirm=True to delete it permanently."""
    if err := mealie._check_mealie():
        return err
    try:
        if not confirm:
            r = requests.get(
                f"{config.MEALIE_BASE_URL}/api/households/shopping/lists/{list_id}",
                headers=mealie._mealie_headers(), timeout=30,
            )
            if not 200 <= r.status_code < 300:
                return {"ok": False, "status": r.status_code, "body": r.text[:500]}
            data = r.json()
            items = data.get("listItems") or []
            return {"ok": True, "preview": True, "list_id": list_id,
                    "name": data.get("name"), "items_to_remove": items, "count": len(items)}
        r = requests.delete(
            f"{config.MEALIE_BASE_URL}/api/households/shopping/lists/{list_id}",
            headers=mealie._mealie_headers(),
            timeout=30,
        )
        return {"ok": 200 <= r.status_code < 300, "status": r.status_code}
    except Exception as e:
        return {"ok": False, "error": f"delete_list_failed: {e}"}


@tool("core")
def add_items_to_shopping_list(list_id: str, items: list[str]) -> dict[str, Any]:
    """Anade ingredientes a una lista de compra existente. items: lista de strings."""
    if err := mealie._check_mealie():
        return err
    if not items:
        return {"ok": False, "error": "items list is empty"}
    try:
        payload = [
            {"shoppingListId": list_id, "note": item, "quantity": 1} for item in items
        ]
        r = requests.post(
            f"{config.MEALIE_BASE_URL}/api/households/shopping/items/create-bulk",
            headers=mealie._mealie_headers(),
            json=payload,
            timeout=30,
        )
        if not (200 <= r.status_code < 300):
            return {"ok": False, "status": r.status_code, "body": r.text[:1000]}
        created = len(r.json().get("createdItems") or [])
        return {"ok": True, "list_id": list_id, "items_added": created}
    except Exception as e:
        return {"ok": False, "error": f"add_items_failed: {e}"}


@tool("core")
def clear_shopping_list(list_id: str, confirm: bool = False) -> dict[str, Any]:
    """Preview removal of all shopping items; pass confirm=True to clear the list."""
    if err := mealie._check_mealie():
        return err
    try:
        r = requests.get(
            f"{config.MEALIE_BASE_URL}/api/households/shopping/lists/{list_id}",
            headers=mealie._mealie_headers(),
            timeout=30,
        )
        if not (200 <= r.status_code < 300):
            return {"ok": False, "status": r.status_code, "body": r.text[:500]}
        items = r.json().get("listItems") or []
        if not confirm:
            return {"ok": True, "preview": True, "list_id": list_id,
                    "items_to_remove": items, "count": len(items)}
        if not items:
            return {"ok": True, "deleted": 0, "message": "Lista ya estaba vacia"}
        item_ids = [it["id"] for it in items if it.get("id")]
        params = "&".join(f"ids={iid}" for iid in item_ids)
        rd = requests.delete(
            f"{config.MEALIE_BASE_URL}/api/households/shopping/items?{params}",
            headers=mealie._mealie_headers(),
            timeout=30,
        )
        return {
            "ok": 200 <= rd.status_code < 300,
            "deleted": len(item_ids),
            "status": rd.status_code,
        }
    except Exception as e:
        return {"ok": False, "error": f"clear_list_failed: {e}"}


@tool("core")
def add_recipe_ingredients_to_list(list_id: str, recipe_slug: str) -> dict[str, Any]:
    """Anade automaticamente TODOS los ingredientes de una receta a una lista de compra."""
    if err := mealie._check_mealie():
        return err
    try:
        r = requests.post(
            f"{config.MEALIE_BASE_URL}/api/households/shopping/lists/{list_id}/recipe/{recipe_slug}",
            headers=mealie._mealie_headers(),
            timeout=30,
        )
        if not (200 <= r.status_code < 300):
            return {
                "ok": False,
                "status": r.status_code,
                "body": r.text[:500],
                "recipe_slug": recipe_slug,
            }
        return {
            "ok": True,
            "list_id": list_id,
            "recipe_slug": recipe_slug,
            "status": r.status_code,
        }
    except Exception as e:
        return {"ok": False, "error": f"add_recipe_failed: {e}"}


@tool("core")
def get_or_create_shopping_list(name: str) -> dict[str, Any]:
    """Busca una lista de compra por nombre; si no existe, la crea. Devuelve siempre el ID."""
    if err := mealie._check_mealie():
        return err
    try:
        r = requests.get(
            f"{config.MEALIE_BASE_URL}/api/households/shopping/lists",
            headers=mealie._mealie_headers(),
            timeout=30,
        )
        if 200 <= r.status_code < 300:
            for it in r.json().get("items") or []:
                if it.get("name", "").lower() == name.lower():
                    return {
                        "ok": True,
                        "id": it["id"],
                        "name": it["name"],
                        "created": False,
                    }
        r2 = requests.post(
            f"{config.MEALIE_BASE_URL}/api/households/shopping/lists",
            headers=mealie._mealie_headers(),
            json={"name": name},
            timeout=30,
        )
        if not (200 <= r2.status_code < 300):
            return {"ok": False, "status": r2.status_code, "body": r2.text[:500]}
        data = r2.json()
        return {"ok": True, "id": data["id"], "name": data["name"], "created": True}
    except Exception as e:
        return {"ok": False, "error": f"get_or_create_failed: {e}"}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
