"""Instance-owned SQLite recipe store with short-lived transactional connections."""
from contextlib import contextmanager
from dataclasses import asdict, dataclass, fields, replace
from decimal import Decimal
from datetime import datetime, timezone
from hashlib import sha256
import json
from math import isfinite
from pathlib import Path
import sqlite3
import threading
import unicodedata

from . import (NamedRef, Ingredient, Instruction, Recipe, RecipeOccurrence,
               ShoppingItem, RecipeStore, StoreError, RecipeRef, RecipeSummary,
               Page, Suggestion, MealEntry, ListSummary, ListRef, ShoppingSnapshot,
               WriteResult, Tag)
from .slugs import next_slug
from .sqlite_schema import migrate
from sabor.pricing.quantities import from_structured, ingredient_name, to_base


def _normal(text):
    return " ".join("".join(c for c in unicodedata.normalize("NFD", text.casefold())
                            if not unicodedata.combining(c)).split())


def _json(value):
    return json.dumps(asdict(value), ensure_ascii=False)


def _decode(payload):
    try:
        return json.loads(payload)
    except (ValueError, TypeError):
        raise StoreError("invalid_response", "sqlite") from None


def _ref(value):
    return NamedRef(**value) if value is not None else None


def _ingredient(value):
    return Ingredient(**{**value, "food": _ref(value.get("food")),
                         "unit": _ref(value.get("unit"))})


def _recipe(payload):
    value = _decode(payload)
    for key in ("tags", "recipeCategory"):
        if value[key] is not None:
            value[key] = tuple(NamedRef(v.get("id"), v.get("name")) for v in value[key])
    if value["recipeIngredient"] is not None:
        value["recipeIngredient"] = tuple(_ingredient(v) for v in value["recipeIngredient"])
    if value["recipeInstructions"] is not None:
        value["recipeInstructions"] = tuple(Instruction(**v) for v in value["recipeInstructions"])
    return Recipe(**value)


def _item(payload):
    value = _decode(payload)
    value["food"], value["unit"] = _ref(value["food"]), _ref(value["unit"])
    if value["recipe_references"] is not None:
        value["recipe_references"] = tuple(RecipeOccurrence(**v) for v in value["recipe_references"])
    return ShoppingItem(**value)


@dataclass(frozen=True)
class _Tag(Tag):
    slug: str | None = None
    created: bool = False


@dataclass(frozen=True)
class _Tagged(WriteResult):
    result: dict | None = None


