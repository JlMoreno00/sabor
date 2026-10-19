import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock

from nutrition.db import NutritionDB
from nutrition.models import SPOONACULAR_PROFILE_CONFIDENCE
from nutrition.resolver import FoodResolver, normalize_term


class NutritionResolverTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.db = NutritionDB(Path(self.tempdir.name) / "n.db")
        self.provider = Mock()
        self.resolver = FoodResolver(self.db, self.provider)

    def test_normalization_and_spanish_alias(self):
        self.assertEqual(normalize_term("  TOMÁTE  "), "tomate")
        self.provider.fetch_profile.return_value = {
            "ok": True, "provider_food_id": "11529", "name": "tomato",
            "nutrients": {"calories_kcal": 18}, "payload_hash": "hash",
        }
        result = self.resolver.resolve({"id": "f1", "name": "Tomáte"})
        self.assertEqual(result.canonical_term, "tomato")
        self.assertEqual(result.method, "curated_alias")
        self.assertEqual(result.confidence, .98)

    def test_manual_override_and_fresh_cache_avoid_provider(self):
        self.db.upsert_resolution("f1", "Tomate", None, "blocked", 0, "untranslated")
        self.db.upsert_resolution("f1", "Tomate", "tomato", "manual", 1, "manual", True)
        self.db.upsert_profile("f1", {"calories_kcal": 18}, "manual", 1, "x", manual_override=True)
        result = self.resolver.resolve({"id": "f1", "name": "Changed"})
        self.assertEqual(result.method, "manual")
        self.provider.fetch_profile.assert_not_called()

        forced = self.resolver.resolve({"id": "f1", "name": "Changed"}, force=True)
        self.assertEqual(forced.method, "manual")
        self.provider.fetch_profile.assert_not_called()
        self.provider.fetch_profile_by_id.assert_not_called()

    def test_manual_mapping_fetches_missing_profile_without_overwriting_mapping(self):
        self.db.upsert_resolution("f1", "Cebolleta", None, "blocked", 0, "untranslated")
        self.db.upsert_resolution("f1", "Cebolleta", "spring onion", "manual", 1, "manual", True)
        self.provider.fetch_profile.return_value = {
            "ok": True, "provider_food_id": "11291", "name": "spring onion",
            "nutrients": {"calories_kcal": 32}, "payload_hash": "profile",
        }

        result = self.resolver.resolve({"id": "f1", "name": "Cebolleta"})

        self.assertEqual(result.method, "manual")
        self.provider.fetch_profile.assert_called_once_with("spring onion", force=False)
        self.assertEqual(self.db.get_profile("f1")["calories_kcal"], 32)
        self.assertTrue(self.db.get_resolution("f1")["manual_override"])

    def test_provider_bound_manual_mapping_fetches_by_id_without_text_match(self):
        self.db.upsert_resolution("f1", "Cebolleta", None, "blocked", 0, "untranslated")
        self.db.upsert_resolution(
            "f1", "Cebolleta", "spring onion", "manual", .9, "manual", True,
            provider="spoonacular", provider_food_id="11291", source_note="Reviewed",
        )
        self.provider.fetch_profile_by_id.return_value = {
            "ok": True, "provider_food_id": "11291", "name": "scallion",
            "nutrients": {"calories_kcal": 32}, "payload_hash": "profile",
        }

        result = self.resolver.resolve({"id": "f1", "name": "Cebolleta"})

        self.assertEqual(result.method, "manual")
        self.provider.fetch_profile_by_id.assert_called_once_with("11291", force=False)
        self.provider.fetch_profile.assert_not_called()
        self.assertEqual(self.db.get_profile("f1")["provider_food_id"], "11291")
        self.assertEqual(
            self.db.get_profile("f1")["confidence"], SPOONACULAR_PROFILE_CONFIDENCE,
        )

    def test_spoonacular_profile_quality_is_independent_of_resolution_confidence(self):
        self.provider.fetch_profile.return_value = {
            "ok": True, "provider_food_id": "11529", "name": "tomato",
            "nutrients": {"calories_kcal": 18}, "payload_hash": "profile",
        }

        resolution = self.resolver.resolve({"id": "f1", "name": "Tomate"})
        profile = self.db.get_profile("f1")

        self.assertEqual(resolution.confidence, .98)
        self.assertEqual(profile["confidence"], SPOONACULAR_PROFILE_CONFIDENCE)

    def test_changed_provider_binding_bypasses_fresh_old_profile(self):
        self.db.upsert_resolution("f1", "Cebolleta", None, "blocked", 0, "untranslated")
        self.db.upsert_resolution(
            "f1", "Cebolleta", "spring onion", "manual", .9, "manual", True,
            provider="spoonacular", provider_food_id="old", source_note="First review",
        )
        self.db.upsert_profile(
            "f1", {"calories_kcal": 10}, "spoonacular", .9, "old-profile",
            provider_food_id="old", stale_after="2099-01-01T00:00:00+00:00",
        )
        self.db.upsert_resolution(
            "f1", "Cebolleta", "spring onion", "manual", .9, "manual", True,
            provider="spoonacular", provider_food_id="new", source_note="Corrected review",
        )
        self.provider.fetch_profile_by_id.return_value = {
            "ok": True, "provider_food_id": "new", "name": "scallion",
            "nutrients": {"calories_kcal": 32}, "payload_hash": "new-profile",
        }

        self.resolver.resolve({"id": " f1 ", "name": "Cebolleta"})

        self.provider.fetch_profile_by_id.assert_called_once_with("new", force=False)
        profile = self.db.get_profile("f1")
        self.assertEqual(profile["provider_food_id"], "new")
        self.assertEqual(profile["calories_kcal"], 32)

    def test_translation_ambiguity_not_found_and_staleness(self):
        translator = Mock(return_value="spring onion")
        resolver = FoodResolver(self.db, self.provider, translator=translator)
        self.provider.fetch_profile.return_value = {
            "ok": False, "code": "nutrition_food_unresolved",
            "candidates": ["scallion", "onion"],
        }
        result = resolver.resolve({"id": "f2", "name": "cebolleta"})
        self.assertEqual(result.status, "ambiguous")
        self.provider.fetch_profile.return_value = {
            "ok": False, "code": "nutrition_provider_not_found"
        }
        result = resolver.resolve({"id": "f3", "name": "desconocido"})
        self.assertEqual(result.status, "not_found")
        stale = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        self.db.upsert_resolution("f4", "Tomate", "tomato", "resolved", .9, "translated", stale_after=stale)
        self.provider.fetch_profile.return_value = {
            "ok": True, "provider_food_id": "1", "name": "tomato",
            "nutrients": {"calories_kcal": 18}, "payload_hash": "new",
        }
        resolver.resolve({"id": "f4", "name": "Tomate"})
        self.provider.fetch_profile.assert_called()

    def test_rejects_provider_success_for_an_unrelated_name(self):
        self.provider.fetch_profile.return_value = {
            "ok": True, "provider_food_id": "9", "name": "tomato sauce",
            "nutrients": {"calories_kcal": 80}, "payload_hash": "wrong",
        }

        result = self.resolver.resolve({"id": "f1", "name": "Tomate"})

        self.assertEqual(result.status, "ambiguous")
        self.assertEqual(result.confidence, 0)
        self.assertIsNone(self.db.get_profile("f1"))

    def test_retryable_provider_failure_does_not_make_resolution_fresh(self):
        current = datetime(2026, 8, 11, tzinfo=timezone.utc)
        clock = Mock(return_value=current)
        resolver = FoodResolver(self.db, self.provider, now=clock)
        self.provider.fetch_profile.return_value = {
            "ok": False, "code": "nutrition_provider_rate_limited",
            "retryable": True, "retry_after": 300,
            "cooldown_until": (current + timedelta(seconds=300)).isoformat(),
        }
        self.db.put_cache(
            "term:tomato", "spoonacular", "rate_limited",
            self.provider.fetch_profile.return_value, None,
            self.provider.fetch_profile.return_value["cooldown_until"], 300,
            failure_class="quota", error_code="nutrition_provider_rate_limited",
            retryable=True,
        )

        first = resolver.resolve({"id": "f1", "name": "Tomate"})
        clock.return_value = current + timedelta(seconds=299)
        second = resolver.resolve({"id": "f1", "name": "Tomate"})
        clock.return_value = current + timedelta(seconds=300)
        retried = resolver.resolve({"id": "f1", "name": "Tomate"})

        self.assertEqual(first.status, "blocked")
        self.assertEqual(second.status, "blocked")
        self.assertEqual(first.error_code, "nutrition_provider_rate_limited")
        self.assertEqual(second.error_code, "nutrition_provider_rate_limited")
        self.assertEqual(first.retry_after, 300)
        self.assertEqual(second.retry_after, 1)
        self.assertEqual(retried.retry_after, 300)
        self.assertEqual(self.provider.fetch_profile.call_count, 2)

    def test_ascii_spanish_without_curated_alias_does_not_call_provider(self):
        resolver = FoodResolver(self.db, self.provider, source_language="es")

        result = resolver.resolve({"id": "f1", "name": "cebolleta fresca"})

        self.assertEqual(result.status, "blocked")
        self.assertEqual(result.method, "untranslated")
        self.provider.fetch_profile.assert_not_called()

    def test_ambiguous_resolution_has_bounded_expiry(self):
        self.provider.fetch_profile.return_value = {
            "ok": False, "code": "nutrition_food_unresolved", "candidates": ["tomato sauce"],
        }
        resolver = FoodResolver(self.db, self.provider, source_language="en", ambiguity_ttl_days=3)

        result = resolver.resolve({"id": "f1", "name": "tomato"})

        self.assertIsNotNone(result.stale_after)
        remaining = datetime.fromisoformat(result.stale_after) - datetime.now(timezone.utc)
        self.assertGreater(remaining.total_seconds(), 0)
        self.assertLessEqual(remaining, timedelta(days=3))

    def test_migrated_ambiguity_retries_provider_only_after_expiry(self):
        current = datetime(2026, 8, 8, 12, tzinfo=timezone.utc)
        clock = Mock(return_value=current)
        self.db.upsert_resolution(
            "f1", "tomato", "tomato", "ambiguous", 0, "canonical_name",
            stale_after=(current + timedelta(hours=1)).isoformat(),
        )
        resolver = FoodResolver(
            self.db, self.provider, source_language="en", now=clock,
        )
        self.provider.fetch_profile.return_value = {
            "ok": True, "provider_food_id": "1", "name": "tomato",
            "nutrients": {"calories_kcal": 18}, "payload_hash": "new",
        }

        cached = resolver.resolve({"id": "f1", "name": "tomato"})
        clock.return_value = current + timedelta(hours=2)
        retried = resolver.resolve({"id": "f1", "name": "tomato"})

        self.assertEqual(cached.status, "ambiguous")
        self.assertEqual(retried.status, "resolved")
        self.provider.fetch_profile.assert_called_once_with("tomato", force=False)


if __name__ == "__main__":
    unittest.main()
