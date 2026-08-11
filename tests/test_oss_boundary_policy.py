"""
Governance test: OSS boundary policy documents exist and declare required families.

This test does NOT fail on normal file moves or refactors inside the codebase.
It only checks that the boundary policy document and governing ADRs are present
and contain the required DO-NOT-MOVE family declarations.
"""
from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

_BOUNDARY_POLICY = (
    REPO_ROOT / "docs" / "architecture" / "foundations" / "oss-boundary-families.md"
)

_REQUIRED_ADRS = [
    REPO_ROOT / "docs" / "adr" / "0001-public-architecture-and-oss-strategy-boundary.md",
    REPO_ROOT / "docs" / "adr" / "0003-pre-1-0-oss-proprietary-boundary-freeze.md",
]

# Families that must be declared in the boundary policy document.
# These are path prefixes, not filesystem checks — the test verifies governance
# documentation, not that the directories themselves haven't moved.
_REQUIRED_FAMILY_DECLARATIONS = [
    "factory_app/workflows/AppGenerator",
    "factory_app/workflows/AgentGenerator",
    "factory_app/workflows/ExistingAppDiscovery",
    "factory_app/refinement_harness",
    "factory_app/build_context/AppGenerator",
    "factory_app/build_context/mozaikspay",
    "mozaiksai/core",
    "mozaiksai/hosts",
    "mozaiksai/control_plane",
]

_REQUIRED_POLICY_PHRASES = [
    "DO-NOT-MOVE",
    "mechanism",
    "artifact",
    "Private-by-Default",
]


def test_boundary_policy_document_exists() -> None:
    assert _BOUNDARY_POLICY.exists(), (
        f"OSS boundary policy document is missing: {_BOUNDARY_POLICY.relative_to(REPO_ROOT)}\n"
        "This file is the authoritative DO-NOT-MOVE family registry. "
        "Do not delete it without a replacement and ADR review."
    )


def test_governing_adrs_exist() -> None:
    for adr in _REQUIRED_ADRS:
        assert adr.exists(), (
            f"Governing ADR is missing: {adr.relative_to(REPO_ROOT)}\n"
            "ADR documents are governance anchors — do not delete without replacement."
        )


def test_boundary_policy_declares_required_families() -> None:
    content = _BOUNDARY_POLICY.read_text(encoding="utf-8")
    missing = [f for f in _REQUIRED_FAMILY_DECLARATIONS if f not in content]
    assert not missing, (
        "OSS boundary policy is missing required DO-NOT-MOVE family declarations:\n"
        + "\n".join(f"  - {f}" for f in missing)
        + f"\nUpdate {_BOUNDARY_POLICY.relative_to(REPO_ROOT)} to restore them."
    )


def test_boundary_policy_contains_required_phrases() -> None:
    content = _BOUNDARY_POLICY.read_text(encoding="utf-8")
    missing = [p for p in _REQUIRED_POLICY_PHRASES if p not in content]
    assert not missing, (
        "OSS boundary policy is missing required governance phrases:\n"
        + "\n".join(f"  - {p!r}" for p in missing)
    )


def test_adr_0003_records_no_extraction_candidates() -> None:
    adr = REPO_ROOT / "docs" / "adr" / "0003-pre-1-0-oss-proprietary-boundary-freeze.md"
    if not adr.exists():
        return  # covered by test_governing_adrs_exist
    content = adr.read_text(encoding="utf-8")
    assert "NONE" in content, (
        "ADR 0003 should explicitly record that current OSS extraction candidates are NONE."
    )


def test_adr_0003_records_history_decision() -> None:
    adr = REPO_ROOT / "docs" / "adr" / "0003-pre-1-0-oss-proprietary-boundary-freeze.md"
    if not adr.exists():
        return
    content = adr.read_text(encoding="utf-8")
    assert "KEEP" in content, (
        "ADR 0003 should explicitly record the history retention decision (KEEP)."
    )
