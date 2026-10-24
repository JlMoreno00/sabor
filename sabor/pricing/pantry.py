"""Read-only instance pantry and conservative whole-name matching."""
import unicodedata

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
