import json
import unittest
from pathlib import Path

from nutrition.calculator import calculate_recipe_nutrition
from nutrition.conversions import ConversionContext
from nutrition.models import NutrientProfile, NutritionPolicy


class NutritionCalculatorTests(unittest.TestCase):
    def setUp(self):
        self.recipe = {
            "id": "r1", "slug": "pasta", "recipeServings": 4,
            "recipeIngredient": [
                {"id": "i1", "quantity": 200, "unit": {"name": "g"},
                 "food": {"id": "tomato", "name": "tomate"}, "originalText": "200 g tomato"},
                {"id": "i2", "quantity": 15, "unit": {"name": "ml"},
                 "food": {"id": "oil", "name": "aceite"}, "originalText": "15 ml oil"},
            ],
        }
        self.profiles = {
            "tomato": NutrientProfile("tomato", calories_kcal=18, carbohydrate_g=3.89,
                protein_g=.88, fat_g=.2, confidence=.95, resolution_confidence=.98),
            "oil": NutrientProfile("oil", calories_kcal=884, fat_g=100,
                confidence=.95, resolution_confidence=.98),
        }
        self.context = ConversionContext(conversions={
            ("oil", "density", "ml"): {"grams_per_unit": .91, "confidence": .95}
        })

    def test_deterministic_totals_per_serving_and_output_rounding(self):
        result = calculate_recipe_nutrition(self.recipe, self.profiles, self.context)
        self.assertAlmostEqual(result.totals["calories_kcal"], 156.666, places=3)
        self.assertAlmostEqual(result.per_serving["fat_g"], 3.5125, places=4)
        self.assertEqual(result.display_values()["calories_kcal"], 39)
        self.assertEqual(result.status, "complete")

    def test_missing_and_zero_servings_keep_totals_but_block_per_serving(self):
        self.recipe["recipeServings"] = None
        result = calculate_recipe_nutrition(self.recipe, self.profiles, self.context)
        self.assertEqual(result.per_serving, {})
        self.assertIn("missing_servings", result.assumptions)
        self.recipe["recipeServings"] = 0
        result = calculate_recipe_nutrition(self.recipe, self.profiles, self.context)
        self.assertEqual(result.per_serving, {})

    def test_coverage_boundaries_and_count_coverage(self):
        recipe = {"id": "r", "slug": "r", "recipeServings": 1, "recipeIngredient": [
            {"quantity": 9, "unit": {"name": "g"}, "food": {"id": "known"}, "originalText": "known"},
            {"quantity": 1, "unit": {"name": "g"}, "food": {"id": "missing"}, "originalText": "missing"},
        ]}
        profiles = {"known": NutrientProfile("known", calories_kcal=100, confidence=1, resolution_confidence=1)}
        result = calculate_recipe_nutrition(recipe, profiles)
        self.assertEqual(result.mass_coverage_ratio, .9)
        self.assertEqual(result.status, "insufficient")
        recipe["recipeIngredient"][0]["quantity"] = 8
        recipe["recipeIngredient"][1]["quantity"] = 2
        self.assertEqual(calculate_recipe_nutrition(recipe, profiles).status, "insufficient")
        recipe["recipeIngredient"][0]["quantity"] = 6
        recipe["recipeIngredient"][1]["quantity"] = 4
        result = calculate_recipe_nutrition(recipe, profiles)
        self.assertEqual(result.status, "insufficient")
        self.assertEqual(result.ingredient_coverage_ratio, .5)

    def test_weighted_confidence_and_publication_gate(self):
        result = calculate_recipe_nutrition(self.recipe, self.profiles, self.context)
        self.assertGreater(result.confidence, .8)
        low = dict(self.profiles)
        low["oil"] = NutrientProfile("oil", calories_kcal=884, fat_g=100,
            confidence=.5, resolution_confidence=.7)
        result = calculate_recipe_nutrition(self.recipe, low, self.context)
        self.assertFalse(result.is_publishable(NutritionPolicy()))

    def test_section_heading_is_excluded_from_applicable_coverage(self):
        self.recipe["recipeIngredient"][:0] = [
            {"id": "explicit-heading", "quantity": None, "unit": None, "food": None,
             "originalText": "Sauce", "isSectionHeader": True},
            {"id": "english-heading", "quantity": None, "unit": None, "food": None,
             "originalText": "For the sauce"},
            {"id": "spanish-heading", "quantity": None, "unit": None, "food": None,
             "originalText": "Para la salsa"},
        ]

        result = calculate_recipe_nutrition(self.recipe, self.profiles, self.context)

        self.assertTrue(all(item.status == "excluded" for item in result.ingredients[:3]))
        self.assertTrue(all(item.reason == "section_heading" for item in result.ingredients[:3]))
        self.assertEqual(result.ingredient_count, 2)
        self.assertEqual(result.ingredient_coverage_ratio, 1)
        self.assertEqual(result.coverage_ratio, 1)

    def test_mealie_title_only_headings_are_excluded_without_hiding_real_ingredients(self):
        fixture_dir = Path(__file__).parent / "fixtures"
        profile = NutrientProfile("f", calories_kcal=18, confidence=1, resolution_confidence=1)

        for language in ("en", "es"):
            with self.subTest(language=language):
                recipe = json.loads(
                    (fixture_dir / f"mealie_ingredient_rows_{language}.json").read_text()
                )
                source_text = [item["originalText"] for item in recipe["recipeIngredient"]]

                result = calculate_recipe_nutrition(recipe, {"f": profile})

                self.assertEqual(result.ingredients[0].status, "excluded")
                self.assertEqual(result.ingredients[0].reason, "section_heading")
                self.assertEqual(result.ingredients[1].status, "unresolved")
                self.assertEqual(result.ingredients[2].status, "computed")
                self.assertEqual(result.ingredient_count, 2)
                self.assertEqual(result.mass_coverage_ratio, 1)
                self.assertEqual(result.ingredient_coverage_ratio, .5)
                self.assertEqual(result.coverage_ratio, .5)
                self.assertEqual(result.confidence, .5)
                self.assertEqual(result.unresolved, [source_text[1]])
                self.assertEqual(
                    [item.original_text for item in result.ingredients], source_text
                )

    def test_three_mealie_pilot_zero_quantity_headings_are_excluded_from_coverage(self):
        fixture = json.loads(
            (Path(__file__).parent / "fixtures" / "mealie_pilot_zero_headings.json").read_text()
        )
        profile = NutrientProfile("f", calories_kcal=18, confidence=1, resolution_confidence=1)

        result = calculate_recipe_nutrition(fixture, {"f": profile})

        self.assertEqual(
            [(item.mealie_ingredient_id, item.status, item.reason) for item in result.ingredients[:3]],
            [
                ("pilot-position-0", "excluded", "section_heading"),
                ("pilot-position-10", "excluded", "section_heading"),
                ("pilot-position-17", "excluded", "section_heading"),
            ],
        )
        self.assertEqual(result.ingredients[3].status, "unresolved")
        self.assertEqual(result.ingredient_count, 2)
        self.assertEqual(result.ingredient_coverage_ratio, .5)
        self.assertEqual(result.unresolved, ["sal al gusto"])

    def test_unstructured_real_ingredients_are_not_mistaken_for_headings(self):
        recipe = {"id": "r", "slug": "r", "recipeServings": 1, "recipeIngredient": [
            {"id": "salt-en", "quantity": None, "unit": None, "food": None,
             "originalText": "salt to taste"},
            {"id": "salt-es", "quantity": None, "unit": None, "food": None,
             "originalText": "sal al gusto"},
        ]}

        result = calculate_recipe_nutrition(recipe, {})

        self.assertTrue(all(item.status == "unresolved" for item in result.ingredients))
        self.assertEqual(result.ingredient_count, 2)
        self.assertEqual(result.ingredient_coverage_ratio, 0)
        self.assertEqual(result.coverage_ratio, 0)

    def test_unknown_conversion_prevents_complete_status_via_count_coverage(self):
        recipe = {"id": "r", "slug": "r", "recipeServings": 1, "recipeIngredient": [
            {"quantity": 100, "unit": {"name": "gram"},
             "food": {"id": "known", "name": "tomato"}, "originalText": "100 g tomato"},
            {"quantity": 1, "unit": {"name": "bunch"},
             "food": {"id": "unknown", "name": "parsley"}, "originalText": "1 bunch parsley"},
        ]}
        profiles = {
            "known": NutrientProfile("known", calories_kcal=18, confidence=1),
            "unknown": NutrientProfile("unknown", calories_kcal=36, confidence=1),
        }

        result = calculate_recipe_nutrition(recipe, profiles)

        self.assertEqual(result.mass_coverage_ratio, 1)
        self.assertEqual(result.ingredient_coverage_ratio, .5)
        self.assertEqual(result.coverage_ratio, .5)
        self.assertEqual(result.status, "insufficient")
        self.assertEqual(result.resolved_count, 2)
        self.assertEqual(result.computable_count, 1)

    def test_non_finite_profile_is_unresolved_and_never_publishable(self):
        recipe = {"id": "r", "slug": "r", "recipeServings": 1, "recipeIngredient": [
            {"quantity": 100, "unit": {"name": "g"},
             "food": {"id": "egg", "name": "egg"}, "originalText": "100 g egg"},
        ]}

        for amount in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(amount=amount):
                profile = NutrientProfile("egg", calories_kcal=amount, confidence=1)
                result = calculate_recipe_nutrition(recipe, {"egg": profile})

                self.assertEqual(result.status, "insufficient")
                self.assertEqual(result.ingredients[0].status, "unresolved")
                self.assertEqual(result.ingredients[0].reason, "nutrition_profile_invalid")
                self.assertFalse(result.is_publishable(NutritionPolicy()))
                self.assertEqual(result.display_values(), {
                    field: 0 if field == "calories_kcal" or field.endswith("_mg") else 0.0
                    for field in result.totals
                })


if __name__ == "__main__":
    unittest.main()
