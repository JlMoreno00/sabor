"""Conservative Spanish ingredient lines, preserving the untouched source.

Bare amounts require a countable food recognized by quantities.parse_text;
otherwise an explicit catalog/canonical unit and a food name are required.
"""
from dataclasses import replace
import json
import re
from types import MappingProxyType

from sabor.core.paths import catalog_dir
from sabor.pricing.quantities import parse_text
from sabor.store import Ingredient, NamedRef


def _load_units():
    catalog = catalog_dir() / "spanish_units.json"
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


DUBIOUS_NOTE = "cantidad dudosa: línea con varios ingredientes"
_FOOD = re.compile(r"[a-záéíóúüñ]+(?:[ '\-][a-záéíóúüñ]+)*", re.I)
# Separators between foods: commas (never a decimal comma such as '0,5') and whole-word
# conjunctions (so 'agridulce' never splits).
_SEPARATOR = re.compile(r"\s*(?:,(?!\d)|(?<!\d),)\s*|\s+(?:y|e|o|u|and|or)\s+", re.I)
# A first word after a separator that describes the previous food, not a new one.
_DESCRIPTORS = frozenset(
    "blanco blanca blancos blancas negro negra negros negras rojo roja rojos rojas verde verdes "
    "amarillo amarilla amarillos amarillas grande grandes pequeño pequeña pequeños pequeñas "
    "mediano mediana medianos medianas entero entera enteros enteras fresco fresca frescos frescas "
    "seco seca secos secas frío fría fríos frías caliente calientes tibio tibia templado templada "
    "fino fina gordo gorda dulce dulces suave suaves fuerte picante maduro madura crudo cruda "
    "medio media ácido ácida".split())
# Foods ending like participles ('-ado', '-ida'...) that are still foods.
_PARTICIPLE_FOODS = frozenset(
    "pescado pescados helado helados granada granadas ensalada mermelada limonada empanada "
    "empanadas cuajada tostada tostadas paté bizcochada bebida bebidas".split())
# Foods of several words whose first word is also a descriptor.
_FOOD_PHRASES = frozenset({"dulce de leche"})
_LEADING_WORDS = frozenset("en a al para sin con de del muy bien".split())


def _bare_food(text):
    """A separated part without an amount that names a food on its own."""
    words = text.split()
    if not words or not _FOOD.fullmatch(text) or words[0].casefold() in _LEADING_WORDS:
        return False
    if text.casefold() in _FOOD_PHRASES:
        return True
    # A part opening with a descriptor or a participle is a preparation of the previous
    # food ('picada', 'picada en dados', 'cortados en cubos'), whatever its length.
    word = words[0].casefold()
    if word in _DESCRIPTORS:
        return False
    if re.search(r"(?:ado|ada|ados|adas|ido|ida|idos|idas)$", word) and word not in _PARTICIPLE_FOODS:
        return False
    return True


def parse_ingredients(line: str) -> tuple[Ingredient, ...]:
    """Split enumerations ('2 g de harina y azúcar') into one Ingredient per food.

    With a single amount every food shares it and carries DUBIOUS_NOTE; parts with
    their own amount keep it. A part without one in a line with several amounts
    gets the food only, flagged. Anything that is not clearly a list of foods
    (compound names, preparations, ranges, 'al gusto') returns the single parse.
    """
    single = parse_ingredient(line)
    if len(line) > 4096 or re.search(r"\bal gusto\b", line, re.I):
        return (single,)
    parts = [part.strip() for part in _SEPARATOR.split(line.strip())]
    if not 2 <= len(parts) <= 6 or not all(parts):
        return (single,)
    parsed = [parse_ingredient(part) for part in parts]
    if parsed[0].food is None:
        return (single,)
    for part, own in zip(parts[1:], parsed[1:]):
        if own.food is None and not _bare_food(part):
            return (single,)
    shared = sum(own.food is not None for own in parsed) == 1
    rows = []
    for part, own in zip(parts, parsed):
        if own.food is not None:
            rows.append(replace(own, originalText=line, note=DUBIOUS_NOTE if shared else None))
        elif shared:
            rows.append(replace(parsed[0], food=NamedRef(name=part), originalText=line, note=DUBIOUS_NOTE))
        else:
            rows.append(Ingredient(food=NamedRef(name=part), originalText=line, note=DUBIOUS_NOTE))
    return tuple(rows)


def normalize_row(row: Ingredient) -> tuple[Ingredient, ...] | None:
    """Structured rows for one unstructured import row, or None to keep it unchanged.

    Source metadata is kept on every row; only the first keeps the referenceId.
    A dubious-amount note is appended to any existing note.
    """
    parsed = parse_ingredients(row.originalText or "")
    if parsed[0].food is None:
        return None
    rows = []
    for index, item in enumerate(parsed):
        note = row.note
        if item.note:
            note = f"{row.note}; {item.note}" if row.note else item.note
        rows.append(replace(row, quantity=item.quantity, food=item.food, unit=item.unit, note=note,
                            referenceId=row.referenceId if index == 0 else None))
    return tuple(rows)
