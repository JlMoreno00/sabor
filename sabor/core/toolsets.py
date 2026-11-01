"""Collect unchanged tool functions without registering them at import time."""
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Callable, Iterator, cast
from functools import wraps
from inspect import signature

from .config import Settings

catalog: list[tuple[str, Callable, Callable[[Settings], bool] | None]] = []

# The context of the tool call in progress. Each call binds its own value and
# resets it on exit, so servers built from different Settings never share it.
_current: ContextVar[Callable[[], Any] | None] = ContextVar("sabor_context", default=None)


def current_context() -> Any:
    """Return the context bound to the running tool call.

    Raises LookupError outside a bound call; tools never fall back to globals.
    """
    provider = _current.get()
    if provider is None:
        raise LookupError("No Sabor context is bound to this call.")
    return provider()


@contextmanager
def use_context(context: Any = None, *, factory: Callable[[], Any] | None = None) -> Iterator[None]:
    """Bind a context, or a lazily built one, for the duration of a call."""
    built = [context] if factory is None else []

    def provider():
        if not built:
            built.append(cast(Callable[[], Any], factory)())
        return built[0]
    token = _current.set(provider)
    try:
        yield
    finally:
        _current.reset(token)


def bind_context(fn, context):
    """Run a context-aware tool with an explicit context, keeping its MCP schema."""
    implementation = fn.__wrapped__ if getattr(fn, "uses_context", False) else fn

    @wraps(fn)
    def bound(*args, **kwargs):
        with use_context(context):
            return media_result(implementation(*args, **kwargs))

    setattr(bound, "__signature__", signature(fn))
    return bound


def media_result(result):
    """On MCP, video frames travel as ImageContent; the JSON keeps only their times (#102)."""
    data = result.get("data") if isinstance(result, dict) else None
    if not isinstance(data, dict):
        return result
    frames = data.get("frames")
    if not (isinstance(frames, list) and frames
            and all(isinstance(f, dict) and isinstance(f.get("data"), str) for f in frames)):
        return result
    import pydantic_core
    from mcp.types import CallToolResult, ImageContent, TextContent
    plain = {**result, "data": {**data, "frames": [{k: v for k, v in f.items() if k != "data"} for f in frames]}}
    text = pydantic_core.to_json(plain, fallback=str, indent=2).decode()
    return CallToolResult(content=[TextContent(type="text", text=text), *(
        ImageContent(type="image", data=f["data"], mimeType=f.get("mime_type", "image/jpeg")) for f in frames)],
        structuredContent=plain)


def tool(toolset: str, requires: Callable[[Settings], bool] | None = None,
         *, uses_context: bool = False):
    """Collect tools; context-aware bodies read current_context() after validation."""
    def collect(fn):
        if uses_context:
            # Direct Python callers get a fresh compatibility context, built only
            # if the body asks for it and never shared between calls.
            original = fn

            @wraps(original)
            def direct(*args, **kwargs):
                from . import context
                try:
                    current_context()
                except LookupError:
                    with use_context(factory=context.default_context):
                        return original(*args, **kwargs)
                return original(*args, **kwargs)

            setattr(direct, "__signature__", signature(original))
            setattr(direct, "uses_context", True)
            fn = direct
        catalog.append((toolset, fn, requires))
        return fn
    return collect


def mealie_ready(settings: Settings) -> bool:
    return settings.mealie_configured


def store_ready(settings: Settings) -> bool:
    """Use instance-owned store presence; retain unbound Settings compatibility."""
    try:
        return current_context().store is not None or settings.mealie_configured
    except LookupError:
        return settings.mealie_configured


def import_ready(settings: Settings) -> bool:
    # Import writes recipe pages into a store; no AI provider extracts anything (#102).
    return store_ready(settings)


def bring_ready(settings: Settings) -> bool:
    return bool(settings.bring_email and settings.bring_password)
