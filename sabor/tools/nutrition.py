"""MCP adapters for the existing nutrition domain package."""

import copy
import json
import hashlib
import math
import re
import threading
from typing import Any
from uuid import UUID
import requests
import sabor.config as config
import sabor.integrations.mealie as mealie
from sabor.core.toolsets import tool

# Nutrition is initialized lazily so disabled deployments make no database or provider calls.
_NUTRITION_SERVICE_INSTANCE: Any = None
_NUTRITION_RECIPE_LOCKS = tuple(threading.Lock() for _ in range(64))
_MEALPLAN_LOCKS = tuple(threading.Lock() for _ in range(64))
_NUTRITION_CORRECTION_STAGES: dict[str, dict[str, Any]] = {}
_NUTRITION_CORRECTION_STAGE_LOCK = threading.Lock()


def _nutrition_error(code: str, message: str, *, retryable: bool = False,
                     details: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"ok": False, "code": code, "message": message,
            "retryable": retryable, "details": details or {}}


def _keyed_lock(values: tuple[threading.Lock, ...], key: Any) -> threading.Lock:
    return values[hash(key) % len(values)]


def _nutrition_recipe_revision(recipe: dict[str, Any]) -> dict[str, str | None]:
    from nutrition.service import stable_hash

    semantic = copy.deepcopy(recipe)
    semantic.pop("updatedAt", None)
    return {"fingerprint": stable_hash(semantic), "updatedAt": recipe.get("updatedAt")}


