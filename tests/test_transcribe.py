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
        self.assertEqual(args[0], [sys.executable, '-P', '-c', tr.WORKER, '/tmp/x/video.mp4', 'base'])
        self.assertEqual(kwargs['timeout'], tr.TIMEOUT_SECONDS)
        self.assertEqual(list(Path(kwargs['cwd']).iterdir()) if Path(kwargs['cwd']).exists() else [], [])
        self.assertTrue(kwargs['capture_output'])
        # CodeRabbit on #128: the environment is passed unchanged (no PYTHONPATH injected).
        self.assertNotIn('env', kwargs)

    def test_worker_loads_this_sabor_and_keeps_dependency_order(self):
        # CodeRabbit on #128: the worker must import the same sabor package as the server,
        # even from a source tree (Docker copies sabor/ without installing it) or when another
        # sabor is installed, while sys.path, and so where av and ctranslate2 come from, is
        # left exactly as the interpreter builds it.
        import os
        import tempfile
        with tempfile.TemporaryDirectory() as extra:
            # A stale `sabor` earlier on sys.path must not win.
            stale = Path(extra) / 'sabor'
            stale.mkdir()
            (stale / '__init__.py').write_text('STALE = True\n')
            env = dict(os.environ, PYTHONPATH=extra)
            probe = ('import sys; before = list(sys.path); ' + tr.BOOTSTRAP
                     + 'import sabor.importing.transcribe as t; '
                     'print(sys.path == before); print(t.__file__); print(hasattr(sys.modules["sabor"], "STALE"))')
            out = subprocess.run([sys.executable, '-P', '-c', probe], env=env, cwd=extra,
                                 capture_output=True, text=True, check=True).stdout.splitlines()
        self.assertEqual(out[0], 'True', 'sys.path is unchanged')
        self.assertEqual(Path(out[1]).resolve(), Path(tr.__file__).resolve())
        self.assertEqual(out[2], 'False')

    def test_preloaded_sabor_submodules_are_replaced(self):
        # CodeRabbit on #129: a sitecustomize (or .pth) that imports an old sabor.importing
        # before the bootstrap must not leave that submodule in place.
        import os
        import tempfile
        with tempfile.TemporaryDirectory() as extra:
            stale = Path(extra) / 'sabor' / 'importing'
            stale.mkdir(parents=True)
            (stale.parent / '__init__.py').write_text('')
            (stale / '__init__.py').write_text('STALE = True\n')
            (Path(extra) / 'sitecustomize.py').write_text('import sabor.importing\n')
            env = dict(os.environ, PYTHONPATH=extra)
            probe = (tr.BOOTSTRAP + 'import sabor.importing.transcribe as t; print(t.__file__); '
                     'print(hasattr(sys.modules["sabor.importing"], "STALE"))')
            out = subprocess.run([sys.executable, '-P', '-c', probe], env=env, cwd=extra,
                                 capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr[-300:])
        lines = out.stdout.splitlines()
        self.assertEqual(Path(lines[0]).resolve(), Path(tr.__file__).resolve())
        self.assertEqual(lines[1], 'False')

    def test_real_worker_imports_sabor_without_pythonpath(self):
        import os
        import tempfile
        env = {k: v for k, v in os.environ.items() if k != 'PYTHONPATH'}
        with tempfile.TemporaryDirectory() as cwd:
            # An unknown model makes main() exit 2 right after sabor was imported.
            result = subprocess.run([sys.executable, '-P', '-c', tr.WORKER, '/nonexistent.mp4', 'zz'],
                                    env=env, cwd=cwd, capture_output=True, text=True)
        self.assertEqual(result.returncode, 2, result.stderr[-300:])

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
        # faster-whisper 1.2.1 calls av.open(metadata_errors=...), which newer PyAV (19.x)
        # rejects; checked working with av 15.1.0, so av stays below 16.
        self.assertEqual(project['optional-dependencies']['transcribe'], ['faster-whisper==1.2.1', 'av>=11,<16'])
        self.assertNotIn('faster-whisper', ' '.join(project['dependencies']))
        dockerfile = (self.ROOT / 'Dockerfile').read_text()
        self.assertIn('ARG SABOR_TRANSCRIBE=0', dockerfile)
        self.assertIn("faster-whisper==1.2.1 'av>=11,<16'", dockerfile)


