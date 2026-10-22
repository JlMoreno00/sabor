import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from nutrition.db import NutritionDB
from nutrition.models import SPOONACULAR_PROFILE_CONFIDENCE
from nutrition.publisher import MealiePublisher
from nutrition.resolver import FoodResolver
from nutrition.service import ENGINE_VERSION, NutritionService, recipe_input_hash, stable_hash


class NutritionServiceTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.db = NutritionDB(Path(self.tempdir.name) / "n.db")
        self.recipe = {"id": "r", "slug": "recipe", "recipeServings": 2,
            "recipeIngredient": [{"id": "i", "quantity": 100, "unit": {"name": "g"},
                "food": {"id": "f", "name": "tomate"}, "originalText": "100 g tomato"}]}
        self.resolver = Mock()
        self.resolver.resolve.return_value = Mock(status="resolved", confidence=.98)
        self.db.upsert_resolution("f", "tomate", "tomato", "resolved", .98, "alias")
        self.db.upsert_profile("f", {"calories_kcal": 18, "protein_g": .9}, "provider", .95, "h1")
        self.service = NutritionService(self.db, resolver=self.resolver)

    def test_hash_is_stable_and_unchanged_success_skips_recalculation(self):
        self.assertEqual(stable_hash({"b": 1, "a": 2}), stable_hash({"a": 2, "b": 1}))
        first = self.service.calculate(self.recipe)
        second = self.service.calculate(self.recipe)
        self.assertTrue(first["ok"])
        self.assertFalse(first["cached"])
        self.assertTrue(second["cached"])

    def test_protected_publication_never_records_sidecar_history(self):
        request = Mock(return_value=Mock(status_code=200, text=""))
        self.service.publisher = MealiePublisher("http://mealie", {}, request=request)
        protected_recipe = copy.deepcopy(self.recipe)
        protected_recipe["settings"] = {"saborNutritionProtected": True}

        result = self.service.publish(protected_recipe)

        self.assertEqual(result["code"], "nutrition_manual_protection")
        request.assert_not_called()
        calculation = self.service.calculate(protected_recipe)["calculation"]
        self.assertEqual(self.db.publish_history(calculation["id"]), [])

    def test_profile_change_and_force_invalidate_cache(self):
        first = self.service.calculate(self.recipe)
        self.db.upsert_profile("f", {"calories_kcal": 20}, "provider", .95, "h2", force=True)
        changed = self.service.calculate(self.recipe)
        forced = self.service.calculate(self.recipe, force=True)
        self.assertNotEqual(first["calculation"]["profile_version"], changed["calculation"]["profile_version"])
        self.assertFalse(forced["cached"])

    def test_manual_profile_semantics_invalidate_once_then_repeat_hits_cache(self):
        first = self.service.calculate(self.recipe)
        self.db.upsert_profile(
            "f", {"calories_kcal": 18, "protein_g": .9}, "manual", .9, "manual-v1",
            manual_override=True, source_version="reference-v1", source_note="Review one",
        )
        manual = self.service.calculate(self.recipe)
        repeated = self.service.calculate(self.recipe)
        self.db.upsert_profile(
            "f", {"calories_kcal": 18, "protein_g": .9}, "manual", .95, "manual-v2",
            manual_override=True, source_version="reference-v2", source_note="Review two",
        )
        changed = self.service.calculate(self.recipe)

        self.assertNotEqual(first["calculation"]["profile_version"],
                            manual["calculation"]["profile_version"])
        self.assertFalse(manual["cached"])
        self.assertTrue(repeated["cached"])
        self.assertEqual(repeated["calculation"]["id"], manual["calculation"]["id"])
        self.assertFalse(changed["cached"])
        self.assertNotEqual(manual["calculation"]["profile_version"],
                            changed["calculation"]["profile_version"])
        with self.db.connect() as connection:
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM recipe_calculation WHERE mealie_recipe_id='r'"
            ).fetchone()[0], 3)

    def test_mapping_confidence_refinement_reuses_profile_and_recalculates_once(self):
        db = NutritionDB(Path(self.tempdir.name) / "mapping-refinement.db")
        db.upsert_resolution("f", "champiñón", None, "blocked", 0, "untranslated")
        db.upsert_resolution(
            "f", "champiñón", "fresh mushrooms", "manual", .75, "manual", True,
            provider="spoonacular", provider_food_id="11260", source_note="First review",
        )
        db.upsert_profile(
            "f", {"calories_kcal": 22, "protein_g": 3.09}, "spoonacular",
            SPOONACULAR_PROFILE_CONFIDENCE, "mushroom-profile",
            provider_food_id="11260", stale_after="2099-01-01T00:00:00+00:00",
        )
        provider = Mock()
        service = NutritionService(db, resolver=FoodResolver(db, provider))

        old = service.calculate(self.recipe)
        db.upsert_resolution(
            "f", "champiñón", "fresh mushrooms", "manual", .85, "manual", True,
            provider="spoonacular", provider_food_id="11260", source_note="Refined review",
        )
        refined = service.calculate(self.recipe)
        with db.connect() as connection:
            refined_mapping_time = connection.execute(
                "SELECT mapping_updated_at FROM ingredient_calculation "
                "WHERE recipe_calculation_id=?", (refined["calculation"]["id"],),
            ).fetchone()[0]
        db.upsert_resolution(
            "f", "champiñón", "fresh mushrooms", "manual", .85, "manual", True,
            provider="spoonacular", provider_food_id="11260", source_note="Text-only edit",
        )
        repeated = service.calculate(self.recipe)
        with db.connect() as connection:
            repeated_mapping_time = connection.execute(
                "SELECT mapping_updated_at FROM ingredient_calculation "
                "WHERE recipe_calculation_id=?", (refined["calculation"]["id"],),
            ).fetchone()[0]

        self.assertAlmostEqual(old["calculation"]["confidence"], .75 * .97)
        self.assertAlmostEqual(refined["calculation"]["confidence"], .85 * .97)
        self.assertNotEqual(
            old["calculation"]["profile_version"],
            refined["calculation"]["profile_version"],
        )
        self.assertNotEqual(old["calculation"]["id"], refined["calculation"]["id"])
        self.assertFalse(refined["cached"])
        self.assertTrue(repeated["cached"])
        self.assertEqual(repeated["calculation"]["id"], refined["calculation"]["id"])
        self.assertEqual(repeated_mapping_time, refined_mapping_time)
        self.assertEqual(db.get_profile("f")["confidence"], .97)
        self.assertEqual(db.get_profile("f")["payload_hash"], "mushroom-profile")
        provider.fetch_profile.assert_not_called()
        provider.fetch_profile_by_id.assert_not_called()

    def test_pre_v4_calculation_is_recalculated_once_with_source_note(self):
        recipe = {"id": "egg-recipe", "slug": "egg", "recipeServings": 1,
            "recipeIngredient": [{"id": "egg-item", "quantity": 1,
                "unit": {"name": "piece"}, "food": {"id": "egg", "name": "egg"},
                "originalText": "1 egg"}]}
        self.db.upsert_resolution("egg", "egg", "egg", "resolved", .98, "alias")
        self.db.upsert_profile(
            "egg", {"calories_kcal": 143, "protein_g": 12.6}, "provider", .95, "egg-hash",
        )

        with patch("nutrition.service.ENGINE_VERSION", "1"):
            legacy = self.service.calculate(recipe)
        with self.db.connect() as connection:
            connection.execute(
                "UPDATE ingredient_calculation SET source_note=NULL "
                "WHERE recipe_calculation_id=?",
                (legacy["calculation"]["id"],),
            )

        upgraded = self.service.calculate(recipe)
        current = self.service.calculate(recipe)

        self.assertNotEqual(ENGINE_VERSION, "1")
        self.assertFalse(upgraded["cached"])
        self.assertEqual(upgraded["calculation"]["engine_version"], ENGINE_VERSION)
        self.assertTrue(current["cached"])
        self.assertEqual(current["calculation"]["id"], upgraded["calculation"]["id"])
        with self.db.connect() as connection:
            evidence = connection.execute(
                "SELECT source_note FROM ingredient_calculation "
                "WHERE recipe_calculation_id=?",
                (upgraded["calculation"]["id"],),
            ).fetchone()
        self.assertTrue(evidence[0])

    def test_hash_covers_all_conversion_affecting_ingredient_semantics(self):
        base = {
            "id": "r", "slug": "recipe", "recipeServings": 2,
            "recipeYield": "2 servings", "recipeIngredient": [{
                "id": "i", "quantity": 1,
                "unit": {"id": "u", "name": "gram", "abbreviation": "g"},
                "food": {"id": "f", "name": "tomato"},
                "originalText": "1 g tomato", "display": "1 g tomato",
                "optional": False, "included": True, "nonConsumed": False,
                "selectedAlternative": "tomato", "retainedFraction": 1,
            }],
        }
        mutations = {
            "nonConsumed": True,
            "selectedAlternative": "pepper",
            "retainedFraction": .5,
            "display": "discarded brine",
        }
        baseline = recipe_input_hash(base)
        for field, value in mutations.items():
            with self.subTest(field=field):
                changed = copy.deepcopy(base)
                changed["recipeIngredient"][0][field] = value
                self.assertNotEqual(baseline, recipe_input_hash(changed))
        changed = copy.deepcopy(base)
        changed["recipeIngredient"][0]["unit"]["abbreviation"] = "kg"
        self.assertNotEqual(baseline, recipe_input_hash(changed))
        for field, value in {
            "standardQuantity": 15, "standardUnit": "milliliter", "useAbbreviation": True,
        }.items():
            with self.subTest(unit_field=field):
                changed = copy.deepcopy(base)
                changed["recipeIngredient"][0]["unit"][field] = value
                self.assertNotEqual(baseline, recipe_input_hash(changed))

    def test_engine_version_invalidates_pilot_v2_calculations(self):
        self.assertNotEqual(ENGINE_VERSION, "2")

    def test_title_heading_classification_changes_v3_cache_identity_and_persists_evidence(self):
        fixture = json.loads(
            (Path(__file__).parent / "fixtures" / "mealie_ingredient_rows_en.json").read_text()
        )
        without_title = copy.deepcopy(fixture)
        without_title["recipeIngredient"][0].pop("title")

        legacy_shape = self.service.calculate(without_title)
        result = self.service.calculate(fixture)
        cached = self.service.calculate(fixture)

        self.assertEqual(legacy_shape["calculation"]["engine_version"], "3")
        self.assertNotEqual(
            legacy_shape["calculation"]["input_hash"], result["calculation"]["input_hash"]
        )
        self.assertFalse(result["cached"])
        self.assertTrue(cached["cached"])
        self.assertEqual(result["calculation"]["ingredient_count"], 2)
        self.assertEqual(result["calculation"]["ingredient_coverage_ratio"], .5)
        self.assertEqual(result["calculation"]["coverage_ratio"], .5)
        self.assertAlmostEqual(result["calculation"]["confidence"], .4655)
        with self.db.connect() as connection:
            rows = connection.execute(
                "SELECT position,original_text,status,reason FROM ingredient_calculation "
                "WHERE recipe_calculation_id=? ORDER BY position",
                (result["calculation"]["id"],),
            ).fetchall()
        self.assertEqual(
            [tuple(row) for row in rows],
            [
                (0, "Sauce", "excluded", "section_heading"),
                (1, "salt to taste", "unresolved", "invalid_quantity"),
                (2, "100 g tomato", "computed", None),
            ],
        )

    def test_title_text_and_whitespace_changes_reuse_cache_when_heading_classification_matches(self):
        fixture = json.loads(
            (Path(__file__).parent / "fixtures" / "mealie_ingredient_rows_en.json").read_text()
        )
        changed = copy.deepcopy(fixture)
        changed["recipeIngredient"][0]["title"] = "  Salsa section  "

        first = self.service.calculate(fixture)
        reused = self.service.calculate(changed)

        self.assertEqual(recipe_input_hash(fixture), recipe_input_hash(changed))
        self.assertFalse(first["cached"])
        self.assertTrue(reused["cached"])
        self.assertEqual(reused["calculation"]["id"], first["calculation"]["id"])

    def test_none_and_zero_title_heading_quantities_share_semantic_cache_identity(self):
        zero = json.loads(
            (Path(__file__).parent / "fixtures" / "mealie_ingredient_rows_en.json").read_text()
        )
        missing = copy.deepcopy(zero)
        missing["recipeIngredient"][0]["quantity"] = None
        positive = copy.deepcopy(zero)
        positive["recipeIngredient"][0]["quantity"] = 1.0

        self.assertEqual(recipe_input_hash(missing), recipe_input_hash(zero))
        self.assertNotEqual(recipe_input_hash(zero), recipe_input_hash(positive))

    def test_three_mealie_pilot_headings_are_excluded_in_persisted_evidence(self):
        fixture = json.loads(
            (Path(__file__).parent / "fixtures" / "mealie_pilot_zero_headings.json").read_text()
        )

        result = self.service.calculate(fixture)

        self.assertEqual(result["calculation"]["ingredient_count"], 2)
        self.assertEqual(result["calculation"]["ingredient_coverage_ratio"], .5)
        with self.db.connect() as connection:
            rows = connection.execute(
                "SELECT mealie_ingredient_id,status,reason FROM ingredient_calculation "
                "WHERE recipe_calculation_id=? ORDER BY position LIMIT 3",
                (result["calculation"]["id"],),
            ).fetchall()
        self.assertEqual(
            [tuple(row) for row in rows],
            [
                ("pilot-position-0", "excluded", "section_heading"),
                ("pilot-position-10", "excluded", "section_heading"),
                ("pilot-position-17", "excluded", "section_heading"),
            ],
        )

    def test_structured_ingredient_title_changes_reuse_cache(self):
        titled = copy.deepcopy(self.recipe)
        titled["recipeIngredient"][0]["title"] = "Tomatoes"
        changed = copy.deepcopy(titled)
        changed["recipeIngredient"][0]["title"] = "  Produce  "
        blank = copy.deepcopy(titled)
        blank["recipeIngredient"][0]["title"] = "   "

        first = self.service.calculate(titled)
        changed_title = self.service.calculate(changed)
        blank_title = self.service.calculate(blank)

        self.assertEqual(recipe_input_hash(titled), recipe_input_hash(changed))
        self.assertEqual(recipe_input_hash(titled), recipe_input_hash(blank))
        self.assertFalse(first["cached"])
        self.assertTrue(changed_title["cached"])
        self.assertTrue(blank_title["cached"])
        self.assertEqual(changed_title["calculation"]["id"], first["calculation"]["id"])
        self.assertEqual(blank_title["calculation"]["id"], first["calculation"]["id"])

    def test_corrupt_legacy_profile_returns_stable_unresolved_calculation(self):
        complete = self.service.calculate(self.recipe)
        self.assertEqual(complete["calculation"]["status"], "complete")
        corrupt = self.db.get_profile("f")
        corrupt["calories_kcal"] = float("inf")

        with patch.object(self.db, "get_profile", return_value=corrupt):
            result = self.service.calculate(self.recipe)

        self.assertTrue(result["ok"])
        self.assertFalse(result["cached"])
        self.assertEqual(result["calculation"]["status"], "insufficient")
        self.assertEqual(result["calculation"]["ingredients"][0]["reason"],
                         "nutrition_profile_invalid")

    def test_repeated_failed_food_is_resolved_once_per_recipe(self):
        self.db = NutritionDB(Path(self.tempdir.name) / "failed.db")
        resolver = Mock()
        resolver.resolve.return_value = Mock(
            status="blocked", confidence=0, error_code="nutrition_provider_unavailable",
            stale_after="2099-01-01T00:00:00+00:00",
        )
        service = NutritionService(self.db, resolver=resolver)
        recipe = {"id": "repeat", "slug": "repeat", "recipeServings": 1,
                  "recipeIngredient": [
                      {"id": "a", "food": {"id": "failed", "name": "tomato"}},
                      {"id": "b", "food": {"id": "failed", "name": "tomato"}},
                  ]}

        service.calculate(recipe)

        resolver.resolve.assert_called_once()

    def test_provider_retry_after_is_preserved_in_service_error(self):
        self.db = NutritionDB(Path(self.tempdir.name) / "retry.db")
        resolver = Mock()
        resolver.resolve.return_value = Mock(
            status="blocked", confidence=0, error_code="nutrition_provider_rate_limited",
            stale_after="2099-01-01T00:00:00+00:00", retry_after=300,
        )
        service = NutritionService(self.db, resolver=resolver)

        result = service.calculate(self.recipe)

        self.assertEqual(result["code"], "nutrition_provider_rate_limited")
        self.assertEqual(result["details"]["retryAfter"], 300)

    def test_failed_food_is_attempted_once_across_one_backfill_run(self):
        self.db = NutritionDB(Path(self.tempdir.name) / "run-failed.db")
        resolver = Mock()
        resolver.resolve.return_value = Mock(
            status="blocked", confidence=0, error_code="nutrition_provider_unavailable",
            stale_after="2099-01-01T00:00:00+00:00",
        )
        service = NutritionService(self.db, resolver=resolver)
        recipes = [
            {"id": value, "slug": value, "recipeIngredient": [
                {"id": value, "food": {"id": "failed", "name": "tomato"}},
            ]} for value in ("one", "two")
        ]

        service.run_backfill(recipes, calculate=service.calculate, batch_size=2, resume=False)

        resolver.resolve.assert_called_once()

    def test_backfill_processes_one_bounded_batch_and_resumes(self):
        calculate = Mock(return_value={"ok": True, "cached": False})
        recipes = [{"id": str(index), "slug": str(index)} for index in range(5)]

        first = self.service.run_backfill(
            recipes, calculate=calculate, batch_size=2, checkpoint_key="personal",
        )
        second = self.service.run_backfill(
            recipes, calculate=calculate, batch_size=2, checkpoint_key="personal",
        )

        self.assertEqual(first["processed"], 2)
        self.assertTrue(first["has_more"])
        self.assertEqual(second["processed"], 2)
        self.assertEqual([call.args[0]["id"] for call in calculate.call_args_list], ["0", "1", "2", "3"])

    def test_backfill_rate_limit_stops_without_checkpointing_failed_recipe(self):
        calculate = Mock(side_effect=[
            {"ok": True, "cached": False},
            {"ok": False, "code": "nutrition_provider_rate_limited", "retryable": True},
            {"ok": True, "cached": False},
        ])
        recipes = [{"id": "a", "slug": "a"}, {"id": "b", "slug": "b"}]

        first = self.service.run_backfill(
            recipes, calculate=calculate, batch_size=5, checkpoint_key="personal",
        )
        second = self.service.run_backfill(
            recipes, calculate=calculate, batch_size=5, checkpoint_key="personal",
        )

        self.assertTrue(first["stopped_rate_limited"])
        self.assertEqual(second["processed"], 1)
        self.assertEqual(calculate.call_args_list[-1].args[0]["id"], "b")

    def test_completed_backfill_rerun_is_idempotent(self):
        calculate = Mock(return_value={"ok": True, "cached": False})
        recipes = [{"id": "a", "slug": "a"}, {"id": "b", "slug": "b"}]
        self.service.run_backfill(
            recipes, calculate=calculate, batch_size=5, checkpoint_key="personal",
        )

        rerun = self.service.run_backfill(
            recipes, calculate=calculate, batch_size=5, checkpoint_key="personal",
        )

        self.assertEqual(rerun["processed"], 0)
        self.assertEqual(rerun["skipped_checkpoint"], 2)
        self.assertEqual(calculate.call_count, 2)


if __name__ == "__main__":
    unittest.main()
