"""Instance-owned shopping-list transport; item operations live separately."""
from urllib.parse import quote
from typing import Any
from sabor.core import errors
from sabor.store import ListSummary, ListRef, StoreError


class MealieShoppingListsMixin:
    session: Any
    base_url: str
    _headers: dict
    id: str
    def _shopping_request(self, method, path, **kwargs):
        try:
            response = getattr(self.session, method)(
                f"{self.base_url}/api/households/shopping/{path}",
                headers=self._headers, timeout=30, **kwargs)
            if not 200 <= response.status_code < 300:
                raise StoreError(errors.from_http(response.status_code, self.id)['error']['code'], self.id)
            return response
        except StoreError:
            raise
        except Exception as exc:
            raise StoreError(errors.from_exception(exc, self.id)['error']['code'], self.id) from None

    def _shopping_json(self, method, path, **kwargs):
        from sabor.store.mealie import _object
        response = self._shopping_request(method, path, **kwargs)
        try:
            return _object(response.json())
        except Exception:
            raise StoreError('invalid_response', self.id) from None

    def get_shopping_lists(self):
        from sabor.store.mealie import _items
        result, seen, expected = [], set(), None
        for page in range(1, 101):
            data = self._shopping_json('get', 'lists', params={'page': page, 'perPage': 100})
            try:
                rows = _items(data)
                total = data.get('total', len(rows))
                pages = data.get('total_pages', 1)
                if (type(total) is not int or type(pages) is not int or pages < 1
                        or pages > 100 or data.get('page', page) != page
                        or (expected is not None and expected != (total, pages))):
                    raise ValueError()
                expected = (total, pages)
                for row in rows:
                    if not isinstance(row.get('id'), str) or row['id'] in seen:
                        raise ValueError()
                    seen.add(row['id'])
                    result.append(ListSummary(row['id'], row.get('name'), len(row.get('listItems') or [])))
                if page == pages:
                    if len(result) != total:
                        raise ValueError()
                    return result
            except Exception:
                raise StoreError('invalid_response', self.id) from None
        raise StoreError('invalid_response', self.id)

    def create_shopping_list(self, name):
        data = self._shopping_json('post', 'lists', json={'name': name})
        if not isinstance(data.get('id'), str):
            raise StoreError('invalid_response', self.id)
        return ListRef(data['id'], data.get('name'))

    def shopping_list_preview(self, list_id):
        data = self._shopping_json('get', f'lists/{quote(list_id, safe="")}')
        items = data.get('listItems') or []
        if not isinstance(items, list) or any(not isinstance(row, dict) or not row.get('id') for row in items):
            raise StoreError('invalid_response', self.id)
        return {'name': data.get('name'), 'items_to_remove': items, 'count': len(items)}

    def delete_shopping_list(self, list_id):
        return self._shopping_request('delete', f'lists/{quote(list_id, safe="")}').status_code

    def clear_shopping_list_items(self, list_id):
        items = self.shopping_list_preview(list_id)['items_to_remove']
        if not items:
            return {'ok': True, 'deleted': 0, 'message': 'Lista ya estaba vacia'}
        response = self._shopping_request('delete', 'items', params=[('ids', row['id']) for row in items])
        return {'ok': True, 'deleted': len(items), 'status': response.status_code}
