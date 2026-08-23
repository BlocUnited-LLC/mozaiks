from __future__ import annotations

from pydantic import ValidationError

from mozaiksai.core.validation import (
    AcceptanceController,
    ValidationGate,
    ValidationGateResult,
    ValidationIssue,
    ValidationRegistry,
)


def _issue(*, code: str = "broken_route") -> ValidationIssue:
    return ValidationIssue(
        gate_id="routes",
        code=code,
        message="Route points to a missing page.",
        path="app/ui/route_manifest.json",
        suggested_fix="Generate the referenced page.",
        repair_owner="AppSchemaAgent",
    )


def test_controller_accepts_when_every_registered_gate_passes() -> None:
    registry = ValidationRegistry()
    registry.register(ValidationGate(gate_id="routes", handler=lambda _context: []))

    result = AcceptanceController(registry).run(context={"files": {}})

    assert result.accepted is True
    assert result.validation_run.status == "passed"
    assert result.repair_decision.disposition == "accept"
    assert result.validation_run.gate_results[0].gate_id == "routes"


def test_controller_returns_one_canonical_repair_decision() -> None:
    registry = ValidationRegistry()
    registry.register(ValidationGate(gate_id="routes", handler=lambda _context: [_issue()]))

    result = AcceptanceController(registry).run(context={}, attempt=0, max_attempts=1)

    assert result.validation_run.status == "failed"
    assert result.repair_decision.disposition == "repair"
    assert result.repair_decision.repair_owners == ("AppSchemaAgent",)
    assert result.repair_decision.issue_codes == ("broken_route",)
    assert result.validation_run.failure_fingerprint


def test_controller_blocks_when_failure_evidence_is_unchanged() -> None:
    registry = ValidationRegistry()
    registry.register(ValidationGate(gate_id="routes", handler=lambda _context: [_issue()]))
    controller = AcceptanceController(registry)
    first = controller.run(context={}, attempt=0, max_attempts=2)

    second = controller.run(
        context={},
        attempt=1,
        max_attempts=2,
        prior_failure_fingerprint=first.validation_run.failure_fingerprint,
    )

    assert second.repair_decision.disposition == "block"
    assert "unchanged" in second.repair_decision.reason


def test_controller_allows_changed_evidence_within_budget() -> None:
    registry = ValidationRegistry()
    registry.register(
        ValidationGate(gate_id="routes", handler=lambda context: [_issue(code=str(context["code"]))])
    )
    controller = AcceptanceController(registry)
    first = controller.run(context={"code": "broken_route"}, attempt=0, max_attempts=2)

    second = controller.run(
        context={"code": "missing_component"},
        attempt=1,
        max_attempts=2,
        prior_failure_fingerprint=first.validation_run.failure_fingerprint,
    )

    assert second.repair_decision.disposition == "repair"
    assert second.validation_run.failure_fingerprint != first.validation_run.failure_fingerprint


def test_registry_rejects_duplicate_and_unknown_gates() -> None:
    registry = ValidationRegistry()
    registry.register(ValidationGate(gate_id="routes", handler=lambda _context: []))

    try:
        registry.register(ValidationGate(gate_id="routes", handler=lambda _context: []))
    except ValueError as exc:
        assert "already registered" in str(exc)
    else:
        raise AssertionError("duplicate validation gate was accepted")

    try:
        registry.resolve(["missing"])
    except KeyError as exc:
        assert "unknown validation gates" in str(exc)
    else:
        raise AssertionError("unknown validation gate was accepted")


def test_validator_exception_fails_closed() -> None:
    def broken(_context):
        raise RuntimeError("boom")

    registry = ValidationRegistry()
    registry.register(ValidationGate(gate_id="routes", handler=broken))

    result = AcceptanceController(registry).run(context={})

    assert result.validation_run.status == "failed"
    assert result.validation_run.issues[0].code == "validator_exception"
    assert result.repair_decision.disposition == "repair"


def test_gate_result_status_cannot_disagree_with_issues() -> None:
    try:
        ValidationGateResult(gate_id="routes", status="failed", issues=())
    except ValidationError as exc:
        assert "must contain an error issue" in str(exc)
    else:
        raise AssertionError("inconsistent validation result was accepted")
