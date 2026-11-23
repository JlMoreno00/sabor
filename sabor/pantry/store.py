"""Structured pantry storage on SQLite (#144).

With the SQLite recipe store the pantry lives in the same database (schema version 5).
With Mealie, whose API keeps no pantry quantities, it lives in `<data dir>/pantry.db`.
Postgres has no pantry yet. Changes are validated as a whole, previewed by computing the
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


def pantry_store_for(settings, env=None) -> 'PantryStore | None':
    """The pantry of this instance: SQLite store's database, a local file for Mealie, else None.

    The Mealie file follows this instance's own data dir (its frozen settings), never the
    process environment, so instances with different SABOR_DATA_DIR keep separate pantries.
    `env` is accepted for compatibility and not used.
    """
    if settings.store == 'sqlite':
        return PantryStore(Path(settings.store_db))
    if settings.store == 'mealie':
        return PantryStore(Path(settings.legacy_config.SABOR_DATA_DIR) / 'pantry.db')
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
    def _session(self) -> Iterator[sqlite3.Connection]:
        """One connection per operation, always closed; `with connection` commits or rolls back."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path)
        try:
            with connection:
                ensure_schema(connection)
                yield connection
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

    def _digest(self, connection, changes) -> str:
        """Binds a preview to these changes and to the pantry as it is on this connection."""
        snapshot = [(i.id, i.food, i.quantity is not None and format_amount(i.quantity), i.unit,
                     i.expires_on, i.updated_at) for i in self._list(connection)]
        payload = json.dumps([changes, snapshot], sort_keys=True, default=str, ensure_ascii=False)
        return hashlib.sha256(payload.encode()).hexdigest()

    def preview_with_digest(self, changes: list[dict]) -> tuple[list[dict[str, Any]], str]:
        """Effects and digest from one snapshot; nothing is written."""
        with self._session() as connection:
            digest = self._digest(connection, changes)
            effects, _ = self._plan(connection, changes)
            connection.rollback()
            return effects, digest

    def digest(self, changes: list[dict]) -> str:
        with self._session() as connection:
            return self._digest(connection, changes)

    def preview(self, changes: list[dict]) -> list[dict[str, Any]]:
        """The effects apply() would have, computed without writing."""
        with self._session() as connection:
            effects, _ = self._plan(connection, changes)
            connection.rollback()
            return effects

    def apply(self, changes: list[dict], expected_digest: str | None = None) -> list[dict[str, Any]]:
        """Apply in one transaction. With expected_digest, the pantry must still match the preview.

        The digest is checked after BEGIN IMMEDIATE on the connection that plans and writes,
        so two confirmations of one preview cannot both apply.
        """
        with self._session() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if expected_digest is not None and self._digest(connection, changes) != expected_digest:
                raise PantryConflict('The pantry changed since the preview.')
            effects, writes = self._plan(connection, changes)
            for sql, args in writes:
                connection.execute(sql, args)
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
        return ("INSERT OR REPLACE INTO pantry_items VALUES (?,?,?,?,?,?,?,?,?,?)",
                (item.id, item.name, item.food, item.food_id, quantity, item.unit, item.expires_on,
                 item.source, item.note, item.updated_at))
