# Supabase from scratch — guide for AI agents

> For an AI agent helping a user store Sabor's recipes in Supabase (or any Postgres).
> Spanish summary at the end. Steps marked ⚠️ have not been checked against a real
> Supabase project yet.

## Rules

1. **You never see a password or a connection string.** The user types or pastes them only in a
   terminal, into `sabor-mcp setup supabase`, which reads them with hidden input. If the user pastes
   one in the chat, do not repeat it, tell them to reset the database password, and continue.
2. Sabor connects with its own least-privilege role `sabor_app`. It never uses the publishable/anon
   key nor the secret/service_role key.
3. The Supabase MCP server (if your client has it) is a developer tool: use it only to create or
   inspect the project, preferably `read_only`. Sabor does not need it to run.

## Before you start

- Install the Postgres extra: `uvx --from 'sabor-mcp[postgres]' sabor-mcp --help`
  (before the PyPI release: `uvx --from 'sabor-mcp[postgres] @ git+https://github.com/JlMoreno00/sabor' sabor-mcp --help`).
- Tell the user about the Free plan: 500 MB database, the project **pauses after a week of
  inactivity**, and there are **no automatic backups**. `sabor-mcp doctor` repeats these warnings.

## 1. Create the project (pick one route)

| Step | Route A: Supabase MCP | Route B: Supabase CLI | Route C: dashboard |
|---|---|---|---|
| Account | The user signs up at supabase.com (you cannot). | same | same |
| Project | `list_organizations`, then `create_project` named `sabor` in a nearby region; the user confirms the cost ($0 on Free). | `supabase login`, then `supabase projects create sabor --org-id … --region …` **without** `--db-password` (it would land in shell history and in your context). ⚠️ check whether the CLI prompts for it. | New project. |
| Password | The MCP generates one nobody knows: the user resets it in the dashboard (Database settings → reset database password ⚠️ exact menu). | The one the user typed. | The one the user typed. |

## 2. Copy the connection string (the user does this)

Dashboard → **Connect** → **Session pooler** → copy. It looks like
`postgresql://postgres.<ref>:[YOUR-PASSWORD]@aws-<n>-<region>.pooler.supabase.com:5432/postgres`.

- Use the **Session pooler (port 5432)**. Sabor rejects the transaction pooler (6543): it sets the
  schema search path per session.
- The direct host `db.<ref>.supabase.co` is IPv6-only on Free; Sabor warns about it.
- The pooler host cannot be derived from the region: always copy it.

## 3. Hand it to Sabor (the user types it)

Run, or give the user to run in an interactive terminal:

```bash
sabor-mcp setup supabase --create-role
```

The user pastes the string (hidden). Sabor then:

1. validates it and, for Supabase hosts, adds `sslmode=require` (a weaker `sslmode` is rejected);
2. with the `postgres` string, creates (or, on a rerun, rotates the password of) the role
   `sabor_app` with a password generated locally;
3. lets `sabor_app` create and own the `sabor` schema, runs the migrations (row level security
   on every table, no access for `anon`/`authenticated`), then removes its right to create schemas.
   If `sabor` already exists (e.g. configured earlier with the postgres string), `--create-role`
   transfers ownership of the schema and its tables and sequences to `sabor_app`;
4. stores **only** the `sabor_app` string (OS keychain, or an owner-only `secrets.toml`) and
   forgets the `postgres` one; `config.toml` gets `[store] backend = "postgres"`.

Without `--create-role` the pasted string is stored as is: use that only for a role you already
created with limited privileges. Exit codes: 0 ok, 2 invalid string or no terminal, 3 unreachable.
If a rerun fails after rotating the password, the stored string no longer works and `next_steps`
says so: fix the cause and run `sabor-mcp setup supabase --create-role` again.
⚠️ The pooler user format `sabor_app.<ref>` for custom roles is documented by Supabase but has not
been tried on a real project here.

## 4. Verify

1. `sabor-mcp doctor --json`: `store.backend` = `postgres`, `reachable` = true, `schema_version` set.
2. With the Supabase MCP in read-only mode: `list_tables` for schema `sabor`, and `get_advisors`
   (security) without errors for that schema.
3. Then `sabor_capabilities` from the MCP client: `recipe_store.adapters` contains `postgres`.

## 5. Afterwards

Structured preferences now share the Postgres store (`sabor.preferences`, schema
version 2); local nutrition, Mercadona and memory caches do not move. To copy an
existing local profile, run `sabor-mcp preferences import-file` to preview, then
`sabor-mcp preferences import-file --apply`. Lists merge without case-insensitive
duplicates and household size is copied only when unset. The source
`preferences.json` is never changed or deleted. See [operations](operations.md#structured-preferences-migration).

- Disconnect the Supabase MCP, or keep it with `?project_ref=<ref>&read_only=true`.
- If `doctor` reports the store unreachable, the project may be paused: restore it in the
  dashboard. ⚠️ the exact error of a paused project has not been checked.
- Back up regularly: Free has no automatic backups.

---

## Resumen en español

1. El agente **nunca** ve contraseñas ni cadenas de conexión. El usuario las pega solo en la terminal,
   en `sabor-mcp setup supabase --create-role`, con entrada oculta.
2. Crea el proyecto con el MCP de Supabase, con el CLI (sin `--db-password` en la línea de comandos)
   o desde el dashboard. Si lo creó el MCP, el usuario restablece la contraseña de la base de datos.
3. El usuario copia **Connect → Session pooler** (puerto 5432; el 6543 se rechaza).
4. Sabor crea o rota el rol `sabor_app`, que es dueño solo del esquema `sabor`, aplica las
   migraciones con RLS y guarda únicamente la cadena de `sabor_app`. La de `postgres` se descarta.
5. Verifica con `sabor-mcp doctor --json` y, si tienes el MCP de Supabase, `get_advisors` en solo lectura.
6. Plan Free: el proyecto se pausa tras una semana sin uso y no tiene copias de seguridad automáticas.
