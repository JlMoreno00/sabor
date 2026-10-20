import json
import unittest
from unittest.mock import Mock, call, patch

from tests.support import install_runtime_dependency_stubs

install_runtime_dependency_stubs()

import sabor_mcp


class MealieRecipeTests(unittest.TestCase):
    def setUp(self):
        self.token_patch = patch.object(sabor_mcp, "MEALIE_API_TOKEN", "test-token")
        self.token_patch.start()
        self.addCleanup(self.token_patch.stop)

    @patch.object(sabor_mcp.requests, "post")
    def test_create_recipe_legacy_name_only_keeps_placeholders(self, post):
        post.return_value = Mock(status_code=201, text="created")

        result = sabor_mcp.create_recipe("Legacy recipe")

        self.assertTrue(result["ok"])
        payload = json.loads(post.call_args.kwargs["json"]["data"])
        self.assertEqual(payload["name"], "Legacy recipe")
        self.assertEqual(payload["recipeIngredient"], ["Ingrediente a completar"])
        self.assertEqual(payload["recipeInstructions"], ["Instrucciones a completar"])
        self.assertEqual(payload["recipeYield"], "2 raciones")

    @patch.object(sabor_mcp.requests, "post")
    def test_create_recipe_url_remains_backwards_compatible(self, post):
        post.return_value = Mock(status_code=201, text="imported")

        result = sabor_mcp.create_recipe("Ignored legacy name", url="https://example.test/r")

        self.assertTrue(result["ok"])
        post.assert_called_once_with(
            f"{sabor_mcp.MEALIE_BASE_URL}/api/recipes/create/url",
            headers=sabor_mcp._mealie_headers(),
            json={
                "url": "https://example.test/r",
                "includeCategories": True,
                "includeTags": True,
            },
            timeout=60,
        )

    @patch.object(sabor_mcp.requests, "post")
    def test_create_recipe_structured_fields_have_no_placeholders(self, post):
        post.return_value = Mock(status_code=201, text="created")

        result = sabor_mcp.create_recipe(
            "Structured recipe",
            description="Fresh and simple",
            recipe_yield="4 servings",
            total_time="PT35M",
            ingredients=["1 tomato", "2 eggs"],
            instructions=["Chop tomato", "Cook everything"],
            tags=["quick", "dinner"],
            source_url="https://example.test/source",
        )

        self.assertTrue(result["ok"])
        payload = json.loads(post.call_args.kwargs["json"]["data"])
        self.assertEqual(
            payload,
            {
                "@context": "https://schema.org",
                "@type": "Recipe",
                "name": "Structured recipe",
                "description": "Fresh and simple",
                "recipeYield": "4 servings",
                "totalTime": "PT35M",
                "recipeIngredient": ["1 tomato", "2 eggs"],
                "recipeInstructions": ["Chop tomato", "Cook everything"],
                "keywords": ["quick", "dinner"],
                "url": "https://example.test/source",
            },
        )
        self.assertNotIn("Ingrediente a completar", json.dumps(payload))
        self.assertNotIn("Instrucciones a completar", json.dumps(payload))

    @patch.object(sabor_mcp.requests, "post")
    def test_create_recipe_partial_structured_call_does_not_inject_defaults(self, post):
        post.return_value = Mock(status_code=201, text="created")

        sabor_mcp.create_recipe("Minimal structured", description="Only this")

        payload = json.loads(post.call_args.kwargs["json"]["data"])
        self.assertEqual(payload["description"], "Only this")
        self.assertNotIn("recipeIngredient", payload)
        self.assertNotIn("recipeInstructions", payload)
        self.assertNotIn("recipeYield", payload)

    @patch.object(sabor_mcp.requests, "get")
    def test_get_recipe_returns_tag_slug(self, get):
        get.return_value = response(
            200,
            {
                "id": "recipe-id",
                "name": "Soup",
                "slug": "soup",
                "tags": [{"id": "tag-id", "name": "Warm", "slug": "warm"}],
            },
        )

        result = sabor_mcp.get_recipe("soup")

        self.assertEqual(
            result["data"]["tags"],
            [{"id": "tag-id", "name": "Warm", "slug": "warm"}],
        )

    @patch.object(sabor_mcp.requests, "post")
    @patch.object(sabor_mcp.requests, "get")
    def test_get_or_create_tag_returns_existing_slug(self, get, post):
        get.return_value = response(
            200,
            {"items": [{"id": "tag-id", "name": "Dinner", "slug": "dinner"}]},
        )

        result = sabor_mcp.get_or_create_tag("dinner")

        self.assertEqual(result["slug"], "dinner")
        self.assertFalse(result["created"])
        post.assert_not_called()

    @patch.object(sabor_mcp.requests, "post")
    @patch.object(sabor_mcp.requests, "get")
    def test_get_or_create_tag_finds_existing_tag_beyond_default_page(self, get, post):
        get.return_value = response(
            200,
            {
                "items": [
                    {"id": "existing-id", "name": "Pollo", "slug": "pollo"}
                ]
            },
        )

        result = sabor_mcp.get_or_create_tag("POLLO")

        self.assertEqual(
            result,
            {
                "ok": True,
                "id": "existing-id",
                "name": "Pollo",
                "slug": "pollo",
                "created": False,
            },
        )
        get.assert_called_once_with(
            f"{sabor_mcp.MEALIE_BASE_URL}/api/organizers/tags",
            headers=sabor_mcp._mealie_headers(),
            params={"search": "POLLO", "page": 1, "perPage": 100},
            timeout=30,
        )
        post.assert_not_called()

    @patch.object(sabor_mcp.requests, "post")
    @patch.object(sabor_mcp.requests, "get")
    def test_get_or_create_tag_returns_created_slug(self, get, post):
        get.return_value = response(200, {"items": []})
        post.return_value = response(
            201, {"id": "new-id", "name": "Dinner", "slug": "dinner"}
        )

        result = sabor_mcp.get_or_create_tag("Dinner")

        self.assertEqual(result["slug"], "dinner")
        self.assertTrue(result["created"])

    @patch.object(sabor_mcp, "get_or_create_tag")
    @patch.object(sabor_mcp.requests, "patch")
    @patch.object(sabor_mcp.requests, "get")
    def test_tag_recipe_preserves_tags_and_patches_complete_tag_refs(
        self, get, patch_request, resolve_tag
    ):
        get.return_value = response(
            200,
            {
                "tags": [
                    {"id": "old-id", "name": "Existing", "slug": "existing"}
                ]
            },
        )
        resolve_tag.return_value = {
            "ok": True,
            "id": "new-id",
            "name": "New Tag",
            "slug": "new-tag",
            "created": True,
        }
        patch_request.return_value = response(200, {})

        result = sabor_mcp.tag_recipe("recipe", ["New Tag"])

        self.assertTrue(result["ok"])
        patch_request.assert_called_once_with(
            f"{sabor_mcp.MEALIE_BASE_URL}/api/recipes/recipe",
            headers=sabor_mcp._mealie_headers(),
            json={
                "tags": [
                    {"id": "old-id", "name": "Existing", "slug": "existing"},
                    {"id": "new-id", "name": "New Tag", "slug": "new-tag"},
                ]
            },
            timeout=30,
        )

    @patch.object(sabor_mcp, "get_or_create_tag")
    @patch.object(sabor_mcp.requests, "patch")
    @patch.object(sabor_mcp.requests, "get")
    def test_tag_recipe_returns_explicit_error_when_tag_resolution_fails(
        self, get, patch_request, resolve_tag
    ):
        get.return_value = response(200, {"tags": []})
        resolve_tag.return_value = {
            "ok": False,
            "status": 500,
            "body": "tag service failed",
        }

        result = sabor_mcp.tag_recipe("recipe", ["Broken"])

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "tag_resolution_failed: Broken")
        self.assertEqual(result["tag_result"]["status"], 500)
        patch_request.assert_not_called()

    @patch.object(sabor_mcp, "get_or_create_tag")
    @patch.object(sabor_mcp.requests, "patch")
    @patch.object(sabor_mcp.requests, "get")
    def test_tag_recipe_rejects_incomplete_tag_resolution(
        self, get, patch_request, resolve_tag
    ):
        get.return_value = response(200, {"tags": []})
        resolve_tag.return_value = {
            "ok": True,
            "id": "tag-id",
            "name": "Missing slug",
            "slug": None,
        }

        result = sabor_mcp.tag_recipe("recipe", ["Missing slug"])

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "tag_resolution_incomplete: Missing slug")
        patch_request.assert_not_called()


