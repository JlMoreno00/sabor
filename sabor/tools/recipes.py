"""Mealie recipe tools."""

import json
from typing import Any
from dataclasses import dataclass, fields, is_dataclass
from sabor.store import RecipeDraft, RecipePatch, RecipeSearch, SuggestionQuery, StoreError, store_error_envelope
import requests
import sabor.config as config
import sabor.integrations.mealie as mealie
from sabor.core.toolsets import tool, mealie_ready
from sabor.core.envelope import ok, preview, fail
from sabor.core import errors

# MEALIE — Recipes
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def _configured():
    if not config.MEALIE_API_TOKEN:
        return fail("provider_unconfigured", "Mealie is not configured.", "mealie")


def _request(method, path, **kwargs) -> tuple[Any, dict | None]:
    response = method(
        f"{config.MEALIE_BASE_URL}/api/recipes{path}",
        headers=mealie._mealie_headers(), timeout=30, **kwargs)
    if not 200 <= response.status_code < 300:
        return None, errors.from_http(response.status_code, "mealie")
    return response, None


def _object(value):
    if not isinstance(value, dict):
        raise ValueError("Expected object")
    return value


def _items(data):
    items = _object(data).get("items")
    if not isinstance(items, list):
        raise ValueError("Expected items")
    return [_object(item) for item in items]


def _summary(item):
    return {key: item.get(key) for key in ("id", "name", "slug")}


def _tuple(value):
    return None if value is None else tuple(value)


def _defer(operation, argument) -> Any:
    """Internal context callback consumed by the tool decorator, not MCP data."""
    return lambda ctx: _store_read(ctx, operation, argument)


