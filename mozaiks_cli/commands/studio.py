"""mozaiks studio - Inspect or launch Studio for an app workspace."""

from __future__ import annotations

import json

from mozaiksai.core.runtime.app import build_studio_home_summary, get_missing_studio_surfaces
from mozaiks_cli.studio_launcher import launch_studio
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
        print("Run 'mozaiks onboard --dir <workspace>' to create/configure a scaffold first.")
        return

    if getattr(args, "open_studio", False):
        result = launch_studio(
            workspace_root=workspace_root,
            backend_port=int(getattr(args, "backend_port", 8000)),
            frontend_port=int(getattr(args, "frontend_port", 3000)),
            open_browser=not bool(getattr(args, "no_browser", False)),
        )
        print("Studio launched.\n")
        print(f"Backend: {result['backend_url']}")
        if result["studio_url"]:
            print(f"Studio:  {result['studio_url']}")
        elif result["frontend_available"]:
            print(f"Frontend: {result['frontend_url']}")
        else:
            print("Frontend shell unavailable; backend is running but browser Studio is not available in this environment.")
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
    print("Launch the full management interface with: mozaiks studio --dir <workspace> --open")
