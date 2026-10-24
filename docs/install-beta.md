# Beta installation guide

Two supported paths. Follow only this page and the README; if a step fails,
note the step number, your OS and the exact output, and open an issue.

| Path | For | Transport | Needs |
|---|---|---|---|
| A. Sabor solo | Most people: one command, no other service | stdio (uvx) or HTTP (Docker) | Python 3.12+ and uv, or Docker |
| B. Sabor + Mealie | People who already use or want Mealie's web UI | HTTP | Docker with Compose v2 |

Recipes, meal plans and shopping lists work on both paths. Nutrition, the
Spanish catalog, cookbooks and a web UI require Mealie (path B).

## A1. Sabor solo with uvx (stdio)

1. Install [uv](https://docs.astral.sh/uv/) and check `uv --version`.
2. Run Sabor. Until the first PyPI release use the repository:

   ```bash
   uvx --from git+https://github.com/JlMoreno00/sabor sabor-mcp --help
   ```

   After the first release this becomes `uvx sabor-mcp --help`.
3. Register it in your MCP client as a stdio server. Generic configuration:

   ```json
   {
     "mcpServers": {
       "sabor": {
         "command": "uvx",
         "args": ["--from", "git+https://github.com/JlMoreno00/sabor", "sabor-mcp"]
       }
     }
   }
   ```

4. Ask your agent to call `sabor_capabilities`. Expect `recipe_store`
   `configured: true` with the `sqlite` adapter, and `legacy_nutrition` and
   `legacy_catalog` under `mealie_only`.
5. Ask it to create a recipe, add it to this week's meal plan and build a
   shopping list from it.

Data lives in the OS user data directory (`~/.local/share/sabor` on Linux,
`~/Library/Application Support/sabor` on macOS) unless you set
`SABOR_DATA_DIR`. Video import also needs `ffmpeg` and `ffprobe` on `PATH`.

## A2. Sabor solo with Docker (HTTP)

1. Until the first GHCR release, build the image from a checkout:

   ```bash
   git clone https://github.com/JlMoreno00/sabor && cd sabor
   docker build -t sabor:local .
   ```

2. Run it without any Mealie variable:

   ```bash
   docker run -d --name sabor -p 127.0.0.1:9170:8080 -v sabor-data:/data sabor:local
   ```

3. Wait until `docker inspect --format '{{.State.Health.Status}}' sabor` prints
   `healthy`.
4. Register `http://127.0.0.1:9170/mcp` as a Streamable HTTP server in your
   client, then repeat steps 4–5 of A1.

The port is published on loopback only. Do not expose it publicly: Sabor has no
authentication layer.

## B. Sabor + Mealie (Docker Compose)

Follow [Quickstart: Docker — Sabor + Mealie](../README.md#quickstart-docker--sabor--mealie)
in the README. In `sabor_capabilities`, expect the `mealie` recipe store and an
empty `mealie_only` list.

Existing Mealie users have nothing to migrate: with `MEALIE_BASE_URL` set,
Sabor keeps using Mealie (`SABOR_STORE` defaults to `mealie`). Set
`SABOR_STORE=sqlite` only if you deliberately want the built-in store; data is
not copied between stores.

## Verification checklist (beta sign-off)

Record OS, version (`sabor-mcp --help` or the image tag) and date for each run.

| Check | macOS | Linux |
|---|---|---|
| A1: client lists tools; recipe → meal plan → shopping list works | ☐ | ☐ |
| A2: container reaches `healthy`; same flow works over HTTP | ☐ | ☐ |
| B: `docker compose --profile mealie up -d`; token created; Mealie store reported | ☐ | ☐ |
| `sabor_capabilities` without Mealie: core, import, shopping_sync and pricing (with `SABOR_MERCADONA_ENABLED=true`) available; nutrition and catalog in `mealie_only` | ☐ | ☐ |
| Data survives a restart (uvx: run again; Docker: `docker restart sabor`) | ☐ | ☐ |
