---
name: sabor-pantry
description: Keep the user's structured pantry in Sabor (what is at home, how much, until when). Use when the user shares a supermarket receipt (photo or text), says what they bought, used up or threw away, asks what is in the pantry or what expires soon, or plans meals. Offer these flows proactively; Sabor contains no AI model, so you read the receipt yourself.
---

# Sabor pantry: what is at home

Talk to the user in their language. The pantry stores items with quantity, unit, optional
expiry date and source. Sabor never sends receipts or photos to an AI service: **you** read
them and pass the lines. Nothing is written until the user has seen a preview and agreed.

## Before you start

Call `sabor_capabilities()` and use only tools in `data.registered_tools`. The pantry tools
belong to the opt-in `pantry` toolset: a new `sabor-mcp setup` enables it, or add `pantry`
to `SABOR_TOOLSETS`. They work with the SQLite store and with Mealie, not yet with Postgres.
If they are missing, say so in one line and how to enable them; do not fall back to editing
memory notes.

## Every write: preview, then confirm

1. Call the tool without `confirm` (a preview). Nothing is written.
2. Show the user the result in plain words: what will be added, merged, reduced or removed,
   any `uncertain` quantities and any `ignored` lines.
3. Only after the user agrees, call it again with exactly the same input plus
   `confirm=True` and the `preview_digest` from step 1.
4. If the answer is `conflict`, the pantry changed in between: preview again and ask again.

Never invent quantities, units or expiry dates. If the user does not know, leave them out:
an item without a quantity means "there is some".

## Flows to offer (be proactive)

- **A receipt.** When the user shares a supermarket receipt, offer to fill the pantry.
  Read every product line exactly as printed, one string per line, and call
  `add_pantry_from_receipt(lines=[...])`. Sabor drops prices, keeps pack sizes, weights
  and counts, expands common Spanish abbreviations and lists non-food lines (total, VAT,
  discounts, bags) as `ignored`. Every receipt line must enter the pantry only once, with
  its corrected quantity. For a line with `quantity_unknown`, ask the user for the amount
  and either add it to that line before you send it (preview again, then confirm), or
  confirm the receipt and then fix that item with
  `edit_pantry(changes=[{"op": "set", "id": ..., "quantity": ..., "unit": ...}])` using
  its `id` from `get_pantry(...)`. Never add the same product a second time with
  `"op": "add"`: that creates a duplicate entry. If the receipt shows best-before dates,
  offer to add them afterwards with `edit_pantry` (`"op": "set"`, `expires_on`).
- **Bought, used or thrown away.** «He comprado 2 kg de patatas» →
  `edit_pantry(changes=[{"op": "add", "name": "patatas", "quantity": 2, "unit": "kg"}])`. «Se acabó la leche» or
  «he usado 200 g de arroz» → `op: "subtract"` with the amount, or `op: "remove"` with the
  item `id` from `get_pantry(...)`. Subtracting never goes below zero; tell the user about
  any `shortfall`.
- **What is at home.** Answer with `get_pantry()`, or `get_pantry(food="arroz")` for one
  food. Group the answer by food and keep it short.
- **Expiring soon.** When the user plans meals or asks what to cook, check
  `get_pantry(expiring_within_days=3)` first and suggest using those items. Mention them once;
  do not nag.
- **Cooking from the pantry.** To suggest recipes, call `suggest_recipes(foods=[...])`.
  With the SQLite store pass the pantry food names. With Mealie (`recipe_store.adapters` in
  `sabor_capabilities()` lists `mealie`) it needs Mealie food IDs: pass each item's
  `food_id` from `get_pantry(...)`. If an item's `food_id` is null, never send the name
  as an ID; tell the user that food cannot be used for suggestions yet.
- **After a meal.** When the user says they cooked or ate something («ya hemos cenado la
  lasaña», «hoy tocaba el pollo»), offer to subtract its ingredients. For recipes cooked
  now call `consume_from_pantry(recipe_slugs=[...])`; for planned meals that are over call
  `consume_from_pantry(mealplan_start=..., mealplan_end=...)` with ISO dates. If they cooked
  more or fewer servings than the recipe, pass `servings_multiplier` (2 for double, 0.5 for
  half). In the preview, say what will be subtracted and, from the `ingredients` report,
  what will not and why (`not_in_pantry`, `not_measured`, `incompatible_unit`,
  `pantry_amount_unknown`), plus any `shortfall`. Meals already subtracted appear in
  `skipped` as `already_consumed`: tell the user they were counted before, never subtract
  them again by hand. Offer this once per meal; do not ask after every message.
- **Before a shopping list.** `add_to_shopping_list(...)` compares what it adds with the
  pantry by default (`subtract_pantry`). Items the pantry covers in full are added already
  marked as bought; after the call, read `pantry_coverage` and tell the user what was
  taken from home and what is `partial` (the `missing` amount still has to be bought).
  The pantry itself is not changed: once the user has shopped, offer to add the purchase
  (a receipt or `edit_pantry`). Turn the comparison off only if the user asks.
- **Before an estimate.** When the user asks what a recipe, list or week will cost them,
  offer `estimate_cost(source={..., "exclude_pantry": True})` so what is at home is not
  counted. Say that an item with too little stock stays priced, so the estimate is never
  too low.

## Units

Quantities are stored in `g`, `ml` or `ud`; pass the unit the user said (`kg`, `l`, `ud`…)
and Sabor converts it. Items of the same food merge only when their units are compatible:
«1 paquete de arroz» and «500 g de arroz» stay as two entries, because Sabor never guesses
a package size. If the user tells you the size, record it in grams or millilitres.
