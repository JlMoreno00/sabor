import hashlib
import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch


from nutrition.readiness import (
    FoodEvidenceRequest,
    ReadinessPolicy,
    _snapshot_bytes,
    read_readiness_evidence,
    recipe_evidence_requests,
)


NOW = datetime(2026, 8, 11, tzinfo=timezone.utc)


class SidecarReadinessEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.directory = Path(self.tempdir.name)
        self.path = self.directory / "nutrition.db"

    def snapshot(self):
        return {
            path.name: (hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns)
            for path in self.directory.iterdir() if path.is_file()
        }

    def create_schema(self, version=9, *, page_size=None):
        connection = sqlite3.connect(self.path)
        if page_size is not None:
            connection.execute(f"PRAGMA page_size={page_size}")
        connection.executescript("""
            CREATE TABLE schema_version(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL);
            CREATE TABLE food_resolution(
                mealie_food_id TEXT PRIMARY KEY, mealie_food_name TEXT NOT NULL,
                provider TEXT, provider_food_id TEXT, resolution_status TEXT NOT NULL,
                confidence REAL NOT NULL, manual_override INTEGER NOT NULL,
                stale_after TEXT, updated_at TEXT NOT NULL
            );
            CREATE TABLE nutrient_profile(
                mealie_food_id TEXT PRIMARY KEY, basis_grams REAL NOT NULL,
                calories_kcal REAL, protein_g REAL, carbohydrate_g REAL, fat_g REAL,
                provider TEXT NOT NULL, provider_food_id TEXT, confidence REAL NOT NULL,
                manual_override INTEGER NOT NULL, stale_after TEXT
            );
            CREATE TABLE food_conversion(
                mealie_food_id TEXT, conversion_type TEXT NOT NULL, unit_key TEXT NOT NULL,
                grams_per_unit REAL NOT NULL, confidence REAL NOT NULL, provider TEXT NOT NULL,
                manual_override INTEGER NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE recipe_calculation(
                id TEXT PRIMARY KEY, mealie_recipe_id TEXT NOT NULL,
                per_serving_json TEXT, published_to_mealie INTEGER NOT NULL,
                calculated_at TEXT NOT NULL
            );
            CREATE TABLE nutrition_publish_history(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                recipe_calculation_id TEXT NOT NULL, previous_payload_json TEXT NOT NULL,
                published_payload_json TEXT NOT NULL, published_at TEXT NOT NULL
            );
        """)
        connection.execute("INSERT INTO schema_version VALUES (?, ?)", (version, NOW.isoformat()))
        connection.commit()
        return connection

    def add_ready_food(self, connection, food_id="food", *, resolution=None, profile=None):
        resolution_values = {
            "provider": "spoonacular", "provider_food_id": "provider-food",
            "resolution_status": "resolved", "confidence": .9, "manual_override": 0,
            "stale_after": (NOW + timedelta(days=1)).isoformat(),
        }
        resolution_values.update(resolution or {})
        connection.execute(
            "INSERT INTO food_resolution VALUES (?,?,?,?,?,?,?,?,?)",
            (food_id, "Food", resolution_values["provider"], resolution_values["provider_food_id"],
             resolution_values["resolution_status"], resolution_values["confidence"],
             resolution_values["manual_override"], resolution_values["stale_after"], NOW.isoformat()),
        )
        profile_values = {
            "basis_grams": 100, "calories_kcal": 100, "protein_g": 5,
            "carbohydrate_g": 10, "fat_g": 2, "provider": "spoonacular",
            "provider_food_id": "provider-food", "confidence": .9,
            "manual_override": 0, "stale_after": (NOW + timedelta(days=1)).isoformat(),
        }
        profile_values.update(profile or {})
        connection.execute(
            "INSERT INTO nutrient_profile VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (food_id, *(profile_values[key] for key in (
                "basis_grams", "calories_kcal", "protein_g", "carbohydrate_g", "fat_g",
                "provider", "provider_food_id", "confidence", "manual_override", "stale_after",
            ))),
        )
        connection.commit()

    def read(self, *requests, recipe_id=None, nutrition=None):
        return read_readiness_evidence(
            self.path, requests or (FoodEvidenceRequest("food"),),
            policy=ReadinessPolicy(), now=NOW, recipe_id=recipe_id,
            current_nutrition=nutrition,
        )

    def add_publication(self, connection, *, recipe_id="recipe-id", calculation_id="calc-1",
                        per_serving=None, published_payload=None, published_at=None,
                        published_to_mealie=1):
        per_serving = per_serving or {
            "calories_kcal": 100, "protein_g": 5.04, "sodium_mg": 201.2,
        }
        published_at = published_at or NOW.isoformat()
        published_payload = published_payload or {"nutrition": {
            "calories": "100 kcal", "proteinContent": "5.0 g",
            "sodiumContent": "201 mg", "unsupportedPrivateField": "private",
        }, "settings": {"private": "ignored"}}
        connection.execute(
            "INSERT INTO recipe_calculation VALUES (?,?,?,?,?)",
            (calculation_id, recipe_id, json.dumps(per_serving), published_to_mealie,
             published_at),
        )
        connection.execute(
            "INSERT INTO nutrition_publish_history"
            "(recipe_calculation_id,previous_payload_json,published_payload_json,published_at) "
            "VALUES (?,?,?,?)",
            (calculation_id, "{}", json.dumps(published_payload), published_at),
        )
        connection.commit()

    def test_26_absent_sidecar_is_indeterminate_and_creates_nothing(self):
        before = self.snapshot()
        evidence = self.read()
        self.assertEqual(evidence.status, "indeterminate")
        self.assertEqual(self.snapshot(), before)

    def test_27_incompatible_schema_version_is_indeterminate(self):
        connection = self.create_schema(version=10)
        connection.close()
        before = self.snapshot()
        evidence = self.read()
        self.assertEqual(evidence.status, "indeterminate")
        self.assertEqual(self.snapshot(), before)

    def test_28_corrupt_or_unavailable_sidecar_is_failure_safe(self):
        self.path.write_bytes(b"not sqlite and not a secret")
        before = self.snapshot()
        evidence = self.read()
        self.assertEqual(evidence.status, "indeterminate")
        self.assertEqual(self.snapshot(), before)

    def test_29_reader_returns_schema_version_and_only_requested_food_ids(self):
        connection = self.create_schema()
        self.add_ready_food(connection, "wanted")
        self.add_ready_food(connection, "other")
        connection.close()
        evidence = self.read(FoodEvidenceRequest("wanted"))
        self.assertEqual(evidence.schema_version, 9)
        self.assertEqual(tuple(item.food_id for item in evidence.foods), ("wanted",))

    def test_30_missing_resolution_and_profile_are_classified(self):
        self.create_schema().close()
        food = self.read().foods[0]
        self.assertEqual(food.resolution_status, "missing")
        self.assertEqual(food.profile_status, "missing")

    def test_31_ambiguous_resolution_precedes_profile_symptoms(self):
        connection = self.create_schema()
        self.add_ready_food(connection, resolution={"resolution_status": "ambiguous"})
        connection.close()
        self.assertEqual(self.read().foods[0].resolution_status, "ambiguous")

    def test_32_stale_resolution_and_profile_are_classified(self):
        stale = (NOW - timedelta(seconds=1)).isoformat()
        connection = self.create_schema()
        self.add_ready_food(connection, resolution={"stale_after": stale}, profile={"stale_after": stale})
        connection.close()
        food = self.read().foods[0]
        self.assertEqual((food.resolution_status, food.profile_status), ("stale", "stale"))

    def test_33_low_confidence_resolution_profile_and_conversion_are_classified(self):
        connection = self.create_schema()
        self.add_ready_food(connection, resolution={"confidence": .79}, profile={"confidence": .79})
        connection.execute(
            "INSERT INTO food_conversion VALUES (?,?,?,?,?,?,?,?)",
            ("food", "density", "ml", 1, .79, "manual", 1, NOW.isoformat()),
        )
        connection.commit()
        connection.close()
        food = self.read(FoodEvidenceRequest("food", (("density", "ml"),))).foods[0]
        self.assertEqual((food.resolution_status, food.profile_status, food.conversion_status),
                         ("low_confidence", "low_confidence", "low_confidence"))

    def test_34_nonfinite_or_nonpositive_values_are_invalid(self):
        connection = self.create_schema()
        self.add_ready_food(connection, profile={"basis_grams": -1, "calories_kcal": float("inf")})
        connection.execute(
            "INSERT INTO food_conversion VALUES (?,?,?,?,?,?,?,?)",
            ("food", "piece_weight", "piece", float("inf"), .9, "manual", 1, NOW.isoformat()),
        )
        connection.commit()
        connection.close()
        food = self.read(FoodEvidenceRequest("food", (("piece_weight", "piece"),))).foods[0]
        self.assertEqual(food.profile_status, "invalid")
        self.assertEqual(food.conversion_status, "invalid")

    def test_35_manual_profile_is_valid_without_provider_binding(self):
        connection = self.create_schema()
        self.add_ready_food(
            connection,
            resolution={"provider": None, "provider_food_id": None, "resolution_status": "manual",
                        "manual_override": 1},
            profile={"provider": "manual", "provider_food_id": None, "manual_override": 1},
        )
        connection.close()
        food = self.read().foods[0]
        self.assertEqual((food.resolution_status, food.profile_status), ("ready", "ready"))

    def test_36_provider_binding_mismatch_is_classified(self):
        connection = self.create_schema()
        self.add_ready_food(connection, profile={"provider_food_id": "different"})
        connection.close()
        self.assertEqual(self.read().foods[0].profile_status, "binding_mismatch")

    def test_37_direct_mass_request_needs_no_conversion(self):
        connection = self.create_schema()
        self.add_ready_food(connection)
        connection.close()
        self.assertEqual(self.read(FoodEvidenceRequest("food")).foods[0].conversion_status, "ready")
        requests = recipe_evidence_requests({"recipeIngredient": [{
            "quantity": 100, "unit": {"name": "grams", "abbreviation": "g"},
            "food": {"id": "food", "name": "food"}, "originalText": "100 g food",
        }]})
        self.assertEqual(requests, (FoodEvidenceRequest("food"),))

    def test_38_conversion_lookup_uses_exact_food_type_and_unit_without_fallback(self):
        connection = self.create_schema()
        self.add_ready_food(connection)
        connection.executemany(
            "INSERT INTO food_conversion VALUES (?,?,?,?,?,?,?,?)",
            [
                (None, "density", "ml", 1, 1, "manual", 1, NOW.isoformat()),
                ("food", "culinary_measure", "tablespoon", 15, .9, "manual", 1, NOW.isoformat()),
            ],
        )
        connection.commit()
        connection.close()
        exact = self.read(FoodEvidenceRequest("food", (("culinary_measure", "tablespoon"),))).foods[0]
        absent = self.read(FoodEvidenceRequest("food", (("density", "ml"),))).foods[0]
        self.assertEqual(exact.conversion_status, "ready")
        self.assertEqual(absent.conversion_status, "missing")
        requests = recipe_evidence_requests({"recipeIngredient": [
            {"quantity": 1, "unit": {"name": "Cucharadas", "abbreviation": "cda"},
             "food": {"id": "oil", "name": "oil"}, "originalText": "1 cda oil"},
            {"quantity": 2, "unit": {"name": "units", "abbreviation": "uds"},
             "food": {"id": "fruit", "name": "mystery fruit"}, "originalText": "2 fruit"},
            {"quantity": 1, "unit": {"name": "cans", "abbreviation": "latas"},
             "food": {"id": "beans", "name": "beans"}, "originalText": "1 can beans"},
            {"quantity": 2, "unit": None, "food": {"id": "egg", "name": "egg"},
             "originalText": "2 eggs"},
        ]})
        by_food = {request.food_id: request.conversion_candidates for request in requests}
        self.assertEqual(by_food["oil"], (
            ("culinary_measure", "tablespoon"), ("density", "ml"),
        ))
        self.assertEqual(by_food["fruit"], (("piece_weight", "piece"),))
        self.assertEqual(by_food["beans"], (("package_weight", "can"),))
        self.assertEqual(by_food["egg"], ())

    def test_39_wal_commits_are_visible_without_changing_db_wal_or_shm(self):
        connection = self.create_schema()
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA wal_autocheckpoint=0")
        self.add_ready_food(connection)
        connection.execute("UPDATE nutrient_profile SET confidence=.7 WHERE mealie_food_id='food'")
        connection.commit()
        before = self.snapshot()
        evidence = self.read()
        after = self.snapshot()
        self.assertEqual(evidence.foods[0].profile_status, "low_confidence")
        self.assertIsNone(evidence.reason_code)
        self.assertEqual(after, before)
        connection.close()

    def test_40_checkpointed_wal_main_without_companions_is_read_in_memory_only(self):
        connection = self.create_schema()
        connection.execute("PRAGMA journal_mode=WAL")
        self.add_ready_food(connection)
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        connection.close()

        self.assertEqual(self.path.read_bytes()[18:20], b"\x02\x02")
        self.assertFalse(Path(f"{self.path}-wal").exists())
        self.assertFalse(Path(f"{self.path}-shm").exists())
        before = self.snapshot()

        image = _snapshot_bytes(self.path)
        evidence = self.read()

        self.assertEqual(image[18:20], b"\x01\x01")
        self.assertEqual(evidence.status, "available")
        self.assertEqual(evidence.schema_version, 9)
        self.assertEqual(evidence.foods[0].profile_status, "ready")
        self.assertIsNone(evidence.reason_code)
        self.assertEqual(self.snapshot(), before)

    def test_41_rollback_and_unusual_page_sizes_remain_readable_and_unchanged(self):
        for page_size in (512, 65536):
            with self.subTest(page_size=page_size):
                self.path = self.directory / f"nutrition-{page_size}.db"
                connection = self.create_schema(page_size=page_size)
                self.add_ready_food(connection)
                connection.close()
                before = self.snapshot()

                image = _snapshot_bytes(self.path)
                evidence = self.read()

                self.assertEqual(int.from_bytes(image[16:18], "big"), 1 if page_size == 65536 else page_size)
                self.assertEqual(image[18:20], b"\x01\x01")
                self.assertEqual(evidence.status, "available")
                self.assertEqual(evidence.foods[0].profile_status, "ready")
                self.assertEqual(self.snapshot(), before)

    def test_42_malformed_main_headers_are_bounded_and_path_safe(self):
        self.create_schema().close()
        valid = self.path.read_bytes()
        cases = {
            "empty": b"",
            "short_header": valid[:99],
            "bad_magic": b"Not SQLite!!!!!" + valid[16:],
            "bad_page_size": valid[:16] + b"\x00\x03" + valid[18:],
            "partial_page": valid[:-1],
        }
        for name, content in cases.items():
            with self.subTest(name=name):
                self.path.write_bytes(content)
                evidence = self.read()
                self.assertEqual(evidence.status, "indeterminate")
                self.assertEqual(evidence.reason_code, "snapshot_invalid")
                self.assertNotIn(str(self.path), repr(evidence))

    def test_43_sqlite_failures_have_bounded_reason_codes_without_exception_text(self):
        self.create_schema().close()
        with patch("nutrition.readiness.sqlite3.connect", side_effect=sqlite3.OperationalError("secret")):
            evidence = self.read()
        self.assertEqual(evidence.status, "indeterminate")
        self.assertEqual(evidence.reason_code, "sqlite_unavailable")
        self.assertNotIn("secret", repr(evidence))

        connection = MagicMock()
        connection.__enter__.return_value = connection
        connection.deserialize.side_effect = sqlite3.OperationalError("secret /private/database/path")
        with patch("nutrition.readiness.sqlite3.connect", return_value=connection):
            evidence = self.read()
        self.assertEqual(evidence.reason_code, "snapshot_deserialize_failed")
        self.assertNotIn("secret", repr(evidence))
        self.assertNotIn("/private/database/path", repr(evidence))

        connection = self.create_schema() if not self.path.exists() else sqlite3.connect(self.path)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA wal_autocheckpoint=0")
        connection.execute("INSERT OR REPLACE INTO schema_version VALUES (9, ?)", (NOW.isoformat(),))
        connection.commit()
        wal_path = Path(f"{self.path}-wal")
        if wal_path.is_file():
            corrupted = bytearray(wal_path.read_bytes())
            corrupted[-1] ^= 1
            wal_path.write_bytes(corrupted)
            evidence = self.read()
            self.assertEqual(evidence.status, "indeterminate")
            self.assertEqual(evidence.reason_code, "snapshot_invalid")
        connection.close()

    def test_44_truncated_wal_header_is_bounded_unavailable(self):
        self.create_schema().close()
        Path(f"{self.path}-wal").write_bytes(b"\0" * 31)
        evidence = self.read()
        self.assertEqual(evidence.status, "indeterminate")
        self.assertEqual(evidence.reason_code, "snapshot_invalid")

    def test_45_hot_rollback_journal_never_exposes_spilled_uncommitted_pages(self):
        connection = self.create_schema(page_size=512)
        self.add_ready_food(connection)
        connection.execute("PRAGMA journal_mode=DELETE")
        connection.execute("PRAGMA cache_size=1")
        connection.execute("PRAGMA cache_spill=ON")
        committed_main = self.path.read_bytes()

        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "UPDATE nutrient_profile SET confidence=.7 WHERE mealie_food_id='food'"
        )
        for index in range(500):
            connection.execute(
                "INSERT INTO food_resolution VALUES (?,?,?,?,?,?,?,?,?)",
                (f"spill-{index}", "x" * 200, None, None, "missing", 0, 0, None,
                 NOW.isoformat()),
            )

        journal_path = Path(f"{self.path}-journal")
        self.assertTrue(journal_path.is_file())
        self.assertGreater(journal_path.stat().st_size, 0)
        self.assertNotEqual(self.path.read_bytes(), committed_main)
        before = self.snapshot()

        evidence = self.read()

        self.assertEqual(evidence.status, "indeterminate")
        self.assertEqual(evidence.reason_code, "rollback_journal_active")
        self.assertEqual(evidence.foods, ())
        self.assertNotIn("low_confidence", repr(evidence))
        self.assertEqual(self.snapshot(), before)

        connection.rollback()
        connection.close()
        committed = self.read()
        self.assertEqual(committed.status, "available")
        self.assertEqual(committed.foods[0].profile_status, "ready")
        self.assertIsNone(committed.reason_code)

    def test_46_journal_appearance_during_snapshot_fails_closed(self):
        connection = self.create_schema()
        self.add_ready_food(connection)
        connection.close()
        journal_path = Path(f"{self.path}-journal")
        original_read_bytes = Path.read_bytes
        appeared = False

        def read_with_journal_appearance(path):
            nonlocal appeared
            content = original_read_bytes(path)
            if path == self.path and not appeared:
                appeared = True
                journal_path.write_bytes(b"journal appeared during snapshot")
            return content

        with patch.object(Path, "read_bytes", read_with_journal_appearance):
            evidence = self.read()

        self.assertTrue(appeared)
        self.assertEqual(evidence.status, "indeterminate")
        self.assertEqual(evidence.reason_code, "rollback_journal_active")

    def test_47_journal_disappearance_during_snapshot_fails_closed(self):
        connection = self.create_schema()
        self.add_ready_food(connection)
        connection.close()
        journal_path = Path(f"{self.path}-journal")
        journal_path.write_bytes(b"journal disappearing during snapshot")
        original_stat = Path.stat
        disappeared = False

        def stat_with_journal_disappearance(path, *args, **kwargs):
            nonlocal disappeared
            metadata = original_stat(path, *args, **kwargs)
            if path == journal_path and not disappeared:
                disappeared = True
                journal_path.unlink()
            return metadata

        with patch.object(Path, "stat", stat_with_journal_disappearance):
            evidence = self.read()

        self.assertTrue(disappeared)
        self.assertEqual(evidence.status, "indeterminate")
        self.assertEqual(evidence.reason_code, "rollback_journal_active")

    def test_48_journal_variants_are_unavailable_without_inspection_or_path_leak(self):
        connection = self.create_schema()
        self.add_ready_food(connection)
        connection.close()
        journal_path = Path(f"{self.path}-journal")
        cases = {
            "empty": 0,
            "corrupt": b"not a rollback journal",
            "oversized": 256 * 1024 * 1024 + 1,
        }
        for name, content in cases.items():
            with self.subTest(name=name):
                if isinstance(content, int):
                    with journal_path.open("wb") as journal:
                        journal.truncate(content)
                else:
                    journal_path.write_bytes(content)
                before = self.snapshot()

                original_read_bytes = Path.read_bytes

                def reject_journal_read(path):
                    if path == journal_path:
                        raise AssertionError("rollback journal must not be read")
                    return original_read_bytes(path)

                with patch.object(Path, "read_bytes", reject_journal_read):
                    evidence = self.read()

                self.assertEqual(evidence.status, "indeterminate")
                self.assertEqual(evidence.reason_code, "rollback_journal_active")
                self.assertNotIn(str(self.path), repr(evidence))
                self.assertNotIn(str(journal_path), repr(evidence))
                self.assertEqual(self.snapshot(), before)
                journal_path.unlink()

    def test_49_shm_metadata_churn_retries_the_whole_snapshot(self):
        connection = self.create_schema()
        self.add_ready_food(connection)
        connection.close()
        shm_path = Path(f"{self.path}-shm")
        original_read_bytes = Path.read_bytes
        main_reads = 0

        def read_with_shm_appearance(path):
            nonlocal main_reads
            content = original_read_bytes(path)
            if path == self.path:
                main_reads += 1
                if main_reads == 1:
                    shm_path.write_bytes(b"stable metadata only")
            return content

        with patch.object(Path, "read_bytes", read_with_shm_appearance):
            evidence = self.read()

        self.assertEqual(main_reads, 2)
        self.assertEqual(evidence.status, "available")
        self.assertEqual(evidence.foods[0].profile_status, "ready")

    def test_publication_provenance_uses_immutable_history_payload(self):
        connection = self.create_schema()
        self.add_publication(connection)
        connection.execute(
            "UPDATE recipe_calculation SET per_serving_json=?,published_to_mealie=0 "
            "WHERE id='calc-1'",
            ('{"calories_kcal":999}',),
        )
        connection.execute(
            "INSERT INTO recipe_calculation VALUES (?,?,?,?,?)",
            ("later-recalculation", "recipe-id", '{"calories_kcal":777}', 0,
             (NOW + timedelta(minutes=1)).isoformat()),
        )
        connection.commit()
        connection.close()

        matching = self.read(
            recipe_id="recipe-id",
            nutrition={
                "calories": "100 kcal", "proteinContent": "5.0 g",
                "sodiumContent": "201 mg", "fiberContent": "",
            },
        )
        edited = self.read(
            recipe_id="recipe-id",
            nutrition={"calories": "101 kcal", "proteinContent": "5.0 g",
                       "sodiumContent": "201 mg"},
        )
        self.assertEqual(matching.publication_status, "owned")
        self.assertEqual(edited.publication_status, "mismatch")
        self.assertNotIn("100 kcal", repr(matching))

    def test_latest_publication_payload_wins_with_stable_tie_breaking(self):
        connection = self.create_schema()
        self.add_publication(
            connection, calculation_id="newer-time",
            published_payload={"nutrition": {"calories": "200 kcal"}},
            published_at=(NOW + timedelta(minutes=1)).isoformat(),
        )
        self.add_publication(
            connection, calculation_id="older-time-inserted-later",
            published_payload={"nutrition": {"calories": "100 kcal"}},
            published_at=NOW.isoformat(),
        )
        self.add_publication(
            connection, calculation_id="latest-id-at-newer-time",
            published_payload={"nutrition": {"calories": "201 kcal"}},
            published_at=(NOW + timedelta(minutes=1)).isoformat(),
        )
        connection.close()

        evidence = self.read(
            recipe_id="recipe-id", nutrition={"calories": "201 kcal"},
        )
        self.assertEqual(evidence.publication_status, "owned")

    def test_malformed_missing_and_oversized_history_payloads_are_unknown_and_private(self):
        cases = (
            ("malformed", "{private raw payload"),
            ("missing_nutrition", json.dumps({"settings": {"private": "secret"}})),
            ("oversized", json.dumps({"nutrition": {"calories": "1 kcal"},
                                       "private": "x" * 16_384})),
        )
        for name, payload in cases:
            with self.subTest(name=name):
                self.path = self.directory / f"{name}.db"
                connection = self.create_schema()
                self.add_publication(connection)
                connection.execute(
                    "UPDATE nutrition_publish_history SET published_payload_json=?",
                    (payload,),
                )
                connection.commit()
                connection.close()

                evidence = self.read(
                    recipe_id="recipe-id", nutrition={"calories": "100 kcal"},
                )

                self.assertEqual(evidence.status, "available")
                self.assertEqual(evidence.publication_status, "unknown")
                self.assertNotIn("private", repr(evidence))
                self.assertNotIn("secret", repr(evidence))

    def test_publisher_impossible_history_values_never_establish_ownership(self):
        for name, value in (
            ("object", {"value": "100 kcal"}),
            ("array", ["100 kcal"]),
            ("boolean", True),
            ("number", 100),
            ("control", "100\nkcal"),
            ("wrong_unit", "100 g"),
            ("nonfinite", "NaN kcal"),
        ):
            with self.subTest(name=name):
                self.path = self.directory / f"history-{name}.db"
                connection = self.create_schema()
                self.add_publication(
                    connection,
                    published_payload={"nutrition": {"calories": value}},
                )
                connection.close()

                evidence = self.read(
                    recipe_id="recipe-id", nutrition={"calories": value},
                )

                self.assertEqual(evidence.publication_status, "unknown")

    def test_malformed_current_nutrition_never_matches_valid_history(self):
        connection = self.create_schema()
        self.add_publication(connection)
        connection.close()

        for value in ({"value": "100 kcal"}, ["100 kcal"], True, 100,
                      "100\nkcal", "100 g", "NaN kcal"):
            with self.subTest(value=value):
                evidence = self.read(
                    recipe_id="recipe-id", nutrition={"calories": value},
                )

                self.assertNotEqual(evidence.publication_status, "owned")

    def test_old_publication_schema_is_available_with_unknown_provenance(self):
        for name, schema in (
            ("absent_table", "DROP TABLE nutrition_publish_history"),
            ("missing_columns", "DROP TABLE nutrition_publish_history; "
             "CREATE TABLE nutrition_publish_history(id INTEGER PRIMARY KEY)"),
        ):
            with self.subTest(name=name):
                self.path = self.directory / f"{name}.db"
                connection = self.create_schema()
                connection.executescript(schema)
                connection.commit()
                connection.close()

                evidence = self.read(
                    recipe_id="recipe-id", nutrition={"calories": "100 kcal"},
                )

                self.assertEqual(evidence.status, "available")
                self.assertEqual(evidence.publication_status, "unknown")

    def test_publication_provenance_requires_history_not_published_flag_alone(self):
        connection = self.create_schema()
        connection.execute(
            "INSERT INTO recipe_calculation VALUES (?,?,?,?,?)",
            ("calc-flag-only", "recipe-id", '{"calories_kcal":100}', 1,
             NOW.isoformat()),
        )
        connection.commit()
        connection.close()
        evidence = self.read(recipe_id="recipe-id", nutrition={"calories": "100 kcal"})
        self.assertEqual(evidence.publication_status, "unknown")

    def test_low_confidence_resolution_and_conversion_are_valid_found_evidence(self):
        connection = self.create_schema()
        self.add_ready_food(
            connection,
            resolution={"resolution_status": "manual", "confidence": .75,
                        "manual_override": 1, "provider": None,
                        "provider_food_id": None},
            profile={"provider": "manual", "provider_food_id": None,
                     "manual_override": 1, "confidence": .9},
        )
        connection.execute(
            "INSERT INTO food_conversion VALUES (?,?,?,?,?,?,?,?)",
            ("food", "culinary_measure", "teaspoon", 3, .75, "manual", 1,
             NOW.isoformat()),
        )
        connection.commit()
        connection.close()
        food = self.read(FoodEvidenceRequest(
            "food", (("culinary_measure", "teaspoon"),)
        )).foods[0]
        self.assertEqual(food.resolution_status, "low_confidence")
        self.assertEqual(food.conversion_status, "low_confidence")
        self.assertEqual(food.profile_status, "ready")


if __name__ == "__main__":
    unittest.main()
