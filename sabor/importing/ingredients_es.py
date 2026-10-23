"""Conservative Spanish ingredient lines, preserving the untouched source.

Bare amounts require a countable food recognized by quantities.parse_text;
otherwise an explicit catalog/canonical unit and a food name are required.
"""
import json
from pathlib import Path
import re
from types import MappingProxyType

from sabor.pricing.quantities import parse_text
from sabor.store import Ingredient, NamedRef


def _load_units():
    catalog = Path(__file__).resolve().parents[2] / "catalog" / "spanish_units.json"
    units = {}
    for item in json.loads(catalog.read_text(encoding="utf-8"))["items"]:
        for alias in (item["name"], item.get("pluralName"), item.get("abbreviation"),
                      item.get("pluralAbbreviation"), *item.get("aliases", ())):
            if alias:
                units[alias.casefold()] = item["name"]
    return MappingProxyType(units)


_UNITS = _load_units()
_AMOUNT = re.compile(
    r"^(\d+\s+\d+/\d+|\d+/\d+|\d+(?:[.,]\d+)?|[½¼¾⅓⅔⅛]|"
    r"un|una|uno|dos|tres|cuatro|cinco|seis|siete|ocho|nueve|diez|once|doce|medio|media)"
    r"(?=\s|$)\s*", re.I)


def parse_ingredient(line: str) -> Ingredient:
    """Unknown units/ranges/no amounts stay entirely unstructured (4096 cap)."""
    unknown = Ingredient(originalText=line, note=line)
    if len(line) > 4096:
        return unknown
    text = line.strip()
    match = _AMOUNT.match(text)
    if not match or re.search(r"\bal gusto\b", text, re.I):
        return unknown
    token = match[1].casefold()
    amount = parse_text(("1/2" if token in ("medio", "media") else token) + " huevo")
    if amount.amount is None:
        return unknown
    rest = text[match.end():].strip()
    words = rest.split(maxsplit=1)
    if not words:
        return unknown
    first = words[0].casefold().rstrip(".")
    unit = _UNITS.get(first)
    if unit:
        food = words[1].strip() if len(words) == 2 else ""
        food = re.sub(r"^de\s+", "", food, flags=re.I)
    else:
        canonical = parse_text("1 " + first)
        if canonical.unit and canonical.dimension != "count":
            unit = canonical.unit
            food = words[1].strip() if len(words) == 2 else ""
            food = re.sub(r"^de\s+", "", food, flags=re.I)
        elif canonical.dimension == "count":
            food = rest
        else:
            return unknown
    # Reject compound quantities, punctuation and dangling connectors rather than guess.
    if not food or not re.fullmatch(r"[a-záéíóúüñ]+(?:[ '\-][a-záéíóúüñ]+)*", food, re.I):
        return unknown
    if food.casefold() in ("de", "del"):
        return unknown
    return Ingredient(quantity=float(amount.amount), unit=NamedRef(name=unit) if unit else None,
                      food=NamedRef(name=food), originalText=line)
