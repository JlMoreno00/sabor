# Install Sabor — instructions for AI agents

> This file is for an AI agent (Cline, Claude Code, Cursor, Codex, Gemini CLI, OpenClaw…).
> If you are a human, read [README.md](README.md), or paste this into your agent:
> "Install and configure Sabor by following https://github.com/JlMoreno00/sabor/blob/main/llms-install.md"
>
> Spanish version below: [Instalar Sabor (en español)](#instalar-sabor--instrucciones-para-agentes-de-ia).

## Rules for the agent

1. Ask ONE question at a time and always offer the default. Talk to the user in their language.
2. NEVER ask for, accept or repeat passwords, tokens, API keys or connection strings in the chat.
   If the user pastes one, do not repeat it: tell them to rotate it, then use `SABOR setup secret` (step 3).
3. Configure Sabor ONLY with `sabor-mcp setup` in a terminal. No MCP tool changes Sabor's configuration.
4. Do not overwrite the MCP client configuration: MERGE a `sabor` entry and keep a `.bak` copy first.
5. Do not invent URLs, paths or flags. If something fails, run `sabor-mcp doctor --json` and show the result.
6. Every step is idempotent: repeating it is safe.

## 1. Detect the environment (do not ask what you can detect)

- OS: macOS, Linux or Windows.
- `uv --version`. If missing, ask permission, then install it:
  macOS/Linux `curl -LsSf https://astral.sh/uv/install.sh | sh`;
  Windows `powershell -c "irm https://astral.sh/uv/install.ps1 | iex"`.
- Choose the launch command `SABOR`: `uvx --python ">=3.12" --from git+https://github.com/JlMoreno00/sabor sabor-mcp`.
  Run `SABOR --help`. After 0.4.0 is published on PyPI, `uvx --python ">=3.12" sabor-mcp`
  is an alternative. If the user picks Supabase/Postgres in step 2, use
  `uvx --python ">=3.12" --from 'sabor-mcp[postgres] @ git+https://github.com/JlMoreno00/sabor' sabor-mcp`
  for both `SABOR` and the client entry in step 4.
- The MCP client you are running in (table in step 4).
- An earlier install: `SABOR doctor --json`. If `config.found` is true, offer to reconfigure or stop.

## 2. Ask (defaults in brackets)

1. Language [the user's language: `es` or `en`].
2. Where to keep recipes: [local on this computer (SQLite)] | Supabase or another Postgres (cloud,
   several devices; follow [docs/supabase-agentes.md](docs/supabase-agentes.md) before step 3) |
   Mealie (they already run one: ask for
   its base URL only: `scheme://host[:port][/path]`, e.g. `https://mealie.home.lan`. If what the
   user gives contains `user:password@`, `?…` or `#…`, do not use or repeat it: ask for the bare base
   URL; the API token goes through `SABOR setup secret mealie_api_token`). If it is `http://` and the
   host is not local (`localhost`, `127.0.0.1`, `host.docker.internal` or a Docker service name), warn
   that the token would travel unencrypted and recommend `https://`.
3. Supermarket prices? [none] | Mercadona | Open Prices (Open Food Facts, crowd-sourced, sparse in Spain).
4. Sync the shopping list with Bring!? [no] | yes.

Sabor needs no AI model or key: you, the agent, read recipes from text, images or video and save
them with `create_recipe`.

## 3. Configure Sabor

```bash
SABOR setup --non-interactive --json --lang LANG --store sqlite
# LANG is the language chosen in question 1 (es or en).
# options, only when chosen (replace every UPPERCASE value; never run a placeholder):
#   --store mealie --mealie-url MEALIE_URL      (the URL the user gave in question 2)
#   Supabase/Postgres: the user runs `SABOR setup supabase --create-role` (docs/supabase-agentes.md)
#   --pricing mercadona [--mercadona-warehouse mad1]   (repeat --pricing to add open_prices)
#   --bring
```

Read the single JSON line: `ok`, `changed`, `config_path`, `store`, `missing_secrets`, `next_steps`.
Exit codes: 0 ok, 2 invalid configuration (fix the flags), 3 store not reachable.

For every name in `missing_secrets` (`mealie_api_token`, `database_url`, `bring_email`,
`bring_password`), the secret must be typed by the user in a terminal:

```bash
SABOR setup secret mealie_api_token
```

It reads the value with hidden input and stores it in the OS keychain (or an owner-only
`secrets.toml` when no keychain is available). If you have no interactive terminal, give the user
that exact command, ask them to run it themselves and wait until they say "done".

## 4. Register the server in the client (always the same, no environment variables)

Entry: command `uvx`, args `["--python", ">=3.12", "--from", "git+https://github.com/JlMoreno00/sabor", "sabor-mcp"]`.
Use the git form in every row below until 0.4.0 is published on PyPI.

| Client | How | File / key |
|---|---|---|
| Claude Code | `claude mcp add --scope user sabor -- uvx --python ">=3.12" --from git+https://github.com/JlMoreno00/sabor sabor-mcp` | managed by the CLI |
| Codex CLI | `codex mcp add sabor -- uvx --python ">=3.12" --from git+https://github.com/JlMoreno00/sabor sabor-mcp` | `~/.codex/config.toml`, `[mcp_servers.sabor]` |
| Gemini CLI | `gemini mcp add --scope user sabor uvx --python ">=3.12" --from git+https://github.com/JlMoreno00/sabor sabor-mcp` | `~/.gemini/settings.json`, `mcpServers` |
| VS Code | `code --add-mcp '{"name":"sabor","command":"uvx","args":["--python",">=3.12","--from","git+https://github.com/JlMoreno00/sabor","sabor-mcp"]}'` | `mcp.json`, key **`servers`** |
| Cursor | merge, or the "Add to Cursor" button in the README | `~/.cursor/mcp.json`, `mcpServers` |
| Claude Desktop (macOS) | merge | `~/Library/Application Support/Claude/claude_desktop_config.json`, `mcpServers` |
| OpenClaw | see [examples/openclaw/README.md](examples/openclaw/README.md) | — |
| Several at once (Node available) | `npx add-mcp "uvx --python >=3.12 --from git+https://github.com/JlMoreno00/sabor sabor-mcp" --name sabor -g` | — |

For example, with the git form: `claude mcp add --scope user sabor -- uvx --python ">=3.12" --from git+https://github.com/JlMoreno00/sabor sabor-mcp`.
Then ask the user to reload or restart the client.

## 5. Verify

1. `SABOR doctor --json` returns `"ok": true` (exit 0). Otherwise follow its `next_steps`.
2. Call the `sabor_capabilities` tool and check:
   - `data.capabilities.recipe_store.adapters` contains the chosen store (`sqlite`, `mealie` or `postgres`);
   - with prices: `data.capabilities.pricing.configured` is true;
   - `data.config_source` is `file`;
   - `data.warnings` is empty or informational only.
3. Real round trip (all `core` tools): first call `get_shopping_lists()` and check for
   "Prueba Sabor". If it already exists, leave it untouched and skip creation on a retry.
   Otherwise, after approval, call `create_shopping_list(name="Prueba Sabor")` and record
   the returned ID. Check that `get_shopping_lists()` returns it, then, after the user agrees,
   call `delete_shopping_list(list_id=…, confirm=True)` only for the ID created by this run.
   Never delete a pre-existing list. (There is no recipe-delete tool; do not test with a recipe.)

## 6. Finish

Say: "Sabor is ready. Say *Start with Sabor* to set up your cooking profile."
Then follow the portable skill [skills/sabor-onboarding/SKILL.md](skills/sabor-onboarding/SKILL.md)
(install it in your client if it supports Agent Skills). The profile (diners, allergies, budget…)
is saved with the preference tools only when they are registered (the `preferences` toolset is
opt-in); it never changes the configuration.

To import recipes (web pages, text, photos and videos), follow the portable skill
[skills/sabor-import/SKILL.md](skills/sabor-import/SKILL.md): you read the source and save
the recipe yourself; Sabor itself never sends it to an AI service. With the Mealie store,
saving a recipe with ingredients also runs Mealie's ingredient parser when
`SABOR_NORMALIZE_IMPORTS` is on (the default). By default it is Mealie's local `nlp` parser;
an effective parser of `openai`, set with `SABOR_INGREDIENT_PARSER` or
`[import] ingredient_parser` in `config.toml`, makes Mealie send the ingredient lines to
its configured AI provider. The agent cannot see that setting, so with the Mealie store
the skill tells the user about this possibility before they approve.

---

# Instalar Sabor — instrucciones para agentes de IA

> Este fichero es para un agente de IA. Si eres una persona, lee [README.md](README.md) o pega en tu
> agente: "Instala y configura Sabor siguiendo https://github.com/JlMoreno00/sabor/blob/main/llms-install.md".
> Los comandos, flags y la tabla de clientes son los mismos que en la versión inglesa de arriba.

## Reglas para el agente

1. Haz UNA pregunta cada vez y propón siempre la opción por defecto. Habla en el idioma del usuario.
2. NUNCA pidas, aceptes ni repitas en el chat contraseñas, tokens, claves de API ni cadenas de
   conexión. Si el usuario pega uno, no lo repitas: dile que lo rote y usa `SABOR setup secret` (paso 3).
3. Configura Sabor SOLO con `sabor-mcp setup` en una terminal. Ninguna tool MCP cambia la configuración.
4. No sobrescribas la configuración MCP del cliente: FUSIONA la entrada `sabor` y guarda antes una copia `.bak`.
5. No inventes URLs, rutas ni flags. Si algo falla, ejecuta `sabor-mcp doctor --json` y enseña el resultado.
6. Todos los pasos son idempotentes: repetirlos no rompe nada.

## Pasos

1. **Detecta el entorno.** SO, `uv --version` (si falta, pide permiso e instálalo con los comandos
   de arriba) y el cliente MCP. Usa `uvx --python ">=3.12" --from git+https://github.com/JlMoreno00/sabor sabor-mcp` en todos los pasos y registros; llamamos `SABOR` a este lanzador. Comprueba `SABOR --help`. Si el usuario elige
   Supabase/Postgres en el paso 2, añade el extra en `SABOR` y en el registro del cliente:
   `uvx --python ">=3.12" --from 'sabor-mcp[postgres] @ git+https://github.com/JlMoreno00/sabor' sabor-mcp`. Ejecuta `SABOR doctor --json`; si `config.found` es true, ofrece reconfigurar o salir.
2. **Pregunta, de una en una:** idioma [el del usuario]; almacén [local en este equipo (SQLite)] |
   Supabase u otro Postgres (en la nube, varios dispositivos; sigue
   [docs/supabase-agentes.md](docs/supabase-agentes.md)) | Mealie (ya tiene uno: pide solo su URL base, `esquema://host[:puerto][/ruta]`; si trae `usuario:contraseña@`, `?…` o `#…`, no
   la uses ni la repitas y pide la URL limpia: el token va por `SABOR setup secret mealie_api_token`. Si es `http://` y el host no es local
   (`localhost`, `127.0.0.1`, `host.docker.internal` o un servicio de Docker), avisa de que el token viajaría
   sin cifrar y recomienda `https://`); precios
   [ninguno] | Mercadona | Open Prices (colaborativo, con pocos datos de España); sincronizar con
   Bring! [no] | sí. Sabor no necesita ningún modelo ni clave de IA: tú, el agente, lees las recetas de
   texto, imagen o vídeo y las guardas con `create_recipe`.
3. **Configura** con `SABOR setup --non-interactive --json --lang IDIOMA` (el idioma elegido) y los flags
   de la sección 3 de arriba, sustituyendo los valores en MAYÚSCULAS (nunca ejecutes un ejemplo). Lee
   `ok`, `changed`, `missing_secrets` y `next_steps`. Para cada nombre de `missing_secrets`, el
   usuario escribe el secreto en una terminal con `SABOR setup secret <nombre>` (entrada oculta;
   se guarda en el llavero del sistema o en un `secrets.toml` 0600). Si no tienes terminal
   interactiva, dale el comando exacto y espera a que diga "hecho".
4. **Registra el servidor** con la tabla de la sección 4, sin variables de entorno y con el mismo
   lanzador del paso 1 (`uvx --python ">=3.12" --from git+https://github.com/JlMoreno00/sabor sabor-mcp`) y pide al usuario que recargue o reinicie el cliente.
5. **Verifica:** `SABOR doctor --json` con `"ok": true`; después la tool `sabor_capabilities`
   (almacén elegido en `recipe_store.adapters`, `pricing.configured` si eligió precios,
   `config_source` = `file`); y una prueba real con tools `core`: primero llama a
   `get_shopping_lists()` y busca "Prueba Sabor". Si ya existe, no la toques y omite la creación
   al repetir la prueba. Si no existe, con aprobación, llama a `create_shopping_list(name="Prueba Sabor")`
   y guarda el ID devuelto. Comprueba que aparece con `get_shopping_lists()` y, cuando el usuario
   lo acepte, llama a `delete_shopping_list(list_id=…, confirm=True)` solo con el ID creado en
   esta ejecución. Nunca borres una lista preexistente. No hay tool para borrar recetas:
   no pruebes con una receta.
6. **Termina:** "Sabor está listo. Di *Empieza con Sabor* para configurar tu perfil de cocina." y
   sigue la skill portable [skills/sabor-onboarding/SKILL.md](skills/sabor-onboarding/SKILL.md). El perfil
   (comensales, alergias, presupuesto…) se guarda con las tools de preferencias solo si están
   registradas (el toolset `preferences` es opcional); nunca cambia la configuración.
