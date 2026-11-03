"""Optional local transcription (#102): faster-whisper on the CPU, no AI provider is called.

Installed with the `sabor-mcp[transcribe]` extra. Whisper weights are downloaded once from
Hugging Face on first use and then run locally. Each transcription runs in a subprocess,
so its time is bounded and its memory is released afterwards.
"""
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

MODELS = ('tiny', 'base', 'small')
TIMEOUT_SECONDS = 300
MAX_TEXT = 20000
# The directory holding the sabor package: the worker imports it from there even when
# Sabor runs from a source tree that is not installed (the Docker image).
PACKAGE_ROOT = str(Path(__file__).resolve().parents[2])


def available():
    """True when the optional transcriber is installed (checked without importing it)."""
    return importlib.util.find_spec('faster_whisper') is not None


def transcribe(path, model):
    """{source: 'local_asr', language, text} for a media file, or None."""
    if model not in MODELS:
        return None
    env = dict(os.environ, PYTHONPATH=os.pathsep.join(
        part for part in (PACKAGE_ROOT, os.environ.get('PYTHONPATH', '')) if part))
    with TemporaryDirectory() as cwd:
        try:
            # -P keeps the (empty) working directory off sys.path.
            result = subprocess.run([sys.executable, '-P', '-m', 'sabor.importing.transcribe', str(path), model],
                                    capture_output=True, text=True, timeout=TIMEOUT_SECONDS, cwd=cwd, env=env,
                                    check=False)
        except (OSError, subprocess.TimeoutExpired):
            return None
    if result.returncode:
        return None
    try:
        data = json.loads(result.stdout)
        text = str(data['text']).strip()[:MAX_TEXT]
        language = str(data['language'])[:16] or None
    except (ValueError, TypeError, KeyError):
        return None
    return {'source': 'local_asr', 'language': language, 'text': text} if text else None


def main(argv=None):
    """Worker: print {"language", "text"} for one file as JSON."""
    path, model = (sys.argv[1:] if argv is None else argv)[:2]
    if model not in MODELS:
        return 2
    # Only installed with the optional `sabor-mcp[transcribe]` extra.
    from faster_whisper import WhisperModel  # pyright: ignore[reportMissingImports]
    segments, info = WhisperModel(model, device='cpu', compute_type='int8').transcribe(str(Path(path)))
    text = '\n'.join(part for part in (segment.text.strip() for segment in segments) if part)
    print(json.dumps({'language': info.language, 'text': text[:MAX_TEXT]}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
