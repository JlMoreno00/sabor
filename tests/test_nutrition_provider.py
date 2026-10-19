import tempfile
import threading
import unittest
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock

import requests

from nutrition.db import NutritionDB
import nutrition.providers as providers
from nutrition.providers import ProviderBudget, SpoonacularProvider


def response(status, data=None, headers=None):
    result = Mock(status_code=status, headers=headers or {}, text="body")
    result.json.return_value = data
    return result


def profile(nutrients):
    return {"nutrition": {"weightPerServing": {"amount": 100, "unit": "g"},
                          "nutrients": nutrients}}


class Clock:
    def __init__(self):
        self.current = datetime(2026, 8, 11, tzinfo=timezone.utc)

    def __call__(self):
        return self.current

    def advance(self, seconds):
        self.current += timedelta(seconds=seconds)


class NutritionProviderTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.db = NutritionDB(Path(self.tempdir.name) / "n.db")
        self.session = Mock()
        self.sleep = Mock()
        self.provider = SpoonacularProvider("key", self.db, session=self.session, sleep=self.sleep)

    def test_success_maps_profile_and_persistent_cache_deduplicates(self):
        self.session.get.side_effect = [
            response(200, {"results": [{"id": 1, "name": "tomato"}]}),
            response(200, profile([
                {"name": "Calories", "amount": 18, "unit": "kcal"},
                {"name": "Protein", "amount": .9, "unit": "g"},
                {"name": "Sodium", "amount": 5, "unit": "mg"},
            ])),
        ]
        first = self.provider.fetch_profile("tomato")
        second = SpoonacularProvider("key", self.db, session=Mock()).fetch_profile("tomato")
        self.assertTrue(first["ok"])
        self.assertEqual(first["nutrients"]["protein_g"], .9)
        self.assertTrue(second["cache_hit"])
        self.assertEqual(self.session.get.call_count, 2)

    def test_direct_provider_id_fetch_skips_search_and_uses_same_validation(self):
        self.session.get.return_value = response(200, profile([
            {"name": "Calories", "amount": 18, "unit": "kcal"},
        ]))

        result = self.provider.fetch_profile_by_id(" 1 ")

        self.assertTrue(result["ok"])
        self.assertEqual(result["provider_food_id"], "1")
        self.assertEqual(self.session.get.call_count, 1)
        self.assertIn("/food/ingredients/1/information", self.session.get.call_args.args[0])
        self.assertNotIn("search", self.session.get.call_args.args[0])

    def test_direct_provider_id_rejects_malformed_identifier_without_http(self):
        result = self.provider.fetch_profile_by_id("12/34")
        self.assertEqual(result["code"], "nutrition_provider_id_invalid")
        self.session.get.assert_not_called()

    def test_direct_provider_id_uses_profile_basis_and_unit_validation(self):
        self.session.get.return_value = response(200, {
            "nutrition": {"weightPerServing": {"amount": 1, "unit": "g"},
                          "nutrients": [{"name": "Protein", "amount": 1, "unit": "g"}]},
        })
        result = self.provider.fetch_profile_by_id("1", force=True)
        self.assertEqual(result["code"], "nutrition_provider_unavailable")
        self.assertEqual(self.db.get_cache("id:1")["failure_class"], "malformed")

    def test_malformed_timeout_not_found_and_rate_limit_contracts(self):
        self.session.get.return_value = response(200, {"bad": []})
        self.assertEqual(self.provider.fetch_profile("bad")["code"], "nutrition_provider_unavailable")
        self.session.get.side_effect = requests.Timeout()
        self.assertEqual(self.provider.fetch_profile("timeout", force=True)["code"], "nutrition_provider_unavailable")
        self.session.get.side_effect = None
        self.session.get.return_value = response(200, {"results": []})
        self.assertEqual(self.provider.fetch_profile("none", force=True)["code"], "nutrition_provider_not_found")
        self.session.get.return_value = response(429, {}, {"Retry-After": "7"})
        limited = self.provider.fetch_profile("limited", force=True)
        self.assertEqual(limited["code"], "nutrition_provider_rate_limited")
        self.assertEqual(limited["retry_after"], 7)

    def test_retryable_server_error_uses_bounded_backoff(self):
        self.session.get.side_effect = [
            response(503),
            response(200, {"results": [{"id": 1, "name": "egg"}]}),
            response(200, profile([{"name": "Calories", "amount": 155, "unit": "kcal"}])),
        ]
        self.assertTrue(self.provider.fetch_profile("egg")["ok"])
        self.sleep.assert_called_once_with(1)

    def test_non_exact_results_are_ambiguous_instead_of_guessed(self):
        self.session.get.return_value = response(200, {"results": [
            {"id": 9, "name": "tomato sauce"},
            {"id": 10, "name": "sun dried tomato"},
        ]})

        result = self.provider.fetch_profile("tomato")

        self.assertEqual(result["code"], "nutrition_food_unresolved")
        self.assertEqual(result["candidates"], [
            {"id": 9, "name": "tomato sauce"},
            {"id": 10, "name": "sun dried tomato"},
        ])
        self.assertEqual(self.session.get.call_count, 1)

    def test_exact_result_is_selected_even_when_not_first(self):
        self.session.get.side_effect = [
            response(200, {"results": [
                {"id": 9, "name": "tomato sauce"},
                {"id": 1, "name": " Tomato  "},
            ]}),
            response(200, profile([
                {"name": "Calories", "amount": 18, "unit": "kcal"},
            ])),
        ]

        result = self.provider.fetch_profile("tomato")

        self.assertTrue(result["ok"])
        self.assertEqual(result["provider_food_id"], "1")
        self.assertIn("/food/ingredients/1/information", self.session.get.call_args_list[1].args[0])

    def test_retryable_unavailable_failure_is_not_persistently_cached(self):
        self.session.get.side_effect = requests.Timeout()

        result = self.provider.fetch_profile("tomato")

        self.assertTrue(result["retryable"])
        cached = self.db.get_cache("term:tomato")
        self.assertEqual(cached["failure_class"], "unavailable")
        self.assertEqual(cached["error_code"], "nutrition_provider_unavailable")
        self.assertTrue(cached["retryable"])

    def test_transient_failures_persist_safe_structured_metadata_and_short_cooldown(self):
        cases = [
            (402, "payment", False),
            (401, "auth", False),
            (403, "auth", False),
            (503, "unavailable", True),
        ]
        for status, failure_class, retryable in cases:
            with self.subTest(status=status):
                self.session.get.reset_mock()
                self.session.get.side_effect = None
                self.session.get.return_value = response(status, {"secret": "response-body"})
                result = self.provider.fetch_profile(f"food-{status}", force=True)
                cached = self.db.get_cache(f"term:food-{status}")
                self.assertEqual(result["code"], "nutrition_provider_unavailable")
                self.assertEqual(cached["http_status"], status)
                self.assertEqual(cached["failure_class"], failure_class)
                self.assertEqual(bool(cached["retryable"]), retryable)
                serialized = json.dumps(cached)
                self.assertNotIn("response-body", serialized)
                self.assertNotIn("apiKey", serialized)
                remaining = datetime.fromisoformat(cached["expires_at"]) - datetime.now(timezone.utc)
                self.assertGreater(remaining.total_seconds(), 0)
                self.assertLessEqual(remaining.total_seconds(), 300)

    def test_malformed_response_is_cooldown_cached_without_body(self):
        self.session.get.return_value = response(200, {"bad": ["secret-body"]})
        result = self.provider.fetch_profile("malformed", force=True)
        cached = self.db.get_cache("term:malformed")
        self.assertEqual(result["code"], "nutrition_provider_unavailable")
        self.assertEqual(cached["failure_class"], "malformed")
        self.assertFalse(cached["retryable"])
        self.assertNotIn("secret-body", json.dumps(cached))

    def test_failure_cache_drops_nested_secret_like_candidate_payloads(self):
        secret = "supplied-super-secret-value"
        result = {
            "ok": False, "code": "nutrition_food_unresolved",
            "message": "Provider match is ambiguous.", "retryable": False,
            "candidates": [
                {"id": "123", "name": "safe name", "metadata": {"apiKey": secret}},
                {"id": "456\nAuthorization: Bearer token", "name": secret},
            ],
            "provider_payload": {"password": secret},
        }

        self.provider._cache_failure("term:secret", result)

        cached = self.db.get_cache("term:secret")
        self.assertNotIn(secret, cached["response_json"])
        payload = json.loads(cached["response_json"])
        self.assertEqual(payload["candidates"], [{"id": "123", "name": "safe name"}])

    def test_rate_limit_cache_expires_at_retry_after(self):
        self.session.get.return_value = response(429, {}, {"Retry-After": "7"})

        self.provider.fetch_profile("tomato")

        cached = self.db.get_cache("term:tomato")
        remaining = datetime.fromisoformat(cached["expires_at"]) - datetime.now(timezone.utc)
        self.assertEqual(cached["status"], "rate_limited")
        self.assertEqual(cached["failure_class"], "quota")
        self.assertGreater(remaining.total_seconds(), 0)
        self.assertLessEqual(remaining.total_seconds(), 7)

    def test_force_does_not_bypass_active_rate_limit_retry_after(self):
        self.session.get.return_value = response(429, {}, {"Retry-After": "7"})
        self.provider.fetch_profile("tomato")

        forced = self.provider.fetch_profile("tomato", force=True)

        self.assertEqual(forced["code"], "nutrition_provider_rate_limited")
        self.assertTrue(forced["cache_hit"])
        self.assertEqual(self.session.get.call_count, 1)

    def test_retry_after_300_is_identical_on_immediate_cached_repeat(self):
        clock = Clock()
        provider = SpoonacularProvider(
            "key", self.db, session=self.session, sleep=self.sleep, now=clock,
        )
        self.session.get.return_value = response(429, {}, {"Retry-After": "300"})

        first = provider.fetch_profile("tomato")
        second = provider.fetch_profile("tomato")

        self.assertEqual(first["retry_after"], 300)
        self.assertEqual(second["retry_after"], 300)
        self.assertEqual(first["code"], second["code"])
        self.assertTrue(second["cache_hit"])
        self.assertEqual(self.session.get.call_count, 1)

    def test_cached_retry_after_reports_remaining_cooldown_and_expires(self):
        clock = Clock()
        provider = SpoonacularProvider(
            "key", self.db, session=self.session, sleep=self.sleep, now=clock,
        )
        self.session.get.side_effect = [
            response(429, {}, {"Retry-After": "300"}),
            response(429, {}, {"Retry-After": "7"}),
        ]

        first = provider.fetch_profile("tomato")
        clock.advance(1)
        at_one = provider.fetch_profile("tomato")
        clock.advance(298)
        at_299 = provider.fetch_profile("tomato")
        clock.advance(1)
        at_300 = provider.fetch_profile("tomato")

        self.assertEqual(first["retry_after"], 300)
        self.assertEqual(at_one["retry_after"], 299)
        self.assertEqual(at_299["retry_after"], 1)
        self.assertEqual(at_300["retry_after"], 7)
        self.assertFalse(at_300.get("cache_hit", False))
        self.assertEqual(self.session.get.call_count, 2)

    def test_cached_retry_after_is_ceil_bounded_under_clock_skew(self):
        clock = Clock()
        provider = SpoonacularProvider("key", self.db, session=self.session, now=clock)
        self.session.get.return_value = response(429, {}, {"Retry-After": "300"})
        provider.fetch_profile("tomato")

        clock.advance(-30)
        skewed = provider.fetch_profile("tomato")
        clock.advance(329.1)
        fractional = provider.fetch_profile("tomato")

        self.assertEqual(skewed["retry_after"], 300)
        self.assertEqual(fractional["retry_after"], 1)
        self.assertEqual(self.session.get.call_count, 1)

    def test_explicit_zero_retry_after_uses_one_second_on_first_and_cache_paths(self):
        clock = Clock()
        provider = SpoonacularProvider("key", self.db, session=self.session, now=clock)
        self.session.get.return_value = response(429, {}, {"Retry-After": "0"})

        first = provider.fetch_profile("tomato")
        cached = provider.fetch_profile("tomato")

        self.assertEqual(first["retry_after"], 1)
        self.assertEqual(cached["retry_after"], 1)
        self.assertTrue(cached["cache_hit"])
        self.assertEqual(self.session.get.call_count, 1)

    def test_missing_malformed_and_negative_retry_after_follow_bounded_policy(self):
        cases = [({}, 60), ({"Retry-After": "later"}, 60), ({"Retry-After": "-5"}, 1)]
        for index, (headers, expected) in enumerate(cases):
            with self.subTest(headers=headers):
                clock = Clock()
                session = Mock()
                session.get.return_value = response(429, {}, headers)
                provider = SpoonacularProvider("key", self.db, session=session, now=clock)
                term = f"tomato-{index}"

                first = provider.fetch_profile(term)
                cached = provider.fetch_profile(term)

                self.assertEqual(first["retry_after"], expected)
                self.assertEqual(cached["retry_after"], expected)
                self.assertEqual(session.get.call_count, 1)

    def test_concurrent_cold_miss_coalesces_success_and_budget(self):
        workers = 8
        start = threading.Barrier(workers)
        request_started = threading.Event()
        release = threading.Event()
        calls = 0
        call_lock = threading.Lock()

        def get(url, **_kwargs):
            nonlocal calls
            with call_lock:
                calls += 1
                number = calls
            if number == 1:
                request_started.set()
                release.wait(timeout=2)
                return response(200, {"results": [{"id": 1, "name": "tomato"}]})
            return response(200, profile([{"name": "Calories", "amount": 18, "unit": "kcal"}]))

        self.session.get.side_effect = get

        def fetch():
            start.wait()
            with ProviderBudget(2) as budget:
                result = self.provider.fetch_profile("  TOMATO ")
                return result, budget.attempts

        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [executor.submit(fetch) for _ in range(workers)]
            self.assertTrue(request_started.wait(timeout=1))
            release.set()
            results = [future.result(timeout=2) for future in futures]

        self.assertEqual(calls, 2)
        self.assertTrue(all(result["ok"] for result, _ in results))
        self.assertEqual(sorted(attempts for _, attempts in results), [0] * 7 + [2])
        self.assertEqual(sum(bool(result["cache_hit"]) for result, _ in results), 7)

    def test_force_still_refreshes_an_active_success_cache(self):
        self.session.get.side_effect = [
            response(200, {"results": [{"id": 1, "name": "tomato"}]}),
            response(200, profile([{"name": "Calories", "amount": 18, "unit": "kcal"}])),
            response(200, {"results": [{"id": 2, "name": "tomato"}]}),
            response(200, profile([{"name": "Calories", "amount": 19, "unit": "kcal"}])),
        ]
        first = self.provider.fetch_profile("tomato")

        forced = self.provider.fetch_profile("tomato", force=True)

        self.assertEqual(first["provider_food_id"], "1")
        self.assertEqual(forced["provider_food_id"], "2")
        self.assertFalse(forced["cache_hit"])
        self.assertEqual(self.session.get.call_count, 4)

    def test_concurrent_cold_rate_limit_coalesces_failure_and_budget(self):
        workers = 8
        start = threading.Barrier(workers)
        entered = threading.Event()
        release = threading.Event()

        def get(*_args, **_kwargs):
            entered.set()
            release.wait(timeout=2)
            return response(429, {}, {"Retry-After": "7"})

        self.session.get.side_effect = get

        def fetch():
            start.wait()
            with ProviderBudget(1) as budget:
                result = self.provider.fetch_profile("tomato")
                return result, budget.attempts

        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [executor.submit(fetch) for _ in range(workers)]
            self.assertTrue(entered.wait(timeout=1))
            release.set()
            results = [future.result(timeout=2) for future in futures]

        self.assertEqual(self.session.get.call_count, 1)
        self.assertTrue(all(result["code"] == "nutrition_provider_rate_limited"
                            for result, _ in results))
        self.assertEqual(sorted(attempts for _, attempts in results), [0] * 7 + [1])

    def test_concurrent_cold_unavailable_coalesces_retry_sequence_and_budget(self):
        workers = 8
        start = threading.Barrier(workers)
        first_request = threading.Event()
        release = threading.Event()

        def get(*_args, **_kwargs):
            first_request.set()
            release.wait(timeout=2)
            raise requests.Timeout()

        self.session.get.side_effect = get

        def fetch():
            start.wait()
            with ProviderBudget(3) as budget:
                result = self.provider.fetch_profile("tomato")
                return result, budget.attempts

        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [executor.submit(fetch) for _ in range(workers)]
            self.assertTrue(first_request.wait(timeout=1))
            release.set()
            results = [future.result(timeout=2) for future in futures]

        self.assertEqual(self.session.get.call_count, 3)
        self.assertTrue(all(result["code"] == "nutrition_provider_unavailable"
                            for result, _ in results))
        self.assertEqual(sorted(attempts for _, attempts in results), [0] * 7 + [3])

    def test_concurrent_cold_provider_id_coalesces_success_and_budget(self):
        workers = 8
        start = threading.Barrier(workers)
        entered = threading.Event()
        release = threading.Event()

        def get(*_args, **_kwargs):
            entered.set()
            release.wait(timeout=2)
            return response(200, profile([{"name": "Calories", "amount": 18, "unit": "kcal"}]))

        self.session.get.side_effect = get

        def fetch():
            start.wait()
            with ProviderBudget(1) as budget:
                return self.provider.fetch_profile_by_id("1"), budget.attempts

        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [executor.submit(fetch) for _ in range(workers)]
            self.assertTrue(entered.wait(timeout=1))
            release.set()
            results = [future.result(timeout=2) for future in futures]

        self.assertEqual(self.session.get.call_count, 1)
        self.assertTrue(all(result["ok"] for result, _ in results))
        self.assertEqual(sorted(attempts for _, attempts in results), [0] * 7 + [1])

    def test_concurrent_cold_provider_id_coalesces_rate_limit(self):
        workers = 8
        start = threading.Barrier(workers)
        entered = threading.Event()
        release = threading.Event()

        def get(*_args, **_kwargs):
            entered.set()
            release.wait(timeout=2)
            return response(429, {}, {"Retry-After": "7"})

        self.session.get.side_effect = get

        def fetch():
            start.wait()
            with ProviderBudget(1) as budget:
                return self.provider.fetch_profile_by_id("1"), budget.attempts

        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [executor.submit(fetch) for _ in range(workers)]
            self.assertTrue(entered.wait(timeout=1))
            release.set()
            results = [future.result(timeout=2) for future in futures]

        self.assertEqual(self.session.get.call_count, 1)
        self.assertTrue(all(result["code"] == "nutrition_provider_rate_limited"
                            for result, _ in results))
        self.assertEqual(sorted(attempts for _, attempts in results), [0] * 7 + [1])

    def test_concurrent_cold_provider_id_coalesces_unavailable_retries(self):
        workers = 8
        start = threading.Barrier(workers)
        entered = threading.Event()
        release = threading.Event()

        def get(*_args, **_kwargs):
            entered.set()
            release.wait(timeout=2)
            raise requests.Timeout()

        self.session.get.side_effect = get

        def fetch():
            start.wait()
            with ProviderBudget(3) as budget:
                return self.provider.fetch_profile_by_id("1"), budget.attempts

        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [executor.submit(fetch) for _ in range(workers)]
            self.assertTrue(entered.wait(timeout=1))
            release.set()
            results = [future.result(timeout=2) for future in futures]

        self.assertEqual(self.session.get.call_count, 3)
        self.assertTrue(all(result["code"] == "nutrition_provider_unavailable"
                            for result, _ in results))
        self.assertEqual(sorted(attempts for _, attempts in results), [0] * 7 + [3])

    def test_different_provider_ids_use_different_stripes_concurrently(self):
        first = "id:1"
        first_lock = providers._provider_cache_lock(first)
        second = next(
            f"id:{index}" for index in range(2, 200)
            if providers._provider_cache_lock(f"id:{index}") is not first_lock
        )
        requests_met = threading.Barrier(2)

        def get(*_args, **_kwargs):
            requests_met.wait(timeout=1)
            return response(200, profile([{"name": "Calories", "amount": 18, "unit": "kcal"}]))

        self.session.get.side_effect = get
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(
                self.provider.fetch_profile_by_id,
                [first.removeprefix("id:"), second.removeprefix("id:")],
            ))

        self.assertTrue(all(result["ok"] for result in results))

    def test_different_provider_terms_use_different_stripes_concurrently(self):
        first = "term:a"
        first_lock = providers._provider_cache_lock(first)
        second = next(
            f"term:{index}" for index in range(200)
            if providers._provider_cache_lock(f"term:{index}") is not first_lock
        )
        terms = [first.removeprefix("term:"), second.removeprefix("term:")]
        requests_met = threading.Barrier(2)

        def get(url, *, params, **_kwargs):
            if url.endswith("/search"):
                requests_met.wait(timeout=1)
                return response(200, {"results": [{"id": params["query"], "name": params["query"]}]})
            return response(200, profile([{"name": "Calories", "amount": 18, "unit": "kcal"}]))

        self.session.get.side_effect = get
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(self.provider.fetch_profile, terms))

        self.assertTrue(all(result["ok"] for result in results))

    def test_force_does_not_bypass_active_transient_unavailable_cooldown(self):
        self.session.get.side_effect = requests.Timeout()
        first = self.provider.fetch_profile("tomato")

        forced = self.provider.fetch_profile("tomato", force=True)

        self.assertEqual(first["code"], "nutrition_provider_unavailable")
        self.assertEqual(forced["code"], first["code"])
        self.assertEqual(forced["retry_after"], first["retry_after"])
        self.assertTrue(forced["cache_hit"])
        self.assertEqual(self.session.get.call_count, 3)

    def test_provider_budget_counts_each_retry_attempt(self):
        self.session.get.return_value = response(503)

        with ProviderBudget(3) as budget:
            result = self.provider.fetch_profile("tomato", force=True)

        self.assertEqual(result["code"], "nutrition_provider_unavailable")
        self.assertEqual(budget.attempts, 3)
        self.assertEqual(self.session.get.call_count, 3)

    def test_exhausted_budget_does_not_sleep_after_final_attempt(self):
        for index, outcome in enumerate((requests.Timeout(), response(503))):
            with self.subTest(outcome=type(outcome).__name__):
                self.session.get.reset_mock()
                self.sleep.reset_mock()
                self.session.get.side_effect = outcome if isinstance(outcome, Exception) else None
                self.session.get.return_value = outcome if not isinstance(outcome, Exception) else None
                with ProviderBudget(1) as budget:
                    result = self.provider.fetch_profile_by_id(str(index + 1), force=True)

                self.assertEqual(result["code"], "nutrition_provider_budget_exceeded")
                self.assertEqual(budget.attempts, 1)
                self.assertEqual(self.session.get.call_count, 1)
                self.sleep.assert_not_called()

    def test_duplicate_exact_provider_ids_are_deduplicated(self):
        self.session.get.side_effect = [
            response(200, {"results": [
                {"id": 1, "name": "tomato"}, {"id": 1, "name": " Tomato "},
            ]}),
            response(200, profile([{"name": "Calories", "amount": 18, "unit": "kcal"}])),
        ]
        self.assertTrue(self.provider.fetch_profile("tomato")["ok"])

    def test_invalid_nutrient_units_and_non_100g_basis_are_rejected(self):
        cases = [
            profile([{"name": "Protein", "amount": .9, "unit": "mg"}]),
            {"nutrition": {"weightPerServing": {"amount": 1, "unit": "g"},
                           "nutrients": [{"name": "Protein", "amount": .9, "unit": "g"}]}},
        ]
        for index, payload in enumerate(cases):
            with self.subTest(index=index):
                self.session.get.side_effect = [
                    response(200, {"results": [{"id": index + 1, "name": "tomato"}]}),
                    response(200, payload),
                ]
                result = self.provider.fetch_profile("tomato", force=True)
                self.assertEqual(result["code"], "nutrition_provider_unavailable")

    def test_non_finite_nutrient_amounts_are_rejected_before_caching(self):
        for index, amount in enumerate((float("nan"), float("inf"), float("-inf"))):
            with self.subTest(amount=amount):
                provider_id = index + 1
                self.session.get.side_effect = [
                    response(200, {"results": [{"id": provider_id, "name": "tomato"}]}),
                    response(200, profile([
                        {"name": "Calories", "amount": amount, "unit": "kcal"},
                    ])),
                ]

                result = self.provider.fetch_profile("tomato", force=True)

                self.assertEqual(result["code"], "nutrition_provider_unavailable")
                cached = self.db.get_cache("term:tomato")
                self.assertEqual(cached["failure_class"], "malformed")
                self.assertIsNone(self.db.get_cache(f"id:{provider_id}"))

    def test_mono_and_polyunsaturated_fats_are_safely_aggregated(self):
        self.session.get.side_effect = [
            response(200, {"results": [{"id": 1, "name": "oil"}]}),
            response(200, profile([
                {"name": "Monounsaturated Fat", "amount": 7, "unit": "g"},
                {"name": "Polyunsaturated Fat", "amount": 3, "unit": "g"},
            ])),
        ]
        result = self.provider.fetch_profile("oil")
        self.assertEqual(result["nutrients"]["unsaturated_fat_g"], 10)


if __name__ == "__main__":
    unittest.main()
