"""mozaiks CLI — build and dev commands.

Usage:
    python -m mozaiksai.cli build
    python -m mozaiksai.cli dev
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


def _load_app_json() -> dict:
    """Load platform/app.json relative to the repo root."""
    root = Path(__file__).resolve().parents[3]
    candidates = [
        root / "platform" / "app.json",
        root / "app.json",
    ]
    for path in candidates:
        if path.exists():
            with open(path) as f:
                return json.load(f)
    raise FileNotFoundError("app.json not found in platform/ or repo root")


def _run(cmd: list[str], cwd: Path | None = None) -> int:
    print(f"  $ {' '.join(cmd)}", flush=True)
    result = subprocess.run(cmd, cwd=cwd)
    return result.returncode


def cmd_build(args: argparse.Namespace) -> int:
    """Build the Mozaiks web app."""
    root = Path(__file__).resolve().parents[3]
    app_dir = root / "app"

    print("[mozaiks] Building web app")

    rc = _run(["npm", "run", "build"], cwd=app_dir)
    if rc != 0:
        print("[mozaiks] Web build failed.", file=sys.stderr)
        return rc

    print("[mozaiks] Web build complete.")
    return 0


def cmd_dev(args: argparse.Namespace) -> int:
    """Start the Vite dev server."""
    root = Path(__file__).resolve().parents[3]
    app_dir = root / "app"
    return _run(["npm", "run", "dev"], cwd=app_dir)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mozaiks",
        description="Mozaiks developer CLI",
    )
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("build", help="Build the web app")

    sub.add_parser("dev", help="Start the Vite development server")

    return parser


def cli(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "build":
        return cmd_build(args)
    if args.command == "dev":
        return cmd_dev(args)

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(cli())
