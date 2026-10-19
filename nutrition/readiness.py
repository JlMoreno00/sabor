"""Pure recipe nutrition data-readiness assessment."""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .consumption import classify_consumption
from .conversions import (
    CONSUMPTION_UNIT_TOKENS,
    MASS_FACTORS,
    MEASURE_TOKEN_RE,
    MEASURE_UNITS,
    PACKAGE_UNITS,
    PIECE_FOOD_ALIASES,
    PIECE_UNITS,
    VOLUME_FACTORS,
    canonical_unit,
    is_title_only_section_heading,
    normalize_unit,
)
from .publisher import (
    canonical_supported_nutrition,
    has_meaningful_supported_nutrition,
)
from .protection import nutrition_publication_protected


READINESS_CLASSIFIER_VERSION = "1"
PUBLICATION_PROVENANCE_STATUSES = (
    "absent", "owned", "mismatch", "unknown", "protected",
)
ROOT_PRECEDENCE = (
    "derived_non_food_row",
    "parser_loss",
    "missing_quantity",
    "food_selection_required",
    "conversion_evidence_missing",
    "profile_provider_evidence",
    "missing_servings",
    "manual_publication_conflict",
    "ready",
)
BLOCKING_ROOTS = ROOT_PRECEDENCE[1:-1]

_NUMBER = r"(?:\d+(?:[.,]\d+)?|\d+\s*/\s*\d+)"
_SOURCE_QUANTITY_RE = re.compile(
    rf"^\s*{_NUMBER}(?:\s*[-–]\s*{_NUMBER})?(?=\s|$)", re.IGNORECASE,
)
_KNOWN_UNITS = set(MASS_FACTORS) | set(VOLUME_FACTORS) | set(PIECE_UNITS) | set(PACKAGE_UNITS)
_DIRECT_CONSUMPTION_REQUIREMENTS = {
    "frying_consumption_missing", "marinade_retention_missing",
    "conflicting_retention_evidence", "invalid_retained_fraction",
    "invalid_retained_grams", "retained_grams_exceeds_supplied_mass",
    "invalid_absorbed_grams", "absorbed_grams_exceeds_supplied_mass",
}


@dataclass(frozen=True)
class ReadinessPolicy:
    """Quality thresholds for already-recorded evidence."""

    min_confidence: float = .8
    supported_schema_version: int = 9


@dataclass(frozen=True)
class FoodEvidence:
    """Read-only classification of sidecar evidence for one Mealie food."""

    food_id: str
    resolution_status: str = "ready"
    profile_status: str = "ready"
    conversion_status: str = "ready"


@dataclass(frozen=True)
class ReadinessEvidence:
    """Optional read-only sidecar snapshot consumed by the pure classifier."""

    status: str = "indeterminate"
    foods: tuple[FoodEvidence, ...] = ()
    schema_version: int | None = None
    reason_code: str | None = None
    publication_status: str = "unknown"


class _SnapshotUnavailable(OSError):
    def __init__(self, reason_code: str):
        super().__init__()
        self.reason_code = reason_code


@dataclass(frozen=True)
class FoodEvidenceRequest:
    """Exact sidecar facts needed for one food in a recipe."""

    food_id: str
    conversion_candidates: tuple[tuple[str, str], ...] = ()


def _finite_nonnegative(value: Any, *, positive: bool = False) -> bool:
    return (
        isinstance(value, (int, float)) and not isinstance(value, bool)
        and math.isfinite(float(value))
        and (float(value) > 0 if positive else float(value) >= 0)
    )


def _expired(value: Any, now: datetime) -> bool:
    if not value:
        return False
    try:
        parsed = datetime.fromisoformat(str(value))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed <= now
    except (TypeError, ValueError):
        return True


