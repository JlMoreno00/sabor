import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from sabor.core.config import load_settings
from sabor.importing import tools as t

C = dict(name='Soup', ingredients=['salt'], steps=['Mix'], servings='2', notes='', confidence=.9)
ENV = {'SABOR_LLM_BASE_URL': 'http://localhost:11434/v1', 'SABOR_LLM_MODEL': 'vision',
       'GEMINI_API_KEY': 'test', 'VIDEO_RECIPE_GEMINI_MODEL': 'test'}


class VideoTests(unittest.TestCase):
    def setUp(self):
        from sabor.core.context import build_context
        from sabor.store.mealie import MealieRecipeStore
        from sabor.importing.video import GeminiNativeVideo
        def context():
            cfg = t.settings()
            return build_context(cfg, store_factory=lambda s: MealieRecipeStore(
                s.mealie_base_url, s.mealie_api_token, session=t.requests),
                import_factories=(t.OpenAIImportProvider, GeminiNativeVideo))
        p = patch('sabor.core.context.default_context', side_effect=context)
        p.start()
        self.addCleanup(p.stop)

    def test_selection_matrix(self):
        from sabor.importing.video import select_video
        for env, expected in [(ENV, 'gemini_native'),
                (dict(ENV, SABOR_VIDEO_PROVIDER='openai_compatible'), 'openai_compatible'),
                ({k: v for k, v in ENV.items() if k.startswith('SABOR_LLM')}, 'openai_compatible')]:
            self.assertEqual(select_video(load_settings(env)).id, expected)
        with self.assertRaises(t.ImportErrorResult):
            select_video(load_settings({}))

    def test_gemini_cleanup_on_error(self):
        from sabor.importing import video as v
        with patch.object(v.gemini_client, '_upload_to_gemini', return_value={'ok': True, 'name': 'files/a', 'uri': 'uri', 'state': 'ACTIVE'}), patch.object(v.gemini_client, '_extract_recipes_with_gemini', side_effect=ValueError('SECRET')), patch.object(v.gemini_client, '_cleanup_gemini_file') as cleanup:
            with self.assertRaises(ValueError):
                v.GeminiNativeVideo(load_settings(ENV)).extract(Path('unused'), '', dict(language='en', max_recipes=8))
            cleanup.assert_called_once_with('files/a', cfg=load_settings(ENV))

    def response(self, status=200):
        return Mock(status_code=status, json=lambda: {'choices': [{'message': {'content': json.dumps({'recipes': [C]})}}]})

    def test_frames_captions_and_limitations(self):
        from sabor.importing import video as v
        with patch.object(v, 'frames', return_value=['data:image/jpeg;base64,eA==']), patch.object(t.requests, 'post', return_value=self.response()) as post:
            result = v.OpenAICompatibleVideo(load_settings(ENV)).extract(Path('unused'), 'salt', dict(language='en', max_recipes=8, min_confidence=.55))
        content = post.call_args.kwargs['json']['messages'][1]['content']
        self.assertEqual(content[0]['text'], 'salt')
        self.assertEqual(content[1]['type'], 'image_url')
        self.assertEqual(result['limitations'], ['audio_not_analyzed'])

    def test_image_rejection_and_no_evidence(self):
        from sabor.importing import video as v
        with patch.object(v, 'frames', return_value=['data:image/jpeg;base64,eA==']), patch.object(t.requests, 'post', side_effect=[self.response(400), self.response(400), self.response()]) as post:
            result = v.OpenAICompatibleVideo(load_settings(ENV)).extract(Path('unused'), 'salt', dict(language='en', max_recipes=8, min_confidence=.55))
            self.assertIn('visual_evidence_unavailable', result['limitations'])
            self.assertEqual(post.call_args.kwargs['json']['messages'][1]['content'], 'salt')
        with patch.object(v, 'frames', return_value=[]):
            with self.assertRaises(t.ImportErrorResult) as ctx:
                v.OpenAICompatibleVideo(load_settings(ENV)).extract(Path('unused'), '', {})
            self.assertEqual(ctx.exception.result['error']['code'], 'unsupported_operation')

    def test_downloader_bounds_and_temp_cleanup(self):
        from sabor.importing import video as v
        seen = []
        def download(url, root):
            seen.append(root)
            raise RuntimeError('SECRET')
        with patch.object(t, 'settings', return_value=load_settings(ENV)), patch.object(v, 'download', side_effect=download):
            result = t.import_recipe('https://youtu.be/a')
        self.assertNotIn('SECRET', str(result))
        self.assertFalse(seen[0].exists())
        ydl = Mock()
        def extract(*args, **kwargs):
            opts = factory.call_args.args[0]
            self.assertTrue(opts['noplaylist'])
            self.assertGreater(opts['max_filesize'], 0)
            self.assertIsNotNone(opts['match_filter']({'duration': 99999}))
            self.assertTrue(opts['writeautomaticsubtitles'])
            Path(opts['outtmpl'].replace('%(ext)s', 'mp4')).write_bytes(b'video')
            return {'duration': 10}
        ydl.extract_info.side_effect = extract
        factory = Mock(return_value=Mock(__enter__=Mock(return_value=ydl), __exit__=Mock(return_value=False)))
        with patch.dict(sys.modules, yt_dlp=SimpleNamespace(YoutubeDL=factory)), v.TemporaryDirectory() as root:
            media, captions = v.download('https://youtu.be/a', Path(root))
            self.assertTrue(media.exists())

    def test_youtube_dash_streams_are_merged(self):
        # #102 PR 1 (Paleta, 29-oct): YouTube serves some videos only as separate DASH video
        # and audio streams, so a single muxed 'best[...][ext=mp4]' file is unavailable.
        # CodeRabbit on #119: yt-dlp would merge them with an unbounded ffmpeg call, so it
        # downloads them as separate formats and Sabor merges them within the 180 s budget.
        from sabor.importing import video as v
        def extract(*args, **kwargs):
            opts = factory.call_args.args[0]
            self.assertEqual(opts['format'], v.FORMAT)
            self.assertNotIn('merge_output_format', opts)
            for name in ('137.mp4', '140.m4a'):
                Path(opts['outtmpl'].replace('%(format_id)s.%(ext)s', name)).write_bytes(b'stream')
            return {'duration': 10}
        factory = Mock(return_value=Mock(__enter__=Mock(return_value=Mock(extract_info=extract)),
                                         __exit__=Mock(return_value=False)))
        def merge(args, **kwargs):
            Path(args[-1]).write_bytes(b'merged')
            return Mock(returncode=0)
        with patch.dict(sys.modules, yt_dlp=SimpleNamespace(YoutubeDL=factory)), \
                patch.object(v.shutil, 'which', return_value='/bin/ffmpeg'), \
                patch.object(v.subprocess, 'run', side_effect=merge) as run, v.TemporaryDirectory() as root:
            media, _ = v.download('https://youtu.be/a', Path(root))
            self.assertEqual((media.name, media.read_bytes()), ('merged.mp4', b'merged'))
        self.assertEqual(run.call_args.args[0][0], 'ffmpeg')
        self.assertTrue(0 < run.call_args.kwargs['timeout'] <= 180)

    def test_real_yt_dlp_selects_streams_without_requesting_a_merge(self):
        # CodeRabbit on #119: with '+' and no ffmpeg, yt-dlp aborts with DownloadError before
        # downloading anything. Checked with the real format selector (no network): separate
        # streams are picked as independent downloads, with a muxed file as the fallback.
        import yt_dlp
        from sabor.importing import video as v
        select = yt_dlp.YoutubeDL({'quiet': True}).build_format_selector(v.FORMAT)
        def fmt(fid, ext, height=None, vcodec='none', acodec='none'):
            return dict(format_id=fid, ext=ext, height=height, vcodec=vcodec, acodec=acodec,
                        url='https://example.invalid/' + fid, protocol='https', tbr=height or 128)
        cases = {('137', '140'): [fmt('137', 'mp4', 720, 'avc1'), fmt('248', 'webm', 720, 'vp9'),
                                  fmt('140', 'm4a', acodec='mp4a')],
                 ('248', '251'): [fmt('248', 'webm', 720, 'vp9'), fmt('251', 'webm', acodec='opus')],
                 ('18',): [fmt('18', 'mp4', 360, 'avc1', 'mp4a')]}
        for expected, formats in cases.items():
            picked = list(select({'formats': formats, 'has_merged_format': False, 'incomplete_formats': False}))
            self.assertEqual(tuple(f['format_id'] for f in picked), expected)
            self.assertFalse(any('requested_formats' in f for f in picked), expected)

    def test_stream_merge_is_bounded_and_streams_count_together(self):
        # CodeRabbit on #119: a hung merge times out instead of keeping the import alive, and
        # the size limit applies to the streams together, not to each one.
        from sabor.importing import video as v
        def extract(*args, **kwargs):
            opts = factory.call_args.args[0]
            for name in ('137.mp4', '140.m4a'):
                Path(opts['outtmpl'].replace('%(format_id)s.%(ext)s', name)).write_bytes(b's' * 6)
            return {'duration': 10}
        factory = Mock(return_value=Mock(__enter__=Mock(return_value=Mock(extract_info=extract)),
                                         __exit__=Mock(return_value=False)))
        hung = v.subprocess.TimeoutExpired(['ffmpeg'], 1)
        with patch.dict(sys.modules, yt_dlp=SimpleNamespace(YoutubeDL=factory)), \
                patch.object(v.shutil, 'which', return_value='/bin/ffmpeg'), \
                patch.object(v.subprocess, 'run', side_effect=hung), v.TemporaryDirectory() as root:
            with self.assertRaises(t.ImportErrorResult) as ctx:
                v.download('https://youtu.be/a', Path(root))
            self.assertEqual(ctx.exception.result['error']['code'], 'timeout')
            with patch.object(v, 'MAX_BYTES', 10), self.assertRaises(t.ImportErrorResult) as ctx:
                v.download('https://youtu.be/a', Path(root))
            self.assertEqual(ctx.exception.result['error']['code'], 'unsupported_operation')

    def test_download_errors_carry_the_real_redacted_reason(self):
        # #102 PR 1: the yt-dlp reason was lost in a generic upstream_error attributed to the
        # video provider; it now comes from yt_dlp, redacted (no URL query, token or email).
        class DownloadError(Exception):
            pass
        reason = ('ERROR: [youtube] V0ySydoCNXM: Requested format is not available. '
                  'See https://example.com/x?token=SECRET or ask cook@example.com')
        ydl = Mock(extract_info=Mock(side_effect=DownloadError(reason)))
        factory = Mock(return_value=Mock(__enter__=Mock(return_value=ydl), __exit__=Mock(return_value=False)))
        fake = SimpleNamespace(YoutubeDL=factory, DownloadError=DownloadError)
        with patch.dict(sys.modules, yt_dlp=fake), patch.object(t, 'settings', return_value=load_settings(ENV)):
            result = t.import_recipe('https://youtu.be/V0ySydoCNXM')
        self.assertEqual((result['error']['code'], result['error']['provider']), ('upstream_error', 'yt_dlp'))
        self.assertIn('Requested format is not available', result['data']['reason'])
        self.assertNotIn('SECRET', str(result))
        self.assertNotIn('cook@example.com', str(result))
        # CodeRabbit on #119: whole URLs are redacted from the reason, not only their query.
        self.assertNotIn('example.com/x', str(result))

    def test_duplicate_before_commit_and_tag(self):
        from sabor.importing import video as v
        cfg = load_settings(dict(ENV, MEALIE_BASE_URL='http://mealie', MEALIE_API_TOKEN='test'))
        extraction = dict(candidates=[C], dropped=0, limitations=[])
        with patch.object(t, 'settings', return_value=cfg), patch.object(v, 'preview_video', return_value=extraction), patch.object(t, 'store_duplicate', return_value='old') as dup, patch('sabor.store.mealie.MealieRecipeStore.create_recipe', return_value=SimpleNamespace(slug='new')) as req, patch.object(v, 'tag_video') as tag:
            result = t.import_recipe('https://youtu.be/a', dry_run=False)
            self.assertEqual(result['data']['skips'][0]['reason'], 'duplicate')
            req.assert_not_called()
            dup.return_value = None
            result = t.import_recipe('https://youtu.be/a', dry_run=False)
            self.assertEqual(result['data']['provider'], 'gemini_native')
            tag.assert_called_once_with('new')

    def test_ffmpeg_is_bounded(self):
        from sabor.importing import video as v
        # Independent of whether the host has ffmpeg/ffprobe installed.
        with patch.object(v.shutil, 'which', return_value='/bin/tool'), \
                patch.object(v.subprocess, 'run', return_value=Mock(returncode=0, stdout=b'80')) as run:
            self.assertEqual(v.frames(Path('/unused.mp4')), [])
        self.assertEqual(run.call_count, 9)
        self.assertTrue(all(c.kwargs['timeout'] == 15 for c in run.call_args_list))

    def test_tiktok_guidance(self):
        from sabor.importing import video as v
        with self.assertRaises(t.ImportErrorResult) as ctx:
            v.download('https://www.tiktok.com/a', Path('/unused'))
        error = ctx.exception.result['error']
        self.assertEqual(error['code'], 'unsupported_operation')
        # Only suggest inputs import_recipe accepts: no file upload or local path.
        self.assertIn("kind='text'", error['message'])
        self.assertIn('transcripción', error['message'])
        self.assertNotIn('archivo', error['message'])

    def test_caption_parsing_and_successful_gemini_cleanup(self):
        from sabor.importing import video as v
        def extract(url, download):
            root = Path(factory.call_args.args[0]['outtmpl']).parent
            (root / 'video.mp4').write_bytes(b'video')
            (root / 'video.en.vtt').write_text('WEBVTT\n\n00:00:01.000 --> 00:00:02.000\n<b>200 g salt</b>\n')
            return {'duration': 10}
        ydl = Mock(extract_info=extract)
        factory = Mock(return_value=Mock(__enter__=Mock(return_value=ydl), __exit__=Mock(return_value=False)))
        with patch.dict(sys.modules, yt_dlp=SimpleNamespace(YoutubeDL=factory)), v.TemporaryDirectory() as root:
            media, captions = v.download('https://youtu.be/a', Path(root))
            self.assertEqual(captions, '200 g salt')
            with patch.object(v.gemini_client, '_upload_to_gemini', return_value={'ok': True, 'name': 'files/a', 'uri': 'uri', 'state': 'PROCESSING'}), patch.object(v.gemini_client, '_wait_for_gemini_processing', return_value=True) as wait, patch.object(v.gemini_client, '_extract_recipes_with_gemini', return_value={'ok': True, 'recipes': [C]}), patch.object(v.gemini_client, '_cleanup_gemini_file') as cleanup:
                result = v.GeminiNativeVideo(load_settings(ENV)).extract(media, captions, dict(language='en', max_recipes=8, min_confidence=.55))
                self.assertEqual(result['candidates'], [C])
                self.assertEqual(result['limitations'], [])
                wait.assert_called_once_with('files/a', cfg=load_settings(ENV))
                cleanup.assert_called_once_with('files/a', cfg=load_settings(ENV))

    def test_only_video_import_tag_is_added(self):
        from sabor.importing import video as v
        with patch('sabor.store.mealie.MealieRecipeStore.tag_recipe', return_value={'ok': True}) as tag:
            v.tag_video('soup')
            tag.assert_called_once_with('soup', ['video-import'])
