"""Findings of the 0.4.0 pre-release pass against a real Mealie 3.22 and real recipe pages."""
import unittest
from typing import Any, cast
from unittest.mock import patch

from fakes.mealie_api import MealieAPI
from sabor.core.config import load_settings
from sabor.core.context import build_context
from sabor.core.toolsets import bind_context
from sabor.importing import tools as t
from sabor.importing.scrape import recipe_from_html
from sabor.store import DateRange
from sabor.store.mealie import MealieRecipeStore

PAGE = ('<script type="application/ld+json">{"@context": "https://schema.org", "@type": "Recipe", '
        '"name": "Flapjacks", "recipeIngredient": ["150g butter"], "recipeInstructions": ["Bake"], '
        '"totalTime": "PT35M", "prepTime": "PT10M", "cookTime": "PT1H25M"}</script>').encode()


class EmptyMealieTests(unittest.TestCase):
    """Mealie 3.22 answers an empty page with total_pages 0 (checked on a live instance)."""

    def setUp(self):
        self.api = MealieAPI()
        self.store = MealieRecipeStore('https://mealie.invalid', 'token', self.api)

    def test_no_shopping_lists_is_an_empty_result(self):
        self.assertEqual(self.store.get_shopping_lists(), [])

    def test_an_empty_week_is_an_empty_meal_plan(self):
        self.assertEqual(self.store.get_mealplan(DateRange('2031-01-06', '2031-01-12')), [])

    def test_a_new_mealie_user_gets_a_first_shopping_list(self):
        from sabor.tools import mealplan, shopping
        ctx = build_context(load_settings({'MEALIE_BASE_URL': 'https://mealie.invalid', 'MEALIE_API_TOKEN': 't'}),
                            store_factory=lambda s: self.store)
        created = cast(Any, bind_context(shopping.get_or_create_shopping_list, ctx))('Compra')
        self.assertTrue(created['ok'], created)
        self.assertEqual([row['name'] for row in self.api.lists.values()], ['Compra'])
        week = cast(Any, bind_context(mealplan.get_mealplan_week, ctx))('2031-01-06', '2031-01-12')
        self.assertTrue(week['ok'], week)


class InconsistentEmptyPagesTests(unittest.TestCase):
    """Only a consistent empty answer is empty; anything else stays invalid_response."""

    def store(self, body):
        from unittest.mock import Mock
        session = Mock()
        session.get.return_value = Mock(status_code=200, json=lambda: body)
        return MealieRecipeStore('https://mealie.invalid', 'token', session)

    def test_inconsistent_empty_shopping_lists_are_invalid(self):
        from sabor.store import StoreError
        for body in ({'items': [], 'total': 0, 'total_pages': 0, 'page': 2},
                     {'items': [], 'total': False, 'total_pages': 0, 'page': 1},
                     {'items': [], 'total': 0, 'total_pages': False, 'page': 1},
                     {'items': [], 'total': 3, 'total_pages': 0, 'page': 1}):
            with self.assertRaises(StoreError, msg=body) as caught:
                self.store(body).get_shopping_lists()
            self.assertEqual(caught.exception.code, 'invalid_response')

    def test_inconsistent_empty_meal_plans_are_invalid(self):
        from sabor.store import StoreError
        for body in ({'items': [], 'total': 5, 'total_pages': 0, 'page': 1},
                     {'items': [], 'total': 0, 'total_pages': 0, 'page': 2},
                     {'items': [], 'total': 0, 'total_pages': False, 'page': 1}):
            with self.assertRaises(StoreError, msg=body) as caught:
                self.store(body).get_mealplan(DateRange('2031-01-06', '2031-01-12'))
            self.assertEqual(caught.exception.code, 'invalid_response')


class ImportedTimesTests(unittest.TestCase):
    def test_an_explicit_zero_time_is_kept(self):
        # Some recipe-scrapers versions return 0 for an explicit zero; it is a value, not "unknown".
        from unittest.mock import Mock
        scraper = Mock(title=lambda: 'Salad', ingredients=lambda: ['1 lettuce'], instructions_list=lambda: ['Mix'],
                       yields=lambda: None, total_time=lambda: 5, prep_time=lambda: 5, cook_time=lambda: 0,
                       image=lambda: None)
        with patch('sabor.importing.scrape.recipe_scrapers.scrape_html', return_value=scraper):
            data = recipe_from_html(b'<html></html>', 'https://example.org/salad')
        assert data is not None
        self.assertEqual((data['totalTime'], data['cookTime']), ('PT5M', 'PT0M'))

    def test_recipe_scrapers_minutes_become_iso_durations(self):
        # recipe-scrapers turns PT35M into the integer 35, which Mealie showed as a bare "35".
        data = recipe_from_html(PAGE, 'https://example.org/flapjacks')
        assert data is not None
        self.assertEqual((data['totalTime'], data['prepTime'], data['cookTime']), ('PT35M', 'PT10M', 'PT85M'))

    def test_imported_page_times_reach_mealie_with_units(self):
        api = MealieAPI()
        store = MealieRecipeStore('https://mealie.invalid', 'token', api)
        ctx = build_context(load_settings({'MEALIE_BASE_URL': 'https://mealie.invalid', 'MEALIE_API_TOKEN': 't',
                                           'SABOR_NORMALIZE_IMPORTS': 'false'}),
                            store_factory=lambda s: store)
        with patch.object(t, 'html_bytes', return_value=(PAGE, 'text/html')), \
                patch('sabor.tools.nutrition._nutrition_after_import', return_value={}):
            result = t._import_recipe(ctx, 'https://example.org/flapjacks', 'url', False, 'en', 8, .55)
        self.assertEqual(result['data']['created'], ['flapjacks'])
        recipe = api.recipes['flapjacks']
        self.assertEqual((recipe['totalTime'], recipe['prepTime'], recipe['cookTime']), ('35 min', '10 min', '85 min'))


if __name__ == '__main__':
    unittest.main()
