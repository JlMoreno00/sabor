"""Supermarket receipt lines become pantry candidates without any AI provider (#144)."""
from decimal import Decimal

import pytest

from sabor.pantry.receipt import parse_receipt_line


@pytest.mark.parametrize('line, name, food, quantity, unit', [
    # Pack size times a leading count, prices stripped.
    ('2 LECHE SEMI 1L 0,89 1,78', 'leche semidesnatada', 'leche semidesnatada', Decimal('2000'), 'ml'),
    ('ARROZ BOMBA 1KG 2,35', 'arroz bomba', 'arroz bomba', Decimal('1000'), 'g'),
    ('ACEIT OLIV VIRG EXT 750ML 6,95€', 'aceite oliva virgen extra', 'aceite oliva virgen extra',
     Decimal('750'), 'ml'),
    # Multipacks: 6 x 125 g.
    ('YOG NATURAL 6X125G 1,20', 'yogur natural', 'yogur natural', Decimal('750'), 'g'),
    # Weighed produce: the weight is the quantity, the price per kilo is dropped.
    ('TOMATE PERA 0,756 kg x 2,49 €/kg 1,88', 'tomate pera', 'tomate pera', Decimal('756.000'), 'g'),
    # Units and dozens.
    ('HUEVOS L 12U 2,60', 'huevos l', 'huevos l', Decimal('12'), 'ud'),
    ('3 PECH POLL 0,99', 'pechuga pollo', 'pechuga pollo', Decimal('3'), 'ud'),
])
def test_food_lines_are_normalized(line, name, food, quantity, unit):
    result = parse_receipt_line(line)
    assert result.ignored is None
    assert (result.name, result.food, result.quantity, result.unit) == (name, food, quantity, unit)
    assert result.raw == line


def test_line_without_a_size_keeps_quantity_unknown():
    result = parse_receipt_line('PAN BARRA 0,60')
    assert (result.name, result.quantity, result.unit) == ('pan barra', None, None)
    assert 'quantity_unknown' in result.uncertain


@pytest.mark.parametrize('line, reason', [
    ('TOTAL 23,45', 'not_food'),
    ('IVA 10% 1,23', 'not_food'),
    ('DTO. 2ª UNIDAD -0,45', 'not_food'),
    ('BOLSA PLASTICO 0,15', 'not_food'),
    ('TARJETA BANCARIA 23,45', 'not_food'),
    ('', 'empty'),
    ('   1,25  ', 'empty'),
])
def test_non_food_lines_are_ignored_with_a_reason(line, reason):
    assert parse_receipt_line(line).ignored == reason


def test_unknown_abbreviations_are_kept_as_written():
    # Only known abbreviations are expanded; others are never guessed.
    result = parse_receipt_line('ZUMO MANZ 1L 1,10')
    assert result.name == 'zumo manz'
    assert result.quantity == Decimal('1000') and result.unit == 'ml'


def test_food_key_ignores_accents_and_case():
    assert parse_receipt_line('LIMÓN 1KG').food == parse_receipt_line('limon 1kg').food == 'limon'
