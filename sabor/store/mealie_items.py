"""Instance-owned shopping items, retaining private fields for lossless updates."""
from copy import deepcopy
from dataclasses import dataclass
from uuid import UUID
from typing import Any, Callable, cast
from sabor.core.envelope import fail
from sabor.store import (ShoppingItem, ShoppingSnapshot, NamedRef, WriteResult,
                         StoreError)

_UPDATE_FIELDS = ('quantity', 'note', 'display', 'position', 'foodId', 'unitId',
                  'labelId', 'extras', 'recipeReferences')


@dataclass(frozen=True)
class _Item(ShoppingItem):
    raw: dict | None = None


class MealieShoppingItemsMixin:
    id: str
    _shopping_json: Callable[..., Any]
    _shopping_request: Callable[..., Any]
    get_recipe: Callable[..., Any]

    def validate_shopping_list_id(self, list_id, *, adding=False):
        try:
            return str(UUID(list_id))
        except (ValueError, TypeError, AttributeError):
            exc = StoreError('invalid_argument', self.id)
            setattr(
                exc,
                "result",
                fail(
                    'invalid_argument',
                    "Require a UUID list, exactly one nonempty source, valid ISO range, and multiplier > 0 and <= 20."
                    if adding
                    else "list_id must be a UUID.",
                ),
            )
            raise exc from None

    def validate_recipe_id(self, recipe_id):
        try:
            return str(UUID(recipe_id))
        except (ValueError, TypeError, AttributeError):
            raise StoreError('invalid_response', self.id) from None
    def _item_list_id(self, list_id):
        try:
            return str(UUID(list_id))
        except (ValueError, TypeError, AttributeError):
            raise StoreError('invalid_argument', self.id) from None

    def get_shopping_items(self, list_id):
        canonical = self._item_list_id(list_id)
        data = self._shopping_json('get', f'lists/{canonical}')
        try:
            rows = data['listItems']
            if not isinstance(rows, list):
                raise ValueError()
            items, seen = [], set()
            for row in rows:
                if (not isinstance(row, dict) or not isinstance(row.get('id'), str)
                        or row['id'] in seen or row.get('shoppingListId') != canonical):
                    raise ValueError()
                seen.add(row['id'])
                refs = {key: NamedRef(row[key].get('id'), row[key].get('name'))
                        if row.get(key) else None for key in ('food', 'unit')}
                items.append(_Item(id=row['id'], list_id=canonical, **refs,
                    **{key: row.get(key) for key in ('quantity', 'note', 'display', 'checked')},
                    recipe_references=deepcopy(row.get('recipeReferences')), raw=deepcopy(row)))
            return ShoppingSnapshot(canonical, tuple(items))
        except Exception:
            raise StoreError('invalid_response', self.id) from None

    def _selected_items(self, list_id, ids):
        if (not isinstance(ids, list) or not ids or any(not isinstance(x, str) or not x for x in ids)
                or len(set(ids)) != len(ids)):
            raise StoreError('invalid_argument', self.id)
        rows = {row.id: cast(_Item, row) for row in self.get_shopping_items(list_id).items}
        if any(x not in rows for x in ids):
            raise StoreError('invalid_argument', self.id)
        return [rows[x] for x in ids]

    def check_shopping_items(self, list_id, ids, checked):
        if type(checked) is not bool:
            raise StoreError('invalid_argument', self.id)
        rows = self._selected_items(list_id, ids)
        payload = [{**{k: cast(dict, row.raw)[k] for k in _UPDATE_FIELDS if k in cast(dict, row.raw)},
                    'id': row.id, 'shoppingListId': row.list_id, 'checked': checked} for row in rows]
        self._shopping_request('put', 'items', json=payload)
        return WriteResult(tuple(ids))

    def remove_shopping_items(self, list_id, ids):
        self._selected_items(list_id, ids)
        self._shopping_request('delete', 'items', params={'ids': ids})
        return WriteResult(tuple(ids))

    def add_shopping_items(self, list_id, items):
        canonical = self._item_list_id(list_id)
        payload = [{'shoppingListId': canonical, **{key: getattr(row, key)
                    for key in ('note', 'display', 'quantity') if getattr(row, key) is not None},
                    **{key + 'Id': getattr(row, key).id for key in ('food', 'unit')
                       if getattr(row, key) is not None}} for row in items]
        if not payload:
            return WriteResult()
        data = self._shopping_json('post', 'items/create-bulk', json=payload)
        try:
            created = data['createdItems']
            if not isinstance(created, list) or len(created) > len(items):
                raise ValueError()
            return WriteResult(tuple(row['id'] for row in created))
        except Exception:
            raise StoreError('invalid_response', self.id) from None

    def add_recipe_occurrences(self, list_id, occurrences):
        canonical = self._item_list_id(list_id)
        # Resolve the entire batch before mutating; never deduplicate occurrences.
        payload = []
        for row in occurrences:
            rid = getattr(row, 'recipe_id', None)
            if rid is None:
                rid = self.get_recipe(row.slug).id
            try:
                rid = str(UUID(rid))
                if type(row.multiplier) not in (int, float) or not 0 < row.multiplier <= 20:
                    raise ValueError()
            except (ValueError, TypeError, AttributeError):
                raise StoreError('invalid_argument', self.id) from None
            payload.append({'recipeId': rid, 'recipeIncrementQuantity': row.multiplier})
        if payload:
            self._shopping_request('post', f'lists/{canonical}/recipe', json=payload)
        return WriteResult()
