"""Nutrition orchestration, idempotency, and bounded backfill helpers."""

from __future__ import annotations

import hashlib
import json
from dataclasses import fields
from datetime import datetime, timezone
from typing import Any, Callable, Iterable

from .calculator import calculate_recipe_nutrition
from .conversions import ConversionContext, is_title_only_section_heading
from .db import NutritionDB, normalize_mealie_food_id
from .models import (NUTRIENT_FIELDS, NutrientProfile, NutritionPolicy,
                     RecipeCalculation, nutrient_profile_is_finite,
                     nutrition_eligibility)


# Persisted evidence semantics are part of the calculation cache identity.
ENGINE_VERSION = "3"


def stable_hash(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, ensure_ascii=False,
                         separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def recipe_input_hash(recipe: dict[str, Any], overrides: dict[str, Any] | None = None) -> str:
    ingredients = []
    for position, item in enumerate(recipe.get("recipeIngredient") or []):
        title_only_section_heading = is_title_only_section_heading(item)
        ingredient = {
            "id": item.get("id"), "position": position,
            "quantity": None if title_only_section_heading else item.get("quantity"),
            "unit_id": (item.get("unit") or {}).get("id"),
            "unit_name": (item.get("unit") or {}).get("name"),
            "unit_abbreviation": (item.get("unit") or {}).get("abbreviation"),
            "unit_standard_quantity": (item.get("unit") or {}).get("standardQuantity"),
            "unit_standard_unit": (item.get("unit") or {}).get("standardUnit"),
            "unit_use_abbreviation": (item.get("unit") or {}).get("useAbbreviation"),
            "food_id": (
                normalize_mealie_food_id((item.get("food") or {}).get("id"))
                if (item.get("food") or {}).get("id") is not None else None
            ),
            "food_name": (item.get("food") or {}).get("name"),
            "original_text": item.get("originalText"), "display": item.get("display"),
            "optional": item.get("optional"), "included": item.get("included"),
            "non_consumed": item.get("nonConsumed"),
            "selected_alternative": item.get("selectedAlternative"),
            "retained_fraction": item.get("retainedFraction"),
            "absorbed_grams": item.get("absorbedGrams"),
            "frying_consumption": item.get("fryingConsumption"),
            "alternatives": item.get("alternatives") or item.get("alternativeFoods"),
            "title_only_section_heading": title_only_section_heading,
        }
        ingredients.append(ingredient)
    return stable_hash({"id": recipe.get("id"), "slug": recipe.get("slug"),
                        "servings": recipe.get("recipeServings"), "yield": recipe.get("recipeYield"),
                        "ingredients": ingredients, "overrides": overrides or {}})


class NutritionService:
    def __init__(self, db: NutritionDB, *, resolver: Any,
                 publisher: Any | None = None, policy: NutritionPolicy | None = None):
        self.db = db
        self.resolver = resolver
        self.publisher = publisher
        self.policy = policy or NutritionPolicy()
        self._run_failed_foods: dict[str, tuple[str | None, str, int | None]] | None = None

    def eligibility(self, calculation: RecipeCalculation | dict[str, Any]) -> dict[str, Any]:
        model = calculation if isinstance(calculation, RecipeCalculation) else self._model(calculation)
        return nutrition_eligibility(model, self.policy)

    def calculate(self, recipe: dict[str, Any], *, force: bool = False,
                  overrides: dict[str, Any] | None = None) -> dict[str, Any]:
        recipe_id = str(recipe.get("id") or "")
        slug = str(recipe.get("slug") or "")
        if not recipe_id or not slug:
            return _error("nutrition_recipe_not_found", "Recipe identity is missing.")
        profiles: dict[str, NutrientProfile] = {}
        conversion_values: dict[tuple[str, str, str], dict[str, Any]] = {}
        profile_parts = []
        invalid_profile = False
        attempted_food_ids: set[str] = set()
        for item in recipe.get("recipeIngredient") or []:
            food = item.get("food") or {}
            try:
                food_id = normalize_mealie_food_id(food.get("id"))
            except ValueError:
                continue
            if food_id in attempted_food_ids:
                continue
            attempted_food_ids.add(food_id)
            if not force and self._run_failed_foods is not None and food_id in self._run_failed_foods:
                cooldown, code, retry_after = self._run_failed_foods[food_id]
                try:
                    active = cooldown is None or datetime.fromisoformat(cooldown) > datetime.now(timezone.utc)
                except ValueError:
                    active = True
                if active:
                    details = {"retryAfter": retry_after} if retry_after is not None else {}
                    return _error(
                        code, "Nutrition provider work did not complete.", retryable=True,
                        details=details,
                    )
                del self._run_failed_foods[food_id]
            resolution = self.resolver.resolve(food, force=force)
            error_code = getattr(resolution, "error_code", None)
            if error_code and self._run_failed_foods is not None:
                self._run_failed_foods[food_id] = (
                    getattr(resolution, "stale_after", None), error_code,
                    getattr(resolution, "retry_after", None),
                )
            if error_code in {
                "nutrition_provider_rate_limited", "nutrition_provider_unavailable",
                "nutrition_provider_budget_exceeded",
            }:
                retry_after = getattr(resolution, "retry_after", None)
                return _error(
                    error_code, "Nutrition provider work did not complete.", retryable=True,
                    details=({"retryAfter": retry_after} if retry_after is not None else {}),
                )
            profile_row = self.db.get_profile(food_id)
            resolution_row = self.db.get_resolution(food_id)
            if profile_row:
                allowed = {field.name for field in fields(NutrientProfile)}
                values = {key: value for key, value in profile_row.items() if key in allowed}
                values["resolution_confidence"] = float(getattr(resolution, "confidence", 0))
                values["manual_override"] = bool(values.get("manual_override"))
                profiles[food_id] = NutrientProfile(**values)
                invalid_profile = invalid_profile or not nutrient_profile_is_finite(profiles[food_id])
            conversions = self.db.list_conversions(food_id)
            for conversion in conversions:
                conversion_values[(food_id, conversion["conversion_type"], conversion["unit_key"])] = conversion
            profile_parts.append({
                "food_id": food_id,
                "resolution": ({
                    "canonical_term": resolution_row.get("canonical_term"),
                    "provider": resolution_row.get("provider"),
                    "provider_food_id": resolution_row.get("provider_food_id"),
                    "status": resolution_row.get("resolution_status"),
                    "confidence": resolution_row.get("confidence"),
                    "method": resolution_row.get("resolution_method"),
                    "manual_override": bool(resolution_row.get("manual_override")),
                    "source_version": resolution_row.get("source_version"),
                    "payload_hash": resolution_row.get("payload_hash"),
                } if resolution_row else None),
                "profile": ({
                    "basis_grams": profile_row.get("basis_grams"),
                    "nutrients": {field: profile_row.get(field) for field in NUTRIENT_FIELDS},
                    "provider": profile_row.get("provider"),
                    "provider_food_id": profile_row.get("provider_food_id"),
                    "confidence": profile_row.get("confidence"),
                    "manual_override": bool(profile_row.get("manual_override")),
                    "source_version": profile_row.get("source_version"),
                    "payload_hash": profile_row.get("payload_hash"),
                } if profile_row else None),
                "conversions": sorted((
                    value["conversion_type"], value["unit_key"], value["grams_per_unit"],
                    value["confidence"], value["provider"], bool(value["manual_override"]),
                ) for value in conversions),
            })
        input_hash = recipe_input_hash(recipe, overrides)
        profile_version = stable_hash(sorted(profile_parts, key=lambda value: value["food_id"]))
        cached = (None if force or invalid_profile else
                  self.db.find_calculation(recipe_id, input_hash, ENGINE_VERSION, profile_version))
        if cached:
            cached_evidence = self._cached_evidence(cached)
            cached_evidence["applied_mapping_count"] = \
                self.db.manual_mapping_count_for_calculation(cached["id"])
            return {"ok": True, "code": "nutrition_calculation_cached",
                    "message": "Existing calculation reused.", "retryable": False,
                    "cached": True, "calculation": cached_evidence}
        calculation = calculate_recipe_nutrition(
            recipe, profiles, ConversionContext(conversion_values, self.policy.default_cup_ml),
            self.policy, input_hash=input_hash, profile_version=profile_version,
        )
        calculation.engine_version = ENGINE_VERSION
        evidence = calculation.to_dict()
        calculation.id = self.db.save_calculation(evidence)
        evidence["id"] = calculation.id
        persisted = self.db.find_calculation(
            recipe_id, input_hash, ENGINE_VERSION, profile_version,
        )
        evidence["calculated_at"] = persisted["calculated_at"]
        evidence["applied_mapping_count"] = self.db.manual_mapping_count_for_calculation(
            calculation.id
        )
        return {"ok": True, "code": "nutrition_calculated",
                "message": "Nutrition evidence calculated.", "retryable": False,
                "cached": False, "calculation": evidence}

    def publish(self, recipe: dict[str, Any], *, force: bool = False,
                 dry_run: bool = False) -> dict[str, Any]:
        result = self.calculate(recipe, force=force)
        if not result["ok"] or not self.publisher:
            return result if not result["ok"] else _error(
                "nutrition_mealie_patch_failed", "Nutrition publisher is not configured.")
        evidence = result["calculation"]
        if result["cached"] and evidence.get("published_to_mealie") and not force:
            return {"ok": True, "code": "nutrition_publish_cached",
                    "message": "Identical nutrition was already published.",
                    "retryable": False, "published": False, "cached": True}
        return self.publish_calculation(recipe, evidence, dry_run=dry_run)

    def publish_calculation(self, recipe: dict[str, Any], evidence: dict[str, Any], *,
                            dry_run: bool = False) -> dict[str, Any]:
        """Publish already-calculated evidence without another provider pass."""

        if not self.publisher:
            return _error("nutrition_mealie_patch_failed", "Nutrition publisher is not configured.")
        calculation = self._model(evidence)
        published = self.publisher.publish(
            recipe["slug"], calculation, existing_recipe=recipe,
            policy=self.policy, dry_run=dry_run,
        )
        if published.get("published"):
            previous = {"nutrition": recipe.get("nutrition") or {},
                        "settings": recipe.get("settings") or {}}
            self.db.record_publish(calculation.id, previous,
                                   published["details"]["payload"])
        return published

    def run_backfill(self, recipes: Iterable[dict[str, Any]], *,
                     calculate: Callable[..., dict[str, Any]], batch_size: int = 5,
                     limit: int | None = None, resume: bool = True,
                     publish: bool = False,
                     checkpoint_key: str = "personal-recipes") -> dict[str, Any]:
        batch_size = max(1, batch_size)
        summary = {"ok": True, "processed": 0, "calculated": 0, "cached": 0,
                   "failed": 0, "stopped_rate_limited": False,
                   "batch_size": batch_size, "resume": resume, "publish": publish,
                   "checkpoint_key": checkpoint_key, "skipped_checkpoint": 0,
                   "has_more": False, "checkpoint": None}
        if not resume:
            self.db.clear_backfill_checkpoint(checkpoint_key)
        considered = 0
        previous_failures = self._run_failed_foods
        self._run_failed_foods = {}
        try:
            for recipe in recipes:
                if limit is not None and considered >= max(0, limit):
                    break
                considered += 1
                recipe_id = str(recipe.get("id") or recipe.get("slug") or "")
                input_hash = recipe_input_hash(recipe)
                checkpoint = self.db.get_backfill_checkpoint(checkpoint_key, recipe_id)
                if resume and checkpoint and checkpoint["input_hash"] == input_hash:
                    summary["skipped_checkpoint"] += 1
                    continue
                if summary["processed"] >= batch_size:
                    summary["has_more"] = True
                    break
                result = calculate(recipe, publish=publish) if publish else calculate(recipe)
                summary["processed"] += 1
                if result.get("code") == "nutrition_provider_rate_limited":
                    summary["failed"] += 1
                    summary["stopped_rate_limited"] = True
                    break
                if result.get("ok"):
                    summary["cached" if result.get("cached") else "calculated"] += 1
                else:
                    summary["failed"] += 1
                if recipe_id:
                    self.db.mark_backfill_checkpoint(checkpoint_key, recipe_id, input_hash)
                    summary["checkpoint"] = {"recipe_id": recipe_id, "input_hash": input_hash}
        finally:
            self._run_failed_foods = previous_failures
        return summary

    @staticmethod
    def _cached_evidence(row: dict[str, Any]) -> dict[str, Any]:
        totals = {
            "calories_kcal": row.get("total_calories_kcal"),
            "protein_g": row.get("total_protein_g"),
            "carbohydrate_g": row.get("total_carbohydrate_g"),
            "fat_g": row.get("total_fat_g"), "fiber_g": row.get("total_fiber_g"),
            "sugar_g": row.get("total_sugar_g"), "sodium_mg": row.get("total_sodium_mg"),
            "saturated_fat_g": row.get("total_saturated_fat_g"),
        }
        return {**row, "totals": {key: value for key, value in totals.items() if value is not None},
                "mealie_recipe_id": row["mealie_recipe_id"],
                "mealie_recipe_slug": row["mealie_recipe_slug"],
                "published_to_mealie": bool(row.get("published_to_mealie"))}

    @staticmethod
    def _model(value: dict[str, Any]) -> RecipeCalculation:
        return RecipeCalculation(
            id=value["id"], recipe_id=value["mealie_recipe_id"],
            slug=value["mealie_recipe_slug"], input_hash=value["input_hash"],
            profile_version=value["profile_version"], servings=value.get("servings"),
            status=value["status"], coverage_ratio=value["coverage_ratio"],
            confidence=value["confidence"], totals=value.get("totals", {}),
            per_serving=value.get("per_serving", {}), unresolved=value.get("unresolved", []),
            assumptions=value.get("assumptions", []), engine_version=value.get("engine_version", "1"),
            mass_coverage_ratio=value.get("mass_coverage_ratio", value["coverage_ratio"]),
            ingredient_coverage_ratio=value.get("ingredient_coverage_ratio", value["coverage_ratio"]),
            ingredient_count=value.get("ingredient_count", 0),
            resolved_count=value.get("resolved_count", 0),
            computable_count=value.get("computable_count", 0),
            published_to_mealie=bool(value.get("published_to_mealie")),
        )


def _error(code: str, message: str, retryable: bool = False,
           details: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"ok": False, "code": code, "message": message,
            "retryable": retryable, "details": details or {}}
