"""Collect unchanged tool functions without registering them at import time."""
from typing import Callable
from functools import wraps
from inspect import signature

from .config import Settings

catalog: list[tuple[str, Callable, Callable[[Settings], bool] | None]] = []


def bind_context(fn, context):
    """Bind deferred context calls without changing the public MCP schema."""
    if not getattr(fn, "uses_context", False):
        return fn

    @wraps(fn)
    def bound(*args, **kwargs):
        result = fn.__wrapped__(*args, **kwargs)
        return result(context) if callable(result) else result

    setattr(bound, "__signature__", signature(fn))
    return bound


def tool(toolset: str, requires: Callable[[Settings], bool] | None = None,
         *, uses_context: bool = False):
    """Collect tools; context-aware bodies defer execution until a context is bound."""
    def collect(fn):
        if uses_context:
            # Context-aware bodies return a deferred callable after validation.
            # Direct callers get a fresh compatibility context, never a singleton.
            original = fn

            @wraps(original)
            def direct(*args, **kwargs):
                from .context import default_context
                result = original(*args, **kwargs)
                return result(default_context()) if callable(result) else result

            setattr(direct, "__signature__", signature(original))
            setattr(direct, "uses_context", True)
            fn = direct
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
