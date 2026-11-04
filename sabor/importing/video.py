"""Bounded URL video download and the media bundle the client agent reads (#102); no AI provider sees it."""
import base64
import os
import re
import shutil
import stat
import subprocess
import time
import uuid
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from urllib.parse import urlsplit

from sabor.importing.tools import reject

MAX_BYTES = 100 * 1024 * 1024
MAX_SECONDS = 600
FRAME_COUNT = 8
# YouTube may serve only separate DASH video and audio streams (#102): take the best of
# each up to 720p (mp4 + m4a first), else one muxed file. 720p is the short side: a tall
# format (height above 720) is kept when its width is at most 720 (TikTok's 720x1280),
# and that alternative comes first so a vertical video gets its 720-wide version, not a
# smaller one; otherwise the height is at most 720. Sizes, not yt-dlp's rounded
# aspect_ratio, decide (720x722 rounds to 1.0); without a width, the height limit applies.
# ',' makes them independent
# downloads: '+' would ask yt-dlp for an unbounded ffmpeg merge, and without ffmpeg it
# aborts before downloading. Sabor merges them in merge() within the time budget. Each
# stream falls back on its own (mp4 video with WebM audio still gets both); a selection
# that still lacks video or audio is retried as one muxed file (MUXED), never kept.
_SHORT_SIDE = ('[width<=720][height>720]', '[height<=720]')


def _up_to_720p(kind, ext=''):
    return '/'.join(f'{kind}{limit}{ext}' for limit in _SHORT_SIDE)


VIDEO = _up_to_720p('bv', '[ext=mp4]') + '/' + _up_to_720p('bv')
MUXED = _up_to_720p('b')
FORMAT = f'({VIDEO},ba[ext=m4a]/ba)/{MUXED}'
# Only video platforms are fetched, by their own extractors: yt-dlp's generic extractor
# would request any URL, private addresses included, outside the import SSRF guard.
PLATFORMS = ('youtube.com', 'youtu.be', 'vimeo.com', 'instagram.com', 'tiktok.com')
# TikTok short links are resolved by a request that follows redirects outside the SSRF
# guard; only full video URLs are fetched.
SHORT_LINKS = ('vm.tiktok.com', 'vt.tiktok.com')
VIDEO_EXTENSIONS = ('.mp4', '.mov', '.webm', '.mkv', '.m4v')
# Only the containers sabor accepts are decoded: playlist or concat demuxers would let a
# file named .mp4 make ffmpeg read other files.
DEMUXERS = ['-format_whitelist', 'mov,matroska']
# On stdio the bundle also leaves its audio and frames as files the agent can open, in
# <first media root>/.sabor-media/<32 hex>/; Sabor removes its own folders after a day.
MEDIA_DIR = '.sabor-media'
KEEP_SECONDS = 24 * 3600
BUNDLE_NAME = re.compile(r'[0-9a-f]{32}')
# Written in every folder Sabor creates there; only folders holding it are ever pruned.
MARKER = '.sabor-bundle'
OUTSIDE_ROOTS = ('A local video must be a regular video file (.mp4, .mov, .webm, .mkv or .m4v) under '
                 'SABOR_MEDIA_ROOTS, which defaults to your home directory.')


def _complete(info):
    """True when the downloaded formats carry video and audio together. A missing or
    unknown codec ('none' means absent) is not taken as a missing stream."""
    picked = info.get('requested_downloads') or [info]
    return all(any(f.get(kind) != 'none' for f in picked) for kind in ('vcodec', 'acodec'))



