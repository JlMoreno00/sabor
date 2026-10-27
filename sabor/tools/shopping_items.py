"""Read and safely mutate items belonging to one Mealie shopping list."""
from datetime import date
from dataclasses import dataclass

from sabor.store import (StoreError, store_error_envelope, ItemDraft, RecipeOccurrence, DateRange)
from sabor.core import errors
from sabor.core.envelope import fail, ok, preview
from sabor.core.toolsets import current_context, store_ready, tool
from sabor.pricing.pantry import load_pantry, match_pantry, item_name
from sabor.pricing.quantities import ingredient_name

@dataclass(frozen=True)
class _ResolvedOccurrence(RecipeOccurrence):
    recipe_id: str = ''


def _invalid(message):
    return fail("invalid_argument", message)


def _normalize(item):
    def reference(value):
        return {"id": value.id, "name": value.name} if value else None
    return {"id": item.id, "list_id": item.list_id,
            **{key: getattr(item, key) for key in ("checked", "note", "display", "quantity")},
            "food": reference(item.food), "unit": reference(item.unit),
            "recipe_references": item.recipe_references}


def _read(ctx, list_id):
    canonical = ctx.store.validate_shopping_list_id(list_id)
    return ok(list(ctx.store.get_shopping_items(canonical).items))


def _selection(ctx, list_id, item_ids):
    if not isinstance(item_ids, list) or not item_ids or any(
        not isinstance(value, str) or not value for value in item_ids
    ) or len(set(item_ids)) != len(item_ids):
        return _invalid("item_ids must be a nonempty list of unique IDs.")
    result = _read(ctx, list_id)
    if not result["ok"]:
        return result
    by_id = {item.id: item for item in result["data"]}
    unknown = [value for value in item_ids if value not in by_id]
    if unknown:
        return fail("invalid_argument", "Items do not belong to the requested list.",
                    data={"unknown_or_foreign_ids": unknown})
    return ok([by_id[value] for value in item_ids])


def _run(ctx, operation, *args):
    if ctx.store is None:
        return fail("provider_unconfigured", "Recipe store is not configured.", "mealie")
    try:
        return operation(ctx, *args)
    except StoreError as exc:
        return getattr(exc, "result", None) or store_error_envelope(exc)
    except Exception as exc:
        return errors.from_exception(exc, "mealie")


@tool("core", requires=store_ready, uses_context=True)
def add_to_shopping_list(list_id: str, items: list[str] | None = None,
                         recipe_slugs: list[str] | None = None,
                         mealplan_start: str | None = None, mealplan_end: str | None = None,
                         servings_multiplier: float = 1.0) -> dict:
    """Add exactly one source to a list. The client must get user approval before calling.

    Items remain unparsed notes; recipe scaling uses the explicit servings multiplier.
    """
    return _run(current_context(), _add_to_shopping_list, list_id, items, recipe_slugs, mealplan_start, mealplan_end, servings_multiplier)


def _add_to_shopping_list(ctx, list_id: str, items: list[str] | None = None,
                         recipe_slugs: list[str] | None = None,
                         mealplan_start: str | None = None, mealplan_end: str | None = None,
                         servings_multiplier: float = 1.0) -> dict:
    try:
        list_id = ctx.store.validate_shopping_list_id(list_id, adding=True)
        if type(servings_multiplier) not in (int, float) or not 0 < servings_multiplier <= 20:
            raise ValueError
        range_selected = mealplan_start is not None or mealplan_end is not None
        sources = [items is not None, recipe_slugs is not None, range_selected]
        if sources.count(True) != 1:
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
    # Pantry hints are advisory: an unreadable preferences file never blocks the write.
    pantry, error = load_pantry(ctx)
    try:
        if recipe_slugs is not None:
            for slug in recipe_slugs:
                try:
                    recipe = ctx.store.get_recipe(slug)
                except StoreError as exc:
                    result = store_error_envelope(exc)
                    result["data"] = {"unresolved_slugs": [slug]}
                    return result
                occurrences.append({"id": ctx.store.validate_recipe_id(recipe.id), "slug": slug})
        elif range_selected:
            assert isinstance(mealplan_start, str) and isinstance(mealplan_end, str)
            for entry in ctx.store.get_mealplan(DateRange(mealplan_start, mealplan_end)):
                if entry.recipe_id is None:
                    if (getattr(entry, "raw", None) or {}).get("recipeId"):
                        raise ValueError("Missing recipe reference")
                    skipped.append({"entry": entry.id, "reason": "text_only_entry"})
                else:
                    occurrences.append({"id": ctx.store.validate_recipe_id(entry.recipe_id), "slug": entry.recipe_slug})
        for recipe in occurrences:
            summary = recipes.setdefault(recipe["id"], {**recipe, "occurrences": 0})
            summary["occurrences"] += 1
        result_data = {"list_id": list_id, "source": source,
                       "added": {"items": 0, "recipe_occurrences": 0},
                       "recipes": list(recipes.values()), "skipped": skipped}
        if error:
            result_data['pantry_warning'] = 'preferences_unreadable'
        elif pantry:
            try:
                names = [ingredient_name(text) for text in items] if items is not None else [
                    item_name(ingredient) for occurrence in occurrences
                    for ingredient in (ctx.store.get_recipe(occurrence['slug'] or occurrence['id']).recipeIngredient or ())]
                result_data['pantry_matches'] = [
                    {'name': name, 'pantry_entry': entry} for name in names
                    if (entry := match_pantry(name, pantry)) is not None]
            except Exception:
                result_data['pantry_warning'] = 'pantry_matching_failed'
        if items is None and not occurrences:
            return ok(result_data)
        if items is not None:
            written = ctx.store.add_shopping_items(list_id, [ItemDraft(note=x, quantity=1) for x in items])
            result_data["added"]["items"] = len(written.changed_ids)
            if len(written.changed_ids) != len(items):
                result = errors.partial(list(written.changed_ids),
                    [{"source": "items", "unconfirmed_count": len(items) - len(written.changed_ids)}], "mealie")
                result["data"] = {**result_data, **result["data"]}
                return result
        else:
            ctx.store.add_recipe_occurrences(list_id, [
                _ResolvedOccurrence(x["slug"], servings_multiplier, x["id"]) for x in occurrences])
            result_data["added"]["recipe_occurrences"] = len(occurrences)
        return ok(result_data)
    except (KeyError, TypeError, AttributeError):
        return errors.from_exception(ValueError(), "mealie")
    except StoreError:
        raise
    except Exception as exc:
        return errors.from_exception(exc, "mealie")


