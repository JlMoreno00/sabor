"""Collect unchanged tool functions without registering them at import time."""
from typing import Callable

from .config import Settings

catalog: list[tuple[str, Callable, Callable[[Settings], bool] | None]] = []


def tool(toolset: str, requires: Callable[[Settings], bool] | None = None):
    """Retain function identity for direct callers and compatibility patches."""
    def collect(fn):
        catalog.append((toolset, fn, requires))
        return fn
    return collect


def mealie_ready(settings: Settings) -> bool:
    return settings.mealie_configured


def gemini_ready(settings: Settings) -> bool:
    return settings.gemini_configured


def video_commit_ready(settings: Settings) -> bool:
    return settings.gemini_configured and settings.mealie_configured


def bring_ready(settings: Settings) -> bool:
    return bool(settings.bring_email and settings.bring_password)