if __name__ == '__main__':
    unittest.main()


class BundleWiringTests(unittest.TestCase):
    """The bundle falls back to the local transcriber only without platform subtitles."""

    def run_url(self, subtitles, installed, model='base'):
        from sabor.core.context import build_context
        from sabor.importing import tools as t
        from sabor.importing import video as v
        from sabor.store.fake import FakeRecipeStore
        from test_video_bundle import INFO, fake_yt_dlp
        settings = load_settings({'SABOR_STORE': 'sqlite', 'SABOR_TRANSCRIBE_MODEL': model})
        ctx = build_context(settings, store_factory=lambda s: FakeRecipeStore())
        fake, _ = fake_yt_dlp(INFO, subtitles)
        asr = {'source': 'local_asr', 'language': 'es', 'text': 'cuatro huevos y sal'}
        with patch.dict(sys.modules, yt_dlp=fake), patch.object(v.shutil, 'which', return_value='/bin/ffmpeg'), \
                patch.object(v, 'frames', return_value=[]), \
                patch.object(tr, 'available', return_value=installed), \
                patch.object(tr, 'transcribe', return_value=asr) as run:
            data = t._import_recipe(ctx, 'https://youtu.be/V0ySydoCNXM', 'auto', True, 'es', 8, .55)['data']
        return data, run

    def test_no_subtitles_uses_the_installed_transcriber(self):
        data, run = self.run_url((), installed=True, model='small')
        self.assertEqual(data['transcript'], {'source': 'local_asr', 'language': 'es', 'text': 'cuatro huevos y sal'})
        self.assertNotIn('no_transcript', [item['code'] for item in data['limitations']])
        self.assertEqual(run.call_args.args[1], 'small')
        self.assertTrue(str(run.call_args.args[0]).endswith('.mp4'))

    def test_platform_subtitles_win(self):
        data, run = self.run_url((('es-orig', 'cuatro huevos'),), installed=True)
        self.assertEqual(data['transcript']['source'], 'subtitles')
        run.assert_not_called()

    def test_without_the_extra_the_limitation_names_it(self):
        data, run = self.run_url((), installed=False)
        self.assertIsNone(data['transcript'])
        run.assert_not_called()
        [limitation] = [item for item in data['limitations'] if item['code'] == 'no_transcript']
        self.assertIn('sabor-mcp[transcribe]', limitation['message'])

    def test_local_files_are_transcribed_from_the_private_copy(self):
        import tempfile
        from dataclasses import replace
        from sabor.core.context import build_context
        from sabor.importing import tools as t
        from sabor.importing import video as v
        from sabor.store.fake import FakeRecipeStore

        def ffmpeg(args, **kwargs):
            if args[0] == 'ffprobe':
                return Mock(returncode=0, stdout=b'30')
            Path(args[-1]).write_bytes(b'jpeg')
            return Mock(returncode=0)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            video = root / 'tortilla.mp4'
            video.write_bytes(b'video')
            settings = replace(load_settings({'SABOR_STORE': 'sqlite', 'SABOR_MEDIA_ROOTS': str(root),
                                              'SABOR_TRANSCRIBE_MODEL': 'tiny'}), local_media=True)
            ctx = build_context(settings, store_factory=lambda s: FakeRecipeStore())
            asr = {'source': 'local_asr', 'language': 'es', 'text': 'dos huevos'}
            with patch.object(v.shutil, 'which', return_value='/bin/tool'), \
                    patch.object(v.subprocess, 'run', side_effect=ffmpeg), \
                    patch.object(tr, 'available', return_value=True), \
                    patch.object(tr, 'transcribe', return_value=asr) as run:
                data = t._import_recipe(ctx, str(video), 'auto', True, 'es', 8, .55)['data']
        self.assertEqual(data['transcript'], asr)
        self.assertNotIn('no_transcript', [item['code'] for item in data['limitations']])
        self.assertNotEqual(Path(run.call_args.args[0]), video, 'the private copy is transcribed')
        self.assertEqual(run.call_args.args[1], 'tiny', 'the model setting reaches local files')
