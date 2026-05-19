"""mozaiks migrations - Inspect generated-app migration health."""

from __future__ import annotations

import asyncio
import json
import sys
from typing import Any

from mozaiksai.core.runtime.persistence import get_migration_health_report


def run(args) -> int:
    """Execute migration diagnostic commands."""

    action = getattr(args, "migrations_action", None)
    if action != "status":
        print("Error: migrations command requires an action, for example: mozaiks migrations status", file=sys.stderr)
        return 2

    try:
        report = asyncio.run(
            get_migration_health_report(
                app_id=getattr(args, "app_id", None),
                status=getattr(args, "status", None),
                database_name=getattr(args, "database_name", None),
                limit=int(getattr(args, "limit", 100)),
            )
        )
    except Exception as exc:
        print(
            f"Error: unable to load migration health report ({type(exc).__name__}). "
            "Check Mongo configuration and connectivity.",
            file=sys.stderr,
        )
        return 2

    if getattr(args, "json_output", False):
        print(json.dumps(report, indent=2, ensure_ascii=False, default=str))
    else:
        _print_report(report)

    return 1 if report.get("has_blockers") or report.get("has_unknown_statuses") else 0


def _print_report(report: dict[str, Any]) -> None:
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    blockers = "yes" if report.get("has_blockers") else "no"
    unknown = "yes" if report.get("has_unknown_statuses") else "no"

    print("Migration health:")
    print(f"  total:       {summary.get('total', 0)}")
    print(f"  applied:     {summary.get('applied', 0)}")
    print(f"  in_progress: {summary.get('in_progress', 0)}")
    print(f"  failed:      {summary.get('failed', 0)}")
    print(f"  unknown:     {summary.get('unknown', 0)}")
    print(f"  blockers:    {blockers}")
    print(f"  unknown_statuses: {unknown}")

    items = report.get("items") if isinstance(report.get("items"), list) else []
    if not items:
        print("\nRows: none")
        return

    print("\nRows:")
    print("  app_id | migration_id | status | applied_at | failed_at | error_message")
    for item in items:
        if not isinstance(item, dict):
            continue
        print(
            "  "
            f"{_cell(item.get('app_id'))} | "
            f"{_cell(item.get('migration_id'))} | "
            f"{_cell(item.get('status'))} | "
            f"{_cell(item.get('applied_at'))} | "
            f"{_cell(item.get('failed_at'))} | "
            f"{_cell(item.get('error_message'))}"
        )


def _cell(value: Any) -> str:
    text = "" if value is None else str(value)
    return text.replace("\n", " ").replace("\r", " ")[:160]
