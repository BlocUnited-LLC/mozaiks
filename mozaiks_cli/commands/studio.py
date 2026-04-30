"""
mozaiks studio - Print workspace status to the terminal.

This command is a developer diagnostic tool. It reads the active workspace and
prints a status summary: app intent, readiness, adapter config, workflow count,
and the recommended next step.

Studio (the management interface at /studio) is a separate, parallel interface.
This command does not replicate Studio — it provides a quick terminal view of
the same workspace state. For the full management interface, run the server and
open /studio in the browser.
"""

from __future__ import annotations

import json

from mozaiksai.core.runtime.app import build_studio_home_summary, get_missing_studio_surfaces
from mozaiks_cli.workspace import resolve_active_app_root, resolve_workspace_root


def run(args) -> None:
    """Execute the studio command."""
    workspace_root = resolve_workspace_root(getattr(args, "directory", None))
    app_root = resolve_active_app_root(workspace_root)
    missing_surfaces = get_missing_studio_surfaces(app_root)
    if missing_surfaces:
        print(f"Error: no valid Mozaiks scaffold found in {workspace_root}")
        print("Missing required files:")
        for rel_path in missing_surfaces:
            print(f"  - {rel_path}")
        print("Run 'mozaiks init <preset>' first or point --dir at an existing scaffold.")
        return

    summary = build_studio_home_summary(app_root, surface="cli-home", local_only=True)
    if getattr(args, "json_output", False):
        print(json.dumps(summary, indent=2, ensure_ascii=False))
        return

    _print_studio_home(summary)


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
    print("\nUse 'mozaiks studio --json' for machine-readable output.")
    print("For the full management interface, run the server and open /studio in the browser.")
