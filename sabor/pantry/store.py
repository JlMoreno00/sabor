"""Structured pantry storage (#144).

With the SQLite recipe store the pantry lives in the same database (schema version 5).
With Mealie, whose API keeps no pantry quantities, it lives in `<data dir>/pantry.db`.
With Postgres/Supabase it lives in the store's schema (migration 4, row level security on).
Changes are validated as a whole, previewed by computing the
same effects without writing, and applied in one transaction.
"""
import hashlib
import json
import re
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, DecimalException
from pathlib import Path
from typing import Any, cast

from .model import SOURCES, BaseUnit, PantryItem, food_key, format_amount, to_base_quantity

_OPS = ('add', 'set', 'subtract', 'remove')
_ISO_DATE = re.compile(r'^\d{4}-\d{2}-\d{2}$')


class PantryError(ValueError):
    """A change is invalid; nothing was written."""


class PantryConflict(Exception):
    """The pantry changed since the preview; nothing was written."""


def ensure_schema(connection: sqlite3.Connection) -> None:
    connection.execute(
        "CREATE TABLE IF NOT EXISTS pantry_items (id TEXT PRIMARY KEY, name TEXT NOT NULL, "
        "food TEXT NOT NULL, food_id TEXT, quantity TEXT, unit TEXT, expires_on TEXT, "
        "source TEXT NOT NULL, note TEXT, updated_at TEXT NOT NULL)")
    connection.execute("CREATE INDEX IF NOT EXISTS pantry_items_food ON pantry_items(food)")
    # Meal plan entries already subtracted by consume_from_pantry, so a week is used once.
    connection.execute("CREATE TABLE IF NOT EXISTS pantry_consumed_entries "
                       "(entry_id TEXT PRIMARY KEY, consumed_at TEXT NOT NULL)")


def pantry_store_for(settings, env=None, recipe_store=None) -> 'PantryStore | None':
    """The pantry of this instance: SQLite store's database, a local file for Mealie, or the
    Postgres recipe store's schema (#144 point 8); None when there is none.

    The Mealie file follows this instance's own data dir (its frozen settings), never the
    process environment, so instances with different SABOR_DATA_DIR keep separate pantries.
    Postgres needs the instance's recipe store, whose connection and lock it shares.
    `env` is accepted for compatibility and not used.
    """
    if settings.store == 'sqlite':
        return PantryStore(Path(settings.store_db))
    if settings.store == 'mealie':
        return PantryStore(Path(settings.legacy_config.SABOR_DATA_DIR) / 'pantry.db')
    if settings.store == 'postgres' and getattr(recipe_store, 'id', None) == 'postgres':
        return PostgresPantryStore(recipe_store)
    return None


def _row(row) -> PantryItem:
    return PantryItem(row[0], row[1], row[2], row[3], Decimal(row[4]) if row[4] is not None else None,
                      cast(BaseUnit | None, row[5]), row[6], row[7], row[8], row[9])


def _date(value) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not _ISO_DATE.match(value):
        raise PantryError('expires_on must be an ISO date (YYYY-MM-DD).')
    try:
        date.fromisoformat(value)
    except ValueError:
        raise PantryError('expires_on must be an ISO date (YYYY-MM-DD).') from None
    return value


def _text(change, key: str, limit: int) -> str | None:
    """An optional text field: None or a string of at most `limit` characters."""
    value = change.get(key)
    if value is not None and (not isinstance(value, str) or len(value) > limit):
        raise PantryError(f'{key} must be a text of at most {limit} characters.')
    return value


def _use_order(item: PantryItem) -> tuple:
    """Items expiring first are used first; then the oldest; the id only breaks ties."""
    return (item.expires_on is None, item.expires_on or '', item.updated_at, item.id)


def _amount(change) -> tuple[Decimal, BaseUnit] | None:
    """Validated (amount, base unit), or None when the change carries no quantity."""
    if change.get('quantity') is None:
        if change.get('unit') is not None:
            raise PantryError('A unit needs a quantity.')
        return None
    try:
        amount = Decimal(str(change['quantity']).replace(',', '.'))
    except (DecimalException, ValueError):
        raise PantryError('quantity must be a number.') from None
    if not amount.is_finite() or amount <= 0:
        raise PantryError('quantity must be positive.')
    base = to_base_quantity(amount, change.get('unit') or 'ud')
    if base is None:
        raise PantryError(f"Unknown unit: {change.get('unit')!r}.")
    return base


