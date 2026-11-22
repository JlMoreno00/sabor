"""Supermarket receipt lines to pantry candidates (#144).

The client agent reads the receipt (photo or text) and passes its lines; Sabor never
calls an AI provider. Here each line loses its prices, keeps its pack size, weight or
count, and has common Spanish receipt abbreviations expanded. Unknown abbreviations are
kept as written, never guessed.
"""
import re
from dataclasses import dataclass
from decimal import Decimal

from .model import BaseUnit, food_key, to_base_quantity

# Words a receipt uses for lines that are not food bought.
_NOT_FOOD = {'total', 'subtotal', 'iva', 'dto', 'descuento', 'bolsa', 'tarjeta', 'efectivo',
             'cambio', 'entregado', 'ahorro', 'cupon', 'promocion', 'redondeo', 'base', 'importe'}
# Abbreviation -> word, for receipts of the main Spanish supermarkets.
_ABBREVIATIONS = {
    'aceit': 'aceite', 'oliv': 'oliva', 'virg': 'virgen', 'ext': 'extra', 'semi': 'semidesnatada',
    'desn': 'desnatada', 'ent': 'entera', 'yog': 'yogur', 'pech': 'pechuga', 'poll': 'pollo',
    'tom': 'tomate', 'qso': 'queso', 'huev': 'huevos', 'ceb': 'cebolla', 'zanah': 'zanahoria',
    'mantq': 'mantequilla', 'nat': 'natural', 'cong': 'congelado', 'jam': 'jamón',
}
_UNITS = {'kg': 'kg', 'g': 'g', 'gr': 'g', 'l': 'l', 'lt': 'l', 'ml': 'ml', 'cl': 'cl',
          'u': 'ud', 'ud': 'ud', 'uds': 'ud', 'unid': 'ud'}
_NUMBER = r'\d+(?:[.,]\d+)?'
_UNIT = r'(kg|gr|g|lt|l|ml|cl|uds|ud|unid|u)'
_PRICE = re.compile(r'\s+-?\d+[.,]\d{2}\s*€?$|^-?\d+[.,]\d{2}\s*€?$')
_WEIGHED = re.compile(r'(' + _NUMBER + r')\s*kg\s*x\s*' + _NUMBER + r'\s*€?\s*/\s*kg', re.I)
_MULTIPACK = re.compile(r'(?<![\w,.])(\d+)\s*x\s*(' + _NUMBER + r')\s*' + _UNIT + r'(?!\w)', re.I)
_SIZE = re.compile(r'(?<![\w,.])(' + _NUMBER + r')\s*' + _UNIT + r'(?!\w)', re.I)
_COUNT = re.compile(r'^(\d+)\s+(?=[^\d\s])')


@dataclass(frozen=True)
class ReceiptLine:
    raw: str
    name: str
    food: str
    quantity: Decimal | None
    unit: BaseUnit | None
    ignored: str | None = None
    uncertain: tuple[str, ...] = ()


def _ignored(raw: str, reason: str) -> ReceiptLine:
    return ReceiptLine(raw, '', '', None, None, reason)


def _amount(text: str) -> Decimal:
    return Decimal(text.replace(',', '.'))


def parse_receipt_line(line: str) -> ReceiptLine:
    raw = line if isinstance(line, str) else ''
    text = ' '.join(raw.split())
    text = _WEIGHED.sub(lambda m: f'{m[1]} kg', text)
    while (stripped := _PRICE.sub('', text)) != text:
        text = stripped.strip()
    if not text:
        return _ignored(raw, 'empty')
    if food_key(text.split()[0]).strip('.:') in _NOT_FOOD:
        return _ignored(raw, 'not_food')
    count = None
    if match := _COUNT.match(text):
        count, text = int(match[1]), text[match.end():]
    size = None
    if match := _MULTIPACK.search(text):
        size = (_amount(match[1]) * _amount(match[2]), _UNITS[match[3].lower()])
    elif match := _SIZE.search(text):
        size = (_amount(match[1]), _UNITS[match[2].lower()])
    if match:
        text = (text[:match.start()] + text[match.end():]).strip()
    words = [_ABBREVIATIONS.get(food_key(w).rstrip('.'), w.lower().rstrip('.')) for w in text.split()]
    name = ' '.join(words)
    if not name:
        return _ignored(raw, 'empty')
    quantity, unit, uncertain = None, None, ()
    if size and (base := to_base_quantity(size[0], size[1])):
        quantity, unit = base[0] * (count or 1), base[1]
    elif count:
        quantity, unit = Decimal(count), 'ud'
    else:
        uncertain = ('quantity_unknown',)
    return ReceiptLine(raw, name, food_key(name), quantity, unit, None, uncertain)
