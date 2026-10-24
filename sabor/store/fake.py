"""Instance-local deterministic backend for port contracts, not a production store."""
from dataclasses import dataclass, fields, replace, asdict
from math import isfinite
from hashlib import sha256
from . import *


class FakeRecipeStore:
    id = "fake"
    operations = frozenset(k for k in RecipeStore.__dict__ if not k.startswith("_"))

    def __init__(self):
        self.recipes, self.lists, self.entries, self.tags = {}, {}, {}, {}
        self._serial = 0
        self._created, self._updated, self._last_made = {}, {}, {}
        self.comments, self.cooking_events = {}, {}

    def _id(self):
        self._serial += 1
        return str(self._serial)

    def _get(self, mapping, key):
        if key not in mapping:
            raise StoreError("not_found", self.id)
        return mapping[key]

    def search_recipes(self, filters):
        f = filters
        if (type(f.page) is not int or f.page < 1 or type(f.per_page) is not int
                or not 1 <= f.per_page <= 100 or f.sort not in ("name", "created_at", "updated_at", "rating", "last_made", "random")
                or f.direction not in ("asc", "desc")):
            raise StoreError("invalid_argument", self.id)
        if any(x is not None for x in (f.tag_ids, f.category_ids, f.food_ids, f.tool_ids, f.cookbook_id)):
            raise StoreError("unsupported_operation", self.id)
        if f.sort == "rating":
            raise StoreError("unsupported_operation", self.id)
        rows = sorted((RecipeSummary(r.id, r.name, r.slug) for r in self.recipes.values()
                       if f.query.casefold() in (r.name or "").casefold()),
                      key=lambda r: r.name or "", reverse=f.direction == "desc")
        if f.sort in ("created_at", "updated_at", "last_made"):
            times = {"created_at": self._created, "updated_at": self._updated,
                     "last_made": self._last_made}[f.sort]
            rows.sort(key=lambda r: (times.get(r.slug, 0), self._created[r.slug]),
                      reverse=f.direction == "desc")
            rows.sort(key=lambda r: r.slug not in times)
        elif f.sort == "random":
            rows.sort(key=lambda r: sha256(((f.seed or "") + (r.slug or "")).encode()).digest())
        start = (f.page - 1) * f.per_page
        return Page(tuple(rows[start:start + f.per_page]), len(rows), f.page,
                    f.per_page, (len(rows) + f.per_page - 1) // f.per_page)

    def get_recipe(self, slug):
        return self._get(self.recipes, slug)

    def create_recipe(self, draft):
        slug = draft.slug or self._id()
        if slug in self.recipes:
            raise StoreError("conflict", self.id)
        self.recipes[slug] = replace(draft, id=draft.id or self._id(), slug=slug)
        self._created[slug] = self._updated[slug] = int(self._id())
        return RecipeRef(self.recipes[slug].id, draft.name, slug)

    def update_recipe(self, slug, patch):
        recipe = self.get_recipe(slug)
        editable = {f.name for f in fields(Recipe)} - {"id", "slug"}
        if not patch.changes or patch.changes.keys() - editable:
            raise StoreError("invalid_argument", self.id)
        self.recipes[slug] = replace(recipe, **patch.changes)
        self._updated[slug] = int(self._id())
        return RecipeRef(recipe.id, self.recipes[slug].name, slug)

    def suggest_recipes(self, query):
        if (
            type(query.limit) is not int
            or query.limit < 1
            or any(
                type(value) is not int or value < 0
                for value in (query.max_missing_foods, query.max_missing_tools)
            )
        ):
            raise StoreError("invalid_argument", self.id)
        rows = []
        for recipe in self.recipes.values():
            missing = tuple(
                i.food
                for i in recipe.recipeIngredient or ()
                if i.food and i.food.id not in query.foods
            )
            if len(missing) <= query.max_missing_foods:
                rows.append(
                    Suggestion(recipe.id, recipe.name, recipe.slug, missing, ())
                )
        return rows[: query.limit]

    def get_mealplan(self, dates):
        return [e for e in self.entries.values() if dates.start_date <= e.date <= dates.end_date]

    def set_mealplan_entry(self, entry):
        recipe = self.get_recipe(entry.recipe_slug) if entry.recipe_slug else None
        result = MealEntry(entry.date, entry.entry_type, entry.title, entry.recipe_slug,
                           self._id(), recipe.id if recipe else None)
        self.entries[result.id] = result
        return result

    def delete_mealplan_entry(self, entry_id):
        self._get(self.entries, entry_id)
        del self.entries[entry_id]

    def get_shopping_lists(self):
        return [ListSummary(k, name, len(items)) for k, (name, items) in self.lists.items()]

    def create_shopping_list(self, name):
        key = self._id()
        self.lists[key] = (name, {})
        return ListRef(key, name)

    def delete_shopping_list(self, list_id):
        self._get(self.lists, list_id)
        del self.lists[list_id]

    def validate_shopping_list_id(self, list_id, *, adding=False):
        if not isinstance(list_id, str) or not list_id:
            raise StoreError("invalid_argument", self.id)
        return list_id

    def validate_recipe_id(self, recipe_id):
        return recipe_id

    def shopping_list_preview(self, list_id):
        (name, items) = self._get(self.lists, list_id)
        rows = [asdict(item) for item in items.values()]
        return {"name": name, "items_to_remove": rows, "count": len(rows)}

    def clear_shopping_list_items(self, list_id):
        items = self._get(self.lists, list_id)[1]
        count = len(items)
        items.clear()
        if not count:
            return {"ok": True, "deleted": 0, "message": "Lista ya estaba vacia"}
        return {"ok": True, "deleted": count, "status": 200}

    def get_shopping_items(self, list_id):
        return ShoppingSnapshot(list_id, tuple(self._get(self.lists, list_id)[1].values()))

    def add_shopping_items(self, list_id, items):
        target = self._get(self.lists, list_id)[1]
        rows = [ShoppingItem(i.note, i.display, i.quantity, i.food, i.unit,
                             self._id(), list_id) for i in items]
        target.update((r.id, r) for r in rows)
        return WriteResult(tuple(r.id for r in rows))

    def add_recipe_occurrences(self, list_id, occurrences):
        target = self._get(self.lists, list_id)[1]
        for o in occurrences:
            self.get_recipe(o.slug)
            if not isfinite(o.multiplier) or not 0 < o.multiplier <= 20:
                raise StoreError("invalid_argument", self.id)
        rows = [ShoppingItem(id=self._id(), list_id=list_id, quantity=o.multiplier,
                            recipe_references=(o,)) for o in occurrences]
        target.update((r.id, r) for r in rows)
        return WriteResult(tuple(r.id for r in rows))

    def _selection(self, list_id, ids):
        target = self._get(self.lists, list_id)[1]
        if not ids or len(set(ids)) != len(ids) or any(i not in target for i in ids):
            raise StoreError("invalid_argument", self.id)
        return target

    def check_shopping_items(self, list_id, ids, checked):
        target = self._selection(list_id, ids)
        if type(checked) is not bool:
            raise StoreError("invalid_argument", self.id)
        target.update((i, replace(target[i], checked=checked)) for i in ids)
        return WriteResult(tuple(ids))

    def remove_shopping_items(self, list_id, ids):
        target = self._selection(list_id, ids)
        for i in ids:
            del target[i]
        return WriteResult(tuple(ids))

    def get_or_create_tag(self, name):
        if name in self.tags:
            return replace(self.tags[name], created=False)
        tag = _Tag(self._id(), name, name.casefold().replace(" ", "-"), True)
        self.tags[name] = tag
        return tag

    def tag_recipe(self, slug, tags):
        recipe = self.get_recipe(slug)
        existing = {tag.id: tag for tag in recipe.tags or ()}
        added = []
        for name in tags:
            tag = self.get_or_create_tag(name)
            if tag.id not in existing:
                existing[tag.id] = tag
                added.append(tag.name)
        self.recipes[slug] = replace(recipe, tags=tuple(existing.values()))
        return _Tagged(
            (recipe.id,),
            {
                "ok": True,
                "slug": slug,
                "tags_added": added,
                "total_tags": len(existing),
            },
        )

    def add_recipe_comment(self, slug, text):
        self.get_recipe(slug)
        key = self._id()
        self.comments[key] = (slug, text)
        return key

    def log_cooking_event(self, slug, subject, message):
        self.get_recipe(slug)
        key = self._id()
        self.cooking_events[key] = (slug, subject, message)
        self._last_made[slug] = int(key)
        return key


@dataclass(frozen=True)
class _Tag(Tag):
    slug: str | None = None
    created: bool = False


@dataclass(frozen=True)
class _Tagged(WriteResult):
    result: dict | None = None
