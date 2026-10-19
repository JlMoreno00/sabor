"""SQLite migrations and persistence for nutrition evidence."""

from __future__ import annotations

import json
import math
import re
import shutil
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import NUTRIENT_FIELDS, SPOONACULAR_PROFILE_CONFIDENCE, is_finite_number


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


PROVIDER_FOOD_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
SENSITIVE_DIAGNOSTIC_PATTERN = re.compile(
    r"(?i)(api[_-]?key|authorization|bearer|password|secret|token|credential)"
)


def normalize_mealie_food_id(value: Any) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError("Mealie food ID must be non-empty after stripping whitespace")
    return normalized


def normalize_provider_food_id(value: Any) -> str:
    normalized = str(value or "").strip()
    if not normalized or not PROVIDER_FOOD_ID_PATTERN.fullmatch(normalized):
        raise ValueError("Provider food ID must be a non-empty normalized identifier")
    return normalized


def _bounded_diagnostic(value: Any) -> str | None:
    if not isinstance(value, (str, int, float, bool)) or value is None:
        return None
    text = " ".join(str(value).split())[:128]
    if not text or SENSITIVE_DIAGNOSTIC_PATTERN.search(text):
        return None
    return text


def _safe_failure_response(response: dict[str, Any] | None) -> dict[str, Any] | None:
    if response is None:
        return None
    safe: dict[str, Any] = {}
    for key in ("ok", "retryable", "retry_after", "http_status"):
        value = response.get(key)
        if (isinstance(value, (bool, int, float))
                and (not isinstance(value, float) or math.isfinite(value))):
            safe[key] = value
    for key in ("code", "message", "failure_class", "cooldown_until"):
        if (value := _bounded_diagnostic(response.get(key))) is not None:
            safe[key] = value
    candidates = response.get("candidates")
    if isinstance(candidates, list):
        safe_candidates: list[Any] = []
        for candidate in candidates[:5]:
            if isinstance(candidate, dict):
                item = {
                    key: value for key in ("id", "name")
                    if (value := _bounded_diagnostic(candidate.get(key))) is not None
                }
                if item:
                    safe_candidates.append(item)
            elif (value := _bounded_diagnostic(candidate)) is not None:
                safe_candidates.append(value)
        if safe_candidates:
            safe["candidates"] = safe_candidates
    return safe


class ManualMappingTargetError(ValueError):
    code = "nutrition_mapping_target_unknown"


