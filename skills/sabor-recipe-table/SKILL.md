---
name: sabor-recipe-table
description: Show a Sabor recipe, or the weekly menu, as a Tabular Recipe Notation (TRN) table, an HTML table where ingredients sit on the left and the preparation flows to the right, merging as ingredients combine. Use when the user asks for a recipe or the week's menu "in a table", "en tabla", "en formato TRN" or as Tabular Recipe Notation.
---

# Sabor recipe table: Tabular Recipe Notation (TRN)

Talk to the user in their language. This skill only changes how you **present** a recipe:
it needs no extra tool and never writes to Sabor. Use it when the user asks for a recipe or
the weekly menu "in a table", "en tabla", "en formato TRN" or as Tabular Recipe Notation.

## Get the recipe

- One recipe: find it with `search_recipes(...)` if you only have a name, then read it with
  `get_recipe(slug=...)` for its ingredients, instructions, servings and times.
- The weekly menu: read the week with `get_mealplan_week(start_date=..., end_date=...)`, then
  `get_recipe(slug=...)` for each planned recipe.
- Use only what the recipe says. If servings or a time are unknown, write `—`; do not invent
  quantities, times or steps. If a step's time is not given, leave the time out of that cell.

## The format

1. **Ingredients on the left.** The first column lists every ingredient, one per row, with
   its quantity and unit («400 g de pechuga de pollo», «1 cebolla»).
2. **Preparation flows left to right.** Each column to the right is a later stage.
3. **Each step is one cell** that spans (`rowspan`) the rows of the ingredients it uses,
   with a short action and its time: «Cortar en tiras», «Cocinar 8 min»,
   «Añadir y cocinar 5-7 min».
4. **Steps merge as ingredients combine.** When two branches come together, the next step is
   one cell spanning all their rows. An ingredient used later (a garnish, a sauce) gets
   empty cells until the step that adds it, or a cell that spans several columns
   (`colspan`) so it reaches that step.
5. **One final cell spans everything**, for example «Servir 4 raciones».
6. **A header above the table** shows the recipe name, the servings and the approximate
   total time.

Every row must fill the same number of columns once `rowspan` and `colspan` are counted;
check it before you answer, or the table will render out of line.

## Write it as HTML

Markdown tables cannot merge cells, so write a plain HTML `<table>` with `rowspan` and
`colspan`. Keep it simple and clean: no CSS, no classes, no colours, no `<style>` or
scripts; `<caption>`, `<th>` and `<td>` are enough. Labels in the table are in Spanish
(«Ingredientes», «Preparación», «Raciones», «Tiempo aprox.»). Put the table inside an
```` ```html ```` code block only if the client does not render HTML.

## Worked example

Recipe: fajitas de pollo, 4 raciones, unos 30 min. Ingredients: 400 g de pechuga de
pollo, 1 cucharadita de pimentón, 1 cucharada de aceite de oliva, 1 pimiento rojo,
1 pimiento verde, 1 cebolla, 8 tortillas de trigo. Steps: cut the chicken into strips,
season it and cook it 8 min; cut the peppers and the onion into strips; add the vegetables
to the chicken and cook 5-7 min; warm the tortillas and fill them.

Columns: ingredient, then three stages. The chicken rows (1-3) and the vegetable rows (4-6)
are two branches that merge in stage 3; the tortillas (row 7) wait until the last step.

```html
<table>
  <caption>Fajitas de pollo · Raciones: 4 · Tiempo aprox.: 30 min</caption>
  <tr>
    <th>Ingredientes</th>
    <th colspan="4">Preparación</th>
  </tr>
  <tr>
    <td>400 g de pechuga de pollo</td>
    <td>Cortar en tiras</td>
    <td rowspan="3">Sazonar y cocinar 8 min</td>
    <td rowspan="6">Añadir las verduras y cocinar 5-7 min</td>
    <td rowspan="7">Rellenar las tortillas y servir 4 raciones</td>
  </tr>
  <tr>
    <td>1 cucharadita de pimentón</td>
    <td></td>
  </tr>
  <tr>
    <td>1 cucharada de aceite de oliva</td>
    <td></td>
  </tr>
  <tr>
    <td>1 pimiento rojo</td>
    <td rowspan="3" colspan="2">Cortar en tiras</td>
  </tr>
  <tr>
    <td>1 pimiento verde</td>
  </tr>
  <tr>
    <td>1 cebolla</td>
  </tr>
  <tr>
    <td>8 tortillas de trigo</td>
    <td colspan="3">Calentar</td>
  </tr>
</table>
```

Column check: every row covers 5 columns (ingredient + 4 stages). Row 1 opens all four
stages; rows 2-3 add one cell under «Cortar en tiras»; rows 4-6 add the vegetable cut,
spanning two stages; row 7 adds the tortillas, spanning three stages.

## The weekly menu

Show one TRN table per recipe, each under a heading with the day and the meal («Lunes ·
Cena»). Do not merge several recipes into one table. If a day has no recipe, say so in one
line instead of drawing an empty table.
