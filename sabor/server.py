"""FastMCP instance and explicit tool-group registration."""

import argparse
from dataclasses import replace
from sabor.core.config_file import apply_config_file
from importlib import import_module
import os
import sqlite3
from weakref import WeakKeyDictionary

from sabor.core.config import ConfigError, load_settings
from sabor.core.paths import data_dir
from sabor.core.context import assemble_context
from sabor.core.capabilities import describe
from sabor.core.toolsets import catalog, bind_context, use_context
from sabor.core.legacy import legacy_envelope

_registered = WeakKeyDictionary()
# Contexts per registered server, so the runtime entrypoint can start side
# effects (such as catalog warm-up) that registration itself must not trigger.
_contexts = WeakKeyDictionary()
VERSION = "0.3.0"

from mcp.server.fastmcp import FastMCP

HOST = os.getenv("SABOR_HOST", "0.0.0.0")
PORT = int(os.getenv("SABOR_PORT", "8080"))

mcp = FastMCP(
    "sabor",
    host=HOST,
    port=PORT,
    streamable_http_path="/mcp",
    stateless_http=True,
)

TOOL_MODULES = (
    "sabor.integrations.mealie",
    "sabor.tools.recipes", "sabor.importing.tools",
    "sabor.tools.shopping",
    "sabor.tools.shopping_items", "sabor.sync.tools",
    "sabor.tools.mealplan",
    "sabor.tools.tags",
    "sabor.tools.comments",
    "sabor.integrations.spoonacular",
    "sabor.tools.memory",
    "sabor.tools.preferences",
    "sabor.integrations.mercadona", "sabor.pricing.tools",
    "sabor.tools.nutrition",
)


def _mercadona_cache_path(warehouse: str, directory=None) -> str:
    """Resolve persistence without touching the filesystem."""
    root = data_dir(os.environ) if directory is None else directory
    return str(root / f"mercadona-catalog-{warehouse}.json")


def register_tools(server=None, env=None, *, store_factory=None, config_source=None) -> None:
    """Freeze configuration and register each selected tool once per server."""
    server = mcp if server is None else server
    if server in _registered:
        return
    settings = load_settings(os.environ if env is None else env)
    if config_source is not None:
        settings = replace(settings, config_source=config_source)
    context = (
        assemble_context(settings)
        if store_factory is None
        else assemble_context(settings, store_factory=store_factory)
    )
    registry = context.registry
    for module_name in TOOL_MODULES:
        import_module(module_name)
    names = []
    skipped = []
    for group, fn, requires in catalog:
        if group not in registry.enabled_toolsets:
            continue
        with use_context(context):
            ready = requires is None or requires(registry.settings)
        if not ready:
            skipped.append({"tool": fn.__name__, "reason": "missing_configuration"})
            continue
        provider = {"legacy_memory": "memory", "legacy_pricing": "mercadona",
                    "legacy_spoonacular": "spoonacular", "legacy_catalog": "mealie",
                    "legacy_nutrition": "nutrition"}.get(group)
        registered = legacy_envelope(fn, provider) if group.startswith("legacy_") else fn
        server.add_tool(bind_context(registered, context))
        names.append(fn.__name__)
    names.append("sabor_capabilities")

    def sabor_capabilities() -> dict:
        """Describe configured toolsets and the actual registered tool surface."""
        result = describe(context, names, VERSION)
        result["data"]["warnings"].extend(skipped)
        return result

    server.add_tool(sabor_capabilities)
    _registered[server] = registry
    _contexts[server] = context


def start_runtime_services(server=None) -> None:
    """Initialize persistence and background work; I/O belongs to runtime only."""
    context = _contexts.get(mcp if server is None else server)
    ensure_ready = getattr(context.store, "ensure_ready", None) if context is not None else None
    if callable(ensure_ready):
        try:
            ensure_ready()
        except (OSError, sqlite3.Error):
            raise ConfigError("Unable to open SQLite recipe store") from None
    for provider in (context.pricing if context is not None else ()):
        start = getattr(provider, "start_background_refresh", None)
        if callable(start):
            start()


def cli(argv=None) -> None:
    """Run the installed command (stdio by default)."""
    config_source = apply_config_file(os.environ)
    import sys
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments[:1] == ["preferences"]:
        from sabor.preferences_cli import main as preferences_main
        raise SystemExit(preferences_main(arguments[1:]))
    parser = argparse.ArgumentParser(description="Sabor MCP server")
    parser.add_argument("--transport", choices=("stdio", "http"), default="stdio")
    commands = parser.add_subparsers(dest="command")
    catalog_parser = commands.add_parser("catalog", help="Preview or apply the Spanish Mealie catalog")
    catalog_parser.add_argument("action", choices=("status", "apply"))
    args = parser.parse_args(argv)
    if args.command == "catalog":
        from sabor.catalog_cli import run_catalog
        raise SystemExit(run_catalog(args.action))
    if args.transport == "http" and "SABOR_HOST" not in os.environ:
        # A locally installed command has no authentication layer in front of it:
        # listen on loopback unless the operator explicitly chooses an interface.
        mcp.settings.host = "127.0.0.1"
        # FastMCP only enables Host/Origin validation (DNS rebinding protection)
        # when constructed on loopback; mirror its loopback defaults here.
        from mcp.server.transport_security import TransportSecuritySettings
        mcp.settings.transport_security = TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=["127.0.0.1:*", "localhost:*", "[::1]:*"],
            allowed_origins=["http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*"],
        )
    register_tools(config_source=config_source)
    start_runtime_services()
    mcp.run(transport="stdio" if args.transport == "stdio" else "streamable-http")


def main() -> None:
    """Run the original Streamable HTTP entrypoint."""
    register_tools()
    start_runtime_services()
    mcp.run(transport="streamable-http")
