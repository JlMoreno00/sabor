"""Run the reusable contract against isolated, disposable Postgres schemas."""
import os
from importlib import import_module
import unittest
from uuid import uuid4

from unittest.mock import patch

from sabor.store import Recipe, RecipePatch, RecipeSearch, StoreError
from tests.test_store_contract import CAPABILITIES, StoreContractMixin

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
            self.assertEqual(versions, [(1,), (2,)])
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

    def test_new_recipes_get_name_slugs(self):
        # #49 decision: Postgres matches SQLite (same sabor.store.slugs functions).
        for name, slug in (('Tortilla de patatas', 'tortilla-de-patatas'), ('Ñoquis con salsa', 'noquis-con-salsa'),
                           ('  Pollo al curry (fácil)! ', 'pollo-al-curry-facil')):
            self.assertEqual(self.store.create_recipe(Recipe(name=name)).slug, slug)
        slugs = [self.store.create_recipe(Recipe(name='Tortilla de patatas')).slug for _ in range(2)]
        self.assertEqual(slugs, ['tortilla-de-patatas-2', 'tortilla-de-patatas-3'])
        self.assertEqual(self.store.create_recipe(Recipe(name='Sopa', slug='mi-sopa')).slug, 'mi-sopa')
        with self.assertRaises(StoreError) as caught:
            self.store.create_recipe(Recipe(name='Otra', slug='mi-sopa'))
        self.assertEqual(caught.exception.code, 'conflict')
        nameless = self.store.create_recipe(Recipe(name='¡!')).slug
        assert nameless is not None
        self.assertTrue(nameless.isdigit())

    def test_existing_numeric_recipes_are_untouched(self):
        PostgresRecipeStore = import_module('sabor.store.postgres').PostgresRecipeStore
        # Simulate a pre-#49 creation, which always used the numeric serial.
        with patch.object(PostgresRecipeStore, '_name_slug', lambda self, db, name: self._id(db)):
            legacy = self.store.create_recipe(Recipe(name='Tortilla de patatas')).slug
        assert legacy is not None and legacy.isdigit()
        before = self.store.get_recipe(legacy)
        self.assertEqual(self.store.create_recipe(Recipe(name='Tortilla de patatas')).slug, 'tortilla-de-patatas')
        self.assertEqual(self.store.get_recipe(legacy), before)
        self.store.update_recipe(legacy, RecipePatch({'description': 'Con cebolla'}))
        self.assertEqual(self.store.get_recipe(legacy).description, 'Con cebolla')
        self.assertEqual({r.slug for r in self.store.search_recipes(RecipeSearch()).items} - {'soup'},
                         {legacy, 'tortilla-de-patatas'})

    def test_slug_collision_race_retries(self):
        # Another process takes the computed slug between the check and the insert: the
        # unique key rejects it and the store retries with the next suffix (#49).
        PostgresRecipeStore = import_module('sabor.store.postgres').PostgresRecipeStore
        other = PostgresRecipeStore(DSN, schema=self.schema)
        self.addCleanup(other.close)
        self.assertEqual(self.store.create_recipe(Recipe(name='Pan')).slug, 'pan')
        real = PostgresRecipeStore._name_slug
        calls = []

        def stale(store, db, name):
            calls.append(name)
            return 'pan' if len(calls) == 1 else real(store, db, name)
        with patch.object(PostgresRecipeStore, '_name_slug', stale):
            self.assertEqual(other.create_recipe(Recipe(name='Pan')).slug, 'pan-2')
        self.assertEqual(len(calls), 2)
        self.assertEqual(self.store.get_recipe('pan-2').name, 'Pan')

    def test_concurrent_slug_creation_is_unique(self):
        from concurrent.futures import ThreadPoolExecutor
        PostgresRecipeStore = import_module('sabor.store.postgres').PostgresRecipeStore
        stores = [PostgresRecipeStore(DSN, schema=self.schema) for _ in range(4)]
        for store in stores:
            self.addCleanup(store.close)
            store.ensure_ready()
        with ThreadPoolExecutor(max_workers=4) as pool:
            slugs = list(pool.map(lambda store: store.create_recipe(Recipe(name='Gazpacho')).slug, stores))
        self.assertEqual(sorted(slugs), ['gazpacho', 'gazpacho-2', 'gazpacho-3', 'gazpacho-4'])

    def test_concurrent_migration(self):
        from concurrent.futures import ThreadPoolExecutor
        PostgresRecipeStore = import_module('sabor.store.postgres').PostgresRecipeStore
        fresh = self.make_store()
        stores = [PostgresRecipeStore(DSN, schema=self.schema) for _ in range(3)]
        for store in stores:
            self.addCleanup(store.close)
        with ThreadPoolExecutor(max_workers=3) as pool:
            list(pool.map(lambda store: store.ensure_ready(), stores))
        self.assertEqual(fresh.schema_version(), 2)
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
        self.assertEqual(fresh.schema_version(), 2)


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
        self.assertEqual(result['store'], {'backend': 'postgres', 'reachable': True, 'schema_version': 2})
