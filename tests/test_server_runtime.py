"""Runtime-only side effects stay out of tool registration."""
import asyncio
import json
import unittest
from unittest.mock import patch

from mcp.server.fastmcp import FastMCP

import sabor.server as server_module
from sabor.pricing.mercadona import MercadonaPricing

ENV = {"MEALIE_BASE_URL": "http://mealie.test", "MEALIE_API_TOKEN": "t",
       "SABOR_MERCADONA_ENABLED": "true",
       "SABOR_TOOLSETS": "core,pricing"}


class ServerRuntimeTests(unittest.TestCase):
    def test_registration_does_not_start_network_refresh(self):
        with patch.object(MercadonaPricing, "start_background_refresh") as start:
            server_module.register_tools(FastMCP("t"), ENV)
        start.assert_not_called()

    def test_main_starts_refresh_for_registered_pricing_providers(self):
        fresh = FastMCP("main")
        with patch.object(server_module, "mcp", fresh), \
             patch.object(fresh, "run") as run, \
             patch.object(MercadonaPricing, "start_background_refresh") as start, \
             patch.dict("os.environ", ENV, clear=False):
            server_module.main()
        start.assert_called_once()
        run.assert_called_once_with(transport="streamable-http")

    def test_capabilities_report_release_version(self):
        server = FastMCP("v")
        with patch.object(MercadonaPricing, "start_background_refresh"):
            server_module.register_tools(server, ENV)
        out = asyncio.run(server.call_tool("sabor_capabilities", {}))
        content = list(out[0] if isinstance(out, tuple) else out)
        data = json.loads(getattr(content[0], "text"))["data"]
        self.assertEqual(data["version"], "0.3.0")


if __name__ == "__main__":
    unittest.main()
