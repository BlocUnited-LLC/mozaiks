from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

ValidationSeverity = Literal["error", "warning", "info"]
ValidationStatus = Literal["passed", "failed"]
RepairDisposition = Literal["accept", "repair", "block"]


class ValidationIssue(BaseModel):
    """One actionable finding emitted by a registered validation gate."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    gate_id: str
    code: str
    message: str
    severity: ValidationSeverity = "error"
    path: str | None = None
    route: str | None = None
    source: str | None = None
    suggested_fix: str | None = None
    repair_owner: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    def fingerprint_payload(self) -> dict[str, str]:
        return {
            "gate_id": self.gate_id.strip().lower(),
            "code": self.code.strip().lower(),
            "message": " ".join(self.message.split()).lower(),
            "path": (self.path or "").replace("\\", "/").strip().lower(),
            "route": (self.route or "").strip().lower(),
            "repair_owner": (self.repair_owner or "").strip().lower(),
        }


class ValidationGateResult(BaseModel):
    """Result of exactly one registered gate execution."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    gate_id: str
    status: ValidationStatus
    issues: tuple[ValidationIssue, ...] = Field(default_factory=tuple)
    artifacts: tuple[str, ...] = Field(default_factory=tuple)
    duration_ms: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_status_matches_issues(self) -> ValidationGateResult:
        has_errors = any(issue.severity == "error" for issue in self.issues)
        if self.status == "passed" and has_errors:
            raise ValueError("passed validation gate result cannot contain error issues")
        if self.status == "failed" and not has_errors:
            raise ValueError("failed validation gate result must contain an error issue")
        return self


class ValidationRun(BaseModel):
    """Canonical evidence for one ordered acceptance pass."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str = Field(default_factory=lambda: str(uuid4()))
    status: ValidationStatus
    gate_results: tuple[ValidationGateResult, ...]
    started_at: datetime
    completed_at: datetime
    attempt: int = Field(default=0, ge=0)
    failure_fingerprint: str | None = None

    @property
    def issues(self) -> tuple[ValidationIssue, ...]:
        return tuple(issue for result in self.gate_results for issue in result.issues)


class RepairDecision(BaseModel):
    """Single routing decision derived from a canonical validation run."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    disposition: RepairDisposition
    reason: str
    attempt: int = Field(ge=0)
    max_attempts: int = Field(ge=0)
    failure_fingerprint: str | None = None
    repair_owners: tuple[str, ...] = Field(default_factory=tuple)
    issue_codes: tuple[str, ...] = Field(default_factory=tuple)


class AcceptanceResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    validation_run: ValidationRun
    repair_decision: RepairDecision

    @property
    def accepted(self) -> bool:
        return self.repair_decision.disposition == "accept"


GateHandler = Callable[[Mapping[str, Any]], ValidationGateResult | Iterable[ValidationIssue]]


@dataclass(frozen=True)
class ValidationGate:
    gate_id: str
    handler: GateHandler
    description: str = ""


class ValidationRegistry:
    """Ordered, duplicate-safe registry for acceptance gates."""

    def __init__(self) -> None:
        self._gates: dict[str, ValidationGate] = {}

    def register(self, gate: ValidationGate) -> None:
        gate_id = gate.gate_id.strip()
        if not gate_id:
            raise ValueError("validation gate_id must not be empty")
        if gate_id in self._gates:
            raise ValueError(f"validation gate already registered: {gate_id}")
        self._gates[gate_id] = gate

    def resolve(self, gate_ids: Sequence[str] | None = None) -> tuple[ValidationGate, ...]:
        if gate_ids is None:
            return tuple(self._gates.values())
        missing = [gate_id for gate_id in gate_ids if gate_id not in self._gates]
        if missing:
            raise KeyError(f"unknown validation gates: {', '.join(missing)}")
        return tuple(self._gates[gate_id] for gate_id in gate_ids)

    @property
    def gate_ids(self) -> tuple[str, ...]:
        return tuple(self._gates)


