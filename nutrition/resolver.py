"""Conservative canonical resolution for Mealie foods."""

from __future__ import annotations

import unicodedata
import math
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from .db import NutritionDB, normalize_mealie_food_id
from .models import FoodResolution, SPOONACULAR_PROFILE_CONFIDENCE
from .providers import normalized_food_name


CURATED_ALIASES = {
    "tomate": "tomato", "tomates": "tomato", "huevo": "egg", "huevos": "egg",
    "ajo": "garlic", "diente de ajo": "garlic clove", "cebolla": "onion",
    "aceite de oliva": "olive oil", "aceite de oliva virgen extra": "olive oil",
    "pollo": "chicken", "pechuga de pollo": "chicken breast",
    "leche": "milk", "harina": "flour", "azucar": "sugar", "sal": "salt",
    "limon": "lemon", "lima": "lime",
}


def normalize_term(value: Any) -> str:
    text = " ".join(str(value or "").split()).casefold()
    return "".join(char for char in unicodedata.normalize("NFKD", text)
                   if not unicodedata.combining(char))


def _fresh(stale_after: str | None, now: datetime | None = None) -> bool:
    if not stale_after:
        return True
    try:
        return datetime.fromisoformat(stale_after) > (now or datetime.now(timezone.utc))
    except ValueError:
        return False


