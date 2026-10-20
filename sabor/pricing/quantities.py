"""Pure conservative quantities. Base units: grams, millilitres and pieces.

Ranges stay unknown (no upper-bound policy). Bare numbers imply count only
for an allowlist of countable foods; unknown unit names never imply pieces.
API floats are decoded through str once; all arithmetic is Decimal.
"""
import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from decimal import Decimal, DecimalException
from typing import Literal

CanonicalUnit = Literal['g', 'kg', 'ml', 'cl', 'l', 'ud']
Dimension = Literal['mass', 'volume', 'count']


@dataclass(frozen=True)
class Quantity:
    amount: Decimal | None
    unit: CanonicalUnit | None
    dimension: Dimension | None
    source: Literal['structured', 'text', 'unknown']
    ambiguity: tuple[str, ...] = ()


@dataclass(frozen=True)
class PackageQuantity:
    quantity: Quantity
    package_price: Decimal | None
    reference_price: Decimal | None
    reference_unit: CanonicalUnit | None
    ambiguity: tuple[str, ...]


_ALIASES: dict[str, CanonicalUnit] = {}
for _canonical, _names in [('g', 'g gr gramo gramos gram grams'),
                      ('kg', 'kg kilo kilos kilogramo kilogramos kilogram kilograms'),
                      ('ml', 'ml mililitro mililitros milliliter milliliters'),
                      ('cl', 'cl centilitro centilitros'),
                      ('l', 'l litro litros liter liters litre litres'),
                      ('ud', 'ud uds u unidad unidades pieza piezas unit units piece pieces')]:
    for _name in _names.split():
        _ALIASES[_name] = _canonical  # type: ignore[assignment]
_DIMENSIONS: dict[CanonicalUnit, Dimension] = {
    'g': 'mass', 'kg': 'mass', 'ml': 'volume', 'cl': 'volume', 'l': 'volume', 'ud': 'count'}
_BASE: dict[Dimension, CanonicalUnit] = {'mass': 'g', 'volume': 'ml', 'count': 'ud'}
_FACTORS = dict(g=Decimal(1), kg=Decimal(1000), ml=Decimal(1),
                cl=Decimal(10), l=Decimal(1000), ud=Decimal(1))
_WORDS = dict(zip('un una uno dos tres cuatro cinco seis siete ocho nueve diez once doce one two three four five six seven eight nine ten'.split(),
                  (1, 1, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10)))
_FRACTIONS = {'½': '1/2', '¼': '1/4', '¾': '3/4', '⅓': '1/3', '⅔': '2/3', '⅛': '1/8'}
_KITCHEN = set('cucharada cucharadas cda cdas cucharadita cucharaditas cdta cdtas taza tazas cup cups tablespoon tablespoons tbsp teaspoon teaspoons tsp'.split())
_PACKAGES = set('lata latas bote botes paquete paquetes bolsa bolsas pack packs can cans tin tins'.split())
_COUNTABLE = set('huevo huevos cebolla cebollas tomate tomates patata patatas limón limones manzana manzanas egg eggs onion onions tomato tomatoes potato potatoes lemon lemons apple apples'.split())
_NUMBER = r'(?:\d+\s+\d+/\d+|\d+/\d+|\d+(?:[.,]\d+)?|[½¼¾⅓⅔⅛]|' + '|'.join(word + r'\b' for word in _WORDS) + r')'
_LEADING = re.compile(r'^(' + _NUMBER + r')(?=\s|[a-záéíóúñ]|$)', re.I)


def _decimal(value: object, *, api: bool = False) -> Decimal | None:
    if isinstance(value, bool) or not isinstance(value, (str, int, Decimal, float)):
        return None
    if isinstance(value, float) and not api:
        return None
    try:
        result = Decimal(str(value).replace(',', '.'))
        return result if result.is_finite() and result > 0 else None
    except (DecimalException, ValueError):
        return None


def _unknown(reason: str) -> Quantity:
    return Quantity(None, None, None, 'unknown', (reason,))


def _unit(value: object) -> CanonicalUnit | None:
    return _ALIASES.get(value.strip().lower().rstrip('.')) if isinstance(value, str) else None


def _quantity(amount: Decimal, unit_name: object, source: Literal['structured', 'text']) -> Quantity:
    name = unit_name.strip().lower().rstrip('.') if isinstance(unit_name, str) else ''
    if name in ('docena', 'docenas', 'dozen'):
        return Quantity(amount * Decimal(12), 'ud', 'count', source)
    unit = _unit(name)
    reason = 'kitchen_measure' if name in _KITCHEN else 'package' if name in _PACKAGES else 'unknown_unit'
    return Quantity(amount, unit, _DIMENSIONS[unit] if unit else None,
                    source, () if unit else (reason,))


def parse_text(text: object) -> Quantity:
    """Parse leading amounts only; embedded package weights are not ingredients."""
    if not isinstance(text, str) or len(text) > 4096:
        return _unknown('unparsed')
    text = text.strip().lower()
    if re.search(r'\b(?:pizca|pinch|al gusto|to taste)\b|\bc\s*/\s*s\b', text):
        return _unknown('unspecified')
    if re.match(r'^' + _NUMBER + r'\s*(?:[-–—]|a|to)\s*' + _NUMBER + r'\b', text):
        return _unknown('range')
    if text.startswith('-'):
        return _unknown('invalid_amount')
    match = _LEADING.match(text)
    if not match:
        return _unknown('unparsed')
    token = match[1]
    try:
        if token in _WORDS:
            amount = Decimal(_WORDS[token])
        else:
            token = _FRACTIONS.get(token, token)
            if '/' in token:
                parts = token.split()
                numerator, denominator = parts[-1].split('/')
                amount = Decimal(numerator) / Decimal(denominator)
                if len(parts) == 2:
                    amount += Decimal(parts[0])
            else:
                amount = Decimal(token.replace(',', '.'))
    except DecimalException:
        return _unknown('invalid_amount')
    if not amount.is_finite() or amount <= 0:
        return _unknown('invalid_amount')
    rest = text[match.end():].strip()
    name_match = re.match(r'([a-záéíóúñ]+)\.?', rest)
    name = name_match[1] if name_match else ''
    if name in _COUNTABLE or not rest:
        return _quantity(amount, 'ud', 'text')
    q = _quantity(amount, name, 'text')
    if q.dimension and re.search(r'\d', rest):
        return replace(q, unit=None, dimension=None, ambiguity=('compound',))
    return q


