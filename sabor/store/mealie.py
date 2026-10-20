"""Instance-owned Mealie transport and recipe read mapping.

Write/calendar/list support is added in subsequent bounded migration slices.
"""
import json
import requests
from typing import Any, cast
from dataclasses import dataclass
from copy import deepcopy

from sabor.core import errors
from sabor.store import (Ingredient, Instruction, NamedRef, Page, Recipe,
                         RecipeSummary, RecipeRef, StoreError, Suggestion, MealEntry)


def _object(value):
    if not isinstance(value, dict):
        raise ValueError("Expected object")
    return value


def _items(value):
    rows = _object(value).get("items")
    if not isinstance(rows, list):
        raise ValueError("Expected items")
    return [_object(row) for row in rows]


@dataclass(frozen=True)
class _MealieRef(NamedRef):
    # Taxonomy/ingredient metadata is public recipe data, not transport state.
    public_fields: dict | None = None

    def to_public(self):
        return deepcopy(self.public_fields)


@dataclass(frozen=True)
class _MealieSuggestion(Suggestion):
    public_fields: dict | None = None

    def to_public(self):
        return deepcopy(self.public_fields)


def _ref(value):
    if value is None:
        return None
    if isinstance(value, str):
        return _MealieStringRef(name=value)
    value = _object(value)
    return _MealieRef(value.get("id"), value.get("name"), deepcopy(value))


def _refs(value):
    if value is None:
        return None
    if not isinstance(value, list):
        raise ValueError("Expected references")
    return tuple(cast(NamedRef, _ref(row)) for row in value)


def _summary(value):
    return {key: value.get(key) for key in ("id", "name", "slug")}


from sabor.store.mealie_shopping import MealieShoppingListsMixin  # noqa: E402
from sabor.store.mealie_items import MealieShoppingItemsMixin  # noqa: E402


