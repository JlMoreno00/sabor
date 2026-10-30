# Sabor Operations

Start with configuration validation and health checks. Do not run pilots,
backfills, catalog apply, or publication as a deployment smoke test.

## Terminal diagnostics

Run `sabor-mcp doctor --json` after setup; use `sabor-mcp config show --json`
to inspect effective values and their origins. Omit `--json` for human output
(an aligned table for `config show`). Both commands support `--help`, run before
runtime configuration is applied, and leave the process environment unchanged.
Neither registers tools nor serves MCP.

Doctor is read-only: it opens an existing SQLite database with `mode=ro` and reads
its schema version, never creating directories or files nor running migrations. A
missing database is reported (created on first start) when its directory is writable;
an older schema is reported as migrating on next start. For Postgres it connects,
reads the version only if the store schema exists, and never creates it nor migrates.
For Mealie it makes one
unauthenticated GET to `/api/app/about` with a five-second timeout; it never prints response bodies or tokens. This checks
reachability, not token validity or authenticated permissions.

The stable JSON key order and nesting are:

```json
{"ok":true,"version":"0.3.0","config_path":"/path/config.toml","config":{"found":true,"source":"file","valid":true},"store":{"backend":"sqlite","reachable":true,"schema_version":3},"toolsets":["core","import"],"capabilities_summary":{"recipe_store":["sqlite"],"pricing":[],"import":[],"shopping_sync":[]},"missing_secrets":[],"warnings":[],"next_steps":[]}
```

`schema_version` is null for Mealie or failed probes. Invalid configuration adds
an `error` string and sets `config.valid` false. Missing secret names get a
`sabor-mcp setup secret <name>` next step. Warnings are deterministic and advisory:
keyring declared but unavailable, presence of `secrets.toml`, a SQLite
database not created yet, and a SQLite schema that will be migrated on next start.
The `secrets.toml` warning means the file exists, not that its permissions were
verified. On POSIX, owner-only permissions (normally 0600) are validated when
reading it; group/other access raises `ConfigError` and exits 2.
A temporary SQLite directory alone is not a warning.

| Exit | Meaning |
|---|---|
| 0 | Check succeeded (advisory warnings may be present) |
| 1 | Required secrets missing |
| 2 | Invalid configuration or arguments; fix config.toml or rerun setup |
| 3 | Store unreachable; check SQLite directory permissions/path or Mealie service |

Store failure takes precedence over missing secrets. `config show` exits 0 for
valid configuration, even with missing secrets; invalid configuration exits 2.
Its JSON is `{config_path, source, keys: [{key, env, value, origin}]}`: all TOML
keys followed by all `secrets.<name>` keys. Values are effective strings, or null
when unset/default; secrets are `"***"` when set. Origins are `env`, `file`, or
`default`, plus `keyring` for secrets. Source is `file`, `env`, or `defaults`.
Diagnostic strings use shared redaction and mask resolved credential values.

## Optional TOML configuration

Non-empty environment values override file values, which override current defaults.
Empty environment values count as absent. Restart after configuration changes.
`SABOR_CONFIG` selects an explicit file; a missing explicit file fails startup.
Otherwise Sabor uses:

- macOS: `~/Library/Application Support/sabor/config.toml`
- Linux: `$XDG_CONFIG_HOME/sabor/config.toml`, or `~/.config/sabor/config.toml`
- Windows: `%APPDATA%/sabor/config.toml`, or `~/AppData/Roaming/sabor/config.toml`

A missing default file is harmless. Invalid TOML, unknown sections/keys, invalid
types, unreadable files and secret keys fail startup without echoing values.
Keep secrets in environment variables or setup-managed keyring/file references: `MEALIE_API_TOKEN`, `SABOR_LLM_API_KEY`,
`GEMINI_API_KEY`, `BRING_EMAIL`, `BRING_PASSWORD`, `SPOONACULAR_API_KEY`.
A non-empty environment value takes precedence over a stored secret.
Transport variables remain env-only.

| TOML key | Environment variable |
|---|---|
| `[general] language` | `SABOR_LANGUAGE` |
| `[general] toolsets` | `SABOR_TOOLSETS` |
| `[general] data_dir` | `SABOR_DATA_DIR` |
| `[general] memory_file` | `MEMORY_FILE` |
| `[store] backend` | `SABOR_STORE` |
| `[store] sqlite_path` | `SABOR_STORE_DB` |
| `[mealie] base_url` | `MEALIE_BASE_URL` |
| `[llm] base_url` | `SABOR_LLM_BASE_URL` |
| `[llm] model` | `SABOR_LLM_MODEL` |
| `[import] video_provider` | `SABOR_VIDEO_PROVIDER` |
| `[import] gemini_model` | `VIDEO_RECIPE_GEMINI_MODEL` |
| `[import] video_workdir` | `VIDEO_RECIPE_WORKDIR` |
| `[import] normalize` | `SABOR_NORMALIZE_IMPORTS` |
| `[import] ingredient_parser` | `SABOR_INGREDIENT_PARSER` |
| `[import] normalize_confidence` | `SABOR_NORMALIZE_CONFIDENCE` |
| `[bring] catalog_dir` | `BRING_CATALOG_DIR` |
| `[pricing] mercadona` | `SABOR_MERCADONA_ENABLED` |
| `[pricing] mercadona_warehouse` | `MERCADONA_WAREHOUSE` |
| `[pricing] open_prices` | `SABOR_OPEN_PRICES_ENABLED` |
| `[nutrition] enabled` | `SABOR_NUTRITION_ENABLED` |
| `[nutrition] db` | `SABOR_NUTRITION_DB` |
| `[nutrition] provider` | `SABOR_NUTRITION_PROVIDER` |
| `[nutrition] min_profile_confidence` | `SABOR_NUTRITION_MIN_PROFILE_CONFIDENCE` |
| `[nutrition] min_publish_coverage` | `SABOR_NUTRITION_MIN_PUBLISH_COVERAGE` |
| `[nutrition] min_publish_confidence` | `SABOR_NUTRITION_MIN_PUBLISH_CONFIDENCE` |
| `[nutrition] publish_estimates` | `SABOR_NUTRITION_PUBLISH_ESTIMATES` |
| `[nutrition] profile_ttl_days` | `SABOR_NUTRITION_PROFILE_TTL_DAYS` |
| `[nutrition] not_found_ttl_days` | `SABOR_NUTRITION_NOT_FOUND_TTL_DAYS` |
| `[nutrition] source_language` | `SABOR_NUTRITION_SOURCE_LANGUAGE` |
| `[nutrition] default_cup_ml` | `SABOR_NUTRITION_DEFAULT_CUP_ML` |
| `[nutrition] auto_on_import` | `SABOR_NUTRITION_AUTO_ON_IMPORT` |
| `[nutrition] auto_publish` | `SABOR_NUTRITION_AUTO_PUBLISH` |

