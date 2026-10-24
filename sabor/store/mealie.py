"""Instance-owned Mealie transport, mapping and legacy response compatibility."""
import json
import unicodedata
import requests
from typing import Any, cast
from dataclasses import dataclass
from copy import deepcopy

from sabor.core import errors
from sabor.store import (
    WriteResult,
    Ingredient,
    Instruction,
    NamedRef,
    Page,
    Recipe,
    RecipeSummary,
    RecipeRef,
    StoreError,
    Suggestion,
    MealEntry,
)


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
        self.direct_compatibility = session is requests
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

    def get_or_create_tag(self, name):
        result = self._legacy_get_or_create_tag(name)
        _check_legacy_result(result, self.id)
        return _TagResult(result.get("id"), result.get("name"), result=result)

    def tag_recipe(self, slug, tags):
        result = self._legacy_tag_recipe(slug, tags)
        _check_legacy_result(result, self.id)
        return _WriteResult(result=result)

    def add_recipe_comment(self, slug, text):
        result = self._legacy_add_recipe_comment(slug, text)
        _check_legacy_result(result, self.id)
        return _EventId(result.get("comment_id"), result)

    def log_cooking_event(self, slug, subject, message):
        result = self._legacy_log_cooking_event(slug, subject, message)
        _check_legacy_result(result, self.id)
        return _EventId(result.get("event_id"), result)

    def _legacy_get_or_create_tag(self, name: str) -> dict[str, Any]:
        """Busca un tag por nombre (case-insensitive); si no existe, lo crea."""
        try:
            r = self.session.get(
                f"{self.base_url}/api/organizers/tags",
                headers={**self._headers, "Content-Type": "application/json"},
                params={"search": name, "page": 1, "perPage": 100},
                timeout=30,
            )
            if 200 <= r.status_code < 300:
                requested_tag = "".join(
                    char
                    for char in unicodedata.normalize("NFKD", name.casefold())
                    if not unicodedata.combining(char)
                )
                for t in r.json().get("items") or []:
                    tag_names = (t.get("name"), t.get("slug"))
                    if any(
                        isinstance(value, str)
                        and "".join(
                            char
                            for char in unicodedata.normalize("NFKD", value.casefold())
                            if not unicodedata.combining(char)
                        )
                        == requested_tag
                        for value in tag_names
                    ):
                        return {
                            "ok": True,
                            "id": t["id"],
                            "name": t["name"],
                            "slug": t.get("slug"),
                            "created": False,
                        }
            r2 = self.session.post(
                f"{self.base_url}/api/organizers/tags",
                headers={**self._headers, "Content-Type": "application/json"},
                json={"name": name},
                timeout=30,
            )
            if not (200 <= r2.status_code < 300):
                return {"ok": False, "status": r2.status_code, "body": r2.text[:500]}
            data = r2.json()
            return {
                "ok": True,
                "id": data.get("id"),
                "name": data.get("name"),
                "slug": data.get("slug"),
                "created": True,
            }
        except Exception as e:
            return {"ok": False, "error": f"get_or_create_tag_failed: {e}"}

    def _legacy_tag_recipe(self, slug: str, tags: list[str]) -> dict[str, Any]:
        """Asigna etiquetas a una receta. Conserva tags existentes y anade los nuevos."""
        try:
            r = self.session.get(
                f"{self.base_url}/api/recipes/{slug}",
                headers={**self._headers, "Content-Type": "application/json"},
                timeout=30,
            )
            if not (200 <= r.status_code < 300):
                return {
                    "ok": False,
                    "status": r.status_code,
                    "error": f"recipe_not_found: {slug}",
                }
            existing_tags = {
                t["id"]: {
                    "id": t.get("id"),
                    "name": t.get("name"),
                    "slug": t.get("slug"),
                }
                for t in (r.json().get("tags") or [])
                if t.get("id")
            }
            added = []
            for tag_name in tags:
                tag_result = self._legacy_get_or_create_tag(tag_name)
                if not tag_result.get("ok"):
                    return {
                        "ok": False,
                        "error": f"tag_resolution_failed: {tag_name}",
                        "tag_result": tag_result,
                    }
                if not all(tag_result.get(field) for field in ("id", "name", "slug")):
                    return {
                        "ok": False,
                        "error": f"tag_resolution_incomplete: {tag_name}",
                        "tag_result": tag_result,
                    }
                tid = tag_result["id"]
                if tid not in existing_tags:
                    existing_tags[tid] = {
                        "id": tid,
                        "name": tag_result["name"],
                        "slug": tag_result["slug"],
                    }
                    added.append(tag_result["name"])
            all_tags = list(existing_tags.values())
            rp = self.session.patch(
                f"{self.base_url}/api/recipes/{slug}",
                headers={**self._headers, "Content-Type": "application/json"},
                json={"tags": all_tags},
                timeout=30,
            )
            if not (200 <= rp.status_code < 300):
                return {"ok": False, "status": rp.status_code, "body": rp.text[:500]}
            return {
                "ok": True,
                "slug": slug,
                "tags_added": added,
                "total_tags": len(all_tags),
            }
        except Exception as e:
            return {"ok": False, "error": f"tag_recipe_failed: {e}"}

    def _legacy_add_recipe_comment(self, slug: str, text: str) -> dict[str, Any]:
        """Anade un comentario a una receta en Mealie (rating, notas, feedback)."""
        try:
            rr = self.session.get(
                f"{self.base_url}/api/recipes/{slug}",
                headers={**self._headers, "Content-Type": "application/json"},
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
            r = self.session.post(
                f"{self.base_url}/api/comments",
                headers={**self._headers, "Content-Type": "application/json"},
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

    def _legacy_log_cooking_event(
        self, slug: str, subject: str, message: str | None = None
    ) -> dict[str, Any]:
        """Registra un evento en el timeline de una receta (cooking log / historial)."""
        try:
            rr = self.session.get(
                f"{self.base_url}/api/recipes/{slug}",
                headers={**self._headers, "Content-Type": "application/json"},
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
            r = self.session.post(
                f"{self.base_url}/api/recipes/timeline/events",
                headers={**self._headers, "Content-Type": "application/json"},
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
        from sabor.store.mealie_schedule import _schedule
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
                or not 1 <= f.per_page <= 100 or f.sort not in ("name", "created_at", "updated_at", "rating", "last_made", "random")
                or f.direction not in ("asc", "desc")):
            raise StoreError("invalid_argument", self.id)
        params: dict[str, Any] = dict(search=f.query, page=f.page, perPage=f.per_page,
                      orderBy="update_at" if f.sort == "updated_at" else f.sort,
                      orderDirection="asc" if f.sort == "random" else f.direction)
        if f.sort in ("rating", "last_made"):
            params["orderByNullPosition"] = "last"
        if f.sort == "random":
            params["paginationSeed"] = f.seed
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


def _check_legacy_result(result, provider):
    if not result.get("ok"):
        # Tools still return the legacy dict; only the port-level code is refined.
        exc = StoreError("not_found" if result.get("status") == 404 else "upstream_error", provider)
        setattr(exc, "result", result)
        raise exc


@dataclass(frozen=True)
class _TagResult(NamedRef):
    result: dict | None = None


@dataclass(frozen=True)
class _WriteResult(WriteResult):
    result: dict | None = None


class _EventId(str):
    result: dict

    def __new__(cls, value, result):
        obj = super().__new__(cls, value or "")
        obj.result = result
        return obj
