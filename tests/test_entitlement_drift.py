"""Tests for entitlement_drift.py — entitlement gate ↔ subscriptions.yaml alignment.

The platform startup function _warn_undeclared_entitlement_gates logs a soft
warning when an action's entitlement_gate does not appear in any plan.
This module provides the same check as a hard assertion for CI drift-guard
tests that want to fail immediately on misconfiguration.

An undeclared gate causes silent always-deny for non-trusted callers.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mozaiksai.core.runtime.app.entitlement_drift import (
    check_entitlement_gate_coverage,
    load_plan_capabilities,
    scan_module_entitlement_gates,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_module(
    modules_dir: Path,
    module_id: str,
    *,
    actions: list[dict] | None = None,
) -> Path:
    module_dir = modules_dir / module_id
    (module_dir / "backend").mkdir(parents=True, exist_ok=True)

    actions_yaml = ""
    for action in actions or []:
        gate_line = ""
        if action.get("entitlement_gate"):
            gate_line = f"\n    entitlement_gate: {action['entitlement_gate']}"
        actions_yaml += f"""
  - id: {action['id']}
    description: {action.get('description', 'test action')}
    handler_method: {action['id']}{gate_line}
    permissions: []"""

    module_dir.joinpath("module.yaml").write_text(
        f"""schema_version: mozaiks.module.v1
module:
  id: {module_id}
  display_name: {module_id.title()}
  version: 1.0.0
  description: Test module
  handler: backend.handler:Handler
