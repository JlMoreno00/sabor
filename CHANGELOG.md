# Changelog

## [Unreleased]

### Changed
- Dependencies: `urllib3>=2.2` is now declared directly. Recipe image downloads read
  one socket read at a time (`HTTPResponse.read1`, added in urllib3 2.2.0) to keep their
  30 s limit, and `requests` alone still allowed urllib3 1.26, where every image
  download would have been skipped (#49).
- Free-text ingredient names also drop English unit words (`300 grams of rice` gives
  `rice` instead of `grams of rice`: gram(s), kilogram(s), liter(s)/litre(s),
  milliliter(s)/millilitre(s), unit(s), piece(s), dozen(s)). This changes the names
  used for pricing searches, pantry matching and shopping hints with every store,
  **Mealie included**, for lines written with those English words; Spanish lines keep
  their previous names (#49).
- SQLite/Postgres import normalization splits Spanish ingredient enumerations into
  one row per food (`2 g de harina y azúcar` becomes `harina` and `azúcar`). A
  shared amount is assigned to each food and flagged with the note `cantidad dudosa:
  línea con varios ingredientes`; compound names, preparations and lines without an
  amount are not split, and Mealie is unchanged (#49).
- SQLite and Postgres: new recipes without an explicit slug get one derived from
  their name (`tortilla-de-patatas`), with `-2`, `-3`... on collision, instead of a
  numeric identifier. Existing recipes keep their numeric slug and links (no data
  migration); nameless recipes keep numeric slugs; explicit slugs are unchanged.
  Postgres retries a slug taken concurrently by another process (#49).
- `server.json`: the OCI package advertises only the essential environment
  variables (store, Mealie, LLM, Bring, Mercadona, Open Prices, toolsets) and the `pypi`
  package (`uvx sabor-mcp`, stdio) none; `mcp-manifest.json` still lists all.
  The README carries the hidden `mcp-name` marker for registry ownership, and a
  manual *Publish PyPI* run targets TestPyPI unless PyPI is chosen; version tags
  still target PyPI. Nothing is published (#62).
- Store contract tests declare capability groups and backend-specific rating-sort
  and occurrence-ID behavior, with well-formed missing IDs supplied by fixtures (#5).
- Mercadona ranking prefers staples over equally close derivative products,
  without penalizing explicitly requested modifiers (#9).
- Cheapest pricing evaluates repeated ingredient quantities together, choosing a
  common product only for a strict procurement saving; mixed selections survive
  ties, overrides and strict policy are unchanged. Changed lines expose grouped
  selection reasons and combined-quantity alternatives; missing prices stay unknown.
  Each estimate reads the source once and uses a single per-call provider snapshot,
  so trial selections never mix catalog refreshes or repeat provider calls (#9).
- `mcp-manifest.json` and both `server.json` packages mark `MEALIE_BASE_URL` and
  `MEALIE_API_TOKEN` as optional (`required`/`isRequired: false`): they are only
  needed for the optional Mealie adapter, since SQLite is the default store.
  Runtime behavior with Mealie configured is unchanged (#36).

### Added
- URL imports into SQLite or Postgres download the recipe image and keep it with
  its original URL (new `recipe_images` table; SQLite migration 4, Postgres
  migration 3). Public addresses only at every redirect, 5 MB, real JPEG/PNG/WebP/GIF
  by file signature, 30 s total deadline. A failed download still imports the recipe,
  keeps the URL and adds an `image_warning`; `import_recipe` reports `images`. Mealie
  keeps using its native image import (#49).

### Security
- The import download guard (URL/image import and the new recipe images) now also
  refuses multicast addresses and IPv4 embedded in IPv6 (mapped, NAT64, 6to4, Teredo)
  that point to private or loopback space; Python reports several of these as global (#49).
- Setup CLI: terminal onboarding, JSON automation, language configuration and secret
  references; system keyring preferred with an owner-only local fallback; no MCP
  configuration tools (#64).
- Structured preferences use Postgres when selected (private migration v2), with
  shared file/Postgres behavior and `preferences import-file [--apply]` for an
  optional, non-destructive local JSON merge; SQLite/Mealie stay unchanged (#70).
- `sabor-mcp setup supabase [--create-role]`: the Postgres/Supabase connection string
  is typed only in a terminal (hidden input). With `--create-role` the `postgres`
  string is used once, in memory, to create or rotate the `sabor_app` role that
  owns only the `sabor` schema; only that role's string is stored (keychain or
  0600 file). Supabase transaction pooler strings are rejected and `sslmode=require`
  is added. `doctor` warns about Free-plan pauses, missing backups and IPv6-only
  direct hosts. New agent guide `docs/supabase-agentes.md` (#69).
- Optional generic Postgres recipe store (`sabor-mcp[postgres]`) with secret
  `SABOR_DATABASE_URL`, private schema migrations, RLS on every table and
  extension-free phrase search. Setup, doctor and discovery support `postgres`;
  the shared capability contract runs against Postgres 16 in CI (#68).
- `sabor-mcp setup` enables the `preferences` toolset when config.toml does not
  choose toolsets yet, so onboarding can save the cooking profile; existing
  toolsets and the runtime default without config.toml are unchanged (#67).
- Portable Agent Skill `skills/sabor-onboarding/SKILL.md`: the cooking-profile
  interview for any client that supports Agent Skills. It separates the profile
  (preference tools, when the opt-in `preferences` toolset is registered) from
  configuration (`sabor-mcp setup` in a terminal only). A test checks the skill
  frontmatter and that skills and `llms-install.md` name only real tools (#67).
- `llms-install.md` (English and Spanish): agent-driven install that asks one
  question at a time, configures only through `sabor-mcp setup`, keeps secrets out
  of the chat (`setup secret` in a terminal), merges the client entry and verifies
  with `doctor --json` and `sabor_capabilities`. README gains an *Install* section:
  with your AI, one-click links (Cursor, VS Code, Kiro, LM Studio, Goose) and the
  `claude`/`codex`/`gemini`/`code` commands; Docker becomes the advanced path (#66).
- Terminal `doctor [--json]` health checks and `config show [--json]` with
  per-key origins, masked secrets and actionable exit codes; no MCP tools (#65).
- Optional non-secret `config.toml` with OS paths, `SABOR_CONFIG` override,
  environment > file > defaults precedence and `data.config_source` discovery.
  Invalid files fail startup with redacted errors; secrets remain env-only (#63).
- Opt-in Open Prices pricing provider (`SABOR_OPEN_PRICES_ENABLED=true` plus
  `pricing` toolset): barcode search and country-scoped crowd observations,
  observation/store provenance, stale/discount/single-observation uncertainty,
  canonical package units and instance-owned ten-minute cache. Spanish coverage
  is sparse/old. Attribution: Open Prices (Open Food Facts), ODbL (#2).
- The shared store contract runs against the real Mealie adapter using a stateful
  offline Mealie contract double, alongside Fake and SQLite (#5).
- Pantry-aware `estimate_cost` sources accept `exclude_pantry: true`, excluding
  matched lines with `in_pantry` reasons and additive pantry counts. List building
  reports advisory `pantry_matches` from source lines without skipping additions;
  both read instance preferences independently of the preferences toolset (#13).
- Spanish catalog CLI: `sabor-mcp catalog status|apply` and
  `python -m sabor catalog status|apply`, with instance-bound Mealie settings,
  read-only status, create-only apply and JSON output. Existing catalog MCP
  tools remain unchanged, deprecated in documentation pending a future major (#14).
- Opt-in `preferences`: `get_preferences` and preview/confirmed `update_preferences`,
  validated household JSON storage and `sabor-mcp preferences import-memory`
  dry-run/apply migration. Legacy memory behavior is unchanged.
  MINOR contract baseline extension: 50 → 52 application tools (#13).
- Recipe search supports created/updated dates, rating, last made and daily
  seeded random ordering; SQLite persists search timestamps with migration v3
  and reports rating ordering as unsupported (#16).
- `sabor_capabilities` adds `data.mealie_only`: Mealie-only toolsets unavailable
  in this instance with a `reason` (`requires_mealie_store` for
  `legacy_nutrition`, `requires_mealie` for `legacy_catalog`) and `detail`.
  Additive; `disabled_toolsets` is unchanged. Beta installation guide
  (`docs/install-beta.md`) and draft release/migration notes
  (`docs/release-notes-beta.md`); README lists cookbooks and the Mealie web UI
  as Mealie-only (#40).
- Standalone uvx/stdio and Docker/HTTP installation without Mealie, user data
  directory and ffmpeg guidance, dual registry packages, gated PyPI publishing,
  and installed-wheel/container MCP smoke tests in CI (#39).
- SQLite URL import via offline recipe-scrapers and JSON-LD fallback, plus image
  preview/commit through a vision-capable compatible LLM; shared SSRF-safe bounded
  HTML/image fetching. Text/video imports retain store-based creation and native
  Mealie import behavior is unchanged (#38).
- Built-in SQLite recipe store, selected by default without a Mealie URL, with
  configurable `SABOR_STORE` / `SABOR_STORE_DB`, FTS search, meal plans and
  shopping lists usable through Bring! previews without Mealie (#37).
- CI `Lint (ruff)` job with a repository `ruff.toml` (pyflakes `F` and syntax
  errors `E9`, ruff pinned to 0.17.1); existing findings fixed without behavior
  changes (#32).
- One-command install: `docker compose --profile mealie up -d` also starts a
  pinned Mealie 3.22.0; without the profile `docker compose up -d` keeps the
  Sabor-only behavior. Three-step README quickstart, `scripts/check-compose.py`
  in CI, and a GHCR publish workflow that runs only manually or on `v*` tags (#33).
- `server.json` for the official MCP registry (`io.github.JlMoreno00/sabor`,
  OCI package `ghcr.io/jlmoreno00/sabor`), validated offline against the
  registry schema and kept in sync with `mcp-manifest.json`; the image carries
  the registry ownership label. Not submitted to the registry yet (#35).

### Changed
- Core readiness and tools now use the instance RecipeStore rather than requiring
  Mealie. Tags/comments/history and scheduling HTTP live in the Mealie adapter;
  list preview/clear and ID validation belong to stores. An injected Fake store
  exercises all 22 core tools without Mealie; Mealie remains the only production
  backend, with unchanged tool signatures and Mealie behavior (#36).
- Context-aware tools receive their `AppContext` explicitly per call through
  `bind_context`/`use_context` and `current_context()`, instead of returning a
  deferred `lambda ctx` that the binder executed. Public signatures, docstrings
  and MCP schemas are unchanged; direct Python calls still build a fresh
  compatibility context (#4).

### Fixed
- With the built-in stores (SQLite, Postgres, Fake), cost estimates name a structured
  ingredient that has no food, display or note from its text (`300 g de arroz` becomes
  `arroz`), as free-text rows already do, so `exclude_pantry` matches it. Rows with a
  food name, and every Mealie row, are unchanged (#49).
- Legacy Mealie values (`MEALIE_BASE_URL`, `MEALIE_API_TOKEN`) are stripped like
  the startup settings, so the Spanish catalog reconciler and legacy Mealie calls
  never send a padded URL or `Authorization` header; clean values are unchanged (#49).
- Direct Mealie store searches with random ordering now supply the same daily UTC
  `paginationSeed` as the search tool when no seed is provided. Explicit seeds
  and non-random ordering are unchanged (#49).
- URL recipe extraction skips booleans in yield lists (`[true, "4"]` becomes
  `"4"`) and removes blank steps inside nested instruction sections while keeping
  section metadata. The default macOS configuration path is normalized too (#49).
- Polish from #49: SQLite `sort=last_made` breaks equal timestamps by the latest
  cooking event; preferences reject text UTF-8 cannot encode; a partial
  `add_to_shopping_list` failure keeps the operation data; `SABOR_CONFIG`,
  `XDG_CONFIG_HOME` and `APPDATA` expand `~` and are normalized, and without any home
  directory the default path fails with a clear error instead of a literal `~` path; `doctor` with the Mealie store and no base URL
  exits 2; `catalog` ignores unrelated invalid settings; URL import drops blank
  ingredient/step lines and keeps `recipeYield` as text; Mercadona ranking also
  penalizes plural/punctuated derivatives; the Postgres `doctor` probe reconnects
  once after a lost connection, without migrating.
- Mealie port operations translate legacy HTTP 404 failures to `not_found`
  rather than `upstream_error`; legacy tool response dictionaries are unchanged (#5).
- `mcp-manifest.json` no longer advertises Spoonacular and now lists every
  environment variable Sabor reads (adds `SABOR_TOOLSETS`, `SABOR_LLM_*`,
  `SABOR_VIDEO_PROVIDER`, `SABOR_MERCADONA_ENABLED`) with real descriptions and
  code defaults; `MEALIE_BASE_URL` no longer claims a default. A contract test
  keeps the manifest in sync with the configuration parsers and `.env.example` (#34).
- Registered tools now isolate compatibility configuration, Gemini/video tagging,
  nutrition services and keyed/provider locks, correction stages, Bring catalog
  overrides and memory paths per AppContext (#3). Every registered tool binds
  context; workers copy it explicitly. Direct calls retain patchable defaults
  and share provider assembly with registration. Legacy Mercadona catalog and
  promotion state remains process-wide; this is not a security boundary.
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
