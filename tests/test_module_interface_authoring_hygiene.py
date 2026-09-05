"""Hygiene: no production or factory surface authors module_interface.yaml.

The independently authored ``workflows/{workflow_name}/module_interface.yaml``
v1 artifact was retired as write-only: it was generated and shipped with zero
runtime, semantic, validation, or regenerator readers.  Until the compiler-owned
v2 projection lands, the expected producer count is ZERO — no tool, prompt,
catalog, or template under the production/factory trees may reference the
artifact at all.

When the compiler projection is reintroduced, its renderer becomes the sole
producer and must be added to ``COMPILER_OWNER_ALLOWLIST`` deliberately, in the
same change that adds it.

Exemptions (outside the scanned trees by construction):
- historical docs/CHANGELOG references;
- tests that explicitly assert the absence of the authoring instruction.
"""
from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# Trees that constitute production runtime and factory generation surface.
SCANNED_TREES = ("factory_app", "mozaiksai", "scripts", "examples")

SCANNED_SUFFIXES = {".py", ".yaml", ".yml", ".json", ".md", ".txt", ".j2"}

SKIPPED_DIR_NAMES = {"__pycache__", "node_modules", ".git", ".venv"}

TOKEN = "module_interface"

# The future compiler-owned v2 projection renderer is the only surface that
# may ever produce module_interface.yaml.  It is not on main yet, so this
# allowlist is intentionally empty; add the renderer's repo-relative path here
# in the same PR that introduces it.
COMPILER_OWNER_ALLOWLIST: frozenset[str] = frozenset()


def _scan_tree(tree: Path) -> list[str]:
    offenders: list[str] = []
    for path in sorted(tree.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in SCANNED_SUFFIXES:
            continue
        if any(part in SKIPPED_DIR_NAMES for part in path.parts):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if TOKEN in text:
            offenders.append(path.relative_to(REPO_ROOT).as_posix())
    return offenders


def test_no_surface_authors_module_interface() -> None:
    offenders: list[str] = []
    for tree_name in SCANNED_TREES:
        tree = REPO_ROOT / tree_name
        if tree.is_dir():
            offenders.extend(_scan_tree(tree))
    unexpected = [path for path in offenders if path not in COMPILER_OWNER_ALLOWLIST]
    assert not unexpected, (
        "module_interface.yaml has no legal producer until the compiler-owned "
        "v2 projection lands; remove the reference or, if this IS the compiler "
        f"renderer, add it to COMPILER_OWNER_ALLOWLIST: {unexpected}"
    )


def test_compiler_owner_allowlist_entries_exist() -> None:
    # A stale allowlist entry would silently re-open the authoring hole.
    missing = [entry for entry in COMPILER_OWNER_ALLOWLIST if not (REPO_ROOT / entry).is_file()]
    assert not missing, f"COMPILER_OWNER_ALLOWLIST names nonexistent files: {missing}"
