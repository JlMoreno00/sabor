"""Registration-only compatibility envelopes."""
import asyncio
import inspect
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from mcp.server.fastmcp import FastMCP
from sabor.core.legacy import legacy_envelope
from sabor.core.envelope import ErrorCode


class LegacyEnvelopeTests(unittest.TestCase):
    def wrap(self, value):
        return legacy_envelope(lambda: value, "memory")()

    def test_success_rows_and_gates(self):
        for value, data in [({"ok": True, "apply": False, "preview": True},
                             {"apply": False, "preview": True}),
                            ([1], [1]), ("text", "text"), ({"x": 1}, {"x": 1})]:
            self.assertEqual(self.wrap(value), {"ok": True, "data": data})

    def test_error_mapping_rows(self):
        for status, code in [(401, "authentication_failed"), (403, "authentication_failed"),
                             (404, "not_found"), (429, "rate_limited"), (500, "upstream_error")]:
            self.assertEqual(self.wrap({"ok": False, "status": status})["error"]["code"], code)
        for code in ErrorCode:
            self.assertEqual(self.wrap({"ok": False, "code": code.value})["error"]["code"], code.value)
        self.assertEqual(self.wrap({"ok": False, "code": "nutrition_disabled"})["error"]["code"], "upstream_error")

    def test_sabor_domain_identifiers_are_kept(self):
        result = self.wrap({"ok": False, "code": "nutrition_schedule_duplicate_lookup_failed",
                            "stage": "schedule_duplicate_lookup", "error": "Bearer abc123 leaked"})
        self.assertEqual(result["error"]["code"], "upstream_error")
        self.assertEqual(result["data"], {"legacy_code": "nutrition_schedule_duplicate_lookup_failed",
                                          "stage": "schedule_duplicate_lookup"})
        self.assertNotIn("abc123", json.dumps(result))
        free_text = self.wrap({"ok": False, "code": "Upstream said: secret xyz", "stage": "a b"})
        self.assertEqual(free_text.get("data", {}), {})

    def test_raw_content_is_never_error_data(self):
        secret = "fake-secret-with-no-redaction-pattern"
        result = self.wrap({"ok": False, "error": secret, "body": secret,
                            "message": secret, "details": {"error": secret},
                            "reason": secret, "retryable": True, "apply": False})
        self.assertNotIn(secret, json.dumps(result))
        self.assertEqual(result["data"], {"retryable": True, "apply": False})

    def test_exception_is_safe(self):
        def broken():
            raise ValueError("fake-secret")
        result = legacy_envelope(broken, "memory")()
        self.assertEqual(result["error"]["code"], "invalid_response")
        self.assertNotIn("fake-secret", json.dumps(result))

    def test_fastmcp_schema_and_doc_preserved(self):
        def sample(name: str, apply: bool = False) -> dict:
            """An English description."""
            return {"ok": True}
        wrapped = legacy_envelope(sample, "nutrition")
        self.assertEqual(inspect.signature(sample), inspect.signature(wrapped))
        first, second = FastMCP("first"), FastMCP("second")
        first.add_tool(sample)
        second.add_tool(wrapped)
        a = asyncio.run(first.list_tools())[0]
        b = asyncio.run(second.list_tools())[0]
        self.assertEqual(a.inputSchema, b.inputSchema)
        self.assertEqual(a.description, b.description)

    def test_registration_keeps_direct_memory_contract(self):
        from sabor import config
        from sabor.server import register_tools
        from sabor.tools.memory import read_memory
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "MEMORY.md"
            path.write_text("example memory", encoding="utf-8")
            with patch.object(config, "MEMORY_FILE", path):
                server = FastMCP("test")
                register_tools(server, {"SABOR_TOOLSETS": "legacy_memory", "MEMORY_FILE": str(path)})
                direct = read_memory()
                tool = server._tool_manager.get_tool("read_memory")
                assert tool is not None
                registered = asyncio.run(tool.run({}))
                self.assertEqual(direct, {"ok": True, "content": "example memory"})
                self.assertEqual(registered, {"ok": True, "data": {"content": "example memory"}})
