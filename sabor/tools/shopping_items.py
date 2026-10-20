"""Read and safely mutate items belonging to one Mealie shopping list."""
from uuid import UUID
from datetime import date
from urllib.parse import quote

import requests
import sabor.config as config
import sabor.integrations.mealie as mealie
from sabor.core import errors
from sabor.core.envelope import fail, ok, preview
from sabor.core.toolsets import mealie_ready, tool

_ITEMS = "/api/households/shopping/items"
# Preserve update fields so upstream defaults cannot reset unrelated item state.
_UPDATE_FIELDS = ("quantity", "note", "display", "position", "foodId", "unitId",
                  "labelId", "extras", "recipeReferences")


def _invalid(message):
    return fail("invalid_argument", message)


def _normalize(item):
    def reference(key):
        value = item.get(key)
        return {"id": value.get("id"), "name": value.get("name")} if value else None
    return {"id": item["id"], "list_id": item["shoppingListId"],
            **{key: item.get(key) for key in ("checked", "note", "display", "quantity")},
            "food": reference("food"), "unit": reference("unit"),
            "recipe_references": item.get("recipeReferences")}


def _read(list_id):
    try:
        canonical = str(UUID(list_id))
    except (ValueError, TypeError, AttributeError):
        return _invalid("list_id must be a UUID.")
    # Detail includes the complete listItems snapshot; no unverified queryFilter
    # grammar or partial-page ownership checks. Live detail completeness is a smoke gate.
    response = requests.get(
        f"{config.MEALIE_BASE_URL}/api/households/shopping/lists/{canonical}",
        headers=mealie._mealie_headers(), timeout=30)
    if not 200 <= response.status_code < 300:
        return errors.from_http(response.status_code, "mealie")
    items = response.json()["listItems"]
    if not isinstance(items, list) or any(
        not isinstance(item, dict) or not isinstance(item.get("id"), str)
        or item.get("shoppingListId") != canonical for item in items
    ):
        raise ValueError("Invalid list snapshot")
    return ok(items)


def _selection(list_id, item_ids):
    if not isinstance(item_ids, list) or not item_ids or any(
        not isinstance(value, str) or not value for value in item_ids
    ) or len(set(item_ids)) != len(item_ids):
        return _invalid("item_ids must be a nonempty list of unique IDs.")
    result = _read(list_id)
    if not result["ok"]:
        return result
    by_id = {item["id"]: item for item in result["data"]}
    unknown = [value for value in item_ids if value not in by_id]
    if unknown:
        return fail("invalid_argument", "Items do not belong to the requested list.",
                    data={"unknown_or_foreign_ids": unknown})
    return ok([by_id[value] for value in item_ids])


def _compose_get(path, params=None):
    response = requests.get(f"{config.MEALIE_BASE_URL}{path}",
        headers=mealie._mealie_headers(), params=params, timeout=30)
    if not 200 <= response.status_code < 300:
        return errors.from_http(response.status_code, "mealie")
    return ok(response.json())


