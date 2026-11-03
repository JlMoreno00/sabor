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
            # CodeRabbit on #125: every file, the audio included, is 0600 whatever the umask.
            for path in (Path(data['audio_path']), *(Path(f['path']) for f in data['frames'])):
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600, path)
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

    def test_only_old_folders_created_by_sabor_are_pruned(self):
        # Only folders Sabor created (32 hex name, real directory, its own marker file)
        # and older than 24 h are removed; links are never followed, outside or inside.
        self.media.mkdir(mode=0o700)
        day = time.time() - 25 * 3600
        (self.outside / 'secret.txt').write_text('secret')

        def folder(name, marker=True, age=day):
            path = self.media / name
            path.mkdir()
            (path / 'audio.m4a').write_bytes(b'x')
            if marker:
                (path / v.MARKER).write_bytes(b'')
            os.utime(path, (age, age))
            return path
        old = folder('a' * 32)
        fresh = folder('b' * 32, age=time.time() - 3600)
        unmarked = folder('d' * 32, marker=False)
        other = folder('keep-me')
        upper = folder('E' * 32)
        fake_marker = folder('f' * 32, marker=False)
        (fake_marker / v.MARKER).symlink_to(self.outside / 'secret.txt')
        os.utime(fake_marker, (day, day))
        # An old Sabor folder holding links to outside: the folder goes, the targets stay.
        inside = folder('1' * 32)
        (inside / 'to-file').symlink_to(self.outside / 'secret.txt')
        (inside / 'to-dir').symlink_to(self.outside, target_is_directory=True)
        os.utime(inside, (day, day))
        link = self.media / ('c' * 32)
        link.symlink_to(self.outside, target_is_directory=True)
        os.utime(link, (day, day), follow_symlinks=False)
        # An old marked folder elsewhere (the second root) is never looked at.
        stray = self.outside / ('2' * 32)
        stray.mkdir()
        (stray / v.MARKER).write_bytes(b'')
        os.utime(stray, (day, day))

        data = self.run_import()
        self.assertFalse(old.exists())
        self.assertFalse(inside.exists())
        for kept in (fresh, unmarked, other, upper, fake_marker):
            self.assertTrue((kept / 'audio.m4a').exists(), kept.name)
        self.assertTrue(link.is_symlink())
        self.assertTrue(stray.exists())
        self.assertEqual((self.outside / 'secret.txt').read_text(), 'secret')
        self.assertEqual(sorted(p.name for p in self.outside.iterdir()), ['2' * 32, 'secret.txt'])
        # The folder of this import carries the marker, so a later import may prune it.
        created = Path(data['audio_path']).parent
        self.assertTrue((created / v.MARKER).is_file())
        self.assertFalse((created / v.MARKER).is_symlink())

    def test_writes_stay_in_the_checked_folder_after_a_swap(self):
        # CodeRabbit on #125: once .sabor-media is checked, it is used by descriptor. Swapping
        # it for a link afterwards never sends a write (or a prune) outside.
        folder = v.media_folder((str(self.root),))
        assert folder is not None
        try:
            name = folder.path.name
            self.media.rename(self.root / 'moved')
            self.media.symlink_to(self.outside, target_is_directory=True)
            reported = folder.write('frame-0.jpg', b'jpeg')
        finally:
            folder.close()
        self.assertEqual(reported, str(self.media / name / 'frame-0.jpg'))
        self.assertEqual((self.root / 'moved' / name / 'frame-0.jpg').read_bytes(), b'jpeg')
        self.assertEqual(list(self.outside.iterdir()), [])

    def test_a_failed_frame_write_keeps_the_frame(self):
        # CodeRabbit on #125: the attached image survives when its file cannot be written.
        original = v.MediaFolder.write

        def write(folder, name, data):
            if name.startswith('frame-'):
                raise OSError(28, 'No space left on device')
            return original(folder, name, data)
        with patch.object(v.MediaFolder, 'write', write):
            data = self.run_import()
        self.assertEqual(len(data['frames']), 8)
        self.assertFalse(any('path' in f for f in data['frames']))

    def test_an_unsaved_soundtrack_is_not_reported_as_missing_audio(self):
        # CodeRabbit on #125: audio that was extracted but could not be written is
        # `audio_not_saved`, never `no_audio` (which means the video has no audio track).
        original = v.MediaFolder.write

        def write(folder, name, data):
            if name == 'audio.m4a':
                raise OSError(28, 'No space left on device')
            return original(folder, name, data)
        with patch.object(v.MediaFolder, 'write', write):
            data = self.run_import()
        self.assertIsNone(data['audio_path'])
        codes = [item['code'] for item in data['limitations']]
        self.assertIn('audio_not_saved', codes)
        self.assertNotIn('no_audio', codes)

    def test_a_failed_prune_still_creates_the_bundle(self):
        # CodeRabbit on #125: pruning problems never cost the import its files.
        with patch.object(v, 'prune', side_effect=PermissionError(13, 'denied')):
            data = self.run_import()
        self.assertIsNotNone(data['audio_path'])
        self.assertTrue(all('path' in f for f in data['frames']))

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