permissions: []
actions:{actions_yaml or " []"}
""",
        encoding="utf-8",
    )
    return module_dir


def _write_subscriptions(
    config_dir: Path,
    *,
    v2: bool = True,
    capabilities: list[str] | None = None,
) -> Path:
    config_dir.mkdir(parents=True, exist_ok=True)
    caps = capabilities or []

    if v2:
        if caps:
            caps_lines = "\n".join(f"          - {c}" for c in caps)
            caps_block = f"        capabilities:\n{caps_lines}"
        else:
            caps_block = "        capabilities: []"
        content = (
            "schema_version: mozaiks.subscriptions.v2\n"
            "label: Test App\n"
            "default_product_id: platform\n"
            "products:\n"
            "  - product_id: platform\n"
            "    label: Platform\n"
            "    plans:\n"
            "      - plan_id: free\n"
            "        label: Free\n"
            "        description: Free plan\n"
            f"{caps_block}\n"
            "      - plan_id: pro\n"
            "        label: Pro\n"
            "        description: Pro plan\n"
            "        capabilities:\n"
            "          - extra.cap\n"
        )
    else:
        if caps:
            caps_lines = "\n".join(f"    - {c}" for c in caps)
            caps_block = f"    capabilities:\n{caps_lines}"
        else:
            caps_block = "    capabilities: []"
        content = (
            "schema_version: mozaiks.subscriptions.v1\n"
            "plans:\n"
            "  - plan_id: free\n"
            "    label: Free\n"
            f"{caps_block}\n"
        )

    config_dir.joinpath("subscriptions.yaml").write_text(content, encoding="utf-8")
    return config_dir.joinpath("subscriptions.yaml")


# ---------------------------------------------------------------------------
# scan_module_entitlement_gates
# ---------------------------------------------------------------------------


def test_scan_finds_gated_actions(tmp_path: Path) -> None:
    """scan_module_entitlement_gates must return all gated (module, action) pairs."""
    modules_dir = tmp_path / "modules"
    _write_module(
        modules_dir,
        "tasks",
        actions=[
            {"id": "create", "entitlement_gate": "tasks.create"},
            {"id": "list"},  # no gate
        ],
    )

    result = scan_module_entitlement_gates(modules_dir)
    assert result == {("tasks", "create"): "tasks.create"}


def test_scan_returns_empty_for_no_gates(tmp_path: Path) -> None:
    modules_dir = tmp_path / "modules"
    _write_module(modules_dir, "tasks", actions=[{"id": "list"}, {"id": "get"}])

    assert scan_module_entitlement_gates(modules_dir) == {}


def test_scan_returns_empty_when_modules_dir_absent(tmp_path: Path) -> None:
    assert scan_module_entitlement_gates(tmp_path / "nonexistent") == {}


def test_scan_collects_across_multiple_modules(tmp_path: Path) -> None:
    modules_dir = tmp_path / "modules"
    _write_module(
        modules_dir, "marketplace", actions=[{"id": "browse", "entitlement_gate": "marketplace.browse"}]
    )
    _write_module(
        modules_dir, "wallet", actions=[{"id": "payout", "entitlement_gate": "wallet.payout"}]
    )

    result = scan_module_entitlement_gates(modules_dir)
    assert result == {
        ("marketplace", "browse"): "marketplace.browse",
        ("wallet", "payout"): "wallet.payout",
    }


# ---------------------------------------------------------------------------
# load_plan_capabilities — v2 format
# ---------------------------------------------------------------------------


def test_load_capabilities_v2(tmp_path: Path) -> None:
    """load_plan_capabilities reads capabilities from v2 products[].plans[]."""
    subs = _write_subscriptions(
        tmp_path / "config",
        v2=True,
        capabilities=["marketplace.browse", "wallet.payout"],
    )
    result = load_plan_capabilities(subs)
    assert "marketplace.browse" in result
    assert "wallet.payout" in result
    assert "extra.cap" in result  # from the Pro plan in _write_subscriptions


def test_load_capabilities_v1(tmp_path: Path) -> None:
    """load_plan_capabilities reads capabilities from v1 flat plans[]."""
    subs = _write_subscriptions(
        tmp_path / "config",
        v2=False,
        capabilities=["marketplace.browse"],
    )
    result = load_plan_capabilities(subs)
    assert "marketplace.browse" in result


def test_load_capabilities_returns_empty_when_absent(tmp_path: Path) -> None:
    result = load_plan_capabilities(tmp_path / "nonexistent.yaml")
    assert result == set()


def test_load_capabilities_returns_empty_for_no_caps(tmp_path: Path) -> None:
    subs = _write_subscriptions(tmp_path / "config", v2=True, capabilities=[])
    result = load_plan_capabilities(subs)
    assert "extra.cap" in result  # Pro plan still has extra.cap
    assert "marketplace.browse" not in result


# ---------------------------------------------------------------------------
# check_entitlement_gate_coverage — end-to-end
# ---------------------------------------------------------------------------


def test_check_coverage_returns_empty_when_all_covered(tmp_path: Path) -> None:
    """All gates covered by at least one plan → empty result."""
    modules_dir = tmp_path / "modules"
    _write_module(
        modules_dir, "tasks", actions=[{"id": "create", "entitlement_gate": "tasks.create"}]
    )
    _write_subscriptions(tmp_path / "config", v2=True, capabilities=["tasks.create"])

    assert check_entitlement_gate_coverage(tmp_path) == []


def test_check_coverage_reports_uncovered_gate(tmp_path: Path) -> None:
    """Gate referencing an undeclared capability must be reported."""
    modules_dir = tmp_path / "modules"
    _write_module(
        modules_dir,
        "marketplace",
        actions=[{"id": "order_ad", "entitlement_gate": "marketplace.ad.order"}],
    )
    _write_subscriptions(tmp_path / "config", v2=True, capabilities=["some.other.cap"])

    result = check_entitlement_gate_coverage(tmp_path)
    assert result == [("marketplace", "order_ad", "marketplace.ad.order")]


def test_check_coverage_reports_all_uncovered(tmp_path: Path) -> None:
    """Every uncovered gate must be reported, not just the first."""
    modules_dir = tmp_path / "modules"
    _write_module(
        modules_dir,
        "marketplace",
        actions=[
            {"id": "browse", "entitlement_gate": "marketplace.browse"},
            {"id": "order", "entitlement_gate": "marketplace.order"},
        ],
    )
    _write_subscriptions(tmp_path / "config", v2=True, capabilities=[])

    result = check_entitlement_gate_coverage(tmp_path)
    # both uncovered; sorted
    assert set(r[2] for r in result) == {"marketplace.browse", "marketplace.order"}
    assert result == sorted(result)


def test_check_coverage_returns_empty_when_no_gates(tmp_path: Path) -> None:
    """No entitlement gates → nothing to check, result is empty."""
    modules_dir = tmp_path / "modules"
    _write_module(modules_dir, "tasks", actions=[{"id": "list"}, {"id": "create"}])
    _write_subscriptions(tmp_path / "config", v2=True, capabilities=[])

    assert check_entitlement_gate_coverage(tmp_path) == []


def test_check_coverage_returns_empty_when_no_subscriptions_yaml(
    tmp_path: Path,
) -> None:
    """No subscriptions.yaml means no subscription enforcement — gates are no-ops."""
    modules_dir = tmp_path / "modules"
    _write_module(
        modules_dir,
        "tasks",
        actions=[{"id": "create", "entitlement_gate": "tasks.create"}],
    )
    # config/ directory exists but has no subscriptions.yaml
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)

    assert check_entitlement_gate_coverage(tmp_path) == []


def test_check_coverage_result_is_sorted(tmp_path: Path) -> None:
    """Result must be sorted for deterministic output in CI failure messages."""
    modules_dir = tmp_path / "modules"
    _write_module(
        modules_dir, "z_module", actions=[{"id": "act", "entitlement_gate": "z.cap"}]
    )
    _write_module(
        modules_dir, "a_module", actions=[{"id": "act", "entitlement_gate": "a.cap"}]
    )
    _write_subscriptions(tmp_path / "config", v2=True, capabilities=[])

    result = check_entitlement_gate_coverage(tmp_path)
    assert result == sorted(result)


def test_check_coverage_gate_in_any_plan_is_sufficient(tmp_path: Path) -> None:
    """A gate covered by any plan (even non-default) must not be reported."""
    modules_dir = tmp_path / "modules"
    _write_module(
        modules_dir,
        "marketplace",
        actions=[{"id": "order", "entitlement_gate": "extra.cap"}],
    )
    # extra.cap is only in the Pro plan injected by _write_subscriptions
    _write_subscriptions(tmp_path / "config", v2=True, capabilities=[])

    assert check_entitlement_gate_coverage(tmp_path) == []


# ---------------------------------------------------------------------------
# Integration: drift-guard pattern for a generated app
# ---------------------------------------------------------------------------


def test_drift_guard_pattern_used_by_generated_app_tests(tmp_path: Path) -> None:
    """The drift-guard pattern from module_archetypes.yaml must correctly catch
    an entitlement gate that references a capability not in any plan.

    This test mimics exactly what AppGenerator scaffolds into a generated app's
    drift-guard test suite.
    """
    modules_dir = tmp_path / "modules"
    # Simulate a module where the developer added entitlement_gate but forgot
    # to add the capability to subscriptions.yaml
    _write_module(
        modules_dir,
        "billing",
        actions=[
            {"id": "upgrade_plan", "entitlement_gate": "billing.upgrade"},
        ],
    )
    _write_subscriptions(
        tmp_path / "config",
        v2=True,
        capabilities=["billing.view"],  # billing.upgrade was forgotten
    )

    # This is the exact assertion a generated drift-guard test would make:
    uncovered = check_entitlement_gate_coverage(tmp_path)
    assert uncovered != [], (
        "Expected the drift-guard to catch the undeclared entitlement gate; "
        "the entitlement_gate_coverage pattern is broken"
    )
    assert any(cap == "billing.upgrade" for _, _, cap in uncovered)
