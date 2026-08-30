"""Drift guard: capability_directory.yaml capabilities_provided must resolve to module.yaml.

For every capability ID listed under ``capabilities_provided`` in
``factory_app/build_context/AppGenerator/capability_directory.yaml``, at least one
``module.yaml`` in the corresponding pack's template tree must declare that ID under
``capabilities[].capability_id``.

This prevents the directory from advertising entitlement-gate capability IDs that were
never added to any module template — capabilities that agents can reference for plan
gating or feature flags but that no generated app will ever actually produce.

Adapter stubs (``services/adapters/notifications/email.py``, etc.) are *not* module
capabilities and must not appear in ``capabilities_provided``. Only IDs that a module
template declares as ``capability_id`` are real entitlement surfaces.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

WORKSPACE = Path(__file__).resolve().parents[1]
BUILD_CONTEXT = WORKSPACE / "factory_app" / "build_context"
CAPABILITY_DIR = BUILD_CONTEXT / "AppGenerator" / "capability_directory.yaml"


def _read_yaml(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _pack_dir(pack_id: str) -> Path:
    """Map capability_directory pack id to build_context directory.

    Pack IDs in the directory use a ``_pack`` suffix convention for clarity
    (e.g. ``messaging_pack``) while the build_context directory drops that
    suffix (e.g. ``messaging``).  Other packs like ``mozaikspay`` and
    ``operator_readiness`` match verbatim.
    """
    dir_name = pack_id.removesuffix("_pack")
    return BUILD_CONTEXT / dir_name


def _pack_capability_source(pack_dir: Path) -> str | None:
    context_path = pack_dir / "context.yaml"
    if not context_path.is_file():
        return None
    context = _read_yaml(context_path)
    pack = context.get("pack") or {}
    source = pack.get("capability_source")
    return source if isinstance(source, str) else None


def _declared_module_capability_ids(pack_dir: Path) -> set[str]:
    """Return all capability_ids declared in any module.yaml under the pack's templates."""
    ids: set[str] = set()
    templates_root = pack_dir / "templates" / "modules"
    if not templates_root.is_dir():
        return ids
    for module_yaml in templates_root.rglob("module.yaml"):
        data = _read_yaml(module_yaml)
        for cap in data.get("capabilities") or []:
            cid = cap.get("capability_id")
            if cid:
                ids.add(cid)
    return ids


def _collect_cases() -> list[tuple[str, str]]:
    """Return (pack_id, capability_id) for every capabilities_provided entry."""
    if not CAPABILITY_DIR.exists():
        return []
    data = _read_yaml(CAPABILITY_DIR)
    cases: list[tuple[str, str]] = []
    for entry in data.get("capabilities") or []:
        pack_id = entry.get("id", "")
        for cap_id in entry.get("capabilities_provided") or []:
            cases.append((pack_id, cap_id))
    return cases


_CASES = _collect_cases()


@pytest.mark.parametrize(
    "pack_id,capability_id",
    _CASES,
    ids=[f"{p}/cap={c}" for p, c in _CASES],
)
def test_capability_directory_id_resolves_to_module(pack_id: str, capability_id: str) -> None:
    """Every capability ID in capabilities_provided must appear in a module template.

    A ``capabilities_provided`` entry that does not resolve to a ``module.yaml``
    capability_id is a ghost entry — agents may recommend it for entitlement gating
    but no generated app will ever declare or produce that capability.

    Allowed fix: add the capability to a module template, or remove the stale
    entry from ``capability_directory.yaml``.
    """
    pack_dir = _pack_dir(pack_id)
    assert pack_dir.is_dir(), (
        f"capability_directory.yaml lists pack {pack_id!r} with capabilities_provided "
        f"but no build_context directory found at {pack_dir}. "
        f"Either create the pack directory or remove capabilities_provided from the entry."
    )

    capability_source = _pack_capability_source(pack_dir)
    if capability_source == "config_file":
        contract_path = pack_dir / "contract.yaml"
        assert contract_path.is_file(), (
            f"{pack_id}: config_file capability packs must ship contract.yaml so "
            f"capabilities_provided can be validated against the generator contract."
        )
        contract_text = contract_path.read_text(encoding="utf-8")
        assert capability_id in contract_text, (
            f"{pack_id}: capability_directory.yaml lists {capability_id!r} in "
            f"capabilities_provided but contract.yaml does not mention that capability_id. "
            f"config_file packs are validated through their build contract, not templates."
        )
        return

    declared = _declared_module_capability_ids(pack_dir)
    assert capability_id in declared, (
        f"{pack_id}: capability_directory.yaml lists {capability_id!r} in "
        f"capabilities_provided but no module.yaml in {pack_dir}/templates/modules/ "
        f"declares that capability_id. Adapter stubs are not module capabilities. "
        f"Either add the capability to a module template or remove the stale entry."
    )
