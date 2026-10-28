# Sabor Architecture

Sabor is one stateless FastMCP HTTP process around multiple external systems,
plus local recipe, memory and nutrition persistence. The configured RecipeStore owns cooking
data; Sabor owns orchestration and optional evidence. Without an explicit `SABOR_STORE`,
SQLite is the built-in default only when `MEALIE_BASE_URL` is empty; a non-empty URL
selects Mealie. An explicit `SABOR_STORE` takes precedence over this automatic selection.

## Structured preferences

`sabor/preferences.py` owns schema-v1 JSON at the frozen Settings legacy-config
`SABOR_DATA_DIR` / `preferences.json`; there is no new environment variable.
The opt-in `preferences` toolset is always ready; it is not in the default
toolsets and preferences are per instance/household (one `default` profile)
until authenticated users exist (#28). Both are provisional, pending
maintainer confirmation. RuntimeState owns its lock;
read/modify/replace is serialized per AppContext, not across processes sharing
one directory. Registration performs no preferences I/O. First confirmed write
creates the directory, fsyncs a same-directory temporary file and atomically
replaces the destination. Failed writes may leave a `.preferences-*` temporary
file for operator inspection; they never truncate existing preferences.
Corrupt/unreadable files return sanitized errors and block updates.
Only `profiles.default` is exposed: this is household storage, not user auth.
Legacy MEMORY.md and feedback behavior remain separate and unchanged.

`sabor/pricing/pantry.py` reuses the locked read-only preferences call; consumers
need not enable the preferences toolset. Pricing loads pantry only for explicit
`source.exclude_pantry: true`, skips matching lines before product lookup and
reports `in_pantry` through existing excluded-row/coverage structures. Shopping
loads preferences before writing, derives advisory matches from source item text
or each recipe occurrence's structured ingredients, and never removes items.
Source-derived matches omit IDs because store writes may merge lines. Matching
is whole-name, case/accent/whitespace insensitive with one trailing s/es strip
on either side, not fuzzy matching or inventory quantity depletion.

## Recipe store selection

`load_settings` selects explicit `SABOR_STORE=sqlite|mealie|postgres`, otherwise Mealie
when `MEALIE_BASE_URL` is non-empty, SQLite otherwise. `SABOR_STORE_DB` defaults
to `/data/sabor.db`. Assembly constructs an instance-owned SQLite store from
Settings, never a global path or connection. SQLite uses versioned forward-only
migrations, FTS5 search (also import duplicate detection), WAL and a connection
per call. Construction and context assembly perform no filesystem I/O. A per-instance
lock guards lazy initialization and migrations; `start_runtime_services` calls
`ensure_ready()` before serving requests, failing safely if opening fails.
Direct store operations initialize on first use and sanitize I/O failures.
Distinct database paths isolate registrations; shared paths intentionally
share persistence. Startup open errors are sanitized and never trigger fallback.

New SQLite recipes without an explicit slug get one derived from the name
(lowercase ASCII words joined by hyphens: `Tortilla de patatas` becomes
`tortilla-de-patatas`); a taken slug gets `-2`, `-3`... Names with nothing usable
keep the numeric identifier. Existing recipes are never rewritten: numeric slugs
from before this change, and their links, keep working. An explicit slug is used
as given and still returns `conflict` when taken. Postgres uses the same rules
(shared `sabor/store/slugs.py`); concurrent creators of one name are made unique by
the primary key: the insert runs in a savepoint and a taken derived slug retries with
the next suffix. SQLite serializes writers with `BEGIN IMMEDIATE` (#49).

Core recipes, meal plans and shopping lists work without Mealie. Native URL/image
import and Spanish catalog remain Mealie-only; `legacy_nutrition` is unavailable
with SQLite. Discovery lists the active `recipe_store` adapter and operations;
`data.mealie.configured` still reflects Mealie credentials, not the selected store.

Postgres is an optional `psycopg` 3 adapter (`sabor-mcp[postgres]`), selected with
`SABOR_STORE=postgres` and secret `SABOR_DATABASE_URL`. It ports SQLite semantics
with jsonb payloads, sequence-backed IDs/insertion order, ISO UTC text timestamps
and normalized phrase search without extensions. One instance-owned connection
and lock serialize transactional operations; only reads retry a connection failure
once. Forward-only migrations acquire a stable schema-scoped advisory lock before
checking version markers. All tables enable RLS without policies; the owner is
not forced through RLS and public/anonymous schema access is revoked. Default
schema is `sabor`; validated, quoted constructor schema names isolate DB tests.
See [Postgres operations](operations.md#postgres--supabase-store).

## System Boundaries

| Boundary | Sabor responsibility | External responsibility |
|---|---|---|
| MCP client | Expose tools at `/mcp` over Streamable HTTP | Choose tools and supply explicit mutation arguments |
| Mealie | Read and mutate recipes, planning, tags, comments, shopping lists, foods, units, and cookbooks through REST | Persist canonical cooking data and run the configured ingredient parser |
| Spoonacular | Search recipes and fetch exact-match ingredient profiles | Supply external recipe and per-100-g nutrient data |
| Gemini | Upload video, request structured recipe extraction, and attempt uploaded-file deletion | Analyze video content and retain uploaded data until deletion or provider expiry |
| OpenAI parser | Send parser name and ingredient text to Mealie | Mealie selects and operates its `openai` parser; Sabor has no direct OpenAI client or key |
| Bring! | Authenticate and synchronize shopping-list items | Persist Bring! account and list data |
| Mercadona | Read public product catalog and prices into an in-memory cache | Supply public product data |
| Local memory | Read and write configured `MEMORY_FILE` | Docker volume persists the file |
| Nutrition sidecar | Migrate and write SQLite evidence when nutrition is enabled | Docker volume persists `/data/nutrition.db` |

## Process Shape

`sabor_mcp.py` starts the server; `sabor/server.py` assembles settings,
AppContext and registered tool closures. `sabor/store/` owns typed RecipeStore
DTOs and the Mealie adapter/mixins; `sabor/pricing/` owns quote DTOs, the
Mercadona and Open Prices adapters and Decimal costing. The `nutrition/` package isolates the
more complex legacy nutrition domain:

| Module | Responsibility |
|---|---|
| `models.py` | Policy and evidence values |
| `db.py` | Ordered SQLite migrations and persistence |
| `providers.py` | Spoonacular profile lookup, retries, and persistent cache |
| `resolver.py` | Conservative Mealie-food-to-provider resolution |
| `conversions.py` | Deterministic quantity and unit conversion to grams |
| `calculator.py` | Nutrient aggregation, coverage, and confidence |
| `publisher.py` | Guarded Mealie nutrition payload and PATCH |
| `service.py` | Orchestration, cache identity, idempotency, and resumable backfill |
| `readiness.py` | Pure structural classification and optional read-only sidecar evidence assessment |

`models.nutrition_eligibility` is the single automatic-use gate shared by the
service, just-in-time orchestration, and publisher. Manual publication protection
is a separate, dominant boundary.

FastMCP is configured as stateless HTTP. This means MCP session state is not
stored in the process, but instance provider caches, the legacy Mercadona cache
and the local data files are still operational state.

Gemini upload deletion is best effort, not guaranteed. Sabor attempts deletion
after extraction and after a processing failure, but suppresses request errors,
does not verify the DELETE response, and has no retry queue. Process termination,
network failure, or provider errors can therefore leave an uploaded file until
Gemini's documented automatic deletion after 48 hours, or until an operator
removes it through the provider. A failed upload initialization/finalization may
also leave provider-side state that Sabor cannot identify for cleanup.

## Capability registry and public envelopes (0.3)

`core/config.py` validates settings; `core/registry.py` builds immutable provider
metadata; `server.py` collects decorated functions, intersects requested toolsets
with readiness, applies per-tool requirements and registers discovery once.
There are no startup credential probes. Legacy modules remain imported by the
server; selection is not a plugin-loading/security sandbox.

Import adapters implement extraction/preview; the unified importer commits via
the instance store. Native URL/image services remain optional Mealie extensions.
Native discovery advertises URL preview/commit and image commit-only. Non-native
stores use offline `recipe_scrapers.scrape_html` with JSON-LD fallback for URL
imports, and the compatible LLM with a data-URL image content part for image
preview/commit. Both create RecipeDrafts through the store, retaining source URL
provenance in the description. Shared pinned-IP fetching rejects private addresses
at every redirect, bypasses proxies and bounds streamed HTML to 5 MB and images
to 10 MB, with explicit content-type allowlists. Scraped content is untrusted data.
Normalization uses the store's optional hook; nutrition runs only with Mealie.
Gemini-native uses
whole-video audio/vision; OpenAI-compatible uses frames and captions. Selection
is explicit or Gemini-first when configured. Sync adapters implement
targets/plan/apply; the orchestrator reads the recipe store, resolves a unique target,
previews and confirms a rebuilt current plan. Bring! is the only sync provider;
Todoist is planned for 0.3.1.

Public success is `{ok:true,data:{...}}`; errors use
`{ok:false,error:{code,message,provider}}` with optional safe partial outcomes.
Legacy registration wrappers preserve signatures while adapting envelopes.
No raw upstream bodies, exception text or secrets belong in errors/logs.
Discovery intentionally omits model IDs as well as credentials/private URLs.

## Store, context and pricing boundaries

RecipeStore preserves structured ingredients, source IDs, checked state and
recipe occurrences rather than flattening upstream data. SQLite (the built-in
default), Mealie and optional Postgres are the production implementations; the in-memory fake
store only exercises the port contract in tests. Tandoor and KitchenOwl
backends are not implemented (#15). Registered core tools keep existing public
signatures and use instance stores. `AppContext` binds immutable settings,
registry, store and pricing services without startup I/O. Discovery exposes
`recipe_store`/`pricing` operations; the old Mealie summary is deprecated.

Core readiness uses a configured recipe store (SQLite, Mealie, Postgres or an injected
`store_factory`), not Mealie credentials alone. Per-tool requirements run inside
the existing AppContext binding; `store_ready` reads that instance's store and
retains the Settings-based Mealie fallback for compatibility. The backend is
chosen per instance from Settings (`SABOR_STORE`, see Recipe store selection);
there is no global store state. Tags, comments, history,
shopping preview/clear and ID validation belong to the store. Core tool modules
do not import HTTP clients. Mealie-only scheduling transport and its nutrition
gate live in the adapter; other stores schedule without that gate. Native
URL/image imports, catalog reconciliation and nutrition remain Mealie-specific.

Context-aware tools (`@tool(..., uses_context=True)`) receive their context
explicitly per call. `bind_context(fn, context)` runs the tool inside
`use_context(context)`, and the body reads it with `current_context()` after
argument validation; there is no deferred-callable return convention, so a
tool's return value is always its result. The binding is a `ContextVar` that is
set and reset around each call, so concurrent calls and servers built from
different `Settings` never see each other's context; outside a bound call
`current_context()` raises `LookupError`. Public signatures, docstrings and
FastMCP schemas are unchanged (the frozen contract test hashes their AST).
Direct Python calls (`sabor.tools.recipes.search_recipes(...)`, `sabor_mcp`
attributes) remain supported as a compatibility path: they bind a fresh
`default_context()`, built lazily only when the body needs it.

Every registered tool (including legacy envelopes and non-context core tools)
binds its AppContext for the entire call. `core/legacy_config.py` parses the same
mapping as Settings into a read-only compatibility namespace. The runtime proxy
uses that namespace when bound; only unbound direct calls import `sabor.config`.
`default_context()` shares registration's provider assembly and honors compatibility
patches. Tool arguments, docstrings and FastMCP schemas remain unchanged.

Mealie sessions, Bring transport/auth/catalogs, nutrition services and provider
cache locks, recipe/scheduling locks and correction stages are instance-owned.
Nutrition initializes lazily under an instance lock. Worker threads explicitly
copy the calling context. Memory and nutrition persistence use configured paths;
registrations pointing at the same path intentionally share external storage.

This is not a multi-tenant security boundary: legacy Mercadona promotion/catalog
state remains process-wide, as do direct-call compatibility defaults. Separate
processes/volumes remain recommended for untrusted households. Selection is not
sandboxing.

`OpenPricesPricing` is an opt-in read-only requests adapter with an identifying
Sabor User-Agent, timeout and sanitized errors. It selects the latest country
observation (date then ID) from a bounded 50-row window, filtering currency on
the server and ISO country locally. Quantity conversion uses the shared quantity
module. Its bounded 128-response, ten-minute TTL cache and HTTP session belong
to the instance, with no startup I/O or module-global cache. Quotes distinguish
observation/fetch dates, public store provenance and uncertainty, never contributor
or proof data. Source: Open Prices (Open Food Facts), ODbL; do not redistribute
a database combining these observations with non-free data.

Pricing tools resolve a tagged source (items, recipe, shopping_list or mealplan)
through the store when needed. The engine conservatively parses quantities,
converts compatible dimensions, matches candidates and aggregates Decimal costs.
Consumption uses proportional unit prices; procurement rounds package counts up
only after grouping product/location/currency demand. JSON decimals are strings.
Coverage, excluded rows and uncertainty accompany partial subtotals; unknowns
never become free ingredients. Mercadona and opt-in Open Prices are shipped providers; Open Prices uses
crowd-sourced observations from Open Food Facts. No pantry accounting, checkout or recipe mutation occurs.

## Store contract tests

`tests/test_store_contract.py::StoreContractMixin` runs the same behavior
assertions against Fake, SQLite, Postgres and the real Mealie adapter. Each test declares
required capability groups: `recipes_read`, `recipes_write`, `search_sort`,
`mealplan`, `shopping`, `activity` and `suggestions`. Missing groups produce
explicit adapter-specific skips; a vocabulary guard also checks optional branch
capabilities. Creation-based seeding runs only with `recipes_write`; read-only
fixtures supply their own seed. The `absent_id(kind)` hook supplies well-formed
nonexistent backend identifiers, preserving `not_found` expectations separately
from malformed-ID validation (Mealie shopping IDs must be UUIDs).

Two optional capabilities describe genuine backend differences without skipping
the surrounding contract: `rating_sort` is supported by Mealie, while Fake and
SQLite retain the unsupported-operation assertion; `occurrence_ids` is supported
by Fake and SQLite, while Mealie returns no changed IDs for recipe occurrences
because upstream can aggregate existing lines. Every adapter still checks the
resulting shopping contents. Mealie rating fixtures seed simulator state directly.

`tests/fakes/mealie_api.py` is a stateful, offline Mealie v3.22.0 contract double,
not a Mealie reimplementation or a substitute for HTTP characterization tests.
It supplies the recipe, search, activity, suggestion, mealplan and shopping
response shapes exercised by the adapter; unknown requests fail loudly.
Native imports and non-tag taxonomy endpoints remain outside this double.
No external service is needed for the Mealie/Fake/SQLite contract runs.
`tests/test_store_contract_postgres.py` runs the same contract against isolated
random schemas when `SABOR_TEST_DATABASE_URL` and psycopg are available; otherwise
it skips explicitly. CI uses a Postgres 16 service, not a real Supabase instance.

## Sync approval boundary

`preview_digest` hashes the source, target snapshot, actions and provider scope
for the selected mode. Preview → show → ask → confirm with that digest is the
recommended orchestration. Confirmation rebuilds/rechecks; stale digests or any
conflicts yield `conflict`, a fresh `data.preview` and zero writes. A replace
preview needs its own approval; Bring replace can clear all target purchase
entries, not just Sabor-created ones. Without a digest only current-call snapshot
checks apply (`digest_checked=false`). Remote apply is not atomic; inspect partial
outcomes and reconcile uncertain writes before retrying. Cost `source_digest`
hashes only the resolved source lines; `quote_digest` also hashes quote
observations. Both are unrelated to sync authorization.

## Structured Creation and Normalization Flow

```text
Structured recipe creation
  -> create recipe in Mealie
  -> use the slug returned by Mealie
  -> fetch the created recipe
  -> ask Mealie's configured parser to parse every ingredient
  -> accept only food matches at or above the confidence threshold
  -> preserve original text and recipe metadata
  -> PATCH normalized ingredient rows to Mealie
  -> optionally invoke nutrition only when both nutrition flags permit it
  -> return nutrition failures as structured warnings without changing import success
```

Normalization is conservative. Parser schema or cardinality mismatches do not
PATCH Mealie. Low-confidence rows retain their original representation. A
normalization failure is attached as a warning and does not turn a successful
recipe import into a failure. Placeholder-only legacy recipes skip
normalization.

`SABOR_INGREDIENT_PARSER=openai` selects a Mealie parser for the legacy
normalization path. Unified import commits (`dry_run=False`, every kind) run
the same instance-owned normalization (`SABOR_NORMALIZE_IMPORTS`) and
nutrition-after-import gate as `create_recipe`, reported per slug in
`normalization`, `nutrition` and `warnings`; hook failures never fail the
import. A `partial_failure` runs the same hooks on the recipes already created
and carries those keys next to `completed` and `failed`. It has no `normalize` parameter. Its text/video adapter calls
the explicitly configured compatible LLM directly using `SABOR_LLM_*`, not
`OPENAI_API_KEY`.

## Spanish Catalog Flow

Prefer `sabor-mcp catalog status|apply` (or `python -m sabor catalog status|apply`).
The shared CLI loads Settings from the current environment and binds an instance
context around `_reconcile_spanish_catalog`; it does not register MCP tools or
start runtime services. Status passes `apply=False`; apply passes `apply=True`.
The MCP tools remain unchanged but deprecated: prefer the CLI; the MCP tools
will be retired in a future major version.

Catalog manifests use `schema_version: 1` and an independent
`catalog_version`. Reconciliation pages through Mealie foods and units, matching
case, accents, whitespace, abbreviations, and aliases.

- `spanish_catalog_status` is read-only.
- `apply_spanish_catalog` requires an explicit tool call.
- Apply creates only missing curated units.
- It never updates, merges, deletes, or invokes Mealie seeders.
- The food manifest intentionally contains no duplicate food entries because
  Mealie's official Spanish dataset owns them.
- Ambiguous matches are reported rather than guessed.

## Nutrition Flow

```text
Mealie recipe and normalized ingredients
  -> resolve each exact Mealie food ID
  -> reuse fresh sidecar evidence or fetch an exact Spoonacular match
  -> convert consumed quantities to grams
  -> calculate ingredient contributions deterministically
  -> aggregate totals and per-serving values
  -> score gram coverage, ingredient coverage, and confidence
  -> persist calculation and ingredient evidence
  -> PATCH Mealie only after explicit publication and policy checks
```

Structured creation and recipe-backed weekly entries use a shared
`ensure_recipe_nutrition` orchestration path. Fixed 64-stripe process-local lock
sets bound lock memory while preserving same-key serialization; unrelated keys
can collide and serialize, but never bypass correctness checks. Structural
blockers stop before provider access; evidence blockers permit one `force=false`
calculation with at most 20 actual upstream attempts, including retries. For a
recipe-backed weekly entry, duplicate lookup, a final recipe GET/revision check,
and POST all execute under the same stripe. Lookup timeout or non-2xx fails closed
with no POST. Free-text entries bypass this gate.

Correction is a separate MCP boundary. `nutrition_correct_recipe` accepts up to
10 typed allowlisted operations addressed by canonical UUID4 ingredient IDs.
Food and unit IDs are canonical UUID4 values validated before URL construction;
alternative IDs are exact URL-safe identifiers. Both modes use authoritative
recipe and food/unit GETs, build the candidate in memory, and never invoke the
sidecar, provider, or any Mealie mutation. Preview reports revision, readiness,
changed paths, consequences, and the exact proposal. `apply=true` does not apply:
it returns stable `nutrition_correction_external_update_required`, labels the
work `external_required`, names the exact fields to update in Mealie, and emits
the exact readiness and preserved scheduling continuations. A fresh recipe GET
and readiness pass must prove the external edit before weekly scheduling can
continue. There is no correction PATCH, rollback, or mutation concurrency claim.

Transient provider failures persist their stable code, bounded retry interval,
and cooldown in the provider cache. The first response reports the normalized
provider interval exactly; cache hits report the positive, ceiling-rounded
seconds remaining until expiry, bounded to 300, and expiry permits a new retry.
An explicit `Retry-After: 0` normalizes to one second. Active rate-limit,
unavailable, and budget cooldowns return the cached failure without HTTP,
including forced calls; every actual retry attempt still consumes the invocation
budget. A separate fixed 64-stripe process-local lock coalesces concurrent cold
misses for the same canonical provider term or provider ID into one upstream sequence.
Waiters recheck and share success or failure cache state without consuming their
own attempt budgets; terms on different stripes retain concurrency.

The resolver uses curated Spanish aliases, an optional translator hook, or an
already-ASCII canonical name. The current composition root does not configure a
translator, so unsupported non-ASCII terms remain blocked unless covered by an
alias or manual mapping. Spoonacular results must have one exact normalized name
match; the first unrelated search result is never accepted.

Conversions prefer direct mass, then exact sidecar overrides for culinary
measures, density, piece weight, package weight, or other measures. A small set
of curated piece weights is available with source notes. Ambiguous alternatives,
frying oil without valid `absorbedGrams`, retained marinade without a valid
`retainedFraction` or `retainedGrams`, missing density, and invalid quantities
remain unresolved rather than guessed. Frying absorption is separate from
marinade retention evidence.

## SQLite Sidecar

`nutrition/db.py` owns append-only, ordered migrations. Schema versions 1
through 9 currently create and evolve:

- Food resolution and nutrient profiles keyed by Mealie food ID
- Food-specific conversions and manual overrides
- Recipe and ingredient calculation evidence
- Provider response cache
- Publish history
- Resumable backfill checkpoints

The database uses foreign keys and WAL mode. Initialization creates parent
directories and applies unapplied migrations, so merely enabling and invoking
nutrition is a persisted-data operation. Manual mappings, profiles, and
conversions take precedence over provider refreshes.

Reviewed manual nutrient profiles are exact per-100-g evidence. Preview is
read-only and reports the normalized profile, semantic payload hash, target,
overwrite impact, and manual precedence. Apply revalidates the target and uses a
single immediate transaction to upsert `provider='manual'`, the override flag,
confidence, source note/version, hash, and retrieval timestamp. Existing food
resolution and conversion rows are not rewritten. When Mealie is authoritative
for a food not yet represented in the sidecar, apply creates only the neutral
foreign-key parent resolution in the same transaction.

Calculation cache identity combines recipe semantics, nutrition-semantic
resolution evidence, nutrient-profile evidence, conversion evidence, and
`ENGINE_VERSION` (`3` in the current code). Resolution identity includes the
canonical target, provider binding, confidence, override state, source version,
and payload hash. Profile and conversion identity includes numeric values,
quality confidence, provider provenance, and override state. Timestamps and
source-note prose are excluded because they do not alter nutrition values.
Changing any semantic evidence creates a new immutable calculation row; the v8
fingerprint shape itself invalidates older hashes, so no engine-version bump is
required.

## Nutrition Readiness Boundary

Readiness is a derived assessment, not a nutrition calculation or workflow
state. `READINESS_CLASSIFIER_VERSION="1"` classifies current Mealie recipe
structure and an optional sidecar snapshot into deterministic issues, gates,
root counts, an informational score, and a fingerprint. A score never overrides
a blocker. Root precedence is stable, parser-loss evidence is recognized before
downstream missing-field symptoms, and title headings or explicitly excluded
rows never block.

The structural gate contains only quantity, food identity, unit, consumption,
and serving semantics. Sidecar conversion/profile facts are a separate evidence
gate. Resolution and conversion confidence can lower aggregate calculation
confidence without making valid evidence unusable; low values therefore produce
preflight warnings. Profile confidence remains a blocker at the configured
service threshold. Publication admission is conservative and does not replace
the calculator's aggregate thresholds or the publisher's final policy checks.

The readiness reader does not instantiate `NutritionDB`. It snapshots main,
WAL, SHM, and rollback-journal metadata together, reads stable main and WAL
bytes, applies only committed WAL frames to an in-memory image, and queries that
image. It never reads or applies rollback-journal bytes; any journal observed
during an attempt makes evidence `indeterminate`. This preserves WAL visibility
without creating files, migrating, checkpointing, or changing source bytes or
mtimes. Missing, incompatible, changing, corrupt, or unavailable evidence also
becomes `indeterminate`; structural classification still completes.
For publication provenance it reads only the latest immutable
`nutrition_publish_history.published_payload_json` linked to the recipe, ordered
by publication time and history ID. It bounds the JSON before parsing and uses
the shared persisted-payload validator to accept only publisher-formatted ASCII
strings in each field's exact `kcal`, `g`, or `mg` family. Null and blank
supported values are omitted under Mealie's empty-value semantics; unknown
fields are ignored, while malformed supported values invalidate the payload.
It exposes a verdict rather than stored payloads. Mutable calculation values and
publication flags are not ownership evidence, so later recalculation or flag
changes cannot erase historical ownership. Missing, malformed, oversized, or
legacy history is `unknown`; an exact current match is `owned`, and a difference
is `mismatch`.

The two readiness MCP tools are available independently of
`SABOR_NUTRITION_ENABLED`. They perform Mealie GETs only and cannot invoke the
parser, calculator, nutrition service, provider, persistence, or publication
paths.

Reviewed manual mappings may bind a canonical term to a provider food ID. Bound
mappings fetch the 100 g profile directly by ID; unbound mappings retain exact
text matching. Transient provider failures are stored with safe structured
classification and a short cooldown, without response bodies or credentials.

## Publication Boundary

Mealie is patched only when all relevant guards pass:

- Nutrition is enabled.
- The caller explicitly requests publication.
- Per-serving values exist, which requires valid servings.
- Coverage and confidence meet policy.
- Estimated publication is enabled when the result is estimated.
- The recipe is not protected by the explicit boolean `true` at either
  `extras.saborNutritionProtected` or `settings.saborNutritionProtected`.
- Present, non-null, non-object `extras` or `settings` values are treated as
  protected.

The publisher preserves existing recipe settings, enables nutrition display,
and writes supported static per-serving fields. Previous and published payloads
are recorded in the sidecar only after a successful PATCH.
The same pure protection contract is used by readiness and the publisher; the
publisher enforces it before preview generation or any HTTP request.

## Key Invariants

- The configured RecipeStore is the source of truth for recipe identity,
  ingredient ordering, quantity, unit, food linkage and servings. Mealie is an
  optional adapter, including visible nutrition when selected.
- Sabor never extends or writes nutrition into Mealie food records.
- Disabled nutrition does not initialize SQLite or call the provider.
- Recipe import success is independent of normalization and nutrition success.
- Recipe-backed weekly scheduling never posts before deterministic nutrition
  eligibility and a final recipe-revision check pass.
- Existing protected or unowned nutrition may coexist with a ready calculation
  for scheduling, but automatic publication never overwrites it.
- Just-in-time provider work is bounded to 20 actual upstream attempts per
  invocation and never retries at the orchestration layer.
- Provider ambiguity is unresolved, not guessed.
- Manual nutrition evidence is not overwritten by automated refresh.
- Provider profiles are reusable for reviewed mappings only while their provider
  binding matches; explicit manual profiles remain protected.
- Resolution confidence and nutrient-profile quality are independent. Validated
  Spoonacular 100 g profiles use quality confidence `0.97`; the profile-use gate
  checks that quality independently, then calculation confidence multiplies the
  resolution confidence exactly once.
- Schema v7 snapshots manual-mapping application on ingredient evidence, so
  historical mapping counts do not change when current mappings are edited.
- Schema v8 normalizes only non-manual Spoonacular profiles with validated
  provider IDs to quality confidence `0.97`, without changing nutrient values.
- Schema v9 adds reviewed source notes to nutrient-profile provenance.
- Mealie food IDs are stripped and rejected when empty at tool, domain, and
  persistence boundaries.
- Calculations are deterministic for the same recipe, profiles, conversions,
  policy semantics, and engine version.
- Evidence replacement is transactional and does not duplicate ingredient rows.
- Backfill is bounded, checkpointed, idempotent, and stops on provider rate limits.
- Publication is separate from calculation and requires explicit intent.

## Design Context

Current code and tests define the public contract. See [operations](operations.md)
for configuration and safe nutrition rollout guidance.
