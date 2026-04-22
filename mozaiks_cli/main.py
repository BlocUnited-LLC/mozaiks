#!/usr/bin/env python3
"""
Mozaiks CLI - Main entry point.

Commands:
    mozaiks init <preset>     Create a new app bundle scaffold
    mozaiks onboard           Guide setup for an existing scaffold
    mozaiks studio            Show Studio Home for the current workspace
    mozaiks add <feature>     Add feature to existing project
    mozaiks gen <mode>        Generate workflows or apps using AI
    mozaiks info              Show current config and available presets
"""

import argparse
import sys

from mozaiks_cli.commands import init_command, onboard_command, studio_command, add_command, info_command, gen_command


def create_parser():
    """Create the argument parser with subcommands."""
    parser = argparse.ArgumentParser(
        prog="mozaiks",
        description="Mozaiks CLI - Project scaffolding for multi-tier development",
        epilog="Run 'mozaiks <command> --help' for more info on a command.",
    )

    parser.add_argument(
        "--version",
        action="version",
        version="%(prog)s 0.1.0",
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # mozaiks init
    init_parser = subparsers.add_parser(
        "init",
        help="Initialize new Mozaiks project",
        description="Create a new Mozaiks app bundle scaffold from a tier preset.",
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

    # mozaiks onboard
    onboard_parser = subparsers.add_parser(
        "onboard",
        help="Guide setup for an existing scaffold",
        description="Collect app intent and update the canonical app-bundle config surfaces.",
    )
    onboard_parser.add_argument(
        "--dir",
        dest="directory",
        default=".",
        help="Workspace root containing platform/ and brand/ (default: current directory)",
    )
    onboard_parser.add_argument(
        "--name",
        default=None,
        help="Override the app name during onboarding",
    )
    onboard_parser.add_argument(
        "--journey",
        choices=["new_app", "existing_app"],
        default=None,
        help="Onboarding track to configure",
    )
    onboard_parser.add_argument(
        "--goal",
        default=None,
        help="First thing the app should help with",
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
        "--tagline",
        default=None,
        help="Brand tagline to store in theme_config.json",
    )
    onboard_parser.add_argument(
        "--theme-primary",
        choices=["teal", "blue", "emerald", "slate", "amber", "rose"],
        default=None,
        help="Primary brand color token",
    )
    onboard_parser.add_argument(
        "--admin-email",
        default=None,
        help="Admin email to write into platform/config/admin.json",
    )
    onboard_parser.add_argument(
        "--existing-url",
        default=None,
        help="Existing app URL for the existing_app track",
    )
    onboard_parser.add_argument(
        "--host-owned-summary",
        default=None,
        help="Summary of what should remain host-owned during augmentation",
    )
    onboard_parser.add_argument(
        "--non-interactive",
        action="store_true",
        help="Use current config and provided flags without prompting",
    )

    # mozaiks studio
    studio_parser = subparsers.add_parser(
        "studio",
        help="Show local Studio Home summary",
        description="Read the current workspace and show the first local/private Studio Home surface.",
    )
    studio_parser.add_argument(
        "--dir",
        dest="directory",
        default=".",
        help="Workspace root containing platform/, brand/, and ui/ (default: current directory)",
    )
    studio_parser.add_argument(
        "--json",
        dest="json_output",
        action="store_true",
        help="Emit the Studio Home summary as JSON",
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
