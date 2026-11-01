"""#102 PR 2b: AI settings removed in 0.4.0 are ignored, never fatal, and reported."""
import importlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sabor.core.config import REMOVED_SETTINGS, load_settings
from sabor.core.config_file import apply_config_file, read_config_file
from sabor.secrets_store import SECRET_NAMES, required_secrets

ROOT = Path(__file__).resolve().parents[1]
# Values that 0.3 rejected or acted on; 0.4.0 must start with all of them set.
LEFTOVER = {'SABOR_LLM_BASE_URL': 'https://llm.example/v1', 'SABOR_LLM_MODEL': 'm', 'SABOR_LLM_API_KEY': 'k',
            'GEMINI_API_KEY': 'g', 'VIDEO_RECIPE_GEMINI_MODEL': 'model', 'SABOR_VIDEO_PROVIDER': 'not-a-provider'}


class RemovedAISettingsTests(unittest.TestCase):
    def test_leftover_environment_starts_and_is_reported(self):
        settings = load_settings(LEFTOVER)
        self.assertEqual(set(REMOVED_SETTINGS), set(LEFTOVER))
        self.assertEqual(settings.removed_settings, tuple(sorted(LEFTOVER)))
        for name in ('llm_base_url', 'llm_model', 'llm_api_key', 'gemini_api_key',
                     'video_recipe_gemini_model', 'video_provider', 'llm_configured', 'gemini_configured'):
            self.assertFalse(hasattr(settings, name), name)
        # A remote endpoint without a key used to fail startup; now it is ignored.
        self.assertEqual(load_settings({'SABOR_LLM_BASE_URL': 'https://llm.example/v1'}).removed_settings,
                         ('SABOR_LLM_BASE_URL',))
        self.assertEqual(load_settings({}).removed_settings, ())

    def test_leftover_config_file_and_secret_references_start(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'config.toml'
            path.write_text('[llm]\nbase_url = "https://llm.example/v1"\nmodel = "m"\n'
                            '[import]\nvideo_provider = "gemini_native"\ngemini_model = "x"\n'
                            '[secrets]\nllm_api_key = "keyring"\ngemini_api_key = "file"\n')
            read_config_file(path, explicit=True)
            env = {'SABOR_CONFIG': str(path)}
            with patch('keyring.get_password', side_effect=AssertionError('removed secrets are not read')):
                apply_config_file(env)
            self.assertEqual(load_settings(env).removed_settings,
                             ('SABOR_LLM_BASE_URL', 'SABOR_LLM_MODEL', 'SABOR_VIDEO_PROVIDER',
                              'VIDEO_RECIPE_GEMINI_MODEL'))

    def test_no_ai_secret_is_required_or_stored(self):
        self.assertNotIn('llm_api_key', SECRET_NAMES)
        self.assertNotIn('gemini_api_key', SECRET_NAMES)
        self.assertEqual(required_secrets(LEFTOVER), [])

    def test_no_ai_provider_remains_in_the_package(self):
        legacy = importlib.import_module('sabor.core.legacy_config').load_legacy_config({})
        for name in ('GEMINI_API_KEY', 'GEMINI_MODEL', '_GEMINI_FALLBACK_MODELS', '_GEMINI_BASE'):
            self.assertFalse(hasattr(legacy, name), name)
        for path in [ROOT / 'sabor_mcp.py', *(ROOT / 'sabor').rglob('*.py')]:
            text = path.read_text(encoding='utf-8')
            for endpoint in ('/chat/completions', 'generativelanguage.googleapis.com', 'api.openai.com'):
                self.assertNotIn(endpoint, text, path.relative_to(ROOT))


if __name__ == '__main__':
    unittest.main()
