"""Rate-limit-aware Spoonacular profile client with persistent caching."""

from __future__ import annotations

import hashlib
import json
import math
import re
import threading
import time
import unicodedata
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

from .db import NutritionDB, normalize_provider_food_id
from .models import is_finite_number


NUTRIENT_MAP = {
    "Calories": "calories_kcal", "Protein": "protein_g",
    "Carbohydrates": "carbohydrate_g", "Net Carbohydrates": "net_carbohydrate_g",
    "Fat": "fat_g", "Saturated Fat": "saturated_fat_g",
    "Monounsaturated Fat": "unsaturated_fat_g",
    "Polyunsaturated Fat": "unsaturated_fat_g", "Trans Fat": "trans_fat_g",
    "Fiber": "fiber_g", "Sugar": "sugar_g", "Sodium": "sodium_mg",
    "Cholesterol": "cholesterol_mg",
}

EXPECTED_UNITS = {
    field: ("kcal" if field == "calories_kcal" else
            "mg" if field in {"sodium_mg", "cholesterol_mg"} else "g")
    for field in NUTRIENT_MAP.values()
}
SAFE_DIAGNOSTIC_LENGTH = 128
SENSITIVE_TEXT = re.compile(
    r"(?i)(api[_-]?key|authorization|bearer|password|secret|token|credential)"
)
_ACTIVE_BUDGET: ContextVar["ProviderBudget | None"] = ContextVar(
    "nutrition_provider_budget", default=None,
)
_PROVIDER_CACHE_LOCKS = tuple(threading.Lock() for _ in range(64))


class ProviderBudget:
    """Invocation-local cap over actual upstream HTTP attempts."""

    def __init__(self, limit: int = 20):
        self.limit = max(0, int(limit))
        self.attempts = 0
        self._token = None

    def __enter__(self) -> "ProviderBudget":
        self._token = _ACTIVE_BUDGET.set(self)
        return self

    def __exit__(self, *_: Any) -> None:
        if self._token is not None:
            _ACTIVE_BUDGET.reset(self._token)

    def consume(self) -> bool:
        if self.attempts >= self.limit:
            return False
        self.attempts += 1
        return True

    @property
    def exhausted(self) -> bool:
        return self.attempts >= self.limit


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _provider_cache_lock(cache_key: str) -> threading.Lock:
    return _PROVIDER_CACHE_LOCKS[hash(f"spoonacular:{cache_key}") % len(_PROVIDER_CACHE_LOCKS)]


def _retry_seconds(value: Any) -> int:
    try:
        seconds = int(60 if value is None else value)
    except (TypeError, ValueError, OverflowError):
        seconds = 60
    return min(300, max(1, seconds))


