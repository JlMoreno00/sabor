"""Bounded URL video extraction; providers never write recipes."""
import base64
import re
import shutil
import subprocess
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Protocol
from urllib.parse import urlsplit

import sabor.importing.gemini_client as gemini_client
from sabor.importing.tools import OpenAIImportProvider, ImportErrorResult, reject, validate_recipes

MAX_BYTES = 100 * 1024 * 1024
MAX_SECONDS = 600
FRAME_COUNT = 8


class VideoExtractor(Protocol):
    id: str

    def extract(self, path: Path, captions: str, request: dict) -> dict: ...


def download(source, root):
    host = (urlsplit(source).hostname or '').lower()
    if host == 'tiktok.com' or host.endswith('.tiktok.com'):
        reject('unsupported_operation', 'TikTok bloquea descargas. Pega el caption o la transcripción '
               "del video como texto: import_recipe(source=<texto>, kind='text').")
    if urlsplit(source).scheme not in ('http', 'https'):
        reject('unsupported_operation', 'Upload guidance: local media paths are not enabled.')
    import yt_dlp

    def duration_filter(info, *, incomplete=False):
        duration = info.get('duration')
        if duration is None or duration <= 0 or duration > MAX_SECONDS:
            return 'Video duration unavailable or exceeds limit.'
        return None

    started = time.monotonic()

    def budget(progress):
        if progress.get('downloaded_bytes', 0) > MAX_BYTES or time.monotonic() - started > 180:
            raise ValueError('Media download budget exceeded')

    # YouTube may serve only separate DASH video and audio streams: take the best of each
    # up to 720p and merge them into mp4 with ffmpeg, else one muxed file (#102).
    opts: Any = dict(format='bv*[height<=720]+ba/b[height<=720]', merge_output_format='mp4',
        outtmpl=str(root / 'video.%(ext)s'), noplaylist=True, quiet=True,
        max_filesize=MAX_BYTES, match_filter=duration_filter,
        writesubtitles=True, writeautomaticsubtitles=True,
        subtitleslangs=['en', 'es'], subtitlesformat='vtt',
        socket_timeout=20, retries=1, fragment_retries=1, progress_hooks=[budget])
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(source, download=True)
    except getattr(yt_dlp, 'DownloadError', ()) as exc:
        # The real reason, redacted by the error envelope (no URL query, token or email),
        # from yt_dlp instead of a generic failure of the video provider (#102).
        reason = re.sub(r'^ERROR:\s*', '', str(exc)).strip()[:300]
        reject('upstream_error', 'Video download failed.', 'yt_dlp', data={'reason': reason})
    if not isinstance(info, dict) or duration_filter(info):
        reject('unsupported_operation', 'Video exceeds media limits or has no bounded duration.')
    media = [p for p in root.glob('video.*') if p.suffix.lower() in ('.mp4', '.webm', '.mov', '.mkv')]
    if not media or media[0].stat().st_size > MAX_BYTES:
        reject('unsupported_operation', 'Video unavailable or exceeds media limits.')
    captions = []
    for subtitle in sorted(root.glob('*.vtt'))[:2]:
        raw = subtitle.read_bytes()[:100000].decode('utf-8', errors='replace')
        captions.extend(re.sub('<[^>]+>', '', line).strip() for line in raw.splitlines()
            if line.strip() and '-->' not in line and not line.startswith(('WEBVTT', 'NOTE', 'Kind:', 'Language:')))
    return media[0], '\n'.join(dict.fromkeys(captions))[:20000]