def download(source, root, language='en', media=True):
    """Return (media path or None, transcript or None, yt-dlp info). With media=False only
    metadata and subtitles are fetched."""
    host = (urlsplit(source).hostname or '').lower()
    if host in SHORT_LINKS:
        reject('unsupported_operation', 'TikTok short links are not fetched: open the link and pass the full '
               'video URL (https://www.tiktok.com/@user/video/ID).')
    if urlsplit(source).scheme not in ('http', 'https'):
        reject('unsupported_operation', 'Upload guidance: local media paths are not enabled.')
    if not any(host == name or host.endswith('.' + name) for name in PLATFORMS):
        reject('unsupported_operation', 'Sabor fetches videos from YouTube, Vimeo, Instagram and TikTok only. '
               'For other videos, read the recipe yourself and save it with create_recipe.')
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

    # Original-language captions first (YouTube labels them '<lang>-orig'), then the user's.
    languages = list(dict.fromkeys(['.*-orig', re.escape(language), 'es', 'en']))
    info: Any = None
    for spec in (FORMAT, MUXED) if media else (None,):
        # One file per downloaded format, so separate streams never overwrite each other.
        opts: Any = dict(format=spec, skip_download=not media,
            outtmpl=str(root / 'video.%(format_id)s.%(ext)s'), noplaylist=True, quiet=True, noprogress=True,
            allowed_extractors=['default', '-generic'],
            max_filesize=MAX_BYTES, match_filter=duration_filter,
            writesubtitles=True, writeautomaticsubtitles=True,
            subtitleslangs=languages, subtitlesformat='vtt',
            socket_timeout=20, retries=1, fragment_retries=1, progress_hooks=[budget])
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(source, download=True)
        except getattr(yt_dlp, 'DownloadError', ()) as exc:
            # The real reason, redacted by the error envelope (no URL query, token or email),
            # from yt_dlp instead of a generic failure of the video provider (#102).
            reason = re.sub(r'https?://\S+', '[url]', re.sub(r'^ERROR:\s*', '', str(exc)).strip())[:300]
            # Platforms such as TikTok often block downloads: offer the local-file route (#102).
            reject('upstream_error', 'Video download failed. Save the video on your computer and pass '
                   'its absolute path (Sabor on stdio), or read the recipe yourself and save it with '
                   'create_recipe.', 'yt_dlp', data={'reason': reason})
        if not isinstance(info, dict) or duration_filter(info):
            reject('unsupported_operation', 'Video exceeds media limits or has no bounded duration.')
        if not media or _complete(info):
            break
        # Video without audio (or the reverse): drop it and try one muxed file.
        for partial in root.glob('video.*'):
            partial.unlink()
    else:
        reject('unsupported_operation', 'Video has no downloadable file with both video and audio.')
    path = None
    if media:
        streams = sorted(p for p in root.glob('video.*') if p.suffix.lower() in ('.mp4', '.webm', '.mov', '.mkv', '.m4a'))
        # The size limit covers the streams together, not each one.
        if not streams or sum(p.stat().st_size for p in streams) > MAX_BYTES:
            reject('unsupported_operation', 'Video unavailable or exceeds media limits.')
        path = streams[0] if len(streams) == 1 else merge(streams, root, 180 - (time.monotonic() - started))
    return path, transcript(root, languages), info


def transcript(root, languages):
    """The first downloaded subtitle track in priority order, as plain text."""
    tracks = {p.name.split('.')[-2]: p for p in root.glob('*.vtt') if p.name.count('.') >= 2}
    for pattern in languages:
        for lang in sorted(lang for lang in tracks if re.fullmatch(pattern, lang)):
            raw = tracks[lang].read_bytes()[:100000].decode('utf-8', errors='replace')
            lines = (re.sub('<[^>]+>', '', line).strip() for line in raw.splitlines()
                if line.strip() and '-->' not in line and not line.startswith(('WEBVTT', 'NOTE', 'Kind:', 'Language:')))
            text = '\n'.join(dict.fromkeys(line for line in lines if line))[:20000]
            if text:
                return {'source': 'subtitles', 'language': lang.removesuffix('-orig'), 'text': text}
    return None


def bundle(source, language, roots=(), keep=False, transcribe_model='base'):
    """Metadata, transcript and frames for the agent, which extracts the recipe itself.
    With keep (stdio), audio and frames are also left as files under the media roots."""
    media = all(shutil.which(name) for name in ('ffmpeg', 'ffprobe'))
    files = {'audio_path': None}
    audio_problem = None
    with TemporaryDirectory() as tmp:
        path, text, info = download(source, Path(tmp), language, media)
        folder = media_folder(roots) if keep and path else None
        try:
            images = frames(path, keep=folder) if path else []
            if folder:
                files['audio_path'], audio_problem = audio(path, folder)
        finally:
            if folder:
                folder.close()
        if text is None and path:
            text = local_transcript(path, transcribe_model)
    date = str(info.get('upload_date') or '')
    metadata = {
        'title': info.get('title'), 'uploader': info.get('uploader') or info.get('channel'),
        'duration': info.get('duration'),
        'upload_date': f'{date[:4]}-{date[4:6]}-{date[6:]}' if re.fullmatch(r'\d{8}', date) else None,
        'source_url': info.get('webpage_url') or source,
        'platform': str(info.get('extractor_key') or info.get('extractor') or '').lower() or None,
        'description': (info.get('description') or '')[:5000]}
    limitations = []
    if not images:
        limitations.append({'code': 'no_frames', 'message': 'No video frames: frame extraction needs ffmpeg and '
                            'ffprobe installed on the system.' if not media else 'No video frames could be extracted.'})
    if text is None:
        limitations.append(no_transcript('The video has no platform subtitles.'))
    limitations.extend(file_limitations(keep and path, folder, audio_problem))
    return dict(metadata=metadata, transcript=text, frames=images, limitations=limitations, **files,
                next_step=next_step(language, metadata['source_url'], files['audio_path']))