def _public(value) -> Any:
    if hasattr(value, "to_public"):
        return value.to_public()
    if is_dataclass(value):
        return {field.name: _public(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, (tuple, list)):
        return [_public(row) for row in value]
    return value


def _store_read(ctx, operation, argument):
    if ctx.store is None:
        return fail("provider_unconfigured", "Mealie is not configured.", "mealie")
    try:
        data = _public(getattr(ctx.store, operation)(argument))
        if operation == "suggest_recipes":
            data = {"items": data}
        return ok(data)
    except StoreError as exc:
        return store_error_envelope(exc)


@tool("core", requires=mealie_ready, uses_context=True)
def search_recipes(
    query: str = "", page: int = 1, per_page: int = 25,
    tag_ids: list[str] | None = None, category_ids: list[str] | None = None,
    food_ids: list[str] | None = None, tool_ids: list[str] | None = None,
    cookbook_id: str | None = None, require_all: bool = False,
    sort: str = "name", direction: str = "asc",
) -> dict[str, Any]:
    """Search recipes by stable filter IDs with upstream pagination; sort by name."""
    if (type(page) is not int or page < 1 or type(per_page) is not int
            or not 1 <= per_page <= 100 or sort != "name"
            or direction not in ("asc", "desc")):
        return fail("invalid_argument", "Invalid pagination or sorting arguments.")
    filters = RecipeSearch(query, page, per_page, _tuple(tag_ids), _tuple(category_ids),
                           _tuple(food_ids), _tuple(tool_ids), cookbook_id, require_all, sort, direction)
    return _defer("search_recipes", filters)


@tool("core", requires=mealie_ready, uses_context=True)
def get_recipe(slug: str) -> dict[str, Any]:
    """Read structured ingredients, instructions, taxonomy and times; unknowns stay null."""
    return _defer("get_recipe", slug)


_PATCH_FIELDS = frozenset((
    "name", "description", "recipeYield", "recipeServings", "totalTime",
    "prepTime", "cookTime", "recipeIngredient", "recipeInstructions",
    "tags", "recipeCategory",
))
_ARRAY_FIELDS = frozenset(("recipeIngredient", "recipeInstructions", "tags", "recipeCategory"))


@tool("core", requires=mealie_ready, uses_context=True)
def update_recipe(slug: str, patch: dict, confirm: bool = False) -> dict[str, Any]:
    """Preview or PATCH selected fields. Arrays replace whole lists, not individual entries."""
    if not isinstance(patch, dict) or not patch or patch.keys() - _PATCH_FIELDS:
        return fail("invalid_argument", "Provide a nonempty patch containing only editable fields.")
    return _defer_update(slug, patch, confirm)


def _update(ctx, slug, patch, confirm):
    if ctx.store is None:
        return fail("provider_unconfigured", "Mealie is not configured.", "mealie")
    try:
        if not confirm:
            before = _public(ctx.store.get_recipe(slug))
            changes = {key: dict(before=before.get(key), after=value,
                                 full_replacement=key in _ARRAY_FIELDS)
                       for key, value in patch.items()}
            return preview(dict(slug=slug, changed_fields=list(patch), changes=changes))
        ctx.store.update_recipe(slug, RecipePatch(patch))
        return ok(dict(slug=slug, changed_fields=list(patch)))
    except StoreError as exc:
        return store_error_envelope(exc)


@tool("core", requires=mealie_ready, uses_context=True)
def suggest_recipes(
    foods: list[str], tools: list[str] | None = None,
    max_missing_foods: int = 2, max_missing_tools: int = 0,
    limit: int = 10, include_foods_on_hand: bool = True,
) -> dict[str, Any]:
    """Suggest recipes from food/tool IDs, preserving missing foods and tools."""
    if (not isinstance(foods, list) or
            any(type(value) is not int or value < 0
                for value in (max_missing_foods, max_missing_tools)) or
            type(limit) is not int or limit < 1):
        return fail("invalid_argument", "Invalid suggestion arguments.")
    query = SuggestionQuery(tuple(foods), _tuple(tools), max_missing_foods, max_missing_tools,
                            limit, include_foods_on_hand)
    return _defer("suggest_recipes", query)


@tool("core", uses_context=True)
def create_recipe(
    name: str,
    description: str | None = None,
    recipe_yield: str | None = None,
    total_time: str | None = None,
    ingredients: list[str] | None = None,
    instructions: list[str] | None = None,
    tags: list[str] | None = None,
    source_url: str | None = None,
) -> dict[str, Any]:
    """Create a recipe in Mealie using structured fields, not URL import."""
    try:
        structured_values = (
            description,
            recipe_yield,
            total_time,
            ingredients,
            instructions,
            tags,
            source_url,
        )
        is_structured = any(value is not None for value in structured_values)
        jsonld_recipe: dict[str, Any] = {
            "@context": "https://schema.org",
            "@type": "Recipe",
            "name": name,
        }
        if is_structured:
            optional_fields = {
                "description": description,
                "recipeYield": recipe_yield,
                "totalTime": total_time,
                "recipeIngredient": ingredients,
                "recipeInstructions": instructions,
                "keywords": tags,
                "url": source_url,
            }
            jsonld_recipe.update(
                {key: value for key, value in optional_fields.items() if value is not None}
            )
        else:
            jsonld_recipe.update(
                {
                    "recipeIngredient": ["Ingrediente a completar"],
                    "recipeInstructions": ["Instrucciones a completar"],
                    "recipeYield": "2 raciones",
                }
            )
        draft = _CreateDraft(name=name, jsonld=jsonld_recipe)
        return _defer_create(draft, is_structured)
    except Exception as exc:
        return errors.from_exception(exc, "mealie")


@dataclass(frozen=True)
class _CreateDraft(RecipeDraft):
    jsonld: dict | None = None


def _create(ctx, draft, structured):
    if ctx.store is None:
        return {"ok": False, "error": "missing_MEALIE_API_TOKEN"}
    try:
        ref = ctx.store.create_recipe(draft)
        result = _public(ref)
        if getattr(ctx.store, "session", None) is requests:
            return mealie._normalization_result(result, ref.slug, enabled=structured)
        if structured:
            normalizer = getattr(ctx.store, "normalize_imported", None)
            if ctx.settings.normalize_imports and ref.slug and normalizer:
                normalized = normalizer(ref.slug)
                result["normalization"] = normalized
                if not normalized.get("ok"):
                    result["normalization_warning"] = (normalized.get("warning") or
                        normalized.get("error") or "normalization_failed")
            # Preserve the legacy nutrition gates until the J2 isolation slice.
            result = mealie.nutrition._nutrition_after_import(result, ref.slug)
        return result
    except StoreError as exc:
        return store_error_envelope(exc)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def _defer_update(slug, patch, confirm) -> Any:
    return lambda ctx: _update(ctx, slug, patch, confirm)


def _defer_create(draft, structured) -> Any:
    return lambda ctx: _create(ctx, draft, structured)
