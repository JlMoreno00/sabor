import json
import sqlite3
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from nutrition.calculator import calculate_recipe_nutrition
from nutrition.conversions import CURATED_PIECE_WEIGHTS, ConversionContext
from nutrition.db import (MIGRATIONS, ManualMappingTargetError, NutritionDB,
                          init_nutrition_db)
from nutrition.models import NutrientProfile, SPOONACULAR_PROFILE_CONFIDENCE


class NutritionDBTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.path = Path(self.tempdir.name) / "nutrition.db"
        self.db = NutritionDB(self.path)

    def test_migrations_are_explicit_and_reopen_is_idempotent(self):
        init_nutrition_db(self.path)
        init_nutrition_db(self.path)
        with sqlite3.connect(self.path) as connection:
            versions = connection.execute(
                "SELECT version FROM schema_version ORDER BY version"
            ).fetchall()
            mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
        self.assertEqual(versions, [(1,), (2,), (3,), (4,), (5,), (6,), (7,), (8,), (9,)])
        self.assertEqual(mode, "wal")

    def test_constraints_reject_invalid_confidence(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.upsert_resolution(
                mealie_food_id="food", mealie_food_name="Tomate",
                canonical_term="tomato", status="resolved", confidence=1.1,
                method="alias",
            )

    def test_v3_database_migrates_source_note_column_once(self):
        legacy_path = Path(self.tempdir.name) / "nutrition-v3.db"
        with sqlite3.connect(legacy_path) as connection:
            connection.execute(
                "CREATE TABLE schema_version (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
            )
            for version, sql in MIGRATIONS[:3]:
                connection.executescript(sql)
                connection.execute(
                    "INSERT INTO schema_version(version, applied_at) VALUES (?, 'before')",
                    (version,),
                )

        init_nutrition_db(legacy_path)
        init_nutrition_db(legacy_path)

        with sqlite3.connect(legacy_path) as connection:
            columns = {
                row[1] for row in connection.execute("PRAGMA table_info(ingredient_calculation)")
            }
            versions = connection.execute(
                "SELECT version FROM schema_version ORDER BY version"
            ).fetchall()
        self.assertIn("source_note", columns)
        self.assertEqual(versions, [(1,), (2,), (3,), (4,), (5,), (6,), (7,), (8,), (9,)])

    def test_v4_database_migrates_coverage_range_and_mapping_provenance_once(self):
        legacy_path = Path(self.tempdir.name) / "nutrition-v4.db"
        with sqlite3.connect(legacy_path) as connection:
            connection.execute(
                "CREATE TABLE schema_version (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
            )
            for version, sql in MIGRATIONS[:4]:
                connection.executescript(sql)
                connection.execute(
                    "INSERT INTO schema_version(version, applied_at) VALUES (?, 'before')", (version,),
                )
            connection.execute(
                "INSERT INTO food_resolution "
                "(mealie_food_id,mealie_food_name,canonical_term,resolution_status,confidence,"
                "resolution_method,manual_override,stale_after,updated_at) "
                "VALUES ('legacy', 'Tomate', 'tomato', 'ambiguous', 0, 'translated', 0, NULL, 'before')"
            )
            connection.execute(
                "INSERT INTO food_resolution "
                "(mealie_food_id,mealie_food_name,canonical_term,resolution_status,confidence,"
                "resolution_method,manual_override,stale_after,updated_at) "
                "VALUES ('manual', 'Cebolleta', 'spring onion', 'manual', 1, 'manual', 1, NULL, 'before')"
            )

        init_nutrition_db(legacy_path)
        init_nutrition_db(legacy_path)

        with sqlite3.connect(legacy_path) as connection:
            recipe_columns = {row[1] for row in connection.execute("PRAGMA table_info(recipe_calculation)")}
            ingredient_columns = {row[1] for row in connection.execute("PRAGMA table_info(ingredient_calculation)")}
            resolution_columns = {row[1] for row in connection.execute("PRAGMA table_info(food_resolution)")}
            versions = connection.execute("SELECT version FROM schema_version ORDER BY version").fetchall()
            legacy_expiry = connection.execute(
                "SELECT stale_after FROM food_resolution WHERE mealie_food_id='legacy'"
            ).fetchone()[0]
            manual_expiry = connection.execute(
                "SELECT stale_after FROM food_resolution WHERE mealie_food_id='manual'"
            ).fetchone()[0]
        self.assertTrue({"mass_coverage_ratio", "ingredient_coverage_ratio"} <= recipe_columns)
        self.assertTrue({"quantity_min", "quantity_max"} <= ingredient_columns)
        self.assertIn("source_note", resolution_columns)
        self.assertEqual(versions, [(1,), (2,), (3,), (4,), (5,), (6,), (7,), (8,), (9,)])
        remaining = datetime.fromisoformat(legacy_expiry) - datetime.now(timezone.utc)
        self.assertGreater(remaining, timedelta(0))
        self.assertLessEqual(remaining, timedelta(days=7))
        self.assertIsNone(manual_expiry)

    def test_db_rejects_non_finite_manual_mapping_and_conversion_values(self):
        self.db.upsert_resolution("food", "Food", "food", "resolved", 1, "alias")
        for confidence in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(kind="mapping", confidence=confidence), self.assertRaises(ValueError):
                self.db.upsert_resolution(
                    "food", "Food", "food", "manual", confidence, "manual", True,
                )
        self.db.upsert_resolution("food", "Food", "food", "manual", 1, "manual", True)
        for grams_per_unit, confidence in (
            (float("nan"), .8), (float("inf"), .8), (50, float("nan")), (50, float("inf")),
        ):
            with self.subTest(grams_per_unit=grams_per_unit, confidence=confidence), \
                    self.assertRaises(ValueError):
                self.db.upsert_conversion(
                    "food", "piece_weight", "piece", grams_per_unit, confidence,
                    "manual", manual_override=True,
                )

    def test_manual_mapping_confidence_and_provenance_are_persisted(self):
        self.db.upsert_resolution("food", "Cebolleta", None, "blocked", 0, "untranslated")
        self.db.upsert_resolution(
            "food", "Cebolleta", "spring onion", "manual", .82, "manual", True,
            source_note="Reviewed against supplier label", provider="spoonacular",
            provider_food_id=" 11291 ",
        )
        row = self.db.get_resolution("food")
        self.assertEqual(row["confidence"], .82)
        self.assertEqual(row["canonical_term"], "spring onion")
        self.assertEqual(row["provider_food_id"], "11291")
        self.assertEqual(row["source_note"], "Reviewed against supplier label")

    def test_v5_database_migrates_structured_provider_failure_columns_once(self):
        legacy_path = Path(self.tempdir.name) / "nutrition-v5.db"
        with sqlite3.connect(legacy_path) as connection:
            connection.execute(
                "CREATE TABLE schema_version (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
            )
            for version, sql in MIGRATIONS[:5]:
                connection.executescript(sql)
                connection.execute(
                    "INSERT INTO schema_version(version, applied_at) VALUES (?, 'before')", (version,),
                )
            connection.execute(
                "INSERT INTO provider_cache VALUES (?,?,?,?,?,?,?,?)",
                ("legacy", "provider", "success", '{"ok": true}', "hash", None,
                 "2099-01-01T00:00:00+00:00", "before"),
            )

        init_nutrition_db(legacy_path)
        init_nutrition_db(legacy_path)

        with sqlite3.connect(legacy_path) as connection:
            columns = {row[1] for row in connection.execute("PRAGMA table_info(provider_cache)")}
            versions = connection.execute(
                "SELECT version FROM schema_version ORDER BY version"
            ).fetchall()
        self.assertTrue({"http_status", "failure_class", "error_code", "retryable"} <= columns)
        self.assertEqual(versions, [(1,), (2,), (3,), (4,), (5,), (6,), (7,), (8,), (9,)])

        db = NutritionDB(legacy_path)
        self.assertEqual(db.get_cache("legacy")["payload_hash"], "hash")
        db.put_cache(
            "new", "provider", "error", {"ok": False, "code": "safe"}, None,
            "2099-01-01T00:00:00+00:00", error_code="safe", retryable=False,
        )
        self.assertEqual(db.get_cache("new")["error_code"], "safe")

    def test_provider_failure_cache_enforces_scalar_only_sanitization_at_db_boundary(self):
        secret = "db-boundary-secret-value"
        self.db.put_cache(
            "failure", "provider", "error",
            {"ok": False, "code": "safe", "nested": {"token": secret},
             "candidates": [{"id": "1", "name": "safe", "payload": {"key": secret}}]},
            None, "2099-01-01T00:00:00+00:00", 999,
            http_status=9999, failure_class="secret-class", error_code="safe-code",
        )
        row = self.db.get_cache("failure")
        cached = row["response_json"]
        self.assertNotIn(secret, cached)
        self.assertEqual(
            json.loads(cached)["candidates"], [{"id": "1", "name": "safe"}],
        )
        self.assertEqual(row["retry_after"], 300)
        self.assertIsNone(row["http_status"])
        self.assertIsNone(row["failure_class"])
        self.assertEqual(row["error_code"], "safe-code")

    def test_partial_v6_migration_restarts_after_first_column(self):
        legacy_path = Path(self.tempdir.name) / "nutrition-partial-v6.db"
        with sqlite3.connect(legacy_path) as connection:
            connection.execute(
                "CREATE TABLE schema_version (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
            )
            for version, sql in MIGRATIONS[:5]:
                connection.executescript(sql)
                connection.execute(
                    "INSERT INTO schema_version(version, applied_at) VALUES (?, 'before')", (version,),
                )
            connection.execute("ALTER TABLE provider_cache ADD COLUMN http_status INTEGER")

        init_nutrition_db(legacy_path)
        init_nutrition_db(legacy_path)

        with sqlite3.connect(legacy_path) as connection:
            columns = {row[1] for row in connection.execute("PRAGMA table_info(provider_cache)")}
            versions = connection.execute(
                "SELECT version FROM schema_version ORDER BY version"
            ).fetchall()
        self.assertTrue({"http_status", "failure_class", "error_code", "retryable"} <= columns)
        self.assertEqual(versions, [(1,), (2,), (3,), (4,), (5,), (6,), (7,), (8,), (9,)])

    def test_partial_v7_migration_restarts_after_first_column(self):
        legacy_path = Path(self.tempdir.name) / "nutrition-partial-v7.db"
        with sqlite3.connect(legacy_path) as connection:
            connection.execute(
                "CREATE TABLE schema_version (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
            )
            for version, sql in MIGRATIONS[:6]:
                connection.executescript(sql)
                connection.execute(
                    "INSERT INTO schema_version(version, applied_at) VALUES (?, 'before')", (version,),
                )
            connection.execute(
                "ALTER TABLE ingredient_calculation "
                "ADD COLUMN manual_mapping_applied INTEGER NOT NULL DEFAULT 0"
            )

        init_nutrition_db(legacy_path)
        init_nutrition_db(legacy_path)

        with sqlite3.connect(legacy_path) as connection:
            columns = {row[1] for row in connection.execute("PRAGMA table_info(ingredient_calculation)")}
            versions = connection.execute(
                "SELECT version FROM schema_version ORDER BY version"
            ).fetchall()
        self.assertTrue({"manual_mapping_applied", "mapping_updated_at"} <= columns)
        self.assertEqual(versions, [(1,), (2,), (3,), (4,), (5,), (6,), (7,), (8,), (9,)])

    def test_v8_normalizes_only_validated_non_manual_spoonacular_profiles(self):
        legacy_path = Path(self.tempdir.name) / "nutrition-v7.db"
        with sqlite3.connect(legacy_path) as connection:
            connection.execute(
                "CREATE TABLE schema_version (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
            )
            for version, sql in MIGRATIONS[:7]:
                connection.executescript(sql)
                connection.execute(
                    "INSERT INTO schema_version(version, applied_at) VALUES (?, 'before')", (version,),
                )
            rows = (
                ("spoon", 100, "spoonacular", "11260", .75, 0, 22),
                ("manual", 100, "spoonacular", "11261", .66, 1, 33),
                ("other", 100, "other-provider", "11262", .55, 0, 44),
                ("invalid", 100, "spoonacular", "12/34", .44, 0, 55),
                ("wrong-basis", 1, "spoonacular", "11263", .33, 0, 66),
            )
            for food_id, basis, provider, provider_id, confidence, manual, calories in rows:
                connection.execute(
                    "INSERT INTO food_resolution "
                    "(mealie_food_id,mealie_food_name,resolution_status,confidence,"
                    "resolution_method,manual_override,updated_at) VALUES (?,?,?,?,'legacy',0,'before')",
                    (food_id, food_id, "resolved", 1),
                )
                connection.execute(
                    "INSERT INTO nutrient_profile "
                    "(mealie_food_id,basis_grams,calories_kcal,provider,provider_food_id,confidence,"
                    "manual_override,payload_hash,retrieved_at) VALUES (?,?,?,?,?,?,?,'hash','before')",
                    (food_id, basis, calories, provider, provider_id, confidence, manual),
                )

        init_nutrition_db(legacy_path)

        with sqlite3.connect(legacy_path) as connection:
            profiles = connection.execute(
                "SELECT mealie_food_id,confidence,calories_kcal FROM nutrient_profile "
                "ORDER BY mealie_food_id"
            ).fetchall()
            versions = connection.execute(
                "SELECT version FROM schema_version ORDER BY version"
            ).fetchall()
        self.assertEqual(profiles, [
            ("invalid", .44, 55),
            ("manual", .66, 33),
            ("other", .55, 44),
            ("spoon", SPOONACULAR_PROFILE_CONFIDENCE, 22),
            ("wrong-basis", .33, 66),
        ])
        self.assertEqual(versions[-1], (9,))

    def test_v8_interruption_rolls_back_and_resumes_before_recording_version(self):
        legacy_path = Path(self.tempdir.name) / "nutrition-partial-v8.db"
        with sqlite3.connect(legacy_path) as connection:
            connection.execute(
                "CREATE TABLE schema_version (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
            )
            for version, sql in MIGRATIONS[:7]:
                connection.executescript(sql)
                connection.execute(
                    "INSERT INTO schema_version(version, applied_at) VALUES (?, 'before')", (version,),
                )
            connection.execute(
                "INSERT INTO food_resolution "
                "(mealie_food_id,mealie_food_name,resolution_status,confidence,"
                "resolution_method,manual_override,updated_at) "
                "VALUES ('spoon','spoon','resolved',1,'legacy',0,'before')"
            )
            connection.execute(
                "INSERT INTO nutrient_profile "
                "(mealie_food_id,basis_grams,calories_kcal,provider,provider_food_id,confidence,"
                "manual_override,payload_hash,retrieved_at) "
                "VALUES ('spoon',100,22,'spoonacular','11260',.75,0,'hash','before')"
            )

        from nutrition import db as db_module
        apply_statement = db_module._apply_migration_statement

        def interrupt_after_statement(connection, statement):
            apply_statement(connection, statement)
            if "nutrient_profile" in statement:
                raise RuntimeError("simulated interruption")

        with patch.object(db_module, "_apply_migration_statement", interrupt_after_statement):
            with self.assertRaisesRegex(RuntimeError, "simulated interruption"):
                init_nutrition_db(legacy_path)

        with sqlite3.connect(legacy_path) as connection:
            confidence = connection.execute(
                "SELECT confidence FROM nutrient_profile WHERE mealie_food_id='spoon'"
            ).fetchone()[0]
            recorded = connection.execute(
                "SELECT 1 FROM schema_version WHERE version=8"
            ).fetchone()
        self.assertEqual(confidence, .75)
        self.assertIsNone(recorded)

        init_nutrition_db(legacy_path)
        self.assertEqual(
            NutritionDB(legacy_path).get_profile("spoon")["confidence"],
            SPOONACULAR_PROFILE_CONFIDENCE,
        )

    def test_manual_mapping_rejects_unknown_target_but_accepts_persisted_evidence_target(self):
        with self.assertRaises(ManualMappingTargetError) as raised:
            self.db.upsert_resolution(
                "unknown", "Unknown", "unknown", "manual", 1, "manual", True,
                source_note="Reviewed",
            )
        self.assertEqual(raised.exception.code, "nutrition_mapping_target_unknown")

        calculation = {
            "id": "calc-evidence", "mealie_recipe_id": "recipe", "mealie_recipe_slug": "r",
            "input_hash": "input", "engine_version": "3", "profile_version": "p",
            "coverage_ratio": 0, "confidence": 0, "status": "insufficient",
            "ingredients": [{"position": 0, "original_text": "mystery", "status": "unresolved",
                             "mealie_food_id": "evidence-food"}],
        }
        self.db.save_calculation(calculation)
        self.assertTrue(self.db.is_mapping_target_valid("evidence-food"))
        self.assertTrue(self.db.upsert_resolution(
            "evidence-food", "Mystery", "known", "manual", .9, "manual", True,
            source_note="Reviewed",
        ))

    def test_validated_authoritative_target_is_atomically_created(self):
        self.assertTrue(self.db.upsert_resolution(
            "11111111-1111-4111-8111-111111111111", "Authoritative", "known",
            "manual", .9, "manual", True, source_note="Reviewed",
            provider="spoonacular", provider_food_id="123", target_validated=True,
        ))
        row = self.db.get_resolution("11111111-1111-4111-8111-111111111111")
        self.assertEqual(row["mealie_food_name"], "Authoritative")
        self.assertEqual(row["provider_food_id"], "123")

    def test_read_only_mapping_target_preview_does_not_mutate_database(self):
        self.db.upsert_resolution("known", "Known", None, "blocked", 0, "untranslated")
        modified_at = self.path.stat().st_mtime_ns

        self.assertTrue(NutritionDB.mapping_target_valid_at(self.path, "known"))
        self.assertFalse(NutritionDB.mapping_target_valid_at(self.path, "unknown"))
        self.assertEqual(self.path.stat().st_mtime_ns, modified_at)

    def test_manual_mapping_rejects_malformed_provider_identifier(self):
        self.db.upsert_resolution("food", "Food", None, "blocked", 0, "untranslated")
        for provider_food_id in ("   ", "12/34"):
            with self.subTest(provider_food_id=provider_food_id), self.assertRaises(ValueError):
                self.db.upsert_resolution(
                    "food", "Food", "food", "manual", 1, "manual", True,
                    source_note="Reviewed", provider_food_id=provider_food_id,
                )

    def test_mealie_food_ids_are_canonicalized_and_legacy_whitespace_is_read(self):
        self.db.upsert_resolution(" food ", "Food", None, "blocked", 0, "untranslated")
        self.assertEqual(self.db.get_resolution("food")["mealie_food_id"], "food")
        self.assertTrue(self.db.is_mapping_target_valid(" food "))
        with self.assertRaises(ValueError):
            self.db.get_resolution("   ")
        with self.db.connect() as connection:
            count = connection.execute(
                "SELECT COUNT(*) FROM food_resolution WHERE TRIM(mealie_food_id)='food'"
            ).fetchone()[0]
        self.assertEqual(count, 1)

        with self.db.connect() as connection:
            connection.execute(
                "INSERT INTO food_resolution "
                "(mealie_food_id,mealie_food_name,resolution_status,confidence,resolution_method,updated_at) "
                "VALUES (' legacy ', 'Legacy', 'blocked', 0, 'legacy', 'before')"
            )
        self.assertEqual(self.db.get_resolution("legacy")["mealie_food_name"], "Legacy")
        self.db.upsert_resolution(" legacy ", "Updated", None, "blocked", 0, "legacy")
        with self.db.connect() as connection:
            legacy_rows = connection.execute(
                "SELECT mealie_food_id FROM food_resolution WHERE TRIM(mealie_food_id)='legacy'"
            ).fetchall()
        self.assertEqual([tuple(row) for row in legacy_rows], [("legacy",)])

    def test_manual_mapping_count_excludes_mapping_applied_after_calculation(self):
        self.db.upsert_resolution("before", "Before", None, "blocked", 0, "untranslated")
        self.db.upsert_resolution(
            "before", "Before", "before", "manual", 1, "manual", True,
            source_note="Reviewed before calculation",
        )
        self.db.upsert_resolution("after", "After", None, "blocked", 0, "untranslated")
        calculation = {
            "id": "chronology", "mealie_recipe_id": "recipe", "mealie_recipe_slug": "r",
            "input_hash": "input", "engine_version": "3", "profile_version": "p",
            "coverage_ratio": 0, "confidence": 0, "status": "insufficient",
            "ingredients": [
                {"position": 0, "original_text": "before", "status": "unresolved",
                 "mealie_food_id": "before"},
                {"position": 1, "original_text": "after", "status": "unresolved",
                 "mealie_food_id": "after"},
            ],
        }
        self.db.save_calculation(calculation)
        with self.db.connect() as connection:
            connection.execute(
                "UPDATE recipe_calculation SET calculated_at='2026-08-08T10:00:00+00:00' "
                "WHERE id='chronology'"
            )
            connection.execute(
                "UPDATE food_resolution SET updated_at='2026-08-08T09:00:00+00:00' "
                "WHERE mealie_food_id='before'"
            )
        self.db.upsert_resolution(
            "after", "After", "after", "manual", 1, "manual", True,
            source_note="Reviewed after calculation",
        )

        self.assertEqual(self.db.manual_mapping_count_for_calculation("chronology"), 1)

    def test_manual_mapping_count_is_immutable_after_mapping_edit(self):
        self.db.upsert_resolution("food", "Food", None, "blocked", 0, "untranslated")
        self.db.upsert_resolution(
            "food", "Food", "first", "manual", 1, "manual", True,
            source_note="Reviewed first",
        )
        calculation = {
            "id": "immutable", "mealie_recipe_id": "recipe", "mealie_recipe_slug": "r",
            "input_hash": "input", "engine_version": "3", "profile_version": "p",
            "coverage_ratio": 0, "confidence": 0, "status": "insufficient",
            "ingredients": [{"position": 0, "original_text": "food", "status": "unresolved",
                             "mealie_food_id": " food "}],
        }
        self.db.save_calculation(calculation)
        before = self.db.manual_mapping_count_for_calculation("immutable")
        self.db.upsert_resolution(
            "food", "Food", "second", "resolved", 1, "provider", False,
        )
        after = self.db.manual_mapping_count_for_calculation("immutable")

        self.assertEqual((before, after), (1, 1))

    def test_dual_coverage_counts_and_range_evidence_are_persisted_truthfully(self):
        recipe = {"id": "recipe", "slug": "r", "recipeServings": 1, "recipeIngredient": [
            {"id": "known", "quantity": 2, "unit": {"name": "g"},
             "food": {"id": "food", "name": "tomato"}, "originalText": "2-4 g tomato"},
            {"id": "linked-only", "quantity": 1, "unit": {"name": "bunch"},
             "food": {"id": "linked", "name": "parsley"}, "originalText": "1 bunch parsley"},
        ]}
        result = calculate_recipe_nutrition(
            recipe, {"food": NutrientProfile("food", calories_kcal=18, confidence=1)},
            input_hash="input", profile_version="profiles",
        )
        self.db.save_calculation(result.to_dict())

        with self.db.connect() as connection:
            parent = connection.execute(
                "SELECT mass_coverage_ratio,ingredient_coverage_ratio,coverage_ratio,"
                "resolved_count,computable_count FROM recipe_calculation"
            ).fetchone()
            range_row = connection.execute(
                "SELECT quantity,quantity_min,quantity_max FROM ingredient_calculation WHERE position=0"
            ).fetchone()
        self.assertEqual(tuple(parent), (1, .5, .5, 1, 1))
        self.assertEqual(tuple(range_row), (3, 2, 4))

    def test_manual_resolution_and_profile_survive_provider_refresh(self):
        self.db.upsert_resolution(
            mealie_food_id="food", mealie_food_name="Tomate",
            canonical_term=None, status="blocked", confidence=0,
            method="untranslated",
        )
        self.db.upsert_resolution(
            mealie_food_id="food", mealie_food_name="Tomate",
            canonical_term="tomato", status="manual", confidence=1,
            method="manual", manual_override=True,
        )
        self.db.upsert_profile(
            "food", {"calories_kcal": 18, "protein_g": 0.9},
            provider="manual", confidence=1, payload_hash="manual",
            manual_override=True,
        )
        changed = self.db.upsert_profile(
            "food", {"calories_kcal": 20}, provider="spoonacular",
            confidence=.9, payload_hash="provider",
        )
        self.assertFalse(changed)
        self.assertEqual(self.db.get_profile("food")["calories_kcal"], 18)
        self.assertTrue(self.db.get_resolution("food")["manual_override"])

    def test_provider_profile_racing_committed_manual_profile_is_no_op(self):
        self.db.upsert_resolution(
            "food", "Dashi", "dashi", "resolved", .9, "alias",
        )
        provider_write_started = threading.Event()
        provider_result = []
        provider_errors = []
        original_connect = self.db.connect

        def traced_connect():
            connection = original_connect()
            if threading.current_thread() is not threading.main_thread():
                connection.set_trace_callback(
                    lambda sql: provider_write_started.set()
                    if sql.lstrip().startswith("INSERT INTO nutrient_profile") else None
                )
            return connection

        def write_provider_profile():
            try:
                provider_result.append(self.db.upsert_profile(
                    "food", {"calories_kcal": 20}, "spoonacular", .9, "provider",
                ))
            except Exception as error:
                provider_errors.append(error)

        with self.db.connect() as manual_connection:
            manual_connection.execute("BEGIN IMMEDIATE")
            manual_connection.execute("""
                INSERT INTO nutrient_profile
                (mealie_food_id,basis_grams,calories_kcal,protein_g,provider,confidence,
                 manual_override,source_version,payload_hash,retrieved_at,source_note)
                VALUES ('food',100,40,5,'manual',.95,1,'label-v2',?,?,'Reviewed')
            """, ("m" * 64, "2026-08-10T00:00:00+00:00"))
            with patch.object(self.db, "connect", side_effect=traced_connect):
                provider_thread = threading.Thread(target=write_provider_profile)
                provider_thread.start()
                self.assertTrue(provider_write_started.wait(2))
                manual_connection.commit()
                provider_thread.join(2)

        self.assertFalse(provider_thread.is_alive())
        self.assertEqual(provider_errors, [])
        self.assertEqual(provider_result, [False])
        protected = self.db.get_profile("food")
        self.assertEqual(protected["provider"], "manual")
        self.assertEqual(protected["payload_hash"], "m" * 64)
        self.assertEqual(protected["calories_kcal"], 40)

    def test_manual_profile_apply_is_atomic_and_returns_prior_rollback_evidence(self):
        self.db.upsert_resolution("food", "Dashi", None, "blocked", 0, "untranslated")
        first = self.db.set_manual_profile(
            "food", {"calories_kcal": 40, "protein_g": 5}, 100, .9,
            "label-v1", "Reviewed package label", "1" * 64,
        )
        mapping_before = self.db.get_resolution("food")
        self.db.upsert_conversion("food", "density", "milliliter", 1, .8, "manual")
        second = self.db.set_manual_profile(
            "food", {"calories_kcal": 42, "protein_g": 5.2}, 100, .95,
            "label-v2", "Second review", "2" * 64,
        )

        self.assertIsNone(first)
        self.assertEqual(second["payload_hash"], "1" * 64)
        self.assertEqual(second["source_version"], "label-v1")
        profile = self.db.get_profile("food")
        self.assertEqual(profile["provider"], "manual")
        self.assertEqual(profile["manual_override"], 1)
        self.assertEqual(profile["source_note"], "Second review")
        self.assertEqual(self.db.get_resolution("food"), mapping_before)
        self.assertEqual(len(self.db.list_conversions("food")), 1)

    def test_manual_profile_apply_revalidates_target_inside_transaction(self):
        with self.assertRaises(ManualMappingTargetError):
            self.db.set_manual_profile(
                "missing", {"calories_kcal": 10, "protein_g": 1}, 100, .8,
                "reference-v1", "Reviewed", "1" * 64, target_validated=False,
            )

    def test_authoritative_manual_profile_creates_only_neutral_parent_resolution(self):
        prior = self.db.set_manual_profile(
            "11111111-1111-4111-8111-111111111111",
            {"calories_kcal": 10, "protein_g": 1}, 100, .8,
            "reference-v1", "Reviewed", "1" * 64,
            target_validated=True, target_name="Dashi",
        )

        self.assertIsNone(prior)
        resolution = self.db.get_resolution("11111111-1111-4111-8111-111111111111")
        self.assertEqual(resolution["mealie_food_name"], "Dashi")
        self.assertEqual(resolution["resolution_method"], "manual_profile_target")
        self.assertFalse(resolution["manual_override"])

    def test_conversion_override_precedence_and_history_persistence(self):
        self.db.upsert_resolution(
            mealie_food_id="food", mealie_food_name="Huevo",
            canonical_term="egg", status="resolved", confidence=.98,
            method="alias",
        )
        self.db.upsert_conversion(
            "food", "piece_weight", "piece", 50, .8, "curated"
        )
        self.db.upsert_conversion(
            "food", "piece_weight", "piece", 60, 1, "manual",
            manual_override=True,
        )
        self.db.upsert_conversion(
            "food", "piece_weight", "piece", 55, .9, "provider"
        )
        self.assertEqual(
            self.db.get_conversion("food", "piece_weight", "piece")["grams_per_unit"],
            60,
        )
        calculation = {
            "id": "calc", "mealie_recipe_id": "recipe", "mealie_recipe_slug": "r",
            "input_hash": "input", "engine_version": "1", "profile_version": "p",
            "servings": 2, "ingredient_count": 1, "resolved_count": 1,
            "computable_count": 1, "coverage_ratio": 1, "confidence": .9,
            "status": "complete", "totals": {"calories_kcal": 100},
            "per_serving": {"calories_kcal": 50}, "unresolved": [],
            "assumptions": [], "ingredients": [{"position": 0,
                "original_text": "1 egg", "status": "computed",
                "conversion_confidence": 1, "nutrition_confidence": .9}],
        }
        self.db.save_calculation(calculation)
        self.assertEqual(self.db.find_calculation("recipe", "input", "1", "p")["id"], "calc")
        self.db.record_publish("calc", {"nutrition": {"calories": "40 kcal"}}, {"nutrition": {"calories": "50 kcal"}})
        self.assertEqual(len(self.db.publish_history("calc")), 1)

    def test_repeated_calculation_save_updates_parent_and_replaces_evidence(self):
        calculation = {
            "id": "calc", "mealie_recipe_id": "recipe", "mealie_recipe_slug": "old",
            "input_hash": "input", "engine_version": "1", "profile_version": "p",
            "servings": 2, "coverage_ratio": 1, "confidence": .9,
            "status": "complete", "totals": {"calories_kcal": 100},
            "ingredients": [{"position": 0, "original_text": "old", "status": "computed"}],
        }
        self.db.save_calculation(calculation)
        calculation.update({
            "mealie_recipe_slug": "current", "confidence": .95,
            "totals": {"calories_kcal": 120},
            "ingredients": [
                {"position": 1, "original_text": "second", "status": "computed"},
                {"position": 0, "original_text": "first", "status": "computed"},
            ],
        })

        self.db.save_calculation(calculation)

        with self.db.connect() as connection:
            parent = connection.execute(
                "SELECT mealie_recipe_slug,confidence,total_calories_kcal,ingredient_count "
                "FROM recipe_calculation WHERE id='calc'"
            ).fetchone()
            evidence = connection.execute(
                "SELECT position,original_text FROM ingredient_calculation "
                "WHERE recipe_calculation_id='calc' ORDER BY position"
            ).fetchall()
        self.assertEqual(tuple(parent), ("current", .95, 120, 2))
        self.assertEqual([tuple(row) for row in evidence], [(0, "first"), (1, "second")])

    def test_conversion_source_note_survives_calculation_and_persistence_with_db_precedence(self):
        self.db.upsert_resolution(
            mealie_food_id="manual-egg", mealie_food_name="Egg",
            canonical_term=None, status="blocked", confidence=0,
            method="untranslated",
        )
        self.db.upsert_resolution(
            mealie_food_id="manual-egg", mealie_food_name="Egg",
            canonical_term="egg", status="manual", confidence=1,
            method="manual", manual_override=True,
        )
        self.db.upsert_conversion(
            "manual-egg", "piece_weight", "piece", 63, 1, "manual",
            manual_override=True, source_note="Measured kitchen egg",
        )
        manual = self.db.get_conversion("manual-egg", "piece_weight", "piece")
        recipe = {
            "id": "recipe", "slug": "eggs", "recipeServings": 1,
            "recipeIngredient": [
                {"quantity": 1, "unit": {"name": "piece"},
                 "food": {"id": "curated-egg", "name": "egg"},
                 "originalText": "1 egg"},
                {"quantity": 1, "unit": {"name": "piece"},
                 "food": {"id": "manual-egg", "name": "egg"},
                 "originalText": "1 measured egg"},
            ],
        }
        profiles = {
            food_id: NutrientProfile(food_id, calories_kcal=143, confidence=1)
            for food_id in ("curated-egg", "manual-egg")
        }
        calculation = calculate_recipe_nutrition(
            recipe, profiles,
            ConversionContext(conversions={
                ("manual-egg", "piece_weight", "piece"): manual,
            }),
            input_hash="input", profile_version="profiles",
        )

        self.db.save_calculation(calculation.to_dict())

        with self.db.connect() as connection:
            evidence = connection.execute(
                "SELECT position,consumed_grams,conversion_method,source_note "
                "FROM ingredient_calculation ORDER BY position"
            ).fetchall()
        self.assertEqual(tuple(evidence[0]), (
            0, 50, "curated_piece_weight",
            CURATED_PIECE_WEIGHTS["egg"]["source_note"],
        ))
        self.assertEqual(
            tuple(evidence[1]),
            (1, 63, "piece_weight", "Measured kitchen egg"),
        )

    def test_unique_calculation_identity_returns_existing_parent_id(self):
        calculation = {
            "id": "original", "mealie_recipe_id": "recipe", "mealie_recipe_slug": "r",
            "input_hash": "input", "engine_version": "1", "profile_version": "p",
            "coverage_ratio": 1, "confidence": .9, "status": "complete",
            "ingredients": [],
        }
        self.db.save_calculation(calculation)
        calculation["id"] = "duplicate"

        persisted_id = self.db.save_calculation(calculation)

        with self.db.connect() as connection:
            ids = connection.execute("SELECT id FROM recipe_calculation").fetchall()
        self.assertEqual(persisted_id, "original")
        self.assertEqual([row[0] for row in ids], ["original"])


if __name__ == "__main__":
    unittest.main()
