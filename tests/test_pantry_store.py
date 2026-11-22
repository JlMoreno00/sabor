"""Structured pantry storage on SQLite: merge, subtract, remove and preview (#144)."""
import sqlite3
from decimal import Decimal

import pytest

from sabor.core.config import load_settings
from sabor.pantry.store import PantryError, PantryStore, pantry_store_for
from sabor.store.sqlite import SqliteRecipeStore
from sabor.store.sqlite_schema import MIGRATIONS


@pytest.fixture
def store(tmp_path):
    return PantryStore(tmp_path / 'pantry.db')


def _rows(store):
    return [(i.food, i.quantity, i.unit, i.source) for i in store.list_items()]


def test_add_creates_items_with_canonical_units(store):
    effects = store.apply([{'op': 'add', 'name': 'Arroz bomba', 'quantity': '1', 'unit': 'kg',
                            'source': 'receipt', 'expires_on': '2027-01-31'}])
    assert [e['action'] for e in effects] == ['create']
    [item] = store.list_items()
    assert (item.name, item.food, item.quantity, item.unit) == ('Arroz bomba', 'arroz bomba', Decimal('1000'), 'g')
    assert (item.source, item.expires_on) == ('receipt', '2027-01-31')
    assert item.id and item.updated_at


def test_add_merges_same_food_and_dimension(store):
    store.apply([{'op': 'add', 'name': 'Leche', 'quantity': '1', 'unit': 'l'}])
    effects = store.apply([{'op': 'add', 'name': 'leche', 'quantity': '500', 'unit': 'ml'}])
    assert effects[0]['action'] == 'increase'
    assert _rows(store) == [('leche', Decimal('1500'), 'ml', 'manual')]


def test_incompatible_units_are_not_converted(store):
    # «1 paquete» has no known size: it never becomes grams by guesswork.
    store.apply([{'op': 'add', 'name': 'arroz', 'quantity': '1', 'unit': 'kg'}])
    store.apply([{'op': 'add', 'name': 'arroz', 'quantity': '2', 'unit': 'ud'}])
    assert sorted(_rows(store)) == [('arroz', Decimal('2'), 'ud', 'manual'),
                                    ('arroz', Decimal('1000'), 'g', 'manual')]


def test_subtract_never_goes_below_zero_and_reports_the_shortfall(store):
    store.apply([{'op': 'add', 'name': 'tomate', 'quantity': '300', 'unit': 'g'}])
    effects = store.apply([{'op': 'subtract', 'name': 'tomate', 'quantity': '500', 'unit': 'g'}])
    assert effects[0]['action'] == 'remove'
    assert effects[0]['shortfall'] == {'quantity': '200', 'unit': 'g'}
    assert store.list_items() == []


def test_subtract_unknown_food_changes_nothing(store):
    store.apply([{'op': 'add', 'name': 'sal', 'quantity': '1', 'unit': 'kg'}])
    effects = store.apply([{'op': 'subtract', 'name': 'azúcar', 'quantity': '100', 'unit': 'g'}])
    assert effects == [{'op': 'subtract', 'action': 'not_found', 'food': 'azucar'}]
    assert _rows(store) == [('sal', Decimal('1000'), 'g', 'manual')]


def test_set_and_remove_by_id(store):
    store.apply([{'op': 'add', 'name': 'huevos', 'quantity': '12', 'unit': 'ud'}])
    [item] = store.list_items()
    store.apply([{'op': 'set', 'id': item.id, 'quantity': '6', 'unit': 'ud', 'expires_on': None}])
    assert store.list_items()[0].quantity == Decimal('6')
    store.apply([{'op': 'remove', 'id': item.id}])
    assert store.list_items() == []


def test_item_without_quantity_means_there_is_some(store):
    store.apply([{'op': 'add', 'name': 'pimienta'}])
    [item] = store.list_items()
    assert (item.quantity, item.unit) == (None, None)


def test_preview_writes_nothing_and_matches_apply(store):
    changes = [{'op': 'add', 'name': 'pasta', 'quantity': '500', 'unit': 'g'}]
    preview = store.preview(changes)
    assert store.list_items() == []
    assert preview == store.apply(changes)


def test_invalid_changes_are_rejected_without_writing(store):
    store.apply([{'op': 'add', 'name': 'sal', 'quantity': '1', 'unit': 'kg'}])
    for bad in ({'op': 'explode', 'name': 'sal'}, {'op': 'add', 'name': ''},
                {'op': 'add', 'name': 'sal', 'quantity': '-1', 'unit': 'kg'},
                {'op': 'add', 'name': 'sal', 'quantity': '1', 'unit': 'kg', 'expires_on': '31/01/2027'},
                {'op': 'add', 'name': 'sal', 'source': 'magic'},
                {'op': 'remove', 'id': 'missing'}):
        with pytest.raises(PantryError):
            store.apply([{'op': 'add', 'name': 'pan'}, bad])
    assert _rows(store) == [('sal', Decimal('1000'), 'g', 'manual')]


def test_list_filters_by_food_and_expiry(store):
    store.apply([{'op': 'add', 'name': 'yogur', 'expires_on': '2026-11-25'},
                 {'op': 'add', 'name': 'arroz', 'expires_on': '2027-06-01'},
                 {'op': 'add', 'name': 'sal'}])
    assert [i.food for i in store.list_items(food='Yogur')] == ['yogur']
    assert [i.food for i in store.list_items(expiring_within_days=7, today='2026-11-22')] == ['yogur']


def test_sqlite_recipe_store_gets_the_pantry_table_in_migration_5(tmp_path):
    path = tmp_path / 'sabor.db'
    SqliteRecipeStore(path).ensure_ready()
    assert MIGRATIONS[-1][0] == 5
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT max(version) FROM schema_version").fetchone()[0] == 5
        assert connection.execute("SELECT name FROM sqlite_master WHERE name='pantry_items'").fetchone()


def test_pantry_lives_with_sqlite_and_in_the_data_dir_for_mealie(tmp_path):
    def path(env):
        store = pantry_store_for(load_settings(env), env)
        return None if store is None else store.path

    assert path({'SABOR_DATA_DIR': str(tmp_path)}) == tmp_path / 'sabor.db'
    assert path({'SABOR_DATA_DIR': str(tmp_path), 'MEALIE_BASE_URL': 'http://mealie',
                 'MEALIE_API_TOKEN': 't'}) == tmp_path / 'pantry.db'
    # Postgres has no pantry yet (a later phase): no store, never a silent fallback.
    assert path({'SABOR_DATA_DIR': str(tmp_path), 'SABOR_STORE': 'postgres',
                 'SABOR_DATABASE_URL': 'postgresql://u@localhost/db'}) is None
