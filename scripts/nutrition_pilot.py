#!/usr/bin/env python3
"""Run an explicitly selected nutrition pilot, dry-run by default."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Callable


def run_pilot(slugs: list[str], *, calculate: Callable[..., dict[str, Any]],
              publish: bool = False) -> dict[str, Any]:
    if not slugs or any(not slug.strip() for slug in slugs):
        return {"ok": False, "code": "nutrition_pilot_slugs_required", "processed": 0}
    results = []
    for slug in slugs:
        result = calculate(slug.strip(), publish=publish)
        calculation = result.get("calculation") or {}
        results.append({"slug": slug, "ok": result.get("ok", False),
                        "code": result.get("code"), "status": calculation.get("status"),
                        "coverage": calculation.get("coverage_ratio"),
                        "confidence": calculation.get("confidence"),
                        "calculated_at": calculation.get("calculated_at"),
                        "applied_mapping_count": calculation.get("applied_mapping_count", 0)})
    return {"ok": all(item["ok"] for item in results), "dry_run": not publish,
            "processed": len(results),
            "applied_mapping_count": sum(item["applied_mapping_count"] for item in results),
            "results": results}


def serialize_report(report: dict[str, Any]) -> str:
    return json.dumps(report, ensure_ascii=False, sort_keys=True)


def write_report(report: dict[str, Any]) -> None:
    sys.stdout.buffer.write((serialize_report(report) + "\n").encode("utf-8"))
    sys.stdout.buffer.flush()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("slugs", nargs="+", help="Exact Mealie recipe slugs")
    parser.add_argument("--publish", action="store_true",
                        help="Explicitly permit Mealie publication")
    args = parser.parse_args()
    from sabor_mcp import nutrition_calculate_recipe
    report = run_pilot(args.slugs, calculate=nutrition_calculate_recipe,
                       publish=args.publish)
    write_report(report)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