Each key accepts only its own type: TOML booleans for the switches (`pricing.mercadona`,
`pricing.open_prices`, `import.normalize`, `nutrition.enabled`, `nutrition.publish_estimates`,
`nutrition.auto_on_import`, `nutrition.auto_publish`), integers for the `nutrition.*_ttl_days`
keys, numbers for the confidence/coverage thresholds and `nutrition.default_cup_ml`, and strings
for everything else. A mismatch fails startup without echoing the value.
Strings are preserved; numbers become strings; booleans become `true`/`false`.
Only `general.toolsets` also accepts a string list, joined with commas:

```toml
[general]
toolsets = ["core", "import"]
[pricing]
open_prices = true
```

`sabor_capabilities.data.config_source` is `file` whenever a file was loaded
(even with environment overrides), otherwise `env` for known non-empty settings,
or `defaults`.

## Transports and local data

`uvx sabor-mcp` defaults to stdio for clients that launch the process.
Use `sabor-mcp --transport http` or `python sabor_mcp.py` for Streamable HTTP
at `/mcp`; Docker retains HTTP by default. Keep stdout protocol-only in stdio.
HTTP binds with `SABOR_HOST` / `SABOR_PORT`; expose only loopback or a trusted,
authenticated access layer. See README for pre-release install commands.

`SABOR_DATA_DIR` overrides the data directory. Otherwise an existing `/data`
is used, then the OS user data directory (Linux: XDG or ~/.local/share/sabor;
macOS: ~/Library/Application Support/sabor; Windows: LOCALAPPDATA/sabor).
Default recipe, memory, nutrition and catalog cache files derive from it;
per-file overrides win. Directories are created lazily. Persist and back up the
selected directory, not only the container filesystem.

Local video import requires system ffmpeg and ffprobe on PATH; Docker includes
them. Missing binaries produce `provider_unconfigured` with installation guidance.
Core recipes, planning and shopping do not need ffmpeg or Mealie.

## Structured preferences migration

With Postgres, structured preferences live in `sabor.preferences` (migration v2,
profile `default`), shared across devices. SQLite and Mealie keep the existing
local JSON backend. Nutrition, Mercadona and memory caches remain local.

To merge an existing `<data dir>/preferences.json` into Postgres:

```sh
sabor-mcp preferences import-file
sabor-mcp preferences import-file --apply
```

The first command previews without writing preferences; `--apply` commits atomically.
List fields merge case-insensitively; household size is copied only if unset in
Postgres. The local file is never rewritten or deleted. Missing or corrupt files
fail safely; non-Postgres stores return `invalid_argument`. Exit codes are 0 for
success, 1 for an envelope error and 2 for usage errors. Repeated imports preserve
existing database values and do not duplicate list entries. First access may run
schema migrations, including during preview.

Add `preferences` to `SABOR_TOOLSETS` and restart; the default toolset list is
unchanged. Back up `<data dir>/preferences.json` and the configured `MEMORY_FILE`.
No new environment variable is needed. `.env.example` intentionally does not
list this new opt-in value yet.

```sh
sabor-mcp preferences import-memory
sabor-mcp preferences import-memory --apply
```

The first command prints JSON only (dry-run, no writes); inspect it before apply.
It reads the instance MEMORY_FILE and never deletes or modifies it. Exit codes:
0 success, 1 read/validation/write error, 2 usage error. This command is currently
available through the installed `sabor-mcp` entrypoint, not `python -m sabor`.

| MEMORY.md bullets | Destination |
|---|---|
| Despensa Conversacional | pantry, merged case-insensitively |
| Preferencias Aprendidas | notes, merged case-insensitively |
| Feedback Historial, Top Recetas Favoritas, Blacklist Recetas, other sections | Not migrated; section counts reported in not_migrated |

Repeated imports do not duplicate values; preference annotations remain verbatim.
Migration does not infer diet, dislikes or household size. `save_feedback` stays
unchanged; existing ratings/favorites/comments are not redirected.

Example `<data dir>/preferences.json` (single household, reserved default profile):

```json
{
  "schema_version": 1,
  "profiles": {
    "default": {
      "diet": ["vegetarian"],
      "dislikes": [],
      "household_size": 2,
      "pantry": ["Arroz"],
      "notes": ["Cenas rápidas"]
    }
  },
  "updated_at": "2026-10-20T12:00:00+00:00"
}
```

Invalid/corrupt files are never silently replaced. Repair or restore a backup
before retrying. Do not share a data directory between concurrent processes;
locking is per instance, not a cross-process lock. Pantry subtraction is deferred.

## Configuration Authority

Registration authority is `sabor/core/config.py`: `load_settings` validates the
startup environment and `sabor/server.py` freezes the registry once per server.
`sabor/config.py` separately reads legacy/integration and transport defaults at
import time. Set Mealie URL explicitly: registration does not inherit the legacy
localhost URL default. Restart the service and refresh MCP client discovery
after changes. `docker-compose.yml` loads `.env`, with no explicit environment block. An automatically loaded
`docker-compose.override.yml` can override those values; override environment
entries win over `.env`.

The executable runtime, `.env.example`, and `mcp-manifest.json` all use
`MEALIE_API_TOKEN`.

### Core and Integration Variables

| Variable | Code default | Required for | Notes |
|---|---|---|---|
| `SABOR_HOST` | `0.0.0.0` (container, `python sabor_mcp.py`); `127.0.0.1` for `sabor-mcp --transport http` | Server | Compose publishes only to host loopback; set it explicitly to listen on another interface |
| `SABOR_HOST_PORT` | `9170` (Compose) | Docker host binding | Optional, non-secret; loopback only |
| `SABOR_PORT` | `8080` | Server | Must match container port and health check unless all are changed together |
| `MEALIE_BASE_URL` | Empty for registration; legacy client defaults localhost | Optional Mealie adapter only | Remove trailing slash; use the container-reachable URL in Compose |
| `SABOR_STORE` | `mealie` if `MEALIE_BASE_URL` is non-empty, otherwise `sqlite` | Recipe backend | Explicit `sqlite`, `mealie` or `postgres` wins; invalid values fail startup |
| `SABOR_STORE_DB` | `<data dir>/sabor.db` | SQLite backend | Parent directories created at startup; open failures are fatal, with no fallback |
| `SABOR_DATABASE_URL` | Empty | Required for Postgres backend | Secret Postgres URL; environment or setup-managed secret reference, never `[store] database_url` |
| `MEALIE_API_TOKEN` | Empty | Optional Mealie adapter only | Bearer token; secret |
| `SPOONACULAR_API_KEY` | Empty | Spoonacular tools and nutrition profile fetches | Secret |
| `GEMINI_API_KEY` | Empty | Video analysis/import | Secret |
| `VIDEO_RECIPE_GEMINI_MODEL` | `gemini-2.5-flash-lite` | Native video | Same default as 0.1.x; set it to pin another Gemini model |
| `VIDEO_RECIPE_WORKDIR` | `/tmp/video-recipe-mcp` | Video URL processing | Temporary downloaded media |
| `BRING_EMAIL` | Empty | Bring! tools | Account identifier; treat as sensitive |
| `BRING_PASSWORD` | Empty | Bring! tools | Secret |
| `BRING_CATALOG_DIR` | Empty | Optional Bring! catalog override | Directory searched before installed package data |
| `MERCADONA_WAREHOUSE` | `mad1` | Mercadona tools | Public warehouse code |
| `MEMORY_FILE` | `<data dir>/MEMORY.md` | Preference memory | Contains user data; back up and protect it |

