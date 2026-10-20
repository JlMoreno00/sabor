"""Instance-local deterministic backend for port contracts, not a production store."""
from dataclasses import fields, replace
from math import isfinite
from . import *


class FakeRecipeStore:
    id = "fake"
    operations = frozenset(k for k in RecipeStore.__dict__
                           if not k.startswith("_") and k != "suggest_recipes")

    def __init__(self):
        self.recipes, self.lists, self.entries, self.tags = {}, {}, {}, {}
        self._serial = 0
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
                or not 1 <= f.per_page <= 100 or f.sort != "name"
                or f.direction not in ("asc", "desc")):
            raise StoreError("invalid_argument", self.id)
        if any(x is not None for x in (f.tag_ids, f.category_ids, f.food_ids, f.tool_ids, f.cookbook_id)):
            raise StoreError("unsupported_operation", self.id)
        rows = sorted((RecipeSummary(r.id, r.name, r.slug) for r in self.recipes.values()
                       if f.query.casefold() in (r.name or "").casefold()),
                      key=lambda r: r.name or "", reverse=f.direction == "desc")
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
        return RecipeRef(self.recipes[slug].id, draft.name, slug)

    def update_recipe(self, slug, patch):
        recipe = self.get_recipe(slug)
        editable = {f.name for f in fields(Recipe)} - {"id", "slug"}
        if not patch.changes or patch.changes.keys() - editable:
            raise StoreError("invalid_argument", self.id)
        self.recipes[slug] = replace(recipe, **patch.changes)
        return RecipeRef(recipe.id, self.recipes[slug].name, slug)

    def suggest_recipes(self, query):
        raise StoreError("unsupported_operation", self.id)

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
        return self.tags.setdefault(name, Tag(self._id(), name))

    def tag_recipe(self, slug, tags):
        recipe = self.get_recipe(slug)
        self.recipes[slug] = replace(recipe, tags=tuple(self.get_or_create_tag(t) for t in tags))
        return WriteResult((recipe.id,))

    def add_recipe_comment(self, slug, text):
        self.get_recipe(slug)
        key = self._id()
        self.comments[key] = (slug, text)
        return key

    def log_cooking_event(self, slug, subject, message):
        self.get_recipe(slug)
        key = self._id()
        self.cooking_events[key] = (slug, subject, message)
        return key
