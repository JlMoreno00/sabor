"""#102 PR 3b: local video files, only on stdio and only under SABOR_MEDIA_ROOTS."""
import asyncio
import os
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

from sabor.core.config import ConfigError, load_settings
from sabor.core.context import build_context
from sabor.importing import tools as t
from sabor.importing import video as v
from sabor.store.fake import FakeRecipeStore


def fake_ffmpeg(duration=b'30'):
    def run(args, **kwargs):
        if args[0] == 'ffprobe':
            return Mock(returncode=0, stdout=duration)
        Path(args[-1]).write_bytes(b'jpeg')
        return Mock(returncode=0)
    return run


class LocalVideoTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        base = Path(tmp.name).resolve()
        self.root, self.outside = base / 'videos', base / 'private'
        self.root.mkdir()
        self.outside.mkdir()
        self.video = self.root / 'tortilla.mp4'
        self.video.write_bytes(b'video')
        (self.outside / 'secret.mp4').write_bytes(b'secret')

    def run_import(self, source, kind='auto', local=True, roots=None, ffmpeg=True, duration=b'30'):
        env = {'SABOR_STORE': 'sqlite', 'SABOR_MEDIA_ROOTS': str(self.root) if roots is None else roots}
        store = FakeRecipeStore()
        ctx = build_context(replace(load_settings(env), local_media=local), store_factory=lambda s: store)
        ytdlp = Mock(side_effect=AssertionError('a local file never reaches yt-dlp'))
        with patch.dict(sys.modules, yt_dlp=Mock(YoutubeDL=ytdlp)), \
                patch.object(v.shutil, 'which', return_value='/bin/tool' if ffmpeg else None), \
                patch.object(v.subprocess, 'run', side_effect=fake_ffmpeg(duration)) as run:
            result = t._import_recipe(ctx, source, kind, True, 'es', 8, .55)
        self.ran = run.call_args_list
        self.assertEqual(store.recipes, {}, 'a video bundle never writes a recipe')
        return result

    def test_local_video_under_a_root_returns_the_bundle(self):
        for kind in ('auto', 'video'):
            result = self.run_import(str(self.video), kind)
            self.assertTrue(result['ok'], result)
            data = result['data']
            self.assertEqual((data['kind'], data['provider'], data['candidates'], data['created']),
                             ('video', 'sabor', [], []))
            self.assertEqual(data['metadata'], {
                'title': 'tortilla', 'uploader': None, 'duration': 30.0, 'upload_date': None,
                'source_url': None, 'source_path': str(self.video), 'platform': 'local', 'description': ''})
            self.assertIsNone(data['transcript'])
            self.assertEqual(len(data['frames']), 8)
            self.assertEqual([item['code'] for item in data['limitations']], ['no_transcript'])
            for words in ('create_recipe', 'video-import', "'es'", 'tortilla.mp4'):
                self.assertIn(words, data['next_step'])
            # ffmpeg reads a private copy, never the user's path.
            self.assertTrue(self.ran)
            self.assertFalse(any(str(self.video) in call.args[0] for call in self.ran))

    def test_symlinks_inside_the_root_are_followed(self):
        link = self.root / 'link.mp4'
        link.symlink_to(self.video)
        self.assertTrue(self.run_import(str(link))['ok'])

    def test_paths_outside_the_roots_are_rejected_untouched(self):
        escape = self.root / 'escape.mp4'
        escape.symlink_to(self.outside / 'secret.mp4')
        (self.root / 'notes.txt').write_text('x')
        (self.root / 'folder.mp4').mkdir()
        cases = [str(self.outside / 'secret.mp4'), str(escape), str(self.root / '..' / 'private' / 'secret.mp4'),
                 str(self.root / 'sub' / '..' / 'tortilla.mp4'), str(self.root / 'notes.txt'),
                 str(self.root / 'folder.mp4'), str(self.root / 'missing.mp4')]
        for source in cases:
            result = self.run_import(source)
            self.assertEqual(result['error']['code'], 'invalid_argument', source)
            self.assertIn('SABOR_MEDIA_ROOTS', result['error']['message'])
            self.assertNotIn('private', str(result), 'the error never echoes the path')
            self.assertEqual(self.ran, [], source)

    def test_a_root_prefix_is_not_a_root(self):
        # /x/videos must not admit /x/videos-other.
        other = self.root.parent / 'videos-other'
        other.mkdir()
        (other / 'a.mp4').write_bytes(b'video')
        self.assertEqual(self.run_import(str(other / 'a.mp4'))['error']['code'], 'invalid_argument')

    def test_limits(self):
        self.assertEqual(self.run_import(str(self.video), duration=b'601')['error']['code'], 'unsupported_operation')
        with patch.object(v, 'MAX_BYTES', 3):
            result = self.run_import(str(self.video))
        self.assertEqual(result['error']['code'], 'unsupported_operation')
        self.assertEqual(self.ran, [])
        result = self.run_import(str(self.video), ffmpeg=False)
        self.assertEqual(result['error']['code'], 'provider_unconfigured')
        self.assertIn('ffmpeg', result['error']['message'])

    def test_http_and_direct_calls_reject_local_paths(self):
        result = self.run_import(str(self.video), local=False)
        self.assertEqual(result['error']['code'], 'unsupported_operation')
        self.assertIn('URL', result['error']['message'])
        self.assertEqual(self.ran, [])

    def test_text_and_image_local_paths_point_to_create_recipe(self):
        # Deferred from #120: an explicit kind with a local path gets guidance, not invalid_argument.
        for kind in ('text', 'image'):
            for local in (True, False):
                result = self.run_import(str(self.video), kind, local=local)
                self.assertEqual(result['error']['code'], 'unsupported_operation', kind)
                self.assertIn('create_recipe', result['error']['message'])
        self.assertEqual(self.run_import('./tortilla.mp4')['error']['code'], 'invalid_argument')

    def test_default_root_is_the_home_directory(self):
        with patch.object(Path, 'home', return_value=self.root):
            self.assertTrue(self.run_import(str(self.video), roots='')['ok'])
        with patch.object(Path, 'home', return_value=self.outside):
            self.assertEqual(self.run_import(str(self.video), roots='')['error']['code'], 'invalid_argument')


