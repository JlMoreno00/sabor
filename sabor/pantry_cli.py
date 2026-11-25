"""`sabor-mcp pantry import-memory`: «Despensa Conversacional» bullets to the pantry (#144 point 7).

Each bullet is read with the shared quantity parser («2 kg de patatas» is 2000 g of patatas).
A bullet with an amount in g, ml or ud is imported with `source=migration`; one without (a
bare «arroz», «un paquete de pasta») is only listed for the user, never guessed. Preview by
default, `--apply` to write. MEMORY.md is read and never changed. A bullet whose food already
has a migrated item is not imported again, so running it twice adds nothing.
"""
import argparse
import json
import os
from pathlib import Path

from sabor.core.config import load_settings
from sabor.core.envelope import fail, ok
from sabor.pantry.model import food_key, format_amount
from sabor.pricing.quantities import from_structured, ingredient_name, to_base
from sabor.preferences_cli import parse_memory


def plan(bullets: list[str], migrated: set[str]) -> dict:
    items, uncertain, already = [], [], []
    for line in bullets:
        q = to_base(from_structured(None, None, None, line))
        name = ingredient_name(line).strip()
        if q.amount is None or q.unit not in ('g', 'ml', 'ud') or not name:
            uncertain.append({'line': line, 'reason': 'quantity_unknown'})
        elif food_key(name) in migrated:
            already.append(line)
        else:
            items.append({'line': line, 'name': name, 'quantity': format_amount(q.amount), 'unit': q.unit})
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
        from sabor.core.context import assemble_context
        recipes = assemble_context(settings).store
    store = pantry_store_for(settings, recipe_store=recipes)
    if store is None:
        return _print(fail('unsupported_operation', 'This recipe store has no pantry.'))
    try:
        text = Path(settings.memory_file).read_text(encoding='utf-8')
    except (OSError, UnicodeError):
        return _print(fail('invalid_response', 'Unable to read legacy memory safely.'))
    bullets = parse_memory(text)[0]['pantry']
    try:
        migrated = {i.food for i in store.list_items() if i.source == 'migration'}
        result = plan(bullets, migrated)
        changes = [{'op': 'add', 'name': i['name'], 'quantity': i['quantity'], 'unit': i['unit'],
                    'source': 'migration'} for i in result['items']]
        if args.apply:
            effects = store.apply(changes) if changes else []
        else:
            effects = store.preview_with_digest(changes)[0] if changes else []
    except StoreError as exc:
        return _print(fail(exc.code, 'The recipe store could not be reached.', exc.provider))
    except (PantryError, PantryConflict, OSError):
        return _print(fail('invalid_response', 'Unable to update the pantry safely.'))
    return _print(ok({'preview': not args.apply, 'effects': effects, **result, 'memory_kept': True}))


def _print(result) -> int:
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['ok'] else 1
