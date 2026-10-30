"""Read-only Decimal costing. Totals are partial, never missing-price zeroes."""
import re
from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_CEILING
from .provider import RetailContext, ProductQuery, PriceQuote
from .quantities import from_structured, ingredient_name, to_base
from .matching import covers, processed
from .pantry import match_pantry
from sabor.store import DateRange


@dataclass(frozen=True)
class Uncertainty:
    level: str
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class CostLine:
    key: str
    name: str
    required_quantity: Decimal | None
    base_unit: str | None
    consumption: Decimal | None
    quote: PriceQuote | None
    candidates: tuple[str, ...]
    uncertainty: Uncertainty
    provenance: tuple[str, ...]
    match_evidence: tuple[str, ...]
    alternatives: tuple[dict, ...] = ()


@dataclass(frozen=True)
class CostEstimate:
    lines: tuple[CostLine, ...]
    consumption_subtotal: Decimal | None
    procurement_subtotal: Decimal | None
    currency: str | None
    complete: bool
    coverage: dict[str, int]
    excluded_rows: tuple[CostLine, ...]


def _number(value):
    if isinstance(value, (bool, float)):
        raise ValueError('Decimal operands required')
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError('invalid amount') from None
    if not result.is_finite() or result < 0:
        raise ValueError('invalid amount')
    return result


def _resolve(source, store):
    shapes = {'items': {'items'}, 'shopping_list': {'list_id'},
              'recipe': {'slug', 'multiplier'},
              'mealplan': {'start_date', 'end_date', 'multiplier'}}
    if not isinstance(source, dict) or source.get('kind') not in shapes:
        raise ValueError('invalid source')
    kind = source['kind']
    required = shapes[kind] - {'multiplier'}
    if type(source.get('exclude_pantry', False)) is not bool:
        raise ValueError('invalid pantry option')
    if source.keys() - (shapes[kind] | {'kind', 'exclude_pantry'}) or not required <= source.keys():
        raise ValueError('mixed or incomplete source')
    multiplier = _number(source.get('multiplier', 1))
    if not 0 < multiplier <= 20:
        raise ValueError('invalid multiplier')
    if kind == 'mealplan':
        start, end = (date.fromisoformat(source[k]) for k in ('start_date', 'end_date'))
        if not 0 <= (end - start).days < 31:
            raise ValueError('invalid date range')
    for key in required - {'items', 'start_date', 'end_date'}:
        if not isinstance(source[key], str) or not source[key].strip():
            raise ValueError('invalid identifier')
    rows = []
    def add(key, item, scale=multiplier):
        if isinstance(item, dict):
            if item.keys() - {'name', 'quantity', 'unit', 'text'} or not isinstance(item.get('name'), str) or not item['name'].strip():
                raise ValueError('invalid inline item')
            row = dict(item)
        elif item.food is None and item.unit is None:
            # Free-text item (for example a note-only Mealie shopping item): its
            # quantity is a placeholder; amount and name come from the text.
            text = getattr(item, 'originalText', None) or item.note or item.display or ''
            row = dict(name=ingredient_name(text) or text, quantity=None, unit=None, text=text)
        else:
            text = getattr(item, 'originalText', None) or item.display or item.note
            # Without a structured food, built-in stores derive the name from the text like
            # free-text rows; Mealie keeps its previous empty name (Beta A, #41).
            fallback = ingredient_name(text) if getattr(store, 'id', None) != 'mealie' else ''
            row = dict(name=(item.food.name if item.food else None) or item.display or item.note
                       or fallback or '',
                       quantity=item.quantity, unit=item.unit.name if item.unit else None, text=text)
        amount = row.get('quantity')
        # Store DTOs carry API floats; decode once at this boundary only.
        if amount is not None:
            row['quantity'] = _number(str(amount) if not isinstance(item, dict) else amount)
        rows.append((key, row, scale))
        if len(rows) > 500:
            raise ValueError('too many rows')
    def recipe(slug, prefix):
        for i, ingredient in enumerate(store.get_recipe(slug).recipeIngredient or ()):
            add(f'{prefix}:{i}', ingredient)
    if kind == 'items':
        if not isinstance(source['items'], list) or len(source['items']) > 500:
            raise ValueError('invalid items')
        for i, item in enumerate(source['items']):
            if not isinstance(item, dict):
                raise ValueError('invalid item')
            add(f'items:{i}', item)
    else:
        if store is None:
            raise ValueError('recipe store required')
        if kind == 'recipe':
            recipe(source['slug'], 'recipe:' + source['slug'])
        elif kind == 'shopping_list':
            for item in store.get_shopping_items(source['list_id']).items:
                add(f'list:{source["list_id"]}:{item.id}', item)
        else:
            for entry in store.get_mealplan(DateRange(source['start_date'], source['end_date'])):
                prefix = f'meal:{entry.id}'
                if entry.recipe_slug:
                    recipe(entry.recipe_slug, prefix)
                else:
                    add(prefix, dict(name=entry.title or 'Text-only meal', text=entry.title))
    return rows