class MediaRootsSettingTests(unittest.TestCase):
    def test_comma_separated_absolute_roots(self):
        settings = load_settings({'SABOR_MEDIA_ROOTS': '/a/videos, /b'})
        self.assertEqual(settings.media_roots, ('/a/videos', '/b'))
        self.assertEqual(load_settings({}).media_roots, ())
        self.assertFalse(load_settings({}).local_media)
        with self.assertRaises(ConfigError) as ctx:
            load_settings({'SABOR_MEDIA_ROOTS': 'videos'})
        self.assertNotIn('videos', str(ctx.exception))

    def test_config_file_key(self):
        from sabor.core.config_file import FILE_KEYS
        self.assertEqual(FILE_KEYS[('import', 'media_roots')], 'SABOR_MEDIA_ROOTS')


class StdioWiringTests(unittest.TestCase):
    def test_only_the_stdio_command_accepts_local_files(self):
        import sabor.server as server_module
        for argv, local in ((['--transport', 'stdio'], True), ([], True), (['--transport', 'http'], False)):
            with patch.object(server_module, 'register_tools') as register, \
                    patch.object(server_module, 'apply_config_file', return_value='defaults'), \
                    patch.object(server_module, 'start_runtime_services'), \
                    patch.object(server_module.mcp, 'run'), patch.dict(os.environ, {'SABOR_HOST': '127.0.0.1'}):
                server_module.cli(argv)
            self.assertEqual(register.call_args.kwargs.get('local_media', False), local, argv)
        with patch.object(server_module, 'register_tools') as register, \
                patch.object(server_module, 'start_runtime_services'), patch.object(server_module.mcp, 'run'):
            server_module.main()
        self.assertFalse(register.call_args.kwargs.get('local_media', False))

    def test_registered_tool_reads_a_local_file_on_stdio(self):
        from mcp.server.fastmcp import FastMCP
        from mcp.types import CallToolResult, ImageContent
        from sabor.server import register_tools
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            video = root / 'a.mp4'
            video.write_bytes(b'video')
            env = {'SABOR_TOOLSETS': 'core,import', 'SABOR_STORE': 'sqlite', 'SABOR_DATA_DIR': tmp,
                   'SABOR_MEDIA_ROOTS': str(root)}
            stdio, http = FastMCP('stdio'), FastMCP('http')
            register_tools(stdio, env, local_media=True)
            register_tools(http, env)
            with patch.object(v.shutil, 'which', return_value='/bin/tool'), \
                    patch.object(v.subprocess, 'run', side_effect=fake_ffmpeg()):
                local = asyncio.run(stdio.call_tool('import_recipe', {'source': str(video)}))
                remote = asyncio.run(http.call_tool('import_recipe', {'source': str(video)}))
        self.assertIsInstance(local, CallToolResult)
        assert isinstance(local, CallToolResult)
        self.assertEqual([type(c) for c in local.content[1:]], [ImageContent] * 8)
        self.assertNotIsInstance(remote, CallToolResult)
        content = list(remote[0] if isinstance(remote, tuple) else remote)
        self.assertIn('unsupported_operation', getattr(content[0], 'text'))


if __name__ == '__main__':
    unittest.main()