class SqliteRecipeStore:
    id = "sqlite"
    operations = frozenset(k for k in RecipeStore.__dict__ if not k.startswith("_"))

    def __init__(self, path):
        self.path = Path(path)
        self._ready = False
        self._initialization_lock = threading.Lock()

    def ensure_ready(self):
        """Initialize once per instance; startup callers handle raw I/O errors."""
        with self._initialization_lock:
            if not self._ready:
                directory = self.path.parent
                directory.mkdir(parents=True, exist_ok=True)
                with self._connection() as connection:
                    migrate(connection)
                self._ready = True

    @contextmanager
    def _connect(self):
        try:
            self.ensure_ready()
            with self._connection() as db:
                yield db
        except (OSError, sqlite3.Error):
            raise StoreError("upstream_error", self.id) from None

    @contextmanager
    def _connection(self):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            db.execute("PRAGMA busy_timeout=10000")
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("PRAGMA journal_mode=WAL")
            with db:
                yield db
        finally:
            db.close()

    def _id(self, db):
        return str(db.execute("INSERT INTO serial DEFAULT VALUES").lastrowid)

    def _name_slug(self, db, name):
        """New recipes get a name slug, suffixed -2, -3... on collision (#49).

        Without a usable name the numeric identifier is kept. Existing recipes and
        their slugs are never rewritten.
        """
        return next_slug(name, lambda slug: db.execute("SELECT 1 FROM recipes WHERE slug=?", (slug,)).fetchone(),
                         lambda: self._id(db))

    def _get_recipe(self, db, slug):
        row = db.execute("SELECT payload FROM recipes WHERE slug=?", (slug,)).fetchone()
        if row is None:
            raise StoreError("not_found", self.id)
        return _recipe(row[0])

    def get_recipe(self, slug):
        with self._connect() as db:
            return self._get_recipe(db, slug)

    def _save_recipe(self, db, recipe):
        columns = {r[1] for r in db.execute("PRAGMA table_info(recipes)")}
        if "updated_at" in columns:
            db.execute("UPDATE recipes SET updated_at=? WHERE slug=?",
                       (datetime.now(timezone.utc).isoformat(), recipe.slug))
        db.execute("UPDATE recipes SET payload=? WHERE slug=?", (_json(recipe), recipe.slug))
        db.execute("DELETE FROM ingredients WHERE recipe_slug=?", (recipe.slug,))
        db.execute("DELETE FROM instructions WHERE recipe_slug=?", (recipe.slug,))
        for position, item in enumerate(recipe.recipeIngredient or ()):
            db.execute("INSERT INTO ingredients VALUES (?, ?, ?, ?)",
                       (recipe.slug, position, item.originalText, _json(item)))
        for position, item in enumerate(recipe.recipeInstructions or ()):
            db.execute("INSERT INTO instructions VALUES (?, ?, ?)",
                       (recipe.slug, position, _json(item)))
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='recipe_search'").fetchone():
            db.execute("DELETE FROM recipe_search WHERE slug=?", (recipe.slug,))
            text = " ".join(" ".join(filter(None, (i.originalText, i.note, i.display,
                            i.food.name if i.food else None))) for i in recipe.recipeIngredient or ())
            db.execute("INSERT INTO recipe_search VALUES (?, ?, ?, ?)",
                       (recipe.slug, recipe.name, recipe.description, text))

    def create_recipe(self, draft):
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            slug = draft.slug or self._name_slug(db, draft.name)
            if db.execute("SELECT 1 FROM recipes WHERE slug=?", (slug,)).fetchone():
                raise StoreError("conflict", self.id)
            recipe = Recipe(**{f.name: getattr(draft, f.name) for f in fields(Recipe)})
            recipe = replace(recipe, slug=slug, id=draft.id or self._id(db))
            db.execute("INSERT INTO recipes (slug, id, payload) VALUES (?, ?, ?)", (slug, recipe.id, _json(recipe)))
            self._save_recipe(db, recipe)
            if "created_at" in {r[1] for r in db.execute("PRAGMA table_info(recipes)")}:
                db.execute("UPDATE recipes SET created_at=updated_at WHERE slug=? AND created_at IS NULL", (slug,))
            return RecipeRef(recipe.id, recipe.name, slug)

    def update_recipe(self, slug, patch):
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            recipe = self._get_recipe(db, slug)
            editable = {f.name for f in fields(Recipe)} - {"id", "slug"}
            if not patch.changes or patch.changes.keys() - editable:
                raise StoreError("invalid_argument", self.id)
            recipe = replace(recipe, **patch.changes)
            self._save_recipe(db, recipe)
            return RecipeRef(recipe.id, recipe.name, slug)

    def normalize_imported(self, slug):
        """Fill unstructured import rows atomically, retaining all source metadata."""
        from sabor.importing.ingredients_es import normalize_row

        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            recipe = self._get_recipe(db, slug)
            originals = recipe.recipeIngredient
            if not originals:
                return {"ok": True, "slug": slug, "status": "skipped_no_ingredients"}
            merged = []
            accepted = 0
            for row in originals:
                if (row.quantity is not None or row.food is not None or row.unit is not None
                        or not isinstance(row.originalText, str)):
                    merged.append(row)
                    continue
                rows = normalize_row(row)
                if rows is None:
                    merged.append(row)
                    continue
                # An enumeration ('2 g de harina y azúcar') yields one row per food.
                merged.extend(rows)
                accepted += 1
            if accepted:
                self._save_recipe(db, replace(recipe, recipeIngredient=tuple(merged)))
            return {"ok": True, "slug": slug,
                    "status": "normalized" if accepted else "retained_originals",
                    "accepted": accepted}

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
        with self._connect() as db:
            if f.query.strip():
                # Quote the entire user phrase: punctuation is never FTS syntax.
                phrase = '"' + f.query.replace('"', '""') + '"'
                rows = db.execute("SELECT r.payload FROM recipes r JOIN recipe_search s ON s.slug=r.slug WHERE recipe_search MATCH ?", (phrase,)).fetchall()
                # Fake's substring-name semantics also protect import duplicate checks.
                names = db.execute("SELECT payload FROM recipes").fetchall()
                rows = {r[0]: r for r in rows}
                rows.update({r[0]: r for r in names if _normal(f.query) in _normal(_recipe(r[0]).name or "")})
                rows = rows.values()
            else:
                rows = db.execute("SELECT payload FROM recipes ORDER BY rowid").fetchall()
            recipes = sorted((_recipe(r[0]) for r in rows), key=lambda r: r.name or "",
                             reverse=f.direction == "desc")
            if f.sort in ("created_at", "updated_at", "last_made"):
                metadata = {r[0]: r[1:] for r in db.execute(
                    "SELECT slug, rowid, created_at, updated_at, "
                    "(SELECT max(created_at) FROM cooking_events WHERE recipe_slug=recipes.slug), "
                    "(SELECT rowid FROM cooking_events WHERE recipe_slug=recipes.slug "
                    "ORDER BY created_at DESC, rowid DESC LIMIT 1) FROM recipes")}
                index = {"created_at": 1, "updated_at": 2, "last_made": 3}[f.sort]
                # Migration v3 stamped old events with one time: the latest event breaks ties.
                tie = 4 if f.sort == "last_made" else 0
                # Recipes without events fall back to recipe insertion order (stable paging).
                recipes.sort(key=lambda r: (metadata[r.slug][index] or "",
                                            metadata[r.slug][tie] if metadata[r.slug][tie] is not None
                                            else metadata[r.slug][0]),
                             reverse=f.direction == "desc")
                recipes.sort(key=lambda r: metadata[r.slug][index] is None)
            elif f.sort == "random":
                recipes.sort(key=lambda r: sha256(((f.seed or "") + (r.slug or "")).encode()).digest())
            start = (f.page - 1) * f.per_page
            return Page(tuple(RecipeSummary(r.id, r.name, r.slug) for r in recipes[start:start + f.per_page]),
                        len(recipes), f.page, f.per_page, (len(recipes) + f.per_page - 1) // f.per_page)

    def suggest_recipes(self, query):
        if (type(query.limit) is not int or query.limit < 1 or any(
                type(v) is not int or v < 0 for v in (query.max_missing_foods, query.max_missing_tools))):
            raise StoreError("invalid_argument", self.id)
        foods = {_normal(f) for f in query.foods}
        with self._connect() as db:
            result = []
            for row in db.execute("SELECT payload FROM recipes ORDER BY rowid"):
                recipe = _recipe(row[0])
                missing = []
                for i in recipe.recipeIngredient or ():
                    name = i.food.name if i.food and i.food.name else ingredient_name(i.originalText or i.note or i.display)
                    if name and _normal(name) not in foods:
                        missing.append(i.food or NamedRef(name=name))
                if len(missing) <= query.max_missing_foods:
                    result.append(Suggestion(recipe.id, recipe.name, recipe.slug, tuple(missing), ()))
            return result[:query.limit]

    def get_mealplan(self, dates):
        with self._connect() as db:
            entries = [MealEntry(**_decode(r[0])) for r in db.execute("SELECT payload FROM meal_entries ORDER BY rowid")]
            return [e for e in entries if dates.start_date <= e.date <= dates.end_date]

    def set_mealplan_entry(self, entry):
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            recipe = self._get_recipe(db, entry.recipe_slug) if entry.recipe_slug else None
            result = MealEntry(entry.date, entry.entry_type, entry.title, entry.recipe_slug,
                               self._id(db), recipe.id if recipe else None)
            db.execute("INSERT INTO meal_entries VALUES (?, ?, ?)", (result.id, entry.recipe_slug, _json(result)))
            return result

    def delete_mealplan_entry(self, entry_id):
        with self._connect() as db:
            if not db.execute("DELETE FROM meal_entries WHERE id=?", (entry_id,)).rowcount:
                raise StoreError("not_found", self.id)

    def _list(self, db, list_id):
        row = db.execute("SELECT name FROM shopping_lists WHERE id=?", (list_id,)).fetchone()
        if row is None:
            raise StoreError("not_found", self.id)
        return row[0]

    def get_shopping_lists(self):
        with self._connect() as db:
            return [ListSummary(*r) for r in db.execute("SELECT l.id, l.name, count(i.id) FROM shopping_lists l LEFT JOIN shopping_items i ON i.list_id=l.id GROUP BY l.id ORDER BY l.rowid")]

    def create_shopping_list(self, name):
        with self._connect() as db:
            key = self._id(db)
            db.execute("INSERT INTO shopping_lists VALUES (?, ?)", (key, name))
            return ListRef(key, name)

    def delete_shopping_list(self, list_id):
        with self._connect() as db:
            if not db.execute("DELETE FROM shopping_lists WHERE id=?", (list_id,)).rowcount:
                raise StoreError("not_found", self.id)

    def validate_shopping_list_id(self, list_id, *, adding=False):
        if not isinstance(list_id, str) or not list_id:
            raise StoreError("invalid_argument", self.id)
        return list_id

    def validate_recipe_id(self, recipe_id):
        return recipe_id

    def _items(self, db, list_id):
        self._list(db, list_id)
        return [_item(r[0]) for r in db.execute("SELECT payload FROM shopping_items WHERE list_id=? ORDER BY rowid", (list_id,))]

    def get_shopping_items(self, list_id):
        with self._connect() as db:
            return ShoppingSnapshot(list_id, tuple(self._items(db, list_id)))

    def shopping_list_preview(self, list_id):
        with self._connect() as db:
            name = self._list(db, list_id)
            rows = [asdict(i) for i in self._items(db, list_id)]
            return {"name": name, "items_to_remove": rows, "count": len(rows)}

    def clear_shopping_list_items(self, list_id):
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._list(db, list_id)
            count = db.execute("DELETE FROM shopping_items WHERE list_id=?", (list_id,)).rowcount
            if not count:
                return {"ok": True, "deleted": 0, "message": "Lista ya estaba vacia"}
            return {"ok": True, "deleted": count, "status": 200}

    def _save_item(self, db, item):
        db.execute("INSERT INTO shopping_items VALUES (?, ?, ?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload",
                   (item.id, item.list_id, _json(item)))

    def add_shopping_items(self, list_id, items):
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._list(db, list_id)
            rows = [ShoppingItem(i.note, i.display, i.quantity, i.food, i.unit,
                                 self._id(db), list_id) for i in items]
            for row in rows:
                self._save_item(db, row)
            return WriteResult(tuple(r.id for r in rows))

    def add_recipe_occurrences(self, list_id, occurrences):
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = self._items(db, list_id)
            recipes = []
            for o in occurrences:
                recipe = self._get_recipe(db, o.slug)
                if not isfinite(o.multiplier) or not 0 < o.multiplier <= 20:
                    raise StoreError("invalid_argument", self.id)
                recipes.append((RecipeOccurrence(o.slug, o.multiplier), recipe))
            changed = []
            for o, recipe in recipes:
                for i in recipe.recipeIngredient or ():
                    text = i.originalText or i.display or i.note or ""
                    name = i.food.name if i.food and i.food.name else ingredient_name(text)
                    q = to_base(from_structured(i.quantity, i.unit.name if i.unit else None, name, text))
                    mergeable = bool(name and q.amount is not None and q.dimension and not q.ambiguity)
                    match = next((r for r in existing if mergeable and not r.checked
                                  and r.recipe_references and r.food and r.unit
                                  and _normal(r.food.name or "") == _normal(name)
                                  and r.unit.name == q.unit and r.quantity is not None), None)
                    quantity = None
                    if mergeable:
                        assert q.amount is not None
                        quantity = float(q.amount * Decimal(str(o.multiplier)))
                    if match:
                        row = replace(match, quantity=float(Decimal(str(match.quantity)) + Decimal(str(quantity))),
                                      note=((match.note or "") + "\n" + text),
                                      recipe_references=(match.recipe_references or ()) + (o,))
                        existing[existing.index(match)] = row
                    else:
                        row = ShoppingItem(note=text, display=text, quantity=quantity,
                                           food=NamedRef(name=name) if name else i.food,
                                           unit=NamedRef(name=q.unit) if mergeable else None,
                                           id=self._id(db), list_id=list_id, recipe_references=(o,))
                        existing.append(row)
                    if mergeable:
                        position = existing.index(row)
                        row = replace(row, display=f"{row.quantity:g} {q.unit} {name}")
                        existing[position] = row
                    self._save_item(db, row)
                    changed.append(row.id)
            return WriteResult(tuple(dict.fromkeys(changed)))

    def _selection(self, db, list_id, ids):
        target = {i.id: i for i in self._items(db, list_id)}
        if not ids or len(set(ids)) != len(ids) or any(i not in target for i in ids):
            raise StoreError("invalid_argument", self.id)
        return target

    def check_shopping_items(self, list_id, ids, checked):
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            target = self._selection(db, list_id, ids)
            if type(checked) is not bool:
                raise StoreError("invalid_argument", self.id)
            for key in ids:
                self._save_item(db, replace(target[key], checked=checked))
            return WriteResult(tuple(ids))

    def remove_shopping_items(self, list_id, ids):
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._selection(db, list_id, ids)
            db.executemany("DELETE FROM shopping_items WHERE id=?", [(i,) for i in ids])
            return WriteResult(tuple(ids))

    def _tag(self, db, name):
        row = db.execute("SELECT payload FROM tags WHERE name=?", (name,)).fetchone()
        if row:
            try:
                return replace(_Tag(**json.loads(row[0])), created=False)
            except (ValueError, TypeError):
                raise StoreError("invalid_response", self.id) from None
        tag = _Tag(self._id(db), name, name.casefold().replace(" ", "-"), True)
        db.execute("INSERT INTO tags VALUES (?, ?, ?)", (tag.id, name, _json(tag)))
        return tag

    def get_or_create_tag(self, name):
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            return self._tag(db, name)

    def tag_recipe(self, slug, tags):
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            recipe = self._get_recipe(db, slug)
            existing = {t.id: t for t in recipe.tags or ()}
            added = []
            for name in tags:
                tag = self._tag(db, name)
                if tag.id not in existing:
                    existing[tag.id] = tag
                    added.append(tag.name)
            self._save_recipe(db, replace(recipe, tags=tuple(existing.values())))
            assert recipe.id is not None
            return _Tagged((recipe.id,), {"ok": True, "slug": slug, "tags_added": added,
                                          "total_tags": len(existing)})

    def add_recipe_comment(self, slug, text):
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._get_recipe(db, slug)
            key = self._id(db)
            db.execute("INSERT INTO comments VALUES (?, ?, ?)", (key, slug, text))
            return key

    def log_cooking_event(self, slug, subject, message):
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._get_recipe(db, slug)
            key = self._id(db)
            db.execute("INSERT INTO cooking_events (id, recipe_slug, subject, message, created_at) VALUES (?, ?, ?, ?, ?)",
                       (key, slug, subject, message, datetime.now(timezone.utc).isoformat()))
            return key