MIGRATIONS = (
    (1, """
CREATE TABLE food_resolution (
    mealie_food_id TEXT PRIMARY KEY, mealie_food_name TEXT NOT NULL,
    canonical_term TEXT, canonical_language TEXT NOT NULL DEFAULT 'en',
    provider TEXT, provider_food_id TEXT,
    resolution_status TEXT NOT NULL CHECK (resolution_status IN
        ('resolved','manual','ambiguous','not_found','blocked')),
    confidence REAL NOT NULL DEFAULT 0 CHECK (confidence BETWEEN 0 AND 1),
    resolution_method TEXT NOT NULL,
    manual_override INTEGER NOT NULL DEFAULT 0 CHECK (manual_override IN (0,1)),
    source_version TEXT, payload_hash TEXT, resolved_at TEXT, stale_after TEXT,
    updated_at TEXT NOT NULL
);
CREATE TABLE nutrient_profile (
    mealie_food_id TEXT PRIMARY KEY, basis_grams REAL NOT NULL DEFAULT 100 CHECK (basis_grams > 0),
    calories_kcal REAL, protein_g REAL, carbohydrate_g REAL, net_carbohydrate_g REAL,
    fat_g REAL, saturated_fat_g REAL, unsaturated_fat_g REAL, trans_fat_g REAL,
    fiber_g REAL, sugar_g REAL, sodium_mg REAL, cholesterol_mg REAL,
    provider TEXT NOT NULL, provider_food_id TEXT,
    confidence REAL NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    manual_override INTEGER NOT NULL DEFAULT 0 CHECK (manual_override IN (0,1)),
    source_version TEXT, payload_hash TEXT NOT NULL, retrieved_at TEXT NOT NULL,
    stale_after TEXT, FOREIGN KEY (mealie_food_id) REFERENCES food_resolution(mealie_food_id)
);
CREATE TABLE food_conversion (
    id INTEGER PRIMARY KEY AUTOINCREMENT, mealie_food_id TEXT,
    conversion_type TEXT NOT NULL CHECK (conversion_type IN
        ('density','piece_weight','package_weight','culinary_measure')),
    unit_key TEXT NOT NULL, grams_per_unit REAL NOT NULL CHECK (grams_per_unit > 0),
    confidence REAL NOT NULL CHECK (confidence BETWEEN 0 AND 1), provider TEXT NOT NULL,
    manual_override INTEGER NOT NULL DEFAULT 0 CHECK (manual_override IN (0,1)),
    source_note TEXT, updated_at TEXT NOT NULL,
    UNIQUE(mealie_food_id, conversion_type, unit_key),
    FOREIGN KEY (mealie_food_id) REFERENCES food_resolution(mealie_food_id)
);
CREATE TABLE recipe_calculation (
    id TEXT PRIMARY KEY, mealie_recipe_id TEXT NOT NULL, mealie_recipe_slug TEXT NOT NULL,
    input_hash TEXT NOT NULL, engine_version TEXT NOT NULL, profile_version TEXT NOT NULL,
    servings REAL, ingredient_count INTEGER NOT NULL, resolved_count INTEGER NOT NULL,
    computable_count INTEGER NOT NULL, coverage_ratio REAL NOT NULL CHECK (coverage_ratio BETWEEN 0 AND 1),
    confidence REAL NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    status TEXT NOT NULL CHECK (status IN ('complete','estimated','insufficient','failed','stale')),
    total_calories_kcal REAL, total_protein_g REAL, total_carbohydrate_g REAL,
    total_fat_g REAL, total_fiber_g REAL, total_sugar_g REAL, total_sodium_mg REAL,
    total_saturated_fat_g REAL, per_serving_json TEXT, unresolved_json TEXT NOT NULL DEFAULT '[]',
    assumptions_json TEXT NOT NULL DEFAULT '[]', published_to_mealie INTEGER NOT NULL DEFAULT 0,
    calculated_at TEXT NOT NULL,
    UNIQUE(mealie_recipe_id, input_hash, engine_version, profile_version)
);
CREATE TABLE ingredient_calculation (
    id INTEGER PRIMARY KEY AUTOINCREMENT, recipe_calculation_id TEXT NOT NULL,
    mealie_ingredient_id TEXT, position INTEGER NOT NULL, original_text TEXT NOT NULL,
    mealie_food_id TEXT, quantity REAL, unit_key TEXT, consumed_grams REAL,
    conversion_method TEXT, conversion_confidence REAL NOT NULL DEFAULT 0,
    nutrition_confidence REAL NOT NULL DEFAULT 0, contribution_json TEXT,
    status TEXT NOT NULL CHECK (status IN
        ('computed','estimated','optional','non_consumed','unresolved','excluded')),
    reason TEXT, FOREIGN KEY (recipe_calculation_id) REFERENCES recipe_calculation(id) ON DELETE CASCADE
);
CREATE INDEX idx_recipe_calculation_recipe ON recipe_calculation(mealie_recipe_id, calculated_at DESC);
CREATE INDEX idx_ingredient_calculation_food ON ingredient_calculation(mealie_food_id);
"""),
    (2, """
CREATE TABLE provider_cache (
    cache_key TEXT PRIMARY KEY, provider TEXT NOT NULL, status TEXT NOT NULL,
    response_json TEXT, payload_hash TEXT, retry_after INTEGER,
    expires_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE nutrition_publish_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT, recipe_calculation_id TEXT NOT NULL,
    previous_payload_json TEXT NOT NULL, published_payload_json TEXT NOT NULL,
    published_at TEXT NOT NULL,
    FOREIGN KEY (recipe_calculation_id) REFERENCES recipe_calculation(id)
);
"""),
    (3, """
DELETE FROM ingredient_calculation
WHERE id NOT IN (
    SELECT MAX(id) FROM ingredient_calculation
    GROUP BY recipe_calculation_id, position
);
CREATE UNIQUE INDEX idx_ingredient_calculation_position
    ON ingredient_calculation(recipe_calculation_id, position);
CREATE TABLE backfill_checkpoint (
    checkpoint_key TEXT NOT NULL, mealie_recipe_id TEXT NOT NULL,
    input_hash TEXT NOT NULL, completed_at TEXT NOT NULL,
    PRIMARY KEY (checkpoint_key, mealie_recipe_id)
);
"""),
    (4, """
ALTER TABLE ingredient_calculation ADD COLUMN source_note TEXT;
"""),
    (5, """
ALTER TABLE recipe_calculation ADD COLUMN mass_coverage_ratio REAL NOT NULL DEFAULT 0
    CHECK (mass_coverage_ratio BETWEEN 0 AND 1);
ALTER TABLE recipe_calculation ADD COLUMN ingredient_coverage_ratio REAL NOT NULL DEFAULT 0
    CHECK (ingredient_coverage_ratio BETWEEN 0 AND 1);
ALTER TABLE ingredient_calculation ADD COLUMN quantity_min REAL;
ALTER TABLE ingredient_calculation ADD COLUMN quantity_max REAL;
ALTER TABLE food_resolution ADD COLUMN source_note TEXT;
UPDATE recipe_calculation SET mass_coverage_ratio=coverage_ratio,
    ingredient_coverage_ratio=coverage_ratio;
UPDATE food_resolution
SET stale_after=strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now', '+7 days')
WHERE resolution_status='ambiguous' AND stale_after IS NULL AND manual_override=0;
"""),
    (6, """
ALTER TABLE provider_cache ADD COLUMN http_status INTEGER;
ALTER TABLE provider_cache ADD COLUMN failure_class TEXT;
ALTER TABLE provider_cache ADD COLUMN error_code TEXT;
ALTER TABLE provider_cache ADD COLUMN retryable INTEGER CHECK (retryable IN (0,1));
"""),
    (7, """
ALTER TABLE ingredient_calculation ADD COLUMN manual_mapping_applied INTEGER NOT NULL DEFAULT 0
    CHECK (manual_mapping_applied IN (0,1));
ALTER TABLE ingredient_calculation ADD COLUMN mapping_updated_at TEXT;
"""),
    (8, f"""
UPDATE nutrient_profile
SET confidence={SPOONACULAR_PROFILE_CONFIDENCE}
WHERE provider='spoonacular'
  AND manual_override=0
  AND basis_grams=100
  AND provider_food_id IS NOT NULL
  AND provider_food_id=TRIM(provider_food_id)
  AND provider_food_id GLOB '[A-Za-z0-9]*'
  AND provider_food_id NOT GLOB '*[^A-Za-z0-9._:-]*';
"""),
    (9, """
ALTER TABLE nutrient_profile ADD COLUMN source_note TEXT;
"""),
)