def _nutrition_continuation(tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return {"tool": tool, "arguments": copy.deepcopy(arguments)}


def _nutrition_failure(code: str, message: str, *, stage: str,
                       continuation: dict[str, Any] | None = None,
                       retryable: bool = False, retry_after: Any = None,
                       actions: list[dict[str, Any]] | None = None,
                       details: dict[str, Any] | None = None) -> dict[str, Any]:
    result = _nutrition_error(code, message, retryable=retryable, details=details)
    result["stage"] = stage
    result["actions"] = list(actions or [])
    if continuation:
        result["continuation"] = continuation
    if isinstance(retry_after, (int, float)) and not isinstance(retry_after, bool):
        result["retryAfter"] = min(3600, max(1, int(retry_after)))
    return result


def _authoritative_mealie_food(food_id: str,
                               details: dict[str, Any]) -> tuple[dict[str, str] | None,
                                                                 dict[str, Any] | None]:
    try:
        parsed_id = UUID(food_id)
    except ValueError:
        return None, None
    if parsed_id.version != 4 or str(parsed_id) != food_id:
        return None, None
    if mealie._check_mealie():
        return None, _nutrition_error(
            "nutrition_mapping_target_unavailable",
            "Authoritative Mealie food validation is not configured.",
            details={**details, "targetValid": False},
        )
    try:
        response = requests.get(
            f"{config.MEALIE_BASE_URL}/api/foods/{food_id}",
            headers=mealie._mealie_headers(), timeout=30,
        )
    except requests.exceptions.RequestException:
        return None, _nutrition_error(
            "nutrition_mapping_target_unavailable",
            "Authoritative Mealie food validation is temporarily unavailable.",
            retryable=True, details={**details, "targetValid": False},
        )
    if response.status_code == 404:
        return None, _nutrition_error(
            "nutrition_mapping_target_not_found",
            "The authoritative Mealie food was not found.",
            details={**details, "targetValid": False},
        )
    if not 200 <= response.status_code < 300:
        error_details = {**details, "targetValid": False}
        if response.status_code == 429:
            retry_after = response.headers.get("Retry-After")
            if (isinstance(retry_after, str) and 0 < len(retry_after) <= 10
                    and retry_after.isascii()
                    and retry_after.isdigit()):
                error_details["retryAfter"] = min(int(retry_after), 3600)
        return None, _nutrition_error(
            "nutrition_mapping_target_unavailable",
            "Authoritative Mealie food validation is unavailable.",
            retryable=response.status_code in (408, 429) or response.status_code >= 500,
            details=error_details,
        )
    try:
        body = response.json()
    except (TypeError, ValueError):
        body = None
    remote_id = body.get("id") if isinstance(body, dict) else None
    remote_name = body.get("name") if isinstance(body, dict) else None
    if remote_id != food_id:
        return None, _nutrition_error(
            "nutrition_mapping_target_mismatch",
            "Authoritative Mealie food identity did not match the requested ID.",
            details={**details, "targetValid": False},
        )
    if not isinstance(remote_name, str) or not remote_name.strip():
        return None, _nutrition_error(
            "nutrition_mapping_target_unavailable",
            "Authoritative Mealie food validation returned an invalid contract.",
            retryable=True, details={**details, "targetValid": False},
        )
    return {"id": remote_id, "name": remote_name, "source": "mealie"}, None


def _nutrition_service() -> Any:
    global _NUTRITION_SERVICE_INSTANCE
    if _NUTRITION_SERVICE_INSTANCE is None:
        from nutrition.db import NutritionDB
        from nutrition.models import NutritionPolicy
        from nutrition.providers import SpoonacularProvider
        from nutrition.publisher import MealiePublisher
        from nutrition.resolver import FoodResolver
        from nutrition.service import NutritionService

        db = NutritionDB(config.SABOR_NUTRITION_DB)
        policy = NutritionPolicy(
            min_profile_confidence=config.SABOR_NUTRITION_MIN_PROFILE_CONFIDENCE,
            min_publish_coverage=config.SABOR_NUTRITION_MIN_PUBLISH_COVERAGE,
            min_publish_confidence=config.SABOR_NUTRITION_MIN_PUBLISH_CONFIDENCE,
            publish_estimates=config.SABOR_NUTRITION_PUBLISH_ESTIMATES,
            default_cup_ml=config.SABOR_NUTRITION_DEFAULT_CUP_ML,
        )
        provider = SpoonacularProvider(
            config.SPOONACULAR_API_KEY, db, base_url=config.SPOONACULAR_BASE,
            timeout=config.SPOONACULAR_TIMEOUT,
            profile_ttl_days=config.SABOR_NUTRITION_PROFILE_TTL_DAYS,
            not_found_ttl_days=config.SABOR_NUTRITION_NOT_FOUND_TTL_DAYS,
        )
        resolver = FoodResolver(
            db, provider, profile_ttl_days=config.SABOR_NUTRITION_PROFILE_TTL_DAYS,
            not_found_ttl_days=config.SABOR_NUTRITION_NOT_FOUND_TTL_DAYS,
            source_language=config.SABOR_NUTRITION_SOURCE_LANGUAGE,
        )
        publisher = MealiePublisher(config.MEALIE_BASE_URL, mealie._mealie_headers())
        _NUTRITION_SERVICE_INSTANCE = NutritionService(
            db, resolver=resolver, publisher=publisher, policy=policy
        )
    return _NUTRITION_SERVICE_INSTANCE


def _nutrition_fetch_recipe(slug: str) -> dict[str, Any]:
    response = requests.get(
        f"{config.MEALIE_BASE_URL}/api/recipes/{slug}", headers=mealie._mealie_headers(), timeout=30
    )
    if response.status_code == 404:
        raise LookupError(slug)
    if not 200 <= response.status_code < 300:
        raise RuntimeError(f"Mealie recipe read returned HTTP {response.status_code}")
    recipe = response.json()
    if not isinstance(recipe, dict):
        raise ValueError("Mealie recipe response is not an object")
    return recipe


def _nutrition_readiness_assessment(recipe: dict[str, Any], *,
                                    include_suggestions: bool) -> dict[str, Any]:
    from nutrition.readiness import (
        ReadinessPolicy,
        assess_recipe_readiness,
        read_readiness_evidence,
        recipe_evidence_requests,
    )

    policy = ReadinessPolicy(min_confidence=config.SABOR_NUTRITION_MIN_PROFILE_CONFIDENCE)
    evidence = read_readiness_evidence(
        config.SABOR_NUTRITION_DB, recipe_evidence_requests(recipe), policy=policy,
        recipe_id=recipe.get("id"), current_nutrition=recipe.get("nutrition"),
    )
    assessment = assess_recipe_readiness(
        recipe, evidence, policy, include_suggestions=include_suggestions,
    )
    issue_count = len(assessment["issues"])
    warning_count = len(assessment["warnings"])
    assessment["issues"] = assessment["issues"][:config._NUTRITION_RECIPE_ISSUE_LIMIT]
    assessment["warnings"] = assessment["warnings"][:config._NUTRITION_RECIPE_ISSUE_LIMIT]
    assessment["issueCount"] = issue_count
    assessment["omittedIssueCount"] = issue_count - len(assessment["issues"])
    assessment["warningCount"] = warning_count
    assessment["omittedWarningCount"] = warning_count - len(assessment["warnings"])
    return _nutrition_bound_readiness_output(assessment)


def _nutrition_bounded_string(value: Any, cap: int) -> str:
    text = " ".join(str(value or "").split())
    if len(json.dumps(text, ensure_ascii=True)[1:-1].encode("utf-8")) <= cap:
        return text
    budget = cap - 1
    result = []
    used = 0
    for character in text:
        size = len(json.dumps(character, ensure_ascii=True)[1:-1].encode("utf-8"))
        if used + size > budget:
            break
        result.append(character)
        used += size
    return "".join(result) + "~"


def _nutrition_bound_readiness_output(value: Any, field: str | None = None) -> Any:
    if isinstance(value, dict):
        return {
            key: _nutrition_bound_readiness_output(item, key)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_nutrition_bound_readiness_output(item, field) for item in value]
    if isinstance(value, tuple):
        return tuple(_nutrition_bound_readiness_output(item, field) for item in value)
    cap = config._NUTRITION_OUTPUT_FIELD_CAPS.get(field or "")
    return (_nutrition_bounded_string(value, cap)
            if cap is not None and value is not None else value)


@tool("legacy_nutrition")
def nutrition_recipe_readiness(slug: str,
                               include_suggestions: bool = True) -> dict[str, Any]:
    """Assess recipe nutrition data readiness without calculation or mutation."""
    try:
        recipe = _nutrition_fetch_recipe(slug)
        assessment = _nutrition_readiness_assessment(
            recipe, include_suggestions=include_suggestions,
        )
    except LookupError:
        return _nutrition_error(
            "nutrition_recipe_not_found", "The requested recipe was not found.",
        )
    except Exception:
        return _nutrition_error(
            "nutrition_calculation_failed", "Nutrition readiness assessment failed.",
        )
    return _nutrition_bound_readiness_output({
        "ok": True,
        "code": "nutrition_readiness_assessed",
        "message": "Nutrition data readiness assessed.",
        "retryable": False,
        "details": {
            "recipe": {
                "id": recipe.get("id"), "slug": recipe.get("slug"),
                "name": recipe.get("name"),
            },
            "assessment": assessment,
            "runtimeProof": {"providerCalls": 0, "writes": 0},
        },
    })


_NUTRITION_CORRECTION_OPERATIONS = {
    "set_recipe_servings", "set_ingredient_quantity", "set_ingredient_food",
    "set_ingredient_unit", "clear_ingredient_unit", "select_ingredient_alternative",
    "set_ingredient_included", "set_ingredient_consumption", "set_ingredient_retention",
}


def _nutrition_authoritative_reference(kind: str, item_id: str) -> dict[str, Any] | None:
    if kind not in {"foods", "units"} or not _nutrition_canonical_uuid4(item_id):
        return None
    response = requests.get(
        f"{config.MEALIE_BASE_URL}/api/{kind}/{item_id}",
        headers=mealie._mealie_headers(), timeout=30,
    )
    if not 200 <= response.status_code < 300:
        return None
    body = response.json()
    if not isinstance(body, dict) or body.get("id") != item_id:
        return None
    name = body.get("name")
    return body if isinstance(name, str) and name.strip() else None


def _nutrition_canonical_uuid4(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = UUID(value)
    except ValueError:
        return False
    return parsed.version == 4 and str(parsed) == value


def _nutrition_url_safe_id(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", value) is not None


def _nutrition_apply_corrections(recipe: dict[str, Any], corrections: list[dict[str, Any]],
                                  *, validate_references: bool,
                                  validated_references: dict[tuple[str, str], dict[str, Any]] | None = None,
                                  ) -> tuple[dict[str, Any] | None,
                                                                         list[str], str | None]:
    candidate = copy.deepcopy(recipe)
    ingredients = candidate.get("recipeIngredient")
    if not isinstance(ingredients, list) or not all(isinstance(item, dict) for item in ingredients):
        return None, [], "recipe_ingredients_invalid"
    by_id = {item.get("id"): item for item in ingredients
             if _nutrition_canonical_uuid4(item.get("id"))}
    changed_paths: list[str] = []
    targets: set[tuple[str, str]] = set()
    operation_fields = {
        "set_recipe_servings": {"operation", "value"},
        "set_ingredient_quantity": {"operation", "value"},
        "set_ingredient_food": {"operation", "food_id"},
        "set_ingredient_unit": {"operation", "unit_id"},
        "clear_ingredient_unit": {"operation"},
        "select_ingredient_alternative": {"operation", "alternative_id"},
        "set_ingredient_included": {"operation", "value"},
        "set_ingredient_consumption": {"operation", "value"},
        "set_ingredient_retention": {"operation", "mode", "value"},
    }
    for correction in corrections:
        if not isinstance(correction, dict):
            return None, [], "unknown_correction_field"
        operation = correction.get("operation")
        if operation not in _NUTRITION_CORRECTION_OPERATIONS:
            return None, [], "operation_not_allowed"
        expected_fields = operation_fields[operation]
        if operation == "set_recipe_servings":
            if set(correction) != expected_fields:
                return None, [], "unknown_correction_field"
        else:
            id_fields = expected_fields | {"ingredient_id"}
            positional_fields = expected_fields | {"ingredient_index", "original_text"}
            if set(correction) not in {frozenset(id_fields), frozenset(positional_fields)}:
                return None, [], "unknown_correction_field"
        ingredient_id = correction.get("ingredient_id")
        item = None
        ingredient_key: str | int = "recipe"
        if operation != "set_recipe_servings":
            if "ingredient_id" in correction:
                if not _nutrition_canonical_uuid4(ingredient_id) or ingredient_id not in by_id:
                    return None, [], "stable_ingredient_id_required"
                item = by_id[ingredient_id]
                ingredient_key = ingredient_id
            else:
                ingredient_index = correction.get("ingredient_index")
                original_text = correction.get("original_text")
                if (not isinstance(ingredient_index, int) or isinstance(ingredient_index, bool)
                        or not 0 <= ingredient_index < len(ingredients)
                        or not isinstance(original_text, str) or not original_text.strip()):
                    return None, [], "bound_ingredient_position_required"
                item = ingredients[ingredient_index]
                if item.get("id") is not None or item.get("originalText") != original_text:
                    return None, [], "bound_ingredient_position_mismatch"
                ingredient_key = ingredient_index
        field = {
            "set_recipe_servings": "recipeServings",
            "set_ingredient_quantity": "quantity",
            "set_ingredient_food": "food",
            "set_ingredient_unit": "unit",
            "clear_ingredient_unit": "unit",
            "select_ingredient_alternative": "selectedAlternative",
            "set_ingredient_included": "included",
            "set_ingredient_consumption": "nonConsumed",
            "set_ingredient_retention": str(correction.get("mode")),
        }[operation]
        target = (str(ingredient_key), field)
        if target in targets:
            return None, [], "duplicate_correction_target"
        targets.add(target)
        path = "/recipeServings" if item is None else f"/recipeIngredient/{ingredient_key}/{field}"
        if operation in {"set_recipe_servings", "set_ingredient_quantity"}:
            value = correction.get("value")
            if (not isinstance(value, (int, float)) or isinstance(value, bool)
                    or not math.isfinite(float(value)) or not 0 < float(value) <= 1_000_000):
                return None, [], "positive_bounded_value_required"
            if item is None:
                candidate["recipeServings"] = value
            else:
                item["quantity"] = value
        elif operation in {"set_ingredient_food", "set_ingredient_unit"}:
            kind, id_field = (("foods", "food_id") if operation.endswith("food")
                              else ("units", "unit_id"))
            reference_id = correction.get(id_field)
            if not _nutrition_canonical_uuid4(reference_id):
                return None, [], "authoritative_reference_required"
            reference_key = (kind, reference_id)
            reference = (_nutrition_authoritative_reference(kind, reference_id)
                         if validate_references else (validated_references or {}).get(reference_key))
            if reference is None:
                return None, [], "authoritative_reference_invalid"
            if validate_references and validated_references is not None:
                validated_references[reference_key] = copy.deepcopy(reference)
            item["food" if kind == "foods" else "unit"] = {
                key: reference[key] for key in ("id", "name", "abbreviation") if key in reference
            }
        elif operation == "clear_ingredient_unit":
            item.pop("unit", None)
        elif operation == "select_ingredient_alternative":
            alternative_id = correction.get("alternative_id")
            alternatives = item.get("alternatives") or item.get("alternativeFoods") or []
            if not _nutrition_url_safe_id(alternative_id):
                return None, [], "alternative_not_found"
            selected = next((value for value in alternatives if isinstance(value, dict)
                             and value.get("id") == alternative_id), None)
            if selected is None:
                return None, [], "alternative_not_found"
            item["selectedAlternative"] = alternative_id
            item["food"] = copy.deepcopy(selected.get("food") or selected)
            changed_paths.append(path)
            path = f"/recipeIngredient/{ingredient_key}/food"
        elif operation == "set_ingredient_included":
            if item.get("optional") is not True or not isinstance(correction.get("value"), bool):
                return None, [], "optional_ingredient_required"
            item["included"] = correction["value"]
        elif operation == "set_ingredient_consumption":
            if correction.get("value") not in {"consumed", "non_consumed"}:
                return None, [], "consumption_value_invalid"
            item["nonConsumed"] = correction["value"] == "non_consumed"
        else:
            mode = correction.get("mode")
            value = correction.get("value")
            fields = {"fraction": "retainedFraction", "grams": "retainedGrams",
                      "absorbed_grams": "absorbedGrams"}
            if (mode not in fields or not isinstance(value, (int, float)) or isinstance(value, bool)
                    or not math.isfinite(float(value)) or float(value) < 0
                    or (mode == "fraction" and float(value) > 1) or float(value) > 1_000_000):
                return None, [], "retention_value_invalid"
            item[fields[mode]] = value
            path = f"/recipeIngredient/{ingredient_key}/{fields[mode]}"
        changed_paths.append(path)
    return candidate, changed_paths, None


def _nutrition_proposed_fields(candidate: dict[str, Any],
                               changed_paths: list[str]) -> list[dict[str, Any]]:
    ingredients = candidate.get("recipeIngredient") or []
    by_id = {
        item.get("id"): item for item in ingredients
        if isinstance(item, dict) and item.get("id") is not None
    }
    fields = []
    for path in changed_paths:
        parts = path.strip("/").split("/")
        if parts == ["recipeServings"]:
            value = candidate.get("recipeServings")
        else:
            key = parts[1]
            item = ingredients[int(key)] if key.isdigit() else by_id.get(key, {})
            value = item.get(parts[2])
        fields.append({"path": path, "value": copy.deepcopy(value)})
    return fields


def _nutrition_stage_correction(slug: str, candidate: dict[str, Any] | None,
                                schedule: dict[str, Any] | None = None) -> dict[str, Any] | None:
    with _NUTRITION_CORRECTION_STAGE_LOCK:
        existing_schedule = (_NUTRITION_CORRECTION_STAGES.get(slug) or {}).get("schedule")
        if slug not in _NUTRITION_CORRECTION_STAGES and len(_NUTRITION_CORRECTION_STAGES) >= 64:
            _NUTRITION_CORRECTION_STAGES.pop(next(iter(_NUTRITION_CORRECTION_STAGES)))
        active_schedule = schedule or existing_schedule
        _NUTRITION_CORRECTION_STAGES[slug] = {
            "candidate": copy.deepcopy(candidate), "schedule": copy.deepcopy(active_schedule),
        }
        return copy.deepcopy(active_schedule)


@tool("legacy_nutrition")
def nutrition_correct_recipe(slug: str, expected_revision: dict[str, str | None],
                              corrections: list[dict[str, Any]],
                              apply: bool = False) -> dict[str, Any]:
    """Preview or apply revision-bound typed corrections to a Mealie recipe."""

    if (not isinstance(expected_revision, dict)
            or set(expected_revision) != {"fingerprint", "updatedAt"}
            or not isinstance(corrections, list) or not 1 <= len(corrections) <= 10
            or not isinstance(apply, bool)):
        return _nutrition_failure(
            "nutrition_correction_invalid", "Correction request is invalid.", stage="validation",
        )
    try:
        current = _nutrition_fetch_recipe(slug)
    except Exception:
        return _nutrition_failure(
            "nutrition_recipe_not_found", "The requested recipe was not found.", stage="fetch",
        )
    current_revision = _nutrition_recipe_revision(current)
    if current_revision != expected_revision:
        continuation = _nutrition_continuation("nutrition_correct_recipe", {
            "slug": slug, "expected_revision": current_revision,
            "corrections": corrections, "apply": apply,
        })
        return _nutrition_failure(
            "nutrition_recipe_revision_conflict", "Recipe changed before correction.",
            stage="revision", continuation=continuation,
            details={"expectedRevision": expected_revision, "currentRevision": current_revision},
        )
    validated_references: dict[tuple[str, str], dict[str, Any]] = {}
    candidate, changed_paths, invalid = _nutrition_apply_corrections(
        current, corrections, validate_references=True,
        validated_references=validated_references,
    )
    if invalid:
        return _nutrition_failure(
            "nutrition_correction_invalid", "Correction request is not allowed.",
            stage="validation", details={"reason": invalid},
        )
    candidate_revision = _nutrition_recipe_revision(candidate)
    consequence = {
        "sidecarCalls": 0, "providerCalls": 0, "writes": 0,
        "recipePatch": False, "changedPaths": changed_paths,
    }
    continuation = _nutrition_continuation("nutrition_correct_recipe", {
        "slug": slug, "expected_revision": current_revision,
        "corrections": corrections, "apply": True,
    })
    try:
        readiness = _nutrition_readiness_assessment(candidate, include_suggestions=True)
    except Exception:
        readiness = {"ready": False, "root": "assessment_unavailable"}
    proposal = {
        "corrections": copy.deepcopy(corrections),
        "fields": _nutrition_proposed_fields(candidate, changed_paths),
    }
    if not apply:
        _nutrition_stage_correction(slug, candidate)
        return {
            "ok": True, "code": "nutrition_recipe_correction_preview",
            "message": "Correction preview created without writes.", "retryable": False,
            "stage": "preview", "details": {
                "currentRevision": current_revision, "candidateRevision": candidate_revision,
                "changedPaths": changed_paths, "readiness": readiness,
                "correctionProposal": proposal, "consequences": consequence,
                "runtimeProof": {"writes": 0, "providerCalls": 0},
            }, "continuation": continuation,
        }
    try:
        final = _nutrition_fetch_recipe(slug)
    except Exception:
        return _nutrition_failure(
            "nutrition_recipe_not_found", "The requested recipe was not found.", stage="fetch",
            continuation=continuation,
        )
    final_revision = _nutrition_recipe_revision(final)
    if final_revision != expected_revision:
        return _nutrition_failure(
            "nutrition_recipe_revision_conflict", "Recipe changed before correction.",
            stage="revision", continuation=continuation,
            details={"expectedRevision": expected_revision, "currentRevision": final_revision},
        )
    candidate, changed_paths, invalid = _nutrition_apply_corrections(
        final, corrections, validate_references=False,
        validated_references=validated_references,
    )
    if invalid:
        return _nutrition_failure(
            "nutrition_correction_invalid", "Correction request is not allowed.",
            stage="validation", details={"reason": invalid},
        )
    try:
        readiness = _nutrition_readiness_assessment(candidate, include_suggestions=True)
    except Exception:
        readiness = {"ready": False, "root": "assessment_unavailable"}
    changed_paths = list(dict.fromkeys(changed_paths))
    proposal = {
        "corrections": copy.deepcopy(corrections),
        "fields": _nutrition_proposed_fields(candidate, changed_paths),
    }
    readiness_continuation = _nutrition_continuation(
        "nutrition_recipe_readiness", {"slug": slug, "include_suggestions": True},
    )
    changed_roots = {path.strip("/").split("/")[0] for path in changed_paths}
    patch_payload = {key: copy.deepcopy(candidate.get(key)) for key in changed_roots}
    rollback_payload = {key: copy.deepcopy(final.get(key)) for key in changed_roots}

    try:
        response = requests.patch(
            f"{config.MEALIE_BASE_URL}/api/recipes/{slug}", headers=mealie._mealie_headers(),
            json=patch_payload, timeout=30,
        )
    except requests.RequestException:
        return _nutrition_failure(
            "nutrition_correction_patch_failed", "Mealie correction request failed safely.",
            stage="patch", continuation=continuation,
            details={"changedPaths": changed_paths},
        )
    if not 200 <= response.status_code < 300:
        return _nutrition_failure(
            "nutrition_correction_patch_failed", "Mealie rejected the exact correction patch.",
            stage="patch", continuation=continuation,
            details={"status": response.status_code, "changedPaths": changed_paths},
        )

    def semantic(value: dict[str, Any]) -> dict[str, Any]:
        comparable = copy.deepcopy(value)
        comparable.pop("updatedAt", None)
        comparable.pop("dateUpdated", None)
        for instruction in comparable.get("recipeInstructions") or []:
            if isinstance(instruction, dict):
                instruction.pop("id", None)
        return comparable

    try:
        postimage = _nutrition_fetch_recipe(slug)
    except Exception:
        postimage = {}
    expected_postimage = copy.deepcopy(final)
    expected_postimage.update(patch_payload)
    if not postimage or semantic(postimage) != semantic(expected_postimage):
        try:
            rollback = requests.patch(
                f"{config.MEALIE_BASE_URL}/api/recipes/{slug}", headers=mealie._mealie_headers(),
                json=rollback_payload, timeout=30,
            )
        except requests.RequestException:
            rollback = None
        rollback_verified = False
        if rollback is not None and 200 <= rollback.status_code < 300:
            try:
                rollback_verified = semantic(_nutrition_fetch_recipe(slug)) == semantic(final)
            except Exception:
                rollback_verified = False
        return _nutrition_failure(
            "nutrition_correction_postimage_mismatch",
            "Correction verification failed; the exact preimage was restored when possible.",
            stage="verification", continuation=continuation,
            details={"changedPaths": changed_paths, "rollbackVerified": rollback_verified},
        )

    consequence.update({"writes": 1, "recipePatch": True})
    schedule = _nutrition_stage_correction(slug, postimage)
    continuations = {"readiness": readiness_continuation}
    if schedule:
        continuations["schedule"] = copy.deepcopy(schedule)
    return {
        "ok": True, "code": "nutrition_recipe_corrected",
        "message": "Typed recipe corrections were applied and verified.",
        "retryable": False, "stage": "complete",
        "details": {
            "previousRevision": final_revision,
            "revision": _nutrition_recipe_revision(postimage),
            "changedPaths": changed_paths, "readiness": readiness,
            "correctionProposal": proposal, "consequences": consequence,
            "runtimeProof": {"writes": 1, "providerCalls": 0},
        },
        "continuation": readiness_continuation, "continuations": continuations,
    }


def _nutrition_calculate_slug(slug: str, *, publish: bool = False,
                              force: bool = False) -> dict[str, Any]:
    try:
        recipe = _nutrition_fetch_recipe(slug)
        service = _nutrition_service()
        return service.publish(recipe, force=force) if publish else service.calculate(recipe, force=force)
    except LookupError:
        return _nutrition_error("nutrition_recipe_not_found", f"Recipe '{slug}' was not found.")
    except Exception as exc:
        return _nutrition_error("nutrition_calculation_failed", "Nutrition calculation failed.",
                                details={"reason": type(exc).__name__})


def _nutrition_correction_action(recipe: dict[str, Any], assessment: dict[str, Any],
                                 revision: dict[str, Any]) -> dict[str, Any]:
    issues = assessment.get("issues") or []
    root = issues[0].get("root") if issues else "nutrition_recipe_correction_required"
    ingredient_id = issues[0].get("ingredientId") if issues else None
    ingredient = next((item for item in recipe.get("recipeIngredient") or []
                       if isinstance(item, dict) and item.get("id") == ingredient_id), {})
    food_id = (ingredient.get("food") or {}).get("id")
    if root in {"profile_provider_evidence", "conversion_evidence_missing"} and food_id:
        return {
            "kind": "review_food_evidence", "root": root, "mealieFoodId": food_id,
            "allowedTools": ["nutrition_set_food_mapping", "nutrition_set_food_profile",
                             "nutrition_set_conversion_override"],
            "continuation": _nutrition_continuation(
                "nutrition_food_profile", {"mealie_food_id": food_id},
            ),
        }
    allowed = {
        "parser_loss": ["set_ingredient_quantity", "set_ingredient_food", "set_ingredient_unit",
                        "clear_ingredient_unit"],
        "missing_quantity": ["set_ingredient_quantity"],
        "food_selection_required": ["select_ingredient_alternative", "set_ingredient_food"],
        "missing_servings": ["set_recipe_servings"],
        "conversion_evidence_missing": ["set_ingredient_consumption", "set_ingredient_retention"],
    }.get(root, [])
    fields = {
        "set_recipe_servings": ["operation", "value"],
        "set_ingredient_quantity": ["operation", "ingredient_id", "value"],
        "set_ingredient_food": ["operation", "ingredient_id", "food_id"],
        "set_ingredient_unit": ["operation", "ingredient_id", "unit_id"],
        "clear_ingredient_unit": ["operation", "ingredient_id"],
        "select_ingredient_alternative": ["operation", "ingredient_id", "alternative_id"],
        "set_ingredient_consumption": ["operation", "ingredient_id", "value"],
        "set_ingredient_retention": ["operation", "ingredient_id", "mode", "value"],
    }
    return {
        "kind": "correct_recipe", "root": root, "allowedOperations": allowed,
        "operationFields": {operation: fields[operation] for operation in allowed},
        "expectedRevision": revision,
        "continuation": _nutrition_continuation("nutrition_recipe_readiness", {
            "slug": recipe.get("slug"), "include_suggestions": True,
        }),
    }


def ensure_recipe_nutrition(slug: str, trigger: Any) -> dict[str, Any]:
    """Ensure one recipe is deterministically ready without persisted workflow state."""

    continuation = trigger if isinstance(trigger, dict) and {
        "tool", "arguments"
    }.issubset(trigger) else _nutrition_continuation(
        "nutrition_calculate_recipe", {"slug": slug, "publish": False, "force": False},
    )
    lock = _keyed_lock(_NUTRITION_RECIPE_LOCKS, slug)
    with lock:
        try:
            recipe = _nutrition_fetch_recipe(slug)
            revision = _nutrition_recipe_revision(recipe)
            assessment = _nutrition_readiness_assessment(recipe, include_suggestions=True)
        except LookupError:
            return _nutrition_failure(
                "nutrition_recipe_not_found", "The requested recipe was not found.",
                stage="fetch", continuation=continuation,
            )
        except Exception:
            return _nutrition_failure(
                "nutrition_calculation_failed", "Nutrition readiness assessment failed.",
                stage="readiness", continuation=continuation, retryable=True,
            )

        gates = assessment.get("gates") or {}
        if not gates.get("sourceStructure", False):
            action = _nutrition_correction_action(recipe, assessment, revision)
            if continuation.get("tool") == "set_mealplan_entry":
                _nutrition_stage_correction(slug, None, continuation)
            return _nutrition_failure(
                "nutrition_recipe_correction_required",
                "Recipe structure must be corrected before nutrition calculation.",
                stage="readiness", continuation=continuation, actions=[action],
                details={"revision": revision, "issues": assessment.get("issues", [])},
            )

        try:
            from nutrition.providers import ProviderBudget

            service = _nutrition_service()
            with ProviderBudget(20) as budget:
                calculated = service.calculate(recipe, force=False)
        except Exception:
            return _nutrition_failure(
                "nutrition_calculation_failed", "Nutrition calculation failed.",
                stage="calculation", continuation=continuation, retryable=True,
            )
        if not calculated.get("ok"):
            code = calculated.get("code", "nutrition_calculation_failed")
            return _nutrition_failure(
                code, "Nutrition provider work did not complete.", stage="provider",
                continuation=continuation, retryable=bool(calculated.get("retryable")),
                retry_after=(calculated.get("retryAfter")
                             or (calculated.get("details") or {}).get("retryAfter")),
                details={"providerAttempts": budget.attempts},
            )

        try:
            reassessed = _nutrition_readiness_assessment(recipe, include_suggestions=True)
        except Exception:
            reassessed = assessment
        if not (reassessed.get("gates") or {}).get("evidence", True):
            action = _nutrition_correction_action(recipe, reassessed, revision)
            return _nutrition_failure(
                "nutrition_evidence_required", "Nutrition evidence requires review.",
                stage="evidence", continuation=continuation, actions=[action],
                details={"revision": revision, "issues": reassessed.get("issues", []),
                         "providerAttempts": budget.attempts},
            )

        eligibility = service.eligibility(calculated["calculation"])
        if not eligibility["eligible"]:
            return _nutrition_failure(
                eligibility["code"], "Nutrition did not pass the deterministic threshold.",
                stage="threshold", continuation=continuation,
                details={**eligibility, "providerAttempts": budget.attempts},
            )
        latest = _nutrition_fetch_recipe(slug)
        latest_revision = _nutrition_recipe_revision(latest)
        if latest_revision != revision:
            return _nutrition_failure(
                "nutrition_revision_conflict", "Recipe changed during nutrition enrichment.",
                stage="revision", continuation=continuation,
                details={"expectedRevision": revision, "currentRevision": latest_revision},
            )

        publication = {"attempted": False, "published": False}
        provenance = reassessed.get("publicationProvenance", "unknown")
        protected = __import__(
            "nutrition.protection", fromlist=["nutrition_publication_protected"]
        ).nutrition_publication_protected(latest)
        if (config.SABOR_NUTRITION_AUTO_PUBLISH and not protected
                and provenance in {"absent", "owned"} and service.publisher):
            publication = service.publish_calculation(latest, calculated["calculation"])
            publication["attempted"] = True
            if publication.get("published"):
                latest = _nutrition_fetch_recipe(slug)
                latest_revision = _nutrition_recipe_revision(latest)
        return {
            "ok": True, "code": "nutrition_ready", "message": "Nutrition is ready.",
            "retryable": False, "stage": "complete", "actions": [],
            "revision": latest_revision, "eligibility": eligibility,
            "publication": publication, "providerAttempts": budget.attempts,
        }


def _nutrition_after_import(import_result: dict[str, Any], slug: str | None) -> dict[str, Any]:
    if not (config.SABOR_NUTRITION_ENABLED and config.SABOR_NUTRITION_AUTO_ON_IMPORT and slug):
        return import_result
    try:
        nutrition = ensure_recipe_nutrition(slug, "import")
        import_result["nutrition"] = nutrition
        publication = nutrition.get("publication") or {}
        failure = (publication if publication.get("attempted") and not publication.get("ok")
                   else nutrition if not nutrition.get("ok") else None)
        if failure:
            import_result["nutrition_warning"] = {
                "code": failure.get("code", "nutrition_calculation_failed"),
                "stage": ("publication" if failure is publication
                          else nutrition.get("stage", "unknown")),
                "actions": failure.get("actions", nutrition.get("actions", [])),
                "continuation": failure.get("continuation", nutrition.get("continuation")),
            }
    except Exception:
        import_result["nutrition"] = _nutrition_failure(
            "nutrition_calculation_failed", "Nutrition enrichment failed.", stage="unknown",
        )
        import_result["nutrition_warning"] = {
            "code": "nutrition_calculation_failed", "stage": "unknown", "actions": [],
        }
    return import_result


@tool("legacy_nutrition")
def nutrition_status() -> dict[str, Any]:
    """Report nutrition feature flags without initializing the sidecar while disabled."""
    result = {
        "ok": True, "enabled": config.SABOR_NUTRITION_ENABLED,
        "autoOnImport": config.SABOR_NUTRITION_AUTO_ON_IMPORT,
        "autoPublish": config.SABOR_NUTRITION_AUTO_PUBLISH,
        "publishEstimates": config.SABOR_NUTRITION_PUBLISH_ESTIMATES,
        "provider": config.SABOR_NUTRITION_PROVIDER,
    }
    if not config.SABOR_NUTRITION_ENABLED:
        result["status"] = "disabled"
        return result
    try:
        service = _nutrition_service()
        with service.db.connect() as connection:
            result["schemaVersion"] = connection.execute(
                "SELECT MAX(version) FROM schema_version"
            ).fetchone()[0]
            result["profiles"] = connection.execute(
                "SELECT COUNT(*) FROM nutrient_profile"
            ).fetchone()[0]
        result["status"] = "ready"
        return result
    except Exception as exc:
        return _nutrition_error("nutrition_calculation_failed",
                                "Nutrition sidecar status failed.",
                                details={"reason": type(exc).__name__})


@tool("legacy_nutrition")
def nutrition_food_profile(mealie_food_id: str) -> dict[str, Any]:
    """Read cached resolution, profile, and conversions for an exact Mealie food ID."""
    if not config.SABOR_NUTRITION_ENABLED:
        return _nutrition_error("nutrition_disabled", "Nutrition is disabled.")
    from nutrition.db import normalize_mealie_food_id
    try:
        mealie_food_id = normalize_mealie_food_id(mealie_food_id)
    except ValueError:
        return _nutrition_error("nutrition_manual_evidence_invalid", "Mealie food ID is required.")
    service = _nutrition_service()
    resolution = service.db.get_resolution(mealie_food_id)
    profile = service.db.get_profile(mealie_food_id)
    return {"ok": True, "code": "nutrition_food_profile",
            "message": "Cached food evidence returned.", "retryable": False,
            "details": {"resolution": resolution, "profile": profile,
                        "conversions": service.db.list_conversions(mealie_food_id)}}


@tool("legacy_nutrition")
def nutrition_set_food_profile(
        mealie_food_id: str, calories_kcal: float | None = None,
        protein_g: float | None = None, carbohydrate_g: float | None = None,
        net_carbohydrate_g: float | None = None, fat_g: float | None = None,
        saturated_fat_g: float | None = None, unsaturated_fat_g: float | None = None,
        trans_fat_g: float | None = None, fiber_g: float | None = None,
        sugar_g: float | None = None, sodium_mg: float | None = None,
        cholesterol_mg: float | None = None, basis_grams: float = 100,
        confidence: float | None = None, source_note: str = "",
        source_version: str = "", apply: bool = False) -> dict[str, Any]:
    """Preview or apply a reviewed manual per-100-g nutrient profile."""
    from nutrition.db import ManualMappingTargetError
    from nutrition.models import NUTRIENT_FIELDS, is_finite_number

    nutrients = {
        "calories_kcal": calories_kcal, "protein_g": protein_g,
        "carbohydrate_g": carbohydrate_g, "net_carbohydrate_g": net_carbohydrate_g,
        "fat_g": fat_g, "saturated_fat_g": saturated_fat_g,
        "unsaturated_fat_g": unsaturated_fat_g, "trans_fat_g": trans_fat_g,
        "fiber_g": fiber_g, "sugar_g": sugar_g, "sodium_mg": sodium_mg,
        "cholesterol_mg": cholesterol_mg,
    }
    try:
        parsed_id = UUID(mealie_food_id)
        canonical_id = str(parsed_id)
    except (TypeError, ValueError, AttributeError):
        canonical_id = ""
    source_note = source_note.strip() if isinstance(source_note, str) else ""
    source_version = source_version.strip() if isinstance(source_version, str) else ""
    present = {key: value for key, value in nutrients.items() if value is not None}
    valid_numbers = (
        is_finite_number(basis_grams) and basis_grams == 100
        and is_finite_number(confidence) and 0 <= confidence <= 1
        and all(is_finite_number(value) and value >= 0 for value in present.values())
    )
    valid_identity = (
        bool(canonical_id) and parsed_id.version == 4 and canonical_id == mealie_food_id
    )
    has_required_nutrients = (
        calories_kcal is not None
        and any(nutrients[field] is not None
                for field in ("protein_g", "carbohydrate_g", "fat_g"))
    )
    if (not valid_identity or not valid_numbers or not has_required_nutrients
            or not source_note or len(source_note) > 1000
            or not source_version or len(source_version) > 256):
        return _nutrition_error(
            "nutrition_manual_evidence_invalid",
            "A canonical Mealie UUID, 100 g basis, nonnegative nutrients, calories plus one "
            "macro, confidence from 0 to 1, and bounded provenance are required.",
            details={"mealieFoodId": canonical_id or "invalid"},
        )
    normalized_nutrients = {
        field: (float(nutrients[field]) if nutrients[field] is not None else None)
        for field in NUTRIENT_FIELDS
    }
    hash_input = {
        "basis_grams": 100.0, "nutrients": normalized_nutrients,
        "confidence": float(confidence), "source_version": source_version,
        "provider": "manual", "manual_override": True,
    }
    payload_hash = hashlib.sha256(json.dumps(
        hash_input, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")).hexdigest()
    if apply and not config.SABOR_NUTRITION_ENABLED:
        return _nutrition_error("nutrition_disabled", "Nutrition is disabled.")
    if apply:
        db = _nutrition_service().db
    else:
        from nutrition.db import NutritionDB
        db = None
        sidecar_target, existing = NutritionDB.profile_preview_at(
            config.SABOR_NUTRITION_DB, canonical_id,
        )
        target_valid = sidecar_target is not None
    if db is not None:
        target_valid = db.is_mapping_target_valid(canonical_id)
        resolution = db.get_resolution(canonical_id) if target_valid else None
    else:
        resolution = None
    target = sidecar_target if db is None else None
    if target_valid and resolution and resolution.get("mealie_food_name"):
        target = {"id": canonical_id, "name": resolution["mealie_food_name"],
                  "source": "sidecar"}
    elif target_valid and db is not None:
        target = db.get_profile_target(canonical_id)
    if target is None:
        target, error = _authoritative_mealie_food(canonical_id, {"mealieFoodId": canonical_id})
        if error:
            return error
        if target is None:
            return _nutrition_error(
                "nutrition_mapping_target_unknown", "The exact Mealie food target is unknown.",
                details={"mealieFoodId": canonical_id, "targetValid": False},
            )
        target_valid = True
    if db is not None:
        existing = db.get_profile(canonical_id) if target_valid else None
    details = {
        "target": target, "targetValid": True,
        "profile": {"mealieFoodId": canonical_id, "basisGrams": 100.0,
                    "nutrients": normalized_nutrients, "confidence": float(confidence),
                    "provider": "manual", "manualOverride": True,
                    "sourceNote": source_note, "sourceVersion": source_version},
        "payloadHash": payload_hash,
        "overwriteImpact": ("refresh_manual" if existing and existing.get("manual_override")
                            else "replace_provider" if existing else "create"),
        "precedence": "manual_over_provider",
    }
    if existing:
        details["priorEvidence"] = {
            "payloadHash": existing.get("payload_hash"),
            "sourceVersion": existing.get("source_version"),
        }
    if not apply:
        return {"ok": True, "code": "nutrition_profile_preview",
                "message": "No sidecar write was made.", "retryable": False,
                "details": details}
    try:
        prior = db.set_manual_profile(
            canonical_id, normalized_nutrients, 100.0, float(confidence), source_version,
            source_note, payload_hash, target_validated=target["source"] == "mealie",
            target_name=target["name"],
        )
    except ManualMappingTargetError:
        return _nutrition_error(
            "nutrition_mapping_target_unknown",
            "Manual profile target evidence was concurrently removed.",
            details={"mealieFoodId": canonical_id, "targetValid": False},
        )
    details["priorEvidence"] = ({
        "payloadHash": prior.get("payload_hash"),
        "sourceVersion": prior.get("source_version"),
    } if prior else None)
    return {"ok": True, "code": "nutrition_profile_saved",
            "message": "Reviewed manual nutrient profile saved.", "retryable": False,
            "details": details}


@tool("legacy_nutrition")
def nutrition_set_food_mapping(mealie_food_id: str, mealie_food_name: str,
                               canonical_term: str, confidence: float | None = None,
                               source_note: str = "", provider_food_id: str | None = None,
                               apply: bool = False) -> dict[str, Any]:
    """Preview or apply an exact manual canonical mapping; apply is required for mutation."""
    from nutrition.db import normalize_mealie_food_id, normalize_provider_food_id

    try:
        mealie_food_id = normalize_mealie_food_id(mealie_food_id)
    except ValueError:
        mealie_food_id = ""
    normalized_provider_food_id = None
    if provider_food_id is not None:
        try:
            normalized_provider_food_id = normalize_provider_food_id(provider_food_id)
        except ValueError:
            normalized_provider_food_id = ""
    details = {"mealieFoodId": mealie_food_id, "mealieFoodName": mealie_food_name,
               "canonicalTerm": canonical_term, "confidence": confidence,
               "sourceNote": source_note, "providerFoodId": normalized_provider_food_id}
    if (not all(str(value).strip() for value in (mealie_food_id, mealie_food_name, canonical_term,
                                                  source_note))
            or not isinstance(confidence, (int, float)) or isinstance(confidence, bool)
            or not math.isfinite(float(confidence)) or not 0 <= float(confidence) <= 1
            or (provider_food_id is not None and not normalized_provider_food_id)):
        return _nutrition_error(
            "nutrition_manual_evidence_invalid",
            "Exact mapping fields, confidence from 0 to 1, and source_note are required.",
            details=details,
        )
    if apply and not config.SABOR_NUTRITION_ENABLED:
        return _nutrition_error("nutrition_disabled", "Nutrition is disabled.")
    if apply:
        db = _nutrition_service().db
        target_valid = db.is_mapping_target_valid(mealie_food_id)
    elif _NUTRITION_SERVICE_INSTANCE is not None:
        db = _NUTRITION_SERVICE_INSTANCE.db
        target_valid = db.is_mapping_target_valid(mealie_food_id)
    else:
        from nutrition.db import NutritionDB
        db = None
        target_valid = NutritionDB.mapping_target_valid_at(config.SABOR_NUTRITION_DB, mealie_food_id)

    target = {"id": mealie_food_id, "name": mealie_food_name, "source": "sidecar"}
    if not target_valid:
        target, error = _authoritative_mealie_food(mealie_food_id, details)
        if error:
            return error
        if target is None:
            details["targetValid"] = False
            if not apply:
                return {"ok": True, "code": "nutrition_mapping_preview",
                        "message": "No sidecar write was made.", "retryable": False,
                        "details": details}
            return _nutrition_error(
                "nutrition_mapping_target_unknown",
                "Manual mapping target has no sidecar evidence and is not a canonical Mealie UUID.",
                details=details,
            )
        target_valid = True
    details.update({"targetValid": target_valid, "target": target})
    if not apply:
        return {"ok": True, "code": "nutrition_mapping_preview",
                "message": "No sidecar write was made.", "retryable": False, "details": details}
    authoritative = target["source"] == "mealie"
    if authoritative and mealie_food_name.strip() != target["name"]:
        return _nutrition_error(
            "nutrition_mapping_target_mismatch",
            "Caller food name did not match the authoritative Mealie food name.",
            details=details,
        )
    from nutrition.db import ManualMappingTargetError
    try:
        changed = db.upsert_resolution(
            mealie_food_id, target["name"], canonical_term, "manual", float(confidence),
            "manual", True, source_note=source_note, provider="spoonacular",
            provider_food_id=normalized_provider_food_id, target_validated=authoritative,
        )
    except ManualMappingTargetError:
        details["targetValid"] = False
        return _nutrition_error(
            "nutrition_mapping_target_unknown",
            "Manual mapping target evidence was concurrently removed.",
            details=details,
        )
    return {"ok": changed, "code": "nutrition_mapping_saved",
            "message": "Manual canonical mapping saved.", "retryable": False, "details": details}


@tool("legacy_nutrition")
def nutrition_set_conversion_override(mealie_food_id: str, conversion_type: str,
                                       unit_key: str, grams_per_unit: float,
                                       confidence: float | None = None,
                                       source_note: str = "", apply: bool = False) -> dict[str, Any]:
    """Preview or apply a manual food conversion; apply is required for mutation."""
    from nutrition.conversions import canonical_unit
    from nutrition.db import normalize_mealie_food_id

    try:
        mealie_food_id = normalize_mealie_food_id(mealie_food_id)
    except ValueError:
        mealie_food_id = ""
    canonical_key, _ = canonical_unit(unit_key)
    details = {"mealieFoodId": mealie_food_id, "conversionType": conversion_type,
               "unitKey": canonical_key, "gramsPerUnit": grams_per_unit,
               "confidence": confidence, "sourceNote": source_note}
    if (not str(mealie_food_id).strip() or conversion_type not in {
            "density", "piece_weight", "package_weight", "culinary_measure"
        } or not canonical_key or not isinstance(grams_per_unit, (int, float))
            or isinstance(grams_per_unit, bool) or not math.isfinite(float(grams_per_unit))
            or grams_per_unit <= 0
            or not isinstance(confidence, (int, float)) or isinstance(confidence, bool)
            or not math.isfinite(float(confidence)) or not 0 <= float(confidence) <= 1
            or not source_note.strip()):
        return _nutrition_error(
            "nutrition_manual_evidence_invalid",
            "Valid conversion fields, confidence from 0 to 1, and source_note are required.",
            details=details,
        )
    if not apply:
        return {"ok": True, "code": "nutrition_conversion_preview",
                "message": "No sidecar write was made.", "retryable": False, "details": details}
    if not config.SABOR_NUTRITION_ENABLED:
        return _nutrition_error("nutrition_disabled", "Nutrition is disabled.")
    try:
        changed = _nutrition_service().db.upsert_conversion(
            mealie_food_id, conversion_type, canonical_key, grams_per_unit, float(confidence),
            "manual", manual_override=True, source_note=source_note,
        )
    except (ValueError, TypeError):
        return _nutrition_error("nutrition_conversion_missing", "Invalid conversion override.")
    return {"ok": changed, "code": "nutrition_conversion_saved",
            "message": "Manual conversion override saved.", "retryable": False, "details": details}


@tool("legacy_nutrition")
def nutrition_calculate_recipe(slug: str, publish: bool = False,
                               force: bool = False) -> dict[str, Any]:
    """Calculate nutrition evidence; Mealie mutation requires publish=true."""
    if not config.SABOR_NUTRITION_ENABLED:
        return _nutrition_error("nutrition_disabled", "Nutrition is disabled.")
    return _nutrition_calculate_slug(slug, publish=publish, force=force)


@tool("legacy_nutrition")
def nutrition_publish_recipe(slug: str, publish: bool = False,
                             force: bool = False) -> dict[str, Any]:
    """Publish per-serving nutrition only when publish=true and policy gates pass."""
    if not publish:
        return _nutrition_error("nutrition_mutation_not_requested",
                                "Set publish=true to permit a Mealie PATCH.")
    if not config.SABOR_NUTRITION_ENABLED:
        return _nutrition_error("nutrition_disabled", "Nutrition is disabled.")
    return _nutrition_calculate_slug(slug, publish=True, force=force)


def _nutrition_personal_recipes(cookbook_name: str) -> list[dict[str, Any]]:
    cookbooks = mealie._get_all_mealie_items("/api/households/cookbooks")
    matches = [item for item in cookbooks if mealie._catalog_key(item.get("name")) == mealie._catalog_key(cookbook_name)]
    if len(matches) != 1 or not matches[0].get("id"):
        raise LookupError(f"cookbook '{cookbook_name}' was not uniquely resolved")
    return mealie._get_all_mealie_items(f"/api/recipes?cookbook={matches[0]['id']}")


def _nutrition_readiness_cookbook_summaries(cookbook_name: str,
                                            limit: int) -> list[dict[str, Any]]:
    cookbook_response = requests.get(
        f"{config.MEALIE_BASE_URL}/api/households/cookbooks", headers=mealie._mealie_headers(),
        params={"page": 1, "perPage": 100}, timeout=30,
    )
    if not 200 <= cookbook_response.status_code < 300:
        raise RuntimeError("cookbook read failed")
    cookbook_body = cookbook_response.json()
    cookbooks = cookbook_body.get("items") if isinstance(cookbook_body, dict) else None
    if not isinstance(cookbooks, list):
        raise ValueError("invalid cookbook response")
    matches = [
        item for item in cookbooks if isinstance(item, dict)
        and mealie._catalog_key(item.get("name")) == mealie._catalog_key(cookbook_name)
    ]
    if len(matches) != 1 or not matches[0].get("id"):
        raise LookupError("cookbook was not uniquely resolved")
    recipe_response = requests.get(
        f"{config.MEALIE_BASE_URL}/api/recipes", headers=mealie._mealie_headers(),
        params={"cookbook": matches[0]["id"], "page": 1, "perPage": limit,
                "orderBy": "name", "orderDirection": "asc"}, timeout=30,
    )
    if not 200 <= recipe_response.status_code < 300:
        raise RuntimeError("cookbook recipe read failed")
    recipe_body = recipe_response.json()
    summaries = recipe_body.get("items") if isinstance(recipe_body, dict) else None
    if not isinstance(summaries, list):
        raise ValueError("invalid cookbook recipe response")
    return [item for item in summaries[:limit] if isinstance(item, dict) and item.get("slug")]


@tool("legacy_nutrition")
def nutrition_cookbook_readiness(cookbook_name: str = "Mis recetas", limit: int = 100,
                                 detail_limit: int = 25) -> dict[str, Any]:
    """Return a bounded, privacy-safe nutrition readiness inventory."""
    if (not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100
            or not isinstance(detail_limit, int) or isinstance(detail_limit, bool)
            or not 0 <= detail_limit <= 25):
        return _nutrition_error(
            "nutrition_manual_evidence_invalid",
            "limit must be 1..100 and detail_limit must be 0..25.",
        )
    try:
        from nutrition.readiness import PUBLICATION_PROVENANCE_STATUSES

        summaries = _nutrition_readiness_cookbook_summaries(cookbook_name, limit)
        cards: list[tuple[str, str, dict[str, Any]]] = []
        root_counts: dict[str, int] = {}
        gate_counts: dict[str, dict[str, int]] = {}
        publication_provenance_counts = {
            status: 0 for status in PUBLICATION_PROVENANCE_STATUSES
        }
        fingerprints: list[tuple[str, str]] = []
        for summary in summaries:
            recipe = _nutrition_fetch_recipe(str(summary["slug"]))
            assessment = _nutrition_readiness_assessment(
                recipe, include_suggestions=False,
            )
            for root, count in assessment["rootCounts"].items():
                root_counts[root] = root_counts.get(root, 0) + count
            for gate, passed in assessment["gates"].items():
                counts = gate_counts.setdefault(gate, {"passed": 0, "blocked": 0})
                counts["passed" if passed else "blocked"] += 1
            publication_provenance = assessment.get("publicationProvenance")
            if publication_provenance not in publication_provenance_counts:
                publication_provenance = "unknown"
            publication_provenance_counts[publication_provenance] += 1
            slug = str(recipe.get("slug") or summary["slug"])
            fingerprints.append((slug, assessment["fingerprint"]))
            card_issues = assessment["issues"][:config._NUTRITION_COOKBOOK_CARD_ISSUE_LIMIT]
            card_warnings = assessment["warnings"][:config._NUTRITION_COOKBOOK_CARD_ISSUE_LIMIT]
            card = _nutrition_bound_readiness_output({
                "id": recipe.get("id"), "slug": slug, "name": recipe.get("name"),
                "root": assessment["root"], "ready": assessment["ready"],
                "gates": assessment["gates"], "rootCounts": assessment["rootCounts"],
                "publicationProvenance": publication_provenance,
                "score": assessment["score"], "fingerprint": assessment["fingerprint"],
                "issueCount": assessment["issueCount"],
                "omittedIssueCount": assessment["issueCount"] - len(card_issues),
                "warningCount": assessment["warningCount"],
                "omittedWarningCount": assessment["warningCount"] - len(card_warnings),
                "issues": [{
                    key: issue.get(key) for key in
                    ("root", "blocking", "reason", "position", "ingredientId")
                    if key in issue
                } for issue in card_issues],
                "warnings": card_warnings,
            })
            cards.append((slug, str(recipe.get("id") or ""), card))
        aggregate_input = {
            "cookbook": mealie._catalog_key(cookbook_name), "limit": limit,
            "rootCounts": root_counts, "gateCounts": gate_counts,
            "publicationProvenanceCounts": publication_provenance_counts,
            "recipes": sorted(fingerprints),
        }
        fingerprint = hashlib.sha256(json.dumps(
            aggregate_input, sort_keys=True, ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")).hexdigest()
    except Exception:
        return _nutrition_error(
            "nutrition_calculation_failed", "Cookbook readiness assessment failed.",
        )
    cards.sort(key=lambda item: item[:2])
    bounded_cards = [item[2] for item in cards[:detail_limit]]
    return _nutrition_bound_readiness_output({
        "ok": True,
        "code": "nutrition_cookbook_readiness_assessed",
        "message": "Cookbook nutrition data readiness assessed.",
        "retryable": False,
        "details": {
            "cookbook": cookbook_name, "limit": limit, "detailLimit": detail_limit,
            "assessedCount": len(cards), "recipes": bounded_cards,
            "omittedCount": len(cards) - len(bounded_cards),
            "rootCounts": dict(sorted(root_counts.items())),
            "gateCounts": dict(sorted(gate_counts.items())),
            "publicationProvenanceCounts": publication_provenance_counts,
            "fingerprint": fingerprint,
            "runtimeProof": {"providerCalls": 0, "writes": 0},
        },
    })


@tool("legacy_nutrition")
def nutrition_backfill_personal_recipes(limit: int | None = None, batch_size: int = 5,
                                         execute: bool = False, publish: bool = False,
                                         cookbook_name: str = "Mis recetas",
                                         resume: bool = True) -> dict[str, Any]:
    """Run a resumable personal cookbook backfill only when execute=true."""
    if not execute:
        return {"ok": True, "code": "nutrition_backfill_preview",
                 "message": "Set execute=true to enumerate and calculate recipes.",
                 "retryable": False, "details": {"limit": limit, "batchSize": batch_size,
                                                   "publish": publish, "cookbook": cookbook_name,
                                                   "resume": resume}}
    if not config.SABOR_NUTRITION_ENABLED:
        return _nutrition_error("nutrition_disabled", "Nutrition is disabled.")
    try:
        summaries = _nutrition_personal_recipes(cookbook_name)
        recipes = (
            _nutrition_fetch_recipe(str(item["slug"]))
            for item in summaries if item.get("slug")
        )
        service = _nutrition_service()
        operation = service.publish if publish else service.calculate
        return service.run_backfill(recipes, calculate=operation, batch_size=batch_size,
                                    limit=limit, resume=resume, publish=False,
                                    checkpoint_key=f"cookbook:{mealie._catalog_key(cookbook_name)}")
    except Exception as exc:
        return _nutrition_error("nutrition_calculation_failed", "Backfill failed.",
                                details={"reason": type(exc).__name__})


@tool("legacy_nutrition")
def nutrition_unresolved_report() -> dict[str, Any]:
    """Return compact unresolved food mappings from the sidecar."""
    if not config.SABOR_NUTRITION_ENABLED:
        return _nutrition_error("nutrition_disabled", "Nutrition is disabled.")
    items = _nutrition_service().db.unresolved_report()
    return {"ok": True, "code": "nutrition_unresolved_report",
            "message": "Unresolved food report returned.", "retryable": False,
            "details": {"count": len(items), "items": items}}

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
