"""Stateful contract-test double, not a Mealie reimplementation.

Supported Mealie v3.22.0 routes: recipes create/html-or-json, recipe GET/PATCH,
recipe search/suggestions, organizers/tags GET/POST, comments and timeline events
POST, mealplans GET/POST/DELETE, shopping lists GET/POST/DELETE, shopping items
create-bulk/PUT/DELETE, and list recipe occurrences POST.
Unsupported: native imports, taxonomy other than tags, and all other endpoints.
Unknown requests raise AssertionError. Shapes follow the Mealie characterization,
store, shopping-items and mealplan tests; ordering metadata stays internal.
"""
import hashlib
import json
from copy import deepcopy
from dataclasses import dataclass
from urllib.parse import urlsplit
from uuid import UUID


@dataclass
class Response:
    body: object
    status_code: int = 200

    @property
    def text(self):
        return json.dumps(self.body)

    def json(self):
        return deepcopy(self.body)


class MealieAPI:
    def __init__(self):
        self.recipes = {}
        self.tags = {}
        self.comments = []
        self.events = []
        self.lists = {}
        self.entries = []
        self.created = {}
        self.updated = {}
        self.made = {}
        self.clock = 0

    def set_rating(self, slug, rating):
        """Seed upstream ratings without adding a production write operation."""
        self.recipes[slug]["rating"] = rating

    def tick(self):
        self.clock += 1
        return self.clock

    def identifier(self):
        return str(UUID(int=self.tick()))

    def page(self, rows, params):
        page, size = params.get("page", 1), params.get("perPage", 100)
        return Response({"items": rows[(page - 1) * size:page * size],
                         "total": len(rows), "page": page, "per_page": size,
                         "total_pages": max(1, (len(rows) + size - 1) // size)})

    def get(self, url, **kwargs):
        path = urlsplit(url).path
        params = kwargs.get("params", {})
        if path == "/api/recipes":
            rows = [r for r in self.recipes.values()
                    if params.get("search", "").casefold() in r["name"].casefold()]
            order = params["orderBy"]
            reverse = params["orderDirection"] == "desc"
            if order == "random":
                rows.sort(key=lambda r: hashlib.sha256(
                    (str(params["paginationSeed"]) + r["id"]).encode()).digest())
            elif order in ("created_at", "update_at", "last_made"):
                values = {"created_at": self.created, "update_at": self.updated,
                          "last_made": self.made}[order]
                present = [r for r in rows if r["slug"] in values]
                missing = [r for r in rows if r["slug"] not in values]
                present.sort(key=lambda r: values[r["slug"]], reverse=reverse)
                rows = present + missing if params.get("orderByNullPosition") == "last" else missing + present
            elif order == "rating":
                rated = [r for r in rows if r.get("rating") is not None]
                unrated = [r for r in rows if r.get("rating") is None]
                rated.sort(key=lambda r: r["rating"], reverse=reverse)
                rows = rated + unrated if params.get("orderByNullPosition") == "last" else unrated + rated
            elif order == "name":
                rows.sort(key=lambda r: r["name"], reverse=reverse)
            else:
                raise AssertionError(f"Unsupported recipe order {order}")
            return self.page(rows, params)
        if path == "/api/recipes/suggestions":
            return Response({"items": [{"recipe": r, "missingFoods": [], "missingTools": []}
                                       for r in list(self.recipes.values())[:params["limit"]]]})
        if path == "/api/households/mealplans":
            return self.page([e for e in self.entries
                              if params["start_date"] <= e["date"] <= params["end_date"]], params)
        if path == "/api/households/shopping/lists":
            return self.page(list(self.lists.values()), params)
        if path.startswith("/api/households/shopping/lists/"):
            lid = path.rsplit("/", 1)[1]
            return Response(self.lists[lid]) if lid in self.lists else Response({}, 404)
        if path == "/api/organizers/tags":
            name = params["search"]
            return Response({"items": [t for t in self.tags.values()
                                       if t["name"].casefold() == name.casefold()]})
        if path.startswith("/api/recipes/"):
            slug = path.removeprefix("/api/recipes/")
            return Response(self.recipes[slug]) if slug in self.recipes else Response({}, 404)
        raise AssertionError(f"Unsupported GET {path}")

    def post(self, url, **kwargs):
        path = urlsplit(url).path
        payload = kwargs["json"]
        if path == "/api/recipes/create/html-or-json":
            data = json.loads(payload["data"])
            slug = data["name"].lower().replace(" ", "-")
            self.recipes[slug] = {
                "id": self.identifier(), "slug": slug, "name": data["name"],
                "tags": [], "description": data.get("description"),
                "recipeIngredient": [{"quantity": None, "food": None, "unit": None,
                                      "originalText": text} for text in data.get("recipeIngredient", [])],
                "recipeInstructions": [{"text": text} for text in data.get("recipeInstructions", [])]}
            self.created[slug] = self.updated[slug] = self.tick()
            return Response(slug, 201)
        if path == "/api/households/mealplans":
            recipe = next((r for r in self.recipes.values()
                           if r["id"] == payload.get("recipeId")), None)
            row = {"id": self.identifier(), **payload, "recipe": deepcopy(recipe)}
            self.entries.append(row)
            return Response(row, 201)
        if path == "/api/households/shopping/lists":
            row = {"id": self.identifier(), "name": payload["name"], "listItems": []}
            self.lists[row["id"]] = row
            return Response(row, 201)
        if path == "/api/households/shopping/items/create-bulk":
            rows = []
            for item in payload:
                row = {"id": self.identifier(), "checked": False, **item}
                self.lists[item["shoppingListId"]]["listItems"].append(row)
                rows.append(row)
            return Response({"createdItems": rows})
        if path.startswith("/api/households/shopping/lists/") and path.endswith("/recipe"):
            lid = path.split("/")[-2]
            if lid not in self.lists:
                return Response({}, 404)
            for occurrence in payload:
                recipe = next(r for r in self.recipes.values() if r["id"] == occurrence["recipeId"])
                for ingredient in recipe["recipeIngredient"]:
                    self.lists[lid]["listItems"].append({
                        "id": self.identifier(), "shoppingListId": lid, "checked": False,
                        "note": ingredient.get("originalText", ""),
                        "quantity": occurrence["recipeIncrementQuantity"]})
            return Response({})
        if path == "/api/organizers/tags":
            name = payload["name"]
            tag = {"id": name.lower(), "slug": name.lower(), "name": name}
            self.tags[tag["id"]] = tag
            return Response(tag, 201)
        if path in ("/api/comments", "/api/recipes/timeline/events"):
            rows = self.comments if path == "/api/comments" else self.events
            row = {"id": str(len(rows) + 1), **payload}
            rows.append(row)
            if path == "/api/recipes/timeline/events":
                slug = next(r["slug"] for r in self.recipes.values() if r["id"] == payload["recipeId"])
                self.made[slug] = self.tick()
            return Response(row, 201)
        raise AssertionError(f"Unsupported POST {path}")

    def patch(self, url, **kwargs):
        path = urlsplit(url).path
        slug = path.removeprefix("/api/recipes/")
        if path.startswith("/api/recipes/") and slug in self.recipes:
            self.recipes[slug].update(json.loads(json.dumps(kwargs["json"])))
            self.updated[slug] = self.tick()
            return Response(self.recipes[slug])
        raise AssertionError(f"Unsupported PATCH {path}")

    def put(self, url, **kwargs):
        if urlsplit(url).path == "/api/households/shopping/items":
            for patch in kwargs["json"]:
                row = next(r for r in self.lists[patch["shoppingListId"]]["listItems"]
                           if r["id"] == patch["id"])
                row.update(deepcopy(patch))
            return Response({})
        raise AssertionError(f"Unsupported PUT {urlsplit(url).path}")

    def delete(self, url, **kwargs):
        path = urlsplit(url).path
        if path == "/api/households/shopping/items":
            params = kwargs["params"]
            ids = params["ids"] if isinstance(params, dict) else [v for k, v in params]
            for row in self.lists.values():
                row["listItems"] = [item for item in row["listItems"] if item["id"] not in ids]
            return Response({}, 204)
        if path.startswith("/api/households/shopping/lists/"):
            lid = path.rsplit("/", 1)[1]
            if lid not in self.lists:
                return Response({}, 404)
            del self.lists[lid]
            return Response({}, 204)
        if path.startswith("/api/households/mealplans/"):
            eid = path.rsplit("/", 1)[1]
            if not any(e["id"] == eid for e in self.entries):
                return Response({}, 404)
            self.entries = [e for e in self.entries if e["id"] != eid]
            return Response({}, 204)
        raise AssertionError(f"Unsupported DELETE {path}")