def _resolution_status(row: sqlite3.Row | None, policy: ReadinessPolicy,
                       now: datetime) -> str:
    if row is None:
        return "missing"
    status = row["resolution_status"]
    if status == "ambiguous":
        return "ambiguous"
    if status not in {"resolved", "manual"}:
        return "missing"
    if not _finite_nonnegative(row["confidence"]) or float(row["confidence"]) > 1:
        return "invalid"
    if _expired(row["stale_after"], now):
        return "stale"
    if float(row["confidence"]) < policy.min_confidence:
        return "low_confidence"
    return "ready"


def _profile_status(row: sqlite3.Row | None, resolution: sqlite3.Row | None,
                    policy: ReadinessPolicy, now: datetime) -> str:
    if row is None:
        return "missing"
    numeric = (
        row["basis_grams"], row["confidence"], row["calories_kcal"],
        *(row[name] for name in ("protein_g", "carbohydrate_g", "fat_g")
          if row[name] is not None),
    )
    if (not _finite_nonnegative(row["basis_grams"], positive=True)
            or not _finite_nonnegative(row["confidence"])
            or float(row["confidence"]) > 1
            or row["calories_kcal"] is None
            or not any(row[name] is not None for name in ("protein_g", "carbohydrate_g", "fat_g"))
            or not all(_finite_nonnegative(value) for value in numeric[2:])):
        return "invalid"
    if _expired(row["stale_after"], now):
        return "stale"
    if float(row["confidence"]) < policy.min_confidence:
        return "low_confidence"
    manual = bool(row["manual_override"]) and row["provider"] == "manual"
    if not manual and resolution is not None and (
        row["provider"] != resolution["provider"]
        or row["provider_food_id"] != resolution["provider_food_id"]
    ):
        return "binding_mismatch"
    return "ready"


def _conversion_status(rows: list[sqlite3.Row], request: FoodEvidenceRequest,
                       policy: ReadinessPolicy) -> str:
    if not request.conversion_candidates:
        return "ready"
    matches = [row for row in rows if (
        row["conversion_type"], row["unit_key"]
    ) in request.conversion_candidates]
    if not matches:
        return "missing"
    statuses = []
    for row in matches:
        if (not _finite_nonnegative(row["grams_per_unit"], positive=True)
                or not _finite_nonnegative(row["confidence"])
                or float(row["confidence"]) > 1):
            statuses.append("invalid")
        elif float(row["confidence"]) < policy.min_confidence:
            statuses.append("low_confidence")
        else:
            statuses.append("ready")
    return next(status for status in ("ready", "invalid", "low_confidence") if status in statuses)


