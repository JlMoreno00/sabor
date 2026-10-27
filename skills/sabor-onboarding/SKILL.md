---
name: sabor-onboarding
description: Set up or update a person's cooking profile in Sabor (household size, allergies and restrictions, dislikes, pantry, budget, schedule) after Sabor is installed. Use when the user says "Start with Sabor" / "Empieza con Sabor", is new, has no profile, or wants to change it. Profile only; Sabor's configuration is changed with `sabor-mcp setup` in a terminal, never from the chat.
---

# Sabor onboarding: cooking profile

Talk to the user in their language. Ask one short question at a time, offer a default
or examples, and accept free text. The profile is household data, not configuration.

## Profile versus configuration

- **Profile** (this skill): diners, allergies, restrictions, dislikes, pantry, budget,
  schedule and tastes. Saved with Sabor's preference tools, only after the user approves.
- **Configuration** (not this skill): recipe store, prices, import model, Bring!, secrets.
  It is changed only by running `sabor-mcp setup` (and `sabor-mcp setup secret <name>`)
  in a terminal; see `llms-install.md`. MCP tools never change it.
- Never ask for, accept or repeat passwords, tokens or API keys in the chat.

## 1. Check what is available

Call `sabor_capabilities()` and read `data.registered_tools`. Use only tools listed there.

- If `get_preferences` and `update_preferences` are registered, the profile can be saved
  in Sabor. Read the current one with `get_preferences()` and only ask about what is missing
  or what the user wants to change.
- `sabor-mcp setup` enables them for new installs. If they are not registered, the
  `preferences` toolset is not enabled for this install (for example a custom toolsets list).
  Do the interview anyway, keep the confirmed summary in the conversation or your client's
  own notes, and tell the user plainly that it was not saved in Sabor. Do not claim
  otherwise and do not try to enable it from the chat.

## 2. Interview (one question at a time)

1. How many people eat, and whether portions differ.
2. Allergies, intolerances and dietary restrictions. Ask follow-up questions when unclear;
   never infer that something is safe from a mere preference.
3. Days and meals to plan, including weekends if wanted.
4. Budget and the shops they use; do not assume any supermarket.
5. Time per day, kitchen equipment, and whether they prefer cooking fresh, leftovers or batches.
6. Likes, rejected ingredients and favourite recipes.
7. What they usually have in the pantry.
8. Nutrition goals only if they want to share them; never set medical targets.

## 3. Confirm and save

Summarize the profile and ask for confirmation. Map it to the preference fields:

| Field | Type | Content |
|---|---|---|
| `household_size` | integer 1–20 or null | number of diners |
| `diet` | list of up to 200 strings, each up to 200 characters | restrictions; write allergies and intolerances explicitly, e.g. "allergy: peanuts" |
| `dislikes` | list of up to 200 strings, each up to 200 characters | rejected ingredients or dishes |
| `pantry` | list of up to 200 strings, each up to 200 characters | staples they usually have |
| `notes` | list of up to 200 strings, each up to 200 characters | budget, shops, schedule, time, equipment, tastes and goals, as short phrases |

Save only if `update_preferences` is registered. Otherwise summarize the confirmed
profile without claiming it was saved in Sabor.

When registered, preview first with `update_preferences(changes={"set": {...}}, confirm=False)`, show the
diff, and repeat with `confirm=True` only after the user approves. Use `add`/`remove`
for later list changes. Then tell the user exactly what was saved and offer to fix it
before the first meal plan.

## 4. After onboarding

Offer the first step of the everyday flow: suggest recipes with `suggest_recipes` or plan
the week. Every write (meal plan entries, shopping lists, Bring! sync) still needs the
user's approval first, as described in Sabor's README.