class MealieRecipeStore(MealieShoppingItemsMixin, MealieShoppingListsMixin):
    id = "mealie"
    operations: frozenset[str] = frozenset(("search_recipes", "get_recipe", "suggest_recipes", "create_recipe", "update_recipe", "get_mealplan", "set_mealplan_entry", "delete_mealplan_entry", "get_shopping_lists", "create_shopping_list", "delete_shopping_list", "get_shopping_items", "add_shopping_items", "add_recipe_occurrences", "check_shopping_items", "remove_shopping_items"))

    def __init__(self, base_url, token, session=None, settings=None):
        from sabor.core.config import load_settings
        self.settings = settings if settings is not None else load_settings({})
        self._token = token
        self.base_url = base_url.rstrip("/")
        self.session = session if session is not None else requests.Session()
        self._headers = {"Authorization": f"Bearer {token}"}

    native_import_kinds = frozenset({'url', 'image'})

    def _native_import(self, path, **kwargs):
        try:
            response = self.session.post(self.base_url + '/api/recipes' + path,
                                         headers=self._headers, timeout=45, **kwargs)
            if not 200 <= response.status_code < 300:
                raise StoreError(errors.from_http(response.status_code, self.id)['error']['code'], self.id)
            return response.json()
        except StoreError:
            raise
        except Exception as exc:
            raise StoreError(errors.from_exception(exc, self.id)['error']['code'], self.id) from None

    def preview_url(self, source):
        return self._native_import('/test-scrape-url', json={'url': source, 'useOpenAI': False})

    def import_url(self, source):
        return self._native_import('/create/url', json=dict(url=source, includeTags=True, includeCategories=True))

    def import_image(self, content, mime, language):
        return self._native_import('/create/image',
            files=[('images', ('recipe-image', content, mime))],
            params={'translateLanguage': language})

    def normalize_imported(self, slug):
        from sabor.integrations.mealie import normalize_imported_recipe
        return normalize_imported_recipe(slug, self.session, self.base_url,
                                         self._token, self.settings)

    def _unsupported(self, *args, **kwargs):
        raise StoreError("unsupported_operation", self.id)

    # Explicitly unavailable until their bounded migration units land.
    get_or_create_tag = _unsupported
    tag_recipe = _unsupported
    add_recipe_comment = _unsupported
    log_cooking_event = _unsupported

    def _calendar_request(self, method, path="", **kwargs):
        try:
            response = method(f"{self.base_url}/api/households/mealplans{path}",
                              headers=self._headers, timeout=30, **kwargs)
            if not 200 <= response.status_code < 300:
                exc = StoreError(errors.from_http(response.status_code, self.id)["error"]["code"], self.id)
                setattr(exc, "status", response.status_code)
                raise exc
            return response
        except StoreError:
            raise
        except Exception as exc:
            raise StoreError(errors.from_exception(exc, self.id)["error"]["code"], self.id) from None

    def get_mealplan(self, dates):
        rows, seen, expected_pages = [], set(), None
        for page in range(1, 1001):
            params = {"start_date": dates.start_date, "end_date": dates.end_date}
            if page > 1:
                params["page"] = page
            response = self._calendar_request(self.session.get, params=params)
            try:
                data = _object(response.json())
                items = data.get("items") or []
                pages = data.get("total_pages", 1)
                if (type(pages) is not int or not 1 <= pages <= 1000
                        or data.get("page", page) != page
                        or (expected_pages is not None and pages != expected_pages)):
                    raise ValueError("Inconsistent pagination")
                expected_pages = pages
                for item in items:
                    item = _object(item)
                    eid = item.get("id")
                    if eid in seen and eid is not None:
                        raise ValueError("Repeated page entry")
                    seen.add(eid)
                    recipe = item.get("recipe") or {}
                    rows.append(_CalendarEntry(
                        date=cast(Any, item.get("date")), entry_type=cast(Any, item.get("entryType")),
                        title=item.get("title") or recipe.get("name", ""),
                        recipe_slug=recipe.get("slug"), id=cast(Any, eid),
                        recipe_id=recipe.get("id"), raw=deepcopy(item)))
                if page == pages:
                    return rows
            except Exception:
                raise StoreError("invalid_response", self.id) from None
        raise StoreError("invalid_response", self.id)

    def set_mealplan_entry(self, entry):
        # Shared legacy gate intentionally retains nutrition config/services/locks.
        from sabor.tools.mealplan import _schedule
        result = _schedule(entry.date, entry.entry_type, entry.title, entry.recipe_slug,
                           self.session, self.base_url, self._headers)
        if not result.get("ok"):
            if "status" in result:
                raise StoreError(errors.from_http(result["status"], self.id)["error"]["code"], self.id)
            if "code" not in result:
                raise StoreError("upstream_error", self.id)
        recipe_id = result.pop("_recipe_id", None)
        return _CalendarEntry(entry.date, entry.entry_type, entry.title, entry.recipe_slug,
                              result.get("id"), recipe_id, result=result)

    def delete_mealplan_entry(self, entry_id):
        return self._calendar_request(self.session.delete, f"/{entry_id}").status_code

    def _read(self, path, mapper, **kwargs):
        try:
            response = self.session.get(
                f"{self.base_url}/api/recipes{path}",
                headers=self._headers, timeout=30, **kwargs)
            if not 200 <= response.status_code < 300:
                code = errors.from_http(response.status_code, self.id)["error"]["code"]
                raise StoreError(code, self.id)
            return mapper(response.json())
        except StoreError:
            raise
        except Exception as exc:
            code = errors.from_exception(exc, self.id)["error"]["code"]
            raise StoreError(code, self.id) from None

    def search_recipes(self, filters):
        f = filters
        if (type(f.page) is not int or f.page < 1 or type(f.per_page) is not int
                or not 1 <= f.per_page <= 100 or f.sort != "name"
                or f.direction not in ("asc", "desc")):
            raise StoreError("invalid_argument", self.id)
        params: dict[str, Any] = dict(search=f.query, page=f.page, perPage=f.per_page,
                      orderBy=f.sort, orderDirection=f.direction)
        for name, value in (("tags", f.tag_ids), ("categories", f.category_ids),
                            ("foods", f.food_ids), ("tools", f.tool_ids),
                            ("cookbook", f.cookbook_id)):
            if value is not None:
                params[name] = list(value) if isinstance(value, tuple) else value
        for name in ("Tags", "Categories", "Foods", "Tools"):
            params[f"requireAll{name}"] = f.require_all

        def page(source):
            source = _object(source)
            rows = tuple(RecipeSummary(**_summary(row)) for row in _items(source))
            metadata = {key: source.get(key) for key in
                        ("total", "page", "per_page", "total_pages")}
            if any(type(value) is not int for value in metadata.values()):
                raise ValueError("Invalid pagination")
            return Page(rows, **cast(dict[str, Any], metadata))

        return self._read("", page, params=params)

    def get_recipe(self, slug):
        def recipe(source):
            source = _object(source)
            data = {key: source.get(key) for key in (
                "id", "name", "slug", "description", "recipeYield", "recipeServings",
                "totalTime", "prepTime", "cookTime")}
            for key in ("tags", "recipeCategory"):
                data[key] = _refs(source.get(key))
            for key, dto in (("recipeIngredient", Ingredient),
                             ("recipeInstructions", Instruction)):
                values = source.get(key)
                if values is None:
                    data[key] = None
                    continue
                if not isinstance(values, list):
                    raise ValueError("Expected array")
                rows = []
                for value in values:
                    value = _object(value)
                    fields = {name: value.get(name) for name in dto.__dataclass_fields__}
                    if dto is Ingredient:
                        for name in ("unit", "food"):
                            fields[name] = _ref(fields[name])
                    rows.append(dto(**fields))
                data[key] = tuple(rows)
            return Recipe(**data)

        return self._read(f"/{slug}", recipe)

    def suggest_recipes(self, query):
        q = query
        if (not isinstance(q.foods, (tuple, list)) or
                any(type(value) is not int or value < 0 for value in
                    (q.max_missing_foods, q.max_missing_tools)) or
                type(q.limit) is not int or q.limit < 1):
            raise StoreError("invalid_argument", self.id)
        params = dict(foods=list(q.foods), maxMissingFoods=q.max_missing_foods,
                      maxMissingTools=q.max_missing_tools, limit=q.limit,
                      includeFoodsOnHand=q.include_foods_on_hand)
        if q.tools is not None:
            params["tools"] = list(q.tools)

        def suggestions(source):
            rows = []
            for row in _items(source):
                public = _summary(_object(row.get("recipe")))
                public.update({key: deepcopy(row[key]) for key in
                               ("missingFoods", "missingTools") if key in row})
                rows.append(_MealieSuggestion(**_summary(row["recipe"]),
                            missingFoods=_refs(row.get("missingFoods")),
                            missingTools=_refs(row.get("missingTools")), public_fields=public))
            return rows

        return self._read("/suggestions", suggestions, params=params)


    def _write(self, method, path, payload, timeout=30):
        try:
            response = method(f"{self.base_url}/api/recipes{path}",
                              headers=self._headers, json=payload, timeout=timeout)
            if not 200 <= response.status_code < 300:
                raise StoreError(errors.from_http(response.status_code, self.id)["error"]["code"], self.id)
            return response
        except StoreError:
            raise
        except Exception as exc:
            raise StoreError(errors.from_exception(exc, self.id)["error"]["code"], self.id) from None

    def create_recipe(self, draft):
        payload = getattr(draft, "jsonld", None)
        if payload is None:
            payload = {"@context": "https://schema.org", "@type": "Recipe", "name": draft.name}
            for key in ("description", "recipeYield", "totalTime"):
                value = getattr(draft, key)
                if value is not None:
                    payload[key] = value
            if draft.recipeIngredient is not None:
                payload["recipeIngredient"] = [row.originalText or row.display or row.note or ""
                                                for row in draft.recipeIngredient]
            if draft.recipeInstructions is not None:
                payload["recipeInstructions"] = [row.text for row in draft.recipeInstructions]
            if draft.tags is not None:
                payload["keywords"] = [row.name for row in draft.tags]
        response = self._write(self.session.post, "/create/html-or-json",
                               {"data": json.dumps(payload, ensure_ascii=False)}, 60)
        try:
            body = response.json()
            slug = body.strip() if isinstance(body, str) else body.get("slug")
            return _CreatedRecipe(slug=slug, result={
                "ok": True, "status": response.status_code, "body": response.text[:2000], "slug": slug})
        except Exception:
            raise StoreError("invalid_response", self.id) from None

    def update_recipe(self, slug, patch):
        allowed = {"name", "description", "recipeYield", "recipeServings", "totalTime",
                   "prepTime", "cookTime", "recipeIngredient", "recipeInstructions",
                   "tags", "recipeCategory"}
        changes = dict(patch.changes)
        if not changes or changes.keys() - allowed:
            raise StoreError("invalid_argument", self.id)
        self._write(self.session.patch, f"/{slug}", changes)
        return RecipeRef(slug=slug)


@dataclass(frozen=True)
class _CreatedRecipe(RecipeRef):
    result: dict | None = None

    def to_public(self):
        return deepcopy(self.result)


@dataclass(frozen=True)
class _MealieStringRef(NamedRef):
    def to_public(self):
        return self.name


@dataclass(frozen=True)
class _CalendarEntry(MealEntry):
    raw: dict | None = None
    result: dict | None = None