## Store and instance deployment (0.3)

SQLite, Mealie and optional Postgres are shipped RecipeStores. Without a Mealie URL, SQLite enables
recipes, meal plans, shopping lists and import locally. URL import uses offline
recipe-scrapers with JSON-LD fallback and needs no LLM. Text requires a compatible
LLM; image preview/commit requires a vision-capable compatible model (otherwise
`provider_unconfigured`); video requires a configured video adapter. Native
URL/image services, Spanish catalog and legacy nutrition still require Mealie.
HTML downloads accept only text/html or application/xhtml+xml up to 5 MB; image
downloads accept JPEG, PNG, WebP or GIF up to 10 MB. Both pin validated public IPs,
revalidate redirects and ignore proxy settings. Registered tools use an AppContext with
instance-owned Mealie session, Mercadona catalog cache and Bring transport/auth
state. Discovery's `capabilities.recipe_store` lists operations; the old
`data.mealie.configured` field is deprecated. Native URL import supports preview
and commit; image is commit-only. Configuration discovery does not probe health.

Each registration freezes configuration from its supplied environment mapping.
Nutrition services/locks, memory paths, Gemini credentials, video tagging and
Bring catalog overrides are instance-owned. Give independent registrations
distinct `SABOR_STORE_DB`, `MEMORY_FILE` and `SABOR_NUTRITION_DB` paths; identical paths intentionally
share persistence. `BRING_CATALOG_DIR` is captured at registration, before the
installed-package discovery fallback, not reread from the process environment.

Run untrusted households in separate processes/containers with distinct
credentials and data volumes. This is not a multi-tenant security boundary:
legacy Mercadona catalog/promotion state and unbound direct-call compatibility
globals remain process-wide.
Persist `/data/sabor.db` in the existing `/data` volume. Include it in backups:
stop Sabor before copying the volume (including any `sabor.db-wal` and
`sabor.db-shm` files), or use SQLite's online backup API for a consistent live
snapshot. Do not copy only the main database while writes are active. Restore
with Sabor stopped and retain ownership/write permissions. Back up Mealie
separately when selected. For Postgres, back up the database schema separately as described below.

## Postgres / Supabase store

Install the optional driver extra, then configure the store without putting the
connection URL in chat, command-line arguments or non-secret TOML:

```sh
pip install 'sabor-mcp[postgres]'
# Or run without a permanent install:
uvx --from 'sabor-mcp[postgres]' sabor-mcp
sabor-mcp setup --non-interactive --store postgres --json
sabor-mcp setup secret database_url
sabor-mcp doctor --json
```

At runtime select `SABOR_STORE=postgres` and supply `SABOR_DATABASE_URL` through
the environment or the setup-managed secret reference. It accepts `postgresql://`
or `postgres://` URLs. `[store] database_url` is rejected as a secret. Without the
extra, selecting Postgres fails clearly; SQLite and Mealie remain usable.
Setup only records configuration and missing secrets; doctor performs the probe.
A failed Postgres probe exits 3 and suggests checking the URL/connections with
`sabor-mcp setup secret database_url`; driver errors and discovery omit the DSN.

The connecting role must be able to create/own the `sabor` schema, its tables and
sequences. Forward-only migrations run at startup (or first direct store call),
with a transaction-scoped advisory lock and version marker. Every table has RLS
enabled with **no policies**; the owner bypasses RLS (no FORCE). Schema privileges
are revoked from PUBLIC and from `anon`/`authenticated` when those roles exist;
table and sequence grants to those two roles are also revoked. Use a dedicated
owner role, not an anonymous browser key. This is private server-side persistence,
not a public Supabase Data API or a multi-tenant policy framework.

No extensions are required. Search uses normalized, accent-insensitive word
phrases plus substring-name matching. Rating sort remains unsupported, as with
SQLite. One persistent connection is serialized per instance; read operations
reconnect/retry once on connection failure, **writes never auto-retry**. Reconcile
uncertain writes by reading back before retrying manually.

For Supabase use the **session pooler on port 5432** with `sslmode=require`, not
the transaction pooler on port 6543. `sabor-mcp setup supabase --create-role`
reads the `postgres` session-pooler string from hidden terminal input, creates or
rotates the least-privilege `sabor_app` role that owns only the `sabor` schema,
and stores only the `sabor_app` string; see the agent guide
[docs/supabase-agentes.md](supabase-agentes.md). For Supabase hosts, `doctor`
warns about Free-plan pauses, missing automatic backups and IPv6-only direct hosts. Generic local/self-hosted Postgres and Neon
use the same adapter; connection TLS is controlled by the URL. Construction and
registration do not connect. `SABOR_STORE_DB` still controls SQLite only; memory,
preferences and nutrition-sidecar persistence are unchanged by this recipe store.

Back up the `sabor` schema using your Postgres provider's consistent backup/restore
procedure, including sequences and schema-version metadata. Do not treat a Sabor
local-volume backup as a Postgres backup. Migrations do not downgrade; restore a
matching pre-migration backup for incompatible code rollback.

## Pricing operations

Add `pricing` to `SABOR_TOOLSETS` and set `SABOR_MERCADONA_ENABLED=true`
or `SABOR_OPEN_PRICES_ENABLED=true` (both default false).
The default toolset list stays `core,import,shopping_sync`; `legacy_pricing`
keeps only price drops/new arrivals. Inline estimates need no Mealie; recipe,
calendar and shopping-list sources require a store. `store` means retail context,
with provider/location/currency, not a recipe backend. Mercadona uses the startup
warehouse (default `mad1`) and EUR; incompatible contexts fail. Open Prices uses
`store={"provider":"open_prices","location_id":"ES","currency":"EUR"}` with ISO
alpha-2 country (default ES), currency default EUR and barcode product IDs.
Country is filtered locally; absent locations are excluded. The latest of up to
50 observations is selected by date then ID, not cheapest price. No matching
observation returns `not_found`. Spanish coverage is sparse/old, not a catalog.
Search is capped at ten products; a one-word plural query that returns fewer also
searches its singular (`guisantes` → `guisante`) to fill the remaining room, never
displacing the original results. Instance caches hold up to 128 responses for
ten minutes; restart clears them, with no disk cache or background requests.
Quotes always flag `observation`; older than 180 days adds `stale_observation`,
promotions add `discounted`, and a single country observation in the fetched
window adds `single_observation`. Unknown quantities add `unknown_unit`; never
substitute zero. Availability is unknown. Attribution: Open Prices (Open Food
Facts), ODbL, https://prices.openfoodfacts.org/. Do not combine observations with
non-free data into a redistributed database. Public store/city and dates are
reported, not contributor identifiers, proofs or coordinates.
Catalog cache refresh failures may yield explicitly stale quotes;
the unofficial API provides no availability guarantee.

