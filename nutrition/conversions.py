"""Deterministic ingredient quantity-to-gram conversions."""

from __future__ import annotations

import re
import math
import unicodedata
from dataclasses import dataclass, field
from typing import Any

from .consumption import classify_consumption
from .models import ConversionResult


MASS_FACTORS = {"mg": .001, "g": 1, "gram": 1, "gramo": 1,
                "kg": 1000, "kilogram": 1000, "kilogramo": 1000}
VOLUME_FACTORS = {
    "ml": 1, "mililitro": 1, "cl": 10, "centilitro": 10,
    "dl": 100, "decilitro": 100, "l": 1000, "litro": 1000,
    "teaspoon": 5, "cucharadita": 5, "tsp": 5,
    "tablespoon": 15, "cucharada": 15, "tbsp": 15,
    "cup": 240, "taza": 240,
    "fluid ounce": 29.5735,
}
PIECE_UNITS = {"piece", "unit", "unidad", "pieza", "egg", "huevo", "clove", "diente"}
PACKAGE_UNITS = {"can": "can", "lata": "can", "jar": "jar", "tarro": "jar",
                  "bag": "bag", "bolsa": "bag", "packet": "packet", "sobre": "packet",
                  "bottle": "bottle", "botella": "bottle", "bunch": "bunch", "manojo": "bunch",
                  "package": "package", "paquete": "package"}
MEASURE_UNITS = {"slice": "slice", "rebanada": "slice", "rodaja": "slice", "loncha": "slice",
                 "handful": "handful", "punado": "handful", "pinch": "pinch", "pizca": "pinch",
                   "splash": "splash", "chorrito": "splash",
                   "leaf": "leaf", "leaves": "leaf", "hoja": "leaf", "hojas": "leaf"}

UNIT_ALIASES = {
    "grams": "g", "gramos": "g", "kilograms": "kg", "kilogramos": "kg",
    "milliliter": "ml", "milliliters": "ml", "millilitre": "ml", "millilitres": "ml",
    "mililitros": "ml", "liter": "l", "liters": "l", "litre": "l", "litres": "l",
    "litros": "l", "cda": "tablespoon", "cdas": "tablespoon",
    "tablespoons": "tablespoon", "cucharadas": "tablespoon",
    "cdta": "teaspoon", "cdtas": "teaspoon",
    "cdita": "teaspoon", "cditas": "teaspoon", "teaspoons": "teaspoon",
    "cucharaditas": "teaspoon",
    "ud": "piece", "uds": "piece", "unidades": "piece", "pieces": "piece",
    "fl oz": "fluid ounce", "fl. oz.": "fluid ounce", "fluid ounces": "fluid ounce",
    "onza liquida": "fluid ounce", "onzas liquidas": "fluid ounce",
    "cups": "cup", "tazas": "cup",
    "slices": "slice", "rebanadas": "slice", "rodajas": "slice", "lonchas": "slice",
    "handfuls": "handful", "punados": "handful", "pinches": "pinch", "pizcas": "pinch",
    "splashes": "splash", "chorritos": "splash",
    "packages": "package", "paquetes": "package", "cans": "can", "latas": "can",
    "jars": "jar", "tarros": "jar", "bags": "bag", "bolsas": "bag",
    "packets": "packet", "sobres": "packet", "bottles": "bottle", "botellas": "bottle",
    "bunches": "bunch", "manojos": "bunch",
}

CONTRARY_MEASURE_UNITS = (
    set(MASS_FACTORS) | set(VOLUME_FACTORS) | set(PACKAGE_UNITS) | set(MEASURE_UNITS)
)
CONTRARY_MEASURE_UNITS.update(
    alias for alias, canonical in UNIT_ALIASES.items()
    if canonical in CONTRARY_MEASURE_UNITS
)
CONSUMPTION_UNIT_TOKENS = (
    CONTRARY_MEASURE_UNITS | set(PIECE_UNITS) | set(UNIT_ALIASES.values())
)
MEASURE_TOKEN_RE = re.compile(
    r"(?<!\w)(?:" + "|".join(
        re.escape(unit) for unit in sorted(CONTRARY_MEASURE_UNITS, key=len, reverse=True)
    ) + r")(?!\w)"
)
HEADING_PREFIX_RE = re.compile(r"^(?:for|para)\s+(?:(?:the|el|la|los|las)\s+)?\S+(?:\s+\S+){0,3}:?$")

