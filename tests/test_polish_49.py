"""Regressions for the CodeRabbit minors grouped in #49; no network or live database."""
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from sabor.store import Recipe, RecipeSearch, StoreError, WriteResult


class SqliteLastMadeTests(unittest.TestCase):
    def test_equal_timestamps_tie_break_by_event_order(self):
        from sabor.store.sqlite import SqliteRecipeStore
        with tempfile.TemporaryDirectory() as directory:
            store = SqliteRecipeStore(Path(directory) / 'sabor.db')
            store.ensure_ready()
            for slug in ('newer', 'older', 'never'):
                store.create_recipe(Recipe(slug=slug, name=slug))
            for slug in ('older', 'newer'):
                store.log_cooking_event(slug, 'made', '')
            # Migration v3 stamps every pre-existing event with the same time.
            with store._connect() as db:
                db.execute("UPDATE cooking_events SET created_at='2026-01-01T00:00:00+00:00'")
            for direction, expected in (('desc', ['newer', 'older', 'never']),
                                        ('asc', ['older', 'newer', 'never'])):
                page = store.search_recipes(RecipeSearch(sort='last_made', direction=direction))
                self.assertEqual([r.slug for r in page.items], expected)
            # The tie-break uses the event that holds the latest date, not the last inserted one.
            store.log_cooking_event('older', 'made', '')
            with store._connect() as db:
                db.execute("UPDATE cooking_events SET created_at='2025-01-01T00:00:00+00:00' "
                           "WHERE rowid=(SELECT max(rowid) FROM cooking_events)")
            page = store.search_recipes(RecipeSearch(sort='last_made', direction='desc'))
            self.assertEqual([r.slug for r in page.items], ['newer', 'older', 'never'])
            # Other sorts keep their recipe insertion-order tie-break.
            page = store.search_recipes(RecipeSearch(sort='created_at'))
            self.assertEqual(len(page.items), 3)


class PreferencesEncodingTests(unittest.TestCase):
    def test_text_utf8_cannot_encode_is_rejected_without_writing(self):
        from sabor.preferences import FilePreferences, _preferences_call
        with tempfile.TemporaryDirectory() as directory:
            backend = FilePreferences(Path(directory) / 'preferences.json')
            self.assertTrue(_preferences_call(backend, {'set': {'notes': ['café']}}, True)['ok'])
            original = backend.path.read_bytes()
            for operation in ('set', 'add', 'remove'):
                result = _preferences_call(backend, {operation: {'notes': ['\ud800']}}, True)
                self.assertFalse(result['ok'])
                self.assertEqual(result['error']['code'], 'invalid_argument')
                self.assertEqual(backend.path.read_bytes(), original)
                self.assertEqual(sorted(p.name for p in Path(directory).iterdir()), ['preferences.json'])


class PartialShoppingTests(unittest.TestCase):
    def test_partial_failure_keeps_operation_data(self):
        from sabor.tools.shopping_items import _add_to_shopping_list
        store = MagicMock()
        store.validate_shopping_list_id.side_effect = lambda value, **_: value
        store.add_shopping_items.return_value = WriteResult(changed_ids=('one',))
        with patch('sabor.tools.shopping_items.load_pantry', return_value=([], True)):
            result = _add_to_shopping_list(SimpleNamespace(store=store), 'list', items=['rice', 'beans'])
        self.assertFalse(result['ok'])
        self.assertEqual(result['error']['code'], 'partial_failure')
        self.assertEqual(result['data']['completed'], ['one'])
        self.assertEqual(result['data']['failed'], [{'source': 'items', 'unconfirmed_count': 1}])
        self.assertEqual(result['data']['list_id'], 'list')
        self.assertEqual(result['data']['added'], {'items': 1, 'recipe_occurrences': 0})
        self.assertEqual(result['data']['pantry_warning'], 'preferences_unreadable')


