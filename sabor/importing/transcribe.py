"""Optional local transcription (#102): faster-whisper on the CPU, no AI provider is called.

Installed with the `sabor-mcp[transcribe]` extra. Whisper weights are downloaded once from
Hugging Face on first use and then run locally. Each transcription runs in a subprocess,
so its time is bounded and its memory is released afterwards.
"""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

MODELS = ('tiny', 'base', 'small')
TIMEOUT_SECONDS = 300
MAX_TEXT = 20000
# The worker loads this very sabor package by its path, so it runs the server's code even
# from a source tree that is not installed (the Docker image) or next to another installed
# sabor. sys.path is not touched: av, ctranslate2 and the rest still come from the
# interpreter's own paths, in their own order.
PACKAGE = str(Path(__file__).resolve().parents[1])
# Any sabor module imported earlier (a sitecustomize or .pth) is dropped first, so no old
# submodule is reused.
BOOTSTRAP = ('import importlib.util, sys; '
             '[sys.modules.pop(name) for name in list(sys.modules) '
             'if name == "sabor" or name.startswith("sabor.")]; '
             f'spec = importlib.util.spec_from_file_location("sabor", {PACKAGE + "/__init__.py"!r}, '
             f'submodule_search_locations=[{PACKAGE!r}]); '
             'sys.modules["sabor"] = importlib.util.module_from_spec(spec); '
             'spec.loader.exec_module(sys.modules["sabor"]); ')
WORKER = BOOTSTRAP + 'from sabor.importing.transcribe import main; raise SystemExit(main(sys.argv[1:]))'


def available():
    """True when the optional transcriber is installed (checked without importing it)."""
    return importlib.util.find_spec('faster_whisper') is not None


def transcribe(path, model):
    """{source: 'local_asr', language, text} for a media file, or None."""
    if model not in MODELS:
        return None
    with TemporaryDirectory() as cwd:
        try:
            # -P keeps the (empty) working directory off sys.path; the environment is unchanged.
            result = subprocess.run([sys.executable, '-P', '-c', WORKER, str(path), model],
                                    capture_output=True, text=True, timeout=TIMEOUT_SECONDS, cwd=cwd, check=False)
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