class AcceptanceController:
    """Run registered gates and make the only repair/promotion decision."""

    def __init__(self, registry: ValidationRegistry) -> None:
        self._registry = registry

    def run(
        self,
        *,
        context: Mapping[str, Any],
        gate_ids: Sequence[str] | None = None,
        attempt: int = 0,
        max_attempts: int = 1,
        prior_failure_fingerprint: str | None = None,
    ) -> AcceptanceResult:
        if attempt < 0:
            raise ValueError("attempt must be non-negative")
        if max_attempts < 0:
            raise ValueError("max_attempts must be non-negative")

        started_at = datetime.now(UTC)
        gate_results = tuple(self._run_gate(gate, context) for gate in self._registry.resolve(gate_ids))
        blocking_issues = tuple(
            issue
            for result in gate_results
            for issue in result.issues
            if issue.severity == "error"
        )
        fingerprint = _failure_fingerprint(blocking_issues) if blocking_issues else None
        run = ValidationRun(
            status="failed" if blocking_issues else "passed",
            gate_results=gate_results,
            started_at=started_at,
            completed_at=datetime.now(UTC),
            attempt=attempt,
            failure_fingerprint=fingerprint,
        )
        return AcceptanceResult(
            validation_run=run,
            repair_decision=_decide_repair(
                blocking_issues,
                attempt=attempt,
                max_attempts=max_attempts,
                failure_fingerprint=fingerprint,
                prior_failure_fingerprint=prior_failure_fingerprint,
            ),
        )

    @staticmethod
    def _run_gate(gate: ValidationGate, context: Mapping[str, Any]) -> ValidationGateResult:
        started_at = datetime.now(UTC)
        try:
            raw_result = gate.handler(context)
            if isinstance(raw_result, ValidationGateResult):
                if raw_result.gate_id != gate.gate_id:
                    raise ValueError(
                        f"validation gate {gate.gate_id} returned result for {raw_result.gate_id}"
                    )
                return raw_result.model_copy(
                    update={"duration_ms": _duration_ms(started_at)},
                )
            issues = tuple(raw_result)
            for issue in issues:
                if not isinstance(issue, ValidationIssue):
                    raise TypeError(f"validation gate {gate.gate_id} returned a non-ValidationIssue")
                if issue.gate_id != gate.gate_id:
                    raise ValueError(
                        f"validation gate {gate.gate_id} emitted issue for {issue.gate_id}"
                    )
            return ValidationGateResult(
                gate_id=gate.gate_id,
                status="failed" if any(issue.severity == "error" for issue in issues) else "passed",
                issues=issues,
                duration_ms=_duration_ms(started_at),
            )
        except Exception as exc:
            issue = ValidationIssue(
                gate_id=gate.gate_id,
                code="validator_exception",
                message=f"Validation gate {gate.gate_id} failed to execute: {exc}",
                source=gate.gate_id,
                suggested_fix="Fix the validator or its execution environment before promotion.",
            )
            return ValidationGateResult(
                gate_id=gate.gate_id,
                status="failed",
                issues=(issue,),
                duration_ms=_duration_ms(started_at),
            )


def _decide_repair(
    issues: tuple[ValidationIssue, ...],
    *,
    attempt: int,
    max_attempts: int,
    failure_fingerprint: str | None,
    prior_failure_fingerprint: str | None,
) -> RepairDecision:
    owners = tuple(sorted({issue.repair_owner for issue in issues if issue.repair_owner}))
    codes = tuple(sorted({issue.code for issue in issues}))
    if not issues:
        return RepairDecision(
            disposition="accept",
            reason="All registered validation gates passed.",
            attempt=attempt,
            max_attempts=max_attempts,
        )
    if prior_failure_fingerprint and failure_fingerprint == prior_failure_fingerprint:
        return RepairDecision(
            disposition="block",
            reason="Validation evidence is unchanged after repair; operator review is required.",
            attempt=attempt,
            max_attempts=max_attempts,
            failure_fingerprint=failure_fingerprint,
            repair_owners=owners,
            issue_codes=codes,
        )
    if attempt < max_attempts:
        return RepairDecision(
            disposition="repair",
            reason="Blocking validation issues remain within the repair budget.",
            attempt=attempt,
            max_attempts=max_attempts,
            failure_fingerprint=failure_fingerprint,
            repair_owners=owners,
            issue_codes=codes,
        )
    return RepairDecision(
        disposition="block",
        reason="Blocking validation issues remain after the repair budget; operator review is required.",
        attempt=attempt,
        max_attempts=max_attempts,
        failure_fingerprint=failure_fingerprint,
        repair_owners=owners,
        issue_codes=codes,
    )


def _failure_fingerprint(issues: tuple[ValidationIssue, ...]) -> str:
    payload = sorted(
        (issue.fingerprint_payload() for issue in issues),
        key=lambda item: json.dumps(item, sort_keys=True, separators=(",", ":")),
    )
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _duration_ms(started_at: datetime) -> int:
    return max(0, int((datetime.now(UTC) - started_at).total_seconds() * 1000))


__all__ = [
    "AcceptanceController",
    "AcceptanceResult",
    "RepairDecision",
    "ValidationGate",
    "ValidationGateResult",
    "ValidationIssue",
    "ValidationRegistry",
    "ValidationRun",
]
