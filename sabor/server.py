"""FastMCP instance and explicit tool-group registration."""

from importlib import import_module
import os
from weakref import WeakKeyDictionary

from sabor.core.config import load_settings
from sabor.core.registry import Registry
from sabor.core.capabilities import describe
from sabor.core.toolsets import catalog

_registered = WeakKeyDictionary()

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
    "sabor.tools.recipes",
    "sabor.tools.shopping",
    "sabor.tools.shopping_items",
    "sabor.tools.mealplan",
    "sabor.tools.tags",
    "sabor.tools.comments",
    "sabor.integrations.spoonacular",
    "sabor.integrations.scraper",
    "sabor.integrations.gemini_video",
    "sabor.tools.memory",
    "sabor.integrations.bring",
    "sabor.integrations.mercadona",
    "sabor.tools.nutrition",
)


def register_tools(server=None, env=None) -> None:
    """Freeze configuration and register each selected tool once per server."""
    server = mcp if server is None else server
    if server in _registered:
        return
    registry = Registry(load_settings(os.environ if env is None else env))
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
        server.add_tool(fn)
        names.append(fn.__name__)
    names.append("sabor_capabilities")

    def sabor_capabilities() -> dict:
        """Describe configured toolsets and the actual registered tool surface."""
        result = describe(registry, names, "0.2.0")
        result["data"]["warnings"].extend(skipped)
        return result

    server.add_tool(sabor_capabilities)
    _registered[server] = registry


def main() -> None:
    """Run the original Streamable HTTP entrypoint."""
    register_tools()
    mcp.run(transport="streamable-http")
