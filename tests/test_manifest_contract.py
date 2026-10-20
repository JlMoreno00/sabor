import ast
import json
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
        self.assertEqual(len(catalog_names), 50)
        self.assertEqual(len(tools), 50)
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
        self.assertTrue(env_vars["MEALIE_API_TOKEN"]["required"])
        self.assertTrue(env_vars["MEALIE_API_TOKEN"]["secret"])


if __name__ == "__main__":
    unittest.main()
