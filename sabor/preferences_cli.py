"""Explicit, non-destructive MEMORY.md migration command."""
import argparse
import json
import os
from pathlib import Path
import re

from sabor.core.config import load_settings
from sabor.core.context import build_context
from sabor.core.envelope import fail
from sabor.preferences import preferences_call


def parse_memory(text):
    mapped = {"Despensa Conversacional": "pantry", "Preferencias Aprendidas": "notes"}
    additions = {"pantry": [], "notes": []}
    skipped = {}
    section = None
    for line in text.splitlines():
        heading = re.match(r"^#{1,6}\s+(.+?)\s*#*\s*$", line)
        if heading:
            section = heading[1]
        else:
            bullet = re.match(r"^\s*[-*+]\s+(.+?)\s*$", line)
            if bullet:
                if section in mapped:
                    additions[mapped[section]].append(bullet[1])
                else:
                    key = section or "unsectioned"
                    skipped[key] = skipped.get(key, 0) + 1
    return additions, skipped


def main(argv=None):
    parser = argparse.ArgumentParser(prog="sabor-mcp preferences")
    parser.add_argument("command", choices=("import-memory",))
    parser.add_argument("--apply", action="store_true")
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return exc.code
    try:
        settings = load_settings(os.environ)
    except ValueError:
        print(json.dumps(fail("invalid_argument", "Invalid Sabor configuration.")))
        return 1
    try:
        additions, skipped = parse_memory(Path(settings.memory_file).read_text(encoding="utf-8"))
    except (OSError, UnicodeError):
        result = fail("invalid_response", "Unable to read legacy memory safely.")
    else:
        result = preferences_call(build_context(settings), {"add": additions}, args.apply)
        if result["ok"]:
            result["data"]["not_migrated"] = skipped
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1
