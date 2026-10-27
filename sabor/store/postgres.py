"""Instance-owned Postgres recipe store with serialized transactions."""
from contextlib import contextmanager
from dataclasses import asdict, dataclass, fields, replace
from decimal import Decimal
from datetime import datetime, timezone
from hashlib import sha256
import json
from math import isfinite
from functools import wraps
from typing import Any
import re
import threading
import unicodedata

from . import (NamedRef, Ingredient, Instruction, Recipe, RecipeOccurrence,
               ShoppingItem, RecipeStore, StoreError, RecipeRef, RecipeSummary,
               Page, Suggestion, MealEntry, ListSummary, ListRef, ShoppingSnapshot,
               WriteResult, Tag)
from .postgres_schema import migrate
from sabor.pricing.quantities import from_structured, ingredient_name, to_base


def _normal(text):
    return " ".join("".join(c for c in unicodedata.normalize("NFD", text.casefold())
                            if not unicodedata.combining(c)).split())


def _search_text(text):
    text = "".join(c for c in unicodedata.normalize("NFKD", text.casefold())
                   if not unicodedata.combining(c))
    tokens = re.findall(r"[^\W_]+", text)
    return " " + " ".join(tokens) + " "


def _json(value):
    return json.dumps(asdict(value), ensure_ascii=False)


def _decode(payload):
    try:
        return json.loads(payload)
    except (ValueError, TypeError):
        raise StoreError("invalid_response", "postgres") from None


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


