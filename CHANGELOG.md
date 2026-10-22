# Changelog

## [Unreleased]

### Changed
- Context-aware tools receive their `AppContext` explicitly per call through
  `bind_context`/`use_context` and `current_context()`, instead of returning a
  deferred `lambda ctx` that the binder executed. Public signatures, docstrings
  and MCP schemas are unchanged; direct Python calls still build a fresh
  compatibility context (#4).

### Fixed
- `estimate_cost` `source_digest` now hashes only the resolved source (line key,
  name, required quantity and base unit), so catalog refreshes no longer change
  it. The new `quote_digest` covers quote observations (#8).
- `import_recipe(dry_run=False)` now applies the same ingredient normalization
  and nutrition-after-import gate as `create_recipe` for url, text, video and
  image imports, reported per slug in `normalization`, `nutrition` and
  `warnings` (#7).
- TikTok video imports no longer suggest uploading a file, which `import_recipe`
  cannot accept; the error now suggests pasting the caption or transcript with
  `kind='text'` (#10).
- `import_recipe` `partial_failure` now runs the same normalization and
  nutrition hooks on the recipes it already created and reports them in
  `data.normalization`, `data.nutrition` and `data.warnings`; a hook crash adds
  a `post_commit_hooks_failed` warning instead of hiding the partial result. On
  a complete import a hook crash no longer re-runs the hooks or reports
  `partial_failure`/`upstream_error`: the import stays `ok` with that warning (#25).

## [0.3.0] - 2026-10-20

### Added
- RecipeStore abstraction and per-application AppContext; Mealie remains the
  first production store. Core tools use the injected store with existing MCP
  signatures and structured data preserved.
- Opt-in `pricing`: `search_pricing_products`, `get_product_price`, `estimate_cost`.
  Mercadona quotes include package/unit bases, provenance and uncertainty;
  Decimal-string consumption and procurement subtotals distinguish quantities
  consumed from whole packages purchased. Missing prices are never zero.
- Sync `preview_digest` binds approval to source/target/actions and instance
  scope; stale previews return a fresh preview and perform zero writes.
- Discovery lists recipe-store/pricing operations and native URL preview/commit
  and image commit-only import kinds. Documented plan → list → cost → sync flow.

### Changed
- `estimate_cost` on shopping lists reads amount and name from free-text
  (note-only) items such as `1 l leche`, whose Mealie quantity is a placeholder.
- Pricing defaults to `match_policy="cheapest"` (lowest checkout cost for the
  needed quantity, then unit price) with estimated selections and
  alternatives; Mercadona dozen (dc/dz), 100 ml and 100 g reference prices are
  normalized, and unknown denominators are derived from the package
  (`derived_unit_price`); `strict` preserves ambiguous exclusions, overrides take priority.
  Search/cost exclude non-food and rank closest token names. Unitless quantities
  assume count; egg dozen quotes normalize to per-piece.
- Mercadona catalog warm-up runs in one background worker with paced requests,
  bounded backoff and optional atomic `/data` cache; pricing calls never crawl
  synchronously. Stale snapshots remain usable with `stale_catalog`.
- License changed from MIT to GNU AGPL-3.0-only starting with 0.3.0. Releases
  up to 0.2.x remain MIT; contributions use inbound = outbound licensing.
- Bring transport/authentication/cache and Mercadona catalog cache are
  instance-owned. Sync reads the injected recipe store.
- Registered recipe creation again runs conservative normalization with instance
  settings/session; nutrition-after-import hook parity is retained. Unified
  imports use store creation/native import services; they still do not run the
  normalization/nutrition hooks.
- Discovery's `data.mealie.configured` remains for compatibility but is deprecated
  in favor of `data.capabilities.recipe_store`.

### Removed
- Four retailer-specific pricing tools, without aliases (migration below).
  There are now 50 application tools plus always-on discovery.

### Security
- Any sync conflicts block confirmed apply with zero writes. Append conflicts
  never silently authorize replace; replace requires its own approved preview.
- Digest confirmation rejects changed plans and cross-instance previews; remote
  changes during apply still cannot be made atomic across systems.

### Known limitations
- Cold Mercadona pricing returns `rate_limited` with a 120-second retry hint
  while warming. Datacenter anti-bot blocks may delay availability; partial
  catalogs tolerate at most 10% failed subcategories (`catalog_partial`).
- Single-tenant self-hosting only: legacy services, nutrition-after-import,
  video tagging and Gemini helpers retain global reads. AppContext and isolated
  Bring state do not establish shared-process multi-tenant safety.
- Mercadona is the only shipped pricing provider (unofficial API); Open Prices
  is deferred. Todoist is planned for 0.3.1. Other recipe stores are not shipped.
- No pantry subtraction, density/piece-weight guessing, purchases or distributed
  rollback. Unknown quantities/prices remain excluded or uncertain. Cost
  `source_digest` includes quote observations, not only source identity.
- Sync without `preview_digest` retains current-snapshot compatibility behavior,
  not binding to a prior approval. Image import has no read-only preview.

### Migration from 0.2.x

Breaking 0.x change: update callers and refresh MCP discovery after restarting.
Pin 0.2.x if callers cannot migrate; do not interpret old package totals as
quantity-consumption costs. Back up Sabor data and Mealie independently.

| Removed tool | Replacement |
|---|---|
| `mercadona_search_products` | `search_pricing_products(query=..., store={"provider":"mercadona"})` |
| `mercadona_get_product_price` | `get_product_price(product_id=..., store={"provider":"mercadona"})` |
| `mercadona_estimate_recipe_cost` | `estimate_cost(source={"kind":"recipe","slug":slug}, store={"provider":"mercadona"})`, or inline structured items preserving ingredient text |
| `mercadona_estimate_shopping_list_cost` | `estimate_cost(source={"kind":"shopping_list","list_id":id}, store={"provider":"mercadona"})`, or inline structured items |

To keep pricing, set `SABOR_MERCADONA_ENABLED=true` and **add `pricing`** to
`SABOR_TOOLSETS`. Defaults remain `core,import,shopping_sync`; credentials alone
never select the new tools. `legacy_pricing` retains only
`mercadona_get_price_drops` and `mercadona_get_new_arrivals` with their existing
arguments. Keep it explicitly selected if those promotion tools are needed.
Use decimal strings for numeric pricing operands, inspect coverage/uncertainty,
and replace preview → confirm sync calls with preview → show → approval →
`confirm=True, preview_digest=<approved digest>`. A `conflict` requires a newly
reviewed preview, not a blind retry. Existing core and import signatures remain.

## [0.2.0] - 2026-10-20

### Added
- Capability registry, configurable toolsets and always-on discovery.
- Filtered/paginated recipe search, food/tool suggestions, structured recipe
  reads and confirmed partial recipe updates.
- Shopping item read/check/remove and list composition from recipes or inclusive
  meal-plan dates with explicit servings scaling.
- Unified URL/text/image/video import with Gemini-native and OpenAI-compatible
  video adapters; provider-neutral shopping sync backed by Bring!.

### Changed
- Sabor is a kitchen-intelligence layer on top of Mealie, not a tool-count race.
- Results use `{ok:true,data:{...}}` or sanitized
  `{ok:false,error:{code,message,provider}}`; unwrap `data`, including legacy calls.
- Tool descriptions are English. Ingredients and instructions retain structure.
- Sync reads a Mealie source list; both append and replace preview by default.
- Legacy domains require explicit opt-in. Refresh MCP discovery after restart.

### Removed
- Ten duplicate/legacy tool names, with no compatibility aliases (table below).
- Independent wild-mode URL scraping and arbitrary caller-supplied Bring item
  strings. Populate a Mealie list first.
- Local-file video inputs are not supported by the unified importer.

### Security
- Sanitized error classification and discovery redaction; no upstream response
  bodies, exception text or credentials in public errors.
- Image downloads validate public addresses and redirects with pinned-IP TLS.
- Explicit preview/confirmation boundaries for destructive operations and sync;
  unknown targets never fall back. External processing still needs user consent.

### Migration from 0.1.x

This is a breaking 0.x release. Back up local memory/sidecar and Mealie separately.
Pin 0.1.1 if a client cannot migrate; there are no deprecated aliases.

| Removed name | Replacement call |
|---|---|
| `scrape_recipe` | `import_recipe(source=url, kind="url", dry_run=True)` |
| `import_recipe_to_mealie` | `import_recipe(source=url, kind="url", dry_run=False)` |
| `analyze_video_recipes` | `import_recipe(source=url, kind="video", dry_run=True)` |
| `import_video_recipes_to_mealie` | `import_recipe(source=url, kind="video", dry_run=False)` |
| `add_items_to_shopping_list` | `add_to_shopping_list(list_id=id, items=[...])` |
| `add_recipe_ingredients_to_list` | `add_to_shopping_list(list_id=id, recipe_slugs=[slug])` |
| `bring_get_lists` | `get_shopping_sync_targets(provider="bring")` |
| `bring_sync_list` | `sync_shopping_list(mealie_list_id=id, target={"provider":"bring","list_id":target_id}, mode="replace", confirm=False)` |
| `bring_add_items` | `sync_shopping_list(mealie_list_id=id, target={"provider":"bring","list_id":target_id}, mode="append", confirm=False)` |
| `spoonacular_search_by_nutrients` | `spoonacular_search_recipes(query="", max_calories=..., min_protein_g=...)` |

Map the nutrient search's `max_calories_kcal` to `max_calories`; retain the
other matching nutrient filters using the new schema. Import commits now require
explicit `dry_run=False`. For sync, create/populate the Mealie source first,
resolve the target, preview, ask, then repeat with `confirm=True`.

Environment changes:
- `SABOR_TOOLSETS` defaults to `core,import,shopping_sync`; explicitly empty
  means discovery only. Credentials no longer opt legacy tools in.
- `SABOR_MERCADONA_ENABLED=true` is required alongside `legacy_pricing`.
- `SABOR_LLM_BASE_URL`, `SABOR_LLM_MODEL`, `SABOR_LLM_API_KEY` configure
  compatible text/video processing. Remote endpoints require all three;
  localhost/127.0.0.1 may omit the key.
- `SABOR_VIDEO_PROVIDER` selects `gemini_native` or `openai_compatible`.
  Unset prefers configured Gemini, then compatible. Native Gemini requires the
  existing `GEMINI_API_KEY`; `VIDEO_RECIPE_GEMINI_MODEL` keeps its 0.1.x default `gemini-2.5-flash-lite`.
- Existing normalization/nutrition settings remain, but the unified importer
  does not currently run the old post-import normalization/nutrition hooks.

To keep the **remaining 0.1 domains** (not removed aliases), select all legacy sets:

```dotenv
SABOR_TOOLSETS=core,import,shopping_sync,legacy_spoonacular,legacy_pricing,legacy_memory,legacy_nutrition,legacy_catalog
SABOR_MERCADONA_ENABLED=true
```

Keep the corresponding Mealie, Spoonacular, Gemini and Bring! credentials and
existing nutrition flags. No selection restores the removed names or old result
shape. Update structured recipe consumers, list parameter names and approval
flows using the [current reference](README.md).


## [0.1.1] - 2026-10-19

### Fixed

- Bring! sync rejects unknown list names with the available names instead of
  silently replacing the first list.
- Mercadona cost estimates require whole-word matches and strip leading
  quantities and units; salt no longer matches salsa or salmon.

### Changed

- **Breaking for 0.x callers:** `bring_sync_list`, `clear_shopping_list`,
  `delete_shopping_list` and `clear_mealplan_week` now preview by default and
  make no content changes unless `confirm=True`. Review the preview and obtain
  user approval before confirming.
- Cost responses explicitly report `cost_basis: full_package_price`, a note,
  and `total_full_package_cost`; `total_estimated_cost` is retained for compatibility.
- Offline regression tests cover previews, confirmation and cost matching.

## [0.1.0] - 2026-10-19

Initial public release.

### Added

- A single Streamable HTTP MCP endpoint with 52 tools for Mealie recipes,
  shopping lists, meal planning, tags, comments and cooking history.
- Spoonacular recipe and nutrient search, web scraping/import, Gemini video
  recipe extraction/import, Bring! shopping sync and Mercadona price lookup.
- Persistent feedback, pantry notes and learned preferences.
- Spanish catalog reconciliation and conservative import normalization.
- Opt-in nutrition evidence, readiness reports, reviewed mappings and profiles,
  calculation, guarded publication and bounded backfill.
- Portable Docker Compose setup, configuration example, operations and backup
  guides, and an optional generalized OpenClaw/Telegram persona example.
- MIT license.

### Changed

- Integration code is split into a `sabor/` package while preserving the
  `sabor_mcp.py` entry point and public tool contract.
- Nutrition and automatic publication remain disabled by default.
