# Changelog

## [Unreleased]

## [0.1.1] - 2026-10-19

### Fixed

- Bring! sync rejects unknown list names with the available names instead of
  silently replacing the first list.
- Mercadona cost estimates require whole-word matches and strip leading
  quantities and units; salt no longer matches salsa or salmon.

### Changed

- **Breaking for 0.x callers:** `bring_sync_list`, `clear_shopping_list`,
  `delete_shopping_list` and `clear_mealplan_week` now preview by default and
  make no content changes unless `confirm=True`. Review the preview and obtain
  user approval before confirming.
- Cost responses explicitly report `cost_basis: full_package_price`, a note,
  and `total_full_package_cost`; `total_estimated_cost` is retained for compatibility.
- Offline regression tests cover previews, confirmation and cost matching.

## [0.1.0] - 2026-10-19

Initial public release.

### Added

- A single Streamable HTTP MCP endpoint with 52 tools for Mealie recipes,
  shopping lists, meal planning, tags, comments and cooking history.
- Spoonacular recipe and nutrient search, web scraping/import, Gemini video
  recipe extraction/import, Bring! shopping sync and Mercadona price lookup.
- Persistent feedback, pantry notes and learned preferences.
- Spanish catalog reconciliation and conservative import normalization.
- Opt-in nutrition evidence, readiness reports, reviewed mappings and profiles,
  calculation, guarded publication and bounded backfill.
- Portable Docker Compose setup, configuration example, operations and backup
  guides, and an optional generalized OpenClaw/Telegram persona example.
- MIT license.

### Changed

- Integration code is split into a `sabor/` package while preserving the
  `sabor_mcp.py` entry point and public tool contract.
- Nutrition and automatic publication remain disabled by default.
