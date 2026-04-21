#!/usr/bin/env python3
"""
Mozaiks CLI - Main entry point.

Commands:
  mozaiks init <preset>     Create new project from preset
  mozaiks add <feature>     Add feature to existing project
  mozaiks gen <mode>        Generate workflows or apps using AI
  mozaiks info              Show current config and available presets
"""

import argparse
import sys

from mozaiks_cli.commands import init_command, add_command, info_command, gen_command


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
        description="Create a new Mozaiks project from a tier preset.",
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
        default="my-app",
        help="App name (default: my-app)",
    )
    init_parser.add_argument(
        "--dir",
        dest="directory",
        default=".",
        help="Target directory (default: current directory)",
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
