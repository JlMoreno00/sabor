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

    Numbers stay: ``chocolate negro 85%`` must not match ``Chocolate negro 70%``. A
    decimal is one value, comma or point alike (``0,5`` is ``0.5``, never ``5``), and an
    alphanumeric word stays whole (``B12`` is neither ``B`` nor ``12``).
    """
    return set(_tokens(text))


def _tokens(text):
    # The words of ``words`` in order, so a number can be read with the word after it.
    plain = ''.join(c for c in unicodedata.normalize('NFKD', text.casefold())
                    if not unicodedata.combining(c))
    tokens = re.findall(r'\d+(?:[.,]\d+)?(?![^\W_])|[^\W_]+', plain)
    return [_number(t) if re.fullmatch(r'\d+(?:[.,]\d+)?', t) else t for t in tokens
            if t not in ('de', 'the', 'of')]


def _number(token):
    value = token.replace(',', '.')
    return value.rstrip('0').rstrip('.') if '.' in value else value


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


def carried(number, query, name):
    """True when the product carries the query's number with the word it modifies.

    ``4 quesos`` needs a 4 followed by quesos (``Pizza 4 quesos``), never the 4 of
    ``Pizza 3 quesos pack 4 unidades``; ``4 quesos y 4 carnes`` needs both. A number with
    no word after it in the query (``chocolate 85%``) only needs to appear.
    """
    wanted, product = _tokens(query), _tokens(name)
    modified = {wanted[i + 1] for i, t in enumerate(wanted[:-1]) if t == number}
    if not modified:
        return number in product
    return all(any(t == number and same_word(product[i + 1], w) for i, t in enumerate(product[:-1]))
               for w in modified)


def processed(name, query):
    """Processed/compound words in the product name that the query did not ask for."""
    wanted = words(query)
    return sum(1 for p in words(name)
               if not any(same_word(p, w) for w in wanted) and any(same_word(p, x) for x in PROCESSED))
