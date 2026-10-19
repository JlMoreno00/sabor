import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


from tests.support import install_runtime_dependency_stubs

install_runtime_dependency_stubs()

import sabor_mcp
from nutrition.readiness import FoodEvidence, ReadinessEvidence


def ingredient(quantity=None, unit=None, food_id=None, text="salt to taste", **extra):
    value = {
        "id": extra.pop("id", "ingredient"), "quantity": quantity,
        "unit": {"id": unit, "name": unit, "abbreviation": unit} if unit else None,
        "food": {"id": food_id, "name": food_id} if food_id else None,
        "originalText": text,
    }
    value.update(extra)
    return value


def recipe(slug="recipe", *items):
    return {
        "id": f"id-{slug}", "slug": slug, "name": slug.title(),
        "description": "private description", "recipeInstructions": [{"text": "private step"}],
        "recipeServings": 4, "recipeIngredient": list(items) or [
            ingredient(100, "g", "food", "100 g food")
        ],
    }


def response(body, status=200, headers=None, text="private body"):
    value = Mock(status_code=status, headers=headers or {}, text=text)
    value.json.return_value = body
    return value


class ReadinessToolTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.db_path = Path(self.tempdir.name) / "absent.db"

    def single(self, value, **kwargs):
        with patch.object(sabor_mcp, "SABOR_NUTRITION_ENABLED", False), \
             patch.object(sabor_mcp, "SABOR_NUTRITION_DB", self.db_path), \
             patch.object(sabor_mcp.requests, "get", return_value=response(value)) as get:
            result = sabor_mcp.nutrition_recipe_readiness(value["slug"], **kwargs)
        return result, get

    def cookbook_responses(self, recipes):
        summaries = [{"id": item["id"], "slug": item["slug"], "name": item["name"]}
                     for item in recipes]
        return [
            response({"items": [{"id": "cookbook-id", "name": "Mis recetas"}]}),
            response({"items": summaries}),
            *(response(item) for item in recipes),
        ]

    def test_41_disabled_runtime_assesses_without_service_db_parser_or_provider(self):
        value = recipe()
        with patch.object(sabor_mcp, "SABOR_NUTRITION_ENABLED", False), \
             patch.object(sabor_mcp, "SABOR_NUTRITION_DB", self.db_path), \
             patch.object(sabor_mcp, "_nutrition_service") as service, \
             patch.object(sabor_mcp.requests, "get", return_value=response(value)), \
             patch.object(sabor_mcp.requests, "post") as post, \
             patch("nutrition.db.NutritionDB") as database:
            result = sabor_mcp.nutrition_recipe_readiness("recipe")
        self.assertTrue(result["ok"])
        service.assert_not_called()
        database.assert_not_called()
        post.assert_not_called()

    def test_42_single_tool_performs_one_mealie_get_and_no_http_mutation(self):
        value = recipe()
        with patch.object(sabor_mcp, "SABOR_NUTRITION_DB", self.db_path), \
             patch.object(sabor_mcp.requests, "get", return_value=response(value)) as get, \
             patch.object(sabor_mcp.requests, "post") as post, \
             patch.object(sabor_mcp.requests, "patch") as patch_request, \
             patch.object(sabor_mcp.requests, "delete") as delete:
            result = sabor_mcp.nutrition_recipe_readiness("recipe")
        self.assertTrue(result["ok"])
        get.assert_called_once()
        self.assertTrue(get.call_args.args[0].endswith("/api/recipes/recipe"))
        post.assert_not_called()
        patch_request.assert_not_called()
        delete.assert_not_called()

    def test_43_completed_blocked_assessment_is_ok_with_zero_runtime_counters(self):
        value = recipe("blocked", ingredient())
        result, _ = self.single(value)
        self.assertTrue(result["ok"])
        self.assertFalse(result["details"]["assessment"]["ready"])
        self.assertEqual(result["details"]["runtimeProof"], {"providerCalls": 0, "writes": 0})

    def test_44_single_detail_bounds_original_text_and_includes_suggestion(self):
        value = recipe("long", ingredient(text="x" * 1000))
        result, _ = self.single(value, include_suggestions=True)
        issue = result["details"]["assessment"]["issues"][0]
        self.assertLessEqual(len(issue["originalText"]), 300)
        self.assertIn("suggestion", issue)

    def test_45_single_can_omit_suggestions(self):
        value = recipe("blocked", ingredient())
        result, _ = self.single(value, include_suggestions=False)
        self.assertNotIn("suggestion", result["details"]["assessment"]["issues"][0])

    def test_46_recipe_not_found_uses_safe_existing_error_code(self):
        with patch.object(sabor_mcp.requests, "get", return_value=response({}, status=404, text="secret")):
            result = sabor_mcp.nutrition_recipe_readiness("missing")
        self.assertEqual(result["code"], "nutrition_recipe_not_found")
        self.assertFalse(result["ok"])
        self.assertNotIn("secret", repr(result))

    def test_47_operational_exception_does_not_leak_body_headers_or_exception_text(self):
        secret = "token body header exception"
        failures = (
            response({}, status=503, headers={"Authorization": secret}, text=secret),
            sabor_mcp.requests.exceptions.Timeout(secret),
        )
        for failure in failures:
            with self.subTest(failure=type(failure).__name__), patch.object(sabor_mcp.requests, "get") as get:
                if isinstance(failure, Exception):
                    get.side_effect = failure
                else:
                    get.return_value = failure
                result = sabor_mcp.nutrition_recipe_readiness("recipe")
            self.assertFalse(result["ok"])
            self.assertEqual(result["code"], "nutrition_calculation_failed")
            self.assertNotIn(secret, repr(result))

    def test_48_cookbook_rejects_out_of_range_limit_and_detail_limit_without_io(self):
        cases = ((0, 0), (101, 0), (1, -1), (1, 26), (True, 1), (1, True))
        with patch.object(sabor_mcp.requests, "get") as get:
            for limit, detail_limit in cases:
                with self.subTest(limit=limit, detail_limit=detail_limit):
                    result = sabor_mcp.nutrition_cookbook_readiness(
                        limit=limit, detail_limit=detail_limit,
                    )
                    self.assertFalse(result["ok"])
            get.assert_not_called()

    def test_49_cookbook_tool_uses_get_only_and_never_initializes_nutrition(self):
        values = [recipe("one"), recipe("two")]
        with patch.object(sabor_mcp, "SABOR_NUTRITION_ENABLED", False), \
             patch.object(sabor_mcp, "SABOR_NUTRITION_DB", self.db_path), \
             patch.object(sabor_mcp, "_nutrition_service") as service, \
             patch.object(sabor_mcp.requests, "get", side_effect=self.cookbook_responses(values)) as get, \
             patch.object(sabor_mcp.requests, "post") as post, \
             patch.object(sabor_mcp.requests, "patch") as patch_request:
            result = sabor_mcp.nutrition_cookbook_readiness(limit=2, detail_limit=2)
        self.assertTrue(result["ok"])
        self.assertEqual(get.call_count, 4)
        service.assert_not_called()
        post.assert_not_called()
        patch_request.assert_not_called()

    def test_50_cookbook_aggregates_roots_gates_and_bounds_details(self):
        values = [
            recipe("quantity", ingredient()),
            recipe("food", ingredient(1, "g", None, "seasoning")),
            recipe("ready"),
        ]
        with patch.object(sabor_mcp, "SABOR_NUTRITION_DB", self.db_path), \
             patch.object(sabor_mcp.requests, "get", side_effect=self.cookbook_responses(values)):
            result = sabor_mcp.nutrition_cookbook_readiness(limit=3, detail_limit=2)
        details = result["details"]
        self.assertEqual(details["assessedCount"], 3)
        self.assertEqual(len(details["recipes"]), 2)
        self.assertEqual(details["omittedCount"], 1)
        self.assertEqual(details["rootCounts"]["missing_quantity"], 1)
        self.assertIn("gateCounts", details)

    def test_cookbook_aggregates_all_publication_provenance_under_card_truncation(self):
        categories = (
            ["absent"] * 13 + ["owned"] * 13 + ["mismatch"] * 13
            + ["unknown"] * 13 + ["protected"] * 12
        )
        values = [recipe(f"recipe-{index}") for index in range(64)]
        assessments = [{
            "root": "ready", "ready": True,
            "gates": {"publication": category in {"absent", "owned"}},
            "rootCounts": {"ready": 1}, "score": 100,
            "fingerprint": f"fingerprint-{index}", "issueCount": 0,
            "warningCount": 0, "issues": [], "warnings": [],
            "publicationProvenance": category,
        } for index, category in enumerate(categories)]
        with patch.object(sabor_mcp, "SABOR_NUTRITION_DB", self.db_path), \
             patch.object(sabor_mcp.requests, "get",
                          side_effect=self.cookbook_responses(values)), \
             patch.object(sabor_mcp, "_nutrition_readiness_assessment",
                          side_effect=assessments):
            result = sabor_mcp.nutrition_cookbook_readiness(limit=64, detail_limit=2)

        details = result["details"]
        self.assertEqual(details["assessedCount"], 64)
        self.assertEqual(len(details["recipes"]), 2)
        self.assertEqual(details["omittedCount"], 62)
        self.assertEqual(details["publicationProvenanceCounts"], {
            "absent": 13, "owned": 13, "mismatch": 13,
            "unknown": 13, "protected": 12,
        })
        self.assertNotIn("published_payload_json", repr(result))

    def test_51_cookbook_response_omits_private_and_raw_recipe_fields(self):
        value = recipe("private", ingredient(text="private ingredient"))
        value["preview"] = "private preview"
        value["previews"] = ["private previews"]
        with patch.object(sabor_mcp, "SABOR_NUTRITION_DB", self.db_path), \
             patch.object(sabor_mcp.requests, "get", side_effect=self.cookbook_responses([value])):
            result = sabor_mcp.nutrition_cookbook_readiness(limit=1, detail_limit=1)
        envelope = repr(result)
        for private in ("originalText", "private ingredient", "private description", "private step",
                        "private preview", "private previews", "recipeInstructions",
                        "description", "preview", "previews", "raw"):
            self.assertNotIn(private, envelope)

    def test_52_cookbook_fingerprint_is_deterministic_with_bounded_runtime_proof(self):
        values = [recipe("one"), recipe("two", ingredient())]
        results = []
        for ordered in (values, list(reversed(copy.deepcopy(values)))):
            with patch.object(sabor_mcp, "SABOR_NUTRITION_DB", self.db_path), \
                 patch.object(sabor_mcp.requests, "get", side_effect=self.cookbook_responses(ordered)):
                results.append(sabor_mcp.nutrition_cookbook_readiness(limit=2, detail_limit=1))
        self.assertEqual(results[0]["details"]["fingerprint"], results[1]["details"]["fingerprint"])
        self.assertEqual(results[0]["details"]["runtimeProof"], {"providerCalls": 0, "writes": 0})

    def test_53_single_recipe_issues_are_bounded_with_full_summary_counts(self):
        items = [ingredient(id=f"issue-{index}") for index in range(1000)]
        result, _ = self.single(recipe("many-issues", *items))
        assessment = result["details"]["assessment"]
        self.assertEqual(assessment["issueCount"], 1000)
        self.assertEqual(assessment["omittedIssueCount"], 950)
        self.assertEqual(len(assessment["issues"]), 50)
        self.assertEqual([item["position"] for item in assessment["issues"]], list(range(50)))
        self.assertEqual(assessment["rootCounts"]["missing_quantity"], 1000)
        self.assertLess(len(json.dumps(result)), 50_000)

    def test_54_cookbook_cards_have_a_smaller_deterministic_issue_cap(self):
        items = [ingredient(id=f"issue-{index}") for index in range(1000)]
        value = recipe("many-issues", *items)
        with patch.object(sabor_mcp, "SABOR_NUTRITION_DB", self.db_path), \
             patch.object(sabor_mcp.requests, "get", side_effect=self.cookbook_responses([value])):
            result = sabor_mcp.nutrition_cookbook_readiness(limit=1, detail_limit=1)
        card = result["details"]["recipes"][0]
        self.assertEqual(card["issueCount"], 1000)
        self.assertEqual(card["omittedIssueCount"], 990)
        self.assertEqual(len(card["issues"]), 10)
        self.assertEqual([item["position"] for item in card["issues"]], list(range(10)))
        self.assertEqual(card["rootCounts"]["missing_quantity"], 1000)
        self.assertEqual(result["details"]["rootCounts"]["missing_quantity"], 1000)
        self.assertNotIn("originalText", repr(card))
        self.assertLess(len(json.dumps(result)), 20_000)

    def test_55_single_output_is_byte_bounded_with_adversarial_identifiers(self):
        huge = "á" * 10_000
        value = recipe(huge, *[
            ingredient(id=f"{index}-{huge}", text=huge) for index in range(1000)
        ])
        value["id"] = huge
        value["name"] = huge
        result, _ = self.single(value)
        payload = json.dumps(result, ensure_ascii=True, separators=(",", ":")).encode()
        self.assertLessEqual(len(payload), 65_536)
        self.assertLessEqual(len(result["details"]["recipe"]["id"]), 128)
        self.assertLessEqual(len(result["details"]["recipe"]["slug"]), 200)
        self.assertLessEqual(len(result["details"]["recipe"]["name"]), 300)
        issue = result["details"]["assessment"]["issues"][0]
        self.assertLessEqual(len(issue["ingredientId"]), 128)
        self.assertTrue(issue["ingredientId"].endswith("~"))

    def test_56_cookbook_output_is_byte_bounded_without_fingerprint_collisions(self):
        huge = "á" * 10_000
        values = []
        for index in range(25):
            value = recipe(f"{index}-{huge}", *[
                ingredient(id=f"{issue}-{huge}", text=huge) for issue in range(10)
            ])
            value["id"] = f"{index}-{huge}"
            value["name"] = huge
            values.append(value)
        with patch.object(sabor_mcp, "SABOR_NUTRITION_DB", self.db_path), \
             patch.object(sabor_mcp.requests, "get",
                          side_effect=self.cookbook_responses(values)):
            result = sabor_mcp.nutrition_cookbook_readiness(limit=25, detail_limit=25)
        payload = json.dumps(result, ensure_ascii=True, separators=(",", ":")).encode()
        self.assertLessEqual(len(payload), 131_072)
        self.assertEqual(result["details"]["assessedCount"], 25)
        self.assertEqual(result["details"]["rootCounts"]["missing_quantity"], 250)
        self.assertEqual(len({card["fingerprint"] for card in result["details"]["recipes"]}), 25)

    def test_57_output_string_caps_cover_message_action_and_identifier_fields(self):
        huge = "x" * 10_000
        bounded = sabor_mcp._nutrition_bound_readiness_output({
            "id": huge, "slug": huge, "name": huge, "ingredientId": huge,
            "message": huge, "action": huge, "suggestion": huge, "cookbook": huge,
        })
        expected = {
            "id": 128, "slug": 200, "name": 300, "ingredientId": 128,
            "message": 300, "action": 300, "suggestion": 300, "cookbook": 300,
        }
        for field, cap in expected.items():
            with self.subTest(field=field):
                self.assertLessEqual(len(json.dumps(bounded[field])[1:-1].encode()), cap)
                self.assertTrue(bounded[field].endswith("~"))

    def test_low_confidence_warning_output_preserves_recipe_and_card_bounds(self):
        items = [
            ingredient(1, "tbsp", "food", "1 tbsp food", id=f"warning-{index}")
            for index in range(1000)
        ]
        value = recipe("many-warnings", *items)
        evidence = ReadinessEvidence(
            status="available",
            foods=(FoodEvidence("food", conversion_status="low_confidence"),),
        )
        with patch("nutrition.readiness.read_readiness_evidence", return_value=evidence):
            result, _ = self.single(value)
        assessment = result["details"]["assessment"]
        self.assertEqual(assessment["warningCount"], 1000)
        self.assertEqual(assessment["omittedWarningCount"], 950)
        self.assertEqual(len(assessment["warnings"]), 50)
        self.assertLessEqual(len(json.dumps(result).encode()), 65_536)

        with patch("nutrition.readiness.read_readiness_evidence", return_value=evidence), \
             patch.object(sabor_mcp, "SABOR_NUTRITION_DB", self.db_path), \
             patch.object(sabor_mcp.requests, "get",
                          side_effect=self.cookbook_responses([value])):
            cookbook = sabor_mcp.nutrition_cookbook_readiness(limit=1, detail_limit=1)
        card = cookbook["details"]["recipes"][0]
        self.assertEqual(card["warningCount"], 1000)
        self.assertEqual(card["omittedWarningCount"], 990)
        self.assertEqual(len(card["warnings"]), 10)
        self.assertLessEqual(len(json.dumps(cookbook).encode()), 131_072)


if __name__ == "__main__":
    unittest.main()