def _snapshot_bytes(path: Path) -> bytes:
    """Read a stable main/WAL image without opening SQLite's locking files."""

    wal_path = Path(f"{path}-wal")
    shm_path = Path(f"{path}-shm")
    journal_path = Path(f"{path}-journal")

    def normalized(image: bytes, expected_page_size: int | None = None) -> bytes:
        if len(image) < 100 or image[:16] != b"SQLite format 3\0":
            raise ValueError("invalid SQLite header")
        encoded_page_size = int.from_bytes(image[16:18], "big")
        page_size = 65536 if encoded_page_size == 1 else encoded_page_size
        if (page_size < 512 or page_size > 65536
                or page_size & (page_size - 1)
                or expected_page_size is not None and page_size != expected_page_size
                or len(image) < page_size or len(image) % page_size):
            raise ValueError("invalid SQLite page layout")
        if image[18] not in (1, 2) or image[19] not in (1, 2):
            raise ValueError("invalid SQLite journal version")
        result = bytearray(image)
        result[18:20] = b"\x01\x01"
        return bytes(result)

    def signature() -> tuple[tuple[str, int | None, int | None, int | None, int | None], ...]:
        result = []
        for source in (path, wal_path, shm_path, journal_path):
            try:
                metadata = source.stat()
            except FileNotFoundError:
                result.append((source.name, None, None, None, None))
            else:
                result.append((
                    source.name, metadata.st_ino, metadata.st_size,
                    metadata.st_mtime_ns, metadata.st_ctime_ns,
                ))
        return tuple(result)

    for _ in range(3):
        before = signature()
        if before[3][2] is not None:
            raise _SnapshotUnavailable("rollback_journal_active")
        if sum(item[2] or 0 for item in before[:2]) > 256 * 1024 * 1024:
            raise OSError("sidecar snapshot is too large")
        main = path.read_bytes()
        wal = wal_path.read_bytes() if before[1][2] is not None else b""
        after = signature()
        if after[3][2] is not None:
            raise _SnapshotUnavailable("rollback_journal_active")
        if before == after:
            break
    else:
        raise OSError("sidecar changed while being read")
    main = normalized(main)
    main_page_size = 65536 if main[16:18] == b"\0\x01" else int.from_bytes(main[16:18], "big")
    if not wal:
        return main
    if len(wal) < 32 or int.from_bytes(wal[:4], "big") not in {0x377F0682, 0x377F0683}:
        raise ValueError("invalid WAL header")
    magic = int.from_bytes(wal[:4], "big")
    checksum_order = "little" if magic == 0x377F0682 else "big"

    def checksum(data: bytes, seed: tuple[int, int]) -> tuple[int, int]:
        first, second = seed
        if len(data) % 8:
            raise ValueError("invalid WAL checksum input")
        for offset in range(0, len(data), 8):
            word_a = int.from_bytes(data[offset:offset + 4], checksum_order)
            word_b = int.from_bytes(data[offset + 4:offset + 8], checksum_order)
            first = (first + word_a + second) & 0xFFFFFFFF
            second = (second + word_b + first) & 0xFFFFFFFF
        return first, second

    rolling_checksum = checksum(wal[:24], (0, 0))
    stored_header_checksum = (
        int.from_bytes(wal[24:28], "big"), int.from_bytes(wal[28:32], "big"),
    )
    if rolling_checksum != stored_header_checksum:
        raise ValueError("invalid WAL header checksum")
    page_size = int.from_bytes(wal[8:12], "big")
    if page_size == 1:
        page_size = 65536
    if (page_size < 512 or page_size > 65536 or page_size & (page_size - 1)
            or page_size != main_page_size):
        raise ValueError("invalid WAL page size")
    frame_size = 24 + page_size
    if (len(wal) - 32) % frame_size:
        raise ValueError("invalid WAL frame length")
    frame_count = (len(wal) - 32) // frame_size
    salt = wal[16:24]
    frames: list[tuple[int, int, bytes]] = []
    last_commit = -1
    for index in range(frame_count):
        offset = 32 + index * frame_size
        header = wal[offset:offset + 24]
        page_number = int.from_bytes(header[:4], "big")
        database_pages = int.from_bytes(header[4:8], "big")
        if page_number < 1 or header[8:16] != salt:
            raise ValueError("invalid WAL frame")
        page = wal[offset + 24:offset + frame_size]
        rolling_checksum = checksum(header[:8] + page, rolling_checksum)
        stored_frame_checksum = (
            int.from_bytes(header[16:20], "big"), int.from_bytes(header[20:24], "big"),
        )
        if rolling_checksum != stored_frame_checksum:
            raise ValueError("invalid WAL frame checksum")
        frames.append((page_number, database_pages, page))
        if database_pages:
            last_commit = len(frames) - 1
    if last_commit < 0:
        return main
    image = bytearray(main)
    committed_pages = frames[last_commit][1]
    required = committed_pages * page_size
    if required > 256 * 1024 * 1024:
        raise OSError("sidecar snapshot is too large")
    if len(image) < required:
        image.extend(b"\0" * (required - len(image)))
    for page_number, _, page in frames[:last_commit + 1]:
        offset = (page_number - 1) * page_size
        image[offset:offset + page_size] = page
    del image[required:]
    return normalized(bytes(image), page_size)


