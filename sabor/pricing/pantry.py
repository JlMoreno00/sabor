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

    Read-only: a Mealie instance that never used the pantry has no pantry file, and none is
    created here.
    """
    from sabor.pantry.store import pantry_store_for
    store = pantry_store_for(context.settings)
    if store is None or not store.path.exists():
        return []
    return [PantryStock(i.name, i.quantity, i.unit) for i in store.list_items()]

