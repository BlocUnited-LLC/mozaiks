#!/usr/bin/env python3
"""
Mozaiks CLI - Developer interface for the Mozaiks platform.

The CLI is a parallel interface to Studio, not Studio's terminal
representation. Both CLI and the Studio host sit on top of the same shared
system capabilities (runtime, platform, generation). CLI owns filesystem and
process concerns; Studio owns the management interface.

CLI commands are developer conveniences and should not expand into a parallel
project-management surface. The canonical build lifecycle — artifact review,
diff, run history, promotion, build state — belongs to Studio.

Commands:
    mozaiks quickstart      Minimal first-run path: bootstrap + open Studio
    mozaiks init <preset>     Create a new app bundle scaffold
    mozaiks serve [path]      Start the Mozaiks runtime for an app workspace
    mozaiks onboard           Guide setup for an existing scaffold
    mozaiks studio          Print workspace status or open Studio
    mozaiks add <feature>     Add feature to existing project
    mozaiks gen <mode>        Convenience shortcut: generate from a prompt
    mozaiks migrations status Read-only generated-app migration health
    mozaiks context index    Index local workspace App Intelligence
    mozaiks sync-agent-guidance  Sync app-local coding-agent guidance safely
    mozaiks info              Show current config and available presets
"""

import argparse
import sys

from mozaiks_cli.commands import (
    add_command,
    context_command,
    gen_command,
    info_command,
    init_command,
    migrations_command,
    onboard_command,
    quickstart_command,
    serve_command,
    studio_command,
    sync_agent_guidance_command,
)
from mozaiksai.version import __version__


