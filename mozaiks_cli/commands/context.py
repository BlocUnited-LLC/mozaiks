"""mozaiks context - local app-context and Context Graph developer operations."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from mozaiksai.control_plane.workspace_snapshot import register_workspace_snapshot


def run(args: Any) -> int:
    action = str(getattr(args, "context_action", "") or "").strip()
    if action == "snapshot":
        return asyncio.run(_run_snapshot(args))
    raise ValueError("context command requires an action, such as 'snapshot'")


async def _run_snapshot(args: Any) -> int:
    app_id = str(getattr(args, "app_id", "") or "").strip()
    if not app_id:
        raise ValueError("--app-id is required")
    workspace = Path(str(getattr(args, "workspace", ".") or ".")).expanduser().resolve()
    if not workspace.exists() or not workspace.is_dir():
        raise ValueError(f"workspace does not exist or is not a directory: {workspace}")

    result = await register_workspace_snapshot(
        app_id=app_id,
        workspace_root=workspace,
        artifact_key=str(getattr(args, "artifact_key", "") or "workspace_snapshot").strip() or "workspace_snapshot",
        make_current=not bool(getattr(args, "draft", False)),
        generated_artifacts_root=getattr(args, "generated_artifacts_root", None),
        source_workflow="cli_context_snapshot",
    )
    payload = {
        "app_id": result.app_id,
        "app_bundle_artifact_version_id": result.app_bundle_artifact_version_id,
        "app_context_version_id": result.app_context_version_id,
        "app_context_artifact_version_id": result.app_context_artifact_version_id,
        "graph_artifact_version_id": result.graph_artifact_version_id,
        "artifact_path": result.artifact_path,
        "indexed_file_count": result.indexed_file_count,
        "health_report": result.health_report,
        "warnings": result.warnings,
    }

    if bool(getattr(args, "json_output", False)):
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    health = result.health_report or {}
    coverage = health.get("coverage") if isinstance(health.get("coverage"), dict) else {}
    print("Workspace context snapshot registered.")
    print(f"  app_id: {result.app_id}")
    print(f"  app_bundle_artifact_version_id: {result.app_bundle_artifact_version_id}")
    print(f"  app_context_version_id: {result.app_context_version_id}")
    print(f"  graph_artifact_version_id: {result.graph_artifact_version_id}")
    print(f"  indexed_file_count: {result.indexed_file_count}")
    print(f"  health_status: {health.get('status') or 'unknown'}")
    if coverage.get("core_surface_file_count") is not None:
        print(f"  core_surface_file_count: {coverage.get('core_surface_file_count')}")
    for warning in health.get("warnings") or []:
        print(f"  warning: {warning}")
    for blocker in health.get("blockers") or []:
        print(f"  blocker: {blocker}")
    if result.warnings:
        print("  scan_warnings:")
        for warning in result.warnings:
            print(f"    - {warning}")
    print(f"  artifact_path: {result.artifact_path}")
    return 0