def local_transcript(path, model):
    """Optional on-device transcription (sabor-mcp[transcribe]); None when not installed."""
    from . import transcribe
    return transcribe.transcribe(path, model) if transcribe.available() else None


def no_transcript(reason):
    from . import transcribe
    hint = ('' if transcribe.available() else
            ' Install the optional sabor-mcp[transcribe] extra to transcribe the audio on this computer.')
    return {'code': 'no_transcript', 'message': reason + hint}


def next_step(language, source, audio_path=None):
    step = (f'Extract the recipe from the description, transcript and frames in language '
            f'{language!r}, show it to the user as a preview, save it with create_recipe adding '
            f"'Source: {source}' to the description, then tag it video-import.")
    return step + (' If you can transcribe audio, the soundtrack is the file at audio_path.' if audio_path else '')


AUDIO_PROBLEMS = {'no_audio': 'The video has no audio track.',
                  'audio_failed': 'The soundtrack could not be extracted: ffmpeg failed, or ffprobe could '
                                  'not tell whether the video has audio.',
                  'audio_not_saved': f'The soundtrack was extracted but could not be written to {MEDIA_DIR}.'}


def file_limitations(wanted, folder, audio_problem):
    if wanted and folder is None:
        return [{'code': 'no_media_paths', 'message': f'Audio and frame files could not be written to '
                 f'{MEDIA_DIR} in the first SABOR_MEDIA_ROOTS directory; the frames are still attached.'}]
    if folder is not None and audio_problem:
        return [{'code': audio_problem, 'message': AUDIO_PROBLEMS[audio_problem]}]
    return []


class MediaFolder:
    """A bundle folder held open by descriptor: every write goes through it, so swapping a
    directory on the path after the checks never redirects a file."""

    def __init__(self, fd, path):
        self.fd, self.path = fd, path

    def write(self, name, data):
        """A new 0600 file in the folder (never replacing or following a link); its path."""
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=self.fd)
        with open(fd, 'wb') as out:
            out.write(data)
        return str(self.path / name)

    def close(self):
        os.close(self.fd)


_DIR = os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0) | getattr(os, 'O_NOFOLLOW', 0)
# Descriptor-relative calls are POSIX only; elsewhere no files are left (no_media_paths).
_ANCHORED = (hasattr(os, 'O_DIRECTORY') and hasattr(os, 'O_NOFOLLOW') and shutil.rmtree.avoids_symlink_attacks
             and {os.open, os.mkdir, os.stat} <= os.supports_dir_fd)


def media_folder(roots):
    """A new private folder under <first root>/.sabor-media, or None when it cannot be used.

    .sabor-media is opened once without following links and must be a directory owned by
    the user; pruning, the new folder and its files then go through that descriptor.
    """
    if not _ANCHORED:
        return None
    opened = []
    try:
        root = Path(roots[0] if roots else Path.home()).resolve(strict=True)
        root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
        opened.append(root_fd)
        try:
            os.mkdir(MEDIA_DIR, 0o700, dir_fd=root_fd)
        except FileExistsError:
            pass
        base_fd = os.open(MEDIA_DIR, _DIR, dir_fd=root_fd)
        opened.append(base_fd)
        info = os.fstat(base_fd)
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
            return None
        try:
            prune(base_fd)
        except OSError:
            pass  # pruning never costs the import its files
        name = uuid.uuid4().hex
        os.mkdir(name, 0o700, dir_fd=base_fd)
        folder = MediaFolder(os.open(name, _DIR, dir_fd=base_fd), root / MEDIA_DIR / name)
        try:
            folder.write(MARKER, b'')
        except OSError:
            folder.close()
            raise
        return folder
    except (OSError, RuntimeError):
        return None
    finally:
        for fd in opened:
            os.close(fd)


