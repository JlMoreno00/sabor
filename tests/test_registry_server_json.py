"""server.json for the official MCP registry (#35).

The schema in tests/fixtures/mcp-server.schema.2025-12-11.json is a verbatim copy of
https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json
(modelcontextprotocol/registry, Apache-2.0 / MIT), vendored so CI validates offline.
"""
import json
import re
import unittest
from pathlib import Path

import jsonschema

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "tests" / "fixtures" / "mcp-server.schema.2025-12-11.json"
# Fixed inside the container: the package maps 127.0.0.1:8080 to it; compose-only.
NOT_FOR_PACKAGE = {"SABOR_HOST", "SABOR_PORT", "SABOR_HOST_PORT"}
# The OCI entry advertises only what the setup questions cover (#62); the full list
# stays in mcp-manifest.json. The pypi entry advertises none: `sabor-mcp setup` configures it.
OCI_ESSENTIAL = {
    "SABOR_TOOLSETS", "SABOR_STORE", "MEALIE_BASE_URL", "MEALIE_API_TOKEN",
    "SABOR_LLM_BASE_URL", "SABOR_LLM_MODEL", "SABOR_LLM_API_KEY", "SABOR_VIDEO_PROVIDER",
    "GEMINI_API_KEY", "BRING_EMAIL", "BRING_PASSWORD", "SABOR_MERCADONA_ENABLED",
    "MERCADONA_WAREHOUSE", "SABOR_OPEN_PRICES_ENABLED",
}


def _load(name):
    return json.loads((ROOT / name).read_text(encoding="utf-8"))


class ServerJsonTests(unittest.TestCase):
    def setUp(self):
        self.server = _load("server.json")
        self.manifest = _load("mcp-manifest.json")
        self.package = self.server["packages"][0]

    def test_valid_against_the_registry_schema(self):
        schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
        jsonschema.Draft7Validator(schema).validate(self.server)
        self.assertEqual(self.server["$schema"], schema["$id"])

    def test_identity_matches_image_label_and_repository(self):
        self.assertEqual(self.server["name"], "io.github.JlMoreno00/sabor")
        label = re.search(r'^LABEL io\.modelcontextprotocol\.server\.name="([^"]+)"$',
                          (ROOT / "Dockerfile").read_text(encoding="utf-8"), re.M)
        if label is None:
            self.fail("Dockerfile must carry the io.modelcontextprotocol.server.name label")
        self.assertEqual(label[1], self.server["name"])
        self.assertEqual(self.server["repository"]["url"], "https://github.com/JlMoreno00/sabor")

    def test_ghcr_package_pinned_to_the_release_version(self):
        from sabor.server import VERSION
        self.assertEqual(len(self.server["packages"]), 2)
        self.assertEqual(self.server["version"], VERSION)
        self.assertEqual(self.manifest["version"], VERSION)
        self.assertEqual(self.package["registryType"], "oci")
        self.assertEqual(self.package["identifier"], f"ghcr.io/jlmoreno00/sabor:{VERSION}")
        self.assertEqual(self.package["transport"], {"type": "streamable-http",
                                                     "url": "http://localhost:8080/mcp"})
        args = {a["name"]: a["value"] for a in self.package["runtimeArguments"]}
        self.assertEqual(args["-p"], "127.0.0.1:8080:8080")

    def test_oci_environment_variables_are_the_essential_manifest_entries(self):
        expected = [{k: v for k, v in item.items() if k in ("name", "description", "default")}
                    | {"isRequired": item["required"], "isSecret": item["secret"]}
                    for item in self.manifest["env_vars"] if item["name"] in OCI_ESSENTIAL]
        self.assertEqual(len(expected), len(OCI_ESSENTIAL))
        self.assertTrue(OCI_ESSENTIAL.isdisjoint(NOT_FOR_PACKAGE))
        self.assertEqual(self.package["environmentVariables"], expected)

    def test_pypi_package_advertises_no_environment_variables(self):
        self.assertNotIn("environmentVariables", self.server["packages"][1])

    def test_readme_carries_the_pypi_ownership_marker_once(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertEqual(re.findall(r"mcp-name: (\S+)", readme), [self.server["name"]])

    def test_pypi_package(self):
        from sabor.server import VERSION
        package = self.server["packages"][1]
        self.assertEqual(package["registryType"], "pypi")
        self.assertEqual(package["identifier"], "sabor-mcp")
        self.assertEqual(package["version"], VERSION)
        self.assertEqual(package["runtimeHint"], "uvx")
        self.assertEqual(package["transport"], {"type": "stdio"})
        self.assertEqual(self.manifest["transports"], ["stdio", "streamable-http"])

    def test_description_promises_nothing_pending_verification(self):
        description = self.server["description"].casefold()
        for unverified in ("bring", "mercadona", "import", "video", "nutrition", "spoonacular"):
            self.assertNotIn(unverified, description)


if __name__ == "__main__":
    unittest.main()
