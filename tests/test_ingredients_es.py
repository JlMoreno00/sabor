"""Conservative Spanish import parsing; source text is never normalized."""
import pytest

from sabor.importing.ingredients_es import parse_ingredient


@pytest.mark.parametrize("line,quantity,unit,food", [
    ("2 dientes de ajo", 2, "diente", "ajo"),
    ("200 g de harina de trigo", 200, "gramo", "harina de trigo"),
    ("una pizca de sal", 1, "pizca", "sal"),
    ("0,5 kg de azúcar", .5, "kilogramo", "azúcar"),
    ("½ cebolla", .5, None, "cebolla"),
    ("1/2 cebolla", .5, None, "cebolla"),
    ("media cebolla", .5, None, "cebolla"),
    ("medio litro de leche", .5, "litro", "leche"),
    ("un huevo", 1, None, "huevo"),
    ("2 cda de aceite", 2, "cucharada", "aceite"),
    ("1 cdta de sal", 1, "cucharadita", "sal"),
    ("20 ml de leche", 20, "mililitro", "leche"),
    ("  2 dientes de ajo  ", 2, "diente", "ajo"),
    ("sal al gusto", None, None, None),
    ("2-3 huevos", None, None, None),
    ("2 a 3 huevos", None, None, None),
    ("2 medidas de harina", None, None, None),
    ("2 harina", None, None, None),
    ("", None, None, None),
    ("   ", None, None, None),
    ("2 g " + "a" * 4096, None, None, None),
    ("1/0 cebolla", None, None, None),
    ("2 g", None, None, None),
    ("2 g de sal al gusto", None, None, None),
])
def test_parse_ingredient(line, quantity, unit, food):
    result = parse_ingredient(line)
    assert result.quantity == quantity
    assert (result.unit.name if result.unit else None) == unit
    assert (result.food.name if result.food else None) == food
    assert result.originalText == line
    if quantity is None:
        assert result.note == line


DUBIOUS = "cantidad dudosa: línea con varios ingredientes"


def _rows(line):
    from sabor.importing.ingredients_es import parse_ingredients
    return [(r.quantity, r.unit.name if r.unit else None, r.food.name if r.food else None, r.note)
            for r in parse_ingredients(line)]


@pytest.mark.parametrize("line,rows", [
    # #49 decision: enumerations split; a shared amount is flagged as dubious.
    ("2 g de harina y azúcar", [(2, "gramo", "harina", DUBIOUS), (2, "gramo", "azúcar", DUBIOUS)]),
    ("1 pizca de sal y pimienta", [(1, "pizca", "sal", DUBIOUS), (1, "pizca", "pimienta", DUBIOUS)]),
    ("200 g de harina, azúcar y mantequilla", [(200, "gramo", f, DUBIOUS)
                                               for f in ("harina", "azúcar", "mantequilla")]),
    ("2 cda de aceite de oliva o girasol", [(2, "cucharada", "aceite de oliva", DUBIOUS),
                                            (2, "cucharada", "girasol", DUBIOUS)]),
    ("1 cdta de comino e hinojo", [(1, "cucharadita", "comino", DUBIOUS),
                                   (1, "cucharadita", "hinojo", DUBIOUS)]),
    # Each part with its own amount keeps it and is not dubious.
    ("2 huevos y 200 g de harina", [(2, None, "huevos", None), (200, "gramo", "harina", None)]),
    # Mixed lines: a part without its own amount gets the food only, flagged.
    ("2 huevos, 1 litro de leche y sal", [(2, None, "huevos", None), (1, "litro", "leche", None),
                                         (None, None, "sal", DUBIOUS)]),
    # A decimal comma is part of the amount, not a separator.
    ("0,5 kg de azúcar y harina", [(.5, "kilogramo", "azúcar", DUBIOUS), (.5, "kilogramo", "harina", DUBIOUS)]),
    ("1,5 kg de harina, azúcar", [(1.5, "kilogramo", "harina", DUBIOUS), (1.5, "kilogramo", "azúcar", DUBIOUS)]),
])
def test_enumerations_split(line, rows):
    from sabor.importing.ingredients_es import parse_ingredients
    assert _rows(line) == rows
    assert all(r.originalText == line for r in parse_ingredients(line))


@pytest.mark.parametrize("line", [
    # Compound names and preparations are not enumerations.
    "200 g de macarrones con queso", "2 cda de salsa agridulce", "1 cebolla, picada",
    "2 tomates, pelados y picados", "1 litro de leche entera o desnatada",
    "1 cebolla, picada en dados", "2 tomates, cortados en cubos", "1 cebolla, picada fina",
    "1 pizca de pimienta, blanca molida",
    "1 pizca de pimienta blanca o negra", "2 latas de tomate triturado", "1 cebolla en dados",
    "1 kg y medio de patatas", "2 huevos y 200 g",
    # Unchanged conservative cases.
    "sal y pimienta al gusto", "2 g de sal y pimienta al gusto", "sal y pimienta", "2-3 huevos", "",
])
def test_non_enumerations_match_single_parse(line):
    from sabor.importing.ingredients_es import parse_ingredients
    single = parse_ingredient(line)
    assert parse_ingredients(line) == (single,)
