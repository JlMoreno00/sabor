# Sabor

One MCP server for recipes, meal planning, shopping, and optional nutrition with Mealie.

## Why Sabor?

Turn a self-hosted Mealie instance into an AI-agent-ready kitchen through one
Streamable HTTP endpoint: manage recipes, meal plans and shopping lists, import
recipes from websites and videos, send groceries to Bring!, and look up Mercadona
prices. Mealie remains the source of truth; optional nutrition uses a local
SQLite evidence sidecar.

## Integrations

| Integration | Features | Required / optional configuration |
|---|---|---|
| Mealie | Recipes, calendar, shopping lists, tags, comments, cooking history | Required: `MEALIE_BASE_URL`, `MEALIE_API_TOKEN` |
| Spoonacular | Recipe search, nutrient search, ingredient nutrition | Optional: `SPOONACULAR_API_KEY` |
| Gemini | Analyze videos and import extracted recipes | Optional: `GEMINI_API_KEY` |
| Bring! | Shopping-list sync and item additions | Optional: `BRING_EMAIL`, `BRING_PASSWORD` |
| Mercadona | Product search, prices, cost estimates, price drops | Optional; no key; warehouse selected by `MERCADONA_WAREHOUSE` |
| Web scraping | Preview and import supported recipe pages | No additional key |
| Nutrition | Readiness, calculation, reviewed mappings and publication | Opt-in, off by default: `SABOR_NUTRITION_ENABLED`; Spoonacular key for provider lookups |
| Local memory | Feedback, pantry notes, learned preferences | No key; persistent `MEMORY_FILE` |

Import normalization asks Mealie to use its configured parser (default name:
`openai`). Sabor does not call OpenAI directly; configure that parser in Mealie
or disable normalization. See [configuration and operations](docs/operations.md).

## Quickstart: Docker

You need Docker Compose v2, a reachable Mealie instance, and a Mealie API token.
From the checkout:

```bash
cp .env.example .env
# Edit .env: set MEALIE_BASE_URL and MEALIE_API_TOKEN.
docker compose up -d
```

The example URL `http://host.docker.internal:9925` reaches Mealie on the Docker
host. For a remote Mealie instance, set its reachable URL instead. The service
stores local data in the `sabor_data` volume and publishes only on loopback at
`127.0.0.1:9170` (change the host port with `SABOR_HOST_PORT`).

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
export MEALIE_BASE_URL=http://localhost:9925
export MEALIE_API_TOKEN='<your-mealie-token>'
export SABOR_HOST=127.0.0.1
export SABOR_PORT=9170
export MEMORY_FILE="$PWD/.local-data/MEMORY.md"
export SABOR_NUTRITION_DB="$PWD/.local-data/nutrition.db"
mkdir -p .local-data
python3 sabor_mcp.py
```

Keep `.local-data` outside version control. These environment variables set the
same endpoint as Docker; without them the runtime binds to `0.0.0.0:8080`.
Nutrition remains disabled. Do not paste real tokens into shared shell history.

## Connect your MCP client

Register a Streamable HTTP server named `sabor`. A generic configuration is:

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

## Tool overview

Sabor exposes **52 tools**. Use your client's tool discovery for argument schemas.

| Area | Tools |
|---|---|
| Recipes and history | `search_recipes`, `get_recipe`, `create_recipe`, `get_or_create_tag`, `tag_recipe`, `add_recipe_comment`, `log_cooking_event` |
| Mealie shopping | `get_shopping_lists`, `create_shopping_list`, `delete_shopping_list`, `add_items_to_shopping_list`, `clear_shopping_list`, `add_recipe_ingredients_to_list`, `get_or_create_shopping_list` |
| Calendar | `get_mealplan_week`, `set_mealplan_entry`, `delete_mealplan_entry`, `clear_mealplan_week` |
| Spanish catalog | `spanish_catalog_status`, `apply_spanish_catalog` |
| Spoonacular | `spoonacular_search_recipes`, `spoonacular_search_by_nutrients`, `spoonacular_nutrition_100g` |
| Web and video | `scrape_recipe`, `import_recipe_to_mealie`, `analyze_video_recipes`, `import_video_recipes_to_mealie` |
| Memory | `save_feedback`, `read_memory`, `update_pantry`, `update_learned_preference` |
| Bring! | `bring_get_lists`, `bring_sync_list`, `bring_add_items` |
| Mercadona | `mercadona_search_products`, `mercadona_get_product_price`, `mercadona_estimate_recipe_cost`, `mercadona_estimate_shopping_list_cost`, `mercadona_get_price_drops`, `mercadona_get_new_arrivals` |
| Nutrition | `nutrition_recipe_readiness`, `nutrition_cookbook_readiness`, `nutrition_correct_recipe`, `nutrition_status`, `nutrition_food_profile`, `nutrition_set_food_profile`, `nutrition_set_food_mapping`, `nutrition_set_conversion_override`, `nutrition_calculate_recipe`, `nutrition_publish_recipe`, `nutrition_backfill_personal_recipes`, `nutrition_unresolved_report` |

## Deployment and safety

- Compose loads `.env`. Deployment-specific networks, environment overrides and
  container naming belong in an ignored `docker-compose.override.yml`; see the
  [override example](docker-compose.override.example.yml). Its nutrition setting
  is illustrative, not a recommendation to enable nutrition immediately.
- Read [operations](docs/operations.md) and [backup/restore](docs/backup-restore.md)
  before changing a running deployment. Never run `docker compose down -v`.
- Ask for user confirmation before mutations. Where provided, use explicit
  `apply` / `publish` flags or `dry_run` previews. Since 0.1.1,
  `bring_sync_list`, `clear_shopping_list`, `delete_shopping_list` and
  `clear_mealplan_week` preview by default. Review the preview, ask the user,
  then repeat with `confirm=True` only after approval. This is breaking for
  0.x callers that previously expected immediate deletion or replacement.
  Other mutations still require client-side user approval; tool access is authority.
- Mercadona estimates match whole ingredient words after stripping leading
  amounts and units. `total_full_package_cost` sums full package prices, not
  quantities used (`cost_basis: full_package_price`); `total_estimated_cost`
  remains as a compatibility alias.
- Nutrition is off by default; auto-on-import, auto-publication and estimate
  publication are also off. Back up before schema-affecting changes. Readiness
  tools work while disabled; recipe-backed scheduling is gated by nutrition.
- Never use mutations as health checks or print resolved environment values.
  Keep credentials, user memory, databases and backups out of Git.

## Development

Tests use mocks and temporary databases, not live provider credentials:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
```

See [Contributing](CONTRIBUTING.md), [architecture](docs/architecture.md),
[versioning](docs/versioning.md), and the [changelog](CHANGELOG.md).

## Optional agent example

The [OpenClaw / Telegram example](examples/openclaw/README.md) supplies a Spanish
cooking persona and onboarding/feedback skills. It is optional: any compatible
MCP client can use Sabor without OpenClaw or Telegram.

## License

[MIT](LICENSE), copyright 2026 JL Moreno.
