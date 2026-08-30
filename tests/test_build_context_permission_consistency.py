"""Cross-pack drift guard: permission IDs in actions and capabilities must be declared.

Each generated module's module.yaml has a top-level ``permissions:`` block that
declares the full set of permission IDs the module owns. Actions and capabilities
then reference subsets of those IDs in their own ``permissions:`` lists.

If an action or capability references a permission ID that is NOT in the
top-level block, the module_executor will check a permission at dispatch time
that is never granted by any role mapping — silently denying every call to
that action for authenticated users.

This test file walks every pack template module.yaml and asserts that every
permission reference in actions and capabilities is declared in the top-level
permissions block. Tests are parametrized so failures identify the exact
pack/module/action/permission — not just a bulk assertion.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

WORKSPACE = Path(__file__).resolve().parents[1]
BUILD_CONTEXT = WORKSPACE / "factory_app" / "build_context"


def _read_yaml(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _collect_action_cases() -> list[tuple[str, str, str, str]]:
    """Return (pack_id, module_id, action_id, perm_id) for every undeclared action permission."""
    cases: list[tuple[str, str, str, str]] = []
    for module_yaml in sorted(BUILD_CONTEXT.rglob("module.yaml")):
        if "__pycache__" in module_yaml.parts:
            continue
        try:
            pack_id = module_yaml.relative_to(BUILD_CONTEXT).parts[0]
        except ValueError:
            continue
        data = _read_yaml(module_yaml)
        module_id = data.get("module", {}).get("id", module_yaml.parent.name)
        declared = {p["id"] for p in (data.get("permissions") or [])}
        for action in data.get("actions") or []:
            for perm in action.get("permissions") or []:
                cases.append((pack_id, module_id, action["id"], perm, frozenset(declared)))
    # Return (pack, module, action, perm, declared) — include declared for the test body
    return [(p, m, a, perm, d) for p, m, a, perm, d in cases]


def _collect_capability_cases() -> list[tuple[str, str, str, str, frozenset]]:
    cases: list[tuple[str, str, str, str, frozenset]] = []
    for module_yaml in sorted(BUILD_CONTEXT.rglob("module.yaml")):
        if "__pycache__" in module_yaml.parts:
            continue
        try:
            pack_id = module_yaml.relative_to(BUILD_CONTEXT).parts[0]
        except ValueError:
            continue
        data = _read_yaml(module_yaml)
        module_id = data.get("module", {}).get("id", module_yaml.parent.name)
        declared = frozenset(p["id"] for p in (data.get("permissions") or []))
        for cap in data.get("capabilities") or []:
            for perm in cap.get("permissions") or []:
                cases.append((pack_id, module_id, cap.get("capability_id", "?"), perm, declared))
    return cases


_ACTION_CASES = _collect_action_cases()
_CAP_CASES = _collect_capability_cases()


@pytest.mark.parametrize(
    "pack_id,module_id,action_id,perm_id,declared",
    _ACTION_CASES,
    ids=[f"{p}/{m}/action={a}/perm={perm}" for p, m, a, perm, _ in _ACTION_CASES],
)
def test_action_permission_is_declared_in_module(
    pack_id: str, module_id: str, action_id: str, perm_id: str, declared: frozenset
) -> None:
    """Every permission ID in an action's permissions list must be in the module's permissions block.

    An undeclared permission ID will never appear in any granted_permissions set,
    silently denying every authenticated call to that action.
    """
    assert perm_id in declared, (
        f"{pack_id}/{module_id}: action {action_id!r} references permission {perm_id!r} "
        f"which is not declared in the module's top-level permissions block. "
        f"Declared permissions: {sorted(declared)}. "
        f"Add an entry for {perm_id!r} to the permissions block, or use a declared permission ID."
    )


@pytest.mark.parametrize(
    "pack_id,module_id,cap_id,perm_id,declared",
    _CAP_CASES,
    ids=[f"{p}/{m}/cap={c}/perm={perm}" for p, m, c, perm, _ in _CAP_CASES],
)
def test_capability_permission_is_declared_in_module(
    pack_id: str, module_id: str, cap_id: str, perm_id: str, declared: frozenset
) -> None:
    """Every permission ID in a capability's permissions list must be in the module's permissions block.

    Capability permissions are used by the AppGenerator to wire entitlement gates.
    An undeclared permission ID here produces a capability that cannot be granted
    or checked correctly.
    """
    assert perm_id in declared, (
        f"{pack_id}/{module_id}: capability {cap_id!r} references permission {perm_id!r} "
        f"which is not declared in the module's top-level permissions block. "
        f"Declared permissions: {sorted(declared)}. "
        f"Add an entry for {perm_id!r} to the permissions block, or use a declared permission ID."
    )
