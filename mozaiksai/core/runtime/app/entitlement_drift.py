"""entitlement_drift — entitlement gate ↔ subscriptions.yaml alignment checker.

This module is a development and test-time utility.  It is NOT loaded at app
startup and has no effect on the runtime hot path.

Purpose
-------
The platform warms up ``_warn_undeclared_entitlement_gates`` at startup which
logs a WARNING when a module action's ``entitlement_gate`` capability_id is not
granted by any plan in ``config/subscriptions.yaml``.  That warning is soft —
no exception is raised — because an app may be in development with plans not
yet finalised.

This module provides the same check as a hard assertion so CI drift-guard tests
can fail immediately when a gate references a capability that no plan grants.

An undeclared gate causes **silent always-deny** for callers who are not
trusted/admin: every action check returns ``denied`` even for fully subscribed
users.  This is almost always a misconfiguration rather than intentional
behaviour and should be caught before deploy.

Public API
----------
``scan_module_entitlement_gates(modules_dir)``
    Return a mapping ``{(module_id, action_id): capability_id}`` for every
    module action that declares an ``entitlement_gate``.

``load_plan_capabilities(subscriptions_yaml)``
    Return the set of ``capability_id`` strings granted by any plan in a
    ``config/subscriptions.yaml`` file.  Supports both v1 (flat
    ``plans[]``) and v2 (``products[].plans[]``) formats.

``check_entitlement_gate_coverage(app_dir)``
    Return ``[(module_id, action_id, capability_id)]`` triples for gated
    actions whose capability_id is not granted by any plan.  Returns an
    empty list when all gates are covered or no modules gate on entitlements.

Typical usage in a generated app's drift-guard tests
-----------------------------------------------------
::

    from mozaiksai.core.runtime.app.entitlement_drift import (
        check_entitlement_gate_coverage,
    )
    from pathlib import Path

    APP_ROOT = Path(__file__).resolve().parents[1] / "app"

    def test_entitlement_gates_covered_by_plan():
        uncovered = check_entitlement_gate_coverage(APP_ROOT)
        assert uncovered == [], (
            "Actions gate on capabilities not granted by any plan:\\n"
            + "\\n".join(
                f"  {mod}.{action}: '{cap}' not in subscriptions.yaml"
                for mod, action, cap in uncovered
            )
        )
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def scan_module_entitlement_gates(
    modules_dir: Path,
) -> dict[tuple[str, str], str]:
    """Return ``{(module_id, action_id): capability_id}`` for all gated actions.

    Walks every ``<modules_dir>/<module_id>/module.yaml`` and collects actions
    whose ``entitlement_gate`` field is a non-empty string.

    Returns an empty dict when ``modules_dir`` does not exist or no modules
    declare entitlement gates.
    """
    if not modules_dir.exists():
        return {}

    result: dict[tuple[str, str], str] = {}
    for module_yaml in sorted(modules_dir.glob("*/module.yaml")):
        module_id = module_yaml.parent.name
        raw: Any = yaml.safe_load(module_yaml.read_text(encoding="utf-8")) or {}
        for action in raw.get("actions", []):
            gate = action.get("entitlement_gate")
            if gate and isinstance(gate, str):
                result[(module_id, action["id"])] = gate
    return result


def load_plan_capabilities(subscriptions_yaml: Path) -> set[str]:
    """Return all capability_ids granted by any plan in subscriptions.yaml.

    Supports two formats:

    v1 — flat plans list::

        plans:
          - plan_id: free
            capabilities: [cap.a, cap.b]

    v2 — plans nested under products::

        products:
          - product_id: platform
            plans:
              - plan_id: builder
                capabilities: [cap.a, cap.b]

    Returns an empty set when ``subscriptions_yaml`` does not exist or no
    plans declare capabilities.
    """
    if not subscriptions_yaml.exists():
        return set()

    raw: Any = yaml.safe_load(subscriptions_yaml.read_text(encoding="utf-8")) or {}
    capabilities: set[str] = set()

    # v1: flat top-level plans
    for plan in raw.get("plans", []):
        for cap in plan.get("capabilities", []):
            if isinstance(cap, str):
                capabilities.add(cap)

    # v2: plans nested under products
    for product in raw.get("products", []):
        for plan in product.get("plans", []):
            for cap in plan.get("capabilities", []):
                if isinstance(cap, str):
                    capabilities.add(cap)

    return capabilities


def check_entitlement_gate_coverage(
    app_dir: Path,
) -> list[tuple[str, str, str]]:
    """Return ``[(module_id, action_id, capability_id)]`` for uncovered gates.

    An entitlement gate is *uncovered* when the capability_id it references is
    not granted by any plan in ``app/config/subscriptions.yaml``.  Such a gate
    causes silent always-deny for all non-trusted callers regardless of their
    subscription, which is almost always a misconfiguration.

    ``app_dir`` is the app workspace root (the directory containing
    ``modules/`` and ``config/``).

    Returns an empty list when:
    - No module actions declare ``entitlement_gate``
    - All declared gates reference a capability granted by at least one plan
    - ``config/subscriptions.yaml`` does not exist (no subscription config
      means entitlements are not enforced; gates are effectively no-ops)

    Result is sorted for deterministic output: ``(module_id, action_id, cap)``.
    """
    modules_dir = app_dir / "modules"
    subscriptions_yaml = app_dir / "config" / "subscriptions.yaml"

    gates = scan_module_entitlement_gates(modules_dir)
    if not gates:
        return []

    if not subscriptions_yaml.exists():
        return []

    plan_caps = load_plan_capabilities(subscriptions_yaml)

    return sorted(
        (module_id, action_id, cap)
        for (module_id, action_id), cap in gates.items()
        if cap not in plan_caps
    )
