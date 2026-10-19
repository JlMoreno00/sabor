import unittest

from nutrition.models import NutritionPolicy, NutrientProfile, RecipeCalculation


class NutritionModelTests(unittest.TestCase):
    def test_policy_defaults_are_conservative(self):
        policy = NutritionPolicy()
        self.assertEqual(policy.min_publish_coverage, .9)
        self.assertEqual(policy.min_publish_confidence, .8)
        self.assertFalse(policy.publish_estimates)

    def test_models_serialize_stable_evidence(self):
        profile = NutrientProfile("food", calories_kcal=18, confidence=.9)
        calculation = RecipeCalculation(
            id="calc", recipe_id="r", slug="recipe", input_hash="i",
            profile_version="p", servings=2, status="complete",
            coverage_ratio=1, confidence=.9, totals={"calories_kcal": 36},
            per_serving={"calories_kcal": 18},
        )
        self.assertEqual(profile.to_dict()["basis_grams"], 100)
        self.assertEqual(calculation.to_dict()["mealie_recipe_slug"], "recipe")


if __name__ == "__main__":
    unittest.main()
