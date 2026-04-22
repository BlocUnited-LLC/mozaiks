"""
mozaiks studio - Local Studio Home summary for an existing Mozaiks app bundle.
"""

from __future__ import annotations

import json
from pathlib import Path

from mozaiksai.core.runtime.app import build_studio_home_summary, get_missing_studio_surfaces


def run(args) -> None:
    """Execute the studio command."""
    workspace_root = _resolve_workspace_root(getattr(args, "directory", None))
    platform_root = workspace_root / "platform"
    missing_surfaces = get_missing_studio_surfaces(platform_root)
    if missing_surfaces:
        print(f"Error: no valid Mozaiks scaffold found in {workspace_root}")
        print("Missing required files:")
        for rel_path in missing_surfaces:
            print(f"  - {rel_path}")
        print("Run 'mozaiks init <preset>' first or point --dir at an existing scaffold.")
        return

    summary = build_studio_home_summary(platform_root, surface="cli-home", local_only=True)
    if getattr(args, "json_output", False):
        print(json.dumps(summary, indent=2, ensure_ascii=False))
        return

    _print_studio_home(summary)


def _resolve_workspace_root(explicit_directory: str | None) -> Path:
    return Path(explicit_directory or ".").resolve()


def _print_studio_home(summary: dict) -> None:
    app = summary["app"]
    ai = summary["ai"]
    theme = summary["theme"]
    admin = summary["admin"]
    workspace = summary["workspace"]
    home = summary["home"]

    print("Studio Home\n")
    print(f"Workspace:         {summary['studio']['workspace_root']}")
    print(f"Route:             {summary['studio']['route']}")
    print(f"Local Only:        {summary['studio']['local_only']}")
    print(f"App:               {app['name']}")
    print(f"Journey:           {app['journey'] or 'not configured'}")
    print(f"First Goal:        {app['first_goal'] or 'not configured'}")
    print(f"Provider / Model:  {ai['provider'] or 'not configured'} / {ai['model'] or 'not configured'}")
    print(f"Theme:             {theme['primary'] or 'not configured'}")
    print(f"Tagline:           {theme['tagline'] or 'not configured'}")
    print(f"Admin Emails:      {', '.join(admin['admin_emails']) if admin['admin_emails'] else 'none'}")
    print(f"Pages:             {workspace['page_count']}")
    print(f"Workflows:         {workspace['workflow_count']}")
    print(f"Entry Point:       {workspace['entry_point'] or 'not configured'}")
    print(f"Runtime Readiness: {workspace['runtime_readiness']}")
    print("\nNext Step:")
    print(f"  {home['next_step']}")
    print("\nUse 'mozaiks studio --json' for machine-readable output or open /studio in the local shell.")