CURATED_PIECE_WEIGHTS = {
    "egg": {"grams_per_unit": 50, "confidence": .90,
            "source_note": "USDA FoodData Central household measure: large whole egg"},
    "garlic clove": {"grams_per_unit": 3, "confidence": .85,
                     "source_note": "USDA FoodData Central household measure: garlic clove"},
    "onion": {"grams_per_unit": 110, "confidence": .80,
              "source_note": "USDA FoodData Central household measure: medium onion"},
    "lemon": {"grams_per_unit": 58, "confidence": .80,
              "source_note": "USDA FoodData Central household measure: lemon without refuse"},
    "lime": {"grams_per_unit": 67, "confidence": .80,
             "source_note": "USDA FoodData Central household measure: lime without refuse"},
    "tomato": {"grams_per_unit": 123, "confidence": .80,
               "source_note": "USDA FoodData Central household measure: medium tomato"},
    "chicken breast": {"grams_per_unit": 174, "confidence": .65,
                       "source_note": "USDA FoodData Central household measure: boneless breast"},
    "tortilla": {"grams_per_unit": 45, "confidence": .70,
                 "source_note": "USDA FoodData Central household measure: medium flour tortilla"},
}

PIECE_FOOD_ALIASES = {
    "egg": "egg", "huevo": "egg",
    "garlic": "garlic clove", "garlic clove": "garlic clove",
    "ajo": "garlic clove", "diente de ajo": "garlic clove",
    "onion": "onion", "cebolla": "onion",
    "lemon": "lemon", "limon": "lemon", "lime": "lime", "lima": "lime",
    "tomato": "tomato", "tomate": "tomato",
    "chicken breast": "chicken breast", "pechuga de pollo": "chicken breast",
    "tortilla": "tortilla", "wrap": "tortilla",
}


def normalize_unit(value: Any) -> str:
    text = " ".join(str(value or "").split()).casefold()
    return "".join(char for char in unicodedata.normalize("NFKD", text)
                   if not unicodedata.combining(char))


def canonical_unit(value: Any) -> tuple[str, float]:
    if not isinstance(value, dict):
        key = normalize_unit(value)
        return UNIT_ALIASES.get(key, key), 1
    raw = normalize_unit(value.get("abbreviation") or value.get("name"))
    key = UNIT_ALIASES.get(raw, raw)
    standard_quantity = value.get("standardQuantity")
    standard_unit = value.get("standardUnit")
    if standard_quantity is not None and standard_unit:
        standard_raw = (standard_unit.get("abbreviation") or standard_unit.get("name")
                        if isinstance(standard_unit, dict) else standard_unit)
        standard_key = normalize_unit(standard_raw)
        standard_key = UNIT_ALIASES.get(standard_key, standard_key)
        try:
            multiplier = float(standard_quantity)
        except (TypeError, ValueError):
            multiplier = 0
        if multiplier > 0 and (standard_key in MASS_FACTORS or standard_key in VOLUME_FACTORS):
            return standard_key, multiplier
    return key, 1


@dataclass
class ConversionContext:
    conversions: dict[tuple[str, str, str], dict[str, Any]] = field(default_factory=dict)
    default_cup_ml: float = 240

    def find(self, food_id: str, kind: str, unit: str) -> dict[str, Any] | None:
        return self.conversions.get((food_id, kind, unit)) or self.conversions.get(("", kind, unit))


def _quantity(value: Any) -> tuple[float | None, float | None, float | None]:
    if isinstance(value, dict):
        try:
            minimum, maximum = float(value["min"]), float(value["max"])
            return (minimum + maximum) / 2, minimum, maximum
        except (KeyError, TypeError, ValueError):
            return None, None, None
    if isinstance(value, str):
        match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*[-–]\s*(\d+(?:\.\d+)?)\s*", value)
        if match:
            minimum, maximum = map(float, match.groups())
            return (minimum + maximum) / 2, minimum, maximum
    try:
        return float(value), None, None
    except (TypeError, ValueError):
        return None, None, None