def _migration_statements(sql: str) -> list[str]:
    statements: list[str] = []
    pending = ""
    for line in sql.splitlines():
        pending += f"{line}\n"
        if sqlite3.complete_statement(pending):
            statements.append(pending.strip())
            pending = ""
    if pending.strip():
        statements.append(pending.strip())
    return statements


def _apply_migration_statement(connection: sqlite3.Connection, statement: str) -> None:
    alter = re.match(
        r"ALTER\s+TABLE\s+(\w+)\s+ADD\s+COLUMN\s+(\w+)", statement, re.IGNORECASE,
    )
    if alter:
        table, column = alter.groups()
        columns = {row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')}
        if column not in columns:
            connection.execute(statement)
        return
    created = re.match(
        r"CREATE\s+(UNIQUE\s+)?(TABLE|INDEX)\s+(\w+)", statement, re.IGNORECASE,
    )
    if created:
        unique, object_type, name = created.groups()
        schema_type = object_type.casefold()
        exists = connection.execute(
            "SELECT 1 FROM sqlite_schema WHERE type=? AND name=?", (schema_type, name),
        ).fetchone()
        if not exists:
            connection.execute(statement)
        return
    connection.execute(statement)


def _canonicalize_legacy_resolution(connection: sqlite3.Connection, food_id: str) -> None:
    rows = connection.execute(
        "SELECT mealie_food_id FROM food_resolution WHERE TRIM(mealie_food_id)=?",
        (food_id,),
    ).fetchall()
    legacy_ids = [row[0] for row in rows if row[0] != food_id]
    if not legacy_ids or any(row[0] == food_id for row in rows):
        return
    if len(legacy_ids) != 1:
        raise ValueError(f"Multiple legacy whitespace variants exist for Mealie food ID {food_id!r}")
    legacy_id = legacy_ids[0]
    connection.execute("PRAGMA defer_foreign_keys = ON")
    connection.execute(
        "UPDATE food_resolution SET mealie_food_id=? WHERE mealie_food_id=?",
        (food_id, legacy_id),
    )
    for table in ("nutrient_profile", "food_conversion", "ingredient_calculation"):
        connection.execute(
            f'UPDATE "{table}" SET mealie_food_id=? WHERE mealie_food_id=?',
            (food_id, legacy_id),
        )


def init_nutrition_db(path: str | Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)")
        applied = {row[0] for row in connection.execute("SELECT version FROM schema_version")}
        for version, sql in MIGRATIONS:
            if version not in applied:
                with connection:
                    for statement in _migration_statements(sql):
                        _apply_migration_statement(connection, statement)
                    connection.execute(
                        "INSERT INTO schema_version(version, applied_at) VALUES (?, ?)",
                        (version, utc_now()),
                    )


class NutritionDB:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        init_nutrition_db(self.path)

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _one(self, sql: str, params: tuple[Any, ...]) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(sql, params).fetchone()
        return dict(row) if row else None

    def get_resolution(self, food_id: str) -> dict[str, Any] | None:
        food_id = normalize_mealie_food_id(food_id)
        return self._one(
            "SELECT * FROM food_resolution WHERE mealie_food_id=? OR TRIM(mealie_food_id)=? "
            "ORDER BY mealie_food_id=? DESC LIMIT 1", (food_id, food_id, food_id),
        )

    def is_mapping_target_valid(self, food_id: str) -> bool:
        try:
            normalized = normalize_mealie_food_id(food_id)
        except ValueError:
            return False
        with self.connect() as connection:
            row = connection.execute("""
                SELECT 1 FROM food_resolution WHERE TRIM(mealie_food_id)=?
                UNION ALL
                SELECT 1 FROM ingredient_calculation WHERE TRIM(mealie_food_id)=?
                LIMIT 1
            """, (normalized, normalized)).fetchone()
        return row is not None

    @staticmethod
    def mapping_target_valid_at(path: str | Path, food_id: str) -> bool:
        try:
            normalized = normalize_mealie_food_id(food_id)
        except ValueError:
            return False
        path = Path(path)
        if not path.is_file():
            return False
        try:
            with sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True) as connection:
                row = connection.execute("""
                    SELECT 1 FROM food_resolution WHERE TRIM(mealie_food_id)=?
                    UNION ALL
                    SELECT 1 FROM ingredient_calculation WHERE TRIM(mealie_food_id)=?
                    LIMIT 1
                """, (normalized, normalized)).fetchone()
        except sqlite3.Error:
            return False
        return row is not None

    @staticmethod
    def profile_preview_at(
            path: str | Path, food_id: str,
            ) -> tuple[dict[str, str] | None, dict[str, Any] | None]:
        try:
            normalized = normalize_mealie_food_id(food_id)
        except ValueError:
            return None, None
        path = Path(path)
        if not path.is_file():
            return None, None

        def source_signature() -> tuple[tuple[str, int, int], ...]:
            signature = []
            for source in (path, Path(f"{path}-wal")):
                try:
                    stat = source.stat()
                except FileNotFoundError:
                    continue
                signature.append((source.name, stat.st_size, stat.st_mtime_ns))
            return tuple(signature)

        try:
            with tempfile.TemporaryDirectory(prefix="sabor-nutrition-preview-") as tempdir:
                snapshot = Path(tempdir) / path.name
                for _ in range(3):
                    before = source_signature()
                    shutil.copyfile(path, snapshot)
                    source_wal = Path(f"{path}-wal")
                    snapshot_wal = Path(f"{snapshot}-wal")
                    if source_wal.is_file():
                        shutil.copyfile(source_wal, snapshot_wal)
                    elif snapshot_wal.exists():
                        snapshot_wal.unlink()
                    if before == source_signature():
                        break
                else:
                    return None, None

                with sqlite3.connect(snapshot) as connection:
                    row = connection.execute("""
                        SELECT mealie_food_id,mealie_food_name FROM food_resolution
                        WHERE TRIM(mealie_food_id)=?
                        UNION ALL
                        SELECT ?,original_text FROM ingredient_calculation
                        WHERE TRIM(mealie_food_id)=?
                        LIMIT 1
                    """, (normalized, normalized, normalized)).fetchone()
                    profile = connection.execute("""
                        SELECT provider,manual_override,payload_hash,source_version
                        FROM nutrient_profile WHERE TRIM(mealie_food_id)=? LIMIT 1
                    """, (normalized,)).fetchone()
                    profile_columns = tuple(column[0] for column in connection.execute(
                        "SELECT provider,manual_override,payload_hash,source_version "
                        "FROM nutrient_profile LIMIT 0"
                    ).description)
        except (OSError, sqlite3.Error):
            return None, None
        target = ({"id": normalized, "name": row[1], "source": "sidecar"}
                  if row and row[1] else None)
        return target, (dict(zip(profile_columns, profile)) if profile else None)

    def get_profile_target(self, food_id: str) -> dict[str, str] | None:
        food_id = normalize_mealie_food_id(food_id)
        with self.connect() as connection:
            row = connection.execute("""
                SELECT mealie_food_id,mealie_food_name FROM food_resolution
                WHERE TRIM(mealie_food_id)=?
                UNION ALL
                SELECT ?,original_text FROM ingredient_calculation
                WHERE TRIM(mealie_food_id)=?
                LIMIT 1
            """, (food_id, food_id, food_id)).fetchone()
        return ({"id": food_id, "name": row[1], "source": "sidecar"}
                if row and row[1] else None)

    def upsert_resolution(self, mealie_food_id: str, mealie_food_name: str,
                          canonical_term: str | None, status: str, confidence: float,
                          method: str, manual_override: bool = False, **values: Any) -> bool:
        mealie_food_id = normalize_mealie_food_id(mealie_food_id)
        if manual_override and (not math.isfinite(confidence) or not 0 <= confidence <= 1):
            raise ValueError("Manual mapping confidence must be finite and between 0 and 1")
        target_validated = values.pop("target_validated", False) is True
        provider_food_id = values.get("provider_food_id")
        if manual_override and provider_food_id is not None:
            provider_food_id = normalize_provider_food_id(provider_food_id)
        now = utc_now()
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if manual_override and not target_validated:
                evidence = connection.execute("""
                    SELECT 1 FROM food_resolution WHERE TRIM(mealie_food_id)=?
                    UNION ALL
                    SELECT 1 FROM ingredient_calculation WHERE TRIM(mealie_food_id)=?
                    LIMIT 1
                """, (mealie_food_id, mealie_food_id)).fetchone()
                if evidence is None:
                    raise ManualMappingTargetError(
                        f"No persisted food or ingredient evidence exists for {mealie_food_id!r}"
                    )
            _canonicalize_legacy_resolution(connection, mealie_food_id)
            existing = connection.execute("SELECT manual_override FROM food_resolution WHERE mealie_food_id=?", (mealie_food_id,)).fetchone()
            if existing and existing[0] and not manual_override:
                return False
            connection.execute("""
                INSERT INTO food_resolution
                (mealie_food_id,mealie_food_name,canonical_term,provider,provider_food_id,
                 resolution_status,confidence,resolution_method,manual_override,source_version,
                 payload_hash,resolved_at,stale_after,updated_at,source_note)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(mealie_food_id) DO UPDATE SET
                 mealie_food_name=excluded.mealie_food_name, canonical_term=excluded.canonical_term,
                 provider=excluded.provider, provider_food_id=excluded.provider_food_id,
                 resolution_status=excluded.resolution_status, confidence=excluded.confidence,
                 resolution_method=excluded.resolution_method, manual_override=excluded.manual_override,
                 source_version=excluded.source_version, payload_hash=excluded.payload_hash,
                 resolved_at=excluded.resolved_at, stale_after=excluded.stale_after,
                 updated_at=excluded.updated_at, source_note=excluded.source_note
            """, (mealie_food_id, mealie_food_name, canonical_term, values.get("provider"),
                  provider_food_id, status, confidence, method, int(manual_override),
                  values.get("source_version"), values.get("payload_hash"), now,
                  values.get("stale_after"), now, values.get("source_note")))
        return True

    def get_profile(self, food_id: str) -> dict[str, Any] | None:
        food_id = normalize_mealie_food_id(food_id)
        return self._one(
            "SELECT * FROM nutrient_profile WHERE mealie_food_id=? OR TRIM(mealie_food_id)=? "
            "ORDER BY mealie_food_id=? DESC LIMIT 1", (food_id, food_id, food_id),
        )

    def delete_profile(self, food_id: str) -> None:
        food_id = normalize_mealie_food_id(food_id)
        with self.connect() as connection:
            connection.execute(
                "DELETE FROM nutrient_profile WHERE mealie_food_id=? OR TRIM(mealie_food_id)=?",
                (food_id, food_id),
            )

    def upsert_profile(self, food_id: str, nutrients: dict[str, Any], provider: str,
                       confidence: float, payload_hash: str, manual_override: bool = False,
                       force: bool = False, **values: Any) -> bool:
        food_id = normalize_mealie_food_id(food_id)
        fields = ("calories_kcal", "protein_g", "carbohydrate_g", "net_carbohydrate_g",
                  "fat_g", "saturated_fat_g", "unsaturated_fat_g", "trans_fat_g",
                  "fiber_g", "sugar_g", "sodium_mg", "cholesterol_mg")
        columns = ",".join(fields)
        placeholders = ",".join("?" for _ in fields)
        updates = ",".join(f"{field}=excluded.{field}" for field in fields)
        with self.connect() as connection:
            result = connection.execute(f"""
                INSERT INTO nutrient_profile
                (mealie_food_id,basis_grams,{columns},provider,provider_food_id,confidence,
                 manual_override,source_version,payload_hash,retrieved_at,stale_after,source_note)
                VALUES (?,?,{placeholders},?,?,?,?,?,?,?,?,?)
                ON CONFLICT(mealie_food_id) DO UPDATE SET basis_grams=excluded.basis_grams,
                 {updates}, provider=excluded.provider, provider_food_id=excluded.provider_food_id,
                 confidence=excluded.confidence, manual_override=excluded.manual_override,
                 source_version=excluded.source_version, payload_hash=excluded.payload_hash,
                 retrieved_at=excluded.retrieved_at, stale_after=excluded.stale_after,
                 source_note=excluded.source_note
                WHERE excluded.manual_override=1 OR nutrient_profile.manual_override=0
            """, (food_id, values.get("basis_grams", 100), *(nutrients.get(f) for f in fields),
                  provider, values.get("provider_food_id"), confidence, int(manual_override),
                  values.get("source_version"), payload_hash, utc_now(), values.get("stale_after"),
                  values.get("source_note")))
        return result.rowcount == 1

    def set_manual_profile(self, food_id: str, nutrients: dict[str, Any], basis_grams: float,
                           confidence: float, source_version: str, source_note: str,
                           payload_hash: str, *, target_validated: bool = False,
                           target_name: str | None = None
                           ) -> dict[str, Any] | None:
        food_id = normalize_mealie_food_id(food_id)
        numeric_values = [basis_grams, confidence, *(
            value for value in nutrients.values() if value is not None
        )]
        if (set(nutrients) - set(NUTRIENT_FIELDS)
                or not all(is_finite_number(value) and value >= 0 for value in numeric_values)
                or basis_grams != 100 or not 0 <= confidence <= 1
                or nutrients.get("calories_kcal") is None
                or not any(nutrients.get(field) is not None
                           for field in ("protein_g", "carbohydrate_g", "fat_g"))
                or not isinstance(source_version, str) or not 0 < len(source_version) <= 256
                or not isinstance(source_note, str) or not 0 < len(source_note) <= 1000
                or not isinstance(payload_hash, str)
                or not re.fullmatch(r"[0-9a-f]{64}", payload_hash)):
            raise ValueError("Invalid manual nutrient profile")
        fields = NUTRIENT_FIELDS
        columns = ",".join(fields)
        placeholders = ",".join("?" for _ in fields)
        updates = ",".join(f"{field}=excluded.{field}" for field in fields)
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            evidence = connection.execute("""
                SELECT 1 FROM food_resolution WHERE TRIM(mealie_food_id)=?
                UNION ALL
                SELECT 1 FROM ingredient_calculation WHERE TRIM(mealie_food_id)=?
                LIMIT 1
            """, (food_id, food_id)).fetchone()
            if evidence is None and not target_validated:
                raise ManualMappingTargetError(
                    f"No persisted food or ingredient evidence exists for {food_id!r}"
                )
            if evidence is None:
                if not target_name:
                    raise ManualMappingTargetError(
                        "Validated Mealie targets require an authoritative name"
                    )
                connection.execute("""
                    INSERT INTO food_resolution
                    (mealie_food_id,mealie_food_name,resolution_status,confidence,
                     resolution_method,manual_override,updated_at)
                    VALUES (?,?,'blocked',0,'manual_profile_target',0,?)
                """, (food_id, target_name, utc_now()))
            _canonicalize_legacy_resolution(connection, food_id)
            prior = connection.execute(
                "SELECT payload_hash,source_version FROM nutrient_profile "
                "WHERE mealie_food_id=?", (food_id,),
            ).fetchone()
            now = utc_now()
            connection.execute(f"""
                INSERT INTO nutrient_profile
                (mealie_food_id,basis_grams,{columns},provider,provider_food_id,confidence,
                 manual_override,source_version,payload_hash,retrieved_at,stale_after,source_note)
                VALUES (?,?,{placeholders},'manual',NULL,?,1,?,?,?,NULL,?)
                ON CONFLICT(mealie_food_id) DO UPDATE SET basis_grams=excluded.basis_grams,
                 {updates},provider='manual',provider_food_id=NULL,
                 confidence=excluded.confidence,manual_override=1,
                 source_version=excluded.source_version,payload_hash=excluded.payload_hash,
                 retrieved_at=excluded.retrieved_at,stale_after=NULL,
                 source_note=excluded.source_note
            """, (food_id, basis_grams, *(nutrients.get(field) for field in fields),
                  confidence, source_version, payload_hash, now, source_note))
        return dict(prior) if prior else None

    def get_conversion(self, food_id: str | None, conversion_type: str, unit_key: str) -> dict[str, Any] | None:
        food_id = normalize_mealie_food_id(food_id) if food_id is not None else None
        return self._one("SELECT * FROM food_conversion WHERE mealie_food_id IS ? AND conversion_type=? AND unit_key=?", (food_id, conversion_type, unit_key))

    def list_conversions(self, food_id: str) -> list[dict[str, Any]]:
        food_id = normalize_mealie_food_id(food_id)
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM food_conversion WHERE mealie_food_id=? OR TRIM(mealie_food_id)=?",
                (food_id, food_id),
            ).fetchall()
        return [dict(row) for row in rows]

    def upsert_conversion(self, food_id: str | None, conversion_type: str, unit_key: str,
                          grams_per_unit: float, confidence: float, provider: str,
                          manual_override: bool = False, source_note: str | None = None) -> bool:
        food_id = normalize_mealie_food_id(food_id) if food_id is not None else None
        if manual_override and (not math.isfinite(grams_per_unit) or grams_per_unit <= 0
                                or not math.isfinite(confidence) or not 0 <= confidence <= 1):
            raise ValueError("Manual conversion values must be finite and in range")
        existing = self.get_conversion(food_id, conversion_type, unit_key)
        if existing and existing["manual_override"] and not manual_override:
            return False
        with self.connect() as connection:
            connection.execute("""
                INSERT INTO food_conversion
                (mealie_food_id,conversion_type,unit_key,grams_per_unit,confidence,provider,
                 manual_override,source_note,updated_at) VALUES (?,?,?,?,?,?,?,?,?)
                ON CONFLICT(mealie_food_id,conversion_type,unit_key) DO UPDATE SET
                 grams_per_unit=excluded.grams_per_unit, confidence=excluded.confidence,
                 provider=excluded.provider, manual_override=excluded.manual_override,
                 source_note=excluded.source_note, updated_at=excluded.updated_at
            """, (food_id, conversion_type, unit_key, grams_per_unit, confidence, provider,
                  int(manual_override), source_note, utc_now()))
        return True

    def get_cache(self, cache_key: str) -> dict[str, Any] | None:
        return self._one("SELECT * FROM provider_cache WHERE cache_key=?", (cache_key,))

    def put_cache(self, cache_key: str, provider: str, status: str, response: dict[str, Any] | None,
                  payload_hash: str | None, expires_at: str, retry_after: int | None = None,
                  *, http_status: int | None = None, failure_class: str | None = None,
                  error_code: str | None = None, retryable: bool | None = None) -> None:
        if status != "success":
            response = _safe_failure_response(response)
            retry_after = (
                min(300, max(0, retry_after))
                if isinstance(retry_after, int) and not isinstance(retry_after, bool) else None
            )
            http_status = (
                http_status if isinstance(http_status, int) and 100 <= http_status <= 599 else None
            )
            failure_class = _bounded_diagnostic(failure_class)
            error_code = _bounded_diagnostic(error_code)
        with self.connect() as connection:
            connection.execute("""
                INSERT INTO provider_cache
                (cache_key,provider,status,response_json,payload_hash,retry_after,expires_at,
                 updated_at,http_status,failure_class,error_code,retryable)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(cache_key) DO UPDATE SET provider=excluded.provider,status=excluded.status,
                response_json=excluded.response_json,payload_hash=excluded.payload_hash,
                retry_after=excluded.retry_after,expires_at=excluded.expires_at,
                updated_at=excluded.updated_at,http_status=excluded.http_status,
                failure_class=excluded.failure_class,error_code=excluded.error_code,
                retryable=excluded.retryable
            """, (cache_key, provider, status, json.dumps(response, sort_keys=True) if response is not None else None,
                  payload_hash, retry_after, expires_at, utc_now(), http_status, failure_class,
                  error_code, None if retryable is None else int(retryable)))

    def manual_mapping_count_for_calculation(self, calculation_id: str) -> int:
        with self.connect() as connection:
            row = connection.execute("""
                SELECT COUNT(DISTINCT ingredient.mealie_food_id)
                FROM ingredient_calculation AS ingredient
                WHERE ingredient.recipe_calculation_id=?
                  AND ingredient.manual_mapping_applied=1
            """, (calculation_id,)).fetchone()
        return int(row[0])

    def find_calculation(self, recipe_id: str, input_hash: str, engine_version: str,
                         profile_version: str) -> dict[str, Any] | None:
        row = self._one("""SELECT * FROM recipe_calculation WHERE mealie_recipe_id=? AND
            input_hash=? AND engine_version=? AND profile_version=? AND status != 'failed'""",
            (recipe_id, input_hash, engine_version, profile_version))
        if row:
            row["per_serving"] = json.loads(row.get("per_serving_json") or "{}")
            row["unresolved"] = json.loads(row["unresolved_json"])
            row["assumptions"] = json.loads(row["assumptions_json"])
        return row

    def save_calculation(self, value: dict[str, Any]) -> str:
        totals = value.get("totals", {})
        ingredients = value.get("ingredients", [])
        resolved = sum(1 for item in ingredients if item.get("nutrition_confidence", 0) > 0)
        computable = sum(1 for item in ingredients if item.get("status") in {"computed", "estimated"})
        with self.connect() as connection:
            existing = connection.execute("""SELECT id FROM recipe_calculation WHERE
                mealie_recipe_id=? AND input_hash=? AND engine_version=? AND profile_version=?""",
                (value["mealie_recipe_id"], value["input_hash"], value.get("engine_version", "1"),
                 value["profile_version"])).fetchone()
            calculation_id = existing[0] if existing else value["id"]
            connection.execute("""
                INSERT INTO recipe_calculation
                (id,mealie_recipe_id,mealie_recipe_slug,input_hash,engine_version,profile_version,
                 servings,ingredient_count,resolved_count,computable_count,coverage_ratio,
                 mass_coverage_ratio,ingredient_coverage_ratio,confidence,status,
                 total_calories_kcal,total_protein_g,total_carbohydrate_g,total_fat_g,total_fiber_g,
                 total_sugar_g,total_sodium_mg,total_saturated_fat_g,per_serving_json,unresolved_json,
                 assumptions_json,published_to_mealie,calculated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET
                 mealie_recipe_id=excluded.mealie_recipe_id,
                 mealie_recipe_slug=excluded.mealie_recipe_slug,
                 input_hash=excluded.input_hash, engine_version=excluded.engine_version,
                 profile_version=excluded.profile_version, servings=excluded.servings,
                 ingredient_count=excluded.ingredient_count, resolved_count=excluded.resolved_count,
                 computable_count=excluded.computable_count, coverage_ratio=excluded.coverage_ratio,
                 mass_coverage_ratio=excluded.mass_coverage_ratio,
                 ingredient_coverage_ratio=excluded.ingredient_coverage_ratio,
                 confidence=excluded.confidence, status=excluded.status,
                 total_calories_kcal=excluded.total_calories_kcal,
                 total_protein_g=excluded.total_protein_g,
                 total_carbohydrate_g=excluded.total_carbohydrate_g,
                 total_fat_g=excluded.total_fat_g, total_fiber_g=excluded.total_fiber_g,
                 total_sugar_g=excluded.total_sugar_g, total_sodium_mg=excluded.total_sodium_mg,
                 total_saturated_fat_g=excluded.total_saturated_fat_g,
                 per_serving_json=excluded.per_serving_json,
                 unresolved_json=excluded.unresolved_json,
                 assumptions_json=excluded.assumptions_json,
                 published_to_mealie=excluded.published_to_mealie,
                 calculated_at=excluded.calculated_at
            """, (calculation_id, value["mealie_recipe_id"], value["mealie_recipe_slug"],
                  value["input_hash"], value.get("engine_version", "1"), value["profile_version"],
                  value.get("servings"), value.get("ingredient_count", len(ingredients)),
                  value.get("resolved_count", resolved), value.get("computable_count", computable),
                   value["coverage_ratio"], value.get("mass_coverage_ratio", value["coverage_ratio"]),
                   value.get("ingredient_coverage_ratio", value["coverage_ratio"]),
                   value["confidence"], value["status"],
                  totals.get("calories_kcal"), totals.get("protein_g"), totals.get("carbohydrate_g"),
                  totals.get("fat_g"), totals.get("fiber_g"), totals.get("sugar_g"),
                  totals.get("sodium_mg"), totals.get("saturated_fat_g"),
                  json.dumps(value.get("per_serving", {}), sort_keys=True),
                  json.dumps(value.get("unresolved", []), sort_keys=True),
                   json.dumps(value.get("assumptions", []), sort_keys=True),
                   int(value.get("published_to_mealie", False)), utc_now()))
            connection.execute(
                "DELETE FROM ingredient_calculation WHERE recipe_calculation_id=?",
                (calculation_id,),
            )
            for item in sorted(ingredients, key=lambda ingredient: ingredient["position"]):
                raw_food_id = item.get("mealie_food_id")
                food_id = normalize_mealie_food_id(raw_food_id) if raw_food_id is not None else None
                mapping = None
                if food_id:
                    mapping = connection.execute(
                        "SELECT manual_override,updated_at FROM food_resolution "
                        "WHERE mealie_food_id=? OR TRIM(mealie_food_id)=? "
                        "ORDER BY mealie_food_id=? DESC LIMIT 1",
                        (food_id, food_id, food_id),
                    ).fetchone()
                connection.execute("""
                    INSERT INTO ingredient_calculation
                    (recipe_calculation_id,mealie_ingredient_id,position,original_text,mealie_food_id,
                     quantity,quantity_min,quantity_max,unit_key,consumed_grams,conversion_method,conversion_confidence,
                     nutrition_confidence,contribution_json,status,reason,source_note,
                     manual_mapping_applied,mapping_updated_at)
                      VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """, (calculation_id, item.get("mealie_ingredient_id"), item["position"],
                       item.get("original_text", ""), food_id, item.get("quantity"),
                       item.get("quantity_min"), item.get("quantity_max"), item.get("unit_key"),
                       item.get("consumed_grams"), item.get("conversion_method"),
                       item.get("conversion_confidence", 0), item.get("nutrition_confidence", 0),
                       json.dumps(item.get("contribution", {}), sort_keys=True), item["status"],
                       item.get("reason"), item.get("source_note"),
                       int(bool(mapping and mapping[0])), mapping[1] if mapping else None))
        return calculation_id

    def get_backfill_checkpoint(self, checkpoint_key: str,
                                recipe_id: str) -> dict[str, Any] | None:
        return self._one(
            "SELECT * FROM backfill_checkpoint WHERE checkpoint_key=? AND mealie_recipe_id=?",
            (checkpoint_key, recipe_id),
        )

    def mark_backfill_checkpoint(self, checkpoint_key: str, recipe_id: str,
                                 input_hash: str) -> None:
        with self.connect() as connection:
            connection.execute("""
                INSERT INTO backfill_checkpoint
                (checkpoint_key,mealie_recipe_id,input_hash,completed_at) VALUES (?,?,?,?)
                ON CONFLICT(checkpoint_key,mealie_recipe_id) DO UPDATE SET
                 input_hash=excluded.input_hash, completed_at=excluded.completed_at
            """, (checkpoint_key, recipe_id, input_hash, utc_now()))

    def clear_backfill_checkpoint(self, checkpoint_key: str) -> None:
        with self.connect() as connection:
            connection.execute(
                "DELETE FROM backfill_checkpoint WHERE checkpoint_key=?", (checkpoint_key,)
            )

    def record_publish(self, calculation_id: str, previous_payload: dict[str, Any],
                       published_payload: dict[str, Any]) -> None:
        with self.connect() as connection:
            connection.execute("INSERT INTO nutrition_publish_history(recipe_calculation_id,previous_payload_json,published_payload_json,published_at) VALUES (?,?,?,?)",
                (calculation_id, json.dumps(previous_payload, sort_keys=True),
                 json.dumps(published_payload, sort_keys=True), utc_now()))
            connection.execute("UPDATE recipe_calculation SET published_to_mealie=1 WHERE id=?", (calculation_id,))

    def publish_history(self, calculation_id: str) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute("SELECT * FROM nutrition_publish_history WHERE recipe_calculation_id=? ORDER BY id", (calculation_id,)).fetchall()
        return [dict(row) for row in rows]

    def unresolved_report(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute("""SELECT mealie_food_id,mealie_food_name,resolution_status,
                confidence,updated_at FROM food_resolution WHERE resolution_status != 'resolved'
                AND resolution_status != 'manual' ORDER BY updated_at DESC""").fetchall()
        return [dict(row) for row in rows]
