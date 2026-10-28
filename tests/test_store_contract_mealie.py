"""Run the shared contract through the real Mealie adapter, without HTTP."""
import unittest

from fakes.mealie_api import MealieAPI
from test_store_contract import CAPABILITIES, StoreContractMixin
from sabor.store.mealie import MealieRecipeStore


class MealieStoreContract(StoreContractMixin, unittest.TestCase):
    # Mealie stores and serves its own recipe images.
    capabilities = CAPABILITIES - {"occurrence_ids", "recipe_images"}

    def absent_id(self, kind) -> str:
        return "absent" if kind == "recipe" else "ffffffff-ffff-4fff-8fff-ffffffffffff"

    def seed_ratings(self):
        self.store.session.set_rating("soup", 3)
        self.store.session.set_rating("pear", 5)

    def make_store(self):
        return MealieRecipeStore("https://mealie.invalid", "contract", MealieAPI())
