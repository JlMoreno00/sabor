import copy
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from tests.support import install_runtime_dependency_stubs

install_runtime_dependency_stubs()

import sabor_mcp
from nutrition.models import NutritionPolicy, RecipeCalculation, nutrition_eligibility


FOOD_ID = "11111111-1111-4111-8111-111111111111"
UNIT_ID = "22222222-2222-4222-8222-222222222222"
INGREDIENT_ID = "33333333-3333-4333-8333-333333333333"


def response(body, status=200):
    value = Mock(status_code=status, headers={}, text="redacted")
    value.json.return_value = body
    return value


def recipe(updated_at="2026-08-11T12:00:00Z", **changes):
    value = {
        "id": "recipe-id", "slug": "soup", "updatedAt": updated_at,
        "recipeServings": 2, "nutrition": {}, "settings": {},
        "recipeIngredient": [{
            "id": INGREDIENT_ID, "quantity": 100,
            "food": {"id": FOOD_ID, "name": "Tomato"},
            "unit": {"id": UNIT_ID, "name": "gram", "abbreviation": "g"},
            "originalText": "100 g tomato", "display": "100 g tomato",
            "optional": False, "included": True,
        }],
        "recipeInstructions": [{"id": "step-id", "text": "Cook"}],
    }
    value.update(changes)
    return value


def calculation(status="complete", coverage=1.0, confidence=.95, servings=2):
    return RecipeCalculation(
        id="calculation-id", recipe_id="recipe-id", slug="soup",
        input_hash="input", profile_version="profile", servings=servings,
        status=status, coverage_ratio=coverage, confidence=confidence,
        totals={"calories_kcal": 100}, per_serving={"calories_kcal": 50},
        mass_coverage_ratio=coverage, ingredient_coverage_ratio=coverage,
    )


def execute(continuation):
    return getattr(sabor_mcp, continuation["tool"])(**continuation["arguments"])


class EligibilityTests(unittest.TestCase):
    def test_central_gate_requires_complete_finite_threshold_and_servings(self):
        policy = NutritionPolicy(min_publish_coverage=.9, min_publish_confidence=.8)
        self.assertTrue(nutrition_eligibility(calculation(), policy)["eligible"])
        for candidate in (
            calculation(status="estimated"), calculation(coverage=.89),
            calculation(confidence=.79), calculation(servings=0),
        ):
            self.assertFalse(nutrition_eligibility(candidate, policy)["eligible"])
        invalid = calculation()
        invalid.per_serving["calories_kcal"] = float("nan")
        self.assertFalse(nutrition_eligibility(invalid, policy)["eligible"])

    def test_estimates_are_not_auto_publishable_or_schedulable(self):
        result = nutrition_eligibility(
            calculation(status="estimated"), NutritionPolicy(publish_estimates=False),
        )
        self.assertEqual(result["code"], "nutrition_estimate_not_allowed")
        self.assertTrue(nutrition_eligibility(
            calculation(status="estimated"), NutritionPolicy(publish_estimates=True),
        )["eligible"])


