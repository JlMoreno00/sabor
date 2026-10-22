"""Resolve runtime configuration without loading compatibility globals in servers."""
from importlib import import_module
from .toolsets import current_context


class RuntimeConfig:
    def __getattr__(self, name):
        try:
            context = current_context()
        except LookupError:
            return getattr(import_module("sabor.config"), name)
        return getattr(context.settings.legacy_config, name)

    def __setattr__(self, name, value):
        setattr(import_module("sabor.config"), name, value)

    def __delattr__(self, name):
        delattr(import_module("sabor.config"), name)


config = RuntimeConfig()


def instance_state():
    """Return per-server mutable state, or None for standalone compatibility."""
    try:
        return current_context().state
    except LookupError:
        return None
