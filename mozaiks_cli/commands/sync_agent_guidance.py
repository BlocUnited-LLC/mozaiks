"""mozaiks sync-agent-guidance - Safely manage app-local coding-agent guidance."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from mozaiks_cli.agent_guidance import (
    AGENT_GUIDANCE_BEGIN,
    AGENT_GUIDANCE_END,
    build_agent_guidance_files,
)
from mozaiks_cli.workspace import resolve_active_app_root, resolve_workspace_root


@dataclass(frozen=True)
class GuidanceStatus:
    relative_path: Path
    status: str
    message: str


def run(args) -> int:
    """Execute the sync-agent-guidance command."""
    workspace_root = resolve_workspace_root(args.directory)
    app_name, preset = _resolve_app_identity(workspace_root)
    mode = _resolve_mode(args)

    statuses = sync_agent_guidance(
        workspace_root=workspace_root,
        app_name=app_name,
        preset=preset,
        mode=mode,
    )

    print(f"Agent guidance sync: {workspace_root}")
    print(f"Mode: {mode}")
    for status in statuses:
        print(f"  [{status.status}] {status.relative_path} - {status.message}")

    counts = _count_statuses(statuses)
    print(
        "\nSummary: "
        + ", ".join(f"{status}={count}" for status, count in sorted(counts.items()))
    )

    if mode == "check" and any(status.status != "current" for status in statuses):
        print("\nRun with --write-missing, --update, or --force to apply changes.")
        return 1
    return 0


def sync_agent_guidance(
    *,
    workspace_root: Path,
    app_name: str,
    preset: str,
    mode: str,
) -> list[GuidanceStatus]:
    """Sync agent guidance files using a safe mode."""
    if mode not in {"check", "write-missing", "update", "force"}:
        raise ValueError(f"Unknown sync mode: {mode}")

    desired_files = build_agent_guidance_files(app_name, preset)
    statuses: list[GuidanceStatus] = []
    for relative_path, desired_content in desired_files.items():
        path = workspace_root / relative_path
        statuses.append(
            _sync_one_file(
                workspace_root=workspace_root,
                relative_path=relative_path,
                path=path,
                desired_content=desired_content,
                mode=mode,
            )
        )
    return statuses


def _sync_one_file(
    *,
    workspace_root: Path,
    relative_path: Path,
    path: Path,
    desired_content: str,
    mode: str,
) -> GuidanceStatus:
    if not _is_within_workspace(workspace_root, path):
        raise ValueError(f"Refusing to write outside workspace: {path}")

    if not path.exists():
        if mode in {"write-missing", "update", "force"}:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(desired_content.rstrip() + "\n", encoding="utf-8")
            return GuidanceStatus(relative_path, "created", "created missing file")
        return GuidanceStatus(relative_path, "missing", "file is missing")

    existing = path.read_text(encoding="utf-8")
    normalized_desired = desired_content.rstrip() + "\n"
    if existing == normalized_desired:
        return GuidanceStatus(relative_path, "current", "already matches template")

    if mode == "force":
        path.write_text(normalized_desired, encoding="utf-8")
        return GuidanceStatus(relative_path, "overwritten", "overwrote full file by request")

    updated = _replace_managed_block(existing, normalized_desired)
    if updated is None:
        return GuidanceStatus(
            relative_path,
            "unmanaged",
            "existing file has no Mozaiks managed block; left unchanged",
        )

    if updated == existing:
        return GuidanceStatus(relative_path, "current", "managed block is current")

    if mode == "update":
        path.write_text(updated, encoding="utf-8")
        return GuidanceStatus(relative_path, "updated", "updated managed block only")

    return GuidanceStatus(relative_path, "outdated", "managed block differs")


def _replace_managed_block(existing: str, desired: str) -> str | None:
    existing_start = existing.find(AGENT_GUIDANCE_BEGIN)
    existing_end = existing.find(AGENT_GUIDANCE_END)
    if existing_start == -1 or existing_end == -1 or existing_end < existing_start:
        return None
    existing_end += len(AGENT_GUIDANCE_END)

    desired_start = desired.find(AGENT_GUIDANCE_BEGIN)
    desired_end = desired.find(AGENT_GUIDANCE_END)
    if desired_start == -1 or desired_end == -1 or desired_end < desired_start:
        raise ValueError("Desired guidance template is missing its managed block markers")
    desired_end += len(AGENT_GUIDANCE_END)

    desired_block = desired[desired_start:desired_end]
    return existing[:existing_start] + desired_block + existing[existing_end:]


def _resolve_mode(args) -> str:
    if getattr(args, "force", False):
        return "force"
    if getattr(args, "update", False):
        return "update"
    if getattr(args, "write_missing", False):
        return "write-missing"
    return "check"


def _resolve_app_identity(workspace_root: Path) -> tuple[str, str]:
    app_root = resolve_active_app_root(workspace_root)
    app_json_path = app_root / "app.json"
    if not app_json_path.exists():
        return workspace_root.name or "my-app", "chat"

    try:
        app_json = json.loads(app_json_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return workspace_root.name or "my-app", "chat"

    app_name = app_json.get("appName") or app_json.get("name") or workspace_root.name or "my-app"
    preset = app_json.get("preset") or "chat"
    return str(app_name), str(preset)


def _count_statuses(statuses: list[GuidanceStatus]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for status in statuses:
        counts[status.status] = counts.get(status.status, 0) + 1
    return counts


def auto_sync_agent_guidance(workspace_root: Path) -> None:
    """Silently update managed agent guidance blocks when the package version changes.

    Called automatically by workspace-facing commands so builders do not need to
    remember ``mozaiks sync-agent-guidance --update`` after upgrading Mozaiks.
    Any failure is swallowed to avoid blocking host startup.
    """
    try:
        from mozaiks_cli.workspace import is_framework_repo_root, resolve_active_app_root

        workspace_root = workspace_root.resolve()
        # Don't auto-sync inside the framework repo itself — it has its own guidance.
        if is_framework_repo_root(workspace_root):
            return
        app_root = resolve_active_app_root(workspace_root)
        if not (app_root / "app.json").exists():
            return
        app_name, preset = _resolve_app_identity(workspace_root)
        statuses = sync_agent_guidance(
            workspace_root=workspace_root,
            app_name=app_name,
            preset=preset,
            mode="update",
        )
        updated = [s for s in statuses if s.status in {"updated", "created"}]
        if updated:
            print(f"[mozaiks] Agent guidance refreshed ({len(updated)} file(s) updated).")
    except Exception:
        pass


def _is_within_workspace(workspace_root: Path, path: Path) -> bool:
    workspace = workspace_root.resolve()
    target = path.resolve()
    return target == workspace or workspace in target.parents
