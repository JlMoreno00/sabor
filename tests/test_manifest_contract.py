import ast
import json
import re
from pathlib import Path
import unittest


class ManifestContractTests(unittest.TestCase):
    def test_mcp_tool_surface_includes_readiness_tools(self):
        runtime_path = Path(__file__).resolve().parents[1] / "sabor_mcp.py"
        runtime_tree = ast.Module(body=[
            node
            for path in [runtime_path, *sorted(runtime_path.parent.glob("sabor/**/*.py"))]
            for node in ast.parse(path.read_text(encoding="utf-8")).body
        ], type_ignores=[])
        tools = [
            node.name for node in runtime_tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and any(
                isinstance(decorator, ast.Call)
                and isinstance(decorator.func, ast.Name)
                and decorator.func.id == "tool"
                for decorator in node.decorator_list
            )
        ]
        from sabor.server import TOOL_MODULES
        from importlib import import_module
        from sabor.core.toolsets import catalog
        for module in TOOL_MODULES:
            import_module(module)
        catalog_names = [fn.__name__ for group, fn, requires in catalog]
        self.assertCountEqual(catalog_names, tools)
        self.assertEqual(len(catalog_names), 52)
        self.assertEqual(len(tools), 52)
        self.assertEqual(tools.count("nutrition_set_food_profile"), 1)
        self.assertEqual(tools.count("nutrition_recipe_readiness"), 1)
        self.assertEqual(tools.count("nutrition_cookbook_readiness"), 1)
        self.assertEqual(tools.count("nutrition_correct_recipe"), 1)

        manifest_path = runtime_path.with_name("mcp-manifest.json")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(manifest["tool_count"], len(tools))

    def test_mealie_auth_uses_runtime_token_name(self):
        manifest_path = Path(__file__).resolve().parents[1] / "mcp-manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        env_vars = {item["name"]: item for item in manifest["env_vars"]}
        runtime_path = manifest_path.with_name("sabor_mcp.py")
        runtime_tree = ast.Module(body=[
            node
            for path in [runtime_path, *sorted(runtime_path.parent.glob("sabor/**/*.py"))]
            for node in ast.parse(path.read_text(encoding="utf-8")).body
        ], type_ignores=[])
        runtime_env_names = {
            node.args[0].value
            for node in ast.walk(runtime_tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "os"
            and node.func.attr == "getenv"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        }

        self.assertIn("MEALIE_API_TOKEN", env_vars)
        self.assertNotIn("MEALIE_API_KEY", env_vars)
        self.assertIn("MEALIE_API_TOKEN", runtime_env_names)
        # SQLite is the default store (#37): Mealie credentials are only needed by
        # the optional Mealie adapter, so no Mealie variable is required (#36).
        for name in ("MEALIE_BASE_URL", "MEALIE_API_TOKEN"):
            self.assertFalse(env_vars[name]["required"], name)
        self.assertTrue(env_vars["MEALIE_API_TOKEN"]["secret"])
        server = json.loads(manifest_path.with_name("server.json").read_text(encoding="utf-8"))
        for package in server["packages"]:
            for item in package.get("environmentVariables", []):
                if item["name"].startswith("MEALIE_"):
                    self.assertFalse(item.get("isRequired", False), (package["registryType"], item["name"]))
        self.assertEqual([item["name"] for item in manifest["env_vars"] if item["required"]], [])


ROOT = Path(__file__).resolve().parents[1]
# Documented in README/docs; .env.example update pending maintainer review (#37)
PENDING_ENV_EXAMPLE = {"SABOR_STORE", "SABOR_STORE_DB", "SABOR_DATA_DIR"}


class _RecordingEnv(dict):
    """Mapping that records every key the configuration parsers read."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.read = set()

    def get(self, key, default=None):
        self.read.add(key)
        return super().get(key, default)

    def __getitem__(self, key):
        self.read.add(key)
        return super().__getitem__(key)

    def __contains__(self, key):
        self.read.add(key)
        return super().__contains__(key)


def _getenv_defaults():
    """os.getenv(NAME[, default]) reads outside the parsers, with their literal defaults."""
    found = {}
    for path in [ROOT / "sabor_mcp.py", *sorted((ROOT / "sabor").rglob("*.py"))]:
        if path.name == "config.py" and path.parent.name == "sabor":
            continue  # compatibility module; mirrors load_legacy_config exactly
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "getenv" and node.args
                    and isinstance(node.args[0], ast.Constant)):
                default = node.args[1].value if len(node.args) > 1 and isinstance(node.args[1], ast.Constant) else None
                found[node.args[0].value] = default
    return found


def _compose_defaults():
    text = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    return dict(re.findall(r"\$\{([A-Z0-9_]+):-([^}]*)\}", text))


def _parsed(env):
    from sabor.core.config import load_settings
    settings = load_settings(env)
    return settings, dict(settings.legacy_config.values)


class ManifestEnvironmentContractTests(unittest.TestCase):
    """The manifest lists exactly the variables Sabor reads, with their real defaults (#34)."""

    def setUp(self):
        manifest = json.loads((ROOT / "mcp-manifest.json").read_text(encoding="utf-8"))
        self.manifest = manifest
        self.env_vars = {item["name"]: item for item in manifest["env_vars"]}

    def test_names_match_the_configuration_readers(self):
        from sabor.core.config import load_settings
        recorder = _RecordingEnv()
        load_settings(recorder)
        runtime = recorder.read | set(_getenv_defaults()) | set(_compose_defaults())
        self.assertEqual(set(self.env_vars), runtime)

    def test_names_match_env_example(self):
        example = {line.split("=", 1)[0].lstrip("# ").strip()
                   for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines()
                   if re.match(r"^#? ?[A-Z][A-Z0-9_]*=", line)}
        self.assertEqual(set(self.env_vars) - PENDING_ENV_EXAMPLE, example)
        for name in PENDING_ENV_EXAMPLE:
            self.assertNotIn(name, example)
            self.assertIn(name, self.env_vars)

    def test_defaults_are_the_code_defaults(self):
        getenv, compose = _getenv_defaults(), _compose_defaults()
        for name, item in self.env_vars.items():
            with self.subTest(name=name):
                self.assertIsInstance(item["description"], str)
                self.assertGreater(len(item["description"]), 10)
                for key in ("required", "secret"):
                    self.assertIsInstance(item[key], bool)
                if "default" not in item:
                    continue
                self.assertIsInstance(item["default"], str)
                if name in compose:
                    self.assertEqual(item["default"], compose[name])
                elif name in getenv and getenv[name] is not None:
                    self.assertEqual(item["default"], getenv[name])
                else:
                    self.assertEqual(_parsed({name: item["default"]}), _parsed({}))

    def test_secrets_have_no_default_and_spoonacular_is_not_advertised(self):
        for name, item in self.env_vars.items():
            if item["secret"]:
                self.assertNotIn("default", item, name)
        self.assertNotIn("spoonacular", self.manifest["description"].casefold())


if __name__ == "__main__":
    unittest.main()
