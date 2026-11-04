import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from sabor.importing import tools as t



class VideoTests(unittest.TestCase):
    def setUp(self):
        from sabor.core.context import build_context
        from sabor.store.mealie import MealieRecipeStore
        def context():
            cfg = t.settings()
            return build_context(cfg, store_factory=lambda s: MealieRecipeStore(
                s.mealie_base_url, s.mealie_api_token, session=t.requests))
        p = patch('sabor.core.context.default_context', side_effect=context)
        p.start()
        self.addCleanup(p.stop)

    def test_downloader_bounds(self):
        from sabor.importing import video as v
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
            media, _, _ = v.download('https://youtu.be/a', Path(root))
            self.assertTrue(media is not None and media.exists())

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
            media, _, _ = v.download('https://youtu.be/a', Path(root))
            assert media is not None
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
                 # CodeRabbit on #119: each stream falls back on its own, so mp4 video with
                 # only WebM audio still gets both instead of a silent video.
                 ('137', '251'): [fmt('137', 'mp4', 720, 'avc1'), fmt('251', 'webm', acodec='opus')],
                 ('18',): [fmt('18', 'mp4', 360, 'avc1', 'mp4a')]}
        for expected, formats in cases.items():
            picked = list(select({'formats': formats, 'has_merged_format': False, 'incomplete_formats': False}))
            self.assertEqual(tuple(f['format_id'] for f in picked), expected)
            self.assertFalse(any('requested_formats' in f for f in picked), expected)

    def test_vertical_videos_are_limited_by_their_short_side(self):
        # Paleta on 0.4.0: TikTok videos are vertical (576x1024, 720x1280), so `height<=720`
        # dropped every format. The limit is on the short side: 720p landscape or portrait.
        # Formats go through yt-dlp's own processing, which fills aspect_ratio.
        import yt_dlp
        from sabor.importing import video as v
        ydl = yt_dlp.YoutubeDL({'quiet': True})

        def pick(spec, formats):
            info = ydl.process_ie_result({'id': 'x', 'title': 'x', 'extractor': 'test', 'extractor_key': 'Test',
                                          'webpage_url': 'https://example.invalid', 'formats': formats},
                                         download=False)
            selected = ydl.build_format_selector(spec)({'formats': info['formats'], 'has_merged_format': False,
                                                         'incomplete_formats': False})
            return tuple(f['format_id'] for f in selected)

        def muxed(fid, width, height):
            return dict(format_id=fid, ext='mp4', width=width, height=height, vcodec='h264', acodec='aac',
                        url='https://example.invalid/' + fid, protocol='https', tbr=height)
        tiktok = [muxed('576p', 576, 1024), muxed('720p', 720, 1280), muxed('1080p', 1080, 1920)]
        self.assertEqual(pick(v.MUXED, tiktok), ('720p',))
        self.assertEqual(pick(v.FORMAT, tiktok), ('720p',))
        self.assertEqual(pick(v.MUXED, [muxed('576p', 576, 1024)]), ('576p',))
        # CodeRabbit on #133: yt-dlp rounds aspect_ratio (720x722 gives 1.0), so the
        # orientation comes from the sizes themselves.
        self.assertEqual(pick(v.MUXED, [muxed('near-square', 720, 722)]), ('near-square',))
        self.assertEqual(pick(v.MUXED, [muxed('360p', 360, 640), muxed('720p', 720, 1280)]), ('720p',))
        self.assertEqual(pick(v.MUXED, [muxed('360p', 360, 640)]), ('360p',))
        # Landscape and square keep the 720p limit on their height.
        landscape = [muxed('480p', 854, 480), muxed('720p', 1280, 720), muxed('1080p', 1920, 1080)]
        self.assertEqual(pick(v.MUXED, landscape), ('720p',))
        self.assertEqual(pick(v.MUXED, [muxed('sq', 720, 720), muxed('sq-big', 1080, 1080)]), ('sq',))
        # Separate DASH streams of a vertical video: the 720-wide video plus the audio.
        dash = [dict(format_id=fid, ext=ext, width=w, height=h, vcodec=vc, acodec=ac, url='https://example.invalid/'
                     + fid, protocol='https', tbr=h or 128)
                for fid, ext, w, h, vc, ac in (('v720', 'mp4', 720, 1280, 'avc1', 'none'),
                                               ('v1080', 'mp4', 1080, 1920, 'avc1', 'none'),
                                               ('a', 'm4a', None, None, 'none', 'mp4a'))]
        self.assertEqual(pick(v.FORMAT, dash), ('v720', 'a'))

    def test_partial_stream_selection_falls_back_to_a_complete_file(self):
        # CodeRabbit on #119: a group can still yield one stream only (audio-only formats next
        # to muxed ones); the download is retried as one muxed file, never kept partial.
        from sabor.importing import video as v
        calls = []
        def extract(*args, **kwargs):
            opts = factory.call_args.args[0]
            calls.append(opts['format'])
            name, picked = (('140.m4a', [{'vcodec': 'none', 'acodec': 'mp4a'}]) if opts['format'] == v.FORMAT
                            else ('18.mp4', [{'vcodec': 'avc1', 'acodec': 'mp4a'}]))
            Path(opts['outtmpl'].replace('%(format_id)s.%(ext)s', name)).write_bytes(b'stream')
            return {'duration': 10, 'requested_downloads': picked}
        factory = Mock(return_value=Mock(__enter__=Mock(return_value=Mock(extract_info=extract)),
                                         __exit__=Mock(return_value=False)))
        with patch.dict(sys.modules, yt_dlp=SimpleNamespace(YoutubeDL=factory)), v.TemporaryDirectory() as root:
            media, _, _ = v.download('https://youtu.be/a', Path(root))
            assert media is not None
            self.assertEqual(media.name, 'video.18.mp4')
            self.assertFalse((Path(root) / 'video.140.m4a').exists())
        self.assertEqual(calls, [v.FORMAT, v.MUXED])
        # With no complete file either, the import is rejected instead of keeping one stream.
        def video_only(*args, **kwargs):
            opts = factory.call_args.args[0]
            Path(opts['outtmpl'].replace('%(format_id)s.%(ext)s', '137.mp4')).write_bytes(b'stream')
            return {'duration': 10, 'requested_downloads': [{'vcodec': 'avc1', 'acodec': 'none'}]}
        factory.return_value.__enter__.return_value.extract_info = video_only
        with patch.dict(sys.modules, yt_dlp=SimpleNamespace(YoutubeDL=factory)), v.TemporaryDirectory() as root:
            with self.assertRaises(t.ImportErrorResult) as ctx:
                v.download('https://youtu.be/a', Path(root))
        self.assertEqual(ctx.exception.result['error']['code'], 'unsupported_operation')

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
        from sabor.importing import video as v
        with patch.dict(sys.modules, yt_dlp=fake), self.assertRaises(t.ImportErrorResult) as ctx:
            v.download('https://youtu.be/V0ySydoCNXM', Path('/unused'))
        result = ctx.exception.result
        self.assertEqual((result['error']['code'], result['error']['provider']), ('upstream_error', 'yt_dlp'))
        self.assertIn('Requested format is not available', result['data']['reason'])
        self.assertNotIn('SECRET', str(result))
        self.assertNotIn('cook@example.com', str(result))
        # CodeRabbit on #119: whole URLs are redacted from the reason, not only their query.
        self.assertNotIn('example.com/x', str(result))

    def test_ffmpeg_is_bounded(self):
        from sabor.importing import video as v
        # Independent of whether the host has ffmpeg/ffprobe installed.
        with patch.object(v.shutil, 'which', return_value='/bin/tool'), \
                patch.object(v.subprocess, 'run', return_value=Mock(returncode=0, stdout=b'80')) as run:
            self.assertEqual(v.frames(Path('/unused.mp4')), [])
        self.assertEqual(run.call_count, 9)
        self.assertTrue(all(c.kwargs['timeout'] == 15 for c in run.call_args_list))

    def test_tiktok_is_tried_with_its_own_extractor(self):
        # #102 PR 3c: TikTok is no longer rejected up front; yt-dlp's TikTok extractor tries it.
        import yt_dlp
        from sabor.importing import video as v
        ydl = Mock()
        def extract(*args, **kwargs):
            opts = factory.call_args.args[0]
            self.assertEqual(opts['allowed_extractors'], ['default', '-generic'])
            Path(opts['outtmpl'].replace('%(format_id)s.%(ext)s', '0.mp4')).write_bytes(b'video')
            return {'duration': 30, 'extractor_key': 'TikTok'}
        ydl.extract_info.side_effect = extract
        factory = Mock(return_value=Mock(__enter__=Mock(return_value=ydl), __exit__=Mock(return_value=False)))
        with patch.dict(sys.modules, yt_dlp=SimpleNamespace(YoutubeDL=factory)), v.TemporaryDirectory() as root:
            media, _, info = v.download('https://www.tiktok.com/@cook/video/7300000000000000000', Path(root))
            self.assertTrue(media is not None and media.exists())
        self.assertEqual(info['extractor_key'], 'TikTok')
        # The real extractor list (no network) claims TikTok video URLs.
        real = yt_dlp.YoutubeDL({'quiet': True, 'allowed_extractors': ['default', '-generic']})
        names = [ie.IE_NAME for ie in real._ies.values()
                 if ie.suitable('https://www.tiktok.com/@cook/video/7300000000000000000')]
        self.assertIn('TikTok', names)

    def test_tiktok_short_links_are_refused(self):
        # CodeRabbit on #126: yt-dlp resolves vm./vt.tiktok.com short links with a request
        # that follows redirects outside the SSRF guard; ask for the full video URL instead.
        from sabor.importing import video as v
        factory = Mock(side_effect=AssertionError('a short link never reaches yt-dlp'))
        for source in ('https://vm.tiktok.com/ZMabc123/', 'https://VT.tiktok.com/x/'):
            with patch.dict(sys.modules, yt_dlp=SimpleNamespace(YoutubeDL=factory)), \
                    self.assertRaises(t.ImportErrorResult) as ctx:
                v.download(source, Path('/unused'))
            error = ctx.exception.result['error']
            self.assertEqual(error['code'], 'unsupported_operation', source)
            self.assertIn('www.tiktok.com/@', error['message'])
        factory.assert_not_called()

    def test_a_blocked_download_suggests_the_local_file_route(self):
        # #102 PR 3c: when TikTok (or any platform) blocks the download, the error keeps the
        # real reason and offers the local-file route and create_recipe.
        class DownloadError(Exception):
            pass
        ydl = Mock(extract_info=Mock(side_effect=DownloadError('ERROR: [TikTok] 73: Your IP address is blocked')))
        factory = Mock(return_value=Mock(__enter__=Mock(return_value=ydl), __exit__=Mock(return_value=False)))
        from sabor.importing import video as v
        with patch.dict(sys.modules, yt_dlp=SimpleNamespace(YoutubeDL=factory, DownloadError=DownloadError)), \
                self.assertRaises(t.ImportErrorResult) as ctx:
            v.download('https://www.tiktok.com/@cook/video/73', Path('/unused'))
        result = ctx.exception.result
        self.assertEqual((result['error']['code'], result['error']['provider']), ('upstream_error', 'yt_dlp'))
        self.assertIn('Your IP address is blocked', result['data']['reason'])
        for words in ('absolute path', 'create_recipe'):
            self.assertIn(words, result['error']['message'])

    def test_caption_parsing(self):
        from sabor.importing import video as v
        def extract(url, download):
            root = Path(factory.call_args.args[0]['outtmpl']).parent
            (root / 'video.mp4').write_bytes(b'video')
            (root / 'video.en.vtt').write_text('WEBVTT\n\n00:00:01.000 --> 00:00:02.000\n<b>200 g salt</b>\n')
            return {'duration': 10}
        ydl = Mock(extract_info=extract)
        factory = Mock(return_value=Mock(__enter__=Mock(return_value=ydl), __exit__=Mock(return_value=False)))
        with patch.dict(sys.modules, yt_dlp=SimpleNamespace(YoutubeDL=factory)), v.TemporaryDirectory() as root:
            _, transcript, _ = v.download('https://youtu.be/a', Path(root))
            self.assertEqual(transcript, {'source': 'subtitles', 'language': 'en', 'text': '200 g salt'})