def _words(text):
    return set(re.findall(r'[^\W\d_]+', text.casefold())) - {'de', 'the', 'of'}


def _checkout_cost(required, unit, quote):
    """Price of the whole packages needed to cover `required` (base units)."""
    if {'kg': 'g', 'l': 'ml', 'unit': 'ud'}.get(quote.package_unit) != unit:
        return None
    size, price = quote.package_quantity, quote.package_price
    if not (isinstance(size, Decimal) and isinstance(price, Decimal) and size.is_finite()
            and price.is_finite() and size > 0 and price >= 0):
        return None
    factor = Decimal(1) if unit == 'ud' else Decimal(1000)
    return (required / factor / size).to_integral_value(rounding=ROUND_CEILING) * price


class _SnapshotPricing:
    """Per-call cache: every trial selection sees the same quotes, with no extra reads."""

    def __init__(self, pricing):
        self._pricing, self.id = pricing, pricing.id
        self._searches, self._quotes = {}, {}

    def search_products(self, query, store):
        key = (query.query, query.limit)
        if key not in self._searches:
            self._searches[key] = list(self._pricing.search_products(query, store))
        return self._searches[key]

    def get_quote(self, product_id, store):
        if product_id not in self._quotes:
            self._quotes[product_id] = self._pricing.get_quote(product_id, store)
        return self._quotes[product_id]


def estimate(source, store_ctx: RetailContext, recipe_store, pricing, product_overrides=None,
             match_policy='cheapest', pantry=()) -> CostEstimate:
    if match_policy not in ('cheapest', 'strict'):
        raise ValueError('invalid match policy')
    pricing = _SnapshotPricing(pricing)
    rows = _resolve(source, recipe_store)
    result = _estimate(source, store_ctx, rows, pricing, product_overrides, match_policy, pantry)
    if match_policy != 'cheapest':
        return result
    overrides = dict(product_overrides or {})
    selection_groups = {}
    for line in result.lines:
        if line.key not in overrides and line.required_quantity is not None and line.quote is not None:
            identity = (frozenset(_words(line.name)), line.base_unit)
            selection_groups.setdefault(identity, []).append(line)
    for members in selection_groups.values():
        if len(members) < 2:
            continue
        required = sum(line.required_quantity for line in members)
        unit = members[0].base_unit
        common = set(members[0].candidates).intersection(*(line.candidates for line in members[1:]))
        comparable = []
        for pid in common:
            quote = pricing.get_quote(pid, store_ctx)
            checkout = _checkout_cost(required, unit, quote)
            price = quote.normalized_unit_price
            if (quote.food is not False and checkout is not None and isinstance(price, Decimal)
                    and price.is_finite() and price >= 0):
                comparable.append((checkout, price, quote.product_name, pid))
        if not comparable:
            continue
        comparable.sort()
        amounts = {}
        for line in members:
            pid = line.quote.product_id
            amounts[pid] = amounts.get(pid, Decimal(0)) + line.required_quantity
        baseline = Decimal(0)
        for pid, amount in amounts.items():
            checkout = _checkout_cost(amount, unit, next(line.quote for line in members
                                                        if line.quote.product_id == pid))
            if checkout is None:
                break
            baseline += checkout
        else:
            checkout = baseline
        if checkout is None or comparable[0][0] >= baseline:
            continue
        selected = comparable[0][3]
        changed = {line.key for line in members if line.quote.product_id != selected}
        trial_overrides = dict(overrides, **{key: selected for key in changed})
        trial = _estimate(source, store_ctx, rows, pricing, trial_overrides, match_policy, pantry)
        # Products may also be shared outside this group: protect the whole basket.
        if (trial.procurement_subtotal is None or result.procurement_subtotal is None
                or trial.procurement_subtotal > result.procurement_subtotal):
            continue
        previous = {line.key: line for line in result.lines}
        alternatives = tuple(dict(product_id=pid, name=name, normalized_unit_price=price,
                                  checkout_cost=checkout)
                             for checkout, price, name, pid in comparable[1:6])
        merged = []
        for line in trial.lines:
            old = previous[line.key]
            if line.key not in changed:
                merged.append(old)
                continue
            reasons = tuple(dict.fromkeys((*line.uncertainty.reasons,
                                           'auto_selected_cheapest', 'auto_selected_cheapest_group')))
            merged.append(replace(line, candidates=old.candidates,
                                  uncertainty=Uncertainty('estimated', reasons),
                                  match_evidence=(*old.match_evidence, 'grouped_selection'),
                                  alternatives=alternatives))
        result = replace(trial, lines=tuple(merged),
                         complete=bool(merged) and all(line.uncertainty.level == 'known' for line in merged),
                         excluded_rows=tuple(line for line in merged if line.consumption is None))
        overrides = trial_overrides
    return result


