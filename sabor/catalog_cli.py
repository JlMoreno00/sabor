"""Operator-facing Spanish catalog reconciliation."""
import json
import os
import sys

from sabor.core.config import ConfigError, load_settings
from sabor.core.context import build_context
from sabor.core.toolsets import use_context


def run_catalog(action):
    """Use instance configuration without starting server runtime services."""
    try:
        settings = load_settings(os.environ)
    except ConfigError as exc:
        print(f"Catalog configuration error: {exc}", file=sys.stderr)
        return 1
    if not settings.mealie_configured:
        print("Spanish catalog requires Mealie: configure MEALIE_BASE_URL and MEALIE_API_TOKEN.", file=sys.stderr)
        return 1
    from sabor.integrations.mealie import _reconcile_spanish_catalog
    with use_context(build_context(settings)):
        result = _reconcile_spanish_catalog(apply=action == "apply")
    # Legacy reconciliation includes upstream exception/response text. Do not
    # expose those untrusted strings at the operator boundary (or change MCP).
    if not result.get("ok"):
        result = dict(result, error="catalog_reconciliation_failed")
    for kind in ("units", "foods"):
        for error in result.get(kind, {}).get("errors", []):
            error.pop("body", None)
    def redact(value):
        if isinstance(value, str):
            return value.replace(settings.mealie_api_token, "[redacted]")
        if isinstance(value, list):
            return [redact(item) for item in value]
        if isinstance(value, dict):
            return {key: redact(item) for key, item in value.items()}
        return value
    print(json.dumps(redact(result), ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1
