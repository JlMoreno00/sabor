"""Migration contracts for the retained single-capability tool surface."""
import unittest
from unittest.mock import patch
from sabor.integrations import spoonacular


class NutrientSearchMigrationTests(unittest.TestCase):
    def test_nutrient_only_search_uses_complex_search(self):
        with patch.object(spoonacular, '_spoonacular_get', return_value={
            'ok': True, 'data': {'results': [], 'totalResults': 0}}) as get:
            result = spoonacular.spoonacular_search_recipes(
                max_calories=500, min_protein_g=20, max_carbs_g=40)
        self.assertTrue(result['ok'])
        path, params = get.call_args.args
        self.assertEqual(path, '/recipes/complexSearch')
        self.assertEqual(params['query'], '')
        self.assertEqual(params['maxCalories'], 500)
        self.assertEqual(params['minProtein'], 20)
        self.assertEqual(params['maxCarbs'], 40)
        self.assertTrue(params['addRecipeNutrition'])