@tool("core", requires=store_ready, uses_context=True)
def get_shopping_list_items(list_id: str, checked: bool | None = None,
                            page: int = 1, per_page: int = 100) -> dict:
    """Read one list's items, optionally filter checked state, then paginate locally."""
    return _run(current_context(), _get_shopping_list_items, list_id, checked, page, per_page)


def _get_shopping_list_items(ctx, list_id: str, checked: bool | None = None,
                            page: int = 1, per_page: int = 100) -> dict:
    if (type(page) is not int or page < 1 or type(per_page) is not int
            or not 1 <= per_page <= 100 or (checked is not None and type(checked) is not bool)):
        return _invalid("Require page >= 1, per_page 1–100, and a boolean checked filter.")
    try:
        result = _read(ctx, list_id)
        if not result["ok"]:
            return result
        items = [item for item in result["data"]
                 if checked is None or item.checked is checked]
        start = (page - 1) * per_page
        return ok({"list_id": list_id, "items": [_normalize(x) for x in items[start:start + per_page]],
                   "total": len(items), "page": page, "per_page": per_page,
                   "total_pages": (len(items) + per_page - 1) // per_page})
    except StoreError:
        raise
    except Exception as exc:
        return errors.from_exception(exc, "mealie")


@tool("core", requires=store_ready, uses_context=True)
def check_shopping_list_items(list_id: str, item_ids: list[str], checked: bool = True) -> dict:
    """Set checked state only after verifying every requested item belongs to the list."""
    return _run(current_context(), _check_shopping_list_items, list_id, item_ids, checked)


def _check_shopping_list_items(ctx, list_id: str, item_ids: list[str], checked: bool = True) -> dict:
    if type(checked) is not bool:
        return _invalid("checked must be a boolean.")
    try:
        result = _selection(ctx, list_id, item_ids)
        if not result["ok"]:
            return result
        ctx.store.check_shopping_items(list_id, item_ids, checked)
        return ok({"changed_ids": item_ids, "checked": checked})
    except StoreError:
        raise
    except Exception as exc:
        return errors.from_exception(exc, "mealie")


@tool("core", requires=store_ready, uses_context=True)
def remove_shopping_list_items(list_id: str, item_ids: list[str], confirm: bool = False) -> dict:
    """Preview item removal; confirm=True deletes exactly the verified item IDs."""
    return _run(current_context(), _remove_shopping_list_items, list_id, item_ids, confirm)


def _remove_shopping_list_items(ctx, list_id: str, item_ids: list[str], confirm: bool = False) -> dict:
    if type(confirm) is not bool:
        return _invalid("confirm must be a boolean.")
    try:
        result = _selection(ctx, list_id, item_ids)
        if not result["ok"]:
            return result
        if not confirm:
            return preview({"list_id": list_id, "items": [_normalize(x) for x in result["data"]],
                            "count": len(result["data"])})
        ctx.store.remove_shopping_items(list_id, item_ids)
        return ok({"removed_ids": item_ids})
    except StoreError:
        raise
    except Exception as exc:
        return errors.from_exception(exc, "mealie")