class PostgresRecipeStore:
    id = "postgres"
    operations = frozenset(k for k in RecipeStore.__dict__ if not k.startswith("_"))

    def __init__(self, dsn, *, schema='sabor'):
        if not isinstance(schema, str) or not re.fullmatch(r'[a-z_][a-z0-9_]{0,62}', schema):
            raise ValueError('Invalid Postgres schema identifier')
        try:
            import psycopg
        except ImportError:
            from sabor.core.config import ConfigError
            raise ConfigError('Postgres store requires the optional extra: install sabor-mcp[postgres]') from None
        self._driver = psycopg
        self._dsn = dsn
        self.schema = schema
        self._ready = False
        self._connection: Any = None
        self._lock = threading.Lock()

    def _open(self):
        from psycopg import sql
        from psycopg.types.json import set_json_loads
        connection = self._driver.connect(self._dsn, connect_timeout=10, autocommit=True)
        try:
            # Keep the shared dataclass codecs working with serialized JSON.
            set_json_loads(lambda value: value if isinstance(value, str) else value.decode(), connection)
            connection.execute(sql.SQL('SET search_path TO {}, pg_catalog').format(sql.Identifier(self.schema)))
        except BaseException:
            connection.close()
            raise
        self._connection = connection

    def _ensure_ready(self):
        if self._connection is None or self._connection.closed:
            self._open()
        if not self._ready:
            migrate(self._connection, self.schema)
            self._ready = True

    def ensure_ready(self):
        with self._lock:
            try:
                self._ensure_ready()
            except self._driver.Error:
                raise StoreError('upstream_error', self.id) from None

    def schema_version(self):
        with self._connect() as db:
            return db.execute('SELECT MAX(version) FROM schema_version').fetchone()[0]

    def probe_schema_version(self):
        """Read-only diagnostics: connect, never create the schema nor migrate."""
        from psycopg import sql
        with self._lock:
            try:
                if self._connection is None or self._connection.closed:
                    self._open()
                table = self._connection.execute('SELECT to_regclass(%s)', (
                    sql.Identifier(self.schema, 'schema_version').as_string(self._connection),)).fetchone()[0]
                if table is None:
                    return None
                return self._connection.execute('SELECT MAX(version) FROM schema_version').fetchone()[0] or 0
            except self._driver.Error:
                raise StoreError('upstream_error', self.id) from None

    def close(self):
        with self._lock:
            if self._connection is not None:
                self._connection.close()
                self._connection = None

    @contextmanager
    def _connect(self):
        # The public operation wrapper owns the lock and transaction.
        yield self._connection

    def _id(self, db):
        return str(db.execute("INSERT INTO serial DEFAULT VALUES RETURNING id").fetchone()[0])

    def _get_recipe(self, db, slug):
        row = db.execute("SELECT payload FROM recipes WHERE slug=%s", (slug,)).fetchone()
        if row is None:
            raise StoreError("not_found", self.id)
        return _recipe(row[0])

    def get_recipe(self, slug):
        with self._connect() as db:
            return self._get_recipe(db, slug)

    def _save_recipe(self, db, recipe):
        db.execute("UPDATE recipes SET updated_at=%s WHERE slug=%s",
                   (datetime.now(timezone.utc).isoformat(), recipe.slug))
        db.execute("UPDATE recipes SET payload=%s WHERE slug=%s", (_json(recipe), recipe.slug))
        db.execute("DELETE FROM ingredients WHERE recipe_slug=%s", (recipe.slug,))
        db.execute("DELETE FROM instructions WHERE recipe_slug=%s", (recipe.slug,))
        for position, item in enumerate(recipe.recipeIngredient or ()):
            db.execute("INSERT INTO ingredients VALUES (%s, %s, %s, %s)",
                       (recipe.slug, position, item.originalText, _json(item)))
        for position, item in enumerate(recipe.recipeInstructions or ()):
            db.execute("INSERT INTO instructions VALUES (%s, %s, %s)",
                       (recipe.slug, position, _json(item)))
        text = " ".join(filter(None, (recipe.name, recipe.description,
                        *(i.originalText for i in recipe.recipeIngredient or ()))))
        db.execute("UPDATE recipes SET search_text=%s WHERE slug=%s",
                   (_search_text(text), recipe.slug))

    def create_recipe(self, draft):
        with self._connect() as db:
            slug = draft.slug or self._id(db)
            if db.execute("SELECT 1 FROM recipes WHERE slug=%s", (slug,)).fetchone():
                raise StoreError("conflict", self.id)
            recipe = Recipe(**{f.name: getattr(draft, f.name) for f in fields(Recipe)})
            recipe = replace(recipe, slug=slug, id=draft.id or self._id(db))
            db.execute("INSERT INTO recipes (slug, id, payload) VALUES (%s, %s, %s)", (slug, recipe.id, _json(recipe)))
            self._save_recipe(db, recipe)
            db.execute("UPDATE recipes SET created_at=updated_at WHERE slug=%s AND created_at IS NULL", (slug,))
            return RecipeRef(recipe.id, recipe.name, slug)

    def update_recipe(self, slug, patch):
        with self._connect() as db:
            recipe = self._get_recipe(db, slug)
            editable = {f.name for f in fields(Recipe)} - {"id", "slug"}
            if not patch.changes or patch.changes.keys() - editable:
                raise StoreError("invalid_argument", self.id)
            recipe = replace(recipe, **patch.changes)
            self._save_recipe(db, recipe)
            return RecipeRef(recipe.id, recipe.name, slug)

    def normalize_imported(self, slug):
        """Fill unstructured import rows atomically, retaining all source metadata."""
        from sabor.importing.ingredients_es import parse_ingredient

        with self._connect() as db:
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
                parsed = parse_ingredient(row.originalText)
                if parsed.food is None:
                    merged.append(row)
                    continue
                merged.append(replace(row, quantity=parsed.quantity,
                                      food=parsed.food, unit=parsed.unit))
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
                normalized = _search_text(f.query).strip()
                # Tokens are letters and digits only, so no LIKE escaping is needed.
                pattern = '% ' + normalized + ' %'
                rows = db.execute("SELECT payload FROM recipes WHERE search_text LIKE %s ORDER BY seq",
                                  (pattern,)).fetchall() if normalized else []
                names = db.execute("SELECT payload FROM recipes ORDER BY seq").fetchall()
                rows = {r[0]: r for r in rows}
                rows.update({r[0]: r for r in names if _normal(f.query) in _normal(_recipe(r[0]).name or "")})
                rows = rows.values()
            else:
                rows = db.execute("SELECT payload FROM recipes ORDER BY seq").fetchall()
            recipes = sorted((_recipe(r[0]) for r in rows), key=lambda r: r.name or "",
                             reverse=f.direction == "desc")
            if f.sort in ("created_at", "updated_at", "last_made"):
                metadata = {r[0]: r[1:] for r in db.execute(
                    "SELECT slug, seq, created_at, updated_at, "
                    "(SELECT max(created_at) FROM cooking_events WHERE recipe_slug=recipes.slug) FROM recipes")}
                index = {"created_at": 1, "updated_at": 2, "last_made": 3}[f.sort]
                recipes.sort(key=lambda r: (metadata[r.slug][index] or "", metadata[r.slug][0]),
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
            for row in db.execute("SELECT payload FROM recipes ORDER BY seq"):
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
            entries = [MealEntry(**_decode(r[0])) for r in db.execute("SELECT payload FROM meal_entries ORDER BY id::bigint")]
            return [e for e in entries if dates.start_date <= e.date <= dates.end_date]

    def set_mealplan_entry(self, entry):
        with self._connect() as db:
            recipe = self._get_recipe(db, entry.recipe_slug) if entry.recipe_slug else None
            result = MealEntry(entry.date, entry.entry_type, entry.title, entry.recipe_slug,
                               self._id(db), recipe.id if recipe else None)
            db.execute("INSERT INTO meal_entries VALUES (%s, %s, %s)", (result.id, entry.recipe_slug, _json(result)))
            return result

    def delete_mealplan_entry(self, entry_id):
        with self._connect() as db:
            if not db.execute("DELETE FROM meal_entries WHERE id=%s", (entry_id,)).rowcount:
                raise StoreError("not_found", self.id)

    def _list(self, db, list_id):
        row = db.execute("SELECT name FROM shopping_lists WHERE id=%s", (list_id,)).fetchone()
        if row is None:
            raise StoreError("not_found", self.id)
        return row[0]

    def get_shopping_lists(self):
        with self._connect() as db:
            return [ListSummary(*r) for r in db.execute("SELECT l.id, l.name, count(i.id) FROM shopping_lists l LEFT JOIN shopping_items i ON i.list_id=l.id GROUP BY l.id ORDER BY l.id::bigint")]

    def create_shopping_list(self, name):
        with self._connect() as db:
            key = self._id(db)
            db.execute("INSERT INTO shopping_lists VALUES (%s, %s)", (key, name))
            return ListRef(key, name)

    def delete_shopping_list(self, list_id):
        with self._connect() as db:
            if not db.execute("DELETE FROM shopping_lists WHERE id=%s", (list_id,)).rowcount:
                raise StoreError("not_found", self.id)

    def validate_shopping_list_id(self, list_id, *, adding=False):
        if not isinstance(list_id, str) or not list_id:
            raise StoreError("invalid_argument", self.id)
        return list_id

    def validate_recipe_id(self, recipe_id):
        return recipe_id

    def _items(self, db, list_id):
        self._list(db, list_id)
        return [_item(r[0]) for r in db.execute("SELECT payload FROM shopping_items WHERE list_id=%s ORDER BY id::bigint", (list_id,))]

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
            self._list(db, list_id)
            count = db.execute("DELETE FROM shopping_items WHERE list_id=%s", (list_id,)).rowcount
            if not count:
                return {"ok": True, "deleted": 0, "message": "Lista ya estaba vacia"}
            return {"ok": True, "deleted": count, "status": 200}

    def _save_item(self, db, item):
        db.execute("INSERT INTO shopping_items VALUES (%s, %s, %s) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload",
                   (item.id, item.list_id, _json(item)))

    def add_shopping_items(self, list_id, items):
        with self._connect() as db:
            self._list(db, list_id)
            rows = [ShoppingItem(i.note, i.display, i.quantity, i.food, i.unit,
                                 self._id(db), list_id) for i in items]
            for row in rows:
                self._save_item(db, row)
            return WriteResult(tuple(r.id for r in rows))

    def add_recipe_occurrences(self, list_id, occurrences):
        with self._connect() as db:
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
            target = self._selection(db, list_id, ids)
            if type(checked) is not bool:
                raise StoreError("invalid_argument", self.id)
            for key in ids:
                self._save_item(db, replace(target[key], checked=checked))
            return WriteResult(tuple(ids))

    def remove_shopping_items(self, list_id, ids):
        with self._connect() as db:
            self._selection(db, list_id, ids)
            with db.cursor() as cursor:
                cursor.executemany("DELETE FROM shopping_items WHERE id=%s", [(i,) for i in ids])
            return WriteResult(tuple(ids))

    def _tag(self, db, name):
        row = db.execute("SELECT payload FROM tags WHERE name=%s", (name,)).fetchone()
        if row:
            try:
                return replace(_Tag(**json.loads(row[0])), created=False)
            except (ValueError, TypeError):
                raise StoreError("invalid_response", self.id) from None
        tag = _Tag(self._id(db), name, name.casefold().replace(" ", "-"), True)
        db.execute("INSERT INTO tags VALUES (%s, %s, %s)", (tag.id, name, _json(tag)))
        return tag

    def get_or_create_tag(self, name):
        with self._connect() as db:
            return self._tag(db, name)

    def tag_recipe(self, slug, tags):
        with self._connect() as db:
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
            self._get_recipe(db, slug)
            key = self._id(db)
            db.execute("INSERT INTO comments VALUES (%s, %s, %s)", (key, slug, text))
            return key

    def log_cooking_event(self, slug, subject, message):
        with self._connect() as db:
            self._get_recipe(db, slug)
            key = self._id(db)
            db.execute("INSERT INTO cooking_events (id, recipe_slug, subject, message, created_at) VALUES (%s, %s, %s, %s, %s)",
                       (key, slug, subject, message, datetime.now(timezone.utc).isoformat()))
            return key


_READ_ONLY = frozenset({
    'get_recipe', 'search_recipes', 'suggest_recipes', 'get_mealplan',
    'get_shopping_lists', 'get_shopping_items', 'shopping_list_preview', 'schema_version',
})


def _operation(method):
    @wraps(method)
    def run(self: PostgresRecipeStore, *args, **kwargs):
        with self._lock:
            for attempt in range(2):
                try:
                    self._ensure_ready()
                    connection: Any = self._connection
                    if connection is None:
                        raise StoreError('upstream_error', self.id)
                    with connection.transaction():
                        return method(self, *args, **kwargs)
                except self._driver.OperationalError:
                    if self._connection is not None:
                        self._connection.close()
                        self._connection = None
                    if attempt == 0 and method.__name__ in _READ_ONLY:
                        continue
                    raise StoreError('upstream_error', self.id) from None
                except self._driver.Error:
                    raise StoreError('upstream_error', self.id) from None
    return run


# All public persistence operations have one transaction and one error boundary.
for _name in (*PostgresRecipeStore.operations, 'normalize_imported', 'schema_version'):
    if hasattr(PostgresRecipeStore, _name):
        setattr(PostgresRecipeStore, _name, _operation(getattr(PostgresRecipeStore, _name)))
