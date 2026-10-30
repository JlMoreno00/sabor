"""Conservative product-name matching shared by pricing providers and the estimate engine.

Whole words only, with one trailing ``s``/``es`` tolerated on either side (the pantry
rule): ``zanahoria`` matches ``Zanahorias`` but ``sal`` never matches ``salsa``.
"""
import re
import unicodedata

# Words that turn a basic ingredient into a processed or compound product
# ("Croquetas de merluza", "Chicles de jengibre"). Singular, without accents;
# plurals match through ``same_word``. A word the query asks for is never penalized.
PROCESSED = frozenset('''
    aroma barrita batido bebida bizcocho burger cacao caldo caramelo chicle chip chocolate
    comida condensada condensado crema croqueta elaborado empanada empanadilla empanado
    figurita finger galleta gragea hamburguesa helado infusion jardinera lasana mermelada
    molida molido nugget palito papilla pastel pate pizza polvo postre pure rebozada rebozado relleno
    sabor salsa sandwich shot snack sopa tarta yogur zumo
'''.split())


def words(text):
    """Casefolded, accent-free words and numbers, without the connectors de/the/of.

    Numbers stay: ``chocolate negro 85%`` must not match ``Chocolate negro 70%``.
    """
    plain = ''.join(c for c in unicodedata.normalize('NFKD', text.casefold())
                    if not unicodedata.combining(c))
    return set(re.findall(r'[^\W_]+', plain)) - {'de', 'the', 'of'}


def _forms(word):
    forms = {word}
    for suffix in ('es', 's'):
        if word.endswith(suffix) and len(word) > len(suffix):
            forms.add(word[:-len(suffix)])
    return forms


def same_word(a, b):
    return a == b or a in _forms(b) or b in _forms(a)


def covers(query, name):
    """True when every query word appears as a whole word of the product name."""
    product = words(name)
    wanted = words(query)
    return bool(wanted) and all(any(same_word(w, p) for p in product) for w in wanted)


def processed(name, query):
    """Processed/compound words in the product name that the query did not ask for."""
    wanted = words(query)
    return sum(1 for p in words(name)
               if not any(same_word(p, w) for w in wanted) and any(same_word(p, x) for x in PROCESSED))
