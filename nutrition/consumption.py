"""Shared consumed-mass semantics for readiness and conversion."""

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Collection


_NUMBER = r"(?:\d+(?:[.,]\d+)?|\d+\s*/\s*\d+)"
_LEADING_QUANTITY_RE = re.compile(
    rf"^\s*{_NUMBER}(?:\s*[-–]\s*{_NUMBER})?\s*", re.IGNORECASE,
)
_NON_CONSUMED_CONCEPT_RE = re.compile(
    r"(?:"
    r"cooking water|agua de coccion|discarded (?:brine|liquid)|"
    r"(?:salmuera|liquido) desechad[ao]|garnish not consumed|"
    r"bones|.+ bones|huesos|huesos de .+|shells|.+ shells|"
    r"cascaras|cascaras de .+|peels|.+ peels|pieles|pieles de .+"
    r")"
)
_MARINADE_PREFIX_RE = re.compile(
    r"(?:marinade|marinada|marinade for .+|marinada (?:para|de) .+)"
)
_MARINADE_SUFFIX_RE = re.compile(r".+ (?:marinade|marinada)")
_MARINADE_SUFFIX_EXCLUSIONS_RE = re.compile(
    r"\b(?:with|con|in|en|and|y|for|para|de|seasoning|spice|condimento|especia)\b"
)
RETENTION_EVIDENCE_PRECEDENCE = (
    "invalid_retained_fraction",
    "invalid_retained_grams",
    "invalid_absorbed_grams",
    "conflicting_retention_evidence",
    "retained_grams_exceeds_supplied_mass",
    "absorbed_grams_exceeds_supplied_mass",
)


@dataclass(frozen=True)
class ConsumptionSemantics:
    """One classification consumed by both admission and gram conversion."""

    status: str
    reason: str | None = None
    retained_fraction: float | None = None
    retained_grams: float | None = None
    absorbed_grams: float | None = None


def _normalize(value: Any) -> str:
    text = " ".join(str(value or "").split()).casefold()
    return "".join(
        char for char in unicodedata.normalize("NFKD", text)
        if not unicodedata.combining(char)
    )


def _normalize_tokens(value: Any) -> str:
    normalized = _normalize(value)
    return " ".join("".join(
        char if unicodedata.category(char)[0] in {"L", "N"} else " "
        for char in normalized
    ).split())


def _ingredient_concept(source_text: str, unit_tokens: Collection[str]) -> str:
    concept = _normalize(source_text)
    quantity = _LEADING_QUANTITY_RE.match(concept)
    if quantity:
        concept = concept[quantity.end():]
        for token in sorted((_normalize(item) for item in unit_tokens), key=len, reverse=True):
            if token and re.match(rf"^{re.escape(token)}(?:\s|$)", concept):
                concept = concept[len(token):].lstrip()
                break
    return _normalize_tokens(concept)


def _finite_number(value: Any) -> float | None:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def classify_consumption(
    ingredient: dict[str, Any], source_text: str, unit_tokens: Collection[str],
    known_supplied_grams: float | None = None,
) -> ConsumptionSemantics:
    """Classify only whole ingredient concepts, never row-wide substrings."""

    if ingredient.get("nonConsumed") is True:
        return ConsumptionSemantics("excluded", "not_consumed")

    frying = ingredient.get("fryingConsumption")
    frying = frying if isinstance(frying, dict) else {}
    fraction_values = [source["retainedFraction"] for source in (ingredient, frying)
                       if "retainedFraction" in source]
    grams_values = [source["retainedGrams"] for source in (ingredient, frying)
                    if "retainedGrams" in source]
    absorbed_value = ingredient.get("absorbedGrams")
    if absorbed_value is None:
        absorbed_value = frying.get("absorbedGrams")
    fractions = [_finite_number(value) for value in fraction_values]
    grams_candidates = [_finite_number(value) for value in grams_values]
    absorbed = (_finite_number(absorbed_value)
                if absorbed_value is not None else None)
    violations = set()
    if any(value is None or not 0 <= value <= 1 for value in fractions):
        violations.add("invalid_retained_fraction")
    if any(value is None or value < 0 for value in grams_candidates):
        violations.add("invalid_retained_grams")
    if absorbed_value is not None and (absorbed is None or absorbed < 0):
        violations.add("invalid_absorbed_grams")
    if fraction_values and grams_values:
        violations.add("conflicting_retention_evidence")
    if len(set(fractions)) > 1 or len(set(grams_candidates)) > 1:
        violations.add("conflicting_retention_evidence")
    fraction = fractions[0] if fractions else None
    grams = grams_candidates[0] if grams_candidates else None
    if absorbed is not None and (fraction is not None or grams is not None):
        violations.add("conflicting_retention_evidence")
    if (grams is not None and known_supplied_grams is not None
            and grams > known_supplied_grams):
        violations.add("retained_grams_exceeds_supplied_mass")
    if (absorbed is not None and known_supplied_grams is not None
            and absorbed > known_supplied_grams):
        violations.add("absorbed_grams_exceeds_supplied_mass")
    reason = next((item for item in RETENTION_EVIDENCE_PRECEDENCE
                   if item in violations), None)
    if reason is not None:
        return ConsumptionSemantics("invalid_retention", reason)

    concept = _ingredient_concept(source_text, unit_tokens)
    if _NON_CONSUMED_CONCEPT_RE.fullmatch(concept):
        return ConsumptionSemantics(
            "excluded", "not_consumed", fraction, grams,
        )
    suffix_marinade = bool(_MARINADE_SUFFIX_RE.fullmatch(concept)) and not re.search(
        _MARINADE_SUFFIX_EXCLUSIONS_RE,
        concept.rsplit(" ", 1)[0],
    )
    is_marinade = (
        _MARINADE_PREFIX_RE.fullmatch(concept) or suffix_marinade
    )
    if is_marinade and fraction is None and grams is None:
        return ConsumptionSemantics("retention_required", "marinade_requires_retention")
    if fraction is not None:
        return ConsumptionSemantics("retained", retained_fraction=fraction)
    if grams is not None:
        return ConsumptionSemantics("retained", retained_grams=grams)
    return ConsumptionSemantics("consumed", absorbed_grams=absorbed)
