"""#102 PR 3b': on stdio the bundle also gives audio and frame files under .sabor-media."""
import os
import re
import stat
import sys
import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

from sabor.core.config import load_settings
from sabor.core.context import build_context
from sabor.core.toolsets import media_result
from sabor.importing import tools as t
from sabor.importing import video as v
from sabor.store.fake import FakeRecipeStore
from test_video_bundle import INFO, fake_yt_dlp

BUNDLE = re.compile(r'[0-9a-f]{32}')


def fake_ffmpeg(audio=True):
    def run(args, **kwargs):
        if args[0] == 'ffprobe':
            return Mock(returncode=0, stdout=b'30')
        if '-vn' in args and not audio:
            return Mock(returncode=1)
        Path(args[-1]).write_bytes(b'audio' if '-vn' in args else b'jpeg')
        return Mock(returncode=0)
    return run


class MediaPathTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        base = Path(tmp.name).resolve()
        self.root, self.outside = base / 'videos', base / 'private'
        self.root.mkdir()
        self.outside.mkdir()
        self.video = self.root / 'tortilla.mp4'
        self.video.write_bytes(b'video')
        self.media = self.root / '.sabor-media'

    def run_import(self, source=None, local=True, audio=True):
        env = {'SABOR_STORE': 'sqlite', 'SABOR_MEDIA_ROOTS': f'{self.root}, {self.outside}'}
        ctx = build_context(replace(load_settings(env), local_media=local), store_factory=lambda s: FakeRecipeStore())
        fake, _ = fake_yt_dlp(INFO, ())
        with patch.dict(sys.modules, yt_dlp=fake), patch.object(v.shutil, 'which', return_value='/bin/tool'), \
                patch.object(v.subprocess, 'run', side_effect=fake_ffmpeg(audio)):
            result = t._import_recipe(ctx, source or str(self.video), 'auto', True, 'es', 8, .55)
        self.assertTrue(result['ok'], result)
        return result['data']

    def bundle_dirs(self):
        return sorted(p for p in self.media.iterdir() if BUNDLE.fullmatch(p.name)) if self.media.exists() else []

    def test_stdio_bundle_writes_audio_and_frames_in_a_private_folder(self):
        for source in (str(self.video), 'https://youtu.be/V0ySydoCNXM'):
            data = self.run_import(source)
            folder = Path(data['audio_path']).parent
            self.assertEqual(folder.parent, self.media, 'the first root holds the files')
            self.assertTrue(BUNDLE.fullmatch(folder.name))
            self.assertEqual(Path(data['audio_path']).name, 'audio.m4a')
            self.assertEqual(Path(data['audio_path']).read_bytes(), b'audio')
            self.assertEqual([Path(f['path']) for f in data['frames']],
                             [folder / f'frame-{i}.jpg' for i in range(8)])
            self.assertTrue(all(Path(f['path']).read_bytes() == b'jpeg' for f in data['frames']))
            for path in (self.media, folder):
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o700, path)
            self.assertIn('audio_path', data['next_step'])
            self.assertNotIn(str(self.root), data['next_step'], 'paths stay out of the instructions')
        self.assertEqual(len(self.bundle_dirs()), 2, 'one folder per import')
        self.assertEqual(list(self.outside.iterdir()), [])

    def test_http_bundles_write_no_files(self):
        # Over HTTP a URL bundle keeps its in-memory frames and gives no paths.
        data = self.run_import('https://youtu.be/V0ySydoCNXM', local=False)
        self.assertIsNone(data['audio_path'])
        self.assertTrue(data['frames'])
        self.assertFalse(any('path' in f for f in data['frames']))
        self.assertFalse(self.media.exists())

    def test_old_bundles_are_pruned_and_nothing_else(self):
        self.media.mkdir(mode=0o700)
        day = time.time() - 25 * 3600
        old, fresh, other = self.media / ('a' * 32), self.media / ('b' * 32), self.media / 'keep-me'
        for folder in (old, fresh, other):
            folder.mkdir()
            (folder / 'audio.m4a').write_bytes(b'x')
        (self.outside / 'secret.txt').write_text('secret')
        link = self.media / ('c' * 32)
        link.symlink_to(self.outside, target_is_directory=True)
        for path in (old, other):
            os.utime(path, (day, day))
        os.utime(link, (day, day), follow_symlinks=False)
        self.run_import()
        self.assertFalse(old.exists())
        self.assertTrue((fresh / 'audio.m4a').exists())
        self.assertTrue((other / 'audio.m4a').exists(), 'only Sabor folder names are removed')
        self.assertEqual((self.outside / 'secret.txt').read_text(), 'secret', 'links are never followed')

    def test_a_linked_media_folder_is_never_used(self):
        self.media.symlink_to(self.outside, target_is_directory=True)
        data = self.run_import()
        self.assertIsNone(data['audio_path'])
        self.assertTrue(data['frames'])
        self.assertFalse(any('path' in f for f in data['frames']))
        self.assertIn('no_media_paths', [item['code'] for item in data['limitations']])
        self.assertEqual(list(self.outside.iterdir()), [])

    def test_a_video_without_audio(self):
        data = self.run_import(audio=False)
        self.assertIsNone(data['audio_path'])
        self.assertEqual(len(data['frames']), 8)
        self.assertIn('no_audio', [item['code'] for item in data['limitations']])

    def test_mcp_keeps_frame_paths_and_drops_bytes(self):
        frames = [{'index': 0, 'time': 1.0, 'mime_type': 'image/jpeg', 'data': 'AAAA', 'path': '/x/frame-0.jpg'}]
        from mcp.types import CallToolResult
        out = media_result({'ok': True, 'data': {'frames': frames, 'audio_path': '/x/audio.m4a'}})
        assert isinstance(out, CallToolResult) and out.structuredContent is not None
        self.assertEqual(out.structuredContent['data']['frames'],
                         [{'index': 0, 'time': 1.0, 'mime_type': 'image/jpeg', 'path': '/x/frame-0.jpg'}])
        self.assertEqual(out.structuredContent['data']['audio_path'], '/x/audio.m4a')


if __name__ == '__main__':
    unittest.main()
