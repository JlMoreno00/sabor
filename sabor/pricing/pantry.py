"""Read-only instance pantry and conservative whole-name matching."""
import unicodedata
from dataclasses import dataclass
from decimal import Decimal

from sabor.preferences import preferences_call
from .quantities import ingredient_name


def load_pantry(context):
    result = preferences_call(context)
    return (result['data']['preferences']['pantry'], None) if result['ok'] else ([], result)


def _normalize(name):
    return ' '.join(''.join(c for c in unicodedata.normalize('NFD', name.casefold())
                           if not unicodedata.combining(c)).split())


def _forms(name):
    forms = {name}
    for suffix in ('es', 's'):
        if name.endswith(suffix) and len(name) > len(suffix):
            forms.add(name[:-len(suffix)])
    return forms


def match_pantry(name, pantry):
    normalized = _normalize(name)
    for entry in pantry:
        other = _normalize(entry)
        if normalized and (normalized == other or normalized in _forms(other) or other in _forms(normalized)):
            return entry
    return None


def item_name(item):
    return item.food.name if item.food else ingredient_name(
        getattr(item, 'originalText', None) or item.note or item.display or '')


@dataclass(frozen=True)
class PantryStock:
    """One structured pantry item (#144) as pricing sees it: name and base-unit amount."""
    name: str
    quantity: Decimal | None
    unit: str | None


def load_stock(context) -> list[PantryStock]:
    """The structured pantry for exclude_pantry; empty when the store has none.

    Read-only: estimates never create the pantry file, its tables or any row; a missing file
    or a file without pantry tables is an empty pantry. With Postgres the pantry lives in the
    recipe store's schema and is read without migrations; an unreachable database raises
    StoreError, which the estimate reports as upstream_error.
    """
    from sabor.pantry.store import pantry_store_for
    store = pantry_store_for(context.settings, recipe_store=getattr(context, 'store', None))
    if store is None:
        return []
    return [PantryStock(i.name, i.quantity, i.unit) for i in store.read_items()]