def _estimate(source, store_ctx, rows, pricing, product_overrides, match_policy, pantry):
    if match_policy not in ('cheapest', 'strict'):
        raise ValueError('invalid match policy')
    overrides = product_overrides or {}
    if not isinstance(overrides, dict) or overrides.keys() - {r[0] for r in rows} or any(
            not isinstance(v, str) or not v.strip() for v in overrides.values()):
        raise ValueError('invalid overrides')
    if store_ctx.provider != pricing.id:
        raise ValueError('provider mismatch')
    quantities = [to_base(from_structured(row.get('quantity'), row.get('unit'), row['name'], row.get('text')))
                  for _, row, _ in rows]
    if any('invalid_amount' in q.ambiguity for q in quantities):
        raise ValueError('invalid amount')
    lines, groups = [], {}
    currency, location = store_ctx.currency, store_ctx.location_id
    consumption, quote_scope = None, None
    for (key, row, multiplier), q in zip(rows, quantities):
        if q.amount is not None and not row.get('unit') and q.ambiguity == ('unknown_unit',):
            q = replace(q, unit='ud', dimension='count', ambiguity=('assumed_count',))
        if q.source == 'text' and q.dimension == 'count' and _words(row.get('text') or '') == _words(row['name']):
            q = replace(q, ambiguity=tuple(dict.fromkeys((*q.ambiguity, 'assumed_count'))))
        required = q.amount * multiplier if q.amount is not None and q.dimension else None
        reasons, evidence = list(q.ambiguity), [f'quantity:{q.source}']
        candidates, quote, cost = [], None, None
        comparable, alternatives = [], ()
        if source.get('exclude_pantry') and match_pantry(row['name'], pantry) is not None:
            lines.append(CostLine(key, row['name'], required, q.unit, None, None, (),
                                  Uncertainty('unknown', ('in_pantry',)), (key,), tuple(evidence)))
            continue
        if required is not None:
            if key in overrides:
                candidates = [overrides[key]]
                evidence.append('caller_override')
            else:
                compound = {}
                # A leading amount ('2 huevos') is not a number the product must carry.
                wanted = ingredient_name(row['name']) or row['name']
                for product in pricing.search_products(ProductQuery(row['name'], 50), store_ctx):
                    if product.food is not False and covers(wanted, product.name):
                        candidate_quote = pricing.get_quote(product.id, store_ctx)
                        dimension_unit = {'kg': 'g', 'l': 'ml', 'unit': 'ud'}.get(candidate_quote.package_unit)
                        if candidate_quote.food is not False and dimension_unit in (None, q.unit):
                            candidates.append(product.id)
                            compound[product.id] = processed(product.name, wanted) > 0
                            price = candidate_quote.normalized_unit_price
                            checkout = _checkout_cost(required, q.unit, candidate_quote)
                            if (dimension_unit == q.unit and checkout is not None and isinstance(price, Decimal)
                                    and price.is_finite() and price >= 0):
                                comparable.append((checkout, price, product.name, product.id))
                candidates = list(dict.fromkeys(candidates))
                basic = [pid for pid in candidates if not compound[pid]]
                basic_comparable = [row for row in comparable if not compound[row[3]]]
                if basic_comparable or (basic and not comparable):
                    # A basic ingredient prefers unprocessed products: croquetas or chewing
                    # gum only when nothing else matches, or nothing else can be valued.
                    candidates, comparable = basic, basic_comparable
                evidence.append('whole_word')
            if len(candidates) > 1 and match_policy == 'cheapest' and key not in overrides and comparable:
                # Cheapest = lowest checkout cost for the required quantity (whole
                # packages), then lowest unit price; per line, before grouping.
                comparable.sort()
                selected = comparable[0][3]
                alternatives = tuple(dict(product_id=pid, name=name, normalized_unit_price=price,
                                          checkout_cost=checkout)
                                     for checkout, price, name, pid in comparable[1:6])
                reasons.append('auto_selected_cheapest')
            else:
                selected = candidates[0] if len(candidates) == 1 else None
            if selected is not None:
                quote = pricing.get_quote(selected, store_ctx)
                if quote.food is False:
                    reasons.append('non_food')
                    quote = None
                    lines.append(CostLine(key, row['name'], required, q.unit, None, None, tuple(candidates),
                                          Uncertainty('unknown', tuple(reasons)), (key,), tuple(evidence)))
                    continue
                if quote.product_id != selected or quote.location.provider != store_ctx.provider:
                    raise ValueError('quote identity mismatch')
                if (currency and currency != quote.currency) or (location and location != quote.location.location_id):
                    raise ValueError('incompatible currency or location')
                if quote.location.currency not in (None, quote.currency):
                    raise ValueError('incompatible quote currency')
                scope = (quote.currency, quote.location.location_id)
                if quote_scope is not None and scope != quote_scope:
                    raise ValueError('incompatible quote scopes')
                quote_scope = scope
                currency, location = scope
                for value in (quote.package_price, quote.package_quantity, quote.normalized_unit_price):
                    if value is not None and (not isinstance(value, Decimal) or not value.is_finite() or value < 0):
                        raise ValueError('invalid quote operand')
                reasons.extend(quote.uncertainty_reasons)
                if quote.availability is not True:
                    reasons.append('availability_unknown' if quote.availability is None else 'unavailable')
                unit = {'kg': 'g', 'l': 'ml', 'unit': 'ud'}.get(quote.package_unit)
                if unit != q.unit:
                    reasons.append('dimension_mismatch')
                elif quote.package_quantity is None or quote.package_quantity <= 0:
                    reasons.append('package_only')
                else:
                    factor = Decimal(1) if unit == 'ud' else Decimal(1000)
                    if quote.normalized_unit_price is not None:
                        cost = required / factor * quote.normalized_unit_price
                        consumption = (consumption if consumption is not None else Decimal(0)) + cost
                    else:
                        reasons.append('missing_unit_price')
                    if quote.package_price is not None:
                        previous = groups.get(quote.product_id)
                        if previous and previous[1] != quote:
                            raise ValueError('inconsistent product quotes')
                        groups[quote.product_id] = ((previous[0] if previous else Decimal(0)) + required / factor, quote)
                    else:
                        reasons.append('missing_package_price')
            else:
                reasons.append('ambiguous_match' if candidates else 'no_match')
        else:
            reasons.append('unknown_quantity')
        level = 'unknown' if cost is None else 'estimated' if reasons else 'known'
        lines.append(CostLine(key, row['name'], required, q.unit, cost, quote, tuple(candidates),
                              Uncertainty(level, tuple(dict.fromkeys(reasons))),
                              (key, quote.provenance) if quote else (key,), tuple(evidence), alternatives))
    procurement = None
    for amount, quote in groups.values():
        packages = (amount / quote.package_quantity).to_integral_value(rounding=ROUND_CEILING)
        procurement = (procurement if procurement is not None else Decimal(0)) + packages * quote.package_price
    excluded = tuple(line for line in lines if line.consumption is None)
    return CostEstimate(tuple(lines), consumption, procurement, currency,
                        bool(lines) and all(line.uncertainty.level == 'known' for line in lines),
                        dict(total=len(lines), costed=len(lines)-len(excluded), excluded=len(excluded)), excluded)


def present_money(value: Decimal | None) -> str | None:
    """Presentation only; audit operands and estimates remain unrounded."""
    return None if value is None else str(value.quantize(Decimal('0.01')))