def prune(base_fd):
    """Remove folders Sabor created that are older than KEEP_SECONDS.

    A folder qualifies only when its name is 32 lowercase hex, it opens as a real directory
    (no link) and it holds Sabor's marker as a regular file. Every other entry stays.
    """
    cutoff = time.time() - KEEP_SECONDS
    with os.scandir(base_fd) as entries:
        names = [entry.name for entry in entries if BUNDLE_NAME.fullmatch(entry.name)]
    for name in names:
        try:
            fd = os.open(name, _DIR, dir_fd=base_fd)
        except OSError:
            continue
        try:
            sabor = (os.fstat(fd).st_mtime < cutoff
                     and stat.S_ISREG(os.stat(MARKER, dir_fd=fd, follow_symlinks=False).st_mode))
        except OSError:
            sabor = False
        finally:
            os.close(fd)
        if sabor:
            try:
                # Relative to .sabor-media and fd-based: links inside are removed, never followed.
                shutil.rmtree(name, dir_fd=base_fd)
            except OSError:
                continue


def audio(path, folder):
    """(path, None) for the soundtrack as AAC written to the folder as audio.m4a (0600),
    else (None, reason): 'no_audio' when the video has no audio track, 'audio_failed' when
    it has one but ffmpeg could not extract it (or the probe failed), 'audio_not_saved'
    when it was extracted but could not be written."""
    if not has_audio(path):
        return None, 'no_audio'
    with TemporaryDirectory() as tmp:
        output = Path(tmp) / 'audio.m4a'
        try:
            result = subprocess.run(['ffmpeg', '-nostdin', '-n', *DEMUXERS, '-i', str(path), '-vn',
                                     '-c:a', 'aac', '-b:a', '96k', str(output)],
                                    capture_output=True, timeout=120, check=False)
        except (OSError, subprocess.TimeoutExpired):
            return None, 'audio_failed'
        if result.returncode or not output.exists():
            return None, 'audio_failed'
        if output.stat().st_size > MAX_BYTES:
            return None, 'audio_not_saved'
        try:
            return folder.write('audio.m4a', output.read_bytes()), None
        except OSError:
            return None, 'audio_not_saved'


def has_audio(path):
    """False only when ffprobe reports no audio stream; a failed probe counts as audio, so
    the extraction is tried and a failure is reported as such."""
    try:
        probe = subprocess.run(['ffprobe', '-v', 'error', *DEMUXERS, '-select_streams', 'a',
                                '-show_entries', 'stream=index', '-of', 'csv=p=0', str(path)],
                               capture_output=True, timeout=15, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return True
    return bool(probe.returncode) or bool(probe.stdout.strip())


def local_file(text, roots):
    """The resolved path and stat of a local video under an allowed root, or reject.

    No '..' is accepted; symlinks are resolved and the target must stay inside a root.
    """
    raw = Path(text)
    if not raw.is_absolute() or '..' in raw.parts:
        reject('invalid_argument', OUTSIDE_ROOTS)
    try:
        path = raw.resolve(strict=True)
        allowed = [Path(root).resolve() for root in roots or (str(Path.home()),)]
        info = path.stat()
    except (OSError, RuntimeError):
        reject('invalid_argument', OUTSIDE_ROOTS)
    if (path.suffix.lower() not in VIDEO_EXTENSIONS or not stat.S_ISREG(info.st_mode)
            or not any(path.is_relative_to(root) for root in allowed)):
        reject('invalid_argument', OUTSIDE_ROOTS)
    if info.st_size > MAX_BYTES:
        reject('unsupported_operation', 'Video exceeds media limits.')
    return path, info


def private_copy(path, info, root):
    """Copy the validated file, opened without following links, so ffmpeg never reads a
    path that could change after validation."""
    copy = root / ('video' + path.suffix.lower())
    try:
        source = open(os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0)), 'rb')
    except OSError:
        reject('invalid_argument', OUTSIDE_ROOTS)
    with source, open(copy, 'wb') as out:
        opened = os.fstat(source.fileno())
        if (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino) or not stat.S_ISREG(opened.st_mode):
            reject('invalid_argument', OUTSIDE_ROOTS)
        copied = 0
        while chunk := source.read(1 << 20):
            copied += len(chunk)
            if copied > MAX_BYTES:
                reject('unsupported_operation', 'Video exceeds media limits.')
            out.write(chunk)
    return copy


