"""Run the reusable contract against isolated, disposable Postgres schemas."""
import os
from importlib import import_module
import unittest
from uuid import uuid4

from sabor.store import Ingredient, Recipe
from tests.test_store_contract import CAPABILITIES, StoreContractMixin
from sabor.store.postgres_schema import MIGRATIONS as POSTGRES_MIGRATIONS

try:
    import psycopg
    from psycopg import sql
except ImportError:
    psycopg = None
    sql = None

DSN = os.environ.get('SABOR_TEST_DATABASE_URL', '')


@unittest.skipUnless(DSN and psycopg, 'requires SABOR_TEST_DATABASE_URL and psycopg')
class PostgresStoreContract(StoreContractMixin, unittest.TestCase):
    capabilities = CAPABILITIES - {'rating_sort'}

    def make_store(self):
        PostgresRecipeStore = import_module('sabor.store.postgres').PostgresRecipeStore
        assert psycopg is not None and sql is not None
        schema = 'test_' + uuid4().hex
        store = PostgresRecipeStore(DSN, schema=schema)
        def cleanup():
            assert psycopg is not None and sql is not None
            store.close()
            with psycopg.connect(DSN) as connection:
                connection.execute(sql.SQL('DROP SCHEMA IF EXISTS {} CASCADE').format(sql.Identifier(schema)))
        self.addCleanup(cleanup)
        self.schema = schema
        return store

    def test_migrations_and_rls(self):
        assert psycopg is not None and sql is not None
        self.store.ensure_ready()
        self.store.ensure_ready()
        with psycopg.connect(DSN) as connection:
            versions = connection.execute(sql.SQL('SELECT version FROM {}.schema_version').format(
                sql.Identifier(self.schema))).fetchall()
            self.assertEqual(versions, [(v,) for v, _ in POSTGRES_MIGRATIONS])
            tables = connection.execute('SELECT relrowsecurity FROM pg_class c JOIN pg_namespace n '
                                        'ON c.relnamespace=n.oid WHERE n.nspname=%s AND c.relkind=%s',
                                        (self.schema, 'r')).fetchall()
            self.assertTrue(tables)
            self.assertTrue(all(row[0] for row in tables))
            exists = connection.execute("SELECT 1 FROM pg_roles WHERE rolname='anon'").fetchone()
            if not exists:
                connection.execute('CREATE ROLE anon NOLOGIN')
            # Roll back both SET ROLE and the test-only role, if newly created.
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                connection.execute('SET LOCAL ROLE anon')
                connection.execute(sql.SQL('SELECT * FROM {}.recipes').format(sql.Identifier(self.schema)))
            connection.rollback()

    def test_normalize_splits_enumerations(self):
        # #49 decision, shared with SQLite through ingredients_es.normalize_row.
        ref = self.store.create_recipe(Recipe(name='Bizcocho', recipeIngredient=(
            Ingredient(originalText='2 g de harina y azúcar', referenceId='ref-1'),
            Ingredient(originalText='1 cebolla, picada'))))
        assert ref.slug is not None
        self.assertEqual(self.store.normalize_imported(ref.slug)['accepted'], 1)
        rows = self.store.get_recipe(ref.slug).recipeIngredient
        assert rows is not None
        self.assertEqual([(r.quantity, r.unit.name if r.unit else None, r.food.name if r.food else None)
                          for r in rows], [(2, 'gramo', 'harina'), (2, 'gramo', 'azúcar'), (None, None, None)])
        self.assertEqual([r.referenceId for r in rows], ['ref-1', None, None])
        self.assertEqual(rows[1].note, 'cantidad dudosa: línea con varios ingredientes')

    def test_concurrent_migration(self):
        from concurrent.futures import ThreadPoolExecutor
        PostgresRecipeStore = import_module('sabor.store.postgres').PostgresRecipeStore
        fresh = self.make_store()
        stores = [PostgresRecipeStore(DSN, schema=self.schema) for _ in range(3)]
        for store in stores:
            self.addCleanup(store.close)
        with ThreadPoolExecutor(max_workers=3) as pool:
            list(pool.map(lambda store: store.ensure_ready(), stores))
        self.assertEqual(fresh.schema_version(), POSTGRES_MIGRATIONS[-1][0])
        self.assertEqual(stores[0].get_shopping_lists(), [])

    def test_search_phrase_and_diacritics(self):
        from sabor.store import Recipe, RecipeSearch, RecipePatch, Ingredient
        self.store.create_recipe(Recipe(slug='arroz', name='Paella', description='Comida fácil,\n  sabrosa',
                                       recipeIngredient=(Ingredient(originalText='azúcar moreno'),)))
        for query in ('azucar moreno', 'comida facil', 'PAEL', 'sabrosa', 'facil sabrosa',
                      'comida   facil', 'fácil, sabrosa'):
            self.assertEqual([r.slug for r in self.store.search_recipes(RecipeSearch(query=query)).items], ['arroz'])
        for query in ('moreno azucar', '%', '_', 'azucar inexistente'):
            self.assertEqual(self.store.search_recipes(RecipeSearch(query=query)).total, 0)
        self.store.update_recipe('arroz', RecipePatch({'recipeIngredient': ()}))
        self.assertEqual(self.store.search_recipes(RecipeSearch(query='azucar')).total, 0)


    def test_atomic_rollback_and_removal(self):
        from sabor.store import ItemDraft, RecipeOccurrence, StoreError
        target = self.store.create_shopping_list('Week').id
        self.store.add_shopping_items(target, [ItemDraft(note='salt'), ItemDraft(note='pepper')])
        before = self.store.get_shopping_items(target).items
        with self.assertRaises(StoreError):
            self.store.add_recipe_occurrences(target, [RecipeOccurrence('soup'), RecipeOccurrence('missing')])
        self.assertEqual(self.store.get_shopping_items(target).items, before)
        self.store.remove_shopping_items(target, [before[0].id])
        self.assertEqual(self.store.get_shopping_items(target).items, (before[1],))
        self.store.delete_shopping_list(target)
        self.assert_code('not_found', lambda: self.store.get_shopping_items(target))

    def test_migration_rollback_and_advisory_lock(self):
        assert psycopg is not None and sql is not None
        from concurrent.futures import ThreadPoolExecutor
        from hashlib import sha256
        from threading import Event
        from unittest.mock import patch
        from sabor.store import StoreError
        from sabor.store import postgres_schema
        fresh = self.make_store()
        entered = Event()
        lock = int.from_bytes(sha256(('sabor:' + self.schema).encode()).digest()[:8], 'big', signed=True)
        def fail(connection, schema):
            assert psycopg is not None
            entered.set()
            raise psycopg.OperationalError('test migration failure')
        with psycopg.connect(DSN, autocommit=True) as connection, ThreadPoolExecutor(max_workers=1) as pool:
            with patch.object(postgres_schema, 'MIGRATIONS', ((1, fail),)):
                with connection.transaction():
                    connection.execute('SELECT pg_advisory_xact_lock(%s)', (lock,))
                    task = pool.submit(fresh.ensure_ready)
                    self.assertFalse(entered.wait(0.1), 'migration ran without acquiring the schema lock')
                with self.assertRaises(StoreError):
                    task.result(timeout=5)
            self.assertTrue(entered.is_set())
            exists = connection.execute('SELECT 1 FROM pg_namespace WHERE nspname=%s', (self.schema,)).fetchone()
            self.assertIsNone(exists, 'failed migration committed partial schema state')
        fresh.ensure_ready()
        self.assertEqual(fresh.schema_version(), POSTGRES_MIGRATIONS[-1][0])


    def test_doctor_reports_schema_version(self):
        import contextlib
        import io
        import json
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from unittest.mock import patch
        from sabor.doctor_cli import main
        assert psycopg is not None

        def doctor(store):
            output = io.StringIO()
            with TemporaryDirectory() as directory, patch.dict(os.environ, {
                    'SABOR_STORE': 'postgres', 'SABOR_DATABASE_URL': DSN}, clear=True):
                with patch('sabor.doctor_cli.config_path', return_value=Path(directory) / 'config.toml'):
                    with patch('sabor.core.context.postgres_store_factory', return_value=store):
                        with contextlib.redirect_stdout(output):
                            code = main(['doctor', '--json'])
            return code, json.loads(output.getvalue())

        def schema_exists(name):
            with psycopg.connect(DSN) as connection:
                return connection.execute('SELECT 1 FROM pg_namespace WHERE nspname=%s', (name,)).fetchone()

        # Read-only: a missing store schema is reported, never created or migrated.
        migrated = self.store
        fresh = self.make_store()  # new random schema, cleaned up by make_store
        code, result = doctor(fresh)
        self.assertEqual(code, 0, result)
        self.assertEqual(result['store'], {'backend': 'postgres', 'reachable': True, 'schema_version': None})
        self.assertIn('Postgres schema does not exist yet; Sabor creates it on first start', result['warnings'])
        self.assertIsNone(schema_exists(self.schema))
        migrated.ensure_ready()
        code, result = doctor(migrated)
        self.assertEqual(code, 0, result)
        self.assertEqual(result['store'], {'backend': 'postgres', 'reachable': True,
                                            'schema_version': POSTGRES_MIGRATIONS[-1][0]})