def is_title_only_section_heading(ingredient: dict[str, Any]) -> bool:
    quantity = ingredient.get("quantity")
    quantity_is_absent = quantity is None or (
        isinstance(quantity, (int, float))
        and not isinstance(quantity, bool)
        and math.isfinite(float(quantity))
        and float(quantity) == 0
    )
    return (
        bool(str(ingredient.get("title") or "").strip())
        and quantity_is_absent
        and not ingredient.get("unit")
        and not ingredient.get("food")
    )


def _is_section_heading(ingredient: dict[str, Any], text: str, food_id: str,
                        unit: str) -> bool:
    explicit = any(ingredient.get(key) is True for key in
                   ("isSectionHeader", "isSection", "isHeading"))
    explicit = explicit or normalize_unit(ingredient.get("type")) in {"section", "heading"}
    if explicit:
        return True
    if is_title_only_section_heading(ingredient):
        return True
    empty_shape = ingredient.get("quantity") is None and not food_id and not unit
    return bool(empty_shape and HEADING_PREFIX_RE.fullmatch(text))


def _known_supplied_grams(quantity: float, unit: str, food_id: str,
                          context: ConversionContext) -> float | None:
    if unit in MASS_FACTORS:
        return quantity * MASS_FACTORS[unit]
    if unit not in VOLUME_FACTORS:
        return None
    specific = context.find(food_id, "culinary_measure", unit)
    if specific:
        return quantity * specific["grams_per_unit"]
    density = context.find(food_id, "density", "ml")
    if not density:
        return None
    factor = context.default_cup_ml if unit == "cup" else VOLUME_FACTORS[unit]
    return quantity * factor * density["grams_per_unit"]


