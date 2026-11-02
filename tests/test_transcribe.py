"""#102 PR 4: optional local transcription with faster-whisper; no AI provider is called."""
import io
import json
import subprocess
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from sabor.core.config import ConfigError, load_settings
from sabor.importing import transcribe as tr


class AvailabilityTests(unittest.TestCase):
    def test_available_only_when_the_extra_is_installed(self):
        with patch.object(tr.importlib.util, 'find_spec', return_value=None) as find:
            self.assertFalse(tr.available())
        find.assert_called_once_with('faster_whisper')
        with patch.object(tr.importlib.util, 'find_spec', return_value=object()):
            self.assertTrue(tr.available())


class SubprocessTests(unittest.TestCase):
    def run_transcribe(self, result=None, side_effect=None):
        with patch.object(tr.subprocess, 'run', return_value=result, side_effect=side_effect) as run:
            out = tr.transcribe(Path('/tmp/x/video.mp4'), 'base')
        return out, run

    def test_runs_isolated_and_bounded(self):
        out, run = self.run_transcribe(Mock(returncode=0, stdout=json.dumps({'language': 'es', 'text': 'Dos huevos'})))
        self.assertEqual(out, {'source': 'local_asr', 'language': 'es', 'text': 'Dos huevos'})
        args, kwargs = run.call_args
        # -P keeps the working directory off sys.path; the cwd is an empty private folder.
        self.assertEqual(args[0], [sys.executable, '-P', '-m', 'sabor.importing.transcribe',
                                   '/tmp/x/video.mp4', 'base'])
        self.assertEqual(kwargs['timeout'], tr.TIMEOUT_SECONDS)
        self.assertEqual(list(Path(kwargs['cwd']).iterdir()) if Path(kwargs['cwd']).exists() else [], [])
        self.assertTrue(kwargs['capture_output'])

    def test_failures_give_no_transcript(self):
        for result, effect in ((Mock(returncode=1, stdout=''), None), (Mock(returncode=0, stdout='not json'), None),
                               (Mock(returncode=0, stdout=json.dumps({'language': 'es', 'text': '  '})), None),
                               (None, subprocess.TimeoutExpired(['python'], 300)), (None, OSError('no python'))):
            out, _ = self.run_transcribe(result, effect)
            self.assertIsNone(out, (result, effect))

    def test_text_is_bounded(self):
        out, _ = self.run_transcribe(Mock(returncode=0, stdout=json.dumps({'language': 'en', 'text': 'a' * 30000})))
        assert out is not None
        self.assertEqual(len(out['text']), 20000)

    def test_only_known_models(self):
        with patch.object(tr.subprocess, 'run') as run:
            self.assertIsNone(tr.transcribe(Path('/tmp/x.mp4'), '../../evil'))
        run.assert_not_called()


class WorkerTests(unittest.TestCase):
    def test_main_runs_faster_whisper_on_cpu(self):
        model = Mock()
        segments = [SimpleNamespace(text=' Dos huevos '), SimpleNamespace(text='y sal.')]
        model.transcribe.return_value = (iter(segments), SimpleNamespace(language='es'))
        fake = SimpleNamespace(WhisperModel=Mock(return_value=model))
        out = io.StringIO()
        with patch.dict(sys.modules, faster_whisper=fake), redirect_stdout(out):
            code = tr.main(['/tmp/x/video.mp4', 'small'])
        self.assertEqual(code, 0)
        fake.WhisperModel.assert_called_once_with('small', device='cpu', compute_type='int8')
        self.assertEqual(model.transcribe.call_args.args[0], '/tmp/x/video.mp4')
        self.assertEqual(json.loads(out.getvalue()), {'language': 'es', 'text': 'Dos huevos\ny sal.'})

    def test_main_rejects_unknown_models(self):
        with redirect_stdout(io.StringIO()):
            self.assertEqual(tr.main(['/tmp/x.mp4', 'large-v3']), 2)


class SettingTests(unittest.TestCase):
    def test_model_setting(self):
        self.assertEqual(load_settings({}).transcribe_model, 'base')
        for name in ('tiny', 'base', 'small'):
            self.assertEqual(load_settings({'SABOR_TRANSCRIBE_MODEL': name}).transcribe_model, name)
        with self.assertRaises(ConfigError) as ctx:
            load_settings({'SABOR_TRANSCRIBE_MODEL': 'large-v3'})
        self.assertNotIn('large-v3', str(ctx.exception))
        from sabor.core.config_file import FILE_KEYS
        self.assertEqual(FILE_KEYS[('import', 'transcribe_model')], 'SABOR_TRANSCRIBE_MODEL')


class PackagingTests(unittest.TestCase):
    ROOT = Path(__file__).resolve().parents[1]

    def test_extra_and_docker_build_argument(self):
        import tomllib
        project = tomllib.loads((self.ROOT / 'pyproject.toml').read_text())['project']
        self.assertEqual(project['optional-dependencies']['transcribe'], ['faster-whisper==1.2.1'])
        self.assertNotIn('faster-whisper', ' '.join(project['dependencies']))
        dockerfile = (self.ROOT / 'Dockerfile').read_text()
        self.assertIn('ARG SABOR_TRANSCRIBE=0', dockerfile)
        self.assertIn('faster-whisper==1.2.1', dockerfile)


if __name__ == '__main__':
    unittest.main()
