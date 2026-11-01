"""Bounded URL video download and frames; no AI provider ever sees the media (#102)."""
import base64
import re
import shutil
import subprocess
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from urllib.parse import urlsplit

from sabor.importing.tools import reject

MAX_BYTES = 100 * 1024 * 1024
MAX_SECONDS = 600
FRAME_COUNT = 8
# YouTube may serve only separate DASH video and audio streams (#102): take the best of
# each up to 720p (mp4 + m4a first), else one muxed file. ',' makes them independent
# downloads: '+' would ask yt-dlp for an unbounded ffmpeg merge, and without ffmpeg it
# aborts before downloading. Sabor merges them in merge() within the time budget. Each
# stream falls back on its own (mp4 video with WebM audio still gets both); a selection
# that still lacks video or audio is retried as one muxed file (MUXED), never kept.
FORMAT = '(bv[height<=720][ext=mp4]/bv[height<=720],ba[ext=m4a]/ba)/b[height<=720]'
MUXED = 'b[height<=720]'


def _complete(info):
    """True when the downloaded formats carry video and audio together. A missing or
    unknown codec ('none' means absent) is not taken as a missing stream."""
    picked = info.get('requested_downloads') or [info]
    return all(any(f.get(kind) != 'none' for f in picked) for kind in ('vcodec', 'acodec'))



def download(source, root):
    host = (urlsplit(source).hostname or '').lower()
    if host == 'tiktok.com' or host.endswith('.tiktok.com'):
        reject('unsupported_operation', 'TikTok bloquea descargas. Lee el caption o la transcripción '
               'del vídeo y guarda la receta con create_recipe.')
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

    info: Any = None
    for spec in (FORMAT, MUXED):
        # One file per downloaded format, so separate streams never overwrite each other.
        opts: Any = dict(format=spec,
            outtmpl=str(root / 'video.%(format_id)s.%(ext)s'), noplaylist=True, quiet=True,
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
            reason = re.sub(r'https?://\S+', '[url]', re.sub(r'^ERROR:\s*', '', str(exc)).strip())[:300]
            reject('upstream_error', 'Video download failed.', 'yt_dlp', data={'reason': reason})
        if not isinstance(info, dict) or duration_filter(info):
            reject('unsupported_operation', 'Video exceeds media limits or has no bounded duration.')
        if _complete(info):
            break
        # Video without audio (or the reverse): drop it and try one muxed file.
        for partial in root.glob('video.*'):
            partial.unlink()
    else:
        reject('unsupported_operation', 'Video has no downloadable file with both video and audio.')
    media = sorted(p for p in root.glob('video.*') if p.suffix.lower() in ('.mp4', '.webm', '.mov', '.mkv', '.m4a'))
    # The size limit covers the streams together, not each one.
    if not media or sum(p.stat().st_size for p in media) > MAX_BYTES:
        reject('unsupported_operation', 'Video unavailable or exceeds media limits.')
    if len(media) > 1:
        media = [merge(media, root, 180 - (time.monotonic() - started))]
    captions = []
    for subtitle in sorted(root.glob('*.vtt'))[:2]:
        raw = subtitle.read_bytes()[:100000].decode('utf-8', errors='replace')
        captions.extend(re.sub('<[^>]+>', '', line).strip() for line in raw.splitlines()
            if line.strip() and '-->' not in line and not line.startswith(('WEBVTT', 'NOTE', 'Kind:', 'Language:')))
    return media[0], '\n'.join(dict.fromkeys(captions))[:20000]


def merge(streams, root, remaining):
    """Merge separate video and audio streams within the download's remaining budget."""
    if not shutil.which('ffmpeg'):
        reject('provider_unconfigured', 'Merging video and audio streams requires ffmpeg installed on the system.')
    if remaining <= 0:
        reject('timeout', 'Video download exceeded its time budget.', 'yt_dlp')
    mp4 = {p.suffix.lower() for p in streams} <= {'.mp4', '.m4a'}
    output = root / ('merged.mp4' if mp4 else 'merged.mkv')
    args = ['ffmpeg', '-nostdin', '-y']
    for stream in streams:
        args += ['-i', str(stream)]
    for index in range(len(streams)):
        args += ['-map', str(index)]
    try:
        # subprocess.run kills ffmpeg when the timeout expires; the caller's temporary
        # directory removes every partial file.
        result = subprocess.run(args + ['-c', 'copy', str(output)], capture_output=True,
                                timeout=remaining, check=False)
    except subprocess.TimeoutExpired:
        reject('timeout', 'Merging video and audio streams timed out.', 'ffmpeg')
    if result.returncode or not output.exists() or output.stat().st_size > MAX_BYTES:
        reject('upstream_error', 'Merging video and audio streams failed.', 'ffmpeg')
    return output


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
