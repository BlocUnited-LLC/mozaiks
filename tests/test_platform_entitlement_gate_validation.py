"""Tests for startup entitlement gate validation in mozaiksai/hosts/platform.py.

_warn_undeclared_entitlement_gates logs a WARNING for any module action that
declares an entitlement_gate capability_id not present in any plan's
capabilities in subscriptions.yaml. This catches misconfiguration early
instead of silently always-denying the action at runtime.
"""
from __future__ import annotations

import logging
from types import SimpleNamespace

from mozaiksai.hosts.platform import _warn_undeclared_entitlement_gates


def _module(name: str, gates: dict[str, str | None]):
    """Minimal LoadedModule stub with an action_entitlement_map."""
    return SimpleNamespace(name=name, action_entitlement_map=gates)


def _subscriptions(plan_capabilities: list[list[str]]):
    """Minimal SubscriptionsConfig stub with plans."""
    plans = [
        SimpleNamespace(capabilities=caps)
        for caps in plan_capabilities
    ]
    return SimpleNamespace(plans=plans)


class TestWarnUndeclaredEntitlementGates:
    def test_no_warning_when_gate_declared_in_plan(self, caplog):
        modules = [_module("reports", {"export": "reports.export"})]
        config = _subscriptions([[], ["reports.export"]])
        with caplog.at_level(logging.WARNING, logger="mozaiksai.hosts.platform"):
            _warn_undeclared_entitlement_gates(modules, config)
        assert not any("ENTITLEMENT_GATE_UNDECLARED" in r.message for r in caplog.records)

    def test_warning_when_gate_absent_from_all_plans(self, caplog):
        modules = [_module("reports", {"export": "reports.export"})]
        config = _subscriptions([[], ["reports.view"]])  # export not in any plan
        with caplog.at_level(logging.WARNING, logger="mozaiksai.hosts.platform"):
            _warn_undeclared_entitlement_gates(modules, config)
        assert any(
            "ENTITLEMENT_GATE_UNDECLARED" in r.message and "reports.export" in r.message
            for r in caplog.records
        )

    def test_no_warning_for_ungated_actions(self, caplog):
        modules = [_module("reports", {"list": None, "export": None})]
        config = _subscriptions([[]])
        with caplog.at_level(logging.WARNING, logger="mozaiksai.hosts.platform"):
            _warn_undeclared_entitlement_gates(modules, config)
        assert not any("ENTITLEMENT_GATE_UNDECLARED" in r.message for r in caplog.records)

    def test_no_warning_when_no_modules(self, caplog):
        config = _subscriptions([["some.capability"]])
        with caplog.at_level(logging.WARNING, logger="mozaiksai.hosts.platform"):
            _warn_undeclared_entitlement_gates([], config)
        assert not any("ENTITLEMENT_GATE_UNDECLARED" in r.message for r in caplog.records)

    def test_warning_mentions_module_and_action(self, caplog):
        modules = [_module("inventory", {"reserve": "inventory.reserve"})]
        config = _subscriptions([[]])
        with caplog.at_level(logging.WARNING, logger="mozaiksai.hosts.platform"):
            _warn_undeclared_entitlement_gates(modules, config)
        assert any(
            "inventory" in r.message and "reserve" in r.message and "inventory.reserve" in r.message
            for r in caplog.records
        )

    def test_gate_in_any_plan_is_sufficient(self, caplog):
        modules = [_module("reports", {"export": "reports.export"})]
        # Only the second plan grants the capability
        config = _subscriptions([[], [], ["reports.export"]])
        with caplog.at_level(logging.WARNING, logger="mozaiksai.hosts.platform"):
            _warn_undeclared_entitlement_gates(modules, config)
        assert not any("ENTITLEMENT_GATE_UNDECLARED" in r.message for r in caplog.records)

    def test_multiple_undeclared_gates_each_warned(self, caplog):
        modules = [
            _module("reports", {"export": "reports.export", "archive": "reports.archive"}),
        ]
        config = _subscriptions([[]])  # neither capability declared
        with caplog.at_level(logging.WARNING, logger="mozaiksai.hosts.platform"):
            _warn_undeclared_entitlement_gates(modules, config)
        warned = [r.message for r in caplog.records if "ENTITLEMENT_GATE_UNDECLARED" in r.message]
        assert len(warned) == 2
        assert any("reports.export" in w for w in warned)
        assert any("reports.archive" in w for w in warned)

    def test_mixed_declared_and_undeclared(self, caplog):
        modules = [
            _module("reports", {"view": "reports.view", "export": "reports.export"}),
        ]
        config = _subscriptions([["reports.view"]])  # only view declared, not export
        with caplog.at_level(logging.WARNING, logger="mozaiksai.hosts.platform"):
            _warn_undeclared_entitlement_gates(modules, config)
        records = [r for r in caplog.records if "ENTITLEMENT_GATE_UNDECLARED" in r.message]
        assert len(records) == 1
        assert "reports.export" in records[0].message
        assert "reports.view" not in records[0].message
