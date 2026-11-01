"""#102 PR 3a: a video URL returns a media bundle for the client agent, never candidates."""
import asyncio
import base64
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from sabor.core.config import load_settings
from sabor.core.context import build_context
from sabor.importing import tools as t
from sabor.importing import video as v
from sabor.store.fake import FakeRecipeStore

INFO = {'duration': 95, 'title': 'Tortilla de patatas', 'uploader': 'Paleta', 'upload_date': '20261029',
        'webpage_url': 'https://www.youtube.com/watch?v=V0ySydoCNXM', 'extractor_key': 'Youtube',
        'description': 'Ingredientes: 4 huevos, 3 patatas, sal.', 'id': 'V0ySydoCNXM',
        'requested_downloads': [{'vcodec': 'avc1', 'acodec': 'mp4a'}]}
FRAME = [{'index': 0, 'time': 5.9, 'mime_type': 'image/jpeg', 'data': base64.b64encode(b'jpeg').decode()}]


def fake_yt_dlp(info, subtitles=(), media=True):
    """yt_dlp stand-in that writes the files the real one writes, with the options it got."""
    seen = {}

    def extract(url, download=True):
        opts = factory.call_args.args[0]
        seen.update(opts)
        root = Path(opts['outtmpl']).parent
        if media and not opts.get('skip_download'):
            (root / 'video.18.mp4').write_bytes(b'video')
        for lang, text in subtitles:
            (root / f'video.18.{lang}.vtt').write_text(f'WEBVTT\n\n00:00:01.000 --> 00:00:02.000\n{text}\n')
        return dict(info)
    factory = Mock(return_value=Mock(__enter__=Mock(return_value=Mock(extract_info=extract)),
                                     __exit__=Mock(return_value=False)))
    return SimpleNamespace(YoutubeDL=factory), seen


class VideoBundleTests(unittest.TestCase):
    def run_import(self, source='https://youtu.be/V0ySydoCNXM', kind='auto', dry_run=True, language='es',
                   subtitles=(('en', 'four eggs'), ('es-orig', 'cuatro huevos')), ffmpeg=True, frames=FRAME):
        store = FakeRecipeStore()
        ctx = build_context(load_settings({'SABOR_STORE': 'sqlite'}), store_factory=lambda s: store)
        fake, self.opts = fake_yt_dlp(INFO, subtitles)
        with patch.dict(sys.modules, yt_dlp=fake), \
                patch.object(v.shutil, 'which', return_value='/bin/ffmpeg' if ffmpeg else None), \
                patch.object(v, 'frames', return_value=frames) as grab:
            result = t._import_recipe(ctx, source, kind, dry_run, language, 8, .55)
        self.frames_called = grab.called
        self.assertEqual(store.recipes, {}, 'a video bundle never writes a recipe')
        return result

    def test_url_video_returns_the_media_bundle(self):
        for kind in ('auto', 'video'):
            for dry_run in (True, False):
                result = self.run_import(kind=kind, dry_run=dry_run)
                self.assertTrue(result['ok'], result)
                data = result['data']
                self.assertEqual((data['kind'], data['provider'], data['candidates'], data['created']),
                                 ('video', 'sabor', [], []))
                self.assertEqual(data['metadata'], {
                    'title': 'Tortilla de patatas', 'uploader': 'Paleta', 'duration': 95,
                    'upload_date': '2026-10-29', 'source_url': 'https://www.youtube.com/watch?v=V0ySydoCNXM',
                    'platform': 'youtube', 'description': 'Ingredientes: 4 huevos, 3 patatas, sal.'})
                # Platform subtitles in the original language come first.
                self.assertEqual(data['transcript'], {'source': 'subtitles', 'language': 'es',
                                                      'text': 'cuatro huevos'})
                self.assertEqual(data['frames'], FRAME)
                self.assertEqual(data['limitations'], [])
                for words in ('create_recipe', 'video-import', "'es'", 'https://www.youtube.com/watch?v=V0ySydoCNXM'):
                    self.assertIn(words, data['next_step'])
        self.assertEqual(self.opts['subtitleslangs'][0], '.*-orig')
        self.assertEqual(self.opts['subtitleslangs'][1], 'es')

    def test_user_language_subtitles_without_an_original_track(self):
        data = self.run_import(subtitles=(('en', 'four eggs'), ('es', 'cuatro huevos')), language='en')['data']
        self.assertEqual(data['transcript'], {'source': 'subtitles', 'language': 'en', 'text': 'four eggs'})

    def test_no_subtitles_is_a_limitation(self):
        data = self.run_import(subtitles=())['data']
        self.assertIsNone(data['transcript'])
        self.assertEqual([item['code'] for item in data['limitations']], ['no_transcript'])

    def test_without_ffmpeg_metadata_and_subtitles_still_come(self):
        result = self.run_import(ffmpeg=False)
        self.assertTrue(result['ok'], result)
        data = result['data']
        self.assertTrue(self.opts['skip_download'], 'no media is downloaded that cannot be used')
        self.assertFalse(self.frames_called)
        self.assertEqual(data['frames'], [])
        self.assertEqual(data['transcript']['text'], 'cuatro huevos')
        self.assertEqual(data['metadata']['title'], 'Tortilla de patatas')
        self.assertEqual([item['code'] for item in data['limitations']], ['no_frames'])
        self.assertIn('ffmpeg', data['limitations'][0]['message'])

    def test_text_and_image_still_point_to_create_recipe(self):
        for source, kind in (('Sopa con sal', 'auto'), ('https://example.org/a.jpg', 'image')):
            result = self.run_import(source=source, kind=kind)
            self.assertEqual(result['error']['code'], 'unsupported_operation')
            self.assertIn('create_recipe', result['error']['message'])

    def test_only_video_platforms_are_fetched(self):
        # CodeRabbit on #122: yt-dlp's generic extractor would fetch any URL, private ones
        # included, outside the SSRF guard; only platform extractors run, on platform hosts.
        for source, kind in (('https://10.0.0.1/a.mp4', 'auto'), ('https://example.org/clip.mp4', 'auto'),
                             ('http://169.254.169.254/latest', 'video'), ('https://youtube.com.evil.test/v', 'video')):
            fake, seen = fake_yt_dlp(INFO, ())
            store = FakeRecipeStore()
            ctx = build_context(load_settings({'SABOR_STORE': 'sqlite'}), store_factory=lambda s: store)
            with patch.dict(sys.modules, yt_dlp=fake):
                result = t._import_recipe(ctx, source, kind, True, 'es', 8, .55)
            self.assertEqual(result['error']['code'], 'unsupported_operation', source)
            self.assertIn('create_recipe', result['error']['message'])
            fake.YoutubeDL.assert_not_called()
        self.run_import(source='https://www.youtube.com/watch?v=V0ySydoCNXM')
        self.assertEqual(self.opts['allowed_extractors'], ['default', '-generic'])
        # yt-dlp's progress bar never reaches the server's output (stdio is the MCP channel).
        self.assertTrue(self.opts['noprogress'])

    def test_language_is_a_literal_subtitle_name(self):
        # CodeRabbit on #122: the language is escaped, not read as a regex.
        result = self.run_import(language='[')
        self.assertTrue(result['ok'], result)
        self.assertEqual(self.opts['subtitleslangs'][:2], ['.*-orig', r'\['])

    def test_frames_are_described_with_their_time(self):
        def ffmpeg(args, **kwargs):
            if args[0] == 'ffprobe':
                return Mock(returncode=0, stdout=b'80')
            Path(args[-1]).write_bytes(b'jpeg')
            return Mock(returncode=0)
        with patch.object(v.shutil, 'which', return_value='/bin/tool'), \
                patch.object(v.subprocess, 'run', side_effect=ffmpeg):
            frames = v.frames(Path('/unused.mp4'))
        self.assertEqual([f['index'] for f in frames], list(range(8)))
        self.assertEqual([f['time'] for f in frames], [5.0, 15.0, 25.0, 35.0, 45.0, 55.0, 65.0, 75.0])
        self.assertEqual({(f['mime_type'], base64.b64decode(f['data'])) for f in frames}, {('image/jpeg', b'jpeg')})


