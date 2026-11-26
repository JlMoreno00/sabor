"""`sabor-mcp pantry import-memory`: «Despensa Conversacional» bullets to the pantry (#144 point 7).

Each bullet is read with the shared quantity parser («2 kg de patatas» is 2000 g of patatas).
A bullet with an amount in g, ml or ud is imported with `source=migration`; one without (a
bare «arroz», «un paquete de pasta») is only listed for the user, never guessed. Preview by
default, `--apply` to write; a preview creates nothing, not even an empty pantry. MEMORY.md is
read and never changed. Every imported food is recorded in the pantry's consumed-entries table
(`memory-import:<food>`) in the same transaction as the items, so a food is imported once even
when it merged into an item of another source; a concurrent import is a `conflict`.
"""
import argparse
import json
import os
import sqlite3
import tempfile
from pathlib import Path

from sabor.core.config import load_settings
from sabor.core.envelope import fail, ok
from sabor.pantry.model import food_key, format_amount
from sabor.pricing.quantities import from_structured, ingredient_name, to_base
from sabor.preferences_cli import parse_memory

ENTRY = 'memory-import:'


def _measured(line: str):
    """(name, amount, unit) for a bullet with an amount in g, ml or ud; None otherwise."""
    q = to_base(from_structured(None, None, None, line))
    name = ingredient_name(line).strip()
    if q.amount is None or q.unit not in ('g', 'ml', 'ud') or not name:
        return None
    return name, q.amount, q.unit


def plan(bullets: list[str], migrated: set[str]) -> dict:
    items, uncertain, already = [], [], []
    for line in bullets:
        measured = _measured(line)
        if measured is None:
            uncertain.append({'line': line, 'reason': 'quantity_unknown'})
        elif food_key(measured[0]) in migrated:
            already.append(line)
        else:
            name, amount, unit = measured
            items.append({'line': line, 'name': name, 'quantity': format_amount(amount), 'unit': unit})
    return {'items': items, 'uncertain': uncertain, 'already_imported': already}


def main(argv=None):
    parser = argparse.ArgumentParser(prog='sabor-mcp pantry')
    parser.add_argument('command', choices=('import-memory',),
                        help='import-memory: add the measured «Despensa Conversacional» bullets of MEMORY.md')
    parser.add_argument('--apply', action='store_true', help='Write changes; default is a read-only preview')
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return exc.code
    try:
        settings = load_settings(os.environ)
    except ValueError:
        return _print(fail('invalid_argument', 'Invalid Sabor configuration.'))
    from sabor.pantry.store import PantryConflict, PantryError, pantry_store_for
    from sabor.store import StoreError
    # The same pantry as the `pantry` tools: SQLite, Mealie's local file or the Postgres schema,
    # which shares the recipe store's connection (only Postgres needs the recipe store).
    recipes = None
    if settings.store == 'postgres':
        from sabor.core.config import ConfigError
        from sabor.core.context import assemble_context
        try:
            recipes = assemble_context(settings).store
        except ConfigError:
            # The Postgres store needs the optional extra: sabor-mcp[postgres].
            return _print(fail('provider_unconfigured',
                               'The Postgres store is not available; install sabor-mcp[postgres].', 'postgres'))
    store = pantry_store_for(settings, recipe_store=recipes)
    if store is None:
        return _print(fail('unsupported_operation', 'This recipe store has no pantry.'))
    try:
        text = Path(settings.memory_file).read_text(encoding='utf-8')
    except (OSError, UnicodeError):
        return _print(fail('invalid_response', 'Unable to read legacy memory safely.'))
    bullets = parse_memory(text)[0]['pantry']
    try:
        foods = {food_key(m[0]) for m in map(_measured, bullets) if m is not None}
        ids = [ENTRY + food for food in sorted(foods)]
        # A preview only reads, with no session: nothing is created and no migration runs,
        # even when the schema already exists (CodeRabbit on #156).
        done = store.consumed_entries(ids) if args.apply else store.read_consumed_entries(ids)
        result = plan(bullets, {entry.removeprefix(ENTRY) for entry in done})
        changes = [{'op': 'add', 'name': i['name'], 'quantity': i['quantity'], 'unit': i['unit'],
                    'source': 'migration'} for i in result['items']]
        entries = list(dict.fromkeys(ENTRY + food_key(i['name']) for i in result['items']))
        if args.apply:
            # The import record is checked inside the write transaction: a concurrent import
            # that recorded one of these foods first makes this one a conflict.
            effects = store.apply(changes, consumed_entries=entries) if changes else []
        else:
            effects = _preview(store.snapshot(), changes) if changes else []
    except StoreError as exc:
        return _print(fail(exc.code, 'The recipe store could not be reached.', exc.provider))
    except PantryConflict:
        return _print(fail('conflict', 'Another import changed the pantry meanwhile; run the command again.'))
    except (PantryError, OSError, sqlite3.Error):
        return _print(fail('invalid_response', 'Unable to update the pantry safely.'))
    return _print(ok({'preview': not args.apply, 'effects': effects, **result, 'memory_kept': True}))


def _preview(items, changes) -> list:
    """The effects of `changes` planned on a throwaway copy of the pantry's items."""
    from sabor.pantry.store import PantryStore, ensure_schema
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / 'pantry.db'
        connection = sqlite3.connect(path)
        try:
            with connection:
                ensure_schema(connection)
                connection.executemany(
                    'INSERT INTO pantry_items VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                    [(i.id, i.name, i.food, i.food_id,
                      format_amount(i.quantity) if i.quantity is not None else None, i.unit,
                      i.expires_on, i.source, i.note, i.updated_at) for i in items])
        finally:
            connection.close()
        return PantryStore(path).preview_with_digest(changes)[0]


def _print(result) -> int:
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['ok'] else 1