def read_readiness_evidence(
    path: str | Path,
    requests: tuple[FoodEvidenceRequest, ...],
    *,
    policy: ReadinessPolicy | None = None,
    now: datetime | None = None,
    recipe_id: str | None = None,
    current_nutrition: Any = None,
) -> ReadinessEvidence:
    """Inspect an existing sidecar directly without initialization or writes."""

    policy = policy or ReadinessPolicy()
    now = now or datetime.now(timezone.utc)
    path = Path(path)
    if not path.is_file():
        return ReadinessEvidence()
    normalized_requests = tuple(
        request for request in requests if str(request.food_id).strip()
    )
    try:
        snapshot = _snapshot_bytes(path)
    except _SnapshotUnavailable as error:
        return ReadinessEvidence(reason_code=error.reason_code)
    except (MemoryError, OSError, OverflowError):
        return ReadinessEvidence(reason_code="snapshot_unavailable")
    except (TypeError, ValueError):
        return ReadinessEvidence(reason_code="snapshot_invalid")
    try:
        connection = sqlite3.connect(":memory:")
    except (MemoryError, sqlite3.Error):
        return ReadinessEvidence(reason_code="sqlite_unavailable")
    try:
        with connection:
            try:
                connection.deserialize(snapshot)
            except (MemoryError, OverflowError, sqlite3.Error):
                return ReadinessEvidence(reason_code="snapshot_deserialize_failed")
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA query_only=ON")
            schema_row = connection.execute(
                "SELECT MAX(version) AS version FROM schema_version"
            ).fetchone()
            schema_version = schema_row["version"] if schema_row else None
            if schema_version != policy.supported_schema_version:
                return ReadinessEvidence(schema_version=schema_version)
            tables = {row[0] for row in connection.execute(
                "SELECT name FROM sqlite_schema WHERE type='table' AND name IN "
                "('food_resolution','nutrient_profile','food_conversion',"
                "'recipe_calculation','nutrition_publish_history')"
            )}
            required = {"food_resolution", "nutrient_profile", "food_conversion"}
            if not required.issubset(tables):
                return ReadinessEvidence(schema_version=schema_version)
            publication_status = "unknown"
            normalized_recipe_id = str(recipe_id or "").strip()
            current_values = canonical_supported_nutrition(current_nutrition)
            if (normalized_recipe_id and current_values
                    and {"recipe_calculation", "nutrition_publish_history"}.issubset(tables)):
                calculation_columns = {
                    row[1] for row in connection.execute("PRAGMA table_info(recipe_calculation)")
                }
                history_columns = {
                    row[1] for row in connection.execute(
                        "PRAGMA table_info(nutrition_publish_history)"
                    )
                }
                if ({"id", "mealie_recipe_id"}.issubset(calculation_columns)
                        and {"id", "recipe_calculation_id", "published_payload_json",
                             "published_at"}.issubset(history_columns)):
                    publication_row = connection.execute(
                        "SELECT history.published_payload_json "
                        "FROM nutrition_publish_history AS history "
                        "JOIN recipe_calculation AS rc ON rc.id=history.recipe_calculation_id "
                        "WHERE rc.mealie_recipe_id=? "
                        "ORDER BY history.published_at DESC,history.id DESC LIMIT 1",
                        (normalized_recipe_id,),
                    ).fetchone()
                    if publication_row is not None:
                        serialized = publication_row["published_payload_json"]
                        if (isinstance(serialized, str) and len(serialized) <= 16384
                                and len(serialized.encode("utf-8")) <= 16384):
                            try:
                                payload = json.loads(serialized)
                            except (TypeError, ValueError):
                                payload = None
                            if isinstance(payload, dict):
                                published_values = canonical_supported_nutrition(
                                    payload, full_payload=True,
                                )
                                if published_values:
                                    publication_status = (
                                        "owned" if current_values == published_values else "mismatch"
                                    )
            food_ids = tuple(dict.fromkeys(str(item.food_id).strip() for item in normalized_requests))
            if not food_ids:
                return ReadinessEvidence(
                    status="available", schema_version=schema_version,
                    publication_status=publication_status,
                )
            placeholders = ",".join("?" for _ in food_ids)
            resolution_rows = connection.execute(
                "SELECT mealie_food_id,provider,provider_food_id,resolution_status,confidence,"
                f"manual_override,stale_after FROM food_resolution WHERE mealie_food_id IN ({placeholders})",
                food_ids,
            ).fetchall()
            profile_rows = connection.execute(
                "SELECT mealie_food_id,basis_grams,calories_kcal,protein_g,carbohydrate_g,fat_g,"
                "provider,provider_food_id,confidence,manual_override,stale_after "
                f"FROM nutrient_profile WHERE mealie_food_id IN ({placeholders})",
                food_ids,
            ).fetchall()
            conversion_rows = connection.execute(
                "SELECT mealie_food_id,conversion_type,unit_key,grams_per_unit,confidence "
                f"FROM food_conversion WHERE mealie_food_id IN ({placeholders})",
                food_ids,
            ).fetchall()
    except (MemoryError, OverflowError, sqlite3.Error):
        return ReadinessEvidence(reason_code="evidence_query_failed")

    resolutions = {row["mealie_food_id"]: row for row in resolution_rows}
    profiles = {row["mealie_food_id"]: row for row in profile_rows}
    conversions: dict[str, list[sqlite3.Row]] = {}
    for row in conversion_rows:
        conversions.setdefault(row["mealie_food_id"], []).append(row)
    request_by_food: dict[str, FoodEvidenceRequest] = {}
    for request in normalized_requests:
        food_id = str(request.food_id).strip()
        previous = request_by_food.get(food_id)
        candidates = tuple(dict.fromkeys(
            (previous.conversion_candidates if previous else ()) + request.conversion_candidates
        ))
        request_by_food[food_id] = FoodEvidenceRequest(food_id, candidates)
    foods = tuple(
        FoodEvidence(
            food_id=food_id,
            resolution_status=_resolution_status(resolutions.get(food_id), policy, now),
            profile_status=_profile_status(
                profiles.get(food_id), resolutions.get(food_id), policy, now,
            ),
            conversion_status=_conversion_status(
                conversions.get(food_id, []), request_by_food[food_id], policy,
            ),
        )
        for food_id in food_ids
    )
    return ReadinessEvidence(
        status="available", foods=foods, schema_version=schema_version,
        publication_status=publication_status,
    )


