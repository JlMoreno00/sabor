"""Mark Mealie foods "on hand" from the structured pantry (#144 point 9, José's decision).

Mealie 3.22 keeps "on hand" per household: a food is on hand when the household's slug is in
its `householdsWithIngredientFood`. A pantry item is matched to a Mealie food by its own
`food_id`, else by one exact name, plural or alias (accents and case ignored); anything else
is reported as unmatched, never guessed. Sabor marks a food only when it is not on hand yet,
and records that in the food's `extras` (`sabor_on_hand: <household>`); it unmarks only
those foods, once they leave the pantry, so marks set by hand in Mealie are never removed.
"""
import hashlib
import json
from typing import Any

from .model import PantryItem, food_key

MARK = 'sabor_on_hand'
# The fields Mealie's PUT /api/foods/{id} accepts; the PUT replaces the whole food.
_FIELDS = ('name', 'pluralName', 'description', 'extras', 'labelId', 'aliases',
           'householdsWithIngredientFood')


def _names(food: dict) -> list[str]:
    names = [food.get('name'), food.get('pluralName')]
    names += [a.get('name') for a in food.get('aliases') or [] if isinstance(a, dict)]
    return [n for n in names if isinstance(n, str) and n.strip()]


def _on_hand(food: dict, household: str) -> bool:
    return household in (food.get('householdsWithIngredientFood') or [])


def plan(items: list[PantryItem], foods: list[dict], household: str) -> dict[str, Any]:
    by_id = {f['id']: f for f in foods}
    by_name: dict[str, set[str]] = {}
    for f in foods:
        for name in _names(f):
            by_name.setdefault(food_key(name), set()).add(f['id'])
    wanted: list[str] = []
    unmatched: list[dict[str, str]] = []
    for item in items:
        if item.quantity is not None and item.quantity <= 0:
            continue
        if item.food_id is not None and item.food_id in by_id:
            food_id: str = item.food_id
        else:
            matches = by_name.get(item.food, set()) | by_name.get(food_key(item.name), set())
            if len(matches) != 1:
                unmatched.append({'name': item.name, 'reason': 'ambiguous' if matches else 'not_in_mealie'})
                continue
            food_id = next(iter(matches))
        if food_id not in wanted:
            wanted.append(food_id)
    mark = [{'food_id': i, 'name': by_id[i].get('name')} for i in wanted if not _on_hand(by_id[i], household)]
    unmark = [{'food_id': f['id'], 'name': f.get('name')} for f in foods
              if f['id'] not in wanted and _on_hand(f, household)
              and (f.get('extras') or {}).get(MARK) == household]
    return {'household': household, 'mark': mark, 'unmark': unmark, 'unmatched': unmatched}


def digest(result: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(result, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _body(food: dict) -> dict:
    return {key: food.get(key) for key in _FIELDS if key in food}


def apply(recipes, result: dict[str, Any], done: dict[str, list[str]]) -> None:
    """Write each change on a fresh copy of the food; `done` collects what was written."""
    household = result['household']
    for row in result['mark']:
        food = recipes.get_food(row['food_id'])
        if _on_hand(food, household):
            continue
        body = _body(food)
        body['householdsWithIngredientFood'] = [*(food.get('householdsWithIngredientFood') or []), household]
        body['extras'] = {**(food.get('extras') or {}), MARK: household}
        recipes.put_food(row['food_id'], body)
        done['marked'].append(row['food_id'])
    for row in result['unmark']:
        food = recipes.get_food(row['food_id'])
        if (food.get('extras') or {}).get(MARK) != household:
            continue
        body = _body(food)
        body['householdsWithIngredientFood'] = [h for h in food.get('householdsWithIngredientFood') or []
                                                if h != household]
        body['extras'] = {k: v for k, v in (food.get('extras') or {}).items() if k != MARK}
        recipes.put_food(row['food_id'], body)
        done['unmarked'].append(row['food_id'])