class JitNutritionContractTests(unittest.TestCase):
    def setUp(self):
        sabor_mcp._NUTRITION_CORRECTION_STAGES.clear()
        self.flags = [
            patch.object(sabor_mcp, "MEALIE_API_TOKEN", "test-token"),
            patch.object(sabor_mcp, "SABOR_NUTRITION_ENABLED", True),
            patch.object(sabor_mcp, "SABOR_NUTRITION_AUTO_ON_IMPORT", True),
            patch.object(sabor_mcp, "SABOR_NUTRITION_AUTO_PUBLISH", True),
        ]
        for item in self.flags:
            item.start()
            self.addCleanup(item.stop)

    def test_structured_ai_import_success_and_publish(self):
        ensured = {"ok": True, "code": "nutrition_ready", "stage": "complete"}
        with patch.object(sabor_mcp, "ensure_recipe_nutrition", return_value=ensured):
            result = sabor_mcp._nutrition_after_import({"ok": True, "slug": "soup"}, "soup")
        self.assertTrue(result["ok"])
        self.assertEqual(result["nutrition"], ensured)
        self.assertNotIn("nutrition_warning", result)

    def test_parser_loss_correction_loop_continuation_is_runnable(self):
        with patch.object(sabor_mcp, "_nutrition_fetch_recipe", return_value=recipe()), \
             patch.object(sabor_mcp, "_nutrition_recipe_revision", return_value={"fingerprint": "f", "updatedAt": "u"}), \
             patch.object(sabor_mcp, "_nutrition_readiness_assessment", return_value={
                 "gates": {"sourceStructure": False}, "issues": [{"root": "parser_loss"}],
             }):
            result = sabor_mcp.ensure_recipe_nutrition("soup", "import")
        continuation = result["actions"][0]["continuation"]
        with patch.object(sabor_mcp, "_nutrition_fetch_recipe", return_value=recipe()), \
             patch.object(sabor_mcp, "_nutrition_readiness_assessment", return_value={"ready": True, "gates": {}}):
            preview = execute(continuation)
        self.assertEqual(preview["code"], "nutrition_readiness_assessed")

    def test_ambiguous_food_emits_executable_correction_action(self):
        assessment = {
            "gates": {"sourceStructure": False},
            "issues": [{"root": "food_selection_required", "ingredientId": INGREDIENT_ID}],
        }
        with patch.object(sabor_mcp, "_nutrition_fetch_recipe", return_value=recipe()), \
             patch.object(sabor_mcp, "_nutrition_readiness_assessment", return_value=assessment):
            result = sabor_mcp.ensure_recipe_nutrition("soup", {"tool": "set_mealplan_entry", "arguments": {}})
        self.assertEqual(result["actions"][0]["kind"], "correct_recipe")
        self.assertEqual(result["actions"][0]["allowedOperations"], ["select_ingredient_alternative", "set_ingredient_food"])

    def test_provider_ambiguity_emits_executable_evidence_action(self):
        assessment = {
            "issues": [{"root": "profile_provider_evidence", "ingredientId": INGREDIENT_ID}],
        }
        action = sabor_mcp._nutrition_correction_action(
            recipe(), assessment, {"fingerprint": "f", "updatedAt": "u"},
        )
        service = Mock()
        service.db.get_resolution.return_value = {"resolution_status": "ambiguous"}
        service.db.get_profile.return_value = None
        service.db.list_conversions.return_value = []
        with patch.object(sabor_mcp, "_nutrition_service", return_value=service):
            result = execute(action["continuation"])
        self.assertEqual(action["kind"], "review_food_evidence")
        self.assertEqual(result["code"], "nutrition_food_profile")

    def test_provider_unavailable_import_vs_weekly(self):
        failure = {"ok": False, "code": "nutrition_provider_unavailable", "stage": "provider",
                   "retryable": True, "actions": []}
        with patch.object(sabor_mcp, "ensure_recipe_nutrition", return_value=failure):
            imported = sabor_mcp._nutrition_after_import({"ok": True}, "soup")
            weekly = sabor_mcp.set_mealplan_entry("2026-08-12", "dinner", "Soup", "soup")
        self.assertTrue(imported["ok"])
        self.assertEqual(imported["nutrition_warning"]["code"], "nutrition_provider_unavailable")
        self.assertFalse(weekly["scheduled"])

    def test_weekly_stays_blocked_until_a_fresh_readiness_pass(self):
        blocked = {"ok": False, "code": "nutrition_recipe_correction_required",
                   "stage": "readiness", "actions": []}
        ready = {"ok": True, "code": "nutrition_ready",
                 "revision": sabor_mcp._nutrition_recipe_revision(recipe())}
        with patch.object(sabor_mcp, "ensure_recipe_nutrition",
                          side_effect=[blocked, ready]) as ensure, \
             patch.object(sabor_mcp, "_nutrition_fetch_recipe", return_value=recipe()), \
             patch.object(sabor_mcp.requests, "get", return_value=response({"items": []})), \
             patch.object(sabor_mcp.requests, "post", return_value=response({"id": "entry"}, 201)) as post:
            first = sabor_mcp.set_mealplan_entry("2026-08-12", "dinner", "Soup", "soup")
            second = sabor_mcp.set_mealplan_entry("2026-08-12", "dinner", "Soup", "soup")

        self.assertFalse(first["scheduled"])
        self.assertTrue(second["scheduled"])
        self.assertEqual(ensure.call_count, 2)
        post.assert_called_once()

    def test_weekly_retry_cached_and_schedules_once(self):
        ready = {"ok": True, "code": "nutrition_ready", "revision": {"fingerprint": "f", "updatedAt": "u"}}
        existing = response({"items": []})
        created = response({"id": "entry-id"}, 201)
        with patch.object(sabor_mcp, "ensure_recipe_nutrition", return_value=ready), \
             patch.object(sabor_mcp, "_nutrition_fetch_recipe", return_value=recipe()), \
             patch.object(sabor_mcp, "_nutrition_recipe_revision", return_value=ready["revision"]), \
             patch.object(sabor_mcp.requests, "get", side_effect=[existing, response({"items": [{
                 "id": "entry-id", "date": "2026-08-12", "entryType": "dinner",
                 "recipe": {"id": "recipe-id"},
             }]})]), patch.object(sabor_mcp.requests, "post", return_value=created) as post:
            first = sabor_mcp.set_mealplan_entry("2026-08-12", "dinner", "Soup", "soup")
            second = sabor_mcp.set_mealplan_entry("2026-08-12", "dinner", "Soup", "soup")
        self.assertTrue(first["scheduled"])
        self.assertEqual(second["code"], "mealplan_entry_existing")
        post.assert_called_once()

    def test_import_success_across_all_nutrition_failures(self):
        for code in ("nutrition_recipe_correction_required", "nutrition_provider_unavailable",
                     "nutrition_provider_rate_limited", "nutrition_provider_budget_exceeded",
                     "nutrition_revision_conflict", "nutrition_insufficient_coverage"):
            with self.subTest(code=code), patch.object(
                sabor_mcp, "ensure_recipe_nutrition", return_value={"ok": False, "code": code, "actions": []},
            ):
                result = sabor_mcp._nutrition_after_import({"ok": True}, "soup")
                self.assertTrue(result["ok"])
                self.assertEqual(result["nutrition_warning"]["code"], code)

    def test_protected_schedule_without_nutrition_patch(self):
        ready = {"ok": True, "code": "nutrition_ready", "revision": {"fingerprint": "f", "updatedAt": "u"}}
        protected = recipe(settings={"saborNutritionProtected": True})
        with patch.object(sabor_mcp, "ensure_recipe_nutrition", return_value=ready), \
             patch.object(sabor_mcp, "_nutrition_fetch_recipe", return_value=protected), \
             patch.object(sabor_mcp, "_nutrition_recipe_revision", return_value=ready["revision"]), \
             patch.object(sabor_mcp.requests, "get", return_value=response({"items": []})), \
             patch.object(sabor_mcp.requests, "patch") as patch_request, \
             patch.object(sabor_mcp.requests, "post", return_value=response({"id": "entry"}, 201)):
            result = sabor_mcp.set_mealplan_entry("2026-08-12", "dinner", "Soup", "soup")
        self.assertTrue(result["scheduled"])
        patch_request.assert_not_called()

    def test_duplicate_schedule_is_idempotent(self):
        existing = {"id": "entry", "date": "2026-08-12", "entryType": "dinner",
                    "recipe": {"id": "recipe-id"}}
        ready = {"ok": True, "revision": {"fingerprint": "f", "updatedAt": "u"}}
        with patch.object(sabor_mcp, "ensure_recipe_nutrition", return_value=ready), \
             patch.object(sabor_mcp, "_nutrition_fetch_recipe", return_value=recipe()), \
             patch.object(sabor_mcp, "_nutrition_recipe_revision", return_value=ready["revision"]), \
             patch.object(sabor_mcp.requests, "get", return_value=response({"items": [existing]})), \
             patch.object(sabor_mcp.requests, "post") as post:
            result = sabor_mcp.set_mealplan_entry("2026-08-12", "dinner", "Soup", "soup")
        self.assertEqual(result["code"], "mealplan_entry_existing")
        post.assert_not_called()

    def test_recipe_level_correction_applies_when_ingredient_ids_are_missing(self):
        before = recipe(recipeServings=None)
        before["recipeIngredient"][0]["id"] = None
        after = copy.deepcopy(before)
        after["recipeServings"] = 4
        after["updatedAt"] = "after"
        revision = sabor_mcp._nutrition_recipe_revision(before)
        with patch.object(sabor_mcp, "_nutrition_fetch_recipe", side_effect=[before, before, after]), \
             patch.object(sabor_mcp.requests, "patch", return_value=response({}, 200)) as patch_request:
            result = sabor_mcp.nutrition_correct_recipe(
                "soup", revision,
                [{"operation": "set_recipe_servings", "value": 4}], apply=True,
            )
        self.assertTrue(result["ok"])
        self.assertEqual(result["code"], "nutrition_recipe_corrected")
        patch_request.assert_called_once_with(
            f"{sabor_mcp.MEALIE_BASE_URL}/api/recipes/soup",
            headers=sabor_mcp._mealie_headers(), json={"recipeServings": 4}, timeout=30,
        )

    def test_null_id_ingredient_can_be_corrected_by_bound_position_and_original_text(self):
        before = recipe()
        before["recipeIngredient"][0].update({"id": None, "originalText": "2 tomatoes", "quantity": 0})
        after = copy.deepcopy(before)
        after["recipeIngredient"][0]["quantity"] = 2
        after["updatedAt"] = "after"
        revision = sabor_mcp._nutrition_recipe_revision(before)
        correction = [{
            "operation": "set_ingredient_quantity", "ingredient_index": 0,
            "original_text": "2 tomatoes", "value": 2,
        }]
        with patch.object(sabor_mcp, "_nutrition_fetch_recipe", side_effect=[before, before, after]), \
             patch.object(sabor_mcp.requests, "patch", return_value=response({}, 200)) as patch_request:
            result = sabor_mcp.nutrition_correct_recipe("soup", revision, correction, apply=True)
        self.assertTrue(result["ok"])
        self.assertEqual(result["details"]["changedPaths"], ["/recipeIngredient/0/quantity"])
        self.assertEqual(patch_request.call_args.kwargs["json"], {
            "recipeIngredient": after["recipeIngredient"],
        })

    def test_postimage_mismatch_rolls_back_and_fails_closed(self):
        before = recipe(recipeServings=2)
        bad_postimage = copy.deepcopy(before)
        bad_postimage.update({"recipeServings": 4, "name": "unrelated change", "updatedAt": "after"})
        restored = copy.deepcopy(before)
        restored["updatedAt"] = "rollback"
        revision = sabor_mcp._nutrition_recipe_revision(before)
        with patch.object(sabor_mcp, "_nutrition_fetch_recipe",
                          side_effect=[before, before, bad_postimage, restored]), \
             patch.object(sabor_mcp.requests, "patch",
                          side_effect=[response({}, 200), response({}, 200)]) as patch_request:
            result = sabor_mcp.nutrition_correct_recipe(
                "soup", revision,
                [{"operation": "set_recipe_servings", "value": 4}], apply=True,
            )
        self.assertEqual(result["code"], "nutrition_correction_postimage_mismatch")
        self.assertTrue(result["details"]["rollbackVerified"])
        self.assertEqual(patch_request.call_count, 2)
        self.assertEqual(patch_request.call_args_list[1].kwargs["json"], {"recipeServings": 2})

    def test_patch_transport_failure_is_bounded_and_makes_no_rollback_guess(self):
        before = recipe(recipeServings=None)
        revision = sabor_mcp._nutrition_recipe_revision(before)
        correction = [{"operation": "set_recipe_servings", "value": 3}]
        with patch.object(sabor_mcp, "_nutrition_fetch_recipe",
                          side_effect=[before, before]), \
             patch.object(sabor_mcp.requests, "patch",
                          side_effect=sabor_mcp.requests.Timeout) as patch_request:
            result = sabor_mcp.nutrition_correct_recipe(
                "soup", revision, correction, apply=True,
            )
        self.assertEqual(result["code"], "nutrition_correction_patch_failed")
        self.assertEqual(result["details"]["changedPaths"], ["/recipeServings"])
        self.assertEqual(patch_request.call_count, 1)

    def test_arbitrary_patch_and_unbound_position_are_rejected(self):
        expected = {"fingerprint": "f", "updatedAt": "u"}
        forbidden = (
            {"operation": "patch", "path": "/name", "value": "x"},
            {"operation": "set_name", "value": "x"},
            {"operation": "set_ingredient_quantity", "ingredient_index": 0, "value": 1},
            {"operation": "clear_ingredient_unit", "ingredient_id": INGREDIENT_ID,
             "value": "ignored content"},
        )
        with patch.object(sabor_mcp, "_nutrition_fetch_recipe", return_value=recipe()), \
             patch.object(sabor_mcp, "_nutrition_recipe_revision", return_value=expected):
            for correction in forbidden:
                result = sabor_mcp.nutrition_correct_recipe("soup", expected, [correction])
                self.assertEqual(result["code"], "nutrition_correction_invalid")

    def test_revision_conflict_has_exact_continuation(self):
        expected = {"fingerprint": "old", "updatedAt": "old"}
        current = {"fingerprint": "new", "updatedAt": "new"}
        arguments = {"slug": "soup", "expected_revision": expected,
                     "corrections": [{"operation": "set_recipe_servings", "value": 4}], "apply": True}
        with patch.object(sabor_mcp, "_nutrition_fetch_recipe", return_value=recipe()), \
             patch.object(sabor_mcp, "_nutrition_recipe_revision", return_value=current):
            result = sabor_mcp.nutrition_correct_recipe(**arguments)
        self.assertEqual(result["code"], "nutrition_recipe_revision_conflict")
        self.assertEqual(result["details"]["expectedRevision"], expected)
        self.assertEqual(result["details"]["currentRevision"], current)
        self.assertEqual(result["stage"], "revision")

    def test_authoritative_food_and_unit_revalidation(self):
        current = recipe()
        revision = sabor_mcp._nutrition_recipe_revision(current)
        corrections = [
            {"operation": "set_ingredient_food", "ingredient_id": INGREDIENT_ID, "food_id": FOOD_ID},
            {"operation": "set_ingredient_unit", "ingredient_id": INGREDIENT_ID, "unit_id": UNIT_ID},
        ]
        with patch.object(sabor_mcp, "_nutrition_fetch_recipe", return_value=current), \
             patch.object(sabor_mcp.requests, "get", side_effect=[
                 response({"id": FOOD_ID, "name": "Tomato"}),
                 response({"id": UNIT_ID, "name": "gram", "abbreviation": "g"}),
             ]) as get, patch.object(sabor_mcp, "_nutrition_readiness_assessment", return_value={"ready": True}):
            result = sabor_mcp.nutrition_correct_recipe("soup", revision, corrections)
        self.assertTrue(result["ok"])
        self.assertEqual(get.call_count, 2)
        with patch.object(sabor_mcp, "_nutrition_fetch_recipe", return_value=current), \
             patch.object(sabor_mcp.requests, "get", side_effect=[
                  response({"id": FOOD_ID, "name": "Tomato"}),
                  response({"id": UNIT_ID, "name": "gram", "abbreviation": "g"}),
              ]), patch.object(sabor_mcp.requests, "patch", return_value=response({}, 200)) as apply_patch:
            applied = execute(result["continuation"])
        self.assertEqual(applied["code"], "nutrition_recipe_corrected")
        self.assertEqual(applied["details"]["runtimeProof"], {"writes": 1, "providerCalls": 0})
        apply_patch.assert_called_once()

    def test_apply_refetches_after_authoritative_reads_and_stays_get_only_on_drift(self):
        before = recipe()
        drifted = recipe(updated_at="later", recipeServings=7)
        revision = sabor_mcp._nutrition_recipe_revision(before)
        events = []

        def fetch(_slug):
            events.append("recipe_get")
            return before if events.count("recipe_get") == 1 else drifted

        def authoritative(*_args, **_kwargs):
            events.append("food_get")
            return response({"id": FOOD_ID, "name": "Tomato"})

        with patch.object(sabor_mcp, "_nutrition_fetch_recipe", side_effect=fetch), \
             patch.object(sabor_mcp.requests, "get", side_effect=authoritative), \
             patch.object(sabor_mcp.requests, "patch") as patch_request:
            result = sabor_mcp.nutrition_correct_recipe(
                "soup", revision,
                [{"operation": "set_ingredient_food", "ingredient_id": INGREDIENT_ID,
                  "food_id": FOOD_ID}], apply=True,
            )
        self.assertEqual(events, ["recipe_get", "food_get", "recipe_get"])
        self.assertEqual(result["code"], "nutrition_recipe_revision_conflict")
        self.assertEqual(result["details"]["expectedRevision"], revision)
        self.assertEqual(result["details"]["currentRevision"],
                         sabor_mcp._nutrition_recipe_revision(drifted))
        patch_request.assert_not_called()

    def test_alternative_selection_proposes_selected_alternative_and_food(self):
        before = recipe()
        before["recipeIngredient"][0]["alternatives"] = [{
            "id": "pepper", "food": {"id": FOOD_ID, "name": "Pepper"},
        }]
        after = copy.deepcopy(before)
        after["recipeIngredient"][0]["selectedAlternative"] = "pepper"
        after["recipeIngredient"][0]["food"] = {"id": FOOD_ID, "name": "Pepper"}
        revision = sabor_mcp._nutrition_recipe_revision(before)
        with patch.object(sabor_mcp, "_nutrition_fetch_recipe", side_effect=[before, before, after]), \
             patch.object(sabor_mcp.requests, "patch", return_value=response({}, 200)) as patch_request:
            result = sabor_mcp.nutrition_correct_recipe(
                "soup", revision,
                [{"operation": "select_ingredient_alternative",
                  "ingredient_id": INGREDIENT_ID, "alternative_id": "pepper"}], apply=True,
            )
        expected = [
            f"/recipeIngredient/{INGREDIENT_ID}/selectedAlternative",
            f"/recipeIngredient/{INGREDIENT_ID}/food",
        ]
        self.assertEqual(result["code"], "nutrition_recipe_corrected")
        self.assertEqual(result["details"]["changedPaths"], expected)
        patch_request.assert_called_once()

    def test_path_like_and_noncanonical_uuid_references_never_reach_http(self):
        before = recipe()
        revision = sabor_mcp._nutrition_recipe_revision(before)
        cases = [
            {"operation": "set_ingredient_food", "ingredient_id": INGREDIENT_ID,
             "food_id": "../foods/anything"},
            {"operation": "set_ingredient_unit", "ingredient_id": INGREDIENT_ID,
             "unit_id": "AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA"},
            {"operation": "set_ingredient_quantity", "ingredient_id": "../ingredient", "value": 1},
        ]
        with patch.object(sabor_mcp, "_nutrition_fetch_recipe", return_value=before), \
             patch.object(sabor_mcp.requests, "get") as get:
            for correction in cases:
                with self.subTest(correction=correction):
                    result = sabor_mcp.nutrition_correct_recipe(
                        "soup", revision, [correction], apply=False,
                    )
                    self.assertEqual(result["code"], "nutrition_correction_invalid")
        get.assert_not_called()

    def test_apply_remains_available_when_master_flag_is_disabled(self):
        before = recipe()
        after = copy.deepcopy(before)
        after["recipeServings"] = 3
        revision = sabor_mcp._nutrition_recipe_revision(before)
        correction = [{"operation": "set_recipe_servings", "value": 3}]
        with patch.object(sabor_mcp, "SABOR_NUTRITION_ENABLED", False), \
             patch.object(sabor_mcp, "_nutrition_fetch_recipe",
                          side_effect=[before, before, before, after]), \
             patch.object(sabor_mcp, "_nutrition_readiness_assessment", return_value={"ready": True}), \
             patch.object(sabor_mcp.requests, "patch", return_value=response({}, 200)) as patch_request:
            preview = sabor_mcp.nutrition_correct_recipe("soup", revision, correction)
            applied = sabor_mcp.nutrition_correct_recipe("soup", revision, correction, apply=True)
        self.assertTrue(preview["ok"])
        self.assertEqual(applied["code"], "nutrition_recipe_corrected")
        patch_request.assert_called_once()

    def test_standalone_apply_patches_exact_field_then_emits_runnable_readiness(self):
        before = recipe(recipeServings=None)
        before["dateUpdated"] = "before"
        after = copy.deepcopy(before)
        after["recipeServings"] = 3
        after["dateUpdated"] = "after"
        after["recipeInstructions"][0]["id"] = "server-regenerated-step-id"
        revision = sabor_mcp._nutrition_recipe_revision(before)
        with patch.object(sabor_mcp, "_nutrition_fetch_recipe",
                          side_effect=[before, before, after]), \
             patch.object(sabor_mcp, "_nutrition_readiness_assessment", return_value={"ready": True}), \
             patch.object(sabor_mcp.requests, "patch", return_value=response({}, 200)) as patch_request:
            result = sabor_mcp.nutrition_correct_recipe(
                "soup", revision, [{"operation": "set_recipe_servings", "value": 3}],
                apply=True,
            )

        readiness = {
            "tool": "nutrition_recipe_readiness",
            "arguments": {"slug": "soup", "include_suggestions": True},
        }
        self.assertEqual(result["code"], "nutrition_recipe_corrected")
        self.assertEqual(result["continuation"], readiness)
        self.assertEqual(result["continuations"], {"readiness": readiness})
        self.assertEqual(result["details"]["runtimeProof"]["writes"], 1)
        patch_request.assert_called_once_with(
            "http://localhost:9925/api/recipes/soup",
            headers={"Authorization": "Bearer test-token", "Content-Type": "application/json"},
            json={"recipeServings": 3}, timeout=30,
        )
        with patch.object(sabor_mcp, "nutrition_recipe_readiness",
                          return_value={"ok": True}) as readiness_tool:
            execute(result["continuation"])
        readiness_tool.assert_called_once_with(slug="soup", include_suggestions=True)

    def test_blocked_weekly_apply_adds_runnable_schedule_after_readiness(self):
        before = recipe(recipeServings=None)
        revision = sabor_mcp._nutrition_recipe_revision(before)
        schedule = {"tool": "set_mealplan_entry", "arguments": {
            "date_str": "2026-08-12", "entry_type": "dinner", "title": "Soup",
            "recipe_slug": "soup",
        }}
        assessment = {"gates": {"sourceStructure": False},
                      "issues": [{"root": "missing_servings"}]}
        with patch.object(sabor_mcp, "_nutrition_fetch_recipe", return_value=before), \
             patch.object(sabor_mcp, "_nutrition_readiness_assessment", return_value=assessment):
            blocked = sabor_mcp.ensure_recipe_nutrition("soup", schedule)
        self.assertEqual(blocked["continuation"], schedule)

        after = copy.deepcopy(before)
        after["recipeServings"] = 3
        with patch.object(sabor_mcp, "_nutrition_fetch_recipe",
                          side_effect=[before, before, after]), \
             patch.object(sabor_mcp.requests, "patch", return_value=response({}, 200)) as patch_request, \
             patch.object(sabor_mcp.requests, "post") as post_request:
            result = sabor_mcp.nutrition_correct_recipe(
                "soup", revision, [{"operation": "set_recipe_servings", "value": 3}],
                apply=True,
            )
        self.assertEqual(result["details"]["correctionProposal"]["corrections"],
                         [{"operation": "set_recipe_servings", "value": 3}])
        self.assertEqual(result["code"], "nutrition_recipe_corrected")
        self.assertEqual(result["continuations"]["schedule"], schedule)
        with patch.object(sabor_mcp, "nutrition_recipe_readiness",
                          return_value={"ok": True}) as readiness:
            execute(result["continuations"]["readiness"])
        readiness.assert_called_once_with(slug="soup", include_suggestions=True)
        with patch.object(sabor_mcp, "set_mealplan_entry",
                          return_value={"scheduled": True}) as resumed:
            execute(result["continuations"]["schedule"])
        resumed.assert_called_once_with(**schedule["arguments"])
        patch_request.assert_called_once()
        post_request.assert_not_called()

    def test_preview_is_read_only_and_apply_uses_one_exact_patch(self):
        before = recipe()
        after = copy.deepcopy(before)
        revision = sabor_mcp._nutrition_recipe_revision(before)
        correction = [{"operation": "set_ingredient_food", "ingredient_id": INGREDIENT_ID,
                       "food_id": FOOD_ID}]
        after["recipeIngredient"][0]["food"] = {"id": FOOD_ID, "name": "Tomato"}
        for apply in (False, True):
            with self.subTest(apply=apply), \
                 patch.object(sabor_mcp, "_nutrition_fetch_recipe",
                              side_effect=([before, before, after] if apply else [before])), \
                 patch.object(sabor_mcp.requests, "get",
                              return_value=response({"id": FOOD_ID, "name": "Tomato"})) as get, \
                 patch.object(sabor_mcp.requests, "patch", return_value=response({}, 200)) as patch_request, \
                 patch.object(sabor_mcp.requests, "post") as post_request, \
                 patch.object(sabor_mcp.requests, "put") as put_request, \
                 patch.object(sabor_mcp.requests, "delete") as delete_request:
                result = sabor_mcp.nutrition_correct_recipe(
                    "soup", revision, correction, apply=apply,
                )
            self.assertEqual(get.call_count, 1)
            self.assertEqual(result["details"]["runtimeProof"]["writes"], int(apply))
            self.assertEqual(patch_request.call_count, int(apply))
            for mutation in (post_request, put_request, delete_request):
                mutation.assert_not_called()

    def test_unowned_nutrition_is_never_overwritten(self):
        current = recipe(nutrition={"calories": "999 kcal"})
        service = Mock()
        service.calculate.return_value = {"ok": True, "calculation": {
            "status": "complete", "coverage_ratio": 1, "confidence": 1,
            "servings": 2, "totals": {"calories_kcal": 100},
            "per_serving": {"calories_kcal": 50}, "mass_coverage_ratio": 1,
            "ingredient_coverage_ratio": 1,
        }}
        service.eligibility.return_value = {"eligible": True, "code": "nutrition_eligible"}
        with patch.object(sabor_mcp, "_nutrition_fetch_recipe", return_value=current), \
             patch.object(sabor_mcp, "_nutrition_readiness_assessment", return_value={
                 "gates": {"sourceStructure": True, "evidence": True}, "issues": [],
                 "publicationProvenance": "unknown",
             }), patch.object(sabor_mcp, "_nutrition_service", return_value=service), \
             patch.object(sabor_mcp.requests, "patch") as patch_request:
            result = sabor_mcp.ensure_recipe_nutrition("soup", {"tool": "set_mealplan_entry", "arguments": {}})
        self.assertTrue(result["ok"])
        patch_request.assert_not_called()

    def test_provider_budget_and_partial_cache(self):
        from nutrition.db import NutritionDB
        from nutrition.providers import ProviderBudget, SpoonacularProvider
        from nutrition.resolver import FoodResolver
        from nutrition.service import NutritionService

        with tempfile.TemporaryDirectory() as directory:
            db = NutritionDB(Path(directory) / "nutrition.db")
            session = Mock()
            session.get.side_effect = [
                response({"results": [{"id": 1, "name": "tomato"}]}),
                response({"name": "tomato", "nutrition": {
                    "weightPerServing": {"amount": 100, "unit": "g"},
                    "nutrients": [{"name": "Calories", "amount": 18, "unit": "kcal"},
                                  {"name": "Protein", "amount": .9, "unit": "g"}],
                }}),
            ]
            provider = SpoonacularProvider("test", db, session=session)
            service = NutritionService(db, resolver=FoodResolver(db, provider))
            value = recipe(recipeIngredient=[
                {"id": "tomato", "quantity": 100, "unit": {"name": "g"},
                 "food": {"id": "tomato-food", "name": "tomate"},
                 "originalText": "100 g tomato"},
                {"id": "egg", "quantity": 1, "unit": {"name": "piece"},
                 "food": {"id": "egg-food", "name": "huevo"}, "originalText": "1 egg"},
            ])
            with ProviderBudget(2) as budget:
                result = service.calculate(value)
            cached_profile = db.get_profile("tomato-food")
        self.assertEqual(budget.attempts, 2)
        self.assertEqual(result["code"], "nutrition_provider_budget_exceeded")
        self.assertIsNotNone(cached_profile)

    def test_rate_limit_envelope_is_identical_on_immediate_repeat(self):
        service = Mock()
        service.calculate.return_value = {
            "ok": False, "code": "nutrition_provider_rate_limited", "retryable": True,
            "details": {"retryAfter": 300},
        }
        trigger = {"tool": "nutrition_calculate_recipe",
                   "arguments": {"slug": "soup", "publish": False, "force": False}}
        assessment = {"gates": {"sourceStructure": True, "evidence": True}, "issues": []}
        with patch.object(sabor_mcp, "_nutrition_fetch_recipe", return_value=recipe()), \
             patch.object(sabor_mcp, "_nutrition_readiness_assessment", return_value=assessment), \
             patch.object(sabor_mcp, "_nutrition_service", return_value=service), \
             patch.object(sabor_mcp.requests, "get") as get:
            first = sabor_mcp.ensure_recipe_nutrition("soup", trigger)
            second = sabor_mcp.ensure_recipe_nutrition("soup", trigger)
        self.assertEqual(first["code"], second["code"])
        self.assertEqual(first["retryAfter"], 300)
        self.assertEqual(second["retryAfter"], 300)
        self.assertEqual(first["continuation"], trigger)
        self.assertEqual(second["continuation"], trigger)
        self.assertFalse(first["ok"])
        self.assertFalse(second["ok"])
        get.assert_not_called()

    def test_revision_change_before_schedule_blocks_post_and_continuation_runs(self):
        old = {"fingerprint": "old", "updatedAt": "old"}
        new = {"fingerprint": "new", "updatedAt": "new"}
        with patch.object(sabor_mcp, "ensure_recipe_nutrition", return_value={"ok": True, "revision": old}), \
             patch.object(sabor_mcp, "_nutrition_fetch_recipe", return_value=recipe()), \
             patch.object(sabor_mcp, "_nutrition_recipe_revision", return_value=new), \
             patch.object(sabor_mcp.requests, "post") as post:
            result = sabor_mcp.set_mealplan_entry("2026-08-12", "dinner", "Soup", "soup")
        self.assertFalse(result["scheduled"])
        post.assert_not_called()
        with patch.object(sabor_mcp, "set_mealplan_entry", return_value={"scheduled": True}) as retry:
            execute(result["continuation"])
        retry.assert_called_once()

    def test_duplicate_lookup_503_fails_closed_with_exact_retry(self):
        arguments = {"date_str": "2026-08-12", "entry_type": "dinner",
                     "title": "Soup", "recipe_slug": "soup"}
        revision = sabor_mcp._nutrition_recipe_revision(recipe())
        with patch.object(sabor_mcp, "ensure_recipe_nutrition",
                          return_value={"ok": True, "revision": revision}), \
             patch.object(sabor_mcp, "_nutrition_fetch_recipe", return_value=recipe()), \
             patch.object(sabor_mcp.requests, "get", return_value=response({}, 503)), \
             patch.object(sabor_mcp.requests, "post") as post:
            result = sabor_mcp.set_mealplan_entry(**arguments)
        self.assertEqual(result["code"], "nutrition_schedule_duplicate_lookup_failed")
        self.assertTrue(result["retryable"])
        self.assertFalse(result["scheduled"])
        self.assertEqual(result["continuation"]["arguments"], arguments)
        post.assert_not_called()

    def test_duplicate_lookup_timeout_fails_closed_without_post(self):
        revision = sabor_mcp._nutrition_recipe_revision(recipe())
        with patch.object(sabor_mcp, "ensure_recipe_nutrition",
                          return_value={"ok": True, "revision": revision}), \
             patch.object(sabor_mcp, "_nutrition_fetch_recipe", return_value=recipe()), \
             patch.object(sabor_mcp.requests, "get", side_effect=sabor_mcp.requests.Timeout), \
             patch.object(sabor_mcp.requests, "post") as post:
            result = sabor_mcp.set_mealplan_entry("2026-08-12", "dinner", "Soup", "soup")
        self.assertEqual(result["code"], "nutrition_schedule_duplicate_lookup_failed")
        post.assert_not_called()

    def test_duplicate_lookup_final_revision_and_post_share_schedule_lock(self):
        before = recipe()
        revision = sabor_mcp._nutrition_recipe_revision(before)
        drifted = recipe(updated_at="later")
        events = []

        class RecordingLock:
            def __enter__(self):
                events.append("lock_enter")

            def __exit__(self, *_args):
                events.append("lock_exit")

        def lookup(*_args, **_kwargs):
            events.append("duplicate_get")
            return response({"items": []})

        def final_fetch(_slug):
            events.append("recipe_get")
            return drifted

        with patch.object(sabor_mcp, "ensure_recipe_nutrition",
                          return_value={"ok": True, "revision": revision}), \
             patch.object(sabor_mcp, "_keyed_lock", return_value=RecordingLock()), \
             patch.object(sabor_mcp, "_nutrition_fetch_recipe", side_effect=final_fetch), \
             patch.object(sabor_mcp.requests, "get", side_effect=lookup), \
             patch.object(sabor_mcp.requests, "post") as post:
            result = sabor_mcp.set_mealplan_entry("2026-08-12", "dinner", "Soup", "soup")
        self.assertEqual(events, ["lock_enter", "duplicate_get", "recipe_get", "lock_exit"])
        self.assertEqual(result["code"], "nutrition_revision_conflict")
        post.assert_not_called()

    def test_striped_locks_are_bounded_and_serialize_same_key(self):
        locks = {sabor_mcp._keyed_lock(sabor_mcp._NUTRITION_RECIPE_LOCKS, str(index))
                 for index in range(10_000)}
        self.assertLessEqual(len(locks), 64)
        self.assertLessEqual(len(sabor_mcp._NUTRITION_RECIPE_LOCKS), 64)
        key_lock = sabor_mcp._keyed_lock(sabor_mcp._NUTRITION_RECIPE_LOCKS, "same")
        entered = threading.Event()
        released = threading.Event()

        def contender():
            with key_lock:
                entered.set()
            released.set()

        key_lock.acquire()
        thread = threading.Thread(target=contender)
        thread.start()
        time.sleep(.02)
        self.assertFalse(entered.is_set())
        key_lock.release()
        thread.join(timeout=1)
        self.assertTrue(released.is_set())

    def test_free_text_mealplan_compatibility(self):
        with patch.object(sabor_mcp, "ensure_recipe_nutrition") as ensure, \
             patch.object(sabor_mcp.requests, "post", return_value=response({"id": "entry"}, 201)) as post:
            result = sabor_mcp.set_mealplan_entry("2026-08-12", "dinner", "Takeout")
        self.assertTrue(result["ok"])
        ensure.assert_not_called()
        self.assertNotIn("recipeId", post.call_args.kwargs["json"])


if __name__ == "__main__":
    unittest.main()
