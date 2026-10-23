# Versioning and Releases

Sabor uses Semantic Versioning for the externally maintained service contract,
with independent versions for persisted schemas, calculation semantics, and
catalog data.

## Version Authorities

| Version | Current code value | Meaning |
|---|---|---|
| Service release candidate | `mcp-manifest.json` `version` = `0.1.0` | Initial Sabor MCP/configuration release target; no release exists until tagged |
| Nutrition schema | `nutrition/db.py` migrations through `9` | SQLite storage compatibility |
| Nutrition engine | `nutrition/service.py` `ENGINE_VERSION` = `3` | Calculation and evidence semantics/cache identity |
| Catalog schema | Catalog `schema_version` = `1` | Manifest document shape |
| Catalog content | Catalog `catalog_version` = `2026.08.2` | Curated dataset revision |

Do not infer one version from another. A patch release can include a forward
SQLite migration when it is backward-compatible and safely deployable, while a
calculation semantics change may require an engine bump even if no schema
changes.

## SemVer Policy

- **PATCH**: backward-compatible fixes, provider hardening, documentation, and
  internal refactoring with no intended MCP contract change.
- **MINOR**: backward-compatible tools, optional configuration, fields, or
  capabilities.
- **MAJOR**: removed or renamed tools/arguments, incompatible response or
  configuration changes, changed safety defaults, or persisted-data changes
  that require coordinated consumer action.

Safety behavior is part of the public contract. Enabling nutrition,
auto-publication, mutation, or estimate publication by default is not a routine
patch.

The initial public release is `0.1.0`; release publication is finalized by its
annotated tag. In the `0.y.z` line, MINOR may contain breaking changes under SemVer, but every breaking change
must still be called out in the changelog and migration notes. PATCH releases
remain backward-compatible fixes.

## Compatibility Rules

### MCP and Configuration

- Preserve tool names, argument defaults, response fields, error codes, and
  explicit mutation guards within a major version.
- Add optional response fields rather than repurposing existing fields.
- Keep runtime environment names synchronized across code, Compose,
  `.env.example`, manifest metadata, and operations docs.
- Treat secret classification and disabled-first defaults as compatibility
  requirements.

### SQLite Schema

- Add ordered migrations to `MIGRATIONS`; never rewrite a migration that may
  have run in a deployed volume.
- Migrations must be forward-only, idempotent on reopen, and covered by tests
  from the latest previously supported schema.
- Every additive statement checks the live SQLite table, column, or index before
  execution. `schema_version` is recorded only after the complete migration
  succeeds, so restart after an interrupted prefix is safe.
- Back up the volume before deployment and document whether the previous code
  can read the new schema.
- Destructive table rebuilds, data reinterpretation, or required downgrades need
  an explicit migration and rollback design, not manual SQL.
- The `schema_version` table is authoritative. Do not use filenames or service
  versions as a substitute.

### Nutrition Engine

- Bump `ENGINE_VERSION` whenever existing inputs could produce meaningfully
  different evidence or persisted provenance without another cache-key change.
- Include every calculation-affecting recipe, profile, conversion, override,
  and policy semantic in cache invalidation.
- Test one-time recalculation from the previous engine semantics and stable cache
  reuse afterward.
- Engine changes do not automatically publish recalculated values to Mealie.

### Catalogs

- Bump `catalog_version` when curated content changes.
- Bump `schema_version` only when the manifest shape or interpretation changes.
- Reconciliation remains create-only unless a separately reviewed major design
  introduces safe update or migration semantics.

## Release Checklist

1. Confirm the release scope is composed of reviewed work-unit commits.
2. Review all changes since the previous tag and identify MCP, configuration,
   schema, engine, catalog, and operational effects.
3. Update the `Unreleased` changelog entries and choose the SemVer increment.
4. Update `mcp-manifest.json` `version`, `server.json` `version` and its
   package `identifier` tag, and `VERSION` in `sabor/server.py` (a test keeps
   them equal); update schema, engine, or catalog versions only when their
   independent contracts changed.
5. Update README, operations, architecture, and migration notes where affected.
6. Run the complete validation suite:

   ```bash
   PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
   PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile sabor_mcp.py nutrition/*.py scripts/*.py tests/*.py
   docker compose config -q
   ```

7. Scan tracked release content for secrets and runtime data.
8. Verify backup and rollback boundaries for any persisted-data or deployment
   change.
9. Move relevant changelog entries from `Unreleased` to a new version heading
   only when the release is being created.
10. Create an annotated tag after the release commit and review are complete.
11. Deploy only through the reviewed deployment procedure, then verify
    transport health and read-only integrations.

## Publishing the image and the MCP registry entry

Nothing is published automatically from pull requests. Each external
publication needs the maintainer's explicit approval, in this order:

1. **Image.** Pushing the annotated tag `vX.Y.Z` (or running *Publish image*
   by hand) builds `ghcr.io/jlmoreno00/sabor:X.Y.Z` with
   `.github/workflows/publish-image.yml`. The image carries the
   `io.modelcontextprotocol.server.name` label the registry uses to verify
   ownership. Make the GHCR package public after the first publication.
2. **Registry.** Only once that image exists: `server.json` describes it
   (schema `2025-12-11`, validated offline by `tests/test_registry_server_json.py`).
   Publish with the official `mcp-publisher` CLI: `mcp-publisher login github`
   as `JlMoreno00`, then `mcp-publisher publish` from the repository root.

Keep `server.json` environment variables in sync with `mcp-manifest.json`;
the test fails otherwise.

## Tags and Changelog

Use annotated tags in the form `vMAJOR.MINOR.PATCH`, matching
`mcp-manifest.json` exactly. Do not move or reuse a published tag.

`CHANGELOG.md` follows Keep a Changelog categories: `Added`, `Changed`,
`Deprecated`, `Removed`, `Fixed`, and `Security`. Keep `Unreleased` at the top.
Write user and operator impact, not commit-message copies. Do not invent release
dates or reconstruct tags without repository evidence.