def create_parser():
    """Create the argument parser with subcommands."""
    parser = argparse.ArgumentParser(
        prog="mozaiks",
        description="Mozaiks CLI - developer tooling for local workspaces, host processes, and diagnostics",
        epilog="Run 'mozaiks <command> --help' for more info on a command.",
    )

    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # mozaiks quickstart
    quickstart_parser = subparsers.add_parser(
        "quickstart",
        help="Minimal first-run path for builders",
        description=(
            "Bootstrap a workspace with minimal defaults and launch Studio. "
            "This is the recommended path for people building apps through the shared "
            "factory_app workflows. Use the lower-level commands only when you need "
            "explicit workspace configuration or framework control."
        ),
    )
    quickstart_parser.add_argument(
        "--dir",
        dest="directory",
        default=".",
        help="Workspace root to create/configure (default: current directory)",
    )
    quickstart_parser.add_argument(
        "--preset",
        choices=["engine", "chat", "integrated", "full"],
        default="chat",
        help="Scaffold preset to use if the workspace is missing a valid app bundle (default: chat)",
    )
    quickstart_parser.add_argument(
        "--name",
        default=None,
        help="App name to store in the scaffold (default: workspace folder name)",
    )
    quickstart_parser.add_argument(
        "--provider",
        choices=["anthropic", "openai", "local", "other"],
        default=None,
        help="Default AI provider to store in the workspace config",
    )
    quickstart_parser.add_argument(
        "--model",
        default=None,
        help="Default model name to store in the workspace config",
    )
    quickstart_parser.add_argument(
        "--backend-port",
        type=int,
        default=8000,
        help="Backend port to use when launching Studio (default: 8000)",
    )
    quickstart_parser.add_argument(
        "--frontend-port",
        type=int,
        default=3000,
        help="Frontend port to use when launching Studio (default: 3000)",
    )
    quickstart_parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Launch Studio services without opening the browser",
    )

    # mozaiks init
    init_parser = subparsers.add_parser(
        "init",
        help="Initialize new Mozaiks project",
        description=(
            "Create a new Mozaiks app-bundle scaffold that can be served through "
            "the platform host, the Studio host (`--host studio`), or "
            "product hosts. This creates the local workspace shell only; app "
            "creation still happens in Studio."
        ),
    )
    init_parser.add_argument(
        "preset",
        nargs="?",
        default="chat",
        choices=["engine", "chat", "integrated", "full"],
        help="Tier preset to use (default: chat)",
    )
    init_parser.add_argument(
        "--name",
        default=None,
        help="App name (if omitted, the CLI will prompt)",
    )
    init_parser.add_argument(
        "--dir",
        dest="directory",
        default=None,
        help="Target directory (default: derived from app name)",
    )
    init_parser.add_argument(
        "--starter",
        action="store_true",
        help="Seed a starter workflow after creating the blank scaffold",
    )

    # mozaiks serve
    serve_parser = subparsers.add_parser(
        "serve",
        help="Start the Mozaiks runtime for an app workspace",
        description=(
            "Resolve the app bundle at the given workspace path and start the selected "
            "host layer. The platform host serves the app without the Studio management "
            "UI. Use --host studio for the Studio management host "
            "(requires factory_app in the Python path, available when running from the "
            "Mozaiks repo checkout)."
        ),
    )
    serve_parser.add_argument(
        "workspace",
        nargs="?",
        default=".",
        help="Path to the app workspace root (default: current directory)",
    )
    serve_parser.add_argument(
        "--host",
        choices=["runtime", "platform", "studio"],
        default="platform",
        help="Host layer to start (default: platform). Use `studio` for the Studio management host.",
    )
    serve_parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Port to listen on (default: 8000)",
    )
    serve_parser.add_argument(
        "--listen",
        default="0.0.0.0",
        help="Interface to bind (default: 0.0.0.0)",
    )
    serve_parser.add_argument(
        "--reload",
        action="store_true",
        help="Enable uvicorn auto-reload (development only)",
    )

    # mozaiks onboard
    onboard_parser = subparsers.add_parser(
        "onboard",
        help="Guide first-run setup and open Studio",
        description="Configure Mozaiks, create a scaffold when missing, and update the canonical app-bundle config surfaces.",
    )
    onboard_parser.add_argument(
        "--dir",
        dest="directory",
        default=".",
        help="Workspace root containing the active app root at app/ (default: current directory)",
    )
    onboard_parser.add_argument(
        "--preset",
        choices=["engine", "chat", "integrated", "full"],
        default="chat",
        help="Preset to use if onboarding needs to create a missing scaffold (default: chat)",
    )
    onboard_parser.add_argument(
        "--name",
        default=None,
        help="Override the app name during onboarding",
    )
    onboard_parser.add_argument(
        "--provider",
        choices=["anthropic", "openai", "local", "other"],
        default=None,
        help="Default AI provider",
    )
    onboard_parser.add_argument(
        "--model",
        default=None,
        help="Default model name",
    )
    onboard_parser.add_argument(
        "--non-interactive",
        action="store_true",
        help="Use current config and provided flags without prompting",
    )
    onboard_parser.add_argument(
        "--open-studio",
        dest="open_studio",
        action="store_true",
        help="Launch Studio after onboarding completes",
    )
    onboard_parser.add_argument(
        "--backend-port",
        type=int,
        default=8000,
        help="Backend port to use when launching Studio (default: 8000)",
    )
    onboard_parser.add_argument(
        "--frontend-port",
        type=int,
        default=3000,
        help="Frontend port to use when launching Studio (default: 3000)",
    )
    onboard_parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Launch Studio services without opening the browser",
    )

    # mozaiks studio
    studio_parser = subparsers.add_parser(
        "studio",
        help="Inspect or launch Studio",
        description="Read the active workspace and print an app overview summary, or launch Studio when used with --open.",
    )
    studio_parser.add_argument(
        "--dir",
        dest="directory",
        default=".",
        help="Workspace root containing the active app root at app/ (default: current directory)",
    )
    studio_parser.add_argument(
        "--json",
        dest="json_output",
        action="store_true",
        help="Emit the app overview summary as JSON",
    )
    studio_parser.add_argument(
        "--open",
        dest="open_studio",
        action="store_true",
        help="Launch the backend + frontend Studio stack for this workspace",
    )
    studio_parser.add_argument(
        "--backend-port",
        type=int,
        default=8000,
        help="Backend port to use when launching Studio (default: 8000)",
    )
    studio_parser.add_argument(
        "--frontend-port",
        type=int,
        default=3000,
        help="Frontend port to use when launching Studio (default: 3000)",
    )
    studio_parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Launch Studio services without opening the browser",
    )

    # mozaiks add
    add_parser = subparsers.add_parser(
        "add",
        help="Add feature to existing project",
        description="Enable a feature in your Mozaiks project.",
    )
    add_parser.add_argument(
        "feature",
        choices=["modules", "event_bus", "auth", "admin", "chat_ui"],
        help="Feature to enable",
    )
    add_parser.add_argument(
        "--preset",
        help="Upgrade to a preset instead of individual feature",
    )

    # mozaiks gen
    gen_parser = subparsers.add_parser(
        "gen",
        help="Generate workflows or apps using AI",
        description="Generate AI agent workflows or full apps from a descriptive prompt.",
    )
    gen_parser.add_argument(
        "mode",
        nargs="?",
        default=None,
        choices=["workflow", "app"],
        help="What to generate: 'workflow' for agent workflows only, 'app' for full application",
    )
    gen_parser.add_argument(
        "--prompt", "-p",
        default=None,
        help="Description of what you want to build (be detailed)",
    )
    gen_parser.add_argument(
        "--output", "-o",
        default=None,
        help="Output directory (default: ./generated)",
    )
    gen_parser.add_argument(
        "--validation-strategy",
        choices=["e2b", "docker", "local", "skip"],
        default=None,
        help="App validation strategy for AppGenerator runs (default: resolved from the current environment)",
    )

    # mozaiks migrations
    migrations_parser = subparsers.add_parser(
        "migrations",
        help="Inspect generated-app migration health",
        description="Read-only diagnostics for generated-app Data migration history.",
    )
    migrations_subparsers = migrations_parser.add_subparsers(
        dest="migrations_action",
        help="Migration diagnostic actions",
    )
    migrations_status_parser = migrations_subparsers.add_parser(
        "status",
        help="Show generated-app migration health",
        description="Read migration history and report failed, in-progress, and unknown migration states.",
    )
    migrations_status_parser.add_argument(
        "--app-id",
        default=None,
        help="Filter report to one app_id",
    )
    migrations_status_parser.add_argument(
        "--status",
        default=None,
        help="Filter report by migration status, such as applied, in_progress, or failed",
    )
    migrations_status_parser.add_argument(
        "--limit",
        type=int,
        default=100,
        help="Maximum rows to return (default: 100)",
    )
    migrations_status_parser.add_argument(
        "--database-name",
        default=None,
        help="Migration history database name override for diagnostics",
    )
    migrations_status_parser.add_argument(
        "--json",
        dest="json_output",
        action="store_true",
        help="Emit the exact migration health report as JSON",
    )

    # mozaiks context
    context_parser = subparsers.add_parser(
        "context",
        help="Manage local App Intelligence context",
        description=(
            "Developer operations for indexing local workspace App Intelligence. "
            "Studio remains the management UI; this command is a local dogfood/bootstrap helper."
        ),
    )
    context_subparsers = context_parser.add_subparsers(
        dest="context_action",
        help="Context actions",
    )
    context_index_parser = context_subparsers.add_parser(
        "index",
        help="Index local workspace App Intelligence",
        description=(
            "Scan a local workspace, persist safe source-context artifacts, "
            "build an app_context_graph and AppIntelligenceSnapshot, and register a current AppContextVersion."
        ),
    )
    context_index_parser.add_argument(
        "--app-id",
        required=True,
        help="Managed app id to index",
    )
    context_index_parser.add_argument(
        "--workspace",
        default=".",
        help="Workspace root to scan (default: current directory)",
    )
    context_index_parser.add_argument(
        "--artifact-key",
        default="app_intelligence_workspace",
        help="Artifact key for the indexed app_bundle evidence artifact (default: app_intelligence_workspace)",
    )
    context_index_parser.add_argument(
        "--draft",
        action="store_true",
        help="Create the AppContextVersion as draft instead of making it current",
    )
    context_index_parser.add_argument(
        "--generated-artifacts-root",
        default=None,
        help="Override generated artifact output root for the indexed source bundle",
    )
    context_index_parser.add_argument(
        "--json",
        dest="json_output",
        action="store_true",
        help="Emit the App Intelligence index result as JSON",
    )

    # mozaiks sync-agent-guidance
    sync_parser = subparsers.add_parser(
        "sync-agent-guidance",
        help="Safely sync app-local coding-agent guidance",
        description=(
            "Check or apply the generated AGENTS.md, CLAUDE.md, and .claude guidance "
            "for an app workspace. Workspace commands refresh managed blocks automatically; "
            "use this command for manual inspection or repair. By default this only checks. "
            "Existing custom files are not overwritten unless --force is used."
        ),
    )
    sync_parser.add_argument(
        "--dir",
        dest="directory",
        default=".",
        help="Workspace root to sync (default: current directory)",
    )
    sync_parser.add_argument(
        "--check",
        action="store_true",
        help="Check guidance status only (default)",
    )
    sync_parser.add_argument(
        "--write-missing",
        action="store_true",
        help="Create missing guidance files without changing existing files",
    )
    sync_parser.add_argument(
        "--update",
        action="store_true",
        help="Create missing files and update existing Mozaiks managed blocks only",
    )
    sync_parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite guidance files with current templates",
    )

    # mozaiks info
    info_parser = subparsers.add_parser(
        "info",
        help="Show current configuration",
        description="Display current preset, enabled features, and available tiers.",
    )
    info_parser.add_argument(
        "--available",
        action="store_true",
        help="Show all available presets and features",
    )

    return parser


def main():
    """Main entry point for the CLI."""
    parser = create_parser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    try:
        if args.command == "init":
            init_command.run(args)
        elif args.command == "quickstart":
            quickstart_command.run(args)
        elif args.command == "serve":
            serve_command.run(args)
        elif args.command == "onboard":
            onboard_command.run(args)
        elif args.command == "studio":
            studio_command.run(args)
        elif args.command == "add":
            add_command.run(args)
        elif args.command == "gen":
            # Interactive mode if no mode or prompt provided
            if not args.mode or not args.prompt:
                result = gen_command.run_interactive(args)
            else:
                result = gen_command.run(args)
            if result:
                sys.exit(result)
        elif args.command == "migrations":
            result = migrations_command.run(args)
            if result:
                sys.exit(result)
        elif args.command == "context":
            result = context_command.run(args)
            if result:
                sys.exit(result)
        elif args.command == "sync-agent-guidance":
            result = sync_agent_guidance_command.run(args)
            if result:
                sys.exit(result)
        elif args.command == "info":
            info_command.run(args)
        else:
            parser.print_help()
            sys.exit(1)
    except KeyboardInterrupt:
        print("\nAborted.")
        sys.exit(130)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
