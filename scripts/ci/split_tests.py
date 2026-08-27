#!/usr/bin/env python3
"""Emit the pytest path list for one CI test shard.

Splits the top-level collection units of ``tests/`` — test files plus
subdirectories (pytest recurses into directories exactly as ``pytest tests/``
does) — across N shards by round-robin over the alphabetically sorted unit
list.  The partition is exhaustive and disjoint by construction, so the union
of every shard's selection is identical to running ``pytest tests/``.

Which top-level files count as test files is taken from the repository's
active pytest configuration: the ``python_files`` patterns in
``pyproject.toml`` ``[tool.pytest.ini_options]``, falling back to pytest's
built-in default (``test_*.py`` and ``*_test.py``) when the key is unset.
There is deliberately no second hardcoded pattern list here.  If another
supported pytest config source appears (``pytest.ini``, ``setup.cfg`` with a
``[tool:pytest]`` section, or ``tox.ini`` with a ``[pytest]`` section), this
script fails closed rather than reproducing pytest's config-discovery rules.
``pytest.ini`` takes precedence over ``pyproject.toml``; ``tox.ini`` and
``setup.cfg`` are lower-precedence at the same root but are rejected as
competing configuration sources so selection cannot silently drift later.

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
import configparser
import sys
import tomllib
from fnmatch import fnmatchcase
from pathlib import Path

IGNORED_DIRS = {"__pycache__"}

# pytest's built-in default for python_files, used only when the active
# configuration does not set the key.
PYTEST_DEFAULT_PYTHON_FILES = ("test_*.py", "*_test.py")


def _fail(message: str) -> None:
    print(message, file=sys.stderr)
    raise SystemExit(2)


def python_file_patterns(repo_root: Path) -> tuple[str, ...]:
    """Return the active pytest ``python_files`` patterns for repo_root.

    Fails closed on competing config layouts this script does not read.
    ``pytest.ini`` takes precedence over ``pyproject.toml``; ``tox.ini`` and
    ``setup.cfg`` are currently lower-precedence at the same root but are
    rejected conservatively so shard selection cannot silently diverge later.
    """
    if (repo_root / "pytest.ini").exists():
        _fail(
            "pytest.ini found: it takes precedence over pyproject.toml and this "
            "script does not read it. Update scripts/ci/split_tests.py first."
        )
    for candidate, section in (("setup.cfg", "tool:pytest"), ("tox.ini", "pytest")):
        path = repo_root / candidate
        if path.exists():
            parser = configparser.ConfigParser()
            parser.read(path, encoding="utf-8")
            if parser.has_section(section):
                _fail(
                    f"{candidate} has a competing [{section}] pytest section "
                    "that this script does not read. "
                    "Update scripts/ci/split_tests.py first."
                )

    pyproject = repo_root / "pyproject.toml"
    if not pyproject.exists():
        _fail(f"pyproject.toml not found at {pyproject}")

    config = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    ini_options = config.get("tool", {}).get("pytest", {}).get("ini_options", {})
    configured = ini_options.get("python_files")
    if configured is None:
        return PYTEST_DEFAULT_PYTHON_FILES
    if isinstance(configured, str):
        configured = configured.split()
    if not configured or not all(isinstance(p, str) for p in configured):
        _fail(f"unsupported python_files value in pyproject.toml: {configured!r}")
    return tuple(configured)


def collection_units(tests_dir: Path, patterns: tuple[str, ...]) -> list[str]:
    """Top-level collection units of tests_dir, sorted like pytest sorts them."""
    units: list[str] = []
    for entry in tests_dir.iterdir():
        if entry.is_dir() and entry.name not in IGNORED_DIRS:
            units.append(entry.as_posix())
        elif entry.is_file() and any(fnmatchcase(entry.name, p) for p in patterns):
            units.append(entry.as_posix())
    return sorted(units)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shard", type=int, required=True, help="0-based shard index")
    parser.add_argument("--num-shards", type=int, required=True)
    parser.add_argument("--tests-dir", default="tests")
    parser.add_argument(
        "--repo-root",
        default=".",
        help="directory holding the active pytest configuration",
    )
    args = parser.parse_args()

    if args.num_shards < 1 or not 0 <= args.shard < args.num_shards:
        print(f"--shard must be in [0, {args.num_shards})", file=sys.stderr)
        return 2

    tests_dir = Path(args.tests_dir)
    if not tests_dir.is_dir():
        print(f"tests directory not found: {tests_dir}", file=sys.stderr)
        return 2

    patterns = python_file_patterns(Path(args.repo_root))
    units = collection_units(tests_dir, patterns)
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
