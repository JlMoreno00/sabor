import unittest

from nutrition.conversions import (CURATED_PIECE_WEIGHTS, ConversionContext,
                                   convert_ingredient_to_grams,
                                   is_title_only_section_heading)


def ingredient(quantity, unit, food="food", text="ingredient", **extra):
    value = {
        "quantity": quantity, "unit": {"name": unit},
        "food": {"id": food, "name": food}, "originalText": text,
    }
    value.update(extra)
    return value


def mealie_unit(name, abbreviation=None, **extra):
    value = {"id": f"unit-{name}", "name": name, "abbreviation": abbreviation}
    value.update(extra)
    return value


class NutritionConversionTests(unittest.TestCase):
    def test_title_only_heading_accepts_only_missing_or_finite_zero_quantity(self):
        base = {"title": "Sauce", "unit": None, "food": None, "originalText": "Sauce"}
        for quantity in (None, 0, 0.0, -0.0):
            with self.subTest(quantity=quantity):
                value = {**base, "quantity": quantity}
                self.assertTrue(is_title_only_section_heading(value))
                self.assertEqual(convert_ingredient_to_grams(value).status, "excluded")

        non_headings = (
            {**base, "title": "   ", "quantity": 0.0, "originalText": "For the sauce"},
            {**base, "quantity": 0.0, "unit": {"name": "gram"}},
            {**base, "quantity": 0.0, "food": {"id": "f", "name": "tomato"}},
            *({**base, "quantity": quantity}
              for quantity in (False, "0", 1, -1, float("inf"), float("-inf"), float("nan"))),
        )
        for value in non_headings:
            with self.subTest(value=value):
                self.assertFalse(is_title_only_section_heading(value))
                self.assertEqual(convert_ingredient_to_grams(value).status, "unresolved")

    def test_direct_mass_units_and_invalid_quantity(self):
        self.assertEqual(convert_ingredient_to_grams(ingredient(1000, "mg")).grams, 1)
        self.assertEqual(convert_ingredient_to_grams(ingredient(2, "g")).grams, 2)
        self.assertEqual(convert_ingredient_to_grams(ingredient(1.5, "kg")).grams, 1500)
        self.assertEqual(convert_ingredient_to_grams(ingredient(0, "g")).status, "unresolved")
        self.assertEqual(convert_ingredient_to_grams(ingredient(-1, "g")).status, "unresolved")

    def test_volume_requires_density_and_supports_all_factors(self):
        context = ConversionContext(conversions={
            ("food", "density", "ml"): {"grams_per_unit": .91, "confidence": .95}
        })
        expected = {"ml": .91, "cl": 9.1, "dl": 91, "l": 910,
                    "cucharadita": 4.55, "cucharada": 13.65, "taza": 218.4}
        for unit, grams in expected.items():
            with self.subTest(unit=unit):
                self.assertAlmostEqual(convert_ingredient_to_grams(ingredient(1, unit), context).grams, grams)
        self.assertEqual(convert_ingredient_to_grams(ingredient(10, "ml")).status, "unresolved")

    def test_piece_package_and_override_precedence(self):
        context = ConversionContext(conversions={
            ("egg", "piece_weight", "piece"): {"grams_per_unit": 55, "confidence": .9},
            ("beans", "package_weight", "can"): {"grams_per_unit": 400, "confidence": 1},
            ("bread", "culinary_measure", "slice"): {"grams_per_unit": 30, "confidence": .9},
        })
        self.assertEqual(convert_ingredient_to_grams(ingredient(2, "unidad", "egg"), context).grams, 110)
        self.assertEqual(convert_ingredient_to_grams(ingredient(1, "lata", "beans"), context).grams, 400)
        self.assertEqual(convert_ingredient_to_grams(ingredient(2, "rebanada", "bread"), context).grams, 60)
        self.assertEqual(convert_ingredient_to_grams(ingredient(1, "bolsa", "unknown"), context).status, "unresolved")

    def test_ranges_alternatives_optional_and_non_consumed_semantics(self):
        ranged = convert_ingredient_to_grams(ingredient({"min": 2, "max": 4}, "g"))
        self.assertEqual(ranged.grams, 3)
        self.assertIn("range:2-4", ranged.assumptions)
        self.assertEqual(convert_ingredient_to_grams(ingredient(1, "g", text="tomato o pepper")).status, "computed")
        self.assertEqual(convert_ingredient_to_grams(ingredient(1, "g", optional=True)).status, "optional")
        self.assertEqual(convert_ingredient_to_grams(ingredient(1, "l", text="agua de coccion")).status, "non_consumed")
        self.assertEqual(convert_ingredient_to_grams(
            ingredient(10, "ml", food="oil", text="aceite para freir")
        ).reason, "frying_oil_requires_absorption")
        self.assertEqual(convert_ingredient_to_grams(ingredient(10, "ml", text="marinada")).reason, "marinade_requires_retention")

    def test_curated_piece_defaults_include_source_and_confidence(self):
        planned = {"egg", "garlic clove", "onion", "lemon", "lime", "tomato",
                   "chicken breast", "tortilla"}
        self.assertTrue(planned.issubset(CURATED_PIECE_WEIGHTS))
        for name in planned:
            with self.subTest(name=name):
                default = CURATED_PIECE_WEIGHTS[name]
                self.assertGreater(default["grams_per_unit"], 0)
                self.assertGreater(default["confidence"], 0)
                self.assertLess(default["confidence"], 1)
                self.assertTrue(default["source_note"])

    def test_db_piece_conversion_precedes_curated_default_and_unknown_is_unresolved(self):
        context = ConversionContext(conversions={
            ("egg-id", "piece_weight", "piece"): {
                "grams_per_unit": 63, "confidence": 1, "provider": "manual",
            },
        })

        manual = convert_ingredient_to_grams(
            ingredient(2, "piece", "egg-id"), context,
        )
        curated = convert_ingredient_to_grams(
            ingredient(2, "piece", "egg", text="2 eggs"),
        )
        unknown = convert_ingredient_to_grams(
            ingredient(1, "piece", "other", text="1 mystery fruit"),
        )

        self.assertEqual(manual.grams, 126)
        self.assertEqual(manual.confidence, 1)
        self.assertEqual(curated.grams, CURATED_PIECE_WEIGHTS["egg"]["grams_per_unit"] * 2)
        self.assertEqual(curated.confidence, CURATED_PIECE_WEIGHTS["egg"]["confidence"])
        self.assertEqual(unknown.status, "unresolved")

    def test_actual_mealie_units_aliases_standard_units_and_fluid_ounces(self):
        context = ConversionContext(conversions={
            ("oil", "density", "ml"): {"grams_per_unit": .91, "confidence": .95},
            ("egg", "piece_weight", "piece"): {"grams_per_unit": 55, "confidence": .9},
        })
        tablespoons = ingredient(2, "", "oil")
        tablespoons["unit"] = mealie_unit("Cucharadas", "cda")
        fluid_ounce = ingredient(1, "", "oil")
        fluid_ounce["unit"] = mealie_unit(
            "Fluid Ounce", "fl oz", standardQuantity=29.5735, standardUnit="milliliter",
        )
        scoop = ingredient(2, "", "oil")
        scoop["unit"] = mealie_unit(
            "Scoop", None, standardQuantity=15, standardUnit="milliliter",
        )
        pieces = ingredient(2, "", "egg")
        pieces["unit"] = mealie_unit("Unidades", "uds")
        teaspoons = []
        for abbreviation in ("cdta", "cdtas"):
            value = ingredient(2, "", "oil")
            value["unit"] = mealie_unit("Cucharadita", abbreviation)
            teaspoons.append(value)

        self.assertAlmostEqual(convert_ingredient_to_grams(tablespoons, context).grams, 27.3)
        self.assertAlmostEqual(convert_ingredient_to_grams(fluid_ounce, context).grams, 26.911885)
        self.assertAlmostEqual(convert_ingredient_to_grams(scoop, context).grams, 27.3)
        self.assertEqual(convert_ingredient_to_grams(pieces, context).grams, 110)
        for value in teaspoons:
            with self.subTest(abbreviation=value["unit"]["abbreviation"]):
                self.assertAlmostEqual(convert_ingredient_to_grams(value, context).grams, 9.1)

    def test_unitless_piece_inference_is_conservative(self):
        context = ConversionContext(conversions={
            ("egg", "piece_weight", "piece"): {"grams_per_unit": 55, "confidence": .9},
        })
        inferred = ingredient(2, "", "egg", text="2 eggs")
        inferred["unit"] = None
        contrary = ingredient(2, "", "egg", text="2 kg eggs")
        contrary["unit"] = None
        tablespoons = ingredient(2, "", "egg", text="2 tbsp beaten egg")
        tablespoons["unit"] = None
        unlinked = ingredient(2, "", "", text="2 eggs")
        unlinked["food"] = None
        unlinked["unit"] = None

        self.assertEqual(convert_ingredient_to_grams(inferred, context).grams, 110)
        self.assertEqual(convert_ingredient_to_grams(contrary, context).reason, "conversion_missing")
        self.assertEqual(convert_ingredient_to_grams(tablespoons, context).reason, "conversion_missing")
        self.assertEqual(convert_ingredient_to_grams(unlinked, context).reason, "conversion_missing")

    def test_unitless_piece_inference_rejects_full_measure_vocabulary(self):
        context = ConversionContext(conversions={
            ("egg", "piece_weight", "piece"): {"grams_per_unit": 55, "confidence": .9},
        })
        measures = (
            "tablespoon", "tablespoons", "cucharada", "cucharadas", "tbsp", "cda", "cdas",
            "teaspoon", "teaspoons", "cucharadita", "cucharaditas", "tsp", "cdta", "cdtas",
            "cdita", "cditas", "cup", "cups", "taza", "tazas",
            "fluid ounce", "fluid ounces", "fl oz", "fl. oz.", "onza liquida", "onzas liquidas",
            "slice", "slices", "rebanada", "rebanadas", "rodaja", "rodajas", "loncha", "lonchas",
            "handful", "handfuls", "punado", "punados", "pinch", "pinches", "pizca", "pizcas",
            "splash", "splashes", "chorrito", "chorritos", "leaf", "leaves", "hoja", "hojas",
            "package", "packages", "paquete", "paquetes", "can", "cans", "lata", "latas",
            "jar", "jars", "tarro", "tarros", "bag", "bags", "bolsa", "bolsas",
            "packet", "packets", "sobre", "sobres", "bottle", "bottles", "botella", "botellas",
            "bunch", "bunches", "manojo", "manojos",
        )

        for measure in measures:
            with self.subTest(measure=measure):
                value = ingredient(2, "", "egg", text=f"2 {measure} egg")
                value["unit"] = None
                result = convert_ingredient_to_grams(value, context)
                self.assertEqual(result.reason, "conversion_missing")

        for text in ("2 cucharadas de huevo", "2 fl oz beaten egg", "2 slices egg"):
            with self.subTest(text=text):
                value = ingredient(2, "", "egg", text=text)
                value["unit"] = None
                self.assertEqual(convert_ingredient_to_grams(value, context).reason,
                                 "conversion_missing")

        inferred = ingredient(2, "", "egg", text="2 eggs")
        inferred["unit"] = None
        self.assertEqual(convert_ingredient_to_grams(inferred, context).grams, 110)

    def test_leaf_measure_requires_food_specific_evidence(self):
        leaf = ingredient(3, "", "basil", text="3 basil leaves")
        leaf["unit"] = mealie_unit("Leaves", "hojas")
        self.assertEqual(convert_ingredient_to_grams(leaf).reason, "conversion_missing")
        context = ConversionContext(conversions={
            ("basil", "culinary_measure", "leaf"): {
                "grams_per_unit": .5, "confidence": .8, "source_note": "Reviewed basil leaves",
            },
        })
        self.assertEqual(convert_ingredient_to_grams(leaf, context).grams, 1.5)

    def test_source_range_alternatives_and_frying_are_conservative(self):
        ranged = ingredient(2, "g", text="2-4 g salt")
        result = convert_ingredient_to_grams(ranged)
        self.assertEqual((result.grams, result.quantity_min, result.quantity_max), (3, 2, 4))
        self.assertIn("range:2-4", result.assumptions)

        linked_note = ingredient(1, "g", "tomato", text="tomato, or diced for serving")
        substitution = ingredient(1, "g", "tomato", text="tomato or another variety")
        structured = ingredient(1, "g", "tomato", alternatives=[{"foodId": "tomato"}, {"foodId": "pepper"}])
        selected = ingredient(1, "g", "tomato", alternatives=[{"foodId": "tomato"}, {"foodId": "pepper"}], selectedAlternative="tomato")
        self.assertEqual(convert_ingredient_to_grams(linked_note).status, "computed")
        self.assertEqual(convert_ingredient_to_grams(substitution).status, "computed")
        self.assertEqual(convert_ingredient_to_grams(structured).reason, "ambiguous_alternative")
        self.assertEqual(convert_ingredient_to_grams(selected).status, "computed")

        potatoes = ingredient(200, "g", "potato", text="potatoes for frying")
        frying_oil = ingredient(100, "g", "oil", text="oil for frying")
        absorbed = ingredient(100, "g", "oil", text="oil for frying", absorbedGrams=12)
        retained = ingredient(100, "g", "oil", text="oil for frying", retainedFraction=.1)
        metadata = ingredient(100, "g", "oil", text="oil for frying",
                              fryingConsumption={"absorbedGrams": 8})
        self.assertEqual(convert_ingredient_to_grams(potatoes).grams, 200)
        self.assertEqual(convert_ingredient_to_grams(frying_oil).reason, "frying_oil_requires_absorption")
        self.assertEqual(convert_ingredient_to_grams(absorbed).grams, 12)
        self.assertEqual(convert_ingredient_to_grams(retained).grams, 10)
        self.assertEqual(convert_ingredient_to_grams(metadata).grams, 8)

        excessive_absorption = ingredient(
            10, "g", "oil", text="oil for frying", absorbedGrams=12,
        )
        negative_absorption = ingredient(
            10, "g", "oil", text="oil for frying", absorbedGrams=-1,
        )
        self.assertEqual(
            convert_ingredient_to_grams(excessive_absorption).reason,
            "absorbed_grams_exceeds_supplied_mass",
        )
        self.assertEqual(
            convert_ingredient_to_grams(negative_absorption).reason,
            "invalid_absorbed_grams",
        )

        marinade = ingredient(100, "g", "chicken", text="chicken marinade")
        marinade_retained = ingredient(100, "g", "chicken", text="chicken marinade", retainedFraction=.4)
        self.assertEqual(convert_ingredient_to_grams(marinade).reason,
                         "marinade_requires_retention")
        self.assertEqual(convert_ingredient_to_grams(marinade_retained).grams, 40)

        for text in ("marinade seasoning", "condimento para marinada"):
            with self.subTest(text=text):
                direct_mass = ingredient(100, "g", "seasoning", text=text)
                self.assertEqual(convert_ingredient_to_grams(direct_mass).grams, 100)

        for text in (
            "marinade for chicken", "Marinade FOR chicken!", "marinada para pollo",
            "MARINADA de pollo.", "chicken marinade", "Marinada de POLLO",
        ):
            with self.subTest(text=text):
                retained_unknown = ingredient(100, "g", "marinade", text=text)
                self.assertEqual(
                    convert_ingredient_to_grams(retained_unknown).reason,
                    "marinade_requires_retention",
                )

    def test_consumption_semantics_are_whole_concept_not_row_substrings(self):
        excluded = (
            "cooking water", "1 l COOKING WATER!", "agua de cocción",
            "500 ml agua de coccion.", "discarded brine", "salmuera desechada",
            "discarded liquid", "líquido desechado", "bones", "chicken bones",
            "huesos de pollo", "shells", "egg shells",
            "cáscaras de huevo", "peels", "potato peels", "pieles de patata",
        )
        for text in excluded:
            with self.subTest(text=text):
                value = ingredient(100, "g", "refuse", text=text)
                self.assertEqual(convert_ingredient_to_grams(value).status, "non_consumed")

        consumed = (
            "400 g chickpeas, discarded brine",
            "400 g garbanzos, salmuera desechada",
            "100 g chicken with marinade",
            "100 g pollo con marinada",
            "marinade seasoning",
            "marinade spice",
            "condimento para marinada",
            "chicken prepared with discarded liquid",
        )
        for text in consumed:
            with self.subTest(text=text):
                value = ingredient(100, "g", "food", text=text)
                self.assertEqual(convert_ingredient_to_grams(value).grams, 100)

        explicit = ingredient(100, "g", "food", text="100 g chicken", nonConsumed=True)
        self.assertEqual(convert_ingredient_to_grams(explicit).status, "non_consumed")

    def test_whole_concept_marinade_supports_retained_grams(self):
        retained = ingredient(100, "g", "marinade", text="chicken marinade", retainedGrams=35)
        self.assertEqual(convert_ingredient_to_grams(retained).grams, 35)
        self.assertIn("retained_grams:35", convert_ingredient_to_grams(retained).assumptions)

        invalid = ingredient(100, "g", "marinade", text="chicken marinade", retainedGrams=101)
        self.assertEqual(convert_ingredient_to_grams(invalid).reason,
                         "retained_grams_exceeds_supplied_mass")


if __name__ == "__main__":
    unittest.main()
