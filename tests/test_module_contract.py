"""Frozen pre-split tool names, signatures and docstrings (refactor baseline)."""

import ast
import hashlib
from pathlib import Path
import unittest

from support import install_runtime_dependency_stubs

install_runtime_dependency_stubs()
import sabor_mcp
from sabor.server import register_tools
from sabor.core.toolsets import catalog


# SHA-256 of ast.dump(arguments) + the exact uncleaned docstring, before moving.
BASELINE_CONTRACT = {
    "search_pricing_products": "731eb14dad31b462147dd8b551ee8507b3d35a7a1ab7572aed1a881d7a786034",
    "get_product_price": "1676539839c387f8c1ffb5dae583c1ae4065ce25e39796aac4302baedb90efc0",
    "estimate_cost": "bf8ec1ccc110d227248a566805f64f227bb0dea1a7636a6712fc56a978db77da",
    "get_shopping_sync_targets": "091ac0b4400db55cd0204d8decd6a76746f9456269ab69b14bedd4dd791bf7d0",
    "sync_shopping_list": "65fcaf2b6adc81d6d16ee672e163d62dfb660b2e6575dc0b3482b0ab70fd1178",
    "import_recipe": "8da1c991c9d13470c17690d825d890d4eb9b7f7b4490361acffaee4273c52914",
    "add_to_shopping_list": "3efe414918d389283ef0b3394126399f907c51e5116752ca4f8b4aef5d87ca1c",
    "check_shopping_list_items": "e3f57862d4c913fa3b448c4f5ebf0354b2c90e1bb28ea414efa8b99039597d99",
    "get_shopping_list_items": "6394c56a022f72ddc4a7b49a8fa1103a7eda6be90c23c3a68e53cd9243bd3931",
    "remove_shopping_list_items": "3fc583904f09ed69326cf6e39881babef3b75d7205d2aa9a3ad8974b72a207a7",
    "suggest_recipes": "2bedf80b32d638229e1c8932480f5403e70f1abfd6415e31b64ac8c269f6e332",
    "update_recipe": "0a42fb71dfdc7e09fe3ade020988d0ee01721847973e5372f416053c34002d0f",
    "spanish_catalog_status": "7f6abed6b30f8a20bf9ee8a7ee97370b19e3bcb54cd80b77f58504562b0b95cd",
    "apply_spanish_catalog": "d9a8660db1a250ca7b60c63f61ff2d13c8d59740874e0b52a82f2668702e69e6",
    "search_recipes": "128ffad429f12b735c1a94ab8dc5b2f4776969c9efd0df2896195d56b3bb1724",
    "get_recipe": "74049c9d77b43e8b40236a033ee69091fa89a09ead56ef7d60714106112f37b6",
    "create_recipe": "2ccf74cf9017a1ea33acf103534f4b3acbb451be6e99b9fa2045870a8dad7311",
    "get_shopping_lists": "a90ac771915d1a06659884e8fbebc687f5f6840046f62348ce1344c9d293ec27",
    "create_shopping_list": "cf4fda38b3372c074245e4a16fd673e19428015b7bdd5de0c1a1eef684005c31",
    "delete_shopping_list": "b5656aba7dc18532e21002163c8c5e3690b4e4f2750d5fc46d3f83cb7ed00b36",
    "clear_shopping_list": "4c64679ffb195b8cdd9e87b09e144ee3a7ef8e918ed009c7fcc0d21c14231c34",
    "get_or_create_shopping_list": "2a43ff281ed75d1c61ead9f5fa54305e2a7ffee8acc3c88b3643a8f57020a1f1",
    "get_mealplan_week": "5e97997f9b82608e3d13b1d521d954589dc35279a4675ed2946581e0fb2ffb67",
    "set_mealplan_entry": "b048f5fd92866f9fa6fb39057a307e8822e4fe338c3246077a3f143a72b38ab0",
    "delete_mealplan_entry": "4bcc0358b9ecda8bf0d57a9ff5ddb8f5928b40aea75c2bd027dc08ab4fbcba62",
    "clear_mealplan_week": "23459a799b10af2e537d692b5e5f871493148a006d5bc5f6da7ae33867e1de1b",
    "get_or_create_tag": "409f3d177ef4f89b3e561ff4958d4f9efe6b36daebaa97932984e3657533d0b8",
    "tag_recipe": "04669ca7e5db073047464b46f489e7e51d88a1faa45fa1988a77ca19265bbdb3",
    "add_recipe_comment": "7194080243de8eb19f479285bc73a222f4e2bd9576118c48c0ab2ff32aef68d3",
    "log_cooking_event": "f2db8653fd70ad05ba1f1758cafada8204010369a730120f3239466059ff2d36",
    "spoonacular_search_recipes": "eec6c12441449c66c4b89fa97d596d67a349fea7c36ab0b70493d8d2a7e26d48",
    "spoonacular_nutrition_100g": "4ffe9762272ec6750570342ad0589dacfe8040292eaf5e7ba515568f6709cccd",
    "save_feedback": "3a520e7042170bf03131a9682c70172e05c7c78b87ebc2bafc59301c9099e421",
    "read_memory": "f4a5f93e16e3d6a4b27fdcf8b328e666f91220eae1d9bfc126396524243d0f22",
    "update_pantry": "d1730b5ffe7af215f3458bc4e6f8a10655544c50d98e79848be457990e10b4ba",
    "update_learned_preference": "6662d7a4bffeda5d8bb22d9c037e6df7b88fd9849a53e15aacc9db64c5eabeff",
    "mercadona_get_price_drops": "23a24e912d59f9da96dbfcef9f4caa6692472009154e710ee185de1db1a22420",
    "mercadona_get_new_arrivals": "a31079566128a01a8a00dc12394227525c248b1287c7e3ada8d9c4d5ea2f033f",
    "nutrition_recipe_readiness": "633aa225a56721fed3a12c410efdb2db858ac6b4532bd7676aba92df7627b8ec",
    "nutrition_correct_recipe": "f2b12af42aee4c099e359485675d8d0e59dbbc736c61713a0074fc511768df43",
    "nutrition_status": "2890ef096888ab46688dfb697caf4c4bf9ebdbb355f70bbba6486019c39f1d55",
    "nutrition_food_profile": "adcee0af427bfe7cab1938bc16b854896e714f6a05b84e709db5f1d472bb62e2",
    "nutrition_set_food_profile": "b3c2a9ea4c4afdc32e8569e34e002f46981c5c8713119175f3da7676ae82a0d6",
    "nutrition_set_food_mapping": "f90fb1c9a439b7acdfce24a862e4671271b93cba44907fd6644249d82981883a",
    "nutrition_set_conversion_override": "ca116036c246e82d5aac5154c8f0d57436207077c3f04513dbc7ccf73cae1e11",
    "nutrition_calculate_recipe": "e9189a9d9fc735c1ea78a0325840c3d62f46604a44e73bb37744d0b9999d8f99",
    "nutrition_publish_recipe": "87b2b2b24f1bfd65ce444d47635bf3ac20d62611b620b50f427773926e566235",
    "nutrition_cookbook_readiness": "1a8c0d9aee7669ba85506eb7b31818d780cecf2641b5fe1c1da7fe2a90a41a3e",
    "nutrition_backfill_personal_recipes": "eabe5913f018c57b452fd14a1701ad6ce3a3e27e5ca5dec4eb570398f1326227",
    "nutrition_unresolved_report": "ad0b03d64424b1552f70dfb1f545cac2377ca39c6e658666c4c14c26d72f1b54"
}