class ConfigPathTests(unittest.TestCase):
    def test_tilde_expands_and_paths_are_normalized(self):
        from sabor.core.config_file import config_path
        with tempfile.TemporaryDirectory() as home:
            home = Path(home).resolve()
            env = {'HOME': str(home), 'USERPROFILE': str(home)}
            with patch.dict(os.environ, env, clear=True):
                self.assertEqual(config_path(dict(env, SABOR_CONFIG='~/a/../config.toml'), 'linux'),
                                 home / 'config.toml')
                for platform, root in (('linux', '.config'), ('darwin', 'Library/Application Support'),
                                       ('win32', 'AppData/Roaming')):
                    self.assertEqual(config_path(env, platform), home / root / 'sabor/config.toml')
                self.assertEqual(config_path(dict(env, XDG_CONFIG_HOME='~/custom'), 'linux'),
                                 home / 'custom/sabor/config.toml')

    def test_without_any_home_the_path_is_never_a_literal_tilde(self):
        from sabor.core.config import ConfigError
        from sabor.core.config_file import config_path
        with patch('pathlib.Path.home', side_effect=RuntimeError('no home')):
            with self.assertRaisesRegex(ConfigError, 'set SABOR_CONFIG'):
                config_path({}, 'linux')
            # An explicit absolute SABOR_CONFIG needs no home directory.
            self.assertEqual(config_path({'SABOR_CONFIG': '/etc/sabor/../sabor/config.toml'}, 'linux'),
                             Path('/etc/sabor/config.toml'))


class DoctorMealieUrlTests(unittest.TestCase):
    def test_mealie_store_without_url_is_a_config_error(self):
        from sabor.doctor_cli import main
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / 'config.toml'
            config.touch()
            out = io.StringIO()
            with patch.dict(os.environ, {'SABOR_CONFIG': str(config), 'SABOR_STORE': 'mealie',
                                         'SABOR_DATA_DIR': directory}, clear=True), \
                    patch('requests.get') as probe, contextlib.redirect_stdout(out):
                code = main(['doctor', '--json'])
            probe.assert_not_called()
        result = json.loads(out.getvalue())
        self.assertEqual(code, 2)
        self.assertEqual(result['error'], 'Mealie store requires a base URL')


class CatalogSettingsTests(unittest.TestCase):
    def run_catalog(self, env, result):
        from sabor import catalog_cli
        out, err = io.StringIO(), io.StringIO()
        with patch.dict(os.environ, env, clear=True), \
                patch('sabor.integrations.mealie._reconcile_spanish_catalog', return_value=result), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = catalog_cli.run_catalog('status')
        return code, out.getvalue(), err.getvalue()

    def test_unrelated_invalid_setting_does_not_block_catalog(self):
        env = {'MEALIE_BASE_URL': 'http://mealie:9000', 'MEALIE_API_TOKEN': 'TOKEN_SENTINEL',
               'SABOR_LLM_BASE_URL': 'not a url', 'SABOR_LANGUAGE': 'fr'}
        code, out, err = self.run_catalog(env, {'ok': True, 'units': {}, 'foods': {}})
        self.assertEqual((code, err), (0, ''))
        self.assertTrue(json.loads(out)['ok'])

    def test_nested_error_bodies_are_removed(self):
        env = {'MEALIE_BASE_URL': 'http://mealie:9000', 'MEALIE_API_TOKEN': 'TOKEN_SENTINEL'}
        result = {'ok': False, 'units': {'errors': [{'name': 'g', 'body': 'TOKEN_SENTINEL secret'}]},
                  'foods': {'errors': [{'name': 'sal', 'body': '<html>'}]}}
        code, out, _ = self.run_catalog(env, result)
        self.assertEqual(code, 1)
        printed = json.loads(out)
        self.assertEqual(printed['units']['errors'], [{'name': 'g'}])
        self.assertEqual(printed['foods']['errors'], [{'name': 'sal'}])
        self.assertNotIn('TOKEN_SENTINEL', out)


