"""Guarded Mealie recipe nutrition publisher."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from typing import Any

import requests

from .models import NutritionPolicy, RecipeCalculation, nutrition_eligibility
from .protection import nutrition_publication_protected


FIELD_MAP = {
    "calories_kcal": ("calories", "kcal", 0),
    "carbohydrate_g": ("carbohydrateContent", "g", 1),
    "protein_g": ("proteinContent", "g", 1),
    "fat_g": ("fatContent", "g", 1),
    "fiber_g": ("fiberContent", "g", 1),
    "sugar_g": ("sugarContent", "g", 1),
    "sodium_mg": ("sodiumContent", "mg", 0),
    "saturated_fat_g": ("saturatedFatContent", "g", 1),
    "trans_fat_g": ("transFatContent", "g", 1),
    "unsaturated_fat_g": ("unsaturatedFatContent", "g", 1),
    "cholesterol_mg": ("cholesterolContent", "mg", 0),
}
_PERSISTED_VALUE_MAX_LENGTH = 64
_SUPPORTED_TARGETS = {
    target: (unit, digits) for target, unit, digits in FIELD_MAP.values()
}


def canonical_nutrition_from_per_serving(per_serving: dict[str, Any]) -> dict[str, str]:
    """Format supported calculation fields exactly as Mealie publication does."""

    nutrition: dict[str, str] = {}
    for source, (target, unit, digits) in FIELD_MAP.items():
        value = per_serving.get(source)
        if (not isinstance(value, (int, float)) or isinstance(value, bool)
                or not math.isfinite(float(value))):
            continue
        rounded = round(value, digits)
        formatted = str(int(rounded)) if digits == 0 else f"{rounded:.{digits}f}"
        nutrition[target] = f"{formatted} {unit}"
    return nutrition


def canonical_supported_nutrition(
    value: Any, *, full_payload: bool = False,
) -> dict[str, str] | None:
    """Validate publisher-shaped persisted nutrition, ignoring unknown fields.

    Null and blank supported values follow Mealie's empty-value semantics and
    are omitted. ``None`` means that the envelope or a supported value is not a
    shape this publisher can persist.
    """

    if not isinstance(value, Mapping):
        return None
    nutrition = value.get("nutrition") if full_payload else value
    if not isinstance(nutrition, Mapping):
        return None
    result = {}
    for key, (unit, digits) in _SUPPORTED_TARGETS.items():
        if key not in nutrition:
            continue
        field_value = nutrition[key]
        if field_value is None or (isinstance(field_value, str) and not field_value.strip()):
            continue
        if not isinstance(field_value, str) or len(field_value) > _PERSISTED_VALUE_MAX_LENGTH:
            return None
        integer = r"-?(?:0|[1-9][0-9]*)"
        number = integer if digits == 0 else rf"{integer}\.[0-9]"
        if re.fullmatch(rf"{number} {re.escape(unit)}", field_value) is None:
            return None
        result[key] = field_value
    return result


def has_meaningful_supported_nutrition(nutrition: Any) -> bool:
    """Return whether current Mealie data requires ownership review."""

    if nutrition is None:
        return False
    if not isinstance(nutrition, Mapping):
        return True
    return any(
        key in nutrition
        and nutrition[key] is not None
        and not (isinstance(nutrition[key], str) and not nutrition[key].strip())
        for key in _SUPPORTED_TARGETS
    )


def nutrition_payload(calculation: RecipeCalculation,
                      existing_recipe: dict[str, Any] | None = None) -> dict[str, Any]:
    nutrition = canonical_nutrition_from_per_serving(calculation.per_serving)
    settings = dict((existing_recipe or {}).get("settings") or {})
    settings["showNutrition"] = True
    return {"nutrition": nutrition, "settings": settings}


class MealiePublisher:
    def __init__(self, base_url: str, headers: dict[str, str], *,
                 request: Any = requests.patch, timeout: int = 30):
        self.base_url = base_url.rstrip("/")
        self.headers = headers
        self.request = request
        self.timeout = timeout

    def publish(self, slug: str, calculation: RecipeCalculation, *,
                existing_recipe: dict[str, Any] | None = None,
                policy: NutritionPolicy | None = None, dry_run: bool = False) -> dict[str, Any]:
        policy = policy or NutritionPolicy()
        if nutrition_publication_protected(existing_recipe):
            return _error("nutrition_manual_protection",
                          "Existing Mealie nutrition is manually protected.")
        eligibility = nutrition_eligibility(calculation, policy)
        if not eligibility["eligible"]:
            return _error(
                eligibility["code"], "Nutrition is not eligible for automatic publication.",
                {"coverage": calculation.coverage_ratio,
                 "confidence": calculation.confidence,
                 "unresolved": calculation.unresolved},
            )
        payload = nutrition_payload(calculation, existing_recipe)
        if dry_run:
            return {"ok": True, "code": "nutrition_publish_preview", "message": "No Mealie write was made.",
                    "retryable": False, "published": False, "details": {"payload": payload}}
        try:
            response = self.request(f"{self.base_url}/api/recipes/{slug}",
                                    headers=self.headers, json=payload, timeout=self.timeout)
        except Exception as exc:
            return _error("nutrition_mealie_patch_failed", "Mealie PATCH failed.",
                          {"reason": type(exc).__name__}, retryable=True)
        if not 200 <= response.status_code < 300:
            return _error("nutrition_mealie_patch_failed",
                          f"Mealie PATCH returned HTTP {response.status_code}.",
                          {"status": response.status_code}, retryable=response.status_code >= 500)
        return {"ok": True, "code": "nutrition_published", "message": "Nutrition published to Mealie.",
                "retryable": False, "published": True, "details": {"payload": payload}}


def _error(code: str, message: str, details: dict[str, Any] | None = None,
           retryable: bool = False) -> dict[str, Any]:
    return {"ok": False, "code": code, "message": message,
            "retryable": retryable, "details": details or {}}
