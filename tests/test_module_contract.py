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
    "get_shopping_sync_targets": "091ac0b4400db55cd0204d8decd6a76746f9456269ab69b14bedd4dd791bf7d0",
    "sync_shopping_list": "c903bd0d108c1231a80cd66e9ee5b8593cef203599e1218afdf965b3265c79a0",
    "import_recipe": "8da1c991c9d13470c17690d825d890d4eb9b7f7b4490361acffaee4273c52914",
    "add_to_shopping_list": "3efe414918d389283ef0b3394126399f907c51e5116752ca4f8b4aef5d87ca1c",
    "check_shopping_list_items": "e3f57862d4c913fa3b448c4f5ebf0354b2c90e1bb28ea414efa8b99039597d99",
    "get_shopping_list_items": "6394c56a022f72ddc4a7b49a8fa1103a7eda6be90c23c3a68e53cd9243bd3931",
    "remove_shopping_list_items": "3fc583904f09ed69326cf6e39881babef3b75d7205d2aa9a3ad8974b72a207a7",
    "suggest_recipes": "2bedf80b32d638229e1c8932480f5403e70f1abfd6415e31b64ac8c269f6e332",
    "update_recipe": "0a42fb71dfdc7e09fe3ade020988d0ee01721847973e5372f416053c34002d0f",
    "spanish_catalog_status": "86a2e7b817dfd97c44131476a16d072c83329a5434b3a4e024a0f5cbd0e2209f",
    "apply_spanish_catalog": "44a6883ceaf144f43fab3e05550ae336c91493db66fb8074ae799175f7c2e457",
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
    "spoonacular_search_recipes": "e98acc68c4ec9924306b238b190c9afab561971823631266b27f822858334d56",
    "spoonacular_nutrition_100g": "01018b051f2e622531510d7081b18ed77f8d1acd8432cf1b4b282b4484fb8f24",
    "save_feedback": "17aba06a697c7906f12f6810cd3ef30f8ab1e77ec6345b7352d92f2103646bcf",
    "read_memory": "79228db57e091b2e9cc41b590a5dd4ef1d7a7f5f60eac08acfe14ca0eaacd953",
    "update_pantry": "fb152616de6a8483734866e8593bd7319dcc9ac787f67c703f24221cb8a08db4",
    "update_learned_preference": "ad0b80288f1f6d139a91ec88af8f244ca344e49b09f67aaa23dd762143aa899c",
    "mercadona_search_products": "3c2a2b603d70bd99ea661051d07ecfdfcd96baf7870ff1bffa831d37248340b3",
    "mercadona_get_product_price": "cc9e87f7e9d13889ee3bf3ca77bfd991afc80a4e548537bf252ee689a3f587c1",
    "mercadona_estimate_recipe_cost": "6f6b1655ce93efe94bfa1741f25c660b3087f575391479b1e731989338234c38",
    "mercadona_estimate_shopping_list_cost": "c91da98d2d0ebf11038d4599a0e4469530a9765bcf122de7e0f8530497b4e267",
    "mercadona_get_price_drops": "5eb7ebed3940b1053cccb0f5ffb64c6d5e19c36ba1914ec8ed03c2b104abf849",
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
        self.assertEqual(len(names), 51)
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
        self.assertIs(sabor_mcp.create_recipe, recipes.create_recipe)
        self.assertEqual(sabor_mcp.CATALOG_DIR, Path(__file__).resolve().parents[1] / "catalog")


if __name__ == "__main__":
    unittest.main()
