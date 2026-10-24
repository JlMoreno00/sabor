#!/usr/bin/env python3
"""Sabor — Unified MCP Server for the Cooking Planner Agent.

Run python sabor_mcp.py for Streamable HTTP on 0.0.0.0:8080/mcp.
Legacy Python attributes remain available for existing callers and test patches.
"""

import sys
import os
from sabor.core.config_file import apply_config_file

from sabor.compat import install
from sabor.server import main, register_tools

register_tools(config_source=apply_config_file(os.environ) if __name__ == "__main__" else None)
install(sys.modules[__name__])

if __name__ == "__main__":
    main()
