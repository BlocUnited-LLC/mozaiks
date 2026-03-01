#!/usr/bin/env python3
"""
CI Protocol Guard — Frozen Event Envelope Schema
=================================================

This script is designed to run in CI (GitHub Actions, Azure DevOps, etc.)
to prevent accidental modification of frozen transport schemas without a
corresponding schema_version bump.

Usage:
    python scripts/check_event_envelope_protocol_guard.py

Exit codes:
    0 — No frozen schemas modified, or version was bumped (OK)
    1 — Frozen schema changed without a version bump (FAIL)
    2 — Internal error (e.g. missing files)

The guard reads every JSON file under the transport schema directory that
contains `"x-contract-frozen": true` and compares it to the git index.
If any such file has been modified in the current diff, the guard checks
that `schema_version` (or `$defs.*_version`) has also been changed.

If the version was NOT bumped, the script exits 1.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #
REPO_ROOT = Path(__file__).resolve().parent.parent
TRANSPORT_DIR = REPO_ROOT / "mozaiksai" / "core" / "contracts" / "schemas" / "v1" / "transport"

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _git(*args: str) -> str:
    """Run a git command and return stdout (stripped)."""
    result = subprocess.run(
        ["git", *args],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )
    return result.stdout.strip()


def _load_json(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _is_frozen(data: dict) -> bool:
    """Return True if the schema declares itself frozen."""
    return data.get("x-contract-frozen", False) is True


def _extract_version(data: dict) -> str | None:
    """Best-effort extraction of version from a schema dict."""
    # Direct field
    if "schema_version" in data:
        sv = data["schema_version"]
        if isinstance(sv, dict):
            return sv.get("const") or sv.get("default")
        return str(sv)
    # Nested in properties
    props = data.get("properties", {})
    sv_prop = props.get("schema_version", {})
    return sv_prop.get("const") or sv_prop.get("default")


def _git_show_json(rel_path: str) -> dict | None:
    """Load JSON from the git index (HEAD) for comparison."""
    raw = _git("show", f"HEAD:{rel_path}")
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #

def main() -> int:
    if not TRANSPORT_DIR.is_dir():
        print(f"[GUARD] Transport schema dir not found: {TRANSPORT_DIR}", file=sys.stderr)
        return 2

    # Collect frozen schemas
    frozen_files: list[Path] = []
    for f in TRANSPORT_DIR.glob("*.json"):
        try:
            data = _load_json(f)
        except (json.JSONDecodeError, OSError):
            continue
        if _is_frozen(data):
            frozen_files.append(f)

    if not frozen_files:
        print("[GUARD] No frozen schemas found — nothing to protect.")
        return 0

    print(f"[GUARD] Found {len(frozen_files)} frozen schema(s):")
    for f in frozen_files:
        print(f"  • {f.relative_to(REPO_ROOT)}")

    # Detect which files were changed in the current diff (staged + unstaged)
    diff_output = _git("diff", "--name-only", "HEAD")
    staged_output = _git("diff", "--name-only", "--cached")
    changed_set = set(diff_output.splitlines()) | set(staged_output.splitlines())

    violations: list[str] = []

    for fpath in frozen_files:
        rel = str(fpath.relative_to(REPO_ROOT)).replace("\\", "/")
        if rel not in changed_set:
            continue  # not modified — OK

        # File was modified. Check version bump.
        current_data = _load_json(fpath)
        prev_data = _git_show_json(rel)

        current_ver = _extract_version(current_data)
        prev_ver = _extract_version(prev_data) if prev_data else None

        if current_ver == prev_ver:
            violations.append(
                f"  FROZEN schema modified WITHOUT version bump:\n"
                f"    File:    {rel}\n"
                f"    Version: {current_ver!r} (unchanged)"
            )

    if violations:
        print("\n[GUARD] ❌ PROTOCOL VIOLATION — frozen schema(s) changed without version bump:\n")
        for v in violations:
            print(v)
        print(
            "\nTo fix: bump 'schema_version' (or the const in properties.schema_version) "
            "in the modified frozen schema(s) and commit again."
        )
        return 1

    print("[GUARD] ✅ All frozen schemas OK.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