class ModuleContractTests(unittest.TestCase):
    def test_registered_tool_names_match_baseline(self):
        names = [fn.__name__ for group, fn, requires in catalog]
        self.assertEqual(set(names), set(BASELINE_CONTRACT))
        self.assertEqual(len(names), 50)
        register_tools()
        self.assertEqual(
            [fn.__name__ for group, fn, requires in catalog], names,
        )

    def test_tool_signatures_and_exact_docstrings_match_baseline(self):
        root = Path(__file__).resolve().parents[1]
        actual = {}
        for path in [root / "sabor_mcp.py", *sorted((root / "sabor").rglob("*.py"))]:
            for node in ast.parse(path.read_text(encoding="utf-8")).body:
                if isinstance(node, ast.FunctionDef) and node.name in BASELINE_CONTRACT:
                    self.assertNotIn(node.name, actual)
                    actual[node.name] = hashlib.sha256(
                        (ast.dump(node.args) + str(ast.get_docstring(node, clean=False))).encode()
                    ).hexdigest()
        self.assertEqual(actual, BASELINE_CONTRACT)

    def test_legacy_patch_targets_forward_to_owning_modules(self):
        from unittest.mock import patch
        from sabor import config
        from sabor.integrations import mealie
        from sabor.tools import recipes

        original = config.MEALIE_API_TOKEN
        with patch.object(sabor_mcp, "MEALIE_API_TOKEN", "compat-token"):
            self.assertEqual(mealie._mealie_headers()["Authorization"], "Bearer compat-token")
        self.assertEqual(config.MEALIE_API_TOKEN, original)
        with patch.object(sabor_mcp, "_normalize_imported_recipe") as normalize:
            self.assertIs(mealie._normalize_imported_recipe, normalize)
        self.assertIs(getattr(sabor_mcp, "create_recipe"), recipes.create_recipe)
        self.assertEqual(getattr(sabor_mcp, "CATALOG_DIR"), Path(__file__).resolve().parents[1] / "catalog")


if __name__ == "__main__":
    unittest.main()
