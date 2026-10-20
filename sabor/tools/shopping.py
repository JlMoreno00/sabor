"""Shopping-list orchestration over the instance-owned store."""
from typing import Any
from sabor.core.toolsets import tool, mealie_ready
from sabor.core.envelope import fail
from sabor.store import StoreError, store_error_envelope


def _run(ctx, operation, value=None, confirm=False):
    store = ctx.store
    if store is None:
        return fail('provider_unconfigured', 'Mealie is not configured.', 'mealie')
    try:
        if operation == 'get':
            rows = store.get_shopping_lists()
            return {'ok': True, 'total': len(rows), 'lists': [
                {'id': row.id, 'name': row.name, 'items_count': row.items_count} for row in rows]}
        if operation in ('create', 'get_or_create'):
            if operation == 'get_or_create':
                matches = [row for row in store.get_shopping_lists() if row.name == value]
                if len(matches) > 1:
                    return fail('conflict', 'Multiple shopping lists have this exact name.', store.id)
                if matches:
                    return {'ok': True, 'id': matches[0].id, 'name': matches[0].name, 'created': False}
            row = store.create_shopping_list(value)
            result = {'ok': True, 'id': row.id, 'name': row.name}
            if operation == 'get_or_create':
                result['created'] = True
            return result
        if not confirm:
            data = store.shopping_list_preview(value)
            if operation == 'clear':
                data.pop('name', None)
            return {'ok': True, 'preview': True, 'list_id': value, **data}
        if operation == 'clear':
            return store.clear_shopping_list_items(value)
        status = store.delete_shopping_list(value)
        return {'ok': True, 'status': status if status is not None else 204}
    except StoreError as exc:
        return store_error_envelope(exc)


def _defer(operation, value=None, confirm=False) -> Any:
    return lambda ctx: _run(ctx, operation, value, confirm)


@tool('core', requires=mealie_ready, uses_context=True)
def get_shopping_lists() -> dict[str, Any]:
    """Devuelve todas las listas de compra del hogar con sus IDs y nombres."""
    return _defer('get')


@tool('core', requires=mealie_ready, uses_context=True)
def create_shopping_list(name: str) -> dict[str, Any]:
    """Crea una nueva lista de compra vacia con el nombre indicado."""
    return _defer('create', name)


@tool('core', requires=mealie_ready, uses_context=True)
def delete_shopping_list(list_id: str, confirm: bool = False) -> dict[str, Any]:
    """Preview deletion of a shopping list; pass confirm=True to delete it permanently."""
    return _defer('delete', list_id, confirm)


@tool('core', requires=mealie_ready, uses_context=True)
def clear_shopping_list(list_id: str, confirm: bool = False) -> dict[str, Any]:
    """Preview removal of all shopping items; pass confirm=True to clear the list."""
    return _defer('clear', list_id, confirm)


@tool('core', requires=mealie_ready, uses_context=True)
def get_or_create_shopping_list(name: str) -> dict[str, Any]:
    """Busca una lista de compra por nombre; si no existe, la crea. Devuelve siempre el ID."""
    return _defer('get_or_create', name)
