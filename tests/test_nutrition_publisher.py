import unittest
from unittest.mock import Mock

from nutrition.models import RecipeCalculation
from nutrition.publisher import (
    FIELD_MAP,
    MealiePublisher,
    canonical_supported_nutrition,
)


class NutritionPublisherTests(unittest.TestCase):
    def calculation(self, status="complete", confidence=.9, coverage=1):
        return RecipeCalculation(
            id="c", recipe_id="r", slug="recipe", input_hash="i", profile_version="p",
            servings=2, status=status, coverage_ratio=coverage, confidence=confidence,
            totals={"calories_kcal": 100}, per_serving={
                "calories_kcal": 50, "carbohydrate_g": 4.24, "protein_g": 3.16,
                "fat_g": 2.05, "fiber_g": 1.02, "sodium_mg": 123.6,
            },
        )

    def test_exact_patch_payload_is_per_serving_and_preserves_settings(self):
        request = Mock(return_value=Mock(status_code=200, text=""))
        publisher = MealiePublisher("http://mealie", {"Authorization": "Bearer x"}, request=request)
        result = publisher.publish("recipe", self.calculation(), existing_recipe={"settings": {"showAssets": True}})
        self.assertTrue(result["ok"])
        payload = request.call_args.kwargs["json"]
        self.assertEqual(payload["nutrition"]["calories"], "50 kcal")
        self.assertEqual(payload["nutrition"]["carbohydrateContent"], "4.2 g")
        self.assertEqual(payload["nutrition"]["sodiumContent"], "124 mg")
        self.assertEqual(payload["settings"], {"showAssets": True, "showNutrition": True})

    def test_manual_protection_thresholds_dry_run_and_http_failure(self):
        request = Mock()
        publisher = MealiePublisher("http://mealie", {}, request=request)
        protected = publisher.publish("recipe", self.calculation(), existing_recipe={"extras": {"saborNutritionProtected": True}})
        self.assertEqual(protected["code"], "nutrition_manual_protection")
        insufficient = publisher.publish("recipe", self.calculation("insufficient", coverage=.6))
        self.assertEqual(insufficient["code"], "nutrition_insufficient_coverage")
        dry = publisher.publish("recipe", self.calculation(), dry_run=True)
        self.assertTrue(dry["ok"])
        self.assertFalse(dry["published"])
        request.assert_not_called()
        request.return_value = Mock(status_code=500, text="failure")
        failed = publisher.publish("recipe", self.calculation())
        self.assertEqual(failed["code"], "nutrition_mealie_patch_failed")

    def test_publication_protection_contract_blocks_preview_and_apply_without_requests(self):
        cases = (
            ("extras", {"extras": {"saborNutritionProtected": True}}, True),
            ("settings", {"settings": {"saborNutritionProtected": True}}, True),
            ("both", {
                "extras": {"saborNutritionProtected": False},
                "settings": {"saborNutritionProtected": True},
            }, True),
            ("neither", {}, False),
            ("non_boolean_values", {
                "extras": {"saborNutritionProtected": "true"},
                "settings": {"saborNutritionProtected": 1},
            }, False),
            ("null_mappings", {"extras": None, "settings": None}, False),
            ("malformed_extras", {"extras": ["not", "a", "mapping"]}, True),
            ("malformed_settings", {"settings": "not a mapping"}, True),
        )
        for name, existing_recipe, protected in cases:
            for dry_run in (True, False):
                with self.subTest(name=name, dry_run=dry_run):
                    request = Mock(return_value=Mock(status_code=200, text=""))
                    publisher = MealiePublisher("http://mealie", {}, request=request)

                    result = publisher.publish(
                        "recipe", self.calculation(), existing_recipe=existing_recipe,
                        dry_run=dry_run,
                    )

                    if protected:
                        self.assertEqual(result["code"], "nutrition_manual_protection")
                        self.assertFalse(result["ok"])
                        request.assert_not_called()
                    elif dry_run:
                        self.assertEqual(result["code"], "nutrition_publish_preview")
                        request.assert_not_called()
                    else:
                        self.assertEqual(result["code"], "nutrition_published")
                        request.assert_called_once()

    def test_non_finite_values_are_rejected_without_formatting_or_request(self):
        for amount in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(amount=amount):
                request = Mock()
                calculation = self.calculation()
                calculation.per_serving["calories_kcal"] = amount
                publisher = MealiePublisher("http://mealie", {}, request=request)

                result = publisher.publish("recipe", calculation)

                self.assertEqual(result["code"], "nutrition_invalid_values")
                request.assert_not_called()

    def test_persisted_nutrition_contract_accepts_every_publisher_field(self):
        valid = {
            target: f"123.4 {unit}" if digits else f"123 {unit}"
            for target, unit, digits in FIELD_MAP.values()
        }

        self.assertEqual(canonical_supported_nutrition(valid), valid)
        self.assertEqual(
            canonical_supported_nutrition({
                "nutrition": valid,
                "settings": {"showNutrition": True},
            }, full_payload=True),
            valid,
        )
        self.assertEqual(
            canonical_supported_nutrition({**valid, "futureField": "ignored"}),
            valid,
        )

    def test_persisted_nutrition_contract_rejects_malformed_supported_values(self):
        malformed_values = (
            {}, [], True, False, 1, 1.2,
            "NaN g", "Infinity g", "-Infinity g",
            "1\tg", "1\ng", "1\x00g", "1 " + "9" * 64 + " g",
        )
        for target, unit, digits in FIELD_MAP.values():
            valid = f"1.0 {unit}" if digits else f"1 {unit}"
            wrong_unit = next(candidate for candidate in ("kcal", "g", "mg")
                              if candidate != unit)
            oversized = f"{'9' * 64}.0 {unit}" if digits else f"{'9' * 65} {unit}"
            cases = malformed_values + (
                f"1 {wrong_unit}",
                f"1.0 {unit}" if not digits else f"1 {unit}",
                f"01.0 {unit}" if digits else f"01 {unit}",
                oversized,
            )
            for value in cases:
                with self.subTest(field=target, value=value):
                    self.assertIsNone(canonical_supported_nutrition({
                        target: value,
                        "futureField": "ignored",
                    }))
            with self.subTest(field=target, value=valid):
                self.assertEqual(canonical_supported_nutrition({target: valid}), {
                    target: valid,
                })

    def test_persisted_nutrition_contract_omits_null_and_blank_supported_values(self):
        for target, _, _ in FIELD_MAP.values():
            for value in (None, "", "   "):
                with self.subTest(field=target, value=value):
                    self.assertEqual(canonical_supported_nutrition({target: value}), {})

    def test_persisted_nutrition_contract_rejects_non_mapping_envelopes(self):
        for value in (None, [], ["nutrition"], True, 1, "nutrition"):
            with self.subTest(value=value):
                self.assertIsNone(canonical_supported_nutrition(value))
        for value in (None, [], True, 1, "nutrition"):
            with self.subTest(full_payload=value):
                self.assertIsNone(canonical_supported_nutrition(
                    {"nutrition": value}, full_payload=True,
                ))

    def test_existing_publication_fixtures_satisfy_persisted_contract(self):
        fixtures = (
            {"calories": "50 kcal", "carbohydrateContent": "4.2 g",
             "proteinContent": "3.2 g", "fatContent": "2.0 g",
             "fiberContent": "1.0 g", "sodiumContent": "124 mg"},
            {"calories": "100 kcal", "proteinContent": "5.0 g",
             "sodiumContent": "201 mg"},
        )
        for fixture in fixtures:
            with self.subTest(fixture=fixture):
                self.assertEqual(canonical_supported_nutrition(fixture), fixture)


if __name__ == "__main__":
    unittest.main()
