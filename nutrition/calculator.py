"""Deterministic recipe aggregation and evidence scoring."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from .conversions import ConversionContext, convert_ingredient_to_grams, normalize_unit
from .db import normalize_mealie_food_id
from .models import (IngredientCalculation, NUTRIENT_FIELDS, NutrientProfile,
                     NutritionPolicy, RecipeCalculation, is_finite_number,
                     nutrient_profile_is_finite)


def _profile_value(profile: NutrientProfile | dict[str, Any], name: str, default: Any = None) -> Any:
    return getattr(profile, name, default) if isinstance(profile, NutrientProfile) else profile.get(name, default)


def calculate_recipe_nutrition(
    recipe: dict[str, Any], profiles: dict[str, NutrientProfile | dict[str, Any]],
    context: ConversionContext | None = None, policy: NutritionPolicy | None = None,
    *, input_hash: str = "", profile_version: str = "",
) -> RecipeCalculation:
    policy = policy or NutritionPolicy()
    context = context or ConversionContext(default_cup_ml=policy.default_cup_ml)
    totals = {field: 0.0 for field in NUTRIENT_FIELDS}
    evidence: list[IngredientCalculation] = []
    assumptions: list[str] = []
    unresolved: list[str] = []
    known_grams = computed_grams = 0.0
    confidence_weights: list[tuple[float, float]] = []
    expected_count = resolved_count = computable_count = 0
    for position, ingredient in enumerate(recipe.get("recipeIngredient") or []):
        original = str(ingredient.get("originalText") or ingredient.get("display") or "")
        food = ingredient.get("food") or {}
        try:
            food_id = normalize_mealie_food_id(food.get("id"))
        except ValueError:
            food_id = ""
        conversion = convert_ingredient_to_grams(ingredient, context)
        if conversion.status not in {"optional", "non_consumed", "excluded"}:
            expected_count += 1
        conversion_is_finite = conversion.grams is None or is_finite_number(conversion.grams)
        if conversion.grams is not None and conversion_is_finite:
            known_grams += conversion.grams
        profile = profiles.get(food_id)
        profile_is_finite = bool(profile and nutrient_profile_is_finite(profile))
        if profile_is_finite and conversion.status not in {"optional", "non_consumed", "excluded"}:
            resolved_count += 1
        profile_confidence = float(_profile_value(profile, "confidence", 0)) if profile_is_finite else 0
        resolution_confidence = float(_profile_value(profile, "resolution_confidence", 1)) if profile_is_finite else 0
        contribution: dict[str, float] = {}
        status = conversion.status
        reason = conversion.reason
        nutrition_confidence = profile_confidence * resolution_confidence
        if (conversion.grams is not None and conversion_is_finite and profile_is_finite
                and profile_confidence >= policy.min_profile_confidence):
            for field in NUTRIENT_FIELDS:
                amount = _profile_value(profile, field)
                if is_finite_number(amount):
                    contribution[field] = conversion.grams / 100 * float(amount)
            if not all(is_finite_number(value) for value in contribution.values()):
                contribution = {}
                status = "unresolved"
                reason = "nutrition_profile_invalid"
                unresolved.append(original or str(food.get("name") or food_id or f"ingredient {position + 1}"))
            else:
                computed_grams += conversion.grams
                computable_count += 1
                for field, value in contribution.items():
                    totals[field] += value
                status = "estimated" if conversion.status == "estimated" else "computed"
                ingredient_confidence = resolution_confidence * profile_confidence * conversion.confidence
                weight = contribution.get("calories_kcal") or conversion.grams
                confidence_weights.append((ingredient_confidence, weight))
        elif conversion.status not in {"optional", "non_consumed", "excluded"}:
            status = "unresolved"
            if not conversion_is_finite or (profile and not profile_is_finite):
                reason = "nutrition_profile_invalid"
            else:
                reason = reason or ("nutrition_profile_low_confidence" if profile else "nutrition_profile_missing")
            unresolved.append(original or str(food.get("name") or food_id or f"ingredient {position + 1}"))
        assumptions.extend(conversion.assumptions)
        evidence.append(IngredientCalculation(
            position=position, original_text=original, status=status,
            mealie_ingredient_id=ingredient.get("id"), mealie_food_id=food_id or None,
            quantity=((conversion.quantity_min + conversion.quantity_max) / 2
                      if conversion.quantity_min is not None else
                      (ingredient.get("quantity") if isinstance(ingredient.get("quantity"), (int, float)) else None)),
            quantity_min=conversion.quantity_min, quantity_max=conversion.quantity_max,
            unit_key=normalize_unit((ingredient.get("unit") or {}).get("name", "")),
            consumed_grams=conversion.grams, conversion_method=conversion.method,
            conversion_confidence=conversion.confidence, nutrition_confidence=nutrition_confidence,
            contribution=contribution, reason=reason, assumptions=conversion.assumptions,
            source_note=conversion.source_note,
        ))
    coverage = computed_grams / known_grams if known_grams else (1.0 if expected_count == 0 else 0.0)
    count_coverage = computable_count / expected_count if expected_count else 1.0
    weighted = (sum(conf * weight for conf, weight in confidence_weights) /
                sum(weight for _, weight in confidence_weights)) if confidence_weights else 0.0
    effective_coverage = min(coverage, count_coverage)
    confidence = weighted * effective_coverage
    if effective_coverage >= policy.min_publish_coverage:
        status = "complete"
    elif effective_coverage >= .7:
        status = "estimated"
    else:
        status = "insufficient"
    servings_value = recipe.get("recipeServings")
    try:
        servings = float(servings_value) if servings_value is not None else None
    except (TypeError, ValueError):
        servings = None
    if servings is not None and (not is_finite_number(servings) or servings <= 0):
        servings = None
    per_serving = ({name: value / servings for name, value in totals.items()}
                   if servings else {})
    if not servings:
        assumptions.append("missing_servings")
    digest_source = json.dumps({"recipe": recipe.get("id"), "input": input_hash,
                                "profile": profile_version}, sort_keys=True).encode()
    calculation_id = hashlib.sha256(digest_source).hexdigest()[:32]
    return RecipeCalculation(
        id=calculation_id, recipe_id=str(recipe.get("id") or ""),
        slug=str(recipe.get("slug") or ""), input_hash=input_hash,
        profile_version=profile_version, servings=servings, status=status,
        coverage_ratio=max(0, min(1, effective_coverage)), confidence=max(0, min(1, confidence)),
        totals=totals, per_serving=per_serving, ingredients=evidence,
        unresolved=unresolved, assumptions=assumptions,
        mass_coverage_ratio=coverage, ingredient_coverage_ratio=count_coverage,
        ingredient_count=expected_count, resolved_count=resolved_count,
        computable_count=computable_count,
    )