Search is food-only; whole-word names (one trailing `s`/`es` tolerated) rank
products carrying the query's name numbers first (`4 quesos`; amounts such as
`500 g` and fractions such as `1/2 kg` or `1 1/2 kg` do not count; the number must
modify the same word, so `pack 4 unidades` does not carry `4 quesos`), then unprocessed products, fewer extra normalized name
words and food/name/ID. `estimate_cost` searches with the name it matches on,
so a leading amount (`2 huevos`) never narrows the query. `estimate_cost` defaults to
`match_policy="cheapest"` among up to 50 candidates: compatible food with the
lowest checkout cost for the required quantity (whole packages x package
price), then the lowest unit price. Processed or compound candidates
(croquetas, chicles, salsa...) are skipped for a basic ingredient whenever an
unprocessed candidate matches; the vocabulary lives in `sabor/pricing/matching.py`. Show `alternatives` and offer to
switch using `product_overrides` (prioritary); use `strict` for ambiguous exclusions.
Unitless quantities assume count (`assumed_count`); kitchen measures stay unknown.
Search products and inspect package/unit quotes before selecting overrides.
Read `complete`, coverage, excluded rows, uncertainty and provenance. Consumption
and procurement are different partial subtotals: the latter aggregates before
rounding packages up. Decimal JSON values are strings; missing/all-unknown prices
are null, never zero. No purchases occur; pantry exclusion is opt-in. See the
[pricing contract](../README.md#quantity-aware-pricing) for source shapes/bounds.

All estimate source kinds accept `exclude_pantry: true` to exclude whole-name
pantry matches from pricing, reported as `in_pantry` in excluded rows/coverage
and `pantry: {"applied": true, "matched": N}`. Absent/false does not read the
preferences file and preserves existing responses. List additions never skip
pantry items: a nonempty pantry adds `pantry_matches` based on source lines,
without item IDs. Reread the list and approve removal separately. Missing
preferences mean empty pantry; corrupt preferences block only pantry-aware
estimates (`Unable to read preferences safely.`). List additions still write and
return `pantry_warning` (`preferences_unreadable` or `pantry_matching_failed`)
instead of `pantry_matches`. No toolset opt-in is
needed to read this instance data. See [matching and examples](../README.md#pantry-aware-estimates).

## Sync with approved digests

1. Obtain approval for plan/list writes, populate the source list, reread it and
   call `estimate_cost`. Show both cost bases and uncertainty; budget changes
   need new approval. This workflow is orchestration, not a composite transaction.
2. Discover the exact Bring target and preview `sync_shopping_list` (append by
   default). Show additions, removals, conflicts and returned `preview_digest`.
3. Ask, then repeat with `confirm=True` and that `preview_digest`. On `conflict`,
   inspect `data.preview`, resolve conflicts and obtain a fresh approval. Never
   silently change mode or target; nonempty conflicts always block writes.
4. Replace requires its own preview and can clear all target purchase entries,
   including for an empty source. Do not infer managed-only Bring ownership.

Omitting the digest preserves current-snapshot checks within the confirmed call,
not approval of the earlier preview. Digests are scoped to the application;
refresh after restart. Concurrent remote edits during apply are not atomic.
Inspect per-action/partial outcomes and reconcile before retrying uncertain
mutations; there is no distributed rollback. Cost `source_digest` is not a sync
authorization token. Todoist remains planned for 0.3.1.

### Import Normalization Variables

| Variable | Default | Effect |
|---|---|---|
| `SABOR_NORMALIZE_IMPORTS` | `true` | Normalize successful non-placeholder imports |
| `SABOR_INGREDIENT_PARSER` | `openai` | Parser name sent to Mealie; Sabor does not call OpenAI directly |
| `SABOR_NORMALIZE_CONFIDENCE` | `0.75` | Minimum accepted parser food confidence |

### Nutrition Variables

| Variable | Default | Effect |
|---|---|---|
| `SABOR_NUTRITION_ENABLED` | `false` | Master feature gate and lazy sidecar initialization |
| `SABOR_NUTRITION_DB` | `/data/nutrition.db` | SQLite sidecar path |
| `SABOR_NUTRITION_PROVIDER` | `spoonacular` | Reported provider selector; current composition constructs Spoonacular |
| `SABOR_NUTRITION_MIN_PROFILE_CONFIDENCE` | `0.80` | Minimum profile confidence used in calculation |
| `SABOR_NUTRITION_MIN_PUBLISH_COVERAGE` | `0.90` | Complete-result coverage threshold |
| `SABOR_NUTRITION_MIN_PUBLISH_CONFIDENCE` | `0.80` | Publication confidence threshold |
| `SABOR_NUTRITION_PUBLISH_ESTIMATES` | `false` | Permit estimated-result publication when other guards pass |
| `SABOR_NUTRITION_PROFILE_TTL_DAYS` | `180` | Successful profile freshness |
| `SABOR_NUTRITION_NOT_FOUND_TTL_DAYS` | `30` | Not-found or ambiguous cache freshness |
| `SABOR_NUTRITION_SOURCE_LANGUAGE` | `es` | Conservative source-language policy; untranslated Spanish is not sent to the provider |
| `SABOR_NUTRITION_DEFAULT_CUP_ML` | `240` | Cup volume used with a known density |
| `SABOR_NUTRITION_AUTO_ON_IMPORT` | `false` | Calculate after successful import |
| `SABOR_NUTRITION_AUTO_PUBLISH` | `false` | Publish an automatic import calculation |

Legacy boolean variables accept `1`, `true`, `yes`, or `on`, case-insensitively.
`SABOR_NUTRITION_ENABLED` uses that same parser for registered and direct calls.
`SABOR_MERCADONA_ENABLED` accepts only `true` (case-insensitive). Numeric variables
are parsed during registration (or compatibility import); invalid values prevent
startup.

Bring requests also send a public web-client identifier embedded in the source.
It is not an account credential or confidential API secret. The same identifier
is published by the reputable [`miaucl/bring-api` integration at the evidence
commit](https://github.com/miaucl/bring-api/blob/e3c9aeb51a866735647874dbf1c112ea416283ca/bring_api/const.py);
its declaration has a line-scoped secret-scan exemption so scanners do not mask
real findings elsewhere. `BRING_EMAIL` and `BRING_PASSWORD` remain protected
configuration.

## Toolset and provider configuration (0.2)

| Variable | Default | Meaning |
|---|---|---|
| `SABOR_TOOLSETS` | `core,import,shopping_sync` | Comma-separated allowlist; empty = discovery only; unknown names fail startup |
| `SABOR_LLM_BASE_URL` | Empty | Compatible endpoint, including /v1 where required |
| `SABOR_LLM_MODEL` | Empty | Explicit compatible model |
| `SABOR_LLM_API_KEY` | Empty | Required remotely; localhost/127.0.0.1 may omit |
| `SABOR_VIDEO_PROVIDER` | Empty | Auto: configured Gemini then compatible; explicit choice has no fallback |
| `SABOR_MERCADONA_ENABLED` | `false` | Explicit readiness for Mercadona pricing and selected legacy pricing |
| `SABOR_OPEN_PRICES_ENABLED` | `false` | Enable Open Prices observations; accepts true/false, case-insensitive; select pricing toolset |

Endpoint examples: OpenAI `https://api.openai.com/v1`; Gemini-compatible
`https://generativelanguage.googleapis.com/v1beta/openai/`; Ollama
`http://localhost:11434/v1`. Models must support the requested media; compatible
video uses frames/captions, not whole-video audio. Native Gemini requires its
existing key plus an explicit model. Inside Docker, localhost means the Sabor
container; use an intentionally trusted reachable service address and key.

For Spanish catalog setup, prefer the CLI; the MCP tools will be retired in a future major version.
The existing `spanish_catalog_status` and `apply_spanish_catalog` MCP tools remain
unchanged and are deprecated in documentation only.

```sh
sabor-mcp catalog status
sabor-mcp catalog apply
python -m sabor catalog status
python -m sabor catalog apply
```

Configure `MEALIE_BASE_URL` and `MEALIE_API_TOKEN`; no toolset opt-in is required
for the CLI. Status only reads. Apply creates missing curated units, audits foods,
and never updates, merges, deletes or invokes seeders. Review the indented UTF-8
JSON, including nested errors/conflicts. Exit 0 means top-level `ok` is true
(not that every entry was created); 1 means configuration/reconciliation failure;
2 means invalid arguments. Missing Mealie configuration reports to stderr without
HTTP requests. No server runtime or SQLite initialization is started.
`sabor-mcp` without arguments still starts stdio; `--transport http` still selects HTTP.

Select legacy domains explicitly; credentials alone do not register them:

```dotenv
SABOR_TOOLSETS=core,import,shopping_sync,legacy_spoonacular,legacy_pricing,legacy_memory,legacy_nutrition,legacy_catalog
SABOR_MERCADONA_ENABLED=true
```

Core/catalog need Mealie. Spoonacular needs its key; memory/nutrition can register
locally, with existing nutrition operation gates unchanged. Sync tools need both
Bring credentials and Mealie; partial Bring credentials fail startup.
Import can register with Mealie alone or an LLM/Gemini preview provider.
Mealie image import uses Mealie-owned OpenAI configuration and cannot preview.

## Redaction policy

Discovery never returns credentials, private URLs, paths, account identifiers,
list contents or operator-selected model IDs. Error envelopes and logs must not
contain raw upstream bodies, exception text, auth headers or arbitrary model
output. Report stable `error.code` / `error.provider` and sanitized outcomes.
Do not print resolved environments, signed URLs, user memory, captions or footage
in support reports. A sanitized error does not make a full recipe/list safe to
publish: successful business data remains private.

## Preflight

From the reviewed deployment checkout:

```bash
docker compose config -q
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile sabor_mcp.py nutrition/*.py scripts/*.py tests/*.py
```

Review the resolved configuration without printing secrets. `docker compose
config -q` validates only; do not publish full `docker compose config` output to
logs or tickets because resolved environment values may be included.

## Startup and Deployment

The following commands build and replace the running service. Use them only in
an approved deployment window after backup and review:

```bash
docker compose build
docker compose up -d
docker compose ps
docker compose logs --since=10m sabor
```

Do not add `-v` to shutdown commands. Do not use deployment startup as a way to
test unreviewed source against production data.

## Health Checks

The image health check posts an MCP `initialize` request every 30 seconds. The
host port is bound to loopback at `127.0.0.1:9170`.

Check container state:

```bash
docker compose ps
docker inspect --format '{{.State.Health.Status}}' "$(docker compose ps -q sabor)"
```

Check the endpoint without invoking an application tool:

```bash
curl -fsS -X POST \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"manual-healthcheck","version":"1.0.0"}},"id":1}' \
  http://127.0.0.1:9170/mcp
```

An MCP initialize response proves process and transport availability. It does
not prove Mealie or provider credentials. Call `sabor_capabilities()` first,
unwrap `data`, compare `registered_tools` and disabled reasons with the intended
configuration. Discovery reports configured, not healthy; it performs no network
probe. Verify integrations with read-only
tools through an MCP client and never use mutation tools as health checks.

## Mercadona pricing warm-up and cache

Configured Mercadona pricing starts one daemon catalog worker after context
construction. Cold calls return `rate_limited` with message “Mercadona catalog
is loading; retry in a few minutes” and `data.retry_after_seconds=120`; they do
not wait for the crawl. This code describes temporary provider readiness, not
missing configuration. Do not repeatedly restart the service to test pricing.

The worker reuses a keep-alive session, spaces subcategory requests by 0.5 s,
and retries 403/429/timeouts at most three attempts (2 s then 4 s backoff).
More than three consecutive blocked subcategories aborts the refresh. Failed
refreshes retain the previous snapshot and impose a 120 s retry cooldown;
a later pricing call can start another worker. At most 10% failed subcategories
are accepted with `catalog_partial`; expired snapshots carry `stale_catalog`.

When `/data` is writable, `/data/mercadona-catalog-<warehouse>.json` persists
`warehouse`, Unix-seconds `loaded_at`, boolean `partial`, and `catalog` (product
ID to raw product object). A sibling `.json.tmp` is written before atomic rename.
The default TTL is 24 hours. Startup accepts fresh or stale validated snapshots;
stale data serves immediately while refreshing. Garbled caches are ignored and
cache write failures leave in-memory pricing usable. No writable directory means
memory-only caching. Keep the normal persistent data volume across restarts.

Smoke once after deployment: check discovery, call one pricing search, accept a
warming envelope if cold, wait a few minutes, then repeat that same search and
one returned product quote. Inspect uncertainty and cache timestamp without
publishing product data. Restart once only after successful warm-up to verify
cache reuse; do not trigger multiple fresh crawls from the datacenter IP.

## Live verification (#11)

### Mercadona data semantics (observed 2026-10-24)

Read-only sample of the public API (`wh=mad1`): 571 products from 15
subcategories (oil and sauces, water and soft drinks, fruit, vegetables, beef,
fresh fish, canned fish and vegetables, eggs), about 25 requests spaced 1.5 s.
These are observations of an unofficial API, not a contract; re-check them if
estimates look wrong.

- `selling_method` was `0` for every product, including fruit, meat and fish
  sold by weight. Variable weight is signalled by `approx_size: true` (114 of
  571), not by `selling_method`. Sabor already reports `approx_size` as
  uncertainty.
- `iva` was `null` for 424 of 571 products; `tax_percentage` (`"4.000"`,
  `"10.000"`, `"21.000"`) was always present. The API exposes no net price; the
  prices are consistent with VAT-inclusive shelf prices, but that is not
  verified. Do not derive anything from `iva` alone.
- Drained weight (71 canned products): `unit_size` is the gross size,
  `drained_weight` the drained one; `bulk_price = unit_price / unit_size`
  (per gross kg) and `reference_price = unit_price / drained_weight` (per
  drained kg). Example: tuna 0.9 kg, 0.65 kg drained, 6.50 €: bulk 7.22, reference
  10.00 €/kg. Sabor normalizes from `reference_price`, so the unit price of
  these products is per drained kg; it always adds the `drained_weight`
  uncertainty reason.
- Packs: `is_pack: true` with `pack_size` (each unit), `total_units` and
  `unit_size` = total (for example 3 × 0.4 kg bricks, `unit_size` 1.2). Sabor
  never multiplies packs and adds the `pack` reason.
- Reference formats `dc` and `dz` are both a dozen (6 eggs at 2.20 € →
  `reference_price` 4.40 per `dc`); Sabor converts them to per unit (÷ 12). `100 g`
  appears for small-format goods and is converted to per kg.
- Anomaly to watch: some `ud` products count pieces inside a bunch in the
  reference but bunches in `unit_size` ("Cebollas tiernas": `unit_size` 1 ud,
  1.70 €, `reference_price` 0.567 per ud). Their per-unit estimate can be off by
  the bunch size.
- Availability: `unavailable_from` was always `null`, `published` always `true`,
  `limit` always 999; four products had `unavailable_weekdays: [1]`.

### Still to verify against real services

These paths need accounts, paid API keys or a local Mealie and remain
unverified. Use throwaway targets only and record the result here.

1. **`search_recipes` sort orders against Mealie 3.22.0** (#16). Start a
   disposable instance on loopback with a temporary volume whose names are unique
   to this run:

   ```sh
   verify_name="sabor-verify-mealie-$(date +%s)-$(openssl rand -hex 6)"
   verify_volume="${verify_name}-data"
   docker volume create "$verify_volume" &&
     docker run -d --name "$verify_name" -p 127.0.0.1:19925:9000 -v "$verify_volume:/app/data" -e ALLOW_SIGNUP=false ghcr.io/mealie-recipes/mealie:v3.22.0
   ```

   Then sign in with the instance's initial administrator, create an API token and
   four test recipes with different creation times, ratings (per-user rating
   endpoint) and `lastMade`, then call `search_recipes(query=..., sort=...,
   direction=...)` for `name`, `created_at`, `updated_at`, `rating`, `last_made`
   and `random`. Expect unrated and never-made recipes last, and identical
   `random` order across pages on the same UTC day. Remove the container and
   volume created by this run afterwards, in the same shell:
   `docker rm -f "$verify_name"; docker volume rm "$verify_volume"` (independent
   commands, so the volume is removed even if the container was never created).
   Not run on 2026-10-24: Docker Desktop was waiting for an administrator
   password on the host.
2. **Bring! apply**: on a dedicated throwaway Bring list, run
   `sync_shopping_list(..., confirm=False)`, review the preview, then
   `confirm=True` with the returned `preview_digest`; check the items in the
   Bring app, then repeat with a changed source to confirm stale-digest
   rejection.
3. **Imports with real models**: `import_recipe` with an OpenAI-compatible LLM
   (text, and video frames plus captions) and with Gemini-native video, preview
   then commit on a test store. Record model names and costs.
4. **Native Mealie image import**: requires Mealie's own OpenAI configuration;
   record the actual `POST /api/recipes/create/image` response shape.

## Disabled-First Nutrition Rollout

The safe sequence is deliberately staged:

1. Select `legacy_nutrition` explicitly; keep all four safety flags false and deploy the normal service.
2. Back up the Sabor data volume and confirm Mealie has its own independent
   backup and rollback plan.
3. Enable only `SABOR_NUTRITION_ENABLED=true`; keep auto-on-import,
   auto-publish, and estimate publication false.
4. Restart, call `nutrition_status`, and confirm sidecar schema version `9`.
5. Inspect exact food mappings and unresolved reports. Apply manual mappings or
   conversions only after previewing them with `apply=false`.
6. Pass the recipe data-readiness gate below before spending provider quota.
7. Calculate a small, explicitly approved set of exact recipe slugs without
   publication and review coverage, confidence, assumptions, and provenance.
8. Publish only explicitly approved recipes with `publish=true` after comparing
   the proposed evidence with existing Mealie nutrition.
9. Run a bounded backfill only after both the pipeline and dataset are accepted.
   Keep publication off until calculated results have been reviewed.
10. Consider automatic import calculation and automatic publication as separate
   later changes, each requiring its own review and rollback plan.

Edit `.env` to change nutrition flags, checking for conflicting values in any
local override. Restart after changes; never print the resolved environment.

The same existing flags control just-in-time behavior. The master flag gates
recipe-backed weekly scheduling. `AUTO_ON_IMPORT` enables the existing structured-creation enrichment path;
the unified URL/text/image/video importer currently does not invoke that hook, and `AUTO_PUBLISH` permits only
eligible, unprotected, Sabor-owned or empty nutrition to be published. No flag
enables estimate publication unless `PUBLISH_ESTIMATES` is separately true; its
checked-in default remains false. There is no queue, workflow table, or backfill
activation in this path.

Blocked weekly calls return `scheduled=false` and an exact `set_mealplan_entry`
continuation; they make no calendar POST. Provider rate-limit, unavailable, and
20-attempt-budget errors include bounded retry timing. The first response reports
the normalized interval exactly (`Retry-After: 0` becomes one second); cache hits
report the ceiling-rounded positive seconds remaining, bounded to 300. At expiry,
a caller may retry. Repeats during cooldown return the same failure and
continuation without provider HTTP; retries count toward the 20-attempt budget.
Concurrent cold misses for one canonical term or provider ID share one upstream
sequence and cache result under a fixed 64-stripe process-local lock; waiters
consume no provider budget. Different stripes proceed concurrently. Import calls return the same
failure under `nutrition_warning` while preserving parent `ok=true` and do not
retry inside the request. Partial valid provider cache writes remain usable.

Recipe-backed scheduling acquires its process-local stripe before duplicate
lookup. A lookup timeout or non-2xx returns
`nutrition_schedule_duplicate_lookup_failed`, is retryable, preserves the exact
`set_mealplan_entry` continuation, and performs zero POSTs. The final recipe GET,
revision comparison, and POST remain under that same lock. A process-local lock
does not coordinate separate Sabor processes.

Use `nutrition_correct_recipe(..., apply=false)` first. The preview performs
authoritative food/unit GET validation, reports current and candidate revisions,
changed paths, readiness, consequences, and the exact typed proposal. `apply=true`
also performs GET validation and makes zero writes, including while nutrition is
disabled or the recipe changes concurrently. It returns
`nutrition_correction_external_update_required`, expected/current revisions, the
normalized proposal, and an `external_required` action naming each Mealie field.
Update those fields in Mealie, execute the emitted
`nutrition_recipe_readiness(slug)` continuation, then execute the original
`set_mealplan_entry` continuation. Scheduling remains blocked until that fresh
recipe GET/readiness pass proves the correction. This connector has no correction
PATCH or rollback path.

AI-generated new recipes should satisfy the readiness schema at creation.
Imports retain conservative normalization and never fail solely because nutrition
enrichment or readiness fails. Existing weekly recipes are never auto-edited.

## Recipe Data-Readiness Gate

Apply this gate before any pilot or backfill. Engine correctness and input-data
fitness are independent decisions: a pipeline PASS does not authorize processing
recipes whose source structure cannot support reliable nutrition.

### Per-Recipe Checklist

- [ ] A positive serving count or yield supports the intended per-serving result.
- [ ] Every consumed ingredient has a structured quantity; intentionally
      unbounded quantities remain explicitly unresolved rather than guessed.
- [ ] Every consumed ingredient is linked to the correct Mealie food identity.
- [ ] Units express the source meaning and are not embedded only in free text.
- [ ] Every non-mass unit has a reviewed density, piece weight, package weight,
      or culinary-measure conversion for that food.
- [ ] Ranges preserve their bounds and use the documented midpoint policy.
- [ ] Alternatives identify the selected food or remain unresolved.
- [ ] Optional, non-consumed, marinade, and frying ingredients have explicit
      consumption semantics.
- [ ] Title-only section rows are distinguishable from edible ingredients.
- [ ] Existing Mealie nutrition and manual-protection intent have been recorded
      before publication is considered.

### Batch Admission

Do not start provider resolution or calculation for a batch until its readiness
inventory has been reviewed. Admit only recipes with no unexplained missing
quantity, food, unit, conversion, serving, or consumption-semantic fields. Keep
unready recipes in a remediation queue with reason codes; they are data debt, not
calculation failures.

For a backfill, choose a small representative sample first. Require reviewed
coverage and confidence before proceeding in bounded batches. Stop on sustained
rate limiting, payment failures, unexpected mutation, or systemic readiness defects.

Run `nutrition_recipe_readiness(slug)` for a bounded detailed report or
`nutrition_cookbook_readiness(cookbook_name="<your-cookbook>", limit=100,
detail_limit=25)` for a privacy-safe aggregate. These tools work while nutrition
is disabled. They perform Mealie GETs and may inspect an existing sidecar, but
they do not initialize or migrate SQLite, call a provider or parser, calculate,
persist, queue, or PATCH.

A completed assessment returns `ok=true` whether it is ready or blocked. Use the
reported gates and root counts for admission; do not use the informational score
to waive blockers. `ok=false` means the assessment could not be completed.
This is a preflight admission decision, not a replacement for the calculator's
aggregate coverage/confidence result or the publisher's final policy checks.
`sourceStructure` covers quantity, food, unit, consumption, and serving semantics
only. `evidence`, `totalCalculation`, and `backfill` require structurally usable
inputs plus evidence the runtime can consume. Valid low-confidence resolution or
conversion evidence is reported as a warning and remains calculation/backfill
usable; nutrient profiles below `SABOR_NUTRITION_MIN_PROFILE_CONFIDENCE` remain
blocking because the service rejects them. `publication` is intentionally more
conservative and does not pass while warnings or publication conflicts remain.

Empty/default nutrition fields do not create a publication conflict. Meaningful
supported fields are compared with the latest immutable published payload in
actual Sabor publish history using the publisher's shared supported-field
canonicalization. An exact match is Sabor-owned; edited values are a mismatch,
while missing, malformed, oversized, or legacy history is unknown and requires
publication-only review. Later recalculation and mutable publication-flag changes
do not invalidate history. The explicit boolean `true` at either
`extras.saborNutritionProtected` or `settings.saborNutritionProtected` always
blocks preview and apply publication; other flag values do not. A present,
non-null, non-object `extras` or `settings` value also blocks conservatively
because its flag cannot be inspected safely. These cases do not block
calculation-only work.
Single-recipe output returns at most 50 issues and 65,536 serialized JSON bytes,
with issue/warning counts and omitted counts. Cookbook cards return at most 10
issues and 10 warnings each and the whole response is bounded to 131,072 serialized JSON bytes.
`limit` is restricted to 1-100 and `detail_limit` to 0-25, so aggregate card
output is bounded to 250 issues. Cookbook cards always omit source text,
previews, descriptions, instructions, and raw payloads. The bounded aggregate
`publicationProvenanceCounts` reports all assessed recipes as `absent`, `owned`,
`mismatch`, `unknown`, or `protected`, even when their cards are omitted.

Presentation strings use normalized whitespace and a deterministic trailing `~`
when truncated. Caps count JSON-escaped content bytes, including the marker:
recipe `id` 128, `slug` 200, `name` 300; issue `ingredientId` 128,
`originalText` 300, `reason` 128, and `message`, `action`, or `suggestion` 300.
Cookbook display names are capped at 300 bytes.
Classification, root counts, gates, and fingerprints use the complete semantic
inputs before these presentation caps, so equal-looking truncated identifiers do
not collapse distinct recipes or issues.

### Semantic Mealie PATCH Verification

Compare a sanitized preimage and postimage for every explicit recipe PATCH.
Ignore only fields Mealie derives or canonicalizes when the following rules hold:

- Derived display text may change only as a consequence of the reviewed
  structured values; it must preserve the same ingredient or instruction meaning.
- `dateUpdated`, `updatedAt`, and equivalent server-managed timestamps may change.
- Instruction identifiers may be regenerated only when instruction order and
  user-authored semantic content are unchanged.
- Ingredient identities, quantities, units, notes, ordering, servings,
  instructions, settings outside the approved field, and unrelated recipe fields
  must match semantically.
- If any user-authored meaning changes or equality cannot be established, stop
  the operation, retain evidence, restore the disabled runtime, and require
  explicit rollback or acceptance authorization. Do not auto-accept based on an
  HTTP success response.

A publication counts toward pipeline PASS only after this semantic comparison is
closed in protected evidence with its projection hashes, changed-ID and field
counts, persistence checks, and explicit acceptance decision. An initial incident
report proves the stop boundary but is not final acceptance proof. API/read-model
verification must not be described as browser UI proof.

## Mutation Warnings

These operations are not validation commands:

| Operation | Explicit guard | Mutation boundary |
|---|---|---|
| `apply_spanish_catalog` | Separate apply tool | Creates missing Mealie units |
| `nutrition_set_food_mapping` | `apply=true` | Writes manual sidecar resolution |
| `nutrition_set_food_profile` | `apply=true` | Atomically writes a reviewed manual per-100-g sidecar profile |
| `nutrition_set_conversion_override` | `apply=true` | Writes manual sidecar conversion |
| `nutrition_calculate_recipe` | Nutrition enabled | Writes/migrates sidecar and may call Spoonacular |
| `nutrition_publish_recipe` | `publish=true` | Writes sidecar and PATCHes Mealie |
| `nutrition_correct_recipe` | None; both modes are read-only | GET-validates and returns an in-memory proposal; `apply=true` labels the required Mealie edit `external_required` |
| recipe-backed `set_mealplan_entry` | Nutrition master flag | Calculates as needed, then POSTs only after eligibility, revision, and duplicate checks |
| `scripts/nutrition_pilot.py` | Exact slugs; `--publish` for Mealie writes | Always may write sidecar/query provider; `--publish` also PATCHes Mealie |
| `nutrition_backfill_personal_recipes` | `execute=true`; separate `publish` | Enumerates cookbook and writes sidecar; optional Mealie PATCHes |
| `nutrition_recipe_readiness`, `nutrition_cookbook_readiness` | None; always read-only | Mealie GETs and optional existing-sidecar inspection only |
| Bring!, memory, recipe, planning, and shopping mutation tools | Tool-specific call | Mutate their named external system or local memory |

"Dry run" is boundary-specific. The pilot without `--publish` avoids Mealie
publication but is not read-only: calculation can initialize/migrate SQLite,
persist evidence, and consume provider quota. Backfill preview with
`execute=false` does not enumerate or calculate; `execute=true` is a live
operation even when `publish=false`.

For a reviewed profile, call `nutrition_set_food_profile` first with
`apply=false` and inspect the exact target, normalized fields, payload hash,
overwrite impact, precedence, and any prior evidence. Re-submit the same reviewed
values with `apply=true` only after approval. A refresh returns the previous
payload hash and source version; rollback is a new explicit apply of the prior
reviewed values, not an automatic history rewrite. The tool never writes Mealie
or invokes the nutrient provider.

Never run pilot or backfill against production merely to confirm that a deploy
worked. Review readiness and calculation evidence before any batch operation.

## Backup Boundaries

There are three independent recovery domains:

| Domain | Back up | Does not cover |
|---|---|---|
| Sabor source/config | Reviewed Git revision and protected deployment configuration | Runtime volume data or Mealie |
| Sabor data volume | Entire `/data` volume, including `MEMORY.md`, `nutrition.db`, and SQLite companion files | Mealie recipes and provider state |
| Mealie | Mealie's own database and media according to its operations procedure | Sabor sidecar evidence |

Use a volume-level snapshot or backup mechanism that preserves SQLite
consistency. Quiesce Sabor writes first unless the backup system provides an
application-consistent SQLite snapshot. Treat backups as sensitive because they
contain user preferences, recipe evidence, and potentially provider payloads.
Do not store them in this repository.

Use the portable [backup and restore runbook](backup-restore.md) for
the Compose `sabor_data` volume; verify its project-specific Docker name before use.

Before a nutrition schema change, record the current source revision, sidecar
schema version, engine version, and backup location. Never edit the SQLite schema
manually in production.

## Rollback Boundaries

Code rollback and data rollback are separate decisions:

- A source rollback restores the previously reviewed files and container image.
- Disabling nutrition stops new nutrition activity but does not downgrade or
  delete the sidecar.
- SQLite migrations are forward-only. Older code may not understand a newer
  schema; restore the matching pre-migration volume snapshot if compatibility
  is not explicitly documented and tested.
- Sidecar rollback does not revert nutrition already published to Mealie.
- Mealie rollback must use Mealie's own backup or the sidecar publish history as
  evidence for a deliberate repair; Sabor has no automatic cross-system
  transaction or rollback command.
- Never use `docker compose down -v` as rollback. It is data deletion.

After rollback, rerun transport health checks and read-only integration checks.

## Setup

Run `sabor-mcp setup` in a terminal for English/español prompts, or
`sabor-mcp setup --non-interactive --json --store sqlite --lang es` for automation.
Use `--help` for pricing, Mealie, LLM and Bring options. Omitted flags retain existing
file values and never add keys: without `--store`, the store the runtime would pick
is kept (`SABOR_STORE`, then `[store] backend`, then Mealie when a Mealie URL is set,
otherwise SQLite).
When config.toml has no `[general] toolsets` yet, setup writes
`core, import, shopping_sync, preferences` (plus `pricing` when a price provider is
enabled) so onboarding can save the cooking profile; an existing toolsets value is
never rewritten, and without config.toml the runtime default is unchanged. `SABOR_CONFIG` overrides the OS config path; non-empty environment
values override file settings at runtime. `SABOR_LANGUAGE` accepts `es` or `en`.

Store credentials with `sabor-mcp setup secret mealie_api_token` in an interactive
terminal. Supported names: `mealie_api_token`, `llm_api_key`, `gemini_api_key`,
`bring_email`, `bring_password`, `spoonacular_api_key`, `database_url`. Values never appear in
output or config.toml: only `keyring`/`file` references do. The system keyring
(service `sabor`) is preferred; an unavailable backend falls back to owner-only
0600 `secrets.toml` beside config.toml. `--backend file` forces the fallback;
`--backend keyring` fails rather than falling back. Protect this directory and backups.

JSON reports changed, missing_secrets and exact next_steps. Exit codes: 0 success,
2 invalid configuration, 3 inaccessible storage. SQLite is initialized locally;
Mealie and Postgres setup make no network calls. Repeating identical flags leaves the file
untouched. Changes atomically replace it and preserve the previous version as
`config.toml.bak`; comments are not retained in the rewritten file.
