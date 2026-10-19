import copy
import json
import math
import unittest
from dataclasses import FrozenInstanceError
from itertools import product
from pathlib import Path


from nutrition.readiness import (
    READINESS_CLASSIFIER_VERSION,
    FoodEvidence,
    ReadinessEvidence,
    ReadinessPolicy,
    assess_recipe_readiness,
)
from nutrition.conversions import convert_ingredient_to_grams
from nutrition.consumption import (
    RETENTION_EVIDENCE_PRECEDENCE,
    classify_consumption,
)


FIXTURES = Path(__file__).with_name("fixtures")


def ingredient(quantity=100, unit="g", food_id="food", text="100 g food", **extra):
    value = {
        "id": extra.pop("id", "ingredient"),
        "quantity": quantity,
        "unit": {"id": f"unit-{unit}", "name": unit, "abbreviation": unit} if unit else None,
        "food": {"id": food_id, "name": food_id} if food_id else None,
        "originalText": text,
    }
    value.update(extra)
    return value


def recipe(*items, servings=4, **extra):
    value = {
        "id": "recipe-id", "slug": "recipe-slug", "name": "Recipe",
        "recipeServings": servings, "recipeIngredient": list(items),
    }
    value.update(extra)
    return value


def ready_evidence(*food_ids):
    return ReadinessEvidence(
        status="available",
        foods=tuple(FoodEvidence(food_id=food_id) for food_id in food_ids),
    )


