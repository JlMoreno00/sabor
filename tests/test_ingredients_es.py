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
