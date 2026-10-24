# Sabor

<!-- mcp-name: io.github.JlMoreno00/sabor -->

A kitchen-intelligence layer with built-in SQLite or optional Mealie. Find or import recipes, plan the
week, build a scaled shopping list, estimate its cost, then sync to Bring! with
an explicit preview and digest-bound confirmation. The selected recipe
store is the source of truth for cooking data; core workflows work without Mealie.

Optional non-secret `config.toml` settings use environment > file > defaults.
Set `SABOR_CONFIG` to select a file; see [configuration keys and OS paths](docs/operations.md#optional-toml-configuration).
Secrets remain environment-only; `sabor_capabilities` reports `data.config_source`.

## The everyday flow

1. Call `sabor_capabilities()`; use only tools in `data.registered_tools`.
2. Find recipes with `search_recipes` or `suggest_recipes`; inspect structured
   ingredients and instructions with `get_recipe`. Alternatively preview
   `import_recipe(source="https://example.org/recipe", kind="url")`, ask the
   user, then repeat with `dry_run=False`.
3. Agree a menu and use `set_mealplan_entry` for each approved entry.
4. Create or select a store shopping list, then call
   `add_to_shopping_list(list_id="MEALIE_ID", mealplan_start="2026-10-19", mealplan_end="2026-10-25", servings_multiplier=2.0)`.
   Dates are inclusive. Each recipe occurrence is added; free-text entries are
   skipped with reasons. The multiplier scales native recipe yield, not a guessed
   diner count. This call writes immediately: obtain approval first.
5. Reread `get_shopping_list_items` and estimate the actual list with
   `estimate_cost(source={"kind":"shopping_list","list_id":"MEALIE_ID"}, store={"provider":"mercadona"})`.
   Show consumption and procurement subtotals, coverage and uncertainties. If
   over budget, propose changes and obtain new approval before changing the plan.
6. Discover `get_shopping_sync_targets`. Preview
   `sync_shopping_list(mealie_list_id="MEALIE_ID", target={"provider":"bring","list_id":"BRING_ID"})`.
   Show additions, removals, conflicts, skipped checked items and `preview_digest`.
   Ask; repeat the same call with `confirm=True, preview_digest="APPROVED_DIGEST"`.
   A stale digest or any conflict blocks writes and returns a fresh preview;
   review and ask again rather than blindly retrying or switching to replace.

This is **documented agent orchestration**, not a composite tool or transaction.
Plan → list → estimate_cost → sync crosses independent approval boundaries.
List creation/additions write immediately; cost approval authorizes no purchase.
Report each system's outcome; uncertain writes require readback/reconciliation,
not automatic retries or an assumed cross-system rollback.

## Sabor solo vs Sabor + Mealie

| Capability | Sabor solo | Sabor + Mealie |
|---|---|---|
| Recipes, meal plans, shopping lists | Built-in SQLite; no account or token | Mealie URL + API token |
| URL import | Offline recipe-scrapers / JSON-LD | Native Mealie scraper |
| Text / image / video import | Optional compatible LLM / Gemini; video needs ffmpeg | Optional providers; native image import uses Mealie configuration |
| Bring! sync / Mercadona estimates | Optional credentials / pricing opt-in | Same optional integrations |
| Spanish catalog / legacy nutrition | Not supported | Mealie adapter required |
| Cookbooks | Not supported | Mealie cookbooks (`search_recipes(cookbook_id=...)`, nutrition cookbook tools) |
| Web UI to browse and edit recipes | None: chat only | Mealie's web interface |

`sabor_capabilities` reports Mealie-only toolsets that are unavailable in the
current instance under `data.mealie_only`, each with a `reason`
(`requires_mealie_store` for `legacy_nutrition`, `requires_mealie` for
`legacy_catalog`) and a short `detail`. Already running Mealie? Nothing to
migrate: keep `MEALIE_BASE_URL` and `MEALIE_API_TOKEN` and Sabor keeps using it.
Step-by-step clean installs for both paths: [docs/install-beta.md](docs/install-beta.md).

Bootstrap the Spanish Mealie catalog outside the agent tool list:

```sh
sabor-mcp catalog status
sabor-mcp catalog apply
# Equivalent module entrypoints:
python -m sabor catalog status
python -m sabor catalog apply
```

Both require `MEALIE_BASE_URL` and `MEALIE_API_TOKEN`, independent of
`SABOR_TOOLSETS`. Status is a read-only dry-run; apply creates only missing
curated units and audits foods. Neither updates, merges, deletes nor runs seeders.
Results are indented UTF-8 JSON; exit codes: 0 when `ok` is true, 1 for
configuration/reconciliation failure, 2 for invalid arguments. Review nested
`errors` and `conflicts` even when `ok` is true (legacy semantics).

## Capabilities and providers

| Capability | Provider / requirements | Limits |
|---|---|---|
| Core | Built-in SQLite, or Mealie URL + token | Recipes, suggestions, planning, shopping items and history; API baseline Mealie 3.22.0 |
| Import: URL | SQLite: offline recipe-scrapers + schema.org JSON-LD; Mealie: native scraper | Read-only preview; pages without a recipe return `not_found`; no paid-provider fallback |
| Import: text | OpenAI-compatible endpoint + model (+ key remotely) | Typed recipe extraction; preview can incur LLM charges |
| Import: image | SQLite: vision-capable OpenAI-compatible LLM; Mealie: native import with Mealie's OpenAI configuration | Explicit `kind="image"`, public image URL; SQLite supports preview, Mealie remains commit-only |
| Import: video | `gemini_native` or `openai_compatible` | URL download; up to 100 MiB / 10 minutes; no playlists or local paths; TikTok URLs unsupported (paste the caption/transcript with `kind='text'`) |
| Shopping sync | Bring! email + password **and a recipe store** | Only unchecked source items; does not check off Mealie items |
| Pricing | `pricing` toolset + `SABOR_MERCADONA_ENABLED=true` or `SABOR_OPEN_PRICES_ENABLED=true` | Read-only quantity-aware estimates; Mercadona catalog or Open Prices observations |
| Legacy | Explicit toolset selection plus provider configuration | Promotions, memory, nutrition and Spanish catalog |

Gemini-native uploads the whole video for audio + vision processing using
`GEMINI_API_KEY` (`VIDEO_RECIPE_GEMINI_MODEL` defaults to `gemini-2.5-flash-lite`).
OpenAI-compatible video uses sampled frames and available captions; visual support
depends on the model, and without captions spoken quantities are unavailable.
No generic audio/transcription endpoint is assumed. Text-only models can use
captions with limitations. Neither adapter guarantees extraction accuracy.

Set `SABOR_VIDEO_PROVIDER=gemini_native` or `openai_compatible` to select
explicitly. Otherwise configured Gemini wins, then the compatible endpoint.
An explicitly unconfigured selection does not silently fall back.
LLM-only setups can preview text/video; committing requires a recipe store (SQLite or Mealie).

## Recipe stores and instance boundaries

`RecipeStore` is a typed persistence abstraction for recipes, calendar entries,
shopping lists/items, tags and history. Without `MEALIE_BASE_URL`, SQLite is
selected automatically at `<data dir>/sabor.db`; set `SABOR_STORE_DB` to change the
path. With a non-empty Mealie URL, Mealie remains selected (and requires its token).
Explicit `SABOR_STORE=sqlite` or `SABOR_STORE=mealie` overrides that selection;
invalid values fail startup. SQLite construction and registration perform no
filesystem I/O. Runtime startup initializes the database before serving requests;
direct store use initializes on first operation. Missing parent directories are
created, and opening failures never silently fall back. Back up its data volume.
URL, text, video and image import work with SQLite: URL needs no LLM; text needs
an OpenAI-compatible LLM, image needs a vision-capable compatible model, and video
needs either video adapter above. The Spanish catalog, legacy nutrition and native
URL/image services still require Mealie; SQLite does not advertise native imports.
Tandoor and KitchenOwl are not implemented integrations. Existing slug/list arguments remain unchanged,
including the historical `mealie_list_id` sync argument. There is no public
per-call backend URL or tenant selector.

An `AppContext` binds settings, store, registry and pricing providers to registered
tools. Mealie sessions, Mercadona catalog caches and Bring authentication/cache
state are instance-owned. Every registered tool binds the server's configuration;
nutrition services, caches and locks, memory paths, Gemini credentials, video
tagging and Bring catalog overrides are isolated between registrations. This is
**not a multi-tenant security boundary**: legacy Mercadona promotion/catalog state
and direct-call compatibility globals remain process-wide. Use separate
processes/containers, credentials and data volumes for untrusted households;
isolated registrations must use distinct recipe-store, memory and nutrition database paths.
Direct Python compatibility calls are not equivalent to registered app contexts.

Discovery exposes `data.capabilities.recipe_store` and `pricing` with adapter IDs
and operations; `data.mealie.configured` remains a deprecated compatibility field.
Native import discovery includes `url` (preview/commit) and `image` (commit only).
Configured means locally configured, never authenticated or healthy.

## Quantity-aware pricing

Enable `SABOR_MERCADONA_ENABLED=true` **and add `pricing` to `SABOR_TOOLSETS`**
(for example `core,import,shopping_sync,pricing`). Defaults remain unchanged.
`search_pricing_products` finds candidates; `get_product_price` returns package
and normalized unit prices, quantity basis, timestamps, location, provenance and
uncertainty. `estimate_cost` reads only; it never modifies recipes or buys goods.
`legacy_pricing` now exposes only price drops and new arrivals.

Mercadona search is food-only (non-food top categories 20, 21, 22, 23, 25,
26 are excluded). Whole-word matches rank by the number of distinct normalized
name tokens absent from the query (fewer is closer), then unrequested derivative
modifiers, food metadata, name and ID. Staples beat equally close derivatives
such as condensed milk; requested modifiers are not penalized, and entera,
desnatada and semidesnatada stay neutral. This is lexical closeness, not semantic ingredient identity.
`estimate_cost(..., match_policy="cheapest")` defaults to the compatible food
candidate (search window up to 50) with the lowest checkout cost for the
required quantity, that is whole packages times package price, then the lowest
unit price. For 200 g of flour this picks a 1 kg bag over a cheaper-per-kilo
5 kg bag. Multiple candidates produce `estimated` / `auto_selected_cheapest`
and up to five `alternatives` with names, normalized prices and
`checkout_cost`. Show alternatives and
offer to switch using `product_overrides`; overrides take precedence, but cannot
price non-food. With at least two non-overridden lines sharing normalized name
words and base unit, known quantities are also evaluated together. A common
product replaces per-line choices only when its combined checkout cost is
strictly cheaper than the original grouped procurement; ties keep the original
choices, and basket procurement never increases. For 300 g + 300 g arroz,
500 g at EUR 0.60 loses to 1 kg at EUR 0.90 (EUR 1.20 → 0.90). For 200 g +
900 g with 1 kg at EUR 1.00, mixed choices stay (EUR 1.60, not EUR 1.80).
Changed lines add `auto_selected_cheapest_group` and `grouped_selection` evidence;
their alternatives show checkout costs for the summed quantity (up to five).
Use `match_policy="strict"` to leave ambiguity uncosted; strict is not grouped.
A positive quantity without a unit assumes pieces (`estimated`,
`assumed_count`), matched only to count products. Kitchen measures stay unknown.
Egg reference prices per dozen are divided by 12 for the per-piece basis.

`store={"provider":"mercadona"}` is a **retail context**, not a recipe backend.
Optional `location_id` and `currency` must match the configured adapter (stock
configuration: warehouse `mad1`, EUR); incompatible contexts are rejected, never
silently switched. Omit `store` only when exactly one pricing provider is present.
Mercadona's unofficial API can change. Todoist sync is planned, not shipped here.

Alternatively enable `SABOR_OPEN_PRICES_ENABLED=true` (default `false`) and the
`pricing` toolset. Select `store={"provider":"open_prices","location_id":"ES","currency":"EUR"}`
with `estimate_cost` or `get_product_price`; product IDs are barcodes. Country
is an ISO alpha-2 code (default ES), currency defaults to EUR. Open Prices is
universal crowd observations, not a current catalog: Spanish coverage is sparse
and old. The latest matching country observation wins (ties: latest ID), never
the cheapest. Missing locations/country observations return `not_found`, not zero.
Search returns up to ten products with observations; some may lack a local quote.
Quotes include observation date, public store/city, unknown availability and
`observation` always; `stale_observation` means older than 180 days, `discounted`
marks promotions, `single_observation` means only one matching observation in the
fetched window, and `unknown_unit` leaves package/unit pricing unknown.
Responses are cached per instance for ten minutes (up to 128 requests); prices
inspect only the latest 50 observations, so country coverage can be incomplete.
Attribution: **Open Prices (Open Food Facts), ODbL**, https://prices.openfoodfacts.org/.
Do not combine with non-free data into a redistributed database. Contributor
identifiers, proofs and coordinates are never included in quotes or errors.

Choose exactly one `source` shape:

```json
{"kind":"items","items":[{"name":"arroz","quantity":"500","unit":"g","text":"500 g arroz"}]}
{"kind":"recipe","slug":"arroz-con-verduras","multiplier":"1"}
{"kind":"shopping_list","list_id":"MEALIE_ID"}
{"kind":"mealplan","start_date":"2026-10-19","end_date":"2026-10-25","multiplier":"1"}
```

Inline items work without Mealie; stored references require a recipe store.
Use decimal strings or integers for quantities/multipliers, **not JSON floats**.
Limits: 500 resolved rows, 31 inclusive days, multiplier greater than zero and
at most 20. Repeated meals remain separate occurrences; text-only entries remain
uncosted. No automatic serving-count inference or pantry subtraction occurs.

- `consumption_subtotal`: required quantity × comparable unit price.
- `procurement_subtotal`: whole packages to buy, rounding up **after aggregating**
  demand for the same product/location/currency. For 500 g from a 1 kg package
  priced at EUR 2, consumption is EUR 1 and procurement EUR 2.
- Decimal operands and money are JSON **strings**, with subtotals rounded to two
  places only for presentation. Missing prices are never zero; all-unknown or
  empty estimates have null subtotals. Partial subtotals are not complete budgets.
- Inspect `complete`, `coverage`, `excluded_rows`, per-line `uncertainty.level`
  (`known`, `estimated`, `unknown`) and reasons. Unknown density, piece weight,
  units, ambiguous matches or package size stay unresolved. Availability and
  stale observations remain uncertainties, not purchase guarantees.
- `product_overrides` maps returned line `key` values to selected product IDs;
  inspect candidates first. There is no cross-retailer fallback or mixed-currency
  total. Compare `source_digest` to tell whether the resolved source (line
  keys, names, required quantities and units) changed; it ignores price
  refreshes. `quote_digest` also covers quote observations and changes on every
  catalog refresh. Neither substitutes for sync's `preview_digest`.

## Choose the tool surface

`SABOR_TOOLSETS` defaults to `core,import,shopping_sync`. It is an allowlist,
intersected with configuration readiness. Empty means discovery only; whitespace
is trimmed, duplicates collapse, and unknown names prevent startup.

| Toolset | Selection / configuration |
|---|---|
| `core` | Default; built-in SQLite or Mealie URL + token |
| `import` | Default; recipe store, compatible LLM or Gemini; kinds depend on provider |
| `shopping_sync` | Default; Bring! credentials + recipe store for the tools |
| `pricing` | Opt-in + `SABOR_MERCADONA_ENABLED=true` or `SABOR_OPEN_PRICES_ENABLED=true`; no Mealie required for inline items |
| `legacy_spoonacular` | Opt-in + `SPOONACULAR_API_KEY` |
| `legacy_pricing` | Opt-in + `SABOR_MERCADONA_ENABLED=true` |
| `legacy_memory` | Opt-in; local `MEMORY_FILE` |
| `legacy_nutrition` | Opt-in; operations retain `SABOR_NUTRITION_ENABLED` and existing gates |
| `legacy_catalog` | Opt-in + Mealie |

With Mealie only, the current code registers **23 application tools plus discovery**
(22 core tools and native import). The earlier planned baseline of 21 plus
discovery was superseded by added core functionality.
There are **52 selectable application tools plus the always-on
`sabor_capabilities`** at full configuration. Counts are not a health check.

`sabor_capabilities()` reports version, requested toolsets, actual registered
names, configured capabilities/adapters, supported kinds/operations, disabled
reasons and warnings. It is local discovery, not a network health probe.
It omits credentials, private URLs, paths, email addresses and model IDs.
Restart and refresh client discovery after configuration changes.

## Results and approval

Unwrap successful results through `data`:

```json
{"ok":true,"data":{"preview":true,"actions":[]}}
```

Failures use a sanitized envelope (optional `data` carries safe partial outcomes):

```json
{"ok":false,"error":{"code":"provider_unconfigured","message":"Recipe store is not configured.","provider":"mealie"}}
```

Codes: `invalid_argument`, `provider_unconfigured`, `unsupported_operation`,
`not_found`, `ambiguous_target`, `authentication_failed`, `rate_limited`,
`timeout`, `upstream_error`, `invalid_response`, `conflict`, `partial_failure`.
MCP schema validation can reject a call before a tool runs.

- `confirm=False` previews recipe updates, shopping item removal, clear/delete
  lists, clear calendar ranges and both sync modes. Show preview → ask → confirm.
- Import defaults to `dry_run=True`; preview may download media/upload to an
  external processor and consume quota, but does not create recipes.
  Ask for processing consent **before** sending private content.
- Mealie image import cannot preview; obtain explicit approval for `dry_run=False`.
  SQLite image preview downloads the image and sends it to the configured LLM.
- Other writes require client-side approval even without a server flag.
  Legacy `apply`, `publish` and `execute` gates retain their separate meanings.
- Sync accepts optional `preview_digest`. Supplying the approved preview's digest
  binds confirmation to that source, target, mode, actions and application scope;
  a mismatch returns `conflict` with a fresh preview under `data.preview`, zero
  writes. Any nonempty conflicts block confirmation even with a matching digest.
  Without a digest, compatibility behavior only rechecks current snapshots within
  the confirmed call; it does not bind to the user's earlier preview. Neither mode
  makes remote edits during apply atomic. Inspect partial outcomes before retrying.
- Append does not remove target entries; differing specifications are conflicts.
  Replace can clear all target purchase entries, including with an empty source.
  Unknown/ambiguous targets fail: there is no first-list fallback.
- Recipe PATCH arrays replace entire arrays. Review removals, not just additions.

## Quickstart: Sabor solo

With Python 3.12+ and [uv](https://docs.astral.sh/uv/), the stdio install is:

```bash
uvx sabor-mcp
```

**Only after the first PyPI release.** Until then, use
`uvx --from git+https://github.com/JlMoreno00/sabor sabor-mcp`, or build a
local wheel with `uv build` and run
`uvx --from dist/sabor_mcp-0.3.0-py3-none-any.whl sabor-mcp`.
No Mealie configuration is needed. The console command defaults to stdio;
`sabor-mcp --transport http` instead serves Streamable HTTP on loopback
(`127.0.0.1:8080/mcp`) unless you set `SABOR_HOST` explicitly.

For standalone Docker (HTTP), **only after the first GHCR release**:

```bash
docker run -p 127.0.0.1:9170:8080 -v sabor-data:/data ghcr.io/jlmoreno00/sabor:0.3.0
```

Until then build locally with `docker build -t sabor:local .` and use
`sabor:local` in that command. No Mealie variables are needed.
Connect to `http://127.0.0.1:9170/mcp`.

Data lives in `SABOR_DATA_DIR` when set, otherwise an existing `/data`
directory (Docker), otherwise the OS user data directory:
`$XDG_DATA_HOME/sabor` or `~/.local/share/sabor` on Linux,
`~/Library/Application Support/sabor` on macOS, `%LOCALAPPDATA%\\sabor` on Windows.
SQLite, memory, nutrition and catalog cache paths derive from it; explicit
per-file overrides still win. Persist the Docker volume and back up local data.

Video import needs system **ffmpeg / ffprobe** (included in Docker); install
ffmpeg separately for uvx. Missing binaries return a clear
`provider_unconfigured` error rather than disabling core tools.

## Quickstart: Docker — Sabor + Mealie

You need Docker with Compose v2. Nothing else: the `mealie` profile also starts
a Mealie for Sabor (pinned to the tested Mealie 3.22.0).

1. **Configure.** From the checkout run `cp .env.example .env` and set
   `MEALIE_BASE_URL=http://mealie:9000` in `.env`.
2. **Start and connect Mealie.** Run `docker compose --profile mealie up -d`,
   open Mealie at `http://localhost:9925`, sign in as the administrator and
   create an API token (profile > API Tokens > Generate). Paste it into
   `MEALIE_API_TOKEN` in `.env`.
3. **Apply and connect your agent.** Run
   `docker compose --profile mealie up -d --force-recreate sabor` and register
   `http://127.0.0.1:9170/mcp` in your MCP client
   ([Connect your MCP client](#connect-your-mcp-client)).

Add `COMPOSE_PROFILES=mealie` to `.env` to drop `--profile mealie` from later
commands. Already running Mealie? Leave the profile out and point
`MEALIE_BASE_URL` at it, for example `http://host.docker.internal:9925` for a
Mealie on the Docker host; then `docker compose up -d` starts only Sabor. Data
persists in the `sabor_data` and `mealie_data` volumes, and both services
publish only on loopback (`127.0.0.1:9170` and `127.0.0.1:9925`; change the
Sabor port with `SABOR_HOST_PORT` and Mealie's in
`docker-compose.override.yml`). Sabor is built locally until a release image
is published to `ghcr.io/jlmoreno00/sabor`.

Check the transport without running an application tool:

```bash
curl -fsS -X POST \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"healthcheck","version":"1.0.0"}},"id":1}' \
  http://127.0.0.1:9170/mcp
```

An initialize response checks the server and transport, not provider credentials.

### Without Docker

Use Python 3.12. Local video processing also needs `ffmpeg` and `curl`.

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
export SABOR_HOST=127.0.0.1
export SABOR_PORT=9170
export SABOR_DATA_DIR="$PWD/.local-data"
python3 sabor_mcp.py
```

Keep `.local-data` outside version control. These environment variables set the
same endpoint as Docker; without them the runtime binds to `0.0.0.0:8080`.
Nutrition remains disabled. Do not paste real tokens into shared shell history.

## Connect your MCP client

For stdio, let the MCP client launch uvx (after the first PyPI release):

```json
{
  "mcpServers": {
    "sabor": {
      "command": "uvx",
      "args": ["sabor-mcp", "--transport", "stdio"]
    }
  }
}
```

Before that release use
`"args": ["--from", "git+https://github.com/JlMoreno00/sabor", "sabor-mcp", "--transport", "stdio"]`.
Keep stdout reserved for the protocol; logs go to stderr.

For Docker or a running HTTP process, register a Streamable HTTP server named
`sabor`. A generic configuration is:

```json
{
  "mcpServers": {
    "sabor": {
      "type": "http",
      "url": "http://127.0.0.1:9170/mcp"
    }
  }
}
```

Configuration field names depend on your client. For Claude Code:

```bash
claude mcp add --transport http sabor http://127.0.0.1:9170/mcp
```

Loopback works only when the client runs on the Docker host. For a containerized
or remote client, configure private connectivity deliberately; do not expose
this endpoint publicly without an authenticated access layer.


## Tool reference by toolset

Signatures below are generated from decorated Python functions; `None` means
optional/unset. Stable IDs are required, not fuzzy list names. Use MCP discovery
for full schemas and current availability.

### Always on

```python
sabor_capabilities()
```

### core

`search_recipes` accepts `sort="name"` (default), `created_at`, `updated_at`,
`rating`, `last_made`, or `random`, with `direction="asc"` or `"desc"`.
Random ignores direction and uses the UTC date (`YYYY-MM-DD`) as its daily
seed, keeping pagination stable within the day for unchanged recipes/filters.
Unrated and never-made recipes sort last in either direction. SQLite has no
ratings: `sort="rating"` returns `unsupported_operation`, not a substitute order.


```python
add_recipe_comment(slug: str, text: str)
log_cooking_event(slug: str, subject: str, message: str | None=None)
get_mealplan_week(start_date: str, end_date: str)
set_mealplan_entry(date_str: str, entry_type: str, title: str, recipe_slug: str | None=None)
delete_mealplan_entry(entry_id: str)
clear_mealplan_week(start_date: str, end_date: str, confirm: bool=False)
search_recipes(query: str='', page: int=1, per_page: int=25, tag_ids: list[str] | None=None, category_ids: list[str] | None=None, food_ids: list[str] | None=None, tool_ids: list[str] | None=None, cookbook_id: str | None=None, require_all: bool=False, sort: str='name', direction: str='asc')
get_recipe(slug: str)
update_recipe(slug: str, patch: dict, confirm: bool=False)
suggest_recipes(foods: list[str], tools: list[str] | None=None, max_missing_foods: int=2, max_missing_tools: int=0, limit: int=10, include_foods_on_hand: bool=True)
create_recipe(name: str, description: str | None=None, recipe_yield: str | None=None, total_time: str | None=None, ingredients: list[str] | None=None, instructions: list[str] | None=None, tags: list[str] | None=None, source_url: str | None=None)
get_shopping_lists()
create_shopping_list(name: str)
delete_shopping_list(list_id: str, confirm: bool=False)
clear_shopping_list(list_id: str, confirm: bool=False)
get_or_create_shopping_list(name: str)
add_to_shopping_list(list_id: str, items: list[str] | None=None, recipe_slugs: list[str] | None=None, mealplan_start: str | None=None, mealplan_end: str | None=None, servings_multiplier: float=1.0)
get_shopping_list_items(list_id: str, checked: bool | None=None, page: int=1, per_page: int=100)
check_shopping_list_items(list_id: str, item_ids: list[str], checked: bool=True)
remove_shopping_list_items(list_id: str, item_ids: list[str], confirm: bool=False)
get_or_create_tag(name: str)
tag_recipe(slug: str, tags: list[str])
```

### import

```python
import_recipe(source: str, kind: str='auto', dry_run: bool=True, language: str='en', max_recipes: int=8, min_confidence: float=0.55)
```

### shopping_sync

```python
get_shopping_sync_targets(provider: str | None=None)
sync_shopping_list(mealie_list_id: str, target: dict, mode: str='append', confirm: bool=False, preview_digest: str | None=None)
```

### Pantry-aware estimates

The [instance preferences](#preferences) default-profile `pantry` list is read
under the preferences lock, even when the `preferences` toolset is disabled.
Missing files mean an empty pantry; corrupt files return a sanitized
`invalid_response` before any list write. No pantry data is changed here.

Every `estimate_cost` source kind accepts optional `exclude_pantry: bool`:
`estimate_cost(source={"kind":"recipe","slug":"rice","exclude_pantry":True})`.
Absent/false leaves the full response unchanged and does not read preferences.
True skips pricing matched lines, keeps them in `lines` and `excluded_rows` with
`uncertainty.reasons: ["in_pantry"]`, counts them in coverage `excluded`, and
adds `pantry: {"applied": true, "matched": N}`. Subtotals exclude those lines;
if nothing is costed, subtotals remain null, not zero.

Matching uses structured food names or `ingredient_name` on free text, then
casefolding, accent removal and collapsed whitespace. Whole names must equal,
or equal after stripping one trailing `s` or `es` on either side. No fuzzy or
substring matching: `huevo` matches `huevos`, but `sal` never matches `salsa`.
This is lexical matching, not quantity-aware stock accounting.

`add_to_shopping_list` always adds every requested line. With a nonempty pantry,
its success data additionally contains `pantry_matches`, for example
`[{"name":"huevos","pantry_entry":"huevo"}]` (or `[]` if none match).
The field is absent for an empty/missing pantry. Pantry hints never block the
addition: if preferences are unreadable or matching fails, the lines are still
added and the response carries `pantry_warning` instead. SQLite, Mealie and Fake use
source lines: inline item text, or each occurrence's recipe ingredients read
before writing. IDs are deliberately omitted because stores can merge lines;
reread `get_shopping_list_items` to identify IDs, then ask for approval and use
`remove_shopping_list_items` if desired. No automatic removal or skipping occurs.

### pricing

```python
search_pricing_products(query: str, store: dict | None=None, limit: int=10)
get_product_price(product_id: str, store: dict | None=None)
estimate_cost(source: dict, store: dict | None=None, product_overrides: dict | None=None, match_policy: str='cheapest')
```

### legacy_spoonacular

```python
spoonacular_search_recipes(query: str='', number: int=5, max_calories: float | None=None, min_calories: float | None=None, max_protein_g: float | None=None, min_protein_g: float | None=None, max_fat_g: float | None=None, min_fat_g: float | None=None, max_carbs_g: float | None=None, min_carbs_g: float | None=None)
spoonacular_nutrition_100g(ingredient: str)
```

### legacy_pricing

```python
mercadona_get_price_drops()
mercadona_get_new_arrivals()
```

### preferences

Opt in with `SABOR_TOOLSETS=core,import,shopping_sync,preferences`; defaults
remain unchanged. These tools need no provider. Preferences are per instance /
household, not authenticated users; only the reserved default profile is used.
Both are provisional choices pending maintainer confirmation: the toolset stays
opt-in, and per-user profiles wait for authenticated users (#28).
Preview changes first, then repeat with `confirm=True` after approval.

```python
get_preferences()
update_preferences(changes: dict, confirm: bool=False)
```

`changes` accepts `set` (replace fields), `add` and `remove` (list fields only).
Fields: `diet`, `dislikes`, `pantry`, `notes` are lists of up to 200 strings;
strings are trimmed, non-empty, at most 200 characters and deduplicated ignoring
case. `household_size` is null or an integer from 1 to 20. Operations run in
set → add → remove order. Unknown keys or invalid values write nothing.
Responses include preferences, updated_at and source (`defaults` or `file`);
updates also include a before/after field diff and preview flag. Preview does
not change the timestamp. Pantry is also available to pricing and list building
without enabling this toolset; see [pantry-aware estimates](#pantry-aware-estimates).

### legacy_memory

Deprecated in favor of `preferences`. These tools still use MEMORY.md unchanged,
including `save_feedback`; ratings/favorites/comments are not redirected.
See [migration instructions](docs/operations.md#structured-preferences-migration).

```python
save_feedback(recipe_name: str, rating: int | None=None, comment: str | None=None, tags: list[str] | None=None)
read_memory(section: str | None=None)
update_pantry(items: list[str])
update_learned_preference(preference: str)
```

### legacy_nutrition

```python
nutrition_recipe_readiness(slug: str, include_suggestions: bool=True)
nutrition_correct_recipe(slug: str, expected_revision: dict[str, str | None], corrections: list[dict[str, Any]], apply: bool=False)
nutrition_status()
nutrition_food_profile(mealie_food_id: str)
nutrition_set_food_profile(mealie_food_id: str, calories_kcal: float | None=None, protein_g: float | None=None, carbohydrate_g: float | None=None, net_carbohydrate_g: float | None=None, fat_g: float | None=None, saturated_fat_g: float | None=None, unsaturated_fat_g: float | None=None, trans_fat_g: float | None=None, fiber_g: float | None=None, sugar_g: float | None=None, sodium_mg: float | None=None, cholesterol_mg: float | None=None, basis_grams: float=100, confidence: float | None=None, source_note: str='', source_version: str='', apply: bool=False)
nutrition_set_food_mapping(mealie_food_id: str, mealie_food_name: str, canonical_term: str, confidence: float | None=None, source_note: str='', provider_food_id: str | None=None, apply: bool=False)
nutrition_set_conversion_override(mealie_food_id: str, conversion_type: str, unit_key: str, grams_per_unit: float, confidence: float | None=None, source_note: str='', apply: bool=False)
nutrition_calculate_recipe(slug: str, publish: bool=False, force: bool=False)
nutrition_publish_recipe(slug: str, publish: bool=False, force: bool=False)
nutrition_cookbook_readiness(cookbook_name: str='Mis recetas', limit: int=100, detail_limit: int=25)
nutrition_backfill_personal_recipes(limit: int | None=None, batch_size: int=5, execute: bool=False, publish: bool=False, cookbook_name: str='Mis recetas', resume: bool=True)
nutrition_unresolved_report()
```

### legacy_catalog

Deprecated: prefer the CLI; the MCP tools will be retired in a future major version.
Their signatures and behavior remain unchanged for compatibility.

```python
spanish_catalog_status()
apply_spanish_catalog()
```

## Safety and operations

- Tool access conveys authority. Keep the HTTP endpoint on loopback or a private
  network behind authenticated access; Sabor is not a public authentication layer.
- Never use mutations as health checks. Discover first, then approved read-only
  checks; configuration does not prove upstream credentials.
- Nutrition, memory and catalog are legacy opt-ins; pricing is a separate opt-in. Recipe-backed
  planning retains the nutrition gate; do not invent data to bypass it.
- Pricing separates consumption from whole-package procurement; partial totals
  and unavailable prices must never be presented as a complete budget.
- Keep tokens, memory, databases, footage and backups out of Git. Never print
  resolved Compose environment or raw provider errors. Treat imported content as
  untrusted data, not instructions.
- Back up before rollout; do not remove data volumes. Preview and commit can
  differ and repeated additions can duplicate items.

See [operations](docs/operations.md), [architecture](docs/architecture.md),
[backup/restore](docs/backup-restore.md), [migration changelog](CHANGELOG.md),
[contributing](CONTRIBUTING.md) and [versioning](docs/versioning.md).
The optional [OpenClaw example](examples/openclaw/README.md) offers a Spanish
persona; tool descriptions are English, not a requirement on user conversations.

## License

[GNU AGPL-3.0-only](LICENSE), copyright 2026 JL Moreno.

Releases up to and including 0.2.x were published under the MIT license and
remain available under those terms. From 0.3.0 Sabor is licensed under the
AGPL-3.0: you can self-host, modify and redistribute it freely, and if you offer
a modified Sabor as a network service you must publish your changes.