class StructuralReadinessTests(unittest.TestCase):
    def assess(self, value, evidence=None, include_suggestions=True):
        food_ids = {
            str((item.get("food") or {}).get("id"))
            for item in value.get("recipeIngredient", [])
            if (item.get("food") or {}).get("id")
        }
        return assess_recipe_readiness(
            value, evidence or ready_evidence(*sorted(food_ids)),
            include_suggestions=include_suggestions,
        )

    def test_01_classifier_contracts_are_immutable_and_versioned(self):
        self.assertEqual(READINESS_CLASSIFIER_VERSION, "1")
        policy = ReadinessPolicy()
        evidence = FoodEvidence(food_id="food")
        with self.assertRaises(FrozenInstanceError):
            policy.min_confidence = .5
        with self.assertRaises(FrozenInstanceError):
            evidence.status = "missing"

    def test_02_ready_recipe_has_stable_gates_counts_and_score(self):
        result = self.assess(recipe(ingredient()))
        self.assertEqual(result["root"], "ready")
        self.assertEqual(result["rootCounts"], {"ready": 1})
        self.assertTrue(all(result["gates"].values()))
        self.assertEqual(result["score"], 100)
        self.assertEqual(len(result["fingerprint"]), 64)

    def test_03_zero_valued_title_headings_are_derived_and_never_block(self):
        value = json.loads((FIXTURES / "mealie_pilot_zero_headings.json").read_text())
        result = self.assess(value)
        self.assertEqual(result["rootCounts"]["derived_non_food_row"], 3)
        self.assertNotIn("derived_non_food_row", result["blockingRoots"])
        self.assertEqual(result["root"], "missing_quantity")

    def test_04_title_only_heading_requires_shared_exact_shape(self):
        titled_food = ingredient(0, None, "salt", "Salt", title="Salt")
        result = self.assess(recipe(titled_food))
        self.assertEqual(result["root"], "missing_quantity")

    def test_05_salt_to_taste_is_missing_quantity_not_a_heading(self):
        value = json.loads((FIXTURES / "mealie_ingredient_rows_en.json").read_text())
        result = self.assess(value)
        salt = next(issue for issue in result["issues"] if issue["ingredientId"] == "salt-en-row")
        self.assertEqual(salt["root"], "missing_quantity")

    def test_06_parser_loss_quantity_precedes_missing_quantity(self):
        result = self.assess(recipe(ingredient(None, "g", "salt", "2 g salt")))
        self.assertEqual(result["root"], "parser_loss")
        self.assertNotIn("missing_quantity", result["rootCounts"])

    def test_07_parser_loss_range_precedes_missing_quantity(self):
        result = self.assess(recipe(ingredient(None, "g", "salt", "2-4 g salt")))
        self.assertEqual(result["root"], "parser_loss")

    def test_08_parser_loss_fraction_precedes_missing_quantity(self):
        result = self.assess(recipe(ingredient(None, "cup", "rice", "1/2 cup rice")))
        self.assertEqual(result["root"], "parser_loss")

    def test_09_parser_loss_unit_precedes_conversion_symptom(self):
        result = self.assess(recipe(ingredient(2, None, "oil", "2 tbsp oil")))
        self.assertEqual(result["root"], "parser_loss")

    def test_10_structured_quantity_and_unit_without_food_requires_selection(self):
        result = self.assess(recipe(ingredient(2, "g", None, "2 g salt")))
        self.assertEqual(result["root"], "food_selection_required")

    def test_11_free_text_without_exact_lexical_structure_is_missing_quantity(self):
        result = self.assess(recipe(ingredient(None, None, None, "salt to taste")))
        self.assertEqual(result["root"], "missing_quantity")

    def test_12_missing_food_link_requires_selection_without_invention(self):
        value = recipe(ingredient(2, "g", None, "seasoning"))
        before = copy.deepcopy(value)
        result = self.assess(value)
        self.assertEqual(result["root"], "food_selection_required")
        self.assertEqual(value, before)
        self.assertNotIn("selectedFoodId", repr(result))

    def test_13_unselected_structured_alternatives_require_food_selection(self):
        item = ingredient(1, "g", "tomato", "tomato or pepper", alternatives=[
            {"foodId": "tomato"}, {"foodId": "pepper"},
        ])
        result = self.assess(recipe(item))
        self.assertEqual(result["root"], "food_selection_required")

    def test_14_selected_alternative_can_be_ready(self):
        item = ingredient(1, "g", "tomato", "tomato or pepper", alternatives=[
            {"foodId": "tomato"}, {"foodId": "pepper"},
        ], selectedAlternative="tomato")
        self.assertEqual(self.assess(recipe(item))["root"], "ready")

    def test_15_optional_excluded_and_non_consumed_rows_do_not_block(self):
        items = (
            ingredient(None, None, None, "optional garnish", optional=True, included=False),
            ingredient(None, None, None, "discarded brine", nonConsumed=True),
            ingredient(),
        )
        result = self.assess(recipe(*items))
        self.assertEqual(result["root"], "ready")
        self.assertEqual(result["rootCounts"]["derived_non_food_row"], 2)

    def test_16_optional_included_row_is_consumed_and_blocks(self):
        item = ingredient(None, None, None, "salt to taste", optional=True, included=True)
        self.assertEqual(self.assess(recipe(item))["root"], "missing_quantity")

    def test_17_frying_oil_requires_explicit_consumption_evidence(self):
        item = ingredient(100, "g", "oil", "oil for frying")
        self.assertEqual(self.assess(recipe(item))["root"], "conversion_evidence_missing")

    def test_18_marinade_requires_explicit_retention_evidence(self):
        item = ingredient(100, "g", "marinade", "marinade for chicken")
        self.assertEqual(self.assess(recipe(item))["root"], "conversion_evidence_missing")

    def test_19_explicit_frying_and_marinade_consumption_can_be_ready(self):
        items = (
            ingredient(100, "g", "oil", "oil for frying", absorbedGrams=12),
            ingredient(100, "g", "marinade", "marinade for chicken", retainedFraction=.4),
        )
        self.assertEqual(self.assess(recipe(*items))["root"], "ready")

    def test_20_missing_servings_blocks_per_serving_not_total_gate(self):
        result = self.assess(recipe(ingredient(), servings=None))
        self.assertEqual(result["root"], "missing_servings")
        self.assertFalse(result["gates"]["totalCalculation"])
        self.assertFalse(result["gates"]["perServingCalculation"])
        self.assertFalse(result["gates"]["backfill"])

    def test_21_zero_nonfinite_and_text_servings_are_missing(self):
        for servings in (0, -1, float("nan"), float("inf"), "four"):
            with self.subTest(servings=servings):
                self.assertEqual(self.assess(recipe(ingredient(), servings=servings))["root"],
                                 "missing_servings")

    def test_22_existing_nutrition_with_manual_protection_blocks_publication_only(self):
        value = recipe(
            ingredient(), nutrition={"calories": "100"},
            extras={"saborNutritionProtected": True},
        )
        result = self.assess(value)
        self.assertEqual(result["root"], "manual_publication_conflict")
        self.assertTrue(result["gates"]["totalCalculation"])
        self.assertFalse(result["gates"]["publication"])
        self.assertTrue(result["gates"]["backfill"])

    def test_publication_protection_contract_covers_both_recipe_mappings(self):
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
        for name, fields, protected in cases:
            with self.subTest(name=name):
                result = self.assess(recipe(ingredient(), **fields))
                self.assertEqual(result["gates"]["publication"], not protected)
                self.assertEqual(
                    "manual_publication_conflict" in result["rootCounts"], protected,
                )

    def test_23_one_blocker_is_not_hidden_by_high_score(self):
        items = [ingredient(id=f"ready-{index}") for index in range(19)]
        items.append(ingredient(None, None, None, "salt to taste", id="blocked"))
        result = self.assess(recipe(*items))
        self.assertGreaterEqual(result["score"], 90)
        self.assertEqual(result["root"], "missing_quantity")
        self.assertFalse(result["gates"]["backfill"])

    def test_24_semantic_root_precedence_is_stable_across_row_order(self):
        blockers = [
            ingredient(None, None, None, "salt to taste", id="quantity"),
            ingredient(1, "g", None, "seasoning", id="food"),
        ]
        first = self.assess(recipe(*blockers))
        second = self.assess(recipe(*reversed(blockers)))
        self.assertEqual(first["root"], "missing_quantity")
        self.assertEqual(second["root"], "missing_quantity")

    def test_25_fingerprint_is_deterministic_and_suggestions_do_not_change_it(self):
        value = recipe(ingredient(None, None, None, "salt to taste"))
        first = self.assess(value, include_suggestions=True)
        second = self.assess(copy.deepcopy(value), include_suggestions=False)
        self.assertEqual(first["fingerprint"], second["fingerprint"])
        self.assertIn("suggestion", first["issues"][0])
        self.assertNotIn("suggestion", second["issues"][0])

    def test_26_discarded_qualifier_does_not_exclude_consumed_food(self):
        for text, food_id in (
            ("400 g chickpeas, discarded brine", "chickpeas"),
            ("400 g garbanzos, salmuera desechada", "garbanzos"),
        ):
            with self.subTest(text=text):
                result = self.assess(recipe(ingredient(400, "g", food_id, text)))
                self.assertEqual(result["root"], "ready")
                self.assertEqual(result["rootCounts"], {"ready": 1})

    def test_27_only_whole_text_non_consumed_concepts_are_excluded(self):
        for text in (
            "  DISCARDED   BRINE!!! ",
            "\tSalmuera   DESECHADA.\n",
            "Garnish not consumed;",
            "Agua de cocción:",
        ):
            with self.subTest(text=text):
                result = self.assess(recipe(ingredient(None, None, None, text)))
                self.assertEqual(result["root"], "missing_quantity")
                self.assertEqual(result["issues"][-1]["reason"], "no_consumed_ingredients")
                self.assertEqual(result["rootCounts"], {
                    "derived_non_food_row": 1, "missing_quantity": 1,
                })

    def test_28_recipe_requires_at_least_one_consumed_ingredient(self):
        excluded_rows = (
            ingredient(None, None, None, "Sauce", title="Sauce"),
            ingredient(None, None, None, "optional garnish", optional=True),
            ingredient(None, None, None, "stock", nonConsumed=True),
        )
        cases = (recipe(), recipe(excluded_rows[0]), recipe(excluded_rows[1]),
                 recipe(excluded_rows[2]), recipe(*excluded_rows))
        for value in cases:
            with self.subTest(items=len(value["recipeIngredient"])):
                first = self.assess(value)
                second = self.assess(copy.deepcopy(value))
                issue = next(item for item in first["issues"]
                             if item["reason"] == "no_consumed_ingredients")
                self.assertEqual(issue["root"], "missing_quantity")
                self.assertNotIn("position", issue)
                self.assertEqual(first["score"], 0)
                self.assertFalse(first["ready"])
                self.assertFalse(first["gates"]["totalCalculation"])
                self.assertFalse(first["gates"]["publication"])
                self.assertFalse(first["gates"]["backfill"])
                self.assertEqual(first["fingerprint"], second["fingerprint"])

    def test_29_one_consumed_row_among_exclusions_satisfies_invariant(self):
        rows = (
            ingredient(None, None, None, "optional garnish", optional=True),
            ingredient(None, None, None, "discarded brine", nonConsumed=True),
            ingredient(100, "g", "beans", "100 g beans"),
        )
        result = self.assess(recipe(*rows))
        self.assertEqual(result["root"], "ready")
        self.assertNotIn("no_consumed_ingredients", repr(result))

    def test_30_marinade_seasoning_is_not_a_retention_semantic(self):
        for text, food_id in (
            ("100 g marinade seasoning", "marinade-seasoning"),
            ("100 g condimento para marinada", "condimento-marinada"),
        ):
            with self.subTest(text=text):
                result = self.assess(recipe(ingredient(100, "g", food_id, text)))
                self.assertEqual(result["root"], "ready")

    def test_31_whole_concept_marinades_require_retention(self):
        for text in (
            "100 g marinade for chicken", "100 G Marinade FOR chicken!",
            "100 g marinada para pollo", "100 g MARINADA de pollo.",
            "100 g chicken marinade", "100 g Marinada de POLLO",
        ):
            with self.subTest(text=text):
                result = self.assess(recipe(ingredient(100, "g", "marinade", text)))
                issue = result["issues"][0]
                self.assertEqual(issue["root"], "conversion_evidence_missing")
                self.assertEqual(issue["reason"], "marinade_retention_missing")

    def test_32_readiness_and_conversion_agree_for_every_consumption_shape(self):
        cases = (
            ("cooking water", {}, "non_consumed"),
            ("500 ml agua de cocción.", {}, "non_consumed"),
            ("discarded brine", {}, "non_consumed"),
            ("líquido desechado", {}, "non_consumed"),
            ("chicken bones", {}, "non_consumed"),
            ("cáscaras de huevo", {}, "non_consumed"),
            ("potato peels", {}, "non_consumed"),
            ("400 g chickpeas, discarded brine", {}, "converted"),
            ("400 g garbanzos, salmuera desechada", {}, "converted"),
            ("marinade for chicken", {}, "marinade_requires_retention"),
            ("marinada de pollo", {}, "marinade_requires_retention"),
            ("chicken marinade", {}, "marinade_requires_retention"),
            ("marinade seasoning", {}, "converted"),
            ("marinade spice", {}, "converted"),
            ("condimento para marinada", {}, "converted"),
            ("100 g chicken with marinade", {}, "converted"),
            ("100 g pollo con marinada", {}, "converted"),
            ("chicken marinade", {"retainedFraction": .4}, "converted"),
            ("marinada para pollo", {"retainedGrams": 35}, "converted"),
            ("100 g chicken", {"nonConsumed": True}, "non_consumed"),
        )
        for text, extra, conversion_outcome in cases:
            with self.subTest(text=text, extra=extra):
                item = ingredient(100, "g", "food", text, **extra)
                conversion = convert_ingredient_to_grams(item)
                actual_outcome = (conversion.reason if conversion.status == "unresolved"
                                  else "converted" if conversion.status in {"computed", "estimated"}
                                  else conversion.status)
                self.assertEqual(actual_outcome, conversion_outcome)
                readiness = self.assess(recipe(item))
                if conversion_outcome == "non_consumed":
                    self.assertEqual(readiness["root"], "missing_quantity")
                    self.assertEqual(readiness["issues"][0]["root"], "derived_non_food_row")
                elif conversion_outcome == "marinade_requires_retention":
                    self.assertEqual(readiness["root"], "conversion_evidence_missing")
                    self.assertEqual(readiness["issues"][0]["reason"],
                                     "marinade_retention_missing")
                else:
                    self.assertEqual(readiness["root"], "ready")

    def test_33_invalid_marinade_retention_blocks_readiness_and_conversion(self):
        cases = (
            ({"retainedFraction": 1.1}, "invalid_retained_fraction"),
            ({"retainedGrams": -1}, "invalid_retained_grams"),
            ({"retainedGrams": 101}, "retained_grams_exceeds_supplied_mass"),
        )
        for extra, reason in cases:
            with self.subTest(extra=extra):
                item = ingredient(100, "g", "marinade", "chicken marinade", **extra)
                self.assertEqual(convert_ingredient_to_grams(item).reason, reason)
                result = self.assess(recipe(item))
                self.assertEqual(result["root"], "conversion_evidence_missing")
                self.assertEqual(result["issues"][0]["reason"], reason)

    def test_34_invalid_retention_evidence_blocks_every_consumption_shape(self):
        invalid_cases = (
            ({"retainedFraction": 1.1}, "invalid_retained_fraction"),
            ({"retainedFraction": -.1}, "invalid_retained_fraction"),
            ({"retainedFraction": float("nan")}, "invalid_retained_fraction"),
            ({"retainedFraction": float("inf")}, "invalid_retained_fraction"),
            ({"retainedFraction": True}, "invalid_retained_fraction"),
            ({"retainedFraction": ".5"}, "invalid_retained_fraction"),
            ({"retainedGrams": -1}, "invalid_retained_grams"),
            ({"retainedGrams": 101}, "retained_grams_exceeds_supplied_mass"),
            ({"retainedGrams": float("nan")}, "invalid_retained_grams"),
            ({"retainedGrams": float("inf")}, "invalid_retained_grams"),
            ({"retainedGrams": True}, "invalid_retained_grams"),
            ({"retainedGrams": "50"}, "invalid_retained_grams"),
            ({"retainedFraction": .5, "retainedGrams": 50},
             "conflicting_retention_evidence"),
        )
        shapes = (
            ("plain", "100 g chicken", "chicken", {}),
            ("frying", "100 g oil for frying", "oil", {}),
            ("marinade", "100 g marinade for chicken", "marinade", {}),
            ("nonconsumed", "100 g discarded brine", "brine", {}),
        )
        for shape, text, food_id, base_extra in shapes:
            for evidence, reason in invalid_cases:
                with self.subTest(shape=shape, evidence=evidence):
                    item = ingredient(100, "g", food_id, text,
                                      **base_extra, **evidence)
                    conversion = convert_ingredient_to_grams(item)
                    readiness = self.assess(recipe(item))
                    self.assertEqual(conversion.reason, reason)
                    self.assertEqual(readiness["root"], "conversion_evidence_missing")
                    self.assertEqual(readiness["issues"][0]["reason"], reason)

        for value in (1.1, -.1, float("nan"), float("inf"), True, ".5"):
            with self.subTest(shape="frying_metadata", value=value):
                item = ingredient(
                    100, "g", "oil", "100 g oil for frying",
                    fryingConsumption={"retainedFraction": value},
                )
                conversion = convert_ingredient_to_grams(item)
                readiness = self.assess(recipe(item))
                self.assertEqual(conversion.reason, "invalid_retained_fraction")
                self.assertEqual(readiness["issues"][0]["reason"],
                                 "invalid_retained_fraction")

        mixed_location_cases = (
            ({"retainedFraction": .5,
              "fryingConsumption": {"retainedFraction": True}},
             "invalid_retained_fraction"),
            ({"retainedGrams": 50,
              "fryingConsumption": {"retainedGrams": -1}},
             "invalid_retained_grams"),
            ({"retainedFraction": .5,
              "fryingConsumption": {"retainedFraction": .4}},
             "conflicting_retention_evidence"),
            ({"retainedGrams": 50,
              "fryingConsumption": {"retainedGrams": 40}},
             "conflicting_retention_evidence"),
            ({"absorbedGrams": 10, "retainedFraction": 1.1},
             "invalid_retained_fraction"),
        )
        for evidence, reason in mixed_location_cases:
            with self.subTest(shape="mixed_frying_evidence", evidence=evidence):
                item = ingredient(100, "g", "oil", "100 g oil for frying", **evidence)
                conversion = convert_ingredient_to_grams(item)
                readiness = self.assess(recipe(item))
                self.assertEqual(conversion.reason, reason)
                self.assertEqual(readiness["root"], "conversion_evidence_missing")
                self.assertEqual(readiness["issues"][0]["reason"], reason)

        explicit = ingredient(
            100, "g", "stock", "100 g stock", nonConsumed=True,
            retainedFraction=float("nan"), retainedGrams=-1,
        )
        self.assertEqual(convert_ingredient_to_grams(explicit).status, "non_consumed")
        self.assertEqual(self.assess(recipe(explicit))["issues"][0]["root"],
                         "derived_non_food_row")

    def test_35_valid_retention_bounds_agree_in_readiness_and_conversion(self):
        cases = (
            ({"retainedFraction": 0}, 0),
            ({"retainedFraction": 1}, 100),
            ({"retainedGrams": 0}, 0),
            ({"retainedGrams": 100}, 100),
        )
        for evidence, expected_grams in cases:
            with self.subTest(evidence=evidence):
                item = ingredient(100, "g", "chicken", "100 g chicken", **evidence)
                conversion = convert_ingredient_to_grams(item)
                self.assertEqual(conversion.grams, expected_grams)
                self.assertEqual(self.assess(recipe(item))["root"], "ready")

    def test_36_bilingual_marinade_punctuation_is_conservative_and_differential(self):
        requires_retention = (
            "100 g marinade, for chicken", "100 g marinade (for chicken)",
            "100 g marinade: for chicken", "100 g marinade - for chicken",
            "100 g marinade — for chicken", "100 g marinada, para pollo",
            "100 g marinada (para pollo)", "100 g marinada: de pollo",
            "100 g marinada - de pollo", "100 g marinada — para pollo",
        )
        consumed = (
            "100 g marinade seasoning", "100 g condimento para marinada",
            "100 g chicken with marinade", "100 g chicken, with marinade",
            "100 g pollo con marinada", "100 g pollo: con marinada",
        )
        for text in requires_retention:
            with self.subTest(text=text):
                item = ingredient(100, "g", "marinade", text)
                conversion = convert_ingredient_to_grams(item)
                readiness = self.assess(recipe(item))
                self.assertEqual(conversion.reason, "marinade_requires_retention")
                self.assertEqual(readiness["root"], "conversion_evidence_missing")
                self.assertEqual(readiness["issues"][0]["reason"],
                                 "marinade_retention_missing")

        for text in consumed:
            with self.subTest(text=text):
                item = ingredient(100, "g", "food", text)
                self.assertEqual(convert_ingredient_to_grams(item).grams, 100)
                self.assertEqual(self.assess(recipe(item))["root"], "ready")

    def test_37_absorbed_evidence_type_boundary_and_precedence_matrix(self):
        invalid_values = (
            True, False, "0", "12", [], {}, (), set(), float("nan"), float("inf"),
            float("-inf"), -1,
        )
        for location in ("top_level", "frying_metadata"):
            for value in invalid_values:
                with self.subTest(location=location, value=repr(value)):
                    evidence = ({"absorbedGrams": value} if location == "top_level" else {
                        "fryingConsumption": {"absorbedGrams": value},
                    })
                    item = ingredient(100, "g", "oil", "100 g oil for frying", **evidence)
                    conversion = convert_ingredient_to_grams(item)
                    readiness = self.assess(recipe(item))
                    self.assertEqual(conversion.status, "unresolved")
                    self.assertEqual(conversion.reason, "invalid_absorbed_grams")
                    self.assertEqual(readiness["root"], "conversion_evidence_missing")
                    self.assertEqual(readiness["issues"][0]["reason"], conversion.reason)

        for location in ("top_level", "frying_metadata"):
            for value in (0, 0.0, 100, 100.0):
                with self.subTest(location=location, boundary=value):
                    evidence = ({"absorbedGrams": value} if location == "top_level" else {
                        "fryingConsumption": {"absorbedGrams": value},
                    })
                    item = ingredient(100, "g", "oil", "100 g oil for frying", **evidence)
                    conversion = convert_ingredient_to_grams(item)
                    self.assertEqual(conversion.status, "estimated")
                    self.assertEqual(conversion.grams, value)
                    self.assertEqual(self.assess(recipe(item))["root"], "ready")

        for location in ("top_level", "frying_metadata"):
            with self.subTest(location=location, boundary="exceeds"):
                evidence = ({"absorbedGrams": 100.0001} if location == "top_level" else {
                    "fryingConsumption": {"absorbedGrams": 100.0001},
                })
                item = ingredient(100, "g", "oil", "100 g oil for frying", **evidence)
                conversion = convert_ingredient_to_grams(item)
                readiness = self.assess(recipe(item))
                self.assertEqual(conversion.reason, "absorbed_grams_exceeds_supplied_mass")
                self.assertEqual(readiness["root"], "conversion_evidence_missing")
                self.assertEqual(readiness["issues"][0]["reason"], conversion.reason)

        precedence_cases = (
            ({"absorbedGrams": 12, "fryingConsumption": {"absorbedGrams": 8}}, 12),
            ({"absorbedGrams": None, "fryingConsumption": {"absorbedGrams": 8}}, 8),
            ({"absorbedGrams": 12,
              "fryingConsumption": {"absorbedGrams": "invalid"}}, 12),
        )
        for evidence, expected in precedence_cases:
            with self.subTest(precedence=evidence):
                item = ingredient(100, "g", "oil", "100 g oil for frying", **evidence)
                conversion = convert_ingredient_to_grams(item)
                self.assertEqual(conversion.grams, expected)
                self.assertEqual(self.assess(recipe(item))["root"], "ready")

        invalid_precedence = ingredient(
            100, "g", "oil", "100 g oil for frying", absorbedGrams="invalid",
            fryingConsumption={"absorbedGrams": 8},
        )
        self.assertEqual(convert_ingredient_to_grams(invalid_precedence).reason,
                         "invalid_absorbed_grams")
        self.assertEqual(self.assess(recipe(invalid_precedence))["issues"][0]["reason"],
                         "invalid_absorbed_grams")

        for retained in ({"retainedFraction": .5}, {"retainedGrams": 50}):
            with self.subTest(conflict=retained):
                item = ingredient(
                    100, "g", "oil", "100 g oil for frying", absorbedGrams=10, **retained,
                )
                conversion = convert_ingredient_to_grams(item)
                readiness = self.assess(recipe(item))
                self.assertEqual(conversion.reason, "conflicting_retention_evidence")
                self.assertEqual(readiness["root"], "conversion_evidence_missing")
                self.assertEqual(readiness["issues"][0]["reason"], conversion.reason)

    def test_38_unicode_separator_token_grammar_is_exact_and_source_preserving(self):
        separators = (
            ";", "/", "...", "…", "[", "]", "(", ")", "{", "}", ":", ",",
            "-", "–", "—", "―", "·", "•", "¿", "?", "¡", "!", "\\", "|", "_",
        )
        templates = (
            "100 g marinade{separator}for chicken",
            "100 g marináda{separator}para pollo",
            "100 g marinada{separator}de pollo",
        )
        for separator in separators:
            for template in templates:
                text = template.format(separator=separator)
                with self.subTest(separator=separator, text=text):
                    item = ingredient(100, "g", "marinade", text)
                    before = copy.deepcopy(item)
                    conversion = convert_ingredient_to_grams(item)
                    readiness = self.assess(recipe(item))
                    self.assertEqual(conversion.reason, "marinade_requires_retention")
                    self.assertEqual(readiness["issues"][0]["reason"],
                                     "marinade_retention_missing")
                    self.assertEqual(item, before)

        safe_suffixes = (
            "100 g chicken; marinade", "100 g pollo/ marinada",
            "100 g chicken […] marinade", "100 g pollo—marinada",
        )
        false_positives = (
            "100 g marinade; seasoning", "100 g marinade/spice",
            "100 g condimento; para marinada", "100 g chicken; with marinade",
            "100 g pollo/con marinada", "100 g chicken marinade seasoning",
            "100 g pollo marinada picante",
        )
        for text in safe_suffixes:
            with self.subTest(suffix=text):
                item = ingredient(100, "g", "marinade", text)
                self.assertEqual(convert_ingredient_to_grams(item).reason,
                                 "marinade_requires_retention")
                self.assertEqual(self.assess(recipe(item))["root"],
                                 "conversion_evidence_missing")
        for text in false_positives:
            with self.subTest(false_positive=text):
                item = ingredient(100, "g", "food", text)
                self.assertEqual(convert_ingredient_to_grams(item).grams, 100)
                self.assertEqual(self.assess(recipe(item))["root"], "ready")

    def test_39_retention_evidence_precedence_is_shared_for_every_combination(self):
        self.assertEqual(RETENTION_EVIDENCE_PRECEDENCE, (
            "invalid_retained_fraction",
            "invalid_retained_grams",
            "invalid_absorbed_grams",
            "conflicting_retention_evidence",
            "retained_grams_exceeds_supplied_mass",
            "absorbed_grams_exceeds_supplied_mass",
        ))
        missing = object()
        fraction_values = (missing, .5, 1.1, "0.5", float("nan"))
        retained_values = (missing, 50, 101, -1, "50", float("inf"))
        absorbed_values = (missing, 10, 101, -1, "10", float("nan"))
        layouts = (
            ("top", "top", "top"),
            ("frying", "frying", "frying"),
            ("top", "frying", "top"),
            ("frying", "top", "frying"),
        )

        def invalid(value, *, fraction=False):
            if value is missing:
                return False
            valid_number = (
                isinstance(value, (int, float))
                and not isinstance(value, bool)
                and math.isfinite(value)
            )
            return (not valid_number or value < 0
                    or (fraction and value > 1))

        def expected_reason(fraction, retained, absorbed):
            violations = set()
            if invalid(fraction, fraction=True):
                violations.add("invalid_retained_fraction")
            if invalid(retained):
                violations.add("invalid_retained_grams")
            if invalid(absorbed):
                violations.add("invalid_absorbed_grams")
            if ((fraction is not missing and retained is not missing)
                    or (absorbed is not missing
                        and (fraction is not missing or retained is not missing))):
                violations.add("conflicting_retention_evidence")
            if not invalid(retained) and retained is not missing and retained > 100:
                violations.add("retained_grams_exceeds_supplied_mass")
            if not invalid(absorbed) and absorbed is not missing and absorbed > 100:
                violations.add("absorbed_grams_exceeds_supplied_mass")
            return next((reason for reason in RETENTION_EVIDENCE_PRECEDENCE
                         if reason in violations), None)

        for values in product(fraction_values, retained_values, absorbed_values):
            fraction, retained, absorbed = values
            expected = expected_reason(*values)
            for locations in layouts:
                top = {}
                frying = {}
                for key, value, location in zip(
                    ("retainedFraction", "retainedGrams", "absorbedGrams"),
                    values, locations,
                ):
                    if value is not missing:
                        (top if location == "top" else frying)[key] = value
                evidence = {**top}
                if frying:
                    evidence["fryingConsumption"] = frying
                with self.subTest(values=tuple(map(repr, values)), locations=locations):
                    early = classify_consumption(evidence, "oil for frying", ())
                    mass_aware = classify_consumption(
                        evidence, "oil for frying", (), known_supplied_grams=100,
                    )
                    if expected is None:
                        self.assertNotEqual(mass_aware.status, "invalid_retention")
                        continue
                    self.assertEqual(mass_aware.reason, expected)
                    if "exceeds_supplied_mass" not in expected:
                        self.assertEqual(early.reason, expected)
                    item = ingredient(
                        100, "g", "oil", "100 g oil for frying", **evidence,
                    )
                    conversion = convert_ingredient_to_grams(item)
                    readiness = self.assess(recipe(item))
                    self.assertEqual(conversion.reason, expected)
                    self.assertEqual(readiness["root"], "conversion_evidence_missing")
                    self.assertEqual(readiness["issues"][0]["reason"], expected)

    def test_invariant_readiness_never_ready_when_conversion_unresolved_for_consumption_evidence(self):
        semantic_fixtures = (
            ingredient(100, "g", "oil", "100 g oil for frying"),
            ingredient(100, "g", "oil", "100 g oil for frying", absorbedGrams=True),
            ingredient(100, "g", "oil", "100 g oil for frying", absorbedGrams=-1),
            ingredient(100, "g", "oil", "100 g oil for frying", absorbedGrams=101),
            ingredient(100, "g", "oil", "100 g oil for frying",
                       fryingConsumption={"absorbedGrams": "12"}),
            ingredient(100, "g", "oil", "100 g oil for frying",
                       absorbedGrams=10, retainedFraction=.5),
            ingredient(100, "g", "marinade", "100 g marinade; for chicken"),
            ingredient(100, "g", "marinade", "100 g marinada[…]para pollo"),
            ingredient(100, "g", "marinade", "100 g chicken/marinade"),
        )
        consumption_reasons = {
            "frying_oil_requires_absorption", "invalid_absorbed_grams",
            "absorbed_grams_exceeds_supplied_mass", "conflicting_retention_evidence",
            "marinade_requires_retention",
        }
        for item in semantic_fixtures:
            with self.subTest(text=item["originalText"], evidence=item):
                conversion = convert_ingredient_to_grams(item)
                self.assertEqual(conversion.status, "unresolved")
                self.assertIn(conversion.reason, consumption_reasons)
                self.assertFalse(self.assess(recipe(item))["ready"])

    def test_unavailable_evidence_reason_is_bounded_and_visible_to_operations(self):
        deserialize_failure = self.assess(
            recipe(ingredient()),
            ReadinessEvidence(reason_code="snapshot_deserialize_failed"),
        )
        invalid_snapshot = self.assess(
            recipe(ingredient()),
            ReadinessEvidence(reason_code="snapshot_invalid"),
        )
        self.assertEqual(deserialize_failure["evidence"], {
            "status": "indeterminate",
            "schemaVersion": None,
            "reasonCode": "snapshot_deserialize_failed",
            "publicationStatus": "unknown",
        })
        self.assertNotEqual(deserialize_failure["fingerprint"], invalid_snapshot["fingerprint"])

        rollback_journal = self.assess(
            recipe(ingredient()),
            ReadinessEvidence(reason_code="rollback_journal_active"),
        )
        self.assertEqual(rollback_journal["evidence"]["status"], "indeterminate")
        self.assertEqual(
            rollback_journal["evidence"]["reasonCode"], "rollback_journal_active",
        )
        self.assertTrue(rollback_journal["gates"]["sourceStructure"])

    def test_empty_nutrition_does_not_create_publication_conflict(self):
        empty_values = {
            "calories": None, "proteinContent": "", "fatContent": "   ",
        }
        result = self.assess(recipe(ingredient(), nutrition=empty_values))
        self.assertEqual(result["root"], "ready")
        self.assertTrue(result["gates"]["publication"])
        self.assertNotIn("manual_publication_conflict", result["rootCounts"])
        self.assertEqual(result["publicationProvenance"], "absent")

    def test_malformed_current_nutrition_blocks_publication(self):
        for value in ({}, [], True, 100, "100 g", "NaN kcal", "100\nkcal"):
            with self.subTest(value=value):
                result = self.assess(
                    recipe(ingredient(), nutrition={"calories": value}),
                    ReadinessEvidence(status="available", foods=(FoodEvidence("food"),)),
                )

                self.assertEqual(result["publicationProvenance"], "unknown")
                self.assertFalse(result["gates"]["publication"])
                self.assertIn("manual_publication_conflict", result["rootCounts"])

    def test_sabor_owned_current_publication_is_not_a_conflict(self):
        result = self.assess(
            recipe(ingredient(), nutrition={"calories": "100 kcal"}),
            ReadinessEvidence(status="available", foods=(FoodEvidence("food"),),
                              publication_status="owned"),
        )
        self.assertEqual(result["root"], "ready")
        self.assertTrue(result["gates"]["publication"])
        self.assertEqual(result["publicationProvenance"], "owned")

    def test_explicit_protection_outranks_matching_publication_history(self):
        result = self.assess(
            recipe(
                ingredient(), nutrition={"calories": "100 kcal"},
                extras={"saborNutritionProtected": True},
            ),
            ReadinessEvidence(
                status="available", foods=(FoodEvidence("food"),),
                publication_status="owned",
            ),
        )
        self.assertEqual(result["publicationProvenance"], "protected")
        self.assertFalse(result["gates"]["publication"])

    def test_edited_after_publication_remains_publication_only_conflict(self):
        result = self.assess(
            recipe(ingredient(), nutrition={"calories": "101 kcal"}),
            ReadinessEvidence(status="available", foods=(FoodEvidence("food"),),
                              publication_status="mismatch"),
        )
        conflict = next(issue for issue in result["issues"]
                        if issue["root"] == "manual_publication_conflict")
        self.assertEqual(conflict["reason"], "existing_nutrition_review")
        self.assertTrue(result["gates"]["totalCalculation"])
        self.assertTrue(result["gates"]["backfill"])
        self.assertFalse(result["gates"]["publication"])

    def test_pollo_structured_missing_food_requires_selection_not_parser_repair(self):
        pollo = ingredient(
            300, "g", None, "300 g pechuga de pollo en dados",
            id="pollo-sanitized-missing-food",
        )
        result = self.assess(recipe(pollo))
        self.assertEqual(result["root"], "food_selection_required")
        self.assertNotIn("parser_loss", result["rootCounts"])

    def test_low_confidence_conversion_is_found_and_calculation_usable(self):
        evidence = ReadinessEvidence(
            status="available",
            foods=(FoodEvidence("oil", conversion_status="low_confidence"),),
        )
        result = self.assess(recipe(ingredient(1, "tbsp", "oil", "1 tbsp oil")), evidence)
        self.assertEqual(result["root"], "ready")
        self.assertTrue(result["gates"]["totalCalculation"])
        self.assertTrue(result["gates"]["backfill"])
        self.assertFalse(result["gates"]["publication"])
        self.assertIn("low_confidence_conversion", {
            warning["reason"] for warning in result["warnings"]
        })

    def test_gyudon_manual_resolution_is_usable_below_profile_threshold(self):
        evidence = ReadinessEvidence(
            status="available",
            foods=(FoodEvidence(
                "broth", resolution_status="low_confidence",
                profile_status="ready", conversion_status="low_confidence",
            ),),
        )
        gyudon = ingredient(3, "tsp", "broth", "3 tsp dashi broth")
        result = self.assess(recipe(gyudon), evidence)
        self.assertEqual(result["root"], "ready")
        self.assertTrue(result["gates"]["totalCalculation"])
        self.assertTrue(result["gates"]["backfill"])
        self.assertFalse(result["gates"]["publication"])
        self.assertEqual({warning["reason"] for warning in result["warnings"]}, {
            "low_confidence_conversion", "low_confidence_resolution",
        })

    def test_source_structure_and_runtime_evidence_gates_are_separate(self):
        missing = self.assess(
            recipe(ingredient(1, "tbsp", "oil", "1 tbsp oil")),
            ReadinessEvidence(status="available", foods=(FoodEvidence(
                "oil", conversion_status="missing", profile_status="missing",
            ),)),
        )
        self.assertTrue(missing["gates"]["sourceStructure"])
        self.assertFalse(missing["gates"]["evidence"])
        self.assertFalse(missing["gates"]["totalCalculation"])

        missing_servings = self.assess(recipe(ingredient(), servings=None))
        self.assertFalse(missing_servings["gates"]["sourceStructure"])

    def test_successful_publication_provenance_does_not_override_structural_gaps(self):
        evidence = ReadinessEvidence(
            status="available", publication_status="owned", foods=(),
        )
        result = self.assess(
            recipe(ingredient(None, "g", None, "2 g pollo"),
                   nutrition={"calories": "100 kcal"}),
            evidence,
        )
        self.assertEqual(result["root"], "parser_loss")
        self.assertFalse(result["gates"]["sourceStructure"])
        self.assertFalse(result["gates"]["totalCalculation"])
        self.assertFalse(result["gates"]["backfill"])
        self.assertFalse(result["gates"]["publication"])


if __name__ == "__main__":
    unittest.main()