class FoodResolver:
    def __init__(self, db: NutritionDB, provider: Any, *,
                 translator: Callable[[str], str] | None = None,
                 profile_ttl_days: int = 180, not_found_ttl_days: int = 30,
                 ambiguity_ttl_days: int = 7, source_language: str = "es",
                 now: Callable[[], datetime] | None = None):
        self.db = db
        self.provider = provider
        self.translator = translator
        self.profile_ttl_days = profile_ttl_days
        self.not_found_ttl_days = not_found_ttl_days
        self.ambiguity_ttl_days = ambiguity_ttl_days
        self.source_language = normalize_term(source_language)
        self.now = now or (lambda: datetime.now(timezone.utc))

    def resolve(self, mealie_food: dict[str, Any], *, force: bool = False) -> FoodResolution:
        try:
            food_id = normalize_mealie_food_id(mealie_food.get("id"))
        except ValueError:
            food_id = ""
        name = str(mealie_food.get("name") or "").strip()
        if not food_id:
            return FoodResolution("", name, None, "blocked", 0, "missing_food_id")
        existing = self.db.get_resolution(food_id)
        if existing and existing["manual_override"]:
            profile = self.db.get_profile(food_id)
            active_provider_food_id = existing.get("provider_food_id")
            binding_matches = bool(profile) and (
                not active_provider_food_id
                or (profile.get("provider") == existing.get("provider")
                    and profile.get("provider_food_id") == active_provider_food_id)
            )
            if profile and (profile["manual_override"] or
                            (binding_matches and not force
                             and _fresh(profile.get("stale_after"), self.now()))):
                return self._model(existing)
            if profile and not profile["manual_override"] and not binding_matches:
                self.db.delete_profile(food_id)
            canonical = existing.get("canonical_term")
            if canonical:
                provider_food_id = existing.get("provider_food_id")
                fetched = (
                    self.provider.fetch_profile_by_id(provider_food_id, force=force)
                    if provider_food_id else self.provider.fetch_profile(canonical, force=force)
                )
                if fetched.get("ok") and (provider_food_id or self._provider_match(canonical, fetched)):
                    stale_after = (
                        self.now() + timedelta(days=self.profile_ttl_days)
                    ).isoformat()
                    profile_provider = existing.get("provider") or "spoonacular"
                    profile_confidence = (
                        SPOONACULAR_PROFILE_CONFIDENCE
                        if profile_provider == "spoonacular" else existing["confidence"]
                    )
                    self.db.upsert_profile(
                        food_id, fetched["nutrients"], profile_provider, profile_confidence,
                        fetched["payload_hash"],
                        provider_food_id=fetched.get("provider_food_id"),
                        stale_after=stale_after,
                    )
            result = self._model(existing)
            if canonical and not fetched.get("ok"):
                result.error_code = fetched.get("code")
                result.stale_after = fetched.get("cooldown_until")
                result.retry_after = fetched.get("retry_after")
            return result
        if existing and not force and _fresh(existing.get("stale_after"), self.now()):
            return self._model(existing, self._cached_failure(existing))
        normalized = normalize_term(name)
        canonical = CURATED_ALIASES.get(normalized)
        if canonical:
            method, confidence = "curated_alias", .98
        elif self.translator:
            canonical = normalize_term(self.translator(normalized))
            method, confidence = "translated", .92
        elif self.source_language == "en":
            canonical = normalized
            method, confidence = "canonical_name", .92
        else:
            canonical = None
            method, confidence = "untranslated", 0
        if not canonical:
            self.db.upsert_resolution(food_id, name, None, "blocked", 0, method)
            return FoodResolution(food_id, name, None, "blocked", 0, method)
        fetched = self.provider.fetch_profile(canonical, force=force)
        if fetched.get("ok") and not self._provider_match(canonical, fetched):
            fetched = {
                "ok": False, "code": "nutrition_food_unresolved",
                "message": "Provider result does not exactly match the canonical food.",
                "retryable": False, "candidates": [fetched.get("name")],
            }
        if not fetched.get("ok"):
            status = "ambiguous" if fetched.get("code") == "nutrition_food_unresolved" else "not_found"
            if fetched.get("code") in {
                "nutrition_provider_rate_limited", "nutrition_provider_unavailable",
                "nutrition_provider_budget_exceeded",
            }:
                status = "blocked"
            if status == "ambiguous":
                stale_after = (
                    self.now() + timedelta(days=self.ambiguity_ttl_days)
                ).isoformat()
            elif status == "blocked":
                stale_after = fetched.get("cooldown_until") or self.now().isoformat()
            else:
                stale_after = (
                    self.now() + timedelta(days=self.not_found_ttl_days)
                ).isoformat()
            self.db.upsert_resolution(food_id, name, canonical, status, 0, method,
                                      stale_after=stale_after)
            return FoodResolution(food_id, name, canonical, status, 0, method,
                                  stale_after=stale_after, error_code=fetched.get("code"),
                                  retry_after=fetched.get("retry_after"))
        stale_after = (self.now() + timedelta(days=self.profile_ttl_days)).isoformat()
        self.db.upsert_resolution(
            food_id, name, canonical, "resolved", confidence, method,
            provider="spoonacular", provider_food_id=fetched.get("provider_food_id"),
            payload_hash=fetched.get("payload_hash"), stale_after=stale_after,
        )
        self.db.upsert_profile(
            food_id, fetched["nutrients"], "spoonacular", SPOONACULAR_PROFILE_CONFIDENCE,
            fetched["payload_hash"], provider_food_id=fetched.get("provider_food_id"),
            stale_after=stale_after,
        )
        return FoodResolution(food_id, name, canonical, "resolved", confidence, method,
                              "spoonacular", fetched.get("provider_food_id"), False, stale_after)

    def _cached_failure(self, row: dict[str, Any]) -> dict[str, Any] | None:
        provider_id = row.get("provider_food_id")
        canonical = row.get("canonical_term")
        cache_key = f"id:{provider_id}" if provider_id else f"term:{canonical}" if canonical else None
        cached = self.db.get_cache(cache_key) if cache_key else None
        if not cached or not _fresh(cached.get("expires_at"), self.now()):
            return None
        result = dict(cached)
        if result.get("retryable") or result.get("status") == "rate_limited":
            remaining = (datetime.fromisoformat(result["expires_at"]) - self.now()).total_seconds()
            result["retry_after"] = min(300, max(1, math.ceil(remaining)))
        return result

    @staticmethod
    def _model(row: dict[str, Any], failure: dict[str, Any] | None = None) -> FoodResolution:
        return FoodResolution(
            row["mealie_food_id"], row["mealie_food_name"], row.get("canonical_term"),
            row["resolution_status"], row["confidence"], row["resolution_method"],
            row.get("provider"), row.get("provider_food_id"), bool(row["manual_override"]),
            row.get("stale_after"),
            (failure or {}).get("error_code"), (failure or {}).get("retry_after"),
        )

    @staticmethod
    def _provider_match(canonical: str, fetched: dict[str, Any]) -> bool:
        return normalized_food_name(fetched.get("name")) == normalized_food_name(canonical)
