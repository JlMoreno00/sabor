"""FastMCP instance and explicit tool-group registration."""

from importlib import import_module

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


def register_tools() -> None:
    """Import each tool group once; decorators register on the shared server."""
    for module_name in TOOL_MODULES:
        import_module(module_name)


def main() -> None:
    """Run the original Streamable HTTP entrypoint."""
    register_tools()
    mcp.run(transport="streamable-http")
