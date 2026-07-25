"""mozaiks studio - Inspect or launch Mozaiks Studio for an app workspace."""

from __future__ import annotations

import json

from mozaiks_cli.studio_launcher import launch_studio
from mozaiks_cli.workspace import resolve_active_app_root, resolve_workspace_root


def run(args) -> None:
    """Execute the studio command."""
    from mozaiksai.core.runtime.app import build_app_overview_summary, get_missing_studio_surfaces

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

    from mozaiks_cli.commands.sync_agent_guidance import auto_sync_agent_guidance
    auto_sync_agent_guidance(workspace_root)

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
            print(f"Studio: {result['studio_url']}")
        elif result["frontend_available"]:
            print(f"Frontend: {result['frontend_url']}")
        else:
            print("Frontend shell unavailable; backend is running but Studio is not available in this environment.")
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
    refinement_policy = ai.get("refinement_policy") or {}
    refinement_state = "enabled" if refinement_policy.get("enabled") else "disabled"
    refinement_profile = refinement_policy.get("profile")
    refinement_label = (
        f"{refinement_state} ({refinement_profile})"
        if refinement_profile
        else refinement_state
    )

    print("App Overview\n")
    print(f"Workspace:         {summary['studio']['workspace_root']}")
    print(f"Route:             {summary['studio']['route']}")
    print(f"Local Only:        {summary['studio']['local_only']}")
    print(f"App:               {app['name']}")
    print(f"Provider / Model:  {ai['provider'] or 'not configured'} / {ai['model'] or 'not configured'}")
    print(f"Refinement Engine: {refinement_label}")
    print(f"Theme:             {theme['primary'] or 'not configured'}")
    print(f"Tagline:           {theme['tagline'] or 'not configured'}")
    print(f"Admins:            {', '.join(admin['admins']) if admin['admins'] else 'none'}")
    print(f"Pages:             {workspace['page_count']}")
    print(f"Workflows:         {workspace['workflow_count']}")
    print(f"Entry Point:       {workspace['entry_point'] or 'not configured'}")
    print(f"Runtime Readiness: {workspace['runtime_readiness']}")
    print("\nNext Step:")
    print(f"  {home['next_step']}")
    print("\nUse 'python -m mozaiks studio --json' for machine-readable output.")
    print("Launch Studio with: python -m mozaiks studio --dir <workspace> --open")
