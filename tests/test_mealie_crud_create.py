"""#102: Mealie recipes are created through its CRUD API, never through its scrapers.

Mealie's /create/url and /create/html-or-json run its scraper chain, which falls back to
the AI provider configured in Mealie. Sabor creates with POST /api/recipes and PATCH, and
downloads the image itself with its SSRF-safe fetch, then uploads the bytes (José's
option A): Mealie never fetches a URL taken from a page.
"""
import unittest
from pathlib import Path
from unittest.mock import patch

from fakes.mealie_api import MealieAPI, Response
from sabor.core.config import load_settings
from sabor.core.capabilities import describe
from sabor.core.context import assemble_context, build_context
from sabor.importing import tools as t
from sabor.store import Ingredient, Instruction, NamedRef, RecipeDraft, StoreError
from sabor.store.mealie import MealieRecipeStore

SCRAPERS = ('/create/', '/test-scrape-url')
PAGE = (Path(__file__).parent / 'fixtures' / 'import' / 'scraper.html').read_text()


class RecordingAPI(MealieAPI):
    def __init__(self):
        super().__init__()
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append(('GET', url, kwargs.get('json')))
        return super().get(url, **kwargs)

    def post(self, url, **kwargs):
        self.calls.append(('POST', url, kwargs.get('json')))
        return super().post(url, **kwargs)

    def patch(self, url, **kwargs):
        self.calls.append(('PATCH', url, kwargs.get('json')))
        return super().patch(url, **kwargs)

    def put(self, url, **kwargs):
        self.calls.append(('PUT', url, kwargs.get('json') or kwargs.get('data')))
        return super().put(url, **kwargs)

    def delete(self, url, **kwargs):
        self.calls.append(('DELETE', url, kwargs.get('json')))
        return super().delete(url, **kwargs)

    def scraper_calls(self):
        return [url for _method, url, _body in self.calls if any(part in url for part in SCRAPERS)]


