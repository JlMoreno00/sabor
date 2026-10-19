"""Typed domain values used by the nutrition engine."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any


NUTRIENT_FIELDS = (
    "calories_kcal", "protein_g", "carbohydrate_g", "net_carbohydrate_g",
    "fat_g", "saturated_fat_g", "unsaturated_fat_g", "trans_fat_g",
    "fiber_g", "sugar_g", "sodium_mg", "cholesterol_mg",
)
SPOONACULAR_PROFILE_CONFIDENCE = .97


def is_finite_number(value: Any) -> bool:
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value))


@dataclass(frozen=True)
class NutritionPolicy:
    min_profile_confidence: float = .8
    min_publish_coverage: float = .9
    min_publish_confidence: float = .8
    publish_estimates: bool = False
    default_cup_ml: float = 240


@dataclass
class FoodResolution:
    mealie_food_id: str
    mealie_food_name: str
    canonical_term: str | None
    status: str
    confidence: float
    method: str
    provider: str | None = None
    provider_food_id: str | None = None
    manual_override: bool = False
    stale_after: str | None = None
    error_code: str | None = None
    retry_after: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class NutrientProfile:
    mealie_food_id: str
    basis_grams: float = 100
    calories_kcal: float | None = None
    protein_g: float | None = None
    carbohydrate_g: float | None = None
    net_carbohydrate_g: float | None = None
    fat_g: float | None = None
    saturated_fat_g: float | None = None
    unsaturated_fat_g: float | None = None
    trans_fat_g: float | None = None
    fiber_g: float | None = None
    sugar_g: float | None = None
    sodium_mg: float | None = None
    cholesterol_mg: float | None = None
    provider: str = "unknown"
    provider_food_id: str | None = None
    confidence: float = 0
    resolution_confidence: float = 1
    manual_override: bool = False
    payload_hash: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def nutrient_profile_is_finite(profile: NutrientProfile | dict[str, Any]) -> bool:
    def value(name: str, default: Any = None) -> Any:
        return (getattr(profile, name, default) if isinstance(profile, NutrientProfile)
                else profile.get(name, default))

    if not all(is_finite_number(value(name))
               for name in ("basis_grams", "confidence", "resolution_confidence")):
        return False
    return all(amount is None or is_finite_number(amount)
               for amount in (value(field) for field in NUTRIENT_FIELDS))


@dataclass
class ConversionResult:
    status: str
    grams: float | None = None
    method: str | None = None
    confidence: float = 0
    reason: str | None = None
    assumptions: list[str] = field(default_factory=list)
    quantity_min: float | None = None
    quantity_max: float | None = None
    source_note: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class IngredientCalculation:
    position: int
    original_text: str
    status: str
    mealie_ingredient_id: str | None = None
    mealie_food_id: str | None = None
    quantity: float | None = None
    quantity_min: float | None = None
    quantity_max: float | None = None
    unit_key: str | None = None
    consumed_grams: float | None = None
    conversion_method: str | None = None
    conversion_confidence: float = 0
    nutrition_confidence: float = 0
    contribution: dict[str, float] = field(default_factory=dict)
    reason: str | None = None
    assumptions: list[str] = field(default_factory=list)
    source_note: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RecipeCalculation:
    id: str
    recipe_id: str
    slug: str
    input_hash: str
    profile_version: str
    servings: float | None
    status: str
    coverage_ratio: float
    confidence: float
    totals: dict[str, float] = field(default_factory=dict)
    per_serving: dict[str, float] = field(default_factory=dict)
    ingredients: list[IngredientCalculation] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    mass_coverage_ratio: float = 0
    ingredient_coverage_ratio: float = 0
    ingredient_count: int = 0
    resolved_count: int = 0
    computable_count: int = 0
    engine_version: str = "1"
    published_to_mealie: bool = False

    def has_finite_values(self) -> bool:
        metadata = (self.coverage_ratio, self.confidence, self.mass_coverage_ratio,
                    self.ingredient_coverage_ratio)
        if not all(is_finite_number(value) for value in metadata):
            return False
        if self.servings is not None and not is_finite_number(self.servings):
            return False
        return all(is_finite_number(value)
                   for values in (self.totals, self.per_serving)
                   for value in values.values())

    def is_publishable(self, policy: NutritionPolicy) -> bool:
        return nutrition_eligibility(self, policy)["eligible"]

    def display_values(self) -> dict[str, float]:
        values = self.per_serving or self.totals
        if not all(is_finite_number(value) for value in values.values()):
            return {}
        result: dict[str, float] = {}
        for name, value in values.items():
            if name == "calories_kcal" or name.endswith("_mg"):
                result[name] = round(value)
            else:
                result[name] = round(value, 1)
        return result

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["mealie_recipe_id"] = result.pop("recipe_id")
        result["mealie_recipe_slug"] = result.pop("slug")
        return result


def nutrition_eligibility(calculation: RecipeCalculation,
                          policy: NutritionPolicy) -> dict[str, Any]:
    """Return the single deterministic automatic-use decision."""

    if not calculation.has_finite_values():
        code = "nutrition_invalid_values"
    elif (not is_finite_number(calculation.servings)
          or float(calculation.servings) <= 0 or not calculation.per_serving):
        code = "nutrition_missing_servings"
    elif calculation.status == "estimated" and not policy.publish_estimates:
        code = "nutrition_estimate_not_allowed"
    elif calculation.status == "insufficient":
        code = "nutrition_insufficient_coverage"
    elif calculation.status not in {"complete", "estimated"}:
        code = "nutrition_incomplete"
    elif calculation.coverage_ratio < policy.min_publish_coverage:
        code = "nutrition_insufficient_coverage"
    elif calculation.confidence < policy.min_publish_confidence:
        code = "nutrition_low_confidence"
    else:
        return {
            "eligible": True, "code": "nutrition_eligible",
            "coverage": calculation.coverage_ratio,
            "confidence": calculation.confidence,
        }
    return {
        "eligible": False, "code": code,
        "coverage": calculation.coverage_ratio,
        "confidence": calculation.confidence,
    }
