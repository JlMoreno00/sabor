import hashlib
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from tests.support import install_runtime_dependency_stubs

install_runtime_dependency_stubs()

import sabor_mcp


FOOD_ID = "11111111-1111-4111-8111-111111111111"


class NutritionToolTests(unittest.TestCase):
    @staticmethod
    def _directory_snapshot(directory):
        return {
            path.name: (hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns)
            for path in directory.iterdir() if path.is_file()
        }

    @staticmethod
    def _manual_profile_preview():
        return sabor_mcp.nutrition_set_food_profile(
            FOOD_ID, calories_kcal=40, protein_g=5, confidence=.9,
            source_note="Reviewed package label", source_version="label-v1",
        )

    def test_status_is_disabled_by_default_and_does_not_initialize_db(self):
        with patch.object(sabor_mcp, "SABOR_NUTRITION_ENABLED", False), \
             patch.object(sabor_mcp, "_nutrition_service") as service:
            result = sabor_mcp.nutrition_status()
        self.assertFalse(result["enabled"])
        service.assert_not_called()

    def test_mutation_tools_require_explicit_flags(self):
        with patch.object(sabor_mcp, "SABOR_NUTRITION_ENABLED", True), \
             patch.object(sabor_mcp, "_nutrition_service") as factory:
            factory.return_value.publish.return_value = {"ok": True}
            result = sabor_mcp.nutrition_publish_recipe("recipe", publish=False)
        self.assertEqual(result["code"], "nutrition_mutation_not_requested")
        factory.return_value.publish.assert_not_called()

    def test_import_nutrition_failure_remains_non_fatal(self):
        imported = {"ok": True}
        with patch.object(sabor_mcp, "SABOR_NUTRITION_ENABLED", True), \
             patch.object(sabor_mcp, "SABOR_NUTRITION_AUTO_ON_IMPORT", True), \
             patch.object(sabor_mcp, "_nutrition_calculate_slug", side_effect=RuntimeError("boom")):
            result = sabor_mcp._nutrition_after_import(imported, "recipe")
        self.assertTrue(result["ok"])
        self.assertIn("nutrition_warning", result)

    def test_manual_mapping_requires_valid_confidence_and_provenance_for_apply(self):
        with patch.object(sabor_mcp, "SABOR_NUTRITION_ENABLED", True), \
             patch.object(sabor_mcp, "_nutrition_service") as factory:
            factory.return_value.db.is_mapping_target_valid.return_value = True
            missing = sabor_mcp.nutrition_set_food_mapping("f", "Cebolleta", "spring onion", apply=True)
            invalid = sabor_mcp.nutrition_set_food_mapping(
                "f", "Cebolleta", "spring onion", confidence=1.1,
                source_note="Reviewed", apply=True,
            )
            saved = sabor_mcp.nutrition_set_food_mapping(
                "f", "Cebolleta", "spring onion", confidence=.82,
                source_note="Reviewed source", provider_food_id=" 11291 ", apply=True,
            )
        self.assertFalse(missing["ok"])
        self.assertFalse(invalid["ok"])
        self.assertTrue(saved["ok"])
        args = factory.return_value.db.upsert_resolution.call_args.args
        self.assertEqual(args[4], .82)
        self.assertEqual(factory.return_value.db.upsert_resolution.call_args.kwargs["source_note"], "Reviewed source")
        self.assertEqual(factory.return_value.db.upsert_resolution.call_args.kwargs["provider_food_id"], "11291")

    def test_manual_mapping_preview_reports_target_validity_without_mutation(self):
        with patch.object(sabor_mcp, "SABOR_NUTRITION_ENABLED", True), \
             patch.object(sabor_mcp, "_nutrition_service") as factory, \
             patch("nutrition.db.NutritionDB.mapping_target_valid_at", return_value=False), \
             patch.object(sabor_mcp.requests, "get") as get:
            factory.return_value.db.is_mapping_target_valid.return_value = False
            preview = sabor_mcp.nutrition_set_food_mapping(
                "orphan", "Orphan", "orphan", confidence=1,
                source_note="Reviewed", apply=False,
            )
            rejected = sabor_mcp.nutrition_set_food_mapping(
                "orphan", "Orphan", "orphan", confidence=1,
                source_note="Reviewed", apply=True,
            )

        self.assertTrue(preview["ok"])
        self.assertFalse(preview["details"]["targetValid"])
        self.assertEqual(rejected["code"], "nutrition_mapping_target_unknown")
        factory.return_value.db.upsert_resolution.assert_not_called()
        get.assert_not_called()

    def test_manual_mapping_preview_does_not_initialize_service(self):
        with patch.object(sabor_mcp, "SABOR_NUTRITION_ENABLED", False), \
             patch.object(sabor_mcp, "_nutrition_service") as factory, \
             patch("nutrition.db.NutritionDB.mapping_target_valid_at", return_value=True):
            preview = sabor_mcp.nutrition_set_food_mapping(
                "known", "Known", "known", confidence=1,
                source_note="Reviewed", apply=False,
            )
        self.assertTrue(preview["details"]["targetValid"])
        factory.assert_not_called()

    def test_manual_mapping_existing_sidecar_target_stays_offline(self):
        with patch("nutrition.db.NutritionDB.mapping_target_valid_at", return_value=True), \
             patch.object(sabor_mcp.requests, "get") as get:
            preview = sabor_mcp.nutrition_set_food_mapping(
                "sidecar-only", "Known", "known", confidence=1,
                source_note="Reviewed", apply=False,
            )

        self.assertTrue(preview["details"]["targetValid"])
        self.assertEqual(preview["details"]["target"]["source"], "sidecar")
        get.assert_not_called()

    def test_manual_mapping_preview_validates_exact_authoritative_mealie_food_without_write(self):
        response = Mock(status_code=200)
        response.json.return_value = {"id": FOOD_ID, "name": "Champiñón"}
        with patch("nutrition.db.NutritionDB.mapping_target_valid_at", return_value=False), \
             patch.object(sabor_mcp, "MEALIE_BASE_URL", "http://mealie"), \
             patch.object(sabor_mcp, "MEALIE_API_TOKEN", "test-token"), \
             patch.object(sabor_mcp.requests, "get", return_value=response) as get, \
             patch.object(sabor_mcp, "_nutrition_service") as factory:
            preview = sabor_mcp.nutrition_set_food_mapping(
                f" {FOOD_ID} ", "Champiñón", "mushroom", confidence=.9,
                source_note="Reviewed", apply=False,
            )

        self.assertTrue(preview["ok"])
        self.assertTrue(preview["details"]["targetValid"])
        self.assertEqual(preview["details"]["target"], {
            "id": FOOD_ID, "name": "Champiñón", "source": "mealie",
        })
        get.assert_called_once_with(
            f"http://mealie/api/foods/{FOOD_ID}",
            headers={"Authorization": "Bearer test-token", "Content-Type": "application/json"},
            timeout=30,
        )
        factory.assert_not_called()

    def test_manual_mapping_authoritative_failure_status_matrix_is_safe(self):
        cases = (
            (408, "nutrition_mapping_target_unavailable", True, None),
            (429, "nutrition_mapping_target_unavailable", True, 3600),
            (400, "nutrition_mapping_target_unavailable", False, None),
            (401, "nutrition_mapping_target_unavailable", False, None),
            (403, "nutrition_mapping_target_unavailable", False, None),
            (404, "nutrition_mapping_target_not_found", False, None),
            (500, "nutrition_mapping_target_unavailable", True, None),
            (503, "nutrition_mapping_target_unavailable", True, None),
        )
        expected_details = {
            "mealieFoodId": FOOD_ID,
            "mealieFoodName": "Food",
            "canonicalTerm": "food",
            "confidence": 1,
            "sourceNote": "Reviewed",
            "providerFoodId": None,
            "targetValid": False,
        }
        for status, code, retryable, retry_after in cases:
            response = Mock(
                status_code=status,
                headers={"Retry-After": "999999", "X-Secret": "header-secret"},
                text="response-body-secret",
            )
            with self.subTest(status=status), \
                 patch("nutrition.db.NutritionDB.mapping_target_valid_at", return_value=False), \
                 patch.object(sabor_mcp, "MEALIE_API_TOKEN", "private-token"), \
                 patch.object(sabor_mcp.requests, "get", return_value=response):
                result = sabor_mcp.nutrition_set_food_mapping(
                    FOOD_ID, "Food", "food", confidence=1,
                    source_note="Reviewed", apply=False,
                )

            self.assertEqual(result["code"], code)
            self.assertEqual(result["retryable"], retryable)
            details = dict(expected_details)
            if retry_after is not None:
                details["retryAfter"] = retry_after
            self.assertEqual(result["details"], details)
            envelope = repr(result)
            self.assertNotIn("response-body-secret", envelope)
            self.assertNotIn("header-secret", envelope)
            self.assertNotIn("private-token", envelope)

        with patch("nutrition.db.NutritionDB.mapping_target_valid_at", return_value=False), \
             patch.object(sabor_mcp, "MEALIE_API_TOKEN", "private-token"), \
             patch.object(
                 sabor_mcp.requests, "get",
                 side_effect=sabor_mcp.requests.exceptions.Timeout("timeout-secret"),
             ):
            result = sabor_mcp.nutrition_set_food_mapping(
                FOOD_ID, "Food", "food", confidence=1,
                source_note="Reviewed", apply=False,
            )

        self.assertEqual(result["code"], "nutrition_mapping_target_unavailable")
        self.assertTrue(result["retryable"])
        self.assertEqual(result["details"], expected_details)
        self.assertNotIn("timeout-secret", repr(result))

    def test_manual_mapping_retry_after_is_bounded_ascii_decimal_seconds(self):
        cases = (
            ("0", 0),
            ("60", 60),
            ("3600", 3600),
            ("3601", 3600),
            ("9" * 5000, None),
            ("１２", None),
            ("+60", None),
            ("-60", None),
            (" 60", None),
            ("60 ", None),
            ("Wed, 21 Oct 2015 07:28:00 GMT", None),
            ("", None),
        )
        for retry_after, expected in cases:
            response = Mock(status_code=429, headers={"Retry-After": retry_after})
            with self.subTest(retry_after=retry_after), \
                 patch("nutrition.db.NutritionDB.mapping_target_valid_at", return_value=False), \
                 patch.object(sabor_mcp, "MEALIE_API_TOKEN", "test-token"), \
                 patch.object(sabor_mcp.requests, "get", return_value=response):
                result = sabor_mcp.nutrition_set_food_mapping(
                    FOOD_ID, "Food", "food", confidence=1,
                    source_note="Reviewed", apply=False,
                )

            if expected is None:
                self.assertNotIn("retryAfter", result["details"])
            else:
                self.assertEqual(result["details"]["retryAfter"], expected)

    def test_manual_mapping_authoritative_not_found_is_stable(self):
        response = Mock(status_code=404)
        with patch("nutrition.db.NutritionDB.mapping_target_valid_at", return_value=False), \
             patch.object(sabor_mcp, "MEALIE_API_TOKEN", "test-token"), \
             patch.object(sabor_mcp.requests, "get", return_value=response):
            result = sabor_mcp.nutrition_set_food_mapping(
                FOOD_ID, "Missing", "missing", confidence=1,
                source_note="Reviewed", apply=False,
            )

        self.assertEqual(result["code"], "nutrition_mapping_target_not_found")
        self.assertFalse(result["retryable"])
        self.assertFalse(result["details"]["targetValid"])

    def test_manual_mapping_authoritative_failures_are_retryable_and_secret_safe(self):
        secret = "response-body-secret"
        response = Mock(status_code=503, text=secret)
        failures = (response, sabor_mcp.requests.exceptions.Timeout(secret))
        for failure in failures:
            with self.subTest(failure=type(failure).__name__), \
                 patch("nutrition.db.NutritionDB.mapping_target_valid_at", return_value=False), \
                 patch.object(sabor_mcp, "MEALIE_API_TOKEN", "private-token"), \
                 patch.object(sabor_mcp.requests, "get") as get:
                if isinstance(failure, Exception):
                    get.side_effect = failure
                else:
                    get.return_value = failure
                result = sabor_mcp.nutrition_set_food_mapping(
                    FOOD_ID, "Food", "food", confidence=1,
                    source_note="Reviewed", apply=False,
                )

            self.assertEqual(result["code"], "nutrition_mapping_target_unavailable")
            self.assertTrue(result["retryable"])
            envelope = repr(result)
            self.assertNotIn(secret, envelope)
            self.assertNotIn("private-token", envelope)

    def test_manual_mapping_rejects_authoritative_id_and_apply_name_mismatch(self):
        wrong_id = "22222222-2222-4222-8222-222222222222"
        id_response = Mock(status_code=200)
        id_response.json.return_value = {"id": wrong_id, "name": "Food"}
        name_response = Mock(status_code=200)
        name_response.json.return_value = {"id": FOOD_ID, "name": "Authoritative"}
        with patch.object(sabor_mcp, "SABOR_NUTRITION_ENABLED", True), \
             patch.object(sabor_mcp, "MEALIE_API_TOKEN", "test-token"), \
             patch.object(sabor_mcp, "_nutrition_service") as factory:
            factory.return_value.db.is_mapping_target_valid.return_value = False
            with patch.object(sabor_mcp.requests, "get", return_value=id_response):
                id_mismatch = sabor_mcp.nutrition_set_food_mapping(
                    FOOD_ID, "Food", "food", confidence=1,
                    source_note="Reviewed", apply=True,
                )
            with patch.object(sabor_mcp.requests, "get", return_value=name_response):
                name_mismatch = sabor_mcp.nutrition_set_food_mapping(
                    FOOD_ID, "Caller name", "food", confidence=1,
                    source_note="Reviewed", apply=True,
                )

        self.assertEqual(id_mismatch["code"], "nutrition_mapping_target_mismatch")
        self.assertEqual(name_mismatch["code"], "nutrition_mapping_target_mismatch")
        factory.return_value.db.upsert_resolution.assert_not_called()

    def test_manual_mapping_apply_revalidates_and_writes_authoritative_name(self):
        response = Mock(status_code=200)
        response.json.return_value = {"id": FOOD_ID, "name": "Authoritative"}
        with patch.object(sabor_mcp, "SABOR_NUTRITION_ENABLED", True), \
             patch.object(sabor_mcp, "MEALIE_API_TOKEN", "test-token"), \
             patch.object(sabor_mcp, "_nutrition_service") as factory, \
             patch.object(sabor_mcp.requests, "get", return_value=response) as get:
            factory.return_value.db.is_mapping_target_valid.return_value = False
            factory.return_value.db.upsert_resolution.return_value = True
            saved = sabor_mcp.nutrition_set_food_mapping(
                FOOD_ID, "Authoritative", "food", confidence=.8,
                source_note="Reviewed provider binding", provider_food_id="123", apply=True,
            )

        self.assertTrue(saved["ok"])
        self.assertEqual(saved["details"]["target"]["source"], "mealie")
        self.assertEqual(factory.return_value.db.upsert_resolution.call_args.args[1], "Authoritative")
        self.assertTrue(factory.return_value.db.upsert_resolution.call_args.kwargs["target_validated"])
        self.assertNotIn("params", get.call_args.kwargs)

    def test_manual_mapping_apply_rejects_food_deleted_after_preview(self):
        found = Mock(status_code=200)
        found.json.return_value = {"id": FOOD_ID, "name": "Food"}
        missing = Mock(status_code=404)
        with patch.object(sabor_mcp, "SABOR_NUTRITION_ENABLED", True), \
             patch.object(sabor_mcp, "MEALIE_API_TOKEN", "test-token"), \
             patch("nutrition.db.NutritionDB.mapping_target_valid_at", return_value=False), \
             patch.object(sabor_mcp, "_nutrition_service") as factory, \
             patch.object(sabor_mcp.requests, "get", side_effect=(found, missing)):
            factory.return_value.db.is_mapping_target_valid.return_value = False
            preview = sabor_mcp.nutrition_set_food_mapping(
                FOOD_ID, "Food", "food", confidence=1,
                source_note="Reviewed", apply=False,
            )
            applied = sabor_mcp.nutrition_set_food_mapping(
                FOOD_ID, "Food", "food", confidence=1,
                source_note="Reviewed", apply=True,
            )

        self.assertTrue(preview["details"]["targetValid"])
        self.assertEqual(applied["code"], "nutrition_mapping_target_not_found")
        factory.return_value.db.upsert_resolution.assert_not_called()

    def test_manual_mapping_rejects_malformed_provider_food_id(self):
        with patch.object(sabor_mcp, "SABOR_NUTRITION_ENABLED", True), \
             patch.object(sabor_mcp, "_nutrition_service") as factory:
            malformed = sabor_mcp.nutrition_set_food_mapping(
                "f", "Food", "food", confidence=1, source_note="Reviewed",
                provider_food_id="12/34", apply=True,
            )
        self.assertEqual(malformed["code"], "nutrition_manual_evidence_invalid")
        factory.return_value.db.upsert_resolution.assert_not_called()

    def test_manual_mapping_canonicalizes_mealie_food_id_before_lookup_and_write(self):
        with patch.object(sabor_mcp, "SABOR_NUTRITION_ENABLED", True), \
             patch.object(sabor_mcp, "_nutrition_service") as factory:
            factory.return_value.db.is_mapping_target_valid.return_value = True
            saved = sabor_mcp.nutrition_set_food_mapping(
                " food ", "Food", "food", confidence=1,
                source_note="Reviewed", apply=True,
            )
            empty = sabor_mcp.nutrition_set_food_mapping(
                "   ", "Food", "food", confidence=1,
                source_note="Reviewed", apply=True,
            )

        self.assertTrue(saved["ok"])
        factory.return_value.db.is_mapping_target_valid.assert_called_once_with("food")
        self.assertEqual(factory.return_value.db.upsert_resolution.call_args.args[0], "food")
        self.assertEqual(empty["code"], "nutrition_manual_evidence_invalid")

    def test_conversion_preview_and_apply_require_honest_evidence(self):
        with patch.object(sabor_mcp, "SABOR_NUTRITION_ENABLED", True), \
             patch.object(sabor_mcp, "_nutrition_service") as factory:
            factory.return_value.db.is_mapping_target_valid.return_value = True
            preview = sabor_mcp.nutrition_set_conversion_override(
                "f", "piece_weight", "uds", 55, confidence=.8,
                source_note="Measured sample", apply=False,
            )
            missing = sabor_mcp.nutrition_set_conversion_override(
                "f", "piece_weight", "piece", 55, apply=True,
            )
            saved = sabor_mcp.nutrition_set_conversion_override(
                "f", "piece_weight", "uds", 55, confidence=.8,
                source_note="Measured sample", apply=True,
            )
        self.assertTrue(preview["ok"])
        self.assertEqual(preview["details"]["unitKey"], "piece")
        self.assertFalse(missing["ok"])
        self.assertTrue(saved["ok"])
        args = factory.return_value.db.upsert_conversion.call_args.args
        self.assertEqual(args[2:6], ("piece", 55, .8, "manual"))

    def test_manual_tools_reject_non_finite_numbers_before_persistence(self):
        with patch.object(sabor_mcp, "SABOR_NUTRITION_ENABLED", True), \
             patch.object(sabor_mcp, "_nutrition_service") as factory:
            for confidence in (float("nan"), float("inf"), float("-inf")):
                mapping = sabor_mcp.nutrition_set_food_mapping(
                    "f", "Food", "food", confidence=confidence,
                    source_note="Reviewed", apply=True,
                )
                self.assertEqual(mapping["code"], "nutrition_manual_evidence_invalid")
            for grams_per_unit, confidence in (
                (float("nan"), .8), (float("inf"), .8), (50, float("nan")), (50, float("inf")),
            ):
                conversion = sabor_mcp.nutrition_set_conversion_override(
                    "f", "piece_weight", "piece", grams_per_unit,
                    confidence=confidence, source_note="Measured", apply=True,
                )
                self.assertEqual(conversion["code"], "nutrition_manual_evidence_invalid")
        factory.return_value.db.upsert_resolution.assert_not_called()
        factory.return_value.db.upsert_conversion.assert_not_called()

    def test_runtime_wires_explicit_spanish_source_language_policy(self):
        self.assertEqual(sabor_mcp.SABOR_NUTRITION_SOURCE_LANGUAGE, "es")

    def test_manual_profile_preview_is_normalized_and_performs_no_write(self):
        with patch("nutrition.db.NutritionDB.profile_preview_at", return_value=({
                 "id": FOOD_ID, "name": "Dashi", "source": "sidecar",
             }, None)), \
             patch.object(sabor_mcp.requests, "get") as get:
            result = sabor_mcp.nutrition_set_food_profile(
                FOOD_ID, calories_kcal=40, protein_g=5, sodium_mg=None,
                confidence=.9, source_note="Reviewed package label",
                source_version="label-v1",
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["code"], "nutrition_profile_preview")
        self.assertEqual(result["details"]["profile"]["basisGrams"], 100.0)
        self.assertEqual(result["details"]["profile"]["provider"], "manual")
        self.assertEqual(result["details"]["target"]["name"], "Dashi")
        self.assertEqual(result["details"]["overwriteImpact"], "create")
        self.assertEqual(result["details"]["precedence"], "manual_over_provider")
        self.assertEqual(len(result["details"]["payloadHash"]), 64)
        get.assert_not_called()

    def test_manual_profile_preview_absent_db_leaves_directory_unchanged(self):
        with tempfile.TemporaryDirectory() as tempdir:
            directory = Path(tempdir)
            path = directory / "nutrition.db"
            response = Mock(status_code=200)
            response.json.return_value = {"id": FOOD_ID, "name": "Dashi"}
            before = self._directory_snapshot(directory)

            with patch.object(sabor_mcp, "SABOR_NUTRITION_DB", path), \
                 patch.object(sabor_mcp, "_NUTRITION_SERVICE_INSTANCE", None), \
                 patch.object(sabor_mcp, "MEALIE_BASE_URL", "http://mealie"), \
                 patch.object(sabor_mcp, "MEALIE_API_TOKEN", "test-token"), \
                 patch.object(sabor_mcp.requests, "get", return_value=response):
                result = self._manual_profile_preview()

            self.assertEqual(self._directory_snapshot(directory), before)
            self.assertTrue(result["details"]["targetValid"])
            self.assertEqual(result["details"]["target"]["source"], "mealie")
            self.assertNotIn("priorEvidence", result["details"])

    def test_manual_profile_preview_reads_rollback_db_without_filesystem_changes(self):
        from nutrition.db import NutritionDB

        with tempfile.TemporaryDirectory() as tempdir:
            directory = Path(tempdir)
            path = directory / "nutrition.db"
            with tempfile.TemporaryDirectory() as seed_dir:
                seed_path = Path(seed_dir) / "seed.db"
                db = NutritionDB(seed_path)
                db.upsert_resolution(
                    FOOD_ID, "Dashi", None, "blocked", 0, "untranslated",
                )
                db.upsert_profile(
                    FOOD_ID, {"calories_kcal": 30, "protein_g": 4}, "manual", .8,
                    "r" * 64, manual_override=True, source_version="rollback-v1",
                )
                with sqlite3.connect(seed_path) as source, sqlite3.connect(path) as target:
                    source.backup(target)
            before = self._directory_snapshot(directory)

            with patch.object(sabor_mcp, "SABOR_NUTRITION_DB", path), \
                 patch.object(sabor_mcp, "_NUTRITION_SERVICE_INSTANCE", None), \
                 patch.object(sabor_mcp.requests, "get") as get:
                result = self._manual_profile_preview()

            self.assertEqual(self._directory_snapshot(directory), before)
            self.assertEqual(result["details"]["target"]["name"], "Dashi")
            self.assertEqual(result["details"]["priorEvidence"], {
                "payloadHash": "r" * 64, "sourceVersion": "rollback-v1",
            })
            get.assert_not_called()

    def test_manual_profile_preview_reads_latest_wal_without_filesystem_changes(self):
        from nutrition.db import NutritionDB

        with tempfile.TemporaryDirectory() as tempdir:
            directory = Path(tempdir)
            path = directory / "nutrition.db"
            db = NutritionDB(path)
            db.upsert_resolution(FOOD_ID, "Stale name", None, "blocked", 0, "untranslated")
            db.upsert_profile(
                FOOD_ID, {"calories_kcal": 20, "protein_g": 2}, "manual", .7,
                "s" * 64, manual_override=True, source_version="stale-v1",
            )
            keeper = sqlite3.connect(path)
            self.addCleanup(keeper.close)
            keeper.execute("PRAGMA wal_autocheckpoint=0")
            keeper.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            keeper.execute(
                "UPDATE food_resolution SET mealie_food_name='Latest Dashi' WHERE mealie_food_id=?",
                (FOOD_ID,),
            )
            keeper.execute(
                "UPDATE nutrient_profile SET payload_hash=?,source_version=? WHERE mealie_food_id=?",
                ("w" * 64, "wal-v2", FOOD_ID),
            )
            keeper.commit()
            before = self._directory_snapshot(directory)

            with patch.object(sabor_mcp, "SABOR_NUTRITION_DB", path), \
                 patch.object(sabor_mcp, "_NUTRITION_SERVICE_INSTANCE", None), \
                 patch.object(sabor_mcp.requests, "get") as get:
                result = self._manual_profile_preview()

            self.assertEqual(self._directory_snapshot(directory), before)
            self.assertEqual(result["details"]["target"]["name"], "Latest Dashi")
            self.assertEqual(result["details"]["priorEvidence"], {
                "payloadHash": "w" * 64, "sourceVersion": "wal-v2",
            })
            get.assert_not_called()

    def test_manual_profile_apply_revalidates_and_returns_prior_evidence(self):
        db = Mock()
        db.is_mapping_target_valid.return_value = True
        db.get_resolution.return_value = {"mealie_food_name": "Dashi"}
        db.get_profile.return_value = {
            "provider": "manual", "manual_override": 1,
            "payload_hash": "prior-hash", "source_version": "label-v1",
        }
        db.set_manual_profile.return_value = {
            "payload_hash": "prior-hash", "source_version": "label-v1",
        }
        with patch.object(sabor_mcp, "SABOR_NUTRITION_ENABLED", True), \
             patch.object(sabor_mcp, "_nutrition_service", return_value=Mock(db=db)):
            result = sabor_mcp.nutrition_set_food_profile(
                FOOD_ID, calories_kcal=42, protein_g=5.2, confidence=.95,
                source_note="Second review", source_version="label-v2", apply=True,
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["code"], "nutrition_profile_saved")
        self.assertEqual(result["details"]["priorEvidence"], {
            "payloadHash": "prior-hash", "sourceVersion": "label-v1",
        })
        db.is_mapping_target_valid.assert_called_once_with(FOOD_ID)
        db.set_manual_profile.assert_called_once()

    def test_manual_profile_validation_matrix_is_bounded_and_secret_safe(self):
        valid = dict(
            mealie_food_id=FOOD_ID, calories_kcal=10, protein_g=1,
            confidence=.8, source_note="Reviewed", source_version="reference-v1",
        )
        cases = (
            {"mealie_food_id": "not-a-uuid"},
            {"basis_grams": 0}, {"basis_grams": 99}, {"basis_grams": True},
            {"calories_kcal": None}, {"calories_kcal": -1},
            {"calories_kcal": float("nan")}, {"calories_kcal": float("inf")},
            {"protein_g": None}, {"protein_g": True}, {"sodium_mg": -1},
            {"confidence": -1}, {"confidence": 1.1}, {"confidence": True},
            {"source_note": ""}, {"source_note": "x" * 1001},
            {"source_version": ""}, {"source_version": "x" * 257},
        )
        with patch.object(sabor_mcp, "_nutrition_service") as service, \
             patch.object(sabor_mcp.requests, "get") as get:
            for changes in cases:
                with self.subTest(changes=changes):
                    result = sabor_mcp.nutrition_set_food_profile(**{**valid, **changes})
                    self.assertEqual(result["code"], "nutrition_manual_evidence_invalid")
                    self.assertLess(len(repr(result)), 3000)
        service.assert_not_called()
        get.assert_not_called()

    def test_manual_profile_requires_calories_and_a_macro_and_rejects_unknown_fields(self):
        missing_macro = sabor_mcp.nutrition_set_food_profile(
            FOOD_ID, calories_kcal=10, confidence=.8, source_note="Reviewed",
            source_version="reference-v1",
        )
        self.assertEqual(missing_macro["code"], "nutrition_manual_evidence_invalid")
        with self.assertRaises(TypeError):
            sabor_mcp.nutrition_set_food_profile(
                FOOD_ID, calories_kcal=10, protein_g=1, confidence=.8,
                source_note="Reviewed", source_version="reference-v1", vitamin_c_mg=3,
            )


if __name__ == "__main__":
    unittest.main()