def _source_text(item: dict[str, Any]) -> str:
    for field in ("originalText", "display", "note", "title"):
        value = item.get(field)
        if isinstance(value, str) and value.strip():
            return " ".join(value.split())
    return ""


def _positive_quantity(value: Any) -> bool:
    if isinstance(value, dict):
        values = (value.get("min"), value.get("max"))
        return all(
            isinstance(part, (int, float)) and not isinstance(part, bool)
            and math.isfinite(float(part)) and float(part) > 0
            for part in values
        ) and float(values[0]) <= float(values[1])
    return (
        isinstance(value, (int, float)) and not isinstance(value, bool)
        and math.isfinite(float(value)) and float(value) > 0
    )


def _lexical_parts(text: str) -> tuple[bool, str, bool]:
    quantity = _SOURCE_QUANTITY_RE.match(normalize_unit(text))
    if not quantity:
        return False, "", False
    remainder = normalize_unit(text)[quantity.end():].strip()
    words = remainder.split()
    unit = ""
    for size in (2, 1):
        candidate = " ".join(words[:size])
        canonical, _ = canonical_unit(candidate)
        if canonical in _KNOWN_UNITS:
            unit = canonical
            words = words[size:]
            break
    return True, unit, bool(" ".join(words).strip(" ,;:"))


def _is_derived(item: dict[str, Any]) -> bool:
    if is_title_only_section_heading(item):
        return True
    if item.get("optional") is True and item.get("included") is not True:
        return True
    unit, _ = canonical_unit(item.get("unit") or "")
    quantity = item.get("quantity")
    known_grams = None
    if _positive_quantity(quantity) and unit in MASS_FACTORS:
        amount = ((float(quantity["min"]) + float(quantity["max"])) / 2
                  if isinstance(quantity, dict) else float(quantity))
        known_grams = amount * MASS_FACTORS[unit]
    return classify_consumption(
        item, _source_text(item), CONSUMPTION_UNIT_TOKENS, known_grams,
    ).status == "excluded"