class MealieCrudCreateTests(unittest.TestCase):
    def setUp(self):
        self.api = RecordingAPI()
        self.store = MealieRecipeStore('https://mealie.invalid', 'token', self.api)

    def test_create_recipe_uses_crud_only(self):
        draft = RecipeDraft(name='Soup', description='Hot', recipeYield='2', totalTime='PT1H30M',
                            prepTime='PT10M', recipeIngredient=(Ingredient(originalText='1 tomato'),),
                            recipeInstructions=(Instruction(text='Boil'),), tags=(NamedRef(name='hot'),))
        ref = self.store.create_recipe(draft)
        self.assertEqual(ref.to_public(), {'ok': True, 'status': 201, 'body': '"soup"', 'slug': 'soup'})
        self.assertEqual(self.api.scraper_calls(), [])
        self.assertEqual(self.api.calls[0], ('POST', 'https://mealie.invalid/api/recipes', {'name': 'Soup'}))
        recipe = self.api.recipes['soup']
        self.assertEqual(recipe['description'], 'Hot')
        self.assertEqual(recipe['recipeYield'], '2')
        self.assertEqual((recipe['totalTime'], recipe['prepTime']), ('1 h 30 min', '10 min'))
        self.assertEqual(recipe['recipeIngredient'], [{'note': '1 tomato', 'originalText': '1 tomato'}])
        self.assertEqual(recipe['recipeInstructions'], [{'text': 'Boil'}])
        self.assertEqual([tag['name'] for tag in recipe['tags']], ['hot'])

    def test_create_recipe_tool_keeps_source_url_and_keywords(self):
        from sabor.tools.recipes import _CreateDraft
        draft = _CreateDraft(name='Soup', jsonld={'@context': 'https://schema.org', '@type': 'Recipe',
                                                  'name': 'Soup', 'recipeIngredient': ['1 tomato'],
                                                  'recipeInstructions': ['Boil'], 'keywords': ['hot'],
                                                  'url': 'https://example.org/soup', 'totalTime': '20 min'})
        self.store.create_recipe(draft)
        self.assertEqual(self.api.scraper_calls(), [])
        recipe = self.api.recipes['soup']
        self.assertEqual(recipe['orgURL'], 'https://example.org/soup')
        self.assertEqual(recipe['totalTime'], '20 min')
        self.assertEqual([tag['name'] for tag in recipe['tags']], ['hot'])

    def test_a_failed_update_removes_the_half_created_recipe(self):
        def fail(url, **kwargs):
            self.api.calls.append(('PATCH', url, kwargs.get('json')))
            return Response({}, 500)
        self.api.patch = fail
        with self.assertRaises(StoreError) as caught:
            self.store.create_recipe(RecipeDraft(name='Soup', recipeIngredient=(Ingredient(originalText='1 tomato'),)))
        self.assertEqual(caught.exception.code, 'upstream_error')
        self.assertEqual(self.api.recipes, {})
        self.assertIn(('DELETE', 'https://mealie.invalid/api/recipes/soup', None), self.api.calls)

    def test_any_failure_after_creation_removes_the_recipe(self):
        from sabor.tools.recipes import _CreateDraft
        draft = _CreateDraft(name='Soup', jsonld={'name': 'Soup', 'recipeInstructions': 5})
        with self.assertRaises(TypeError):
            self.store.create_recipe(draft)
        self.assertEqual(self.api.recipes, {})
        self.assertIn(('DELETE', 'https://mealie.invalid/api/recipes/soup', None), self.api.calls)

    def import_page_with_image(self, fetched):
        page = PAGE.replace('"recipeYield":"2"', '"recipeYield":"2","image":"/img/sopa.jpg"')
        ctx = build_context(load_settings({'MEALIE_BASE_URL': 'https://mealie.invalid', 'MEALIE_API_TOKEN': 't',
                                           'SABOR_NORMALIZE_IMPORTS': 'false'}),
                            store_factory=lambda s: self.store)
        with patch.object(t, 'html_bytes', return_value=(page.encode(), 'text/html')), \
                patch('sabor.importing.images.fetch_recipe_image', side_effect=fetched) as fetch, \
                patch('sabor.tools.nutrition._nutrition_after_import', return_value={}):
            preview = t._import_recipe(ctx, 'https://example.org/sopa', 'url', True, 'es', 8, .55)
            self.assertEqual(self.api.calls, [])
            self.assertEqual((preview['data']['provider'], preview['data']['candidates'][0]['name']), ('sabor', 'Sopa'))
            result = t._import_recipe(ctx, 'https://example.org/sopa', 'url', False, 'es', 8, .55)
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['data']['created'], ['sopa'])
        self.assertEqual(self.api.scraper_calls(), [])
        # Mealie never receives the image URL to fetch.
        self.assertFalse([c for c in self.api.calls if c[0] == 'POST' and c[1].endswith('/image')])
        self.assertEqual([c.args[0] for c in fetch.call_args_list], ['https://example.org/img/sopa.jpg'])
        return result

    def test_url_import_with_mealie_uploads_the_image_sabor_downloaded(self):
        result = self.import_page_with_image(lambda url: (b'\x89PNG-bytes', 'image/png'))
        self.assertEqual(self.api.recipes['sopa']['recipeIngredient'][0]['originalText'], '200 g harina')
        self.assertIn(('PUT', 'https://mealie.invalid/api/recipes/sopa/image', {'extension': 'png'}), self.api.calls)
        self.assertEqual(self.api.recipes['sopa']['image'], (b'\x89PNG-bytes', 'png'))
        self.assertEqual(result['data']['images']['sopa'], {'stored': True, 'source_url': 'https://example.org/img/sopa.jpg',
                                                            'content_type': 'image/png', 'bytes': 10})

    def test_a_failed_download_uploads_nothing_and_keeps_the_recipe(self):
        from sabor.importing.images import ImageError
        def refused(url):
            raise ImageError('image_download_failed')
        result = self.import_page_with_image(refused)
        self.assertFalse([c for c in self.api.calls if c[1].endswith('/image')])
        self.assertNotIn('image', self.api.recipes['sopa'])
        self.assertEqual(result['data']['images']['sopa'], {'stored': False, 'source_url': 'https://example.org/img/sopa.jpg',
                                                            'warning': 'image_download_failed'})

    def test_sabors_fetch_refuses_the_carrier_grade_nat_range_mealie_allowed(self):
        # CodeRabbit on #135: Mealie 3.22 lets 100.64.0.0/10 (Tailscale) through; Sabor does not.
        for address in ('100.64.0.1', '100.100.100.100', '10.0.0.5', '127.0.0.1', '169.254.169.254'):
            self.assertFalse(t.public_address(address), address)
        self.assertTrue(t.public_address('93.184.216.34'))

    def test_a_duplicate_name_is_skipped_like_the_other_stores(self):
        self.store.create_recipe(RecipeDraft(name='Sopa'))
        ctx = build_context(load_settings({'MEALIE_BASE_URL': 'https://mealie.invalid', 'MEALIE_API_TOKEN': 't'}),
                            store_factory=lambda s: self.store)
        with patch.object(t, 'html_bytes', return_value=(PAGE.encode(), 'text/html')):
            result = t._import_recipe(ctx, 'https://example.org/sopa', 'url', False, 'es', 8, .55)
        self.assertEqual(result['data']['skips'], [{'name': 'Sopa', 'slug': 'sopa', 'reason': 'duplicate'}])
        self.assertEqual(result['data']['created'], [])

    def test_capabilities_announce_only_sabor_importers(self):
        ctx = assemble_context(load_settings({'MEALIE_BASE_URL': 'https://mealie.invalid', 'MEALIE_API_TOKEN': 't'}))
        rows = describe(ctx, (), 'test')['data']['capabilities']['import']['adapters']
        self.assertEqual({(row['id'], kind) for row in rows for kind in row['kinds']},
                         {('sabor', 'url'), ('sabor', 'video')})
        for name in ('native_import_kinds', 'preview_url', 'import_url'):
            self.assertFalse(hasattr(MealieRecipeStore, name), name)

    def test_no_store_module_names_a_mealie_scraper_route(self):
        root = Path(__file__).resolve().parents[1] / 'sabor'
        for path in root.rglob('*.py'):
            text = path.read_text(encoding='utf-8')
            for route in ('/create/url', '/create/html-or-json', 'test-scrape-url', 'useOpenAI'):
                self.assertNotIn(route, text, f'{path.name}: {route}')


if __name__ == '__main__':
    unittest.main()
