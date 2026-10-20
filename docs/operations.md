# Sabor Operations

Start with configuration validation and health checks. Do not run pilots,
backfills, catalog apply, or publication as a deployment smoke test.

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
| `SABOR_HOST` | `0.0.0.0` | Server | Compose publishes only to host loopback |
| `SABOR_HOST_PORT` | `9170` (Compose) | Docker host binding | Optional, non-secret; loopback only |
| `SABOR_PORT` | `8080` | Server | Must match container port and health check unless all are changed together |
| `MEALIE_BASE_URL` | Empty for registration; legacy client defaults localhost | Mealie tools | Remove trailing slash; use the container-reachable URL in Compose |
| `MEALIE_API_TOKEN` | Empty | Authenticated Mealie tools | Bearer token; secret |
| `SPOONACULAR_API_KEY` | Empty | Spoonacular tools and nutrition profile fetches | Secret |
| `GEMINI_API_KEY` | Empty | Video analysis/import | Secret |
| `VIDEO_RECIPE_GEMINI_MODEL` | `gemini-2.5-flash-lite` | Native video | Same default as 0.1.x; set it to pin another Gemini model |
| `VIDEO_RECIPE_WORKDIR` | `/tmp/video-recipe-mcp` | Video URL processing | Temporary downloaded media |
| `BRING_EMAIL` | Empty | Bring! tools | Account identifier; treat as sensitive |
| `BRING_PASSWORD` | Empty | Bring! tools | Secret |
| `BRING_CATALOG_DIR` | Empty | Optional Bring! catalog override | Directory searched before installed package data |
| `MERCADONA_WAREHOUSE` | `mad1` | Mercadona tools | Public warehouse code |
| `MEMORY_FILE` | `/data/MEMORY.md` | Preference memory | Contains user data; back up and protect it |

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
The new registration flags `SABOR_MERCADONA_ENABLED` and its nutrition snapshot
accept only `true` (case-insensitive); use literal `true` consistently. Numeric
variables are parsed during import; invalid values prevent startup.

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
| `SABOR_MERCADONA_ENABLED` | `false` | Explicit readiness for selected legacy pricing |

Endpoint examples: OpenAI `https://api.openai.com/v1`; Gemini-compatible
`https://generativelanguage.googleapis.com/v1beta/openai/`; Ollama
`http://localhost:11434/v1`. Models must support the requested media; compatible
video uses frames/captions, not whole-video audio. Native Gemini requires its
existing key plus an explicit model. Inside Docker, localhost means the Sabor
container; use an intentionally trusted reachable service address and key.

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