class PostgresProbeRetryTests(unittest.TestCase):
    def test_probe_retries_once_on_connection_loss_and_never_migrates(self):
        try:
            import psycopg
        except ImportError:
            self.skipTest('requires psycopg')
        from sabor.store.postgres import PostgresRecipeStore
        for failure, retried in ((psycopg.OperationalError, True), (psycopg.ProgrammingError, False)):
            with self.subTest(failure=failure.__name__):
                store = PostgresRecipeStore('postgres:///unused')
                broken, fresh = MagicMock(closed=False), MagicMock(closed=False)
                broken.execute.side_effect = failure('PRIVATE_SENTINEL')
                fresh.execute.return_value.fetchone.side_effect = [('sabor.schema_version',), (2,)]
                store._connection = broken

                def reopen():
                    store._connection = fresh
                with patch.object(store, '_open', side_effect=reopen) as opened, \
                        patch('psycopg.sql.Identifier'), \
                        patch('sabor.store.postgres.migrate') as migrate:
                    if retried:
                        self.assertEqual(store.probe_schema_version(), 2)
                        broken.close.assert_called_once_with()
                        opened.assert_called_once_with()
                    else:
                        with self.assertRaises(StoreError) as caught:
                            store.probe_schema_version()
                        self.assertEqual(caught.exception.code, 'upstream_error')
                        opened.assert_not_called()
                    migrate.assert_not_called()


class UrlExtractionTests(unittest.TestCase):
    def extract(self, data):
        from sabor.importing.scrape import recipe_from_html
        html = ('<script type="application/ld+json">' + json.dumps(data) + '</script>').encode()
        with patch('recipe_scrapers.scrape_html', side_effect=ValueError):
            return recipe_from_html(html, 'https://example.org/soup')

    def test_blank_lines_are_dropped_and_yield_is_text(self):
        for value, expected in ((2, '2'), (2.5, '2.5'), (['4', '4 raciones'], '4'),
                                ({'value': 2}, None), (True, None), ('2 personas', '2 personas')):
            with self.subTest(value=value):
                result = self.extract({'@type': 'Recipe', 'name': 'Sopa',
                                       'recipeIngredient': [' ', 'arroz', ''],
                                       'recipeInstructions': ['', {'@type': 'HowToStep', 'text': 'Cocer'}, '  '],
                                       'recipeYield': value})
                assert result is not None
                self.assertEqual(result['recipeIngredient'], ['arroz'])
                self.assertEqual(result['recipeInstructions'], [{'@type': 'HowToStep', 'text': 'Cocer'}])
                self.assertEqual(result['recipeYield'], expected)

    def test_non_text_ingredient_skips_to_the_next_recipe(self):
        from sabor.importing.scrape import recipe_from_html
        bad = {'@type': 'Recipe', 'name': 'Mala', 'recipeIngredient': ['arroz', None],
               'recipeInstructions': ['Cocer']}
        good = {'@type': 'Recipe', 'name': 'Buena', 'recipeIngredient': ['sal'], 'recipeInstructions': ['Cocer']}
        html = ''.join('<script type="application/ld+json">' + json.dumps(d) + '</script>' for d in (bad, good))
        with patch('recipe_scrapers.scrape_html', side_effect=ValueError):
            result = recipe_from_html(html.encode(), 'https://example.org/soup')
        assert result is not None
        self.assertEqual(result['name'], 'Buena')

    def test_scraper_path_is_cleaned_too(self):
        from sabor.importing.scrape import recipe_from_html
        scraper = MagicMock()
        scraper.title.return_value = 'Sopa'
        scraper.ingredients.return_value = ['arroz', '  ']
        scraper.instructions_list.return_value = ['', 'Cocer']
        scraper.yields.return_value = 4
        with patch('recipe_scrapers.scrape_html', return_value=scraper):
            result = recipe_from_html(b'<html></html>', 'https://example.org/soup')
        assert result is not None
        self.assertEqual((result['recipeIngredient'], result['recipeInstructions'], result['recipeYield']),
                         (['arroz'], ['Cocer'], '4'))

    def test_only_blank_ingredients_is_not_a_recipe(self):
        self.assertIsNone(self.extract({'@type': 'Recipe', 'name': 'Sopa', 'recipeIngredient': [' ', ''],
                                        'recipeInstructions': ['Cocer']}))


if __name__ == '__main__':
    unittest.main()
