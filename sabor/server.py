"""FastMCP instance and explicit tool-group registration."""

from importlib import import_module
import os
from weakref import WeakKeyDictionary

from sabor.core.config import load_settings
from sabor.core.context import build_context, mealie_store_factory
from sabor.core.capabilities import describe
from sabor.core.toolsets import catalog, bind_context
from sabor.core.legacy import legacy_envelope

_registered = WeakKeyDictionary()
# Contexts per registered server, so the runtime entrypoint can start side
# effects (such as catalog warm-up) that registration itself must not trigger.
_contexts = WeakKeyDictionary()
VERSION = "0.3.0"

from mcp.server.fastmcp import FastMCP

from sabor.config import HOST, PORT

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
    "sabor.integrations.mercadona", "sabor.pricing.tools",
    "sabor.tools.nutrition",
)


def _mercadona_cache_path(warehouse: str) -> str | None:
    """Persist the catalog on the data volume when it is writable."""
    try:
        writable = os.access("/data", os.W_OK)
    except OSError:
        writable = False
    return f"/data/mercadona-catalog-{warehouse}.json" if writable else None


def register_tools(server=None, env=None) -> None:
    """Freeze configuration and register each selected tool once per server."""
    server = mcp if server is None else server
    if server in _registered:
        return
    settings = load_settings(os.environ if env is None else env)
    from sabor.importing.tools import OpenAIImportProvider
    from sabor.importing.video import GeminiNativeVideo
    from sabor.sync.bring import BringSyncProvider
    from sabor.pricing.mercadona import MercadonaPricing
    from sabor.integrations.mercadona import MERCADONA_WAREHOUSE
    context = build_context(settings, store_factory=mealie_store_factory if settings.mealie_configured else None,
                        pricing_factories=(lambda s: MercadonaPricing(
                            warehouse=MERCADONA_WAREHOUSE,
                            cache_path=_mercadona_cache_path(MERCADONA_WAREHOUSE)),)
                        if settings.mercadona_enabled else (),
                        import_factories=((OpenAIImportProvider,) if settings.llm_configured else ())
                        + ((GeminiNativeVideo,) if settings.gemini_configured else ()),
                        sync_factories=(BringSyncProvider,) if settings.bring_email and settings.bring_password else ())
    registry = context.registry
    for module_name in TOOL_MODULES:
        import_module(module_name)
    names = []
    skipped = []
    for group, fn, requires in catalog:
        if group not in registry.enabled_toolsets:
            continue
        if requires is not None and not requires(registry.settings):
            skipped.append({"tool": fn.__name__, "reason": "missing_configuration"})
            continue
        provider = {"legacy_memory": "memory", "legacy_pricing": "mercadona",
                    "legacy_spoonacular": "spoonacular", "legacy_catalog": "mealie",
                    "legacy_nutrition": "nutrition"}.get(group)
        server.add_tool(legacy_envelope(fn, provider) if group.startswith("legacy_") else bind_context(fn, context))
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
    """Start background work for a registered server (network I/O; runtime only)."""
    context = _contexts.get(mcp if server is None else server)
    for provider in (context.pricing if context is not None else ()):
        start = getattr(provider, "start_background_refresh", None)
        if callable(start):
            start()


def main() -> None:
    """Run the original Streamable HTTP entrypoint."""
    register_tools()
    start_runtime_services()
    mcp.run(transport="streamable-http")