def _conversion_requirement(item: dict[str, Any], unit: str, text: str) -> str | None:
    food_name = normalize_unit((item.get("food") or {}).get("name"))
    frying = "freir" in normalize_unit(text) or "frying" in normalize_unit(text)
    oil_or_fat = any(term in food_name for term in ("oil", "aceite", "fat", "grasa", "lard", "manteca"))
    raw_quantity = item["quantity"]
    quantity = ((float(raw_quantity["min"]) + float(raw_quantity["max"])) / 2
                if isinstance(raw_quantity, dict) else float(raw_quantity))
    known_grams = quantity * MASS_FACTORS[unit] if unit in MASS_FACTORS else None
    consumption = classify_consumption(
        item, text, CONSUMPTION_UNIT_TOKENS, known_grams,
    )
    if consumption.status == "invalid_retention":
        return consumption.reason
    frying_metadata = item.get("fryingConsumption")
    has_retention = (
        consumption.retained_fraction is not None
        or consumption.retained_grams is not None
    )
    if (((frying and oil_or_fat) or frying_metadata is not None)
            and consumption.absorbed_grams is None and not has_retention):
        return "frying_consumption_missing"
    if consumption.status == "retention_required":
        return "marinade_retention_missing"
    if consumption.status == "invalid_retention":
        return consumption.reason
    if consumption.retained_grams is not None:
        return None
    if unit in MASS_FACTORS:
        return None
    if not unit and item.get("food") and not MEASURE_TOKEN_RE.search(normalize_unit(text)):
        return "piece_weight"
    if unit in VOLUME_FACTORS:
        return "culinary_measure_or_density"
    if unit in PIECE_UNITS:
        return "piece_weight"
    if unit in PACKAGE_UNITS:
        return "package_weight"
    return "culinary_measure"


def recipe_evidence_requests(recipe: dict[str, Any]) -> tuple[FoodEvidenceRequest, ...]:
    """Derive exact sidecar lookups using the engine's canonical unit semantics."""

    grouped: dict[str, list[tuple[str, str]]] = {}
    for item in recipe.get("recipeIngredient") or []:
        if not isinstance(item, dict) or _is_derived(item):
            continue
        food = item.get("food") if isinstance(item.get("food"), dict) else {}
        food_id = str(food.get("id") or "").strip()
        if not food_id:
            continue
        candidates = grouped.setdefault(food_id, [])
        unit, _ = canonical_unit(item.get("unit") or "")
        text = _source_text(item)
        requirement = _conversion_requirement(item, unit, text)
        if requirement is None or requirement in {
            "frying_consumption_missing", "marinade_retention_missing",
        }:
            continue
        if requirement == "culinary_measure_or_density":
            candidates.extend((("culinary_measure", unit), ("density", "ml")))
        elif requirement == "piece_weight":
            food_name = normalize_unit(food.get("name"))
            if PIECE_FOOD_ALIASES.get(food_name):
                continue
            candidates.append(("piece_weight", "piece"))
        elif requirement == "package_weight":
            candidates.append(("package_weight", PACKAGE_UNITS[unit]))
        else:
            candidates.append(("culinary_measure", MEASURE_UNITS.get(unit, unit)))
    return tuple(
        FoodEvidenceRequest(food_id, tuple(dict.fromkeys(candidates)))
        for food_id, candidates in sorted(grouped.items())
    )