class PantryStore:
    def __init__(self, path):
        self.path = Path(path)

    @contextmanager
    def _session(self) -> Iterator[Any]:
        """One connection per operation, always closed; `with connection` commits or rolls back."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path)
        try:
            with connection:
                ensure_schema(connection)
                yield connection
        finally:
            connection.close()

    def _begin_write(self, connection) -> None:
        """Serialize writers before the digest check (SQLite: take the write lock now)."""
        connection.execute("BEGIN IMMEDIATE")

    def _begin_read(self, connection) -> None:
        """Read the preview's digest and plan from one snapshot (SQLite: one read transaction)."""
        connection.execute("BEGIN")

    @staticmethod
    def _discard(connection) -> None:
        connection.rollback()

    def read_items(self) -> list[PantryItem]:
        """Read-only listing for pricing: never creates the file, the schema or any row.

        A missing file or a file without pantry tables is an empty pantry.
        """
        if not self.path.exists():
            return []
        connection = sqlite3.connect(self.path.resolve().as_uri() + '?mode=ro', uri=True)
        try:
            return [_row(r) for r in connection.execute("SELECT * FROM pantry_items ORDER BY food, unit, id")]
        except sqlite3.OperationalError:
            return []
        finally:
            connection.close()

    def list_items(self, food: str | None = None, expiring_within_days: int | None = None,
             today: str | None = None) -> list[PantryItem]:
        with self._session() as connection:
            return self._list(connection, food, expiring_within_days, today)

    def _list(self, connection, food=None, expiring_within_days=None, today=None) -> list[PantryItem]:
        query, args = "SELECT * FROM pantry_items", []
        clauses = []
        if food is not None:
            clauses.append("food = ?")
            args.append(food_key(food))
        if expiring_within_days is not None:
            limit = date.fromisoformat(today) if today else date.today()
            clauses.append("expires_on IS NOT NULL AND expires_on <= ?")
            args.append((limit + timedelta(days=expiring_within_days)).isoformat())
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        return [_row(r) for r in connection.execute(query + " ORDER BY food, unit, id", args)]

    def _digest(self, connection, changes, entries=()) -> str:
        """Binds a preview to these changes and entries, and to the pantry as on this connection."""
        snapshot = [(i.id, i.food, i.quantity is not None and format_amount(i.quantity), i.unit,
                     i.expires_on, i.updated_at) for i in self._list(connection)]
        consumed = sorted(self._consumed(connection, entries))
        payload = json.dumps([changes, list(entries), consumed, snapshot], sort_keys=True,
                             default=str, ensure_ascii=False)
        return hashlib.sha256(payload.encode()).hexdigest()

    @staticmethod
    def _consumed(connection, entries) -> set[str]:
        # One parameterized lookup per entry: a week has only a handful.
        return {entry for entry in (str(e) for e in entries) if connection.execute(
            "SELECT 1 FROM pantry_consumed_entries WHERE entry_id = ?", (entry,)).fetchone()}

    def consumed_entries(self, entries) -> set[str]:
        """Which of these meal plan entry IDs were already subtracted."""
        with self._session() as connection:
            return self._consumed(connection, entries)

    def preview_with_digest(self, changes: list[dict], entries=()) -> tuple[list[dict[str, Any]], str]:
        """Effects and digest from one snapshot; nothing is written."""
        with self._session() as connection:
            self._begin_read(connection)
            digest = self._digest(connection, changes, entries)
            effects = self._plan(connection, changes)[0] if changes else []
            self._discard(connection)
            return effects, digest

    def digest(self, changes: list[dict], entries=()) -> str:
        with self._session() as connection:
            return self._digest(connection, changes, entries)

    def preview(self, changes: list[dict]) -> list[dict[str, Any]]:
        """The effects apply() would have, computed without writing."""
        with self._session() as connection:
            effects, _ = self._plan(connection, changes)
            self._discard(connection)
            return effects

    def apply(self, changes: list[dict], expected_digest: str | None = None,
              consumed_entries=()) -> list[dict[str, Any]]:
        """Apply in one transaction. With expected_digest, the pantry must still match the preview.

        The digest is checked after BEGIN IMMEDIATE on the connection that plans and writes,
        so two confirmations of one preview cannot both apply. consumed_entries are meal plan
        entry IDs recorded in the same transaction; one already recorded is a conflict.
        """
        entries = [str(e) for e in consumed_entries]
        with self._session() as connection:
            self._begin_write(connection)
            if expected_digest is not None and self._digest(connection, changes, entries) != expected_digest:
                raise PantryConflict('The pantry changed since the preview.')
            if self._consumed(connection, entries):
                raise PantryConflict('These meal plan entries were already subtracted.')
            effects, writes = self._plan(connection, changes) if changes or not entries else ([], [])
            for sql, args in writes:
                connection.execute(sql, args)
            now = datetime.now(timezone.utc).isoformat()
            for entry in entries:
                connection.execute("INSERT INTO pantry_consumed_entries VALUES (?, ?)", (entry, now))
            return effects

    def _plan(self, connection, changes) -> tuple[list[dict[str, Any]], list[tuple[str, tuple]]]:
        if not isinstance(changes, list) or not changes:
            raise PantryError('changes must be a non-empty list.')
        items = {i.id: i for i in self._list(connection)}
        now = datetime.now(timezone.utc).isoformat()
        effects: list[dict[str, Any]] = []
        writes: list[tuple[str, tuple]] = []
        for change in changes:
            effect, item_writes = self._plan_one(change, items, now)
            effects.append(effect)
            writes.extend(item_writes)
        return effects, writes

    def _plan_one(self, change, items: dict[str, PantryItem], now: str):
        if not isinstance(change, dict) or change.get('op') not in _OPS:
            raise PantryError(f'op must be one of {", ".join(_OPS)}.')
        op = change['op']
        if op in ('set', 'remove'):
            item = items.get(str(change.get('id')))
            if item is None:
                raise PantryError(f'{op} needs the id of an existing pantry item.')
            if op == 'remove':
                del items[item.id]
                return {'op': op, 'action': 'remove', 'id': item.id, 'food': item.food}, [
                    ("DELETE FROM pantry_items WHERE id=?", (item.id,))]
            base = _amount(change)
            expires = _date(change['expires_on']) if 'expires_on' in change else item.expires_on
            note = _text(change, 'note', 500) if 'note' in change else item.note
            items[item.id] = updated = replace(
                item, quantity=base[0] if base else None, unit=base[1] if base else None,
                expires_on=expires, note=note, updated_at=now)
            return {'op': op, 'action': 'update', 'id': item.id, 'food': item.food}, [self._upsert(updated)]
        name = change.get('name')
        if not isinstance(name, str) or not name.strip() or len(name) > 200:
            raise PantryError('name must be a non-empty text (at most 200 characters).')
        food = food_key(name)
        base = _amount(change)
        unit = base[1] if base else None
        # Same food and same base unit (so the same dimension); others are never converted.
        matches = sorted((i for i in items.values() if i.food == food and i.unit == unit), key=_use_order)
        if op == 'subtract':
            return self._subtract(food, base, matches, items, now)
        match = matches[0] if matches else None
        source = change.get('source', 'manual')
        if source not in SOURCES:
            raise PantryError(f'source must be one of {", ".join(SOURCES)}.')
        expires = _date(change.get('expires_on'))
        food_id, note = _text(change, 'food_id', 200), _text(change, 'note', 500)
        if match is not None:
            quantity = match.quantity + base[0] if base and match.quantity is not None else match.quantity
            items[match.id] = updated = replace(match, quantity=quantity,
                                                expires_on=expires or match.expires_on, updated_at=now)
            return {'op': op, 'action': 'increase', 'id': match.id, 'food': food}, [self._upsert(updated)]
        new = PantryItem(uuid.uuid4().hex, name.strip(), food, food_id,
                         base[0] if base else None, unit, expires, source, note, now)
        items[new.id] = new
        return {'op': op, 'action': 'create', 'food': food}, [self._upsert(new)]

    def _subtract(self, food, base, matches: list[PantryItem], items: dict[str, PantryItem], now: str):
        """Draw from every compatible item, expiring first, before reporting a shortfall."""
        if not matches:
            return {'op': 'subtract', 'action': 'not_found', 'food': food}, []
        # Without a quantity the food is used up: every item with an unknown amount goes.
        remaining = base[0] if base else None
        writes, removed, touched = [], [], []
        for item in matches:
            if remaining is not None and remaining <= 0:
                break
            if remaining is not None and item.quantity is not None and item.quantity > remaining:
                items[item.id] = updated = replace(item, quantity=item.quantity - remaining, updated_at=now)
                writes.append(self._upsert(updated))
                touched.append(item.id)
                remaining = Decimal(0)
                break
            if remaining is not None and item.quantity is not None:
                remaining -= item.quantity
            del items[item.id]
            removed.append(item.id)
            touched.append(item.id)
            writes.append(("DELETE FROM pantry_items WHERE id=?", (item.id,)))
        all_gone = len(removed) == len(matches)
        effect: dict[str, Any] = {'op': 'subtract', 'food': food, 'action': 'remove' if all_gone else 'decrease',
                                  'ids': touched}
        if remaining is not None and remaining > 0:
            effect['shortfall'] = {'quantity': format_amount(remaining), 'unit': base[1]}
        return effect, writes

    @staticmethod
    def _upsert(item: PantryItem) -> tuple[str, tuple]:
        quantity = format_amount(item.quantity) if item.quantity is not None else None
        # Standard upsert, the same on SQLite and Postgres.
        return ("INSERT INTO pantry_items VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT (id) DO UPDATE SET "
                "name=excluded.name, food=excluded.food, food_id=excluded.food_id, "
                "quantity=excluded.quantity, unit=excluded.unit, expires_on=excluded.expires_on, "
                "source=excluded.source, note=excluded.note, updated_at=excluded.updated_at",
                (item.id, item.name, item.food, item.food_id, quantity, item.unit, item.expires_on,
                 item.source, item.note, item.updated_at))