class CatalogAndNormalizationTests(unittest.TestCase):
    def setUp(self):
        self.token_patch = patch.object(sabor_mcp, "MEALIE_API_TOKEN", "test-token")
        self.token_patch.start()
        self.addCleanup(self.token_patch.stop)

    def test_catalog_manifests_are_versioned_and_units_cover_core_families(self):
        units = sabor_mcp._load_catalog_manifest("units")
        foods = sabor_mcp._load_catalog_manifest("foods")
        self.assertEqual(units["schema_version"], 1)
        self.assertTrue(units["catalog_version"])
        self.assertTrue(foods["catalog_version"])
        families = {item["family"] for item in units["items"]}
        self.assertTrue({"mass", "volume", "domestic", "piece", "container", "shape"} <= families)
        by_name = {item["name"]: item for item in units["items"]}
        self.assertIn("g", by_name["gramo"]["aliases"])
        self.assertIn("cucharadas", by_name["cucharada"]["aliases"])
        self.assertEqual(foods["source"], "mealie-es-ES-seeder")
        self.assertEqual(foods["items"], [])

    @patch.object(sabor_mcp.requests, "get")
    def test_catalog_dry_run_paginates_and_matches_accents_aliases_without_writes(self, get):
        get.side_effect = [
            response(200, {"items": [{"id": "u1", "name": "Cucharada", "abbreviation": "cda"}], "next": "page2"}),
            response(200, {"items": [{"id": "u2", "name": "Kilogramo", "abbreviation": "kg"}], "next": None}),
            response(200, {"items": [{"id": "f1", "name": "Aceite de oliva virgen extra"}], "next": None}),
        ]
        result = sabor_mcp._reconcile_spanish_catalog(apply=False)
        self.assertTrue(result["ok"])
        self.assertFalse(result["apply"])
        self.assertGreaterEqual(result["units"]["existing"], 2)
        self.assertEqual(get.call_args_list[1].kwargs["params"]["page"], 2)

    @patch.object(sabor_mcp.requests, "post")
    @patch.object(sabor_mcp.requests, "get")
    def test_catalog_apply_is_create_only_and_reports_ambiguous_alias_conflicts(self, get, post):
        get.side_effect = [
            response(200, {"items": [
                {"id": "1", "name": "Cucharada", "abbreviation": "cda"},
                {"id": "2", "name": "Cucharadita", "abbreviation": "cda"},
            ], "next": None}),
            response(200, {"items": [], "next": None}),
        ]
        post.return_value = response(201, {"id": "new"})
        result = sabor_mcp._reconcile_spanish_catalog(apply=True)
        self.assertTrue(result["ok"])
        self.assertTrue(result["apply"])
        self.assertTrue(result["units"]["conflicts"])
        self.assertEqual(result["foods"]["created"], 0)
        self.assertTrue(all(c.args[0].endswith("/api/units") for c in post.call_args_list))
        unit_payloads = [c.kwargs["json"] for c in post.call_args_list]
        self.assertTrue(any(p.get("pluralName") for p in unit_payloads))
        self.assertTrue(any(p.get("aliases") for p in unit_payloads))
        self.assertFalse(any(c.args[0].endswith("/seeders/foods") for c in post.call_args_list))

    @patch.object(sabor_mcp.requests, "patch")
    @patch.object(sabor_mcp.requests, "post")
    @patch.object(sabor_mcp.requests, "get")
    def test_normalize_recipe_preserves_metadata_and_rejects_low_confidence_food(self, get, post, patch_request):
        original = {
            "id": "r1", "slug": "sopa", "name": "Sopa", "recipeIngredient": [
                {"quantity": 1, "unit": {"id": "u1", "name": "taza"}, "food": {"id": "f1", "name": "caldo"},
                 "originalText": "1 taza de caldo", "display": "Caldo (1 taza)", "title": "Caldo", "note": "casero", "section": "Base"},
                {"quantity": 2, "originalText": "2 tomates maduros", "display": "2 tomates maduros", "note": "pelados"},
            ]}
        parsed = [
            {"input": "1 taza de caldo", "confidence": {"average": 0.96, "food": 0.95, "unit": 0.97},
             "ingredient": {"quantity": 1, "unit": {"id": "u9", "name": "taza"}, "food": {"id": "f9", "name": "caldo"}}},
            {"input": "2 tomates maduros", "confidence": {"average": 0.80, "food": 0.4, "unit": None},
             "ingredient": {"quantity": 2, "unit": None, "food": {"id": "f2", "name": "tomate"}}},
        ]
        get.return_value = response(200, original)
        post.return_value = response(200, parsed)
        patch_request.return_value = response(200, {})
        result = sabor_mcp._normalize_imported_recipe("sopa")
        self.assertTrue(result["ok"])
        sent = patch_request.call_args.kwargs["json"]["recipeIngredient"]
        self.assertEqual(sent[0]["food"]["id"], "f9")
        self.assertEqual(sent[0]["unit"]["id"], "u9")
        self.assertEqual(sent[0]["originalText"], "1 taza de caldo")
        self.assertEqual(sent[0]["display"], "Caldo (1 taza)")
        self.assertEqual(sent[0]["section"], "Base")
        self.assertEqual(sent[0]["note"], "casero")
        self.assertEqual(sent[1], original["recipeIngredient"][1])

    @patch.object(sabor_mcp.requests, "patch")
    @patch.object(sabor_mcp.requests, "post")
    @patch.object(sabor_mcp.requests, "get")
    def test_normalize_recipe_omits_parser_unit_without_id(self, get, post, patch_request):
        get.return_value = response(200, {
            "recipeIngredient": [
                {
                    "quantity": 2,
                    "originalText": "2 cucharadas de tomate triturado",
                    "display": "2 cucharadas de tomate triturado",
                },
                {
                    "quantity": 1,
                    "food": {"id": None, "name": "cebolla"},
                    "unit": {"name": "pieza"},
                    "display": "1 cebolla",
                },
            ]
        })
        post.return_value = response(200, [
            {
                "input": "2 cucharadas de tomate triturado",
                "confidence": {"average": 0.95, "food": 0.95, "unit": 0.9},
                "ingredient": {
                    "quantity": 2,
                    "unit": {"id": None, "name": "cucharada"},
                    "food": {"id": "f1", "name": "tomate triturado"},
                },
            },
            {
                "input": "1 cebolla",
                "confidence": {"average": 0.4, "food": 0.4, "unit": 0.4},
                "ingredient": {
                    "quantity": 1,
                    "food": {"id": None, "name": "cebolla"},
                    "unit": {"id": None, "name": "pieza"},
                },
            },
        ])
        patch_request.return_value = response(200, {})

        result = sabor_mcp._normalize_imported_recipe("tomate-triturado")

        self.assertTrue(result["ok"])
        self.assertEqual(result["accepted"], 1)
        sent = patch_request.call_args.kwargs["json"]["recipeIngredient"]
        self.assertEqual(sent[0]["food"]["id"], "f1")
        self.assertNotIn("unit", sent[0])
        self.assertEqual(sent[0]["originalText"], "2 cucharadas de tomate triturado")
        self.assertNotIn("food", sent[1])
        self.assertNotIn("unit", sent[1])
        self.assertEqual(sent[1]["originalText"], "1 cebolla")

    @patch.object(sabor_mcp.requests, "patch")
    @patch.object(sabor_mcp.requests, "post")
    @patch.object(sabor_mcp.requests, "get")
    def test_normalize_recipe_promotes_raw_note_to_original_text_without_duplicate_display(
        self, get, post, patch_request
    ):
        get.return_value = response(200, {
            "recipeIngredient": [{
                "quantity": 0,
                "originalText": None,
                "display": "200 g tomate 200 g de tomate triturado",
                "note": "200 g de tomate triturado",
                "referenceId": "original-reference",
            }]
        })
        post.return_value = response(200, [{
            "input": "200 g de tomate triturado",
            "confidence": {"average": 1.0, "food": 1.0, "unit": 1.0},
            "ingredient": {
                "quantity": 200,
                "unit": {"id": "u1", "name": "g"},
                "food": {"id": "f1", "name": "tomate"},
                "note": "triturado",
                "display": "200 g tomate triturado",
                "originalText": "200 g de tomate triturado",
                "referenceId": "parser-reference",
            },
        }])
        patch_request.return_value = response(200, {})

        result = sabor_mcp._normalize_imported_recipe("tomate")

        self.assertTrue(result["ok"])
        sent = patch_request.call_args.kwargs["json"]["recipeIngredient"][0]
        self.assertEqual(sent["originalText"], "200 g de tomate triturado")
        self.assertEqual(sent["display"], "200 g tomate triturado")
        self.assertEqual(sent["note"], "triturado")
        self.assertEqual(sent["referenceId"], "original-reference")

    @patch.object(sabor_mcp.requests, "patch")
    @patch.object(sabor_mcp.requests, "post")
    @patch.object(sabor_mcp.requests, "get")
    def test_low_confidence_retained_ingredient_keeps_explicit_original_text(
        self, get, post, patch_request
    ):
        get.return_value = response(200, {
            "recipeIngredient": [{
                "quantity": 0,
                "originalText": None,
                "display": "2 cdas de aceite de oliva virgen extra",
                "note": "2 cdas de aceite de oliva virgen extra",
            }]
        })
        post.return_value = response(200, [{
            "input": "2 cdas de aceite de oliva virgen extra",
            "confidence": {"average": 0.9, "food": 0.5},
            "ingredient": {
                "quantity": 2,
                "unit": {"id": "u1", "name": "cda"},
                "food": {"id": None, "name": "aceite de oliva virgen extra"},
            },
        }])
        patch_request.return_value = response(200, {})

        result = sabor_mcp._normalize_imported_recipe("aceite")

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "retained_originals")
        patch_request.assert_called_once()
        sent = patch_request.call_args.kwargs["json"]["recipeIngredient"][0]
        self.assertEqual(sent["originalText"], "2 cdas de aceite de oliva virgen extra")

    @patch.object(sabor_mcp.requests, "patch")
    @patch.object(sabor_mcp.requests, "post")
    @patch.object(sabor_mcp.requests, "get")
    def test_normalize_recipe_schema_or_cardinality_failure_does_not_patch(self, get, post, patch_request):
        get.return_value = response(200, {"recipeIngredient": [{"originalText": "1 huevo"}]})
        post.return_value = response(200, [])
        result = sabor_mcp._normalize_imported_recipe("tortilla")
        self.assertFalse(result["ok"])
        self.assertEqual(result["warning"], "normalization_parser_cardinality_mismatch")
        patch_request.assert_not_called()

    @patch.object(sabor_mcp, "_normalize_imported_recipe")
    @patch.object(sabor_mcp.requests, "post")
    def test_all_import_paths_use_response_slug_and_warn_without_failing(self, post, normalize):
        post.return_value = response(201, "created-slug", text='"created-slug"')
        normalize.return_value = {"ok": False, "warning": "parser_unavailable"}
        with patch.object(sabor_mcp, "SABOR_NORMALIZE_IMPORTS", True):
            url_result = sabor_mcp.import_recipe_to_mealie("https://example.test/r")
            structured_result = sabor_mcp.create_recipe("Soup", ingredients=["1 tomate"])
            video_result = sabor_mcp._create_recipe_in_mealie_from_video(
                {"name": "Video soup", "ingredients": ["1 tomate"], "steps": ["Cook"]}, "video"
            )
        self.assertEqual([url_result["slug"], structured_result["slug"], video_result["slug"]], ["created-slug"] * 3)
        self.assertTrue(all(result["ok"] for result in (url_result, structured_result, video_result)))
        self.assertTrue(all(result["normalization_warning"] == "parser_unavailable" for result in (url_result, structured_result, video_result)))
        self.assertEqual(normalize.call_count, 3)

    @patch.object(sabor_mcp, "_normalize_imported_recipe")
    @patch.object(sabor_mcp.requests, "post")
    def test_placeholder_only_recipe_is_not_normalized(self, post, normalize):
        post.return_value = response(201, "legacy", text='"legacy"')
        result = sabor_mcp.create_recipe("Legacy")
        self.assertTrue(result["ok"])
        normalize.assert_not_called()


def response(status_code, data=None, text=""): 
    mock = Mock(status_code=status_code, text=text)
    mock.json.return_value = data if data is not None else {}
    return mock


if __name__ == "__main__":
    unittest.main()