class McpImageContentTests(unittest.TestCase):
    def test_frames_travel_as_image_content(self):
        from mcp.server.fastmcp import FastMCP
        from mcp.types import CallToolResult, ImageContent, TextContent
        from sabor.server import register_tools
        server = FastMCP('video')
        with tempfile.TemporaryDirectory() as data_dir:
            register_tools(server, {'SABOR_TOOLSETS': 'core,import', 'SABOR_STORE': 'sqlite',
                                    'SABOR_DATA_DIR': data_dir})
            fake, _ = fake_yt_dlp(INFO, (('es-orig', 'cuatro huevos'),))
            with patch.dict(sys.modules, yt_dlp=fake), patch.object(v.shutil, 'which', return_value='/bin/ffmpeg'), \
                    patch.object(v, 'frames', return_value=FRAME * 2):
                out = asyncio.run(server.call_tool('import_recipe', {'source': 'https://youtu.be/V0ySydoCNXM'}))
        self.assertIsInstance(out, CallToolResult)
        assert isinstance(out, CallToolResult)
        text, *images = out.content
        self.assertIsInstance(text, TextContent)
        self.assertEqual([(type(i), i.mimeType, i.data) for i in images],
                         [(ImageContent, 'image/jpeg', FRAME[0]['data'])] * 2)
        # The JSON carries frame times, never the image bytes a second time.
        assert isinstance(text, TextContent) and out.structuredContent is not None
        for payload in (json.loads(text.text), out.structuredContent):
            self.assertEqual(payload['data']['frames'], [{'index': 0, 'time': 5.9, 'mime_type': 'image/jpeg'}] * 2)
            self.assertEqual(payload['data']['transcript']['text'], 'cuatro huevos')

    def test_other_tools_keep_their_plain_result(self):
        from mcp.server.fastmcp import FastMCP
        from mcp.types import CallToolResult
        from sabor.server import register_tools
        server = FastMCP('plain')
        with tempfile.TemporaryDirectory() as data_dir:
            register_tools(server, {'SABOR_TOOLSETS': 'core', 'SABOR_STORE': 'sqlite', 'SABOR_DATA_DIR': data_dir})
            out = asyncio.run(server.call_tool('get_shopping_lists', {}))
        self.assertNotIsInstance(out, CallToolResult)


if __name__ == '__main__':
    unittest.main()
