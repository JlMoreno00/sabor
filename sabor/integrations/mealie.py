"""Mealie catalog reconciliation and import normalization."""

import json
import unicodedata
from typing import Any
import requests
import sabor.config as config
import sabor.tools.nutrition as nutrition
from sabor.core.toolsets import tool

# MEALIE — Helpers
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def _mealie_headers() -> dict[str, str]:
    if not config.MEALIE_API_TOKEN:
        return {}
    return {
        "Authorization": f"Bearer {config.MEALIE_API_TOKEN}",
        "Content-Type": "application/json",
    }


def _check_mealie() -> dict[str, Any] | None:
    if not config.MEALIE_API_TOKEN:
        return {"ok": False, "error": "missing_MEALIE_API_TOKEN"}
    return None


def _catalog_key(value: Any) -> str:
    """Canonical comparison key without changing the stored display value."""
    text = " ".join(str(value or "").split()).casefold()
    return "".join(
        char for char in unicodedata.normalize("NFKD", text)
        if not unicodedata.combining(char)
    )


def _load_catalog_manifest(kind: str) -> dict[str, Any]:
    if kind not in {"foods", "units"}:
        raise ValueError("catalog kind must be foods or units")
    data = json.loads((config.CATALOG_DIR / f"spanish_{kind}.json").read_text(encoding="utf-8"))
    if data.get("schema_version") != 1 or not isinstance(data.get("items"), list):
        raise ValueError(f"unsupported or invalid {kind} catalog manifest")
    return data