def _hash(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def normalized_food_name(value: Any) -> str:
    text = " ".join(str(value or "").split()).casefold()
    return "".join(char for char in unicodedata.normalize("NFKD", text)
                   if not unicodedata.combining(char))


def _safe_diagnostic(value: Any) -> str | None:
    if not isinstance(value, (str, int, float, bool)) or value is None:
        return None
    text = " ".join(str(value).split())[:SAFE_DIAGNOSTIC_LENGTH]
    if not text or SENSITIVE_TEXT.search(text):
        return None
    return text


def _safe_candidates(value: Any) -> list[Any]:
    if not isinstance(value, list):
        return []
    candidates: list[Any] = []
    for candidate in value[:5]:
        if isinstance(candidate, dict):
            safe = {
                key: sanitized for key in ("id", "name")
                if (sanitized := _safe_diagnostic(candidate.get(key))) is not None
            }
            if safe:
                candidates.append(safe)
        elif (safe_name := _safe_diagnostic(candidate)) is not None:
            candidates.append(safe_name)
    return candidates


class SpoonacularProvider:
    def __init__(self, api_key: str, db: NutritionDB, *,
                 base_url: str = "https://api.spoonacular.com",
                 timeout: int = 20, profile_ttl_days: int = 180,
                 not_found_ttl_days: int = 30, session: Any = requests,
                 sleep: Any = time.sleep, now: Any = _now):
        self.api_key = api_key
        self.db = db
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.profile_ttl_days = profile_ttl_days
        self.not_found_ttl_days = not_found_ttl_days
        self.session = session
        self.sleep = sleep
        self.now = now

    def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        for attempt in range(3):
            budget = _ACTIVE_BUDGET.get()
            if budget is not None and not budget.consume():
                return {"ok": False, "code": "nutrition_provider_budget_exceeded",
                        "message": "Nutrition provider attempt budget was exhausted.",
                        "retryable": True, "retry_after": 60,
                        "failure_class": "budget"}
            try:
                response = self.session.get(
                    f"{self.base_url}{path}", params={**params, "apiKey": self.api_key},
                    timeout=self.timeout,
                )
            except (requests.Timeout, requests.ConnectionError) as exc:
                if attempt < 2:
                    if budget is not None and budget.exhausted:
                        return {"ok": False, "code": "nutrition_provider_budget_exceeded",
                                "message": "Nutrition provider attempt budget was exhausted.",
                                "retryable": True, "retry_after": 60,
                                "failure_class": "budget"}
                    self.sleep(2 ** attempt)
                    continue
                return {"ok": False, "code": "nutrition_provider_unavailable",
                        "message": "Nutrition provider request failed.", "retryable": True,
                        "failure_class": "unavailable"}
            if response.status_code == 429:
                raw_retry_after = response.headers.get("Retry-After")
                try:
                    retry_after = int(raw_retry_after) if raw_retry_after is not None else None
                except (TypeError, ValueError):
                    retry_after = None
                return {"ok": False, "code": "nutrition_provider_rate_limited",
                        "message": "Spoonacular rate limit reached.", "retryable": True,
                        "retry_after": retry_after, "http_status": 429,
                        "failure_class": "quota"}
            if response.status_code in {500, 502, 503, 504} and attempt < 2:
                if budget is not None and budget.exhausted:
                    return {"ok": False, "code": "nutrition_provider_budget_exceeded",
                            "message": "Nutrition provider attempt budget was exhausted.",
                            "retryable": True, "retry_after": 60,
                            "failure_class": "budget"}
                self.sleep(2 ** attempt)
                continue
            if not 200 <= response.status_code < 300:
                failure_class = (
                    "payment" if response.status_code == 402 else
                    "auth" if response.status_code in {401, 403} else
                    "unavailable"
                )
                return {"ok": False, "code": "nutrition_provider_unavailable",
                        "message": f"Spoonacular HTTP {response.status_code}",
                        "retryable": response.status_code >= 500,
                        "http_status": response.status_code,
                        "failure_class": failure_class}
            try:
                return {"ok": True, "data": response.json()}
            except Exception:
                return {"ok": False, "code": "nutrition_provider_unavailable",
                        "message": "Spoonacular returned invalid JSON.", "retryable": False,
                        "failure_class": "malformed"}
        return {"ok": False, "code": "nutrition_provider_unavailable",
                "message": "Spoonacular retries exhausted.", "retryable": True,
                "failure_class": "unavailable"}

    def fetch_profile(self, canonical_term: str, *, force: bool = False) -> dict[str, Any]:
        term = " ".join(canonical_term.casefold().split())
        cache_key = f"term:{term}"
        had_active_cache = self._cache_active(self.db.get_cache(cache_key))
        if cached_result := self._cached_result(cache_key, force=force):
            return cached_result
        with _provider_cache_lock(cache_key):
            if cached_result := self._cached_result(
                    cache_key, force=force if had_active_cache else False):
                return cached_result
            return self._fetch_profile_uncached(term, cache_key)

    def _fetch_profile_uncached(self, term: str, cache_key: str) -> dict[str, Any]:
        if not self.api_key:
            return {"ok": False, "code": "nutrition_provider_unavailable",
                    "message": "Spoonacular API key is not configured.", "retryable": False}
        search = self._get("/food/ingredients/search", {
            "query": term, "number": 5, "metaInformation": True,
        })
        if not search["ok"]:
            return self._cache_failure(cache_key, search)
        body = search.get("data")
        if not isinstance(body, dict) or not isinstance(body.get("results"), list):
            return self._cache_failure(cache_key, {
                "ok": False, "code": "nutrition_provider_unavailable",
                "message": "Spoonacular search response is malformed.", "retryable": False,
                "failure_class": "malformed",
            })
        results = body["results"]
        if not results:
            result = {"ok": False, "code": "nutrition_provider_not_found",
                      "message": f"No profile found for '{term}'.", "retryable": False}
            return self._cache_failure(cache_key, result)
        exact_by_id = {
            str(item.get("id")): item for item in results
            if item.get("id") is not None
            and normalized_food_name(item.get("name")) == normalized_food_name(term)
        }
        exact = list(exact_by_id.values())
        if len(exact) != 1:
            result = {"ok": False, "code": "nutrition_food_unresolved",
                       "message": "Provider match is ambiguous.", "retryable": False,
                       "candidates": [
                           {"id": item.get("id"), "name": item.get("name")}
                           for item in (exact or results)
                       ]}
            return self._cache_failure(cache_key, result, days=self.not_found_ttl_days)
        selected = exact[0]
        provider_id = selected.get("id")
        if provider_id is None:
            return self._cache_failure(cache_key, {
                "ok": False, "code": "nutrition_provider_id_invalid",
                "message": "Spoonacular result has no valid ID.", "retryable": False,
                "failure_class": "malformed",
            })
        try:
            provider_id = normalize_provider_food_id(provider_id)
        except ValueError:
            return self._cache_failure(cache_key, {
                "ok": False, "code": "nutrition_provider_id_invalid",
                "message": "Spoonacular result has no valid ID.", "retryable": False,
                "failure_class": "malformed",
            })
        info = self._get(f"/food/ingredients/{provider_id}/information",
                         {"amount": 100, "unit": "grams"})
        if not info["ok"]:
            return self._cache_failure(cache_key, info)
        result = self._profile_result(info, provider_id, selected.get("name", term))
        if not result["ok"]:
            return self._cache_failure(cache_key, result)
        return self._cache_success(cache_key, result)

    def fetch_profile_by_id(self, provider_food_id: str, *, force: bool = False) -> dict[str, Any]:
        try:
            provider_id = normalize_provider_food_id(provider_food_id)
        except ValueError:
            return {"ok": False, "code": "nutrition_provider_id_invalid",
                    "message": "Provider food ID is invalid.", "retryable": False,
                    "failure_class": "malformed"}
        cache_key = f"id:{provider_id}"
        had_active_cache = self._cache_active(self.db.get_cache(cache_key))
        if cached_result := self._cached_result(cache_key, force=force):
            return cached_result
        with _provider_cache_lock(cache_key):
            if cached_result := self._cached_result(
                    cache_key, force=force if had_active_cache else False):
                return cached_result
            return self._fetch_profile_by_id_uncached(provider_id, cache_key)

    def _fetch_profile_by_id_uncached(self, provider_id: str,
                                      cache_key: str) -> dict[str, Any]:
        if not self.api_key:
            return self._cache_failure(cache_key, {
                "ok": False, "code": "nutrition_provider_unavailable",
                "message": "Spoonacular API key is not configured.", "retryable": False,
                "failure_class": "auth",
            })
        info = self._get(f"/food/ingredients/{provider_id}/information",
                         {"amount": 100, "unit": "grams"})
        if not info["ok"]:
            return self._cache_failure(cache_key, info)
        data = info.get("data") or {}
        result = self._profile_result(info, provider_id, data.get("name") or provider_id)
        if not result["ok"]:
            return self._cache_failure(cache_key, result)
        return self._cache_success(cache_key, result)

    def _cached_result(self, cache_key: str, *, force: bool) -> dict[str, Any] | None:
        cached = self.db.get_cache(cache_key)
        if not self._cache_active(cached):
            return None
        remaining = (datetime.fromisoformat(cached["expires_at"]) - self.now()).total_seconds()
        if remaining <= 0 or (force and cached["status"] != "rate_limited" and not (
                cached.get("retryable") and cached.get("error_code") in {
                    "nutrition_provider_unavailable", "nutrition_provider_budget_exceeded",
                })):
            return None
        payload = json.loads(cached["response_json"]) if cached["response_json"] else {}
        if cached["status"] == "rate_limited" or cached.get("retryable"):
            payload["retry_after"] = min(300, max(1, math.ceil(remaining)))
        payload["cache_hit"] = True
        return payload

    def _cache_active(self, cached: dict[str, Any] | None) -> bool:
        if not cached:
            return False
        try:
            return datetime.fromisoformat(cached["expires_at"]) > self.now()
        except (TypeError, ValueError):
            return False

    def _profile_result(self, info: dict[str, Any], provider_id: str,
                        name: str) -> dict[str, Any]:
        nutrition = ((info.get("data") or {}).get("nutrition") or {})
        basis = nutrition.get("weightPerServing") or {}
        try:
            valid_basis = float(basis.get("amount")) == 100
        except (TypeError, ValueError):
            valid_basis = False
        if not valid_basis or normalized_food_name(basis.get("unit")) not in {"g", "gram", "grams"}:
            return {"ok": False, "code": "nutrition_provider_unavailable",
                    "message": "Spoonacular profile is not based on 100 g.", "retryable": False,
                    "failure_class": "malformed"}
        nutrients_raw = nutrition.get("nutrients")
        if not isinstance(nutrients_raw, list):
            return {"ok": False, "code": "nutrition_provider_unavailable",
                    "message": "Spoonacular nutrition response is malformed.", "retryable": False,
                    "failure_class": "malformed"}
        nutrients: dict[str, float] = {}
        for nutrient in nutrients_raw:
            field = NUTRIENT_MAP.get(str(nutrient.get("name", "")))
            amount = nutrient.get("amount")
            unit = normalized_food_name(nutrient.get("unit"))
            if field and isinstance(amount, (int, float)):
                if not is_finite_number(amount):
                    return {"ok": False, "code": "nutrition_provider_unavailable",
                            "message": f"Spoonacular returned an invalid amount for {field}.",
                            "retryable": False, "failure_class": "malformed"}
                if unit != EXPECTED_UNITS[field]:
                    return {"ok": False, "code": "nutrition_provider_unavailable",
                            "message": f"Spoonacular returned an invalid unit for {field}.",
                            "retryable": False, "failure_class": "malformed"}
                combined = nutrients.get(field, 0) + float(amount)
                if not is_finite_number(combined):
                    return {"ok": False, "code": "nutrition_provider_unavailable",
                            "message": f"Spoonacular returned an invalid amount for {field}.",
                            "retryable": False, "failure_class": "malformed"}
                nutrients[field] = combined
        if not nutrients:
            return {"ok": False, "code": "nutrition_provider_unavailable",
                    "message": "Spoonacular profile has no supported nutrients.", "retryable": False,
                    "failure_class": "malformed"}
        result = {"ok": True, "provider": "spoonacular", "provider_food_id": str(provider_id),
                  "name": name, "nutrients": nutrients,
                  "payload_hash": _hash(info["data"]), "cache_hit": False}
        return result

    def _cache_success(self, cache_key: str, result: dict[str, Any]) -> dict[str, Any]:
        expires = (self.now() + timedelta(days=self.profile_ttl_days)).isoformat()
        self.db.put_cache(cache_key, "spoonacular", "success", result,
                          result["payload_hash"], expires)
        self.db.put_cache(f"id:{result['provider_food_id']}", "spoonacular", "success", result,
                          result["payload_hash"], expires)
        return result

    def _cache_failure(self, cache_key: str, result: dict[str, Any],
                        days: int | None = None) -> dict[str, Any]:
        if result["code"] == "nutrition_provider_rate_limited":
            seconds = _retry_seconds(result.get("retry_after"))
            result["retry_after"] = seconds
            expires = (self.now() + timedelta(seconds=seconds)).isoformat()
            status = "rate_limited"
        elif result["code"] in {"nutrition_provider_not_found", "nutrition_food_unresolved"}:
            expires = (self.now() + timedelta(days=days or self.not_found_ttl_days)).isoformat()
            status = "not_found" if result["code"] == "nutrition_provider_not_found" else "error"
        else:
            seconds = _retry_seconds(result.get("retry_after"))
            if result.get("retryable"):
                result["retry_after"] = seconds
            expires = (self.now() + timedelta(seconds=seconds)).isoformat()
            status = "error"
        safe_result: dict[str, Any] = {}
        for key in ("ok", "retryable", "retry_after", "http_status"):
            value = result.get(key)
            if isinstance(value, (bool, int, float)) and not isinstance(value, complex):
                safe_result[key] = value
        for key in ("code", "message", "failure_class"):
            value = _safe_diagnostic(result.get(key))
            if value is not None:
                safe_result[key] = value
        candidates = _safe_candidates(result.get("candidates"))
        if candidates:
            safe_result["candidates"] = candidates
        safe_result["cooldown_until"] = expires
        self.db.put_cache(
            cache_key, "spoonacular", status, safe_result, None, expires,
            result.get("retry_after"), http_status=result.get("http_status"),
            failure_class=result.get("failure_class"), error_code=result.get("code"),
            retryable=bool(result.get("retryable")),
        )
        result.update({"cooldown_until": expires})
        return result
