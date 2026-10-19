# Changelog

## [Unreleased]

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
