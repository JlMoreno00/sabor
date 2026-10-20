"""Instance-owned Mealie transport and recipe read mapping.

Write/calendar/list support is added in subsequent bounded migration slices.
"""
import requests
from typing import Any, cast

from sabor.core import errors
from sabor.store import (Ingredient, Instruction, NamedRef, Page, Recipe,
                         RecipeSummary, StoreError, Suggestion)


def _object(value):
    if not isinstance(value, dict):
        raise ValueError("Expected object")
    return value


def _items(value):
    rows = _object(value).get("items")
    if not isinstance(rows, list):
        raise ValueError("Expected items")
    return [_object(row) for row in rows]


def _ref(value):
    if value is None:
        return None
    value = _object(value)
    return NamedRef(value.get("id"), value.get("name"))


def _refs(value):
    if value is None:
        return None
    if not isinstance(value, list):
        raise ValueError("Expected references")
    return tuple(NamedRef(_object(row).get("id"), row.get("name")) for row in value)


def _summary(value):
    return {key: value.get(key) for key in ("id", "name", "slug")}


class MealieRecipeStore:
    id = "mealie"
    operations = frozenset(("search_recipes", "get_recipe", "suggest_recipes"))

    def __init__(self, base_url, token, session=None):
        self.base_url = base_url.rstrip("/")
        self.session = session if session is not None else requests.Session()
        self._headers = {"Authorization": f"Bearer {token}"}

    def _unsupported(self, *args, **kwargs):
        raise StoreError("unsupported_operation", self.id)

    # Explicitly unavailable until their bounded migration units land.
    create_recipe = _unsupported
    update_recipe = _unsupported
    get_mealplan = _unsupported
    set_mealplan_entry = _unsupported
    delete_mealplan_entry = _unsupported
    get_shopping_lists = _unsupported
    create_shopping_list = _unsupported
    delete_shopping_list = _unsupported
    get_shopping_items = _unsupported
    add_shopping_items = _unsupported
    add_recipe_occurrences = _unsupported
    check_shopping_items = _unsupported
    remove_shopping_items = _unsupported
    get_or_create_tag = _unsupported
    tag_recipe = _unsupported
    add_recipe_comment = _unsupported
    log_cooking_event = _unsupported

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
            return [Suggestion(**_summary(_object(row.get("recipe"))),
                               missingFoods=_refs(row.get("missingFoods")),
                               missingTools=_refs(row.get("missingTools")))
                    for row in _items(source)]

        return self._read("/suggestions", suggestions, params=params)