def convert_ingredient_to_grams(ingredient: dict[str, Any],
                                context: ConversionContext | None = None) -> ConversionResult:
    context = context or ConversionContext()
    text = normalize_unit(ingredient.get("originalText") or ingredient.get("display") or "")
    food = ingredient.get("food") or {}
    food_id = str(food.get("id") or "")
    food_name = normalize_unit(food.get("name"))
    unit_data = ingredient.get("unit") or {}
    unit, unit_multiplier = canonical_unit(unit_data)
    if _is_section_heading(ingredient, text, food_id, unit):
        return ConversionResult("excluded", reason="section_heading")
    if ingredient.get("optional") and not ingredient.get("included"):
        return ConversionResult("optional", reason="optional_excluded")
    consumption = classify_consumption(ingredient, text, CONSUMPTION_UNIT_TOKENS)
    if consumption.status == "invalid_retention":
        return ConversionResult("unresolved", reason=consumption.reason)
    if (consumption.status == "excluded"
            and consumption.retained_fraction is None
            and consumption.retained_grams is None):
        return ConversionResult("non_consumed", reason="not_consumed")
    alternatives = ingredient.get("alternatives") or ingredient.get("alternativeFoods") or []
    if alternatives and len(alternatives) > 1 and not ingredient.get("selectedAlternative"):
        return ConversionResult("unresolved", reason="ambiguous_alternative")
    if not food_id and re.search(r"\b(o|or)\b", text) and not ingredient.get("selectedAlternative"):
        return ConversionResult("unresolved", reason="ambiguous_alternative")
    quantity, minimum, maximum = _quantity(ingredient.get("quantity"))
    if minimum is None and isinstance(quantity, float):
        source_range = re.match(r"\s*(\d+(?:\.\d+)?)\s*[-–]\s*(\d+(?:\.\d+)?)\b", text)
        if source_range:
            minimum, maximum = map(float, source_range.groups())
            quantity = (minimum + maximum) / 2
    assumptions = [] if minimum is None else [f"range:{minimum:g}-{maximum:g}"]
    if quantity is None or quantity <= 0:
        return ConversionResult("unresolved", reason="invalid_quantity", assumptions=assumptions,
                                quantity_min=minimum, quantity_max=maximum)
    quantity *= unit_multiplier
    supplied_grams = _known_supplied_grams(quantity, unit, food_id, context)
    consumption = classify_consumption(
        ingredient, text, CONSUMPTION_UNIT_TOKENS, supplied_grams,
    )
    if consumption.status == "invalid_retention":
        return ConversionResult("unresolved", reason=consumption.reason)
    if consumption.status == "excluded":
        return ConversionResult("non_consumed", reason="not_consumed")
    frying_text = "freir" in text or "frying" in text
    explicit_frying = ingredient.get("fryingConsumption") is not None
    oil_or_fat = any(term in food_name for term in ("oil", "aceite", "fat", "grasa", "lard", "manteca"))
    if ((frying_text and oil_or_fat or explicit_frying)
            and consumption.absorbed_grams is not None):
        absorbed = consumption.absorbed_grams
        return ConversionResult("estimated", absorbed, "absorbed_grams", 1,
                                assumptions=[f"absorbed_grams:{absorbed:g}"])
    has_retention = (
        consumption.retained_fraction is not None
        or consumption.retained_grams is not None
    )
    if frying_text and oil_or_fat and not has_retention:
        return ConversionResult("unresolved", reason="frying_oil_requires_absorption")
    if explicit_frying and not has_retention:
        return ConversionResult("unresolved", reason="frying_oil_requires_absorption")
    if consumption.status == "retention_required":
        return ConversionResult("unresolved", reason=consumption.reason)
    if consumption.retained_grams is not None:
        retained_grams = consumption.retained_grams
        return ConversionResult(
            "estimated", retained_grams, "retained_grams", 1,
            assumptions=[*assumptions, f"retained_grams:{retained_grams:g}"],
            quantity_min=minimum, quantity_max=maximum,
        )
    if consumption.retained_fraction is not None:
        retained_value = consumption.retained_fraction
        quantity *= retained_value
        assumptions.append(f"retained_fraction:{retained_value:g}")
    if not unit and food_id and not MEASURE_TOKEN_RE.search(text):
        unit = "piece"
    if unit in MASS_FACTORS:
        grams = quantity * MASS_FACTORS[unit]
        return ConversionResult("computed", grams, "direct_mass", 1, assumptions=assumptions,
                                quantity_min=minimum, quantity_max=maximum)
    if unit in VOLUME_FACTORS:
        specific = context.find(food_id, "culinary_measure", unit)
        if specific:
            return ConversionResult("estimated", quantity * specific["grams_per_unit"],
                                    "culinary_measure", float(specific["confidence"]),
                                    assumptions=assumptions, quantity_min=minimum, quantity_max=maximum,
                                    source_note=specific.get("source_note"))
        density = context.find(food_id, "density", "ml")
        if not density:
            return ConversionResult("unresolved", reason="density_missing", assumptions=assumptions)
        factor = context.default_cup_ml if unit in {"cup", "taza"} else VOLUME_FACTORS[unit]
        return ConversionResult("estimated", quantity * factor * density["grams_per_unit"],
                                "density", float(density["confidence"]), assumptions=assumptions,
                                quantity_min=minimum, quantity_max=maximum,
                                source_note=density.get("source_note"))
    if unit in PIECE_UNITS:
        conversion = context.find(food_id, "piece_weight", "piece")
        if conversion:
            return ConversionResult("estimated", quantity * conversion["grams_per_unit"],
                                    "piece_weight", float(conversion["confidence"]), assumptions=assumptions,
                                    source_note=conversion.get("source_note"))
        curated_key = PIECE_FOOD_ALIASES.get(food_name)
        curated = CURATED_PIECE_WEIGHTS.get(curated_key or "")
        if curated:
            return ConversionResult("estimated", quantity * curated["grams_per_unit"],
                                    "curated_piece_weight", float(curated["confidence"]),
                                    assumptions=assumptions, source_note=curated.get("source_note"))
    package_key = PACKAGE_UNITS.get(unit)
    if package_key:
        conversion = context.find(food_id, "package_weight", package_key)
        if conversion:
            return ConversionResult("estimated", quantity * conversion["grams_per_unit"],
                                    "package_weight", float(conversion["confidence"]), assumptions=assumptions,
                                    source_note=conversion.get("source_note"))
    measure_key = MEASURE_UNITS.get(unit)
    if measure_key:
        conversion = context.find(food_id, "culinary_measure", measure_key)
        if conversion:
            return ConversionResult("estimated", quantity * conversion["grams_per_unit"],
                                    "culinary_measure", float(conversion["confidence"]), assumptions=assumptions,
                                    source_note=conversion.get("source_note"))
    return ConversionResult("unresolved", reason="conversion_missing", assumptions=assumptions)
