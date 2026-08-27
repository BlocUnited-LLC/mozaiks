#!/usr/bin/env python3
"""Emit the pytest path list for one CI test shard.

Splits the top-level collection units of ``tests/`` — ``test_*.py`` files plus
subdirectories (pytest recurses into directories exactly as ``pytest tests/``
does) — across N shards by round-robin over the alphabetically sorted unit
list.  The partition is exhaustive and disjoint by construction, so the union
of every shard's selection is identical to running ``pytest tests/``.

Why round-robin over a *sorted* list: each shard then executes an alphabetical
subsequence of the full serial collection order, preserving the relative
inter-file order of the single-job suite.  The suite currently holds latent
inter-file ordering hazards (for example, ``sys.modules.pop("factory_app")``
in later files breaks dotted-path ``monkeypatch.setattr`` in earlier files if
their order inverts) that stay dormant only in that relative order.  This is
also why the CI job does not use pytest-xdist: its dynamic file-to-worker
assignment reorders files nondeterministically and was observed to trip those
hazards.  Do not change this split to a timing-, hash-, or size-based scheme
without revalidating suite-order safety.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

IGNORED_DIRS = {"__pycache__"}


def collection_units(tests_dir: Path) -> list[str]:
    """Top-level collection units of tests_dir, sorted like pytest sorts them."""
    units: list[str] = []
    for entry in tests_dir.iterdir():
        if entry.is_dir() and entry.name not in IGNORED_DIRS:
            units.append(entry.as_posix())
        elif entry.is_file() and entry.name.startswith("test_") and entry.suffix == ".py":
            units.append(entry.as_posix())
    return sorted(units)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shard", type=int, required=True, help="0-based shard index")
    parser.add_argument("--num-shards", type=int, required=True)
    parser.add_argument("--tests-dir", default="tests")
    args = parser.parse_args()

    if not 0 <= args.shard < args.num_shards:
        print(f"--shard must be in [0, {args.num_shards})", file=sys.stderr)
        return 2

    tests_dir = Path(args.tests_dir)
    if not tests_dir.is_dir():
        print(f"tests directory not found: {tests_dir}", file=sys.stderr)
        return 2

    units = collection_units(tests_dir)
    selected = [u for i, u in enumerate(units) if i % args.num_shards == args.shard]
    if not selected:
        print(
            f"shard {args.shard}/{args.num_shards} selected no units "
            f"(only {len(units)} units exist)",
            file=sys.stderr,
        )
        return 2

    print(" ".join(selected))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