def frames(path):
    """Eight evenly spaced samples over the bounded stream; no audio is decoded."""
    if not all(shutil.which(name) for name in ("ffmpeg", "ffprobe")):
        reject("provider_unconfigured", "Video frame extraction requires ffmpeg and ffprobe installed on the system.")
    images = []
    try:
        probe = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
            '-of', 'default=noprint_wrappers=1:nokey=1', str(path)],
            capture_output=True, timeout=15, check=False)
        duration = float(probe.stdout)
        if probe.returncode or not 0 < duration <= MAX_SECONDS:
            return []
    except (OSError, ValueError, TypeError, subprocess.TimeoutExpired):
        return []
    with TemporaryDirectory() as tmp:
        for index in range(FRAME_COUNT):
            output = Path(tmp) / f'frame-{index}.jpg'
            try:
                result = subprocess.run(['ffmpeg', '-nostdin', '-y', '-ss', str(duration * (index + .5) / FRAME_COUNT), '-i', str(path),
                    '-an', '-vf', 'scale=640:640:force_original_aspect_ratio=decrease',
                    '-frames:v', '1', str(output)], capture_output=True, timeout=15, check=False)
                if result.returncode == 0 and output.exists() and output.stat().st_size <= 1024 * 1024:
                    images.append('data:image/jpeg;base64,' + base64.b64encode(output.read_bytes()).decode())
            except (OSError, subprocess.TimeoutExpired):
                continue
    return images


class GeminiNativeVideo:
    id = 'gemini_native'
    kinds = frozenset({'video'})

    def __init__(self, cfg):
        self.cfg = cfg

    def preview(self, request):
        return preview_video(request['source'], self.cfg, request, extractor=self)

    def extract(self, path, captions, request):
        upload = gemini_client._upload_to_gemini(path, cfg=self.cfg)
        name = upload.get('name')
        try:
            if not upload.get('ok') or not name or not upload.get('uri'):
                reject('upstream_error', 'Video upload failed.', self.id)
            if upload.get('state') != 'ACTIVE' and not gemini_client._wait_for_gemini_processing(name, cfg=self.cfg):
                reject('upstream_error', 'Video processing failed.', self.id)
            result = gemini_client._extract_recipes_with_gemini(upload['uri'], request['language'], request['max_recipes'], cfg=self.cfg)
            if not result.get('ok'):
                reject('invalid_response', 'Video extraction failed.', self.id)
            return dict(validate_recipes(result['recipes'], request), limitations=[])
        finally:
            if name:
                gemini_client._cleanup_gemini_file(name, cfg=self.cfg)


class OpenAICompatibleVideo:
    id = 'openai_compatible'

    def __init__(self, cfg):
        self.cfg = cfg

    def extract(self, path, captions, request):
        images = frames(path)
        limitations = ['audio_not_analyzed']
        if not images:
            limitations.append('visual_evidence_unavailable')
        if not images and not captions:
            reject('unsupported_operation', 'Neither visual nor caption evidence is available.', self.id)
        source = ([{'type': 'text', 'text': captions}] +
                  [{'type': 'image_url', 'image_url': {'url': image}} for image in images]) if images else captions
        try:
            result = OpenAIImportProvider(self.cfg).preview(dict(request, source=source))
        except ImportErrorResult as exc:
            if not images or exc.result['error']['code'] != 'invalid_argument':
                raise
            if not captions:
                reject('unsupported_operation', 'Model cannot use images and no captions are available.', self.id)
            limitations.append('visual_evidence_unavailable')
            result = OpenAIImportProvider(self.cfg).preview(dict(request, source=captions))
        return dict(result, limitations=limitations)


def select_video(cfg) -> VideoExtractor:
    provider = cfg.video_provider or ('gemini_native' if cfg.gemini_configured else 'openai_compatible')
    if provider == 'gemini_native' and cfg.gemini_configured:
        return GeminiNativeVideo(cfg)
    if provider == 'openai_compatible' and cfg.llm_configured:
        return OpenAICompatibleVideo(cfg)
    reject('provider_unconfigured', 'Video extraction provider is not configured.', provider)


def preview_video(source, cfg, request, extractor=None):
    with TemporaryDirectory() as tmp:
        media, captions = download(source, Path(tmp))
        return (extractor or select_video(cfg)).extract(media, captions, request)


def tag_video(slug):
    from sabor.core.toolsets import current_context, use_context
    from sabor.core.context import default_context
    from sabor.store import StoreError

    try:
        ctx = current_context()
    except LookupError:
        with use_context(factory=default_context):
            return tag_video(slug)
    try:
        ctx.store.tag_recipe(slug, ['video-import'])
    except StoreError:
        reject('upstream_error', 'Video recipe tagging failed.', 'mealie')
