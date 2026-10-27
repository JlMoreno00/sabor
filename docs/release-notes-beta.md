# Release notes: public beta (draft)

**Draft for the maintainer.** The version number, tag and date are decided by
the maintainer according to [versioning.md](versioning.md); nothing has been
tagged or published. Move the relevant `Unreleased` entries of the CHANGELOG
under the chosen version only when the release is created.

## Highlights

- **Sabor works without Mealie.** A built-in SQLite recipe store is the default
  when `MEALIE_BASE_URL` is empty: recipes, meal plans, shopping lists, tags,
  comments and cooking history (#36, #37).
- **Import without Mealie**: URL (offline recipe-scrapers with JSON-LD
  fallback), text, video, and image through a vision-capable compatible LLM;
  conservative Spanish ingredient parsing that always keeps the original text
  (#38).
- **One-command install**: `uvx sabor-mcp` (stdio) or `docker run` (HTTP), with
  "Sabor + Mealie" still available through the Compose `mealie` profile (#33,
  #39).
- Discovery reports Mealie-only toolsets and why they are unavailable
  (`data.mealie_only`) (#40).

## Choosing a store (migration notes)

| You have | Do | Result |
|---|---|---|
| Mealie already configured (`MEALIE_BASE_URL` + token) and `SABOR_STORE` unset | Nothing | Sabor keeps using Mealie; `sabor_capabilities` adds `data.mealie_only: []` (additive) |
| No Mealie | Nothing | Built-in SQLite at `<data dir>/sabor.db` |
| Mealie, but want the built-in store | `SABOR_STORE=sqlite` | SQLite is used; Mealie data is **not** copied |
| A Mealie URL without token | Add the token, or unset the URL | With a URL, Sabor selects Mealie; without a token core stays disabled, by design, so data is never silently written elsewhere |

- New variables: `SABOR_STORE` (`sqlite` or `mealie`), `SABOR_STORE_DB`
  (database path) and `SABOR_DATA_DIR` (data directory; `/data` in Docker).
- `MEALIE_BASE_URL` and `MEALIE_API_TOKEN` are now optional in the manifest and
  the MCP registry metadata.
- Back up `<data dir>` (the `sabor_data` volume in Docker), which now holds the
  recipe database: see [backup-restore.md](backup-restore.md).
- No tool names or arguments changed (`mealie_list_id` is kept).

## Still Mealie-only

- `legacy_nutrition` requires the Mealie recipe store (nutrition stays indexed
  by Mealie IDs until #12); configuring Mealie alongside SQLite/Postgres is not enough.
- `legacy_catalog` (`spanish_catalog_status`, `apply_spanish_catalog`) requires
  configured Mealie credentials, independently of the selected recipe store.
- Cookbooks.
- A web UI to browse and edit recipes.

## Known limitations

- Built-in SQLite slugs are numeric IDs, not derived from the recipe name.
- Images found on imported pages are not stored as recipe media.
- Hosted multi-tenant use is not part of the beta (#28).

## Before announcing (maintainer)

- [ ] Choose the version, update `VERSION`, the manifest and `server.json`
      (a test keeps them equal), move CHANGELOG entries and create the tag.
- [ ] First publications, each with explicit approval: GHCR image, PyPI
      (trusted publisher and `pypi` environment), then the MCP registry.
- [ ] Clean installs on macOS and Linux following
      [install-beta.md](install-beta.md).
- [ ] Announcement (#17), hosted-demand validation (#18) and roadmap (#19).
