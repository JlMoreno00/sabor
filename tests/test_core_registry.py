import json
import unittest
from dataclasses import dataclass
from unittest.mock import patch

from sabor.core.config import ConfigError, load_settings
from sabor.core.registry import Registry
from sabor.core.capabilities import describe


@dataclass
class Adapter:
    id: str
    kinds: frozenset = frozenset()


def registry(env=None, **kwargs):
    return Registry(load_settings(env or {}), **kwargs)


class RegistryTests(unittest.TestCase):
    def test_defaults(self):
        self.assertEqual(load_settings({}).requested_toolsets,
                         ('core', 'import', 'shopping_sync'))

    def test_empty(self):
        self.assertEqual(registry({'SABOR_TOOLSETS': ''}).enabled_toolsets, ())

    def test_unknown(self):
        with self.assertRaises(ConfigError):
            load_settings({'SABOR_TOOLSETS': 'secret-unknown'})

    def test_duplicates(self):
        self.assertEqual(load_settings({'SABOR_TOOLSETS': ' core,core, import '})
                         .requested_toolsets, ('core', 'import'))

    def test_partial_bring(self):
        for key in ('BRING_EMAIL', 'BRING_PASSWORD'):
            with self.subTest(key=key), self.assertRaises(ConfigError):
                load_settings({key: 'secret'})

    def test_llm_key(self):
        env = {'SABOR_LLM_MODEL': 'model', 'SABOR_LLM_BASE_URL': 'https://remote.test'}
        with self.assertRaises(ConfigError):
            load_settings(env)
        for host in ('localhost', '127.0.0.1'):
            self.assertTrue(load_settings(dict(env, SABOR_LLM_BASE_URL=f'http://{host}:11434/v1')).llm_configured)

    def test_video_selection(self):
        llm = {'SABOR_LLM_BASE_URL': 'http://localhost:11434/v1', 'SABOR_LLM_MODEL': 'm'}
        gemini = {'GEMINI_API_KEY': 'secret', 'VIDEO_RECIPE_GEMINI_MODEL': 'g'}
        for env, expected in (({}, None), (llm, 'openai_compatible'),
                              ({**llm, **gemini}, 'gemini_native'),
                              ({**llm, **gemini, 'SABOR_VIDEO_PROVIDER': 'openai_compatible'}, 'openai_compatible')):
            with self.subTest(expected=expected):
                self.assertEqual(registry(env).video_provider, expected)

    def test_gemini_model_keeps_0_1_default(self):
        settings = load_settings({'GEMINI_API_KEY': 'secret'})
        self.assertEqual(settings.video_recipe_gemini_model, 'gemini-2.5-flash-lite')
        self.assertTrue(settings.gemini_configured)
        self.assertEqual(load_settings({'GEMINI_API_KEY': 's', 'VIDEO_RECIPE_GEMINI_MODEL': 'g'})
                         .video_recipe_gemini_model, 'g')

    def test_explicit_unconfigured_video(self):
        self.assertIsNone(registry({'SABOR_VIDEO_PROVIDER': 'gemini_native'}).video_provider)

    def test_duplicate_id(self):
        with self.assertRaises(ConfigError):
            registry(import_factories=(lambda s: Adapter('same', frozenset({'text'})),),
                     sync_factories=(lambda s: Adapter('same'),))

    def test_duplicate_kind(self):
        with self.assertRaises(ConfigError):
            registry(import_factories=(lambda s: Adapter('one', frozenset({'text'})),
                                       lambda s: Adapter('two', frozenset({'text'}))))

    def test_intersection(self):
        r = registry({'SABOR_TOOLSETS': 'core,legacy_memory'})
        self.assertEqual(r.enabled_toolsets, ('legacy_memory',))
        self.assertEqual(r.disabled_toolsets['core'], 'missing_configuration')
        self.assertEqual(r.disabled_toolsets['import'], 'not_requested')

    def test_capabilities_safe(self):
        env = {'MEALIE_BASE_URL': 'https://private.test', 'MEALIE_API_TOKEN': 'token-secret',
               'BRING_EMAIL': 'private@example.test', 'BRING_PASSWORD': 'password-secret',
               'SABOR_LLM_BASE_URL': 'https://llm.private.test', 'SABOR_LLM_MODEL': 'm',
               'SABOR_LLM_API_KEY': 'key-secret', 'MEMORY_FILE': '/private/memory'}
        r = registry(env, import_factories=(lambda s: Adapter('mealie_native', frozenset({'url', 'image'})),),
                     sync_factories=(lambda s: Adapter('bring'),))
        output = describe(r, ['sabor_capabilities', 'import_recipe'], '0.2.0')
        self.assertTrue(output['ok'])
        self.assertTrue(output['data']['capabilities']['import']['configured'])
        serialized = json.dumps(output)
        for key, value in env.items():
            if key != 'SABOR_LLM_MODEL':
                self.assertNotIn(value, serialized)

    def test_pure(self):
        with patch('builtins.open', side_effect=AssertionError('I/O')):
            self.assertEqual(load_settings({}), load_settings({}))

    def test_invalid_url_and_provider_safe(self):
        for env in ({'MEALIE_BASE_URL': 'secret'}, {'SABOR_VIDEO_PROVIDER': 'secret'}):
            with self.assertRaises(ConfigError) as error:
                load_settings(env)
            self.assertNotIn('secret', str(error.exception))

    def test_legacy_and_mealie_configuration(self):
        r = registry({'SABOR_STORE': 'mealie',
                      'SABOR_TOOLSETS': 'legacy_pricing,legacy_nutrition,legacy_catalog',
                      'SABOR_MERCADONA_ENABLED': 'true'})
        self.assertEqual(r.enabled_toolsets, ('legacy_pricing', 'legacy_nutrition'))
        self.assertEqual(r.disabled_toolsets['legacy_catalog'], 'missing_configuration')