@tool("core", requires=mealie_ready)
def add_to_shopping_list(list_id: str, items: list[str] | None = None,
                         recipe_slugs: list[str] | None = None,
                         mealplan_start: str | None = None, mealplan_end: str | None = None,
                         servings_multiplier: float = 1.0) -> dict:
    """Add exactly one source to a list. The client must get user approval before calling.

    Items remain unparsed notes; recipe scaling uses the explicit servings multiplier.
    """
    try:
        list_id = str(UUID(list_id))
        if type(servings_multiplier) not in (int, float) or not 0 < servings_multiplier <= 20:
            raise ValueError
        range_selected = mealplan_start is not None or mealplan_end is not None
        if sum((items is not None, recipe_slugs is not None, range_selected)) != 1:
            raise ValueError
        for values in (items, recipe_slugs):
            if values is not None and (not isinstance(values, list) or not values or any(
                    not isinstance(x, str) or not x.strip() for x in values)):
                raise ValueError
        if range_selected:
            if not isinstance(mealplan_start, str) or not isinstance(mealplan_end, str):
                raise ValueError
            start, end = date.fromisoformat(mealplan_start), date.fromisoformat(mealplan_end)
            if start.isoformat() != mealplan_start or end.isoformat() != mealplan_end or start > end:
                raise ValueError
    except (ValueError, TypeError, AttributeError):
        return _invalid("Require a UUID list, exactly one nonempty source, valid ISO range, and multiplier > 0 and <= 20.")
    source = "items" if items is not None else "recipe_slugs" if recipe_slugs is not None else "mealplan"
    recipes, skipped, occurrences = {}, [], []
    try:
        if recipe_slugs is not None:
            for slug in recipe_slugs:
                result = _compose_get(f"/api/recipes/{quote(slug, safe='')}")
                if not result["ok"]:
                    result["data"] = {"unresolved_slugs": [slug]}
                    return result
                recipe = result["data"]
                occurrences.append({"id": str(UUID(recipe["id"])), "slug": slug})
        elif range_selected:
            entries, page, expected = [], 1, None
            while True:
                result = _compose_get("/api/households/mealplans", {
                    "start_date": mealplan_start, "end_date": mealplan_end,
                    "page": page, "perPage": 100})
                if not result["ok"]:
                    return result
                data = result["data"]
                numbers = tuple(data[k] for k in ("page", "per_page", "total", "total_pages"))
                current, size, total, pages = numbers
                if (any(type(x) is not int for x in numbers) or current != page or size < 1
                        or total < 0 or pages != (total + size - 1) // size
                        or not isinstance(data["items"], list)
                        or (expected is not None and expected != (size, total, pages))):
                    raise ValueError
                expected = (size, total, pages)
                entries.extend(data["items"])
                if page >= pages:
                    if len(entries) != total:
                        raise ValueError
                    break
                if len(data["items"]) != size:
                    raise ValueError
                page += 1
            for entry in entries:
                recipe = entry.get("recipe")
                if recipe is None:
                    if entry.get("recipeId"):
                        raise ValueError
                    skipped.append({"entry": entry["id"], "reason": "text_only_entry"})
                else:
                    occurrences.append({"id": str(UUID(recipe["id"])), "slug": recipe["slug"]})
        for recipe in occurrences:
            summary = recipes.setdefault(recipe["id"], {**recipe, "occurrences": 0})
            summary["occurrences"] += 1
        payload = ([{"shoppingListId": list_id, "note": x, "quantity": 1} for x in items]
                   if items is not None else [{"recipeId": x["id"],
                       "recipeIncrementQuantity": servings_multiplier} for x in occurrences])
        result_data = {"list_id": list_id, "source": source,
                       "added": {"items": 0, "recipe_occurrences": 0},
                       "recipes": list(recipes.values()), "skipped": skipped}
        if not payload:
            return ok(result_data)
        # The schema is an array without uniqueItems: retain one row per occurrence.
        path = f"{_ITEMS}/create-bulk" if items is not None else f"/api/households/shopping/lists/{list_id}/recipe"
        response = requests.post(f"{config.MEALIE_BASE_URL}{path}",
            headers=mealie._mealie_headers(), json=payload, timeout=30)
        if not 200 <= response.status_code < 300:
            return errors.from_http(response.status_code, "mealie")
        if items is not None:
            created = response.json()["createdItems"]
            if not isinstance(created, list):
                raise ValueError
            if len(created) != len(items):
                return errors.partial([x["id"] for x in created],
                                      [{"source": "items", "unconfirmed_count": len(items) - len(created)}], "mealie")
            result_data["added"]["items"] = len(created)
        else:
            result_data["added"]["recipe_occurrences"] = len(occurrences)
        return ok(result_data)
    except (KeyError, TypeError, AttributeError):
        return errors.from_exception(ValueError(), "mealie")
    except Exception as exc:
        return errors.from_exception(exc, "mealie")


@tool("core", requires=mealie_ready)
def get_shopping_list_items(list_id: str, checked: bool | None = None,
                            page: int = 1, per_page: int = 100) -> dict:
    """Read one list's items, optionally filter checked state, then paginate locally."""
    if (type(page) is not int or page < 1 or type(per_page) is not int
            or not 1 <= per_page <= 100 or (checked is not None and type(checked) is not bool)):
        return _invalid("Require page >= 1, per_page 1–100, and a boolean checked filter.")
    try:
        result = _read(list_id)
        if not result["ok"]:
            return result
        items = [item for item in result["data"]
                 if checked is None or item.get("checked") is checked]
        start = (page - 1) * per_page
        return ok({"list_id": list_id, "items": [_normalize(x) for x in items[start:start + per_page]],
                   "total": len(items), "page": page, "per_page": per_page,
                   "total_pages": (len(items) + per_page - 1) // per_page})
    except Exception as exc:
        return errors.from_exception(exc, "mealie")


@tool("core", requires=mealie_ready)
def check_shopping_list_items(list_id: str, item_ids: list[str], checked: bool = True) -> dict:
    """Set checked state only after verifying every requested item belongs to the list."""
    if type(checked) is not bool:
        return _invalid("checked must be a boolean.")
    try:
        result = _selection(list_id, item_ids)
        if not result["ok"]:
            return result
        payload = [{**{k: item[k] for k in _UPDATE_FIELDS if k in item},
                    "id": item["id"], "shoppingListId": item["shoppingListId"],
                    "checked": checked} for item in result["data"]]
        response = requests.put(f"{config.MEALIE_BASE_URL}{_ITEMS}",
            headers=mealie._mealie_headers(), json=payload, timeout=30)
        if not 200 <= response.status_code < 300:
            return errors.from_http(response.status_code, "mealie")
        return ok({"changed_ids": item_ids, "checked": checked})
    except Exception as exc:
        return errors.from_exception(exc, "mealie")


@tool("core", requires=mealie_ready)
def remove_shopping_list_items(list_id: str, item_ids: list[str], confirm: bool = False) -> dict:
    """Preview item removal; confirm=True deletes exactly the verified item IDs."""
    if type(confirm) is not bool:
        return _invalid("confirm must be a boolean.")
    try:
        result = _selection(list_id, item_ids)
        if not result["ok"]:
            return result
        if not confirm:
            return preview({"list_id": list_id, "items": [_normalize(x) for x in result["data"]],
                            "count": len(result["data"])})
        response = requests.delete(f"{config.MEALIE_BASE_URL}{_ITEMS}",
            headers=mealie._mealie_headers(), params={"ids": item_ids}, timeout=30)
        if not 200 <= response.status_code < 300:
            return errors.from_http(response.status_code, "mealie")
        return ok({"removed_ids": item_ids})
    except Exception as exc:
        return errors.from_exception(exc, "mealie")
