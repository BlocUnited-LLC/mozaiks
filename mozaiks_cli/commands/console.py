"""mozaiks console - Inspect or launch the Mozaiks Console for an app workspace."""

from __future__ import annotations

import json

from mozaiks_cli.console_launcher import launch_console
from mozaiks_cli.workspace import resolve_active_app_root, resolve_workspace_root


def run(args) -> None:
    """Execute the console command."""
    from mozaiksai.core.runtime.app import build_app_overview_summary, get_missing_console_surfaces

    workspace_root = resolve_workspace_root(getattr(args, "directory", None))
    app_root = resolve_active_app_root(workspace_root)
    missing_surfaces = get_missing_console_surfaces(app_root)
    if missing_surfaces:
        print(f"Error: no valid Mozaiks scaffold found in {workspace_root}")
        print("Missing required files:")
        for rel_path in missing_surfaces:
            print(f"  - {rel_path}")
        print("Run 'mozaiks onboard --dir <workspace>' to create/configure a scaffold first.")
        return

    if getattr(args, "open_console", False):
        result = launch_console(
            workspace_root=workspace_root,
            backend_port=int(getattr(args, "backend_port", 8000)),
            frontend_port=int(getattr(args, "frontend_port", 3000)),
            open_browser=not bool(getattr(args, "no_browser", False)),
        )
        print("Console launched.\n")
        print(f"Backend: {result['backend_url']}")
        if result["console_url"]:
            print(f"Console: {result['console_url']}")
        elif result["frontend_available"]:
            print(f"Frontend: {result['frontend_url']}")
        else:
            print("Frontend shell unavailable; backend is running but the browser Console is not available in this environment.")
        return

    summary = build_app_overview_summary(app_root, surface="cli-home", local_only=True)
    if getattr(args, "json_output", False):
        print(json.dumps(summary, indent=2, ensure_ascii=False))
        return

    _print_app_overview(summary)


def _print_app_overview(summary: dict) -> None:
    app = summary["app"]
    ai = summary["ai"]
    theme = summary["theme"]
    admin = summary["admin"]
    workspace = summary["workspace"]
    home = summary["home"]

    print("App Overview\n")
    print(f"Workspace:         {summary['console']['workspace_root']}")
    print(f"Route:             {summary['console']['route']}")
    print(f"Local Only:        {summary['console']['local_only']}")
    print(f"App:               {app['name']}")
    print(f"Journey:           {app['journey'] or 'not configured'}")
    print(f"First Goal:        {app['first_goal'] or 'not configured'}")
    print(f"Provider / Model:  {ai['provider'] or 'not configured'} / {ai['model'] or 'not configured'}")
    print(f"Theme:             {theme['primary'] or 'not configured'}")
    print(f"Tagline:           {theme['tagline'] or 'not configured'}")
    print(f"Admins:            {', '.join(admin['admins']) if admin['admins'] else 'none'}")
    print(f"Pages:             {workspace['page_count']}")
    print(f"Workflows:         {workspace['workflow_count']}")
    print(f"Entry Point:       {workspace['entry_point'] or 'not configured'}")
    print(f"Runtime Readiness: {workspace['runtime_readiness']}")
    print("\nNext Step:")
    print(f"  {home['next_step']}")
    print("\nUse 'mozaiks console --json' for machine-readable output.")
    print("Launch the full management interface with: mozaiks console --dir <workspace> --open")
