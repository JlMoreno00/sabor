"""Mealie recipe tools."""

from datetime import datetime, timezone
from typing import Any
from dataclasses import dataclass, fields, is_dataclass
from sabor.core.runtime import config as config  # Direct-call patch compatibility.
from sabor.store import (
    Ingredient,
    Instruction,
    NamedRef,
    RecipeDraft,
    RecipePatch,
    RecipeSearch,
    SuggestionQuery,
    StoreError,
    store_error_envelope,
)
import sabor.integrations.mealie as mealie
from sabor.core.toolsets import current_context, tool, store_ready
from sabor.core.envelope import ok, preview, fail
from sabor.core import errors

# MEALIE — Recipes
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━




def _tuple(value):
    return None if value is None else tuple(value)


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
        return fail("provider_unconfigured", "Recipe store is not configured.", "mealie")
    try:
        data = _public(getattr(ctx.store, operation)(argument))
        if operation == "suggest_recipes":
            data = {"items": data}
        return ok(data)
    except StoreError as exc:
        return store_error_envelope(exc)


@tool("core", requires=store_ready, uses_context=True)
def search_recipes(
    query: str = "", page: int = 1, per_page: int = 25,
    tag_ids: list[str] | None = None, category_ids: list[str] | None = None,
    food_ids: list[str] | None = None, tool_ids: list[str] | None = None,
    cookbook_id: str | None = None, require_all: bool = False,
    sort: str = "name", direction: str = "asc",
) -> dict[str, Any]:
    """Search recipes by stable filter IDs with upstream pagination; sort by name."""
    if (type(page) is not int or page < 1 or type(per_page) is not int
            or not 1 <= per_page <= 100 or sort not in ("name", "created_at", "updated_at", "rating", "last_made", "random")
            or direction not in ("asc", "desc")):
        return fail("invalid_argument", "Invalid pagination or sorting arguments.")
    filters = RecipeSearch(query, page, per_page, _tuple(tag_ids), _tuple(category_ids),
                           _tuple(food_ids), _tuple(tool_ids), cookbook_id, require_all, sort, direction,
                           datetime.now(timezone.utc).date().isoformat() if sort == "random" else None)
    return _store_read(current_context(), "search_recipes", filters)


@tool("core", requires=store_ready, uses_context=True)
def get_recipe(slug: str) -> dict[str, Any]:
    """Read structured ingredients, instructions, taxonomy and times; unknowns stay null."""
    return _store_read(current_context(), "get_recipe", slug)


_PATCH_FIELDS = frozenset((
    "name", "description", "recipeYield", "recipeServings", "totalTime",
    "prepTime", "cookTime", "recipeIngredient", "recipeInstructions",
    "tags", "recipeCategory",
))
_ARRAY_FIELDS = frozenset(("recipeIngredient", "recipeInstructions", "tags", "recipeCategory"))


@tool("core", requires=store_ready, uses_context=True)
def update_recipe(slug: str, patch: dict, confirm: bool = False) -> dict[str, Any]:
    """Preview or PATCH selected fields. Arrays replace whole lists, not individual entries."""
    if not isinstance(patch, dict) or not patch or patch.keys() - _PATCH_FIELDS:
        return fail("invalid_argument", "Provide a nonempty patch containing only editable fields.")
    return _update(current_context(), slug, patch, confirm)


def _update(ctx, slug, patch, confirm):
    if ctx.store is None:
        return fail("provider_unconfigured", "Recipe store is not configured.", "mealie")
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


@tool("core", requires=store_ready, uses_context=True)
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
    return _store_read(current_context(), "suggest_recipes", query)


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
        draft = _CreateDraft(
            name=name,
            description=description,
            recipeYield=jsonld_recipe.get("recipeYield"),
            totalTime=total_time,
            recipeIngredient=tuple(
                Ingredient(originalText=value)
                for value in jsonld_recipe.get("recipeIngredient", ())
            ),
            recipeInstructions=tuple(
                Instruction(text=value)
                for value in jsonld_recipe.get("recipeInstructions", ())
            ),
            tags=tuple(NamedRef(name=value) for value in tags)
            if tags is not None
            else None,
            jsonld=jsonld_recipe,
        )
    except Exception as exc:
        return errors.from_exception(exc, "mealie")
    return _create(current_context(), draft, is_structured)


@dataclass(frozen=True)
class _CreateDraft(RecipeDraft):
    jsonld: dict | None = None


def _create(ctx, draft, structured):
    if ctx.store is None:
        return {"ok": False, "error": "missing_MEALIE_API_TOKEN"}
    try:
        ref = ctx.store.create_recipe(draft)
        result = _public(ref)
        if getattr(ctx.store, "direct_compatibility", False):
            return mealie._normalization_result(result, ref.slug, enabled=structured)
        if structured and ctx.store.id == "mealie":
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