def local_bundle(text, roots, language, keep=True, transcribe_model='base'):
    """The media bundle of a local video file (stdio only; checked by the caller)."""
    if not all(shutil.which(name) for name in ('ffmpeg', 'ffprobe')):
        reject('provider_unconfigured', 'Local video files need ffmpeg and ffprobe installed on the system.')
    path, info = local_file(text, roots)
    with TemporaryDirectory() as tmp:
        copy = private_copy(path, info, Path(tmp))
        seconds = duration(copy)
        if seconds is None:
            reject('unsupported_operation', 'Video exceeds media limits or has no bounded duration.')
        folder = media_folder(roots) if keep else None
        try:
            images = frames(copy, seconds, keep=folder)
            audio_path, audio_problem = audio(copy, folder) if folder else (None, None)
        finally:
            if folder:
                folder.close()
        transcript = local_transcript(copy, transcribe_model)
    metadata = {'title': path.stem, 'uploader': None, 'duration': seconds, 'upload_date': None,
                'source_url': None, 'source_path': str(path), 'platform': 'local', 'description': ''}
    limitations = [] if images else [{'code': 'no_frames', 'message': 'No video frames could be extracted.'}]
    if transcript is None:
        limitations.append(no_transcript('A local video has no platform subtitles.'))
    limitations.extend(file_limitations(keep, folder, audio_problem))
    return dict(metadata=metadata, transcript=transcript, frames=images, limitations=limitations,
                audio_path=audio_path,
                # The file name is third-party text: it stays in metadata, never in instructions.
                next_step=next_step(language, 'the local video file', audio_path))


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
        args += DEMUXERS + ['-i', str(stream)]
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


def duration(path):
    """Seconds of a bounded video (0 < s ≤ MAX_SECONDS) according to ffprobe, else None."""
    try:
        probe = subprocess.run(['ffprobe', '-v', 'error', *DEMUXERS, '-show_entries', 'format=duration',
            '-of', 'default=noprint_wrappers=1:nokey=1', str(path)],
            capture_output=True, timeout=15, check=False)
        seconds = float(probe.stdout)
    except (OSError, ValueError, TypeError, subprocess.TimeoutExpired):
        return None
    return seconds if not probe.returncode and 0 < seconds <= MAX_SECONDS else None


def frames(path, seconds=None, keep=None):
    """Eight evenly spaced JPEG samples (≤640 px) with their time; no audio is decoded.
    With keep (a bundle folder), each frame is also written there and gets its path."""
    if not all(shutil.which(name) for name in ("ffmpeg", "ffprobe")):
        reject("provider_unconfigured", "Video frame extraction requires ffmpeg and ffprobe installed on the system.")
    images = []
    length = duration(path) if seconds is None else seconds
    if length is None:
        return []
    with TemporaryDirectory() as tmp:
        for index in range(FRAME_COUNT):
            output = Path(tmp) / f'frame-{index}.jpg'
            at = length * (index + .5) / FRAME_COUNT
            try:
                result = subprocess.run(['ffmpeg', '-nostdin', '-y', '-ss', str(at), *DEMUXERS, '-i', str(path),
                    '-an', '-vf', 'scale=640:640:force_original_aspect_ratio=decrease',
                    '-frames:v', '1', str(output)], capture_output=True, timeout=15, check=False)
                if result.returncode == 0 and output.exists() and output.stat().st_size <= 1024 * 1024:
                    data = output.read_bytes()
                    frame = {'index': index, 'time': round(at, 1), 'mime_type': 'image/jpeg',
                             'data': base64.b64encode(data).decode()}
                    if keep is not None:
                        try:
                            frame['path'] = keep.write(f'frame-{index}.jpg', data)
                        except OSError:
                            pass  # the attached image is kept without a path
                    images.append(frame)
            except (OSError, subprocess.TimeoutExpired):
                continue
    return images