_CONNECTORS = {'de', 'del', 'of'}
_UNIT_WORDS = set('g gr gramo gramos kg kilo kilos kilogramo kilogramos ml cl l litro litros '
                  'mililitro mililitros ud uds unidad unidades u pieza piezas docena docenas'.split())


def ingredient_name(text: object) -> str:
    """Strip a leading amount, unit and connector from free text ('300 g de arroz').

    Returns the remaining words; amounts are parsed separately by parse_text.
    """
    if not isinstance(text, str):
        return ''
    rest = text.strip()
    match = _LEADING.match(rest.lower())
    if match:
        rest = rest[match.end():].strip()
    words = rest.split()
    if match and words and words[0].lower().rstrip('.') in _UNIT_WORDS | _KITCHEN | _PACKAGES:
        words = words[1:]
    while words and words[0].lower() in _CONNECTORS:
        words = words[1:]
    name = ' '.join(words)
    name = re.sub(r'\b(?:al gusto|to taste|c\s*/\s*s)\b', '', name, flags=re.I)
    return re.sub(r'\s+', ' ', name).strip()


def from_structured(quantity: object, unit_name: object, food_name: object,
                    original_text: object) -> Quantity:
    """Prefer structured data. Only missing/zero placeholders permit text fallback.

    food_name is retained in the calling ingredient DTO, never used to invent
    density or a piece weight here. Missing structured units remain unknown.
    """
    amount = _decimal(quantity, api=True)
    if amount is not None:
        return _quantity(amount, unit_name, 'structured')
    try:
        placeholder = (not isinstance(quantity, bool) and
                       isinstance(quantity, (str, int, float, Decimal)) and
                       Decimal(str(quantity).replace(',', '.')).is_zero())
    except (DecimalException, ValueError):
        placeholder = False
    if quantity is None or placeholder:
        return parse_text(original_text)
    return _unknown('invalid_amount')


def to_base(q: Quantity, *, target_dimension: Dimension | None = None,
            density: Decimal | None = None, piece_weight: Decimal | None = None) -> Quantity:
    """Density is g/ml; piece_weight is g/piece. No implicit cross-dimension rule."""
    if q.amount is None or q.unit is None or q.dimension is None:
        return q
    if _decimal(q.amount) is None or _DIMENSIONS.get(q.unit) != q.dimension:
        return _unknown('invalid_amount')
    target = target_dimension or q.dimension
    amount = q.amount * _FACTORS[q.unit]
    if target != q.dimension:
        pair = {target, q.dimension}
        factor = _decimal(density) if pair == {'mass', 'volume'} else (
            _decimal(piece_weight) if pair == {'mass', 'count'} else None)
        if factor is None:
            return replace(q, amount=None, unit=None, dimension=None,
                           ambiguity=tuple(dict.fromkeys((*q.ambiguity, 'dimension_mismatch'))))
        amount = amount * factor if target == 'mass' else amount / factor
    return replace(q, amount=amount, unit=_BASE[target], dimension=target)


def _package_unit(value: object) -> CanonicalUnit | None:
    name = value.lower() if isinstance(value, str) else ''
    return _unit(name) if name in ('g', 'kg', 'ml', 'l', 'ud') else None


def package_from_mercadona(price_instructions: object) -> PackageQuantity:
    """Decode observed API fields without reconciling packs or drained weights.

    Quantity is the reported nominal size, NOT a resolved multipack size.
    Callers must propagate ambiguity; no pack multiplication is performed.
    Reference price keeps its stated denominator (not automatically per gram).
    """
    data = price_instructions if isinstance(price_instructions, Mapping) else {}
    amount = _decimal(data.get('unit_size'), api=True)
    unit = _package_unit(data.get('size_format'))
    q = (_quantity(amount, unit, 'structured') if amount is not None
         else _unknown('invalid_amount'))
    reasons = list(q.ambiguity)
    reference_unit = _package_unit(data.get('reference_format'))
    if reference_unit is None and data.get('reference_format') is not None:
        reasons.append('unknown_unit')
    if not isinstance(price_instructions, Mapping):
        reasons.append('invalid_metadata')
    for field, reason in [('approx_size', 'approx_size'), ('is_pack', 'pack')]:
        value = data.get(field, False)
        if not isinstance(value, bool):
            reasons.append('invalid_metadata')
        elif value:
            reasons.append(reason)
    method = data.get('selling_method', 0)
    if type(method) is not int:
        reasons.append('invalid_metadata')
    elif method != 0:
        reasons.append('selling_method')
    for field, reason in [('pack_size', 'pack'), ('total_units', 'pack'),
                          ('drained_weight', 'drained_weight')]:
        if data.get(field) is not None:
            reasons.append(reason)
            if _decimal(data[field], api=True) is None:
                reasons.append('invalid_metadata')
    return PackageQuantity(q, _decimal(data.get('unit_price'), api=True),
                           _decimal(data.get('reference_price'), api=True),
                           reference_unit, tuple(dict.fromkeys(reasons)))