class _PostgresConnection:
    """The few connection calls PantryStore makes, on psycopg: `?` placeholders become `%s`."""

    def __init__(self, connection):
        self._connection = connection

    def execute(self, statement: str, args=()):
        return self._connection.execute(statement.replace('?', '%s'), args)


class PostgresPantryStore(PantryStore):
    """The pantry in the Postgres recipe store's schema (#144 point 8).

    The tables come from the store's migration 4 with row level security on and no access
    for Supabase's anon/authenticated roles. Each operation runs in one transaction on the
    store's connection, under its lock; writes also take a transaction advisory lock, so two
    confirmations of one preview cannot both apply.
    """

    def __init__(self, recipe_store):
        self._recipes = recipe_store
        self.path = None
        schema = recipe_store.schema
        self._write_lock = int.from_bytes(hashlib.sha256(('sabor:pantry:' + schema).encode()).digest()[:8],
                                          'big', signed=True)

    @contextmanager
    def _session(self) -> Iterator[Any]:
        from sabor.store import StoreError
        recipes = self._recipes
        driver = recipes._driver
        with recipes._lock:
            try:
                recipes._ensure_ready()
                with recipes._connection.transaction():
                    yield _PostgresConnection(recipes._connection)
            except driver.OperationalError:
                # A lost connection is reopened on the next operation, as the recipe store does.
                if recipes._connection is not None:
                    recipes._connection.close()
                    recipes._connection = None
                raise StoreError('upstream_error', 'postgres') from None
            except driver.Error:
                raise StoreError('upstream_error', 'postgres') from None

    def read_items(self) -> list[PantryItem]:
        """Read-only listing for pricing: never creates the schema nor runs migrations.

        Like the store's probe_schema_version, a lost connection is reopened once; a schema
        without pantry tables is an empty pantry.
        """
        from psycopg import sql
        from sabor.store import StoreError
        recipes = self._recipes
        table = sql.Identifier(recipes.schema, 'pantry_items')
        with recipes._lock:
            for attempt in range(2):
                try:
                    if recipes._connection is None or recipes._connection.closed:
                        recipes._open()
                    connection = recipes._connection
                    if connection.execute('SELECT to_regclass(%s)', (table.as_string(connection),)).fetchone()[0] is None:
                        return []
                    rows = connection.execute(
                        sql.SQL('SELECT * FROM {} ORDER BY food, unit, id').format(table)).fetchall()
                    return [_row(r) for r in rows]
                except recipes._driver.OperationalError:
                    if recipes._connection is not None:
                        recipes._connection.close()
                        recipes._connection = None
                    if attempt == 0:
                        continue
                    raise StoreError('upstream_error', 'postgres') from None
                except recipes._driver.Error:
                    raise StoreError('upstream_error', 'postgres') from None
        return []

    def _begin_write(self, connection) -> None:
        connection.execute("SELECT pg_advisory_xact_lock(?)", (self._write_lock,))

    def _begin_read(self, connection) -> None:
        # The first statement of the transaction: digest and plan read one snapshot, so a
        # commit from another connection between them cannot mix two states (#152 review).
        connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")

    @staticmethod
    def _discard(connection) -> None:
        """Previews only read; the transaction ends without writing."""


