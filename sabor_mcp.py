#!/usr/bin/env python3
"""Sabor — Unified MCP Server for the Cooking Planner Agent.

Run python sabor_mcp.py for Streamable HTTP on 0.0.0.0:8080/mcp.
Legacy Python attributes remain available for existing callers and test patches.
"""

import sys

from sabor.compat import install
from sabor.server import main, register_tools

register_tools()
install(sys.modules[__name__])

if __name__ == "__main__":
    main()
