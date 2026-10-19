"""Minimal import shims for host-side unit tests without runtime dependencies."""

from __future__ import annotations

import importlib.util
import sys
from types import ModuleType


def install_runtime_dependency_stubs() -> None:
    if "mcp" not in sys.modules and importlib.util.find_spec("mcp") is None:
        mcp_module = ModuleType("mcp")
        server_module = ModuleType("mcp.server")
        fastmcp_module = ModuleType("mcp.server.fastmcp")

        class FastMCP:
            def __init__(self, *args, **kwargs):
                pass

            def tool(self):
                return lambda function: function

            def run(self, *args, **kwargs):
                pass

        fastmcp_module.FastMCP = FastMCP
        sys.modules.update({
            "mcp": mcp_module,
            "mcp.server": server_module,
            "mcp.server.fastmcp": fastmcp_module,
        })
    if "recipe_scrapers" not in sys.modules and importlib.util.find_spec("recipe_scrapers") is None:
        scraper_module = ModuleType("recipe_scrapers")

        def scrape_html(*args, **kwargs):
            raise RuntimeError("recipe_scrapers is unavailable in the host test interpreter")

        scraper_module.scrape_html = scrape_html
        sys.modules["recipe_scrapers"] = scraper_module