def _issue(root: str, position: int | None, item: dict[str, Any] | None,
           reason: str, include_suggestions: bool) -> dict[str, Any]:
    result: dict[str, Any] = {
        "root": root,
        "blocking": root not in {"derived_non_food_row", "ready"},
        "reason": reason,
    }
    if position is not None:
        result["position"] = position
    if item is not None:
        result["ingredientId"] = item.get("id")
        result["originalText"] = _source_text(item)
    if include_suggestions and result["blocking"]:
        result["suggestion"] = {
            "parser_loss": "Restore the source quantity, unit, and food structure without reparsing here.",
            "missing_quantity": "Record the source quantity explicitly; do not estimate it.",
            "food_selection_required": "Select the exact consumed Mealie food or explicit alternative.",
            "conversion_evidence_missing": "Record the exact food-specific conversion or consumption evidence.",
            "profile_provider_evidence": "Review the existing food resolution and nutrient profile evidence.",
            "missing_servings": "Record a positive serving count before per-serving publication.",
            "manual_publication_conflict": "Review existing nutrition and manual protection before publication.",
        }[root]
    return result


def assess_recipe_readiness(
    recipe: dict[str, Any],
    evidence: ReadinessEvidence | None = None,
    policy: ReadinessPolicy | None = None,
    *,
    include_suggestions: bool = True,
) -> dict[str, Any]:
    """Derive readiness without parsing, calculation, persistence, or I/O."""

    policy = policy or ReadinessPolicy()
    evidence = evidence or ReadinessEvidence()
    food_evidence = {item.food_id: item for item in evidence.foods}
    issues: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    consumed_count = ready_count = 0

    for position, item in enumerate(recipe.get("recipeIngredient") or []):
        if not isinstance(item, dict):
            item = {}
        text = _source_text(item)
        if _is_derived(item):
            issues.append(_issue("derived_non_food_row", position, item, "not_consumed", False))
            continue
        consumed_count += 1
        food = item.get("food") if isinstance(item.get("food"), dict) else {}
        food_id = str(food.get("id") or "").strip()
        unit, _ = canonical_unit(item.get("unit") or "")
        source_quantity, source_unit, _ = _lexical_parts(text)
        quantity_valid = _positive_quantity(item.get("quantity"))
        alternatives = item.get("alternatives") or item.get("alternativeFoods") or []

        parser_loss = (
            (not quantity_valid and source_quantity)
            or (not unit and bool(source_unit))
        )
        if parser_loss:
            issues.append(_issue("parser_loss", position, item, "structured_source_mismatch",
                                 include_suggestions))
            continue
        if not quantity_valid:
            issues.append(_issue("missing_quantity", position, item, "positive_quantity_missing",
                                 include_suggestions))
            continue
        if not food_id or (len(alternatives) > 1 and not item.get("selectedAlternative")):
            issues.append(_issue("food_selection_required", position, item,
                                 "exact_food_not_selected", include_suggestions))
            continue
        requirement = _conversion_requirement(item, unit, text)
        food_state = food_evidence.get(food_id)
        if requirement and (
            requirement in _DIRECT_CONSUMPTION_REQUIREMENTS
            or food_state is None
            or food_state.conversion_status not in {"ready", "low_confidence"}
        ):
            reason = (requirement if requirement in _DIRECT_CONSUMPTION_REQUIREMENTS
                      else (food_state.conversion_status if food_state else evidence.status))
            issues.append(_issue("conversion_evidence_missing", position, item, reason,
                                 include_suggestions))
            continue
        if requirement and food_state and food_state.conversion_status == "low_confidence":
            warnings.append({
                "reason": "low_confidence_conversion", "position": position,
                "ingredientId": item.get("id"),
            })
        if evidence.status != "available" or food_state is None:
            issues.append(_issue("profile_provider_evidence", position, item, evidence.status,
                                 include_suggestions))
            continue
        if food_state.resolution_status == "low_confidence":
            warnings.append({
                "reason": "low_confidence_resolution", "position": position,
                "ingredientId": item.get("id"),
            })
        profile_problem = next((status for status in (
            food_state.resolution_status
            if food_state.resolution_status != "low_confidence" else "ready",
            food_state.profile_status,
        ) if status != "ready"), None)
        if profile_problem:
            issues.append(_issue("profile_provider_evidence", position, item, profile_problem,
                                 include_suggestions))
            continue
        ready_count += 1

    if not consumed_count:
        issues.append(_issue("missing_quantity", None, None,
                             "no_consumed_ingredients", include_suggestions))

    try:
        servings = float(recipe.get("recipeServings"))
    except (TypeError, ValueError):
        servings = 0
    servings_ready = math.isfinite(servings) and servings > 0
    if not servings_ready:
        issues.append(_issue("missing_servings", None, None, "positive_servings_missing",
                             include_suggestions))

    nutrition = recipe.get("nutrition")
    protected = nutrition_publication_protected(recipe)
    meaningful_nutrition = has_meaningful_supported_nutrition(nutrition)
    publication_provenance = (
        "protected" if protected else
        "absent" if not meaningful_nutrition else
        evidence.publication_status if evidence.publication_status in {"owned", "mismatch"}
        else "unknown"
    )
    manual_conflict = protected or (
        meaningful_nutrition and evidence.publication_status != "owned"
    )
    if manual_conflict:
        issues.append(_issue("manual_publication_conflict", None, None,
                             ("manual_protection" if protected
                              else "existing_nutrition_review"), include_suggestions))

    counts = {
        root: sum(issue["root"] == root for issue in issues)
        for root in ROOT_PRECEDENCE
        if any(issue["root"] == root for issue in issues)
    }
    if ready_count:
        counts["ready"] = ready_count
    blocking = tuple(root for root in BLOCKING_ROOTS if counts.get(root))
    structural_blocked = any(
        issue["root"] in {
            "parser_loss", "missing_quantity", "food_selection_required", "missing_servings",
        } or (
            issue["root"] == "conversion_evidence_missing"
            and issue["reason"] in _DIRECT_CONSUMPTION_REQUIREMENTS
        )
        for issue in issues
    )
    evidence_blocked = any(
        issue["root"] == "profile_provider_evidence" or (
            issue["root"] == "conversion_evidence_missing"
            and issue["reason"] not in _DIRECT_CONSUMPTION_REQUIREMENTS
        )
        for issue in issues
    )
    total_ready = not structural_blocked and not evidence_blocked
    gates = {
        "sourceStructure": not structural_blocked,
        "evidence": not evidence_blocked,
        "totalCalculation": total_ready,
        "perServingCalculation": total_ready and servings_ready,
        "publication": (
            total_ready and servings_ready and not manual_conflict and not warnings
        ),
        "backfill": total_ready and servings_ready,
    }
    score = round(100 * ready_count / consumed_count) if consumed_count else 0
    root = blocking[0] if blocking else "ready"
    fingerprint_input = {
        "classifierVersion": READINESS_CLASSIFIER_VERSION,
        "recipeId": recipe.get("id"),
        "slug": recipe.get("slug"),
        "evidenceStatus": evidence.status,
        "schemaVersion": evidence.schema_version,
        "evidenceReasonCode": evidence.reason_code,
        "issues": [{
            key: issue.get(key) for key in
            ("root", "position", "ingredientId", "originalText", "reason")
        } for issue in issues],
        "gates": gates,
        "rootCounts": counts,
        "warnings": warnings,
        "publicationProvenance": publication_provenance,
    }
    fingerprint = hashlib.sha256(json.dumps(
        fingerprint_input, sort_keys=True, ensure_ascii=False,
        separators=(",", ":"), default=str,
    ).encode("utf-8")).hexdigest()
    return {
        "classifierVersion": READINESS_CLASSIFIER_VERSION,
        "root": root,
        "ready": not blocking,
        "gates": gates,
        "issues": issues,
        "warnings": warnings,
        "rootCounts": counts,
        "blockingRoots": list(blocking),
        "score": score,
        "publicationProvenance": publication_provenance,
        "evidence": {
            "status": evidence.status,
            "schemaVersion": evidence.schema_version,
            "reasonCode": evidence.reason_code,
            "publicationStatus": evidence.publication_status,
        },
        "fingerprint": fingerprint,
    }
