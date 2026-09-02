"""Canonical artifact results accepted from one compiled assignment."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Mapping, Sequence
from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from mozaiksai.core.runtime.app.layout_registry import ValidatorIdentifier
from mozaiksai.core.semantics.refs import PlanUnitRef

from .path_ownership import normalize_owned_path
from .plan_assignment_compiler import CompiledAssignment
from .structured_output_contracts import (
    resolve_structured_output_contract_ref,
    stable_digest,
)

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
ValidatorRunner = Callable[[ValidatorIdentifier, Mapping[str, str]], bool]


class _FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _sha256(value: str, *, field_name: str) -> str:
    text = str(value or "").strip()
    if _SHA256.fullmatch(text) is None:
        raise ValueError(f"{field_name} must be a lowercase SHA-256 digest")
    return text


def _content_digest(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _assert_path_set_closed(paths: Sequence[str]) -> None:
    if len(paths) != len(set(paths)):
        raise ValueError("artifact paths must be unique")
    by_lower: dict[str, str] = {}
    for path in paths:
        prior = by_lower.setdefault(path.casefold(), path)
        if prior != path:
            raise ValueError(f"case-fold artifact collision: {prior!r} and {path!r}")
    ordered = sorted(paths)
    for index, parent in enumerate(ordered):
        for child in ordered[index + 1 :]:
            if child.startswith(f"{parent}/"):
                raise ValueError(f"parent/child artifact collision: {parent!r} and {child!r}")


class AssignmentArtifact(_FrozenModel):
    """One exact UTF-8 artifact produced under assignment path authority."""

    path: str
    content: str
    content_digest: str

    @field_validator("path")
    @classmethod
    def _path(cls, value: str) -> str:
        return cast(str, normalize_owned_path(value))

    @field_validator("content_digest")
    @classmethod
    def _digest(cls, value: str) -> str:
        return _sha256(value, field_name="content_digest")

    @model_validator(mode="after")
    def _content_identity(self) -> AssignmentArtifact:
        if self.content_digest != _content_digest(self.content):
            raise ValueError("content_digest does not match canonical UTF-8 content")
        return self


class ValidatorReceipt(_FrozenModel):
    """Closed evidence emitted by the Mozaiks validator execution seam."""

    validator: ValidatorIdentifier
    subject_digest: str
    passed: bool
    evidence_digest: str

    @field_validator("subject_digest", "evidence_digest")
    @classmethod
    def _digests(cls, value: str, info: Any) -> str:
        return _sha256(value, field_name=str(info.field_name))

    @model_validator(mode="after")
    def _evidence_identity(self) -> ValidatorReceipt:
        expected = stable_digest(
            {
                "validator": self.validator.value,
                "subject_digest": self.subject_digest,
                "passed": self.passed,
            }
        )
        if self.evidence_digest != expected:
            raise ValueError("evidence_digest does not match validator receipt")
        return self


class AssignmentArtifactResult(_FrozenModel):
    """Authoritative artifact/provenance result, distinct from AG2 task state."""

    result_schema_version: Literal["mozaiks.assignment_artifact_result.v1"] = (
        "mozaiks.assignment_artifact_result.v1"
    )
    assignment_id: str = Field(min_length=1)
    assignment_digest: str
    plan_unit_ref: PlanUnitRef
    base_revision_digest: str | None
    structured_output_digest: str
    artifacts: tuple[AssignmentArtifact, ...] = Field(min_length=1)
    validation_receipts: tuple[ValidatorReceipt, ...] = Field(min_length=1)
    result_digest: str

    @field_validator("assignment_digest", "structured_output_digest", "result_digest")
    @classmethod
    def _digests(cls, value: str, info: Any) -> str:
        return _sha256(value, field_name=str(info.field_name))

    @field_validator("base_revision_digest")
    @classmethod
    def _base_digest(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _sha256(value, field_name="base_revision_digest")

    @field_validator("artifacts")
    @classmethod
    def _artifacts(cls, value: tuple[AssignmentArtifact, ...]) -> tuple[AssignmentArtifact, ...]:
        ordered = tuple(sorted(value, key=lambda item: item.path))
        _assert_path_set_closed([item.path for item in ordered])
        return ordered

    @field_validator("validation_receipts")
    @classmethod
    def _receipts(cls, value: tuple[ValidatorReceipt, ...]) -> tuple[ValidatorReceipt, ...]:
        ordered = tuple(sorted(value, key=lambda item: item.validator.value))
        validators = [item.validator for item in ordered]
        if ValidatorIdentifier.NONE in validators:
            raise ValueError("NONE is not an executable validator")
        if len(validators) != len(set(validators)):
            raise ValueError("validator receipts must be unique")
        if any(not item.passed for item in ordered):
            raise ValueError("authoritative artifact results require passing validators")
        return ordered

    @model_validator(mode="after")
    def _result_identity(self) -> AssignmentArtifactResult:
        subject_digest = _result_subject_digest(
            structured_output_digest=self.structured_output_digest,
            artifacts=self.artifacts,
        )
        if any(receipt.subject_digest != subject_digest for receipt in self.validation_receipts):
            raise ValueError(
                "validator receipt subject_digest does not match the exact artifact result"
            )
        payload = self.model_dump(mode="json", exclude={"result_digest"})
        if self.result_digest != stable_digest(payload):
            raise ValueError("result_digest does not match artifact result")
        return self


def _result_subject_digest(
    *, structured_output_digest: str, artifacts: Sequence[AssignmentArtifact]
) -> str:
    return cast(
        str,
        stable_digest(
            {
                "structured_output_digest": structured_output_digest,
                "artifacts": [
                    {"path": item.path, "content_digest": item.content_digest} for item in artifacts
                ],
            },
        ),
    )


def build_assignment_artifact_result(
    *,
    assignment: CompiledAssignment,
    structured_output: Mapping[str, Any] | BaseModel,
    artifacts: Mapping[str, str],
    structured_output_configs: Mapping[str, Any],
    validator_runner: ValidatorRunner,
) -> AssignmentArtifactResult:
    """Validate untrusted output and generate Mozaiks-owned validation receipts."""

    verified_assignment = CompiledAssignment.model_validate(assignment.model_dump(mode="json"))
    model = resolve_structured_output_contract_ref(
        verified_assignment.required_structured_output_ref,
        configs=structured_output_configs,
    )
    raw_output = (
        structured_output.model_dump(mode="json")
        if isinstance(structured_output, BaseModel)
        else structured_output
    )
    validated_output = model.model_validate(raw_output)
    structured_digest = stable_digest(validated_output.model_dump(mode="json"))

    entries = tuple(
        AssignmentArtifact(
            path=path,
            content=content,
            content_digest=_content_digest(content),
        )
        for path, content in artifacts.items()
    )
    entries = tuple(sorted(entries, key=lambda item: item.path))
    _assert_path_set_closed([item.path for item in entries])
    expected_paths = tuple(sorted(verified_assignment.owned_paths))
    actual_paths = tuple(item.path for item in entries)
    if actual_paths != expected_paths:
        missing = sorted(set(expected_paths) - set(actual_paths))
        extra = sorted(set(actual_paths) - set(expected_paths))
        raise ValueError(
            f"artifact paths must exactly match assignment ownership; "
            f"missing={missing}, extra={extra}"
        )

    subject_digest = _result_subject_digest(
        structured_output_digest=structured_digest, artifacts=entries
    )
    receipts: list[ValidatorReceipt] = []
    file_map = {item.path: item.content for item in entries}
    for validator in verified_assignment.required_validators:
        passed = validator_runner(validator, file_map)
        if not isinstance(passed, bool):
            raise TypeError("validator runner must return bool")
        receipt = ValidatorReceipt(
            validator=validator,
            subject_digest=subject_digest,
            passed=passed,
            evidence_digest=stable_digest(
                {
                    "validator": validator.value,
                    "subject_digest": subject_digest,
                    "passed": passed,
                }
            ),
        )
        if not passed:
            raise ValueError(f"required validator {validator.value!r} failed")
        receipts.append(receipt)

    payload: dict[str, Any] = {
        "result_schema_version": "mozaiks.assignment_artifact_result.v1",
        "assignment_id": verified_assignment.assignment_id,
        "assignment_digest": verified_assignment.assignment_digest,
        "plan_unit_ref": verified_assignment.plan_unit_ref,
        "base_revision_digest": verified_assignment.base_revision_digest,
        "structured_output_digest": structured_digest,
        "artifacts": entries,
        "validation_receipts": tuple(receipts),
    }
    return AssignmentArtifactResult(**payload, result_digest=stable_digest(payload))


def validate_assignment_artifact_result(
    *, assignment: CompiledAssignment, result: AssignmentArtifactResult
) -> AssignmentArtifactResult:
    """Cold-bind an artifact result back to its canonical assignment."""

    verified_assignment = CompiledAssignment.model_validate(assignment.model_dump(mode="json"))
    verified_result = AssignmentArtifactResult.model_validate(result.model_dump(mode="json"))
    if verified_result.assignment_id != verified_assignment.assignment_id:
        raise ValueError("artifact result references another assignment id")
    if verified_result.assignment_digest != verified_assignment.assignment_digest:
        raise ValueError("artifact result references another assignment digest")
    if verified_result.plan_unit_ref != verified_assignment.plan_unit_ref:
        raise ValueError("artifact result references another plan unit")
    if verified_result.base_revision_digest != verified_assignment.base_revision_digest:
        raise ValueError("artifact result has a stale base revision digest")
    if tuple(item.path for item in verified_result.artifacts) != tuple(
        sorted(verified_assignment.owned_paths)
    ):
        raise ValueError("artifact result paths do not match assignment ownership")
    if tuple(item.validator for item in verified_result.validation_receipts) != tuple(
        sorted(verified_assignment.required_validators, key=lambda item: item.value)
    ):
        raise ValueError("artifact result validator receipts do not match assignment")
    return cast(AssignmentArtifactResult, verified_result)


__all__ = [
    "AssignmentArtifact",
    "AssignmentArtifactResult",
    "ValidatorReceipt",
    "ValidatorRunner",
    "build_assignment_artifact_result",
    "validate_assignment_artifact_result",
]