def _get_all_mealie_items(endpoint: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    page = 1
    while True:
        response = requests.get(
            f"{config.MEALIE_BASE_URL}{endpoint}", headers=_mealie_headers(),
            params={"page": page, "perPage": 500}, timeout=30,
        )
        if not (200 <= response.status_code < 300):
            raise RuntimeError(f"catalog read {endpoint} failed: HTTP {response.status_code}")
        body = response.json()
        batch = body.get("items") if isinstance(body, dict) else None
        if not isinstance(batch, list):
            raise ValueError(f"catalog read {endpoint} returned invalid schema")
        items.extend(item for item in batch if isinstance(item, dict))
        if not body.get("next") and (not body.get("total_pages") or page >= body["total_pages"]):
            break
        page += 1
    return items


def _reconcile_catalog_kind(kind: str, existing: list[dict[str, Any]], apply: bool) -> dict[str, Any]:
    manifest = _load_catalog_manifest(kind)
    index: dict[str, list[dict[str, Any]]] = {}
    for item in existing:
        values = [item.get("name"), item.get("pluralName"), item.get("abbreviation")]
        values.extend(
            alias.get("name") if isinstance(alias, dict) else alias
            for alias in (item.get("aliases") or [])
        )
        for value in values:
            key = _catalog_key(value)
            if key and item not in index.setdefault(key, []):
                index[key].append(item)
    result: dict[str, Any] = {
        "catalog_version": manifest["catalog_version"], "remote_total": len(existing),
        "manifest_total": len(manifest["items"]), "existing": 0, "missing": [],
        "created": 0, "conflicts": [
            {"key": key, "reason": "ambiguous_existing_key", "ids": [item.get("id") for item in matches]}
            for key, matches in index.items() if len(matches) > 1
        ], "errors": [],
    }
    endpoint = f"/api/{kind}"
    for wanted in manifest["items"]:
        canonical_matches = index.get(_catalog_key(wanted["name"]), [])
        if len(canonical_matches) == 1:
            result["existing"] += 1
            continue
        if len(canonical_matches) > 1:
            result["conflicts"].append({"name": wanted["name"], "reason": "duplicate_canonical", "ids": [x.get("id") for x in canonical_matches]})
            continue
        alias_matches: dict[str, dict[str, Any]] = {}
        for alias in wanted.get("aliases") or []:
            for match in index.get(_catalog_key(alias), []):
                alias_matches[str(match.get("id") or id(match))] = match
        if len(alias_matches) == 1:
            result["existing"] += 1
            continue
        if len(alias_matches) > 1:
            result["conflicts"].append({"name": wanted["name"], "reason": "ambiguous_alias", "ids": [x.get("id") for x in alias_matches.values()]})
            continue
        result["missing"].append(wanted["name"])
        # The installation already has the large built-in food seed. Foods are
        # audited only; explicit apply is intentionally limited to curated units.
        if not apply or kind == "foods":
            continue
        payload = {
            "name": wanted["name"],
            "pluralName": wanted.get("pluralName"),
            "description": wanted.get("description", ""),
            "abbreviation": wanted.get("abbreviation", ""),
            "pluralAbbreviation": wanted.get("pluralAbbreviation"),
            "useAbbreviation": bool(wanted.get("useAbbreviation", False)),
            "standardQuantity": wanted.get("standardQuantity"),
            "standardUnit": wanted.get("standardUnit"),
            "aliases": [
                {"name": alias}
                for alias in (wanted.get("aliases") or [])
                if _catalog_key(alias) not in {
                    _catalog_key(wanted.get("name")),
                    _catalog_key(wanted.get("pluralName")),
                    _catalog_key(wanted.get("abbreviation")),
                    _catalog_key(wanted.get("pluralAbbreviation")),
                }
            ],
            "extras": {
                "managedBy": "sabor",
                "catalog": "es-ES",
                "version": manifest["catalog_version"],
            },
        }
        created = requests.post(
            f"{config.MEALIE_BASE_URL}{endpoint}", headers=_mealie_headers(), json=payload, timeout=30
        )
        if 200 <= created.status_code < 300:
            result["created"] += 1
        else:
            result["errors"].append({"name": wanted["name"], "status": created.status_code, "body": created.text[:300]})
    return result


def _reconcile_spanish_catalog(apply: bool = False) -> dict[str, Any]:
    """Create missing curated entries; never update, merge, delete, or invoke seeders."""
    if err := _check_mealie():
        return err
    try:
        units = _get_all_mealie_items("/api/units")
        foods = _get_all_mealie_items("/api/foods")
        return {"ok": True, "apply": apply,
                "units": _reconcile_catalog_kind("units", units, apply),
                "foods": _reconcile_catalog_kind("foods", foods, apply)}
    except Exception as exc:
        return {"ok": False, "apply": apply, "error": f"catalog_reconciliation_failed: {exc}"}


@tool("legacy_catalog")
def spanish_catalog_status() -> dict[str, Any]:
    """Preview the curated Spanish catalog: missing entries and conflicts, without writes."""
    return _reconcile_spanish_catalog(apply=False)


@tool("legacy_catalog")
def apply_spanish_catalog() -> dict[str, Any]:
    """Create only missing Spanish catalog entries; never merge or modify existing entries."""
    return _reconcile_spanish_catalog(apply=True)


def _response_slug(response: Any) -> str | None:
    try:
        value = response.json()
    except Exception:
        return None
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(value, dict):
        slug = value.get("slug")
        return str(slug).strip() if slug else None
    return None


def _ingredient_source_text(item: dict[str, Any]) -> str:
    for field in ("originalText", "note", "display", "title"):
        if isinstance(item.get(field), str) and item[field].strip():
            return item[field].strip()
    return ""


def _sanitize_ingredient_references(ingredient: dict[str, Any]) -> dict[str, Any]:
    """Remove structured Mealie references that cannot be resolved by id."""
    sanitized = dict(ingredient)
    for field in ("food", "unit"):
        reference = sanitized.get(field)
        if isinstance(reference, dict) and not reference.get("id"):
            sanitized.pop(field)
    return sanitized


def _normalization_result(import_result: dict[str, Any], slug: str | None, enabled: bool = True) -> dict[str, Any]:
    import_result["slug"] = slug
    if enabled and config.SABOR_NORMALIZE_IMPORTS and slug:
        normalized = _normalize_imported_recipe(slug)
        import_result["normalization"] = normalized
        if not normalized.get("ok"):
            import_result["normalization_warning"] = normalized.get("warning") or normalized.get("error") or "normalization_failed"
    return nutrition._nutrition_after_import(import_result, slug) if enabled else import_result


def _normalize_imported_recipe(slug: str) -> dict[str, Any]:
    """Conservatively parse all ingredients and patch only confident food matches."""
    try:
        recipe_response = requests.get(
            f"{config.MEALIE_BASE_URL}/api/recipes/{slug}", headers=_mealie_headers(), timeout=30
        )
        if not (200 <= recipe_response.status_code < 300):
            return {"ok": False, "warning": f"normalization_recipe_read_http_{recipe_response.status_code}"}
        recipe = recipe_response.json()
        originals = recipe.get("recipeIngredient") if isinstance(recipe, dict) else None
        if not isinstance(originals, list) or not originals:
            return {"ok": True, "slug": slug, "status": "skipped_no_ingredients"}
        if not all(isinstance(item, dict) for item in originals):
            return {"ok": False, "warning": "normalization_invalid_recipe_ingredients"}
        texts = [_ingredient_source_text(item) for item in originals]
        if not all(texts):
            return {"ok": False, "warning": "normalization_missing_source_text"}
        parsed_response = requests.post(
            f"{config.MEALIE_BASE_URL}/api/parser/ingredients", headers=_mealie_headers(),
            json={"parser": config.SABOR_INGREDIENT_PARSER, "ingredients": texts}, timeout=60,
        )
        if not (200 <= parsed_response.status_code < 300):
            return {"ok": False, "warning": f"normalization_parser_http_{parsed_response.status_code}"}
        parsed = parsed_response.json()
        if isinstance(parsed, dict):
            parsed = parsed.get("items") or parsed.get("ingredients")
        if not isinstance(parsed, list) or len(parsed) != len(originals):
            return {"ok": False, "warning": "normalization_parser_cardinality_mismatch"}
        merged: list[dict[str, Any]] = []
        accepted = 0
        traceability_updates = 0
        for original, candidate in zip(originals, parsed):
            if not isinstance(candidate, dict):
                return {"ok": False, "warning": "normalization_parser_invalid_schema"}
            ingredient = candidate.get("ingredient", candidate)
            if not isinstance(ingredient, dict):
                return {"ok": False, "warning": "normalization_parser_invalid_schema"}
            food = ingredient.get("food")
            confidence_data = candidate.get("confidence", {})
            if isinstance(confidence_data, dict):
                confidence = confidence_data.get("food", 0)
            else:
                confidence = confidence_data
            if not confidence:
                confidence = ingredient.get("confidence", (food or {}).get("confidence", 0))
            try:
                confidence = float(confidence)
            except (TypeError, ValueError):
                confidence = 0.0
            if not isinstance(food, dict) or not food.get("id") or confidence < config.SABOR_NORMALIZE_CONFIDENCE:
                retained = dict(original)
                if not retained.get("originalText"):
                    retained["originalText"] = _ingredient_source_text(original)
                    traceability_updates += 1
                merged.append(retained)
                continue
            normalized = dict(ingredient)
            source_text = _ingredient_source_text(original)
            normalized["originalText"] = original.get("originalText") or source_text
            if original.get("referenceId"):
                normalized["referenceId"] = original["referenceId"]
            for field in ("id", "title", "section"):
                if original.get(field) is not None:
                    normalized[field] = original[field]
            original_note = original.get("note")
            if (
                isinstance(original_note, str)
                and original_note.strip()
                and _catalog_key(original_note) != _catalog_key(source_text)
            ):
                normalized["note"] = original_note
            if not normalized.get("display") and original.get("display"):
                normalized["display"] = original["display"]
            merged.append(normalized)
            accepted += 1
        if not accepted and not traceability_updates:
            return {"ok": True, "slug": slug, "status": "retained_originals", "accepted": 0}
        merged = [_sanitize_ingredient_references(item) for item in merged]
        patch_response = requests.patch(
            f"{config.MEALIE_BASE_URL}/api/recipes/{slug}",
            headers=_mealie_headers(),
            json={"recipeIngredient": merged},
            timeout=30,
        )
        if not (200 <= patch_response.status_code < 300):
            return {"ok": False, "warning": f"normalization_patch_http_{patch_response.status_code}"}
        status = "normalized" if accepted else "retained_originals"
        return {
            "ok": True,
            "slug": slug,
            "status": status,
            "accepted": accepted,
            "retained": len(merged) - accepted,
            "traceabilityUpdated": traceability_updates,
        }
    except Exception as exc:
        return {"ok": False, "warning": f"normalization_failed: {exc}"}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
