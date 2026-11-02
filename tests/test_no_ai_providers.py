"""#102 PR 2: Sabor contains no AI provider client; the client agent does extraction."""
import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch

from sabor.core.capabilities import describe
from sabor.core.config import load_settings
from sabor.core.context import assemble_context, build_context
from sabor.importing import tools as t
from sabor.store.fake import FakeRecipeStore

ROOT = Path(__file__).resolve().parents[1]
# Every AI setting a previous configuration may still carry; none may enable anything.
AI_ENV = {'SABOR_LLM_BASE_URL': 'http://localhost:11434/v1', 'SABOR_LLM_MODEL': 'vision',
          'SABOR_LLM_API_KEY': 'test', 'GEMINI_API_KEY': 'test', 'VIDEO_RECIPE_GEMINI_MODEL': 'test',
          'SABOR_VIDEO_PROVIDER': 'gemini_native'}


class NoAIProviderTests(unittest.TestCase):
    def setUp(self):
        network = [patch.object(t.requests, name, side_effect=AssertionError('Unexpected network'))
                   for name in ('get', 'post', 'put')]
        for p in network:
            p.start()
            self.addCleanup(p.stop)

    def run_import(self, source, kind='auto', store=None, **kwargs):
        cfg = load_settings(dict(AI_ENV, SABOR_STORE='sqlite'))
        ctx = build_context(cfg, store_factory=lambda s: store or FakeRecipeStore())
        return t._import_recipe(ctx, source, kind, kwargs.get('dry_run', True), 'en', 8, .55)

    def test_text_image_and_video_point_the_agent_to_create_recipe(self):
        # A video URL returns the media bundle (#102 PR 3, tests/test_video_bundle.py).
        for source, kind in (('Soup with salt and water, boiled', 'auto'), ('https://example.org/a.jpg', 'image'),
                             ('Soup', 'video'), ('Soup', 'text')):
            for dry_run in (True, False):
                result = self.run_import(source, kind, dry_run=dry_run)
                self.assertEqual(result['error']['code'], 'unsupported_operation', (source, kind))
                self.assertIn('create_recipe', result['error']['message'], (source, kind))

    def test_mealie_image_import_is_gone(self):
        from sabor.store.mealie import MealieRecipeStore
        self.assertEqual(MealieRecipeStore.native_import_kinds, frozenset({'url'}))
        self.assertFalse(hasattr(MealieRecipeStore, 'import_image'))
        store = MealieRecipeStore('http://mealie.invalid', 'token', session=t.requests)
        result = self.run_import('https://example.org/a.jpg', 'image', store=store, dry_run=False)
        self.assertEqual(result['error']['code'], 'unsupported_operation')

    def test_ai_settings_register_no_import_adapter(self):
        ctx = assemble_context(load_settings(AI_ENV))
        self.assertFalse(hasattr(ctx.registry, 'imports'))
        self.assertFalse(hasattr(ctx.registry, 'video_provider'))
        # The built-in importer is announced (#102 PR 3c); no AI adapter is.
        self.assertEqual({row['id'] for row in describe(ctx, (), 'test')['data']['capabilities']['import']['adapters']},
                         {'sabor'})
        text = str(describe(ctx, (), 'test'))
        for name in ('gemini', 'openai', 'llm'):
            self.assertNotIn(name, text.casefold())

    def test_no_ai_provider_client_ships(self):
        self.assertIsNone(importlib.util.find_spec('sabor.importing.gemini_client'))
        from sabor.importing import video
        for name in ('GeminiNativeVideo', 'OpenAICompatibleVideo', 'select_video', 'preview_video'):
            self.assertFalse(hasattr(video, name), name)
        self.assertFalse(hasattr(t, 'OpenAIImportProvider'))
        # Configuration still parses the removed keys until the next slice of #102.
        for path in [*(ROOT / 'sabor' / 'importing').glob('*.py'), *(ROOT / 'sabor' / 'store').glob('*.py')]:
            text = path.read_text(encoding='utf-8')
            for endpoint in ('/chat/completions', 'generativelanguage.googleapis.com', 'api.openai.com'):
                self.assertNotIn(endpoint, text, path.name)
