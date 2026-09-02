from __future__ import annotations

import hashlib
import inspect

import pytest
from pydantic import ValidationError

from mozaiksai.core.workflow.assignment_artifacts import (
    AssignmentArtifact,
    AssignmentArtifactResult,
    ValidatorReceipt,
    build_assignment_artifact_result,
    validate_assignment_artifact_result,
)
from mozaiksai.core.workflow.structured_output_contracts import stable_digest
from tests.slice_5b_helpers import compiled_assignment


def _build(*, paths: tuple[str, ...] = ("data/contract.json",)):
    assignment, config = compiled_assignment(paths=paths)
    artifacts = {path: f"content for {path}\n" for path in paths}
    result = build_assignment_artifact_result(
        assignment=assignment,
        structured_output={"message": "complete"},
        artifacts=artifacts,
        structured_output_configs={"AppGenerator": config},
        validator_runner=lambda _validator, _files: True,
    )
    return assignment, config, result


def test_artifact_result_is_closed_content_bound_and_order_independent() -> None:
    paths = ("modules/reports/backend/service.py", "modules/reports/backend/handler.py")
    assignment, config = compiled_assignment(paths=paths)
    first = build_assignment_artifact_result(
        assignment=assignment,
        structured_output={"message": "complete"},
        artifacts={paths[0]: "service\n", paths[1]: "handler\n"},
        structured_output_configs={"AppGenerator": config},
        validator_runner=lambda _validator, _files: True,
    )
    second = build_assignment_artifact_result(
        assignment=assignment,
        structured_output={"message": "complete"},
        artifacts={paths[1]: "handler\n", paths[0]: "service\n"},
        structured_output_configs={"AppGenerator": config},
        validator_runner=lambda _validator, _files: True,
    )
    assert first == second
    assert first.result_digest == second.result_digest
    assert [item.path for item in first.artifacts] == sorted(paths)
    assert validate_assignment_artifact_result(assignment=assignment, result=first) == first
    assert set(type(first).model_fields) == {
        "result_schema_version",
        "assignment_id",
        "assignment_digest",
        "plan_unit_ref",
        "base_revision_digest",
        "structured_output_digest",
        "artifacts",
        "validation_receipts",
        "result_digest",
    }


@pytest.mark.parametrize(
    ("paths", "artifacts", "match"),
    [
        (("a.py", "b.py"), {"a.py": "a"}, "missing"),
        (("a.py",), {"a.py": "a", "b.py": "b"}, "extra"),
        (("A.py", "a.py"), {"A.py": "a", "a.py": "b"}, "case"),
        (("pkg", "pkg/x.py"), {"pkg": "a", "pkg/x.py": "b"}, "parent/child"),
    ],
)
def test_missing_extra_case_and_parent_child_artifacts_fail(
    paths: tuple[str, ...], artifacts: dict[str, str], match: str
) -> None:
    assignment, config = compiled_assignment(paths=paths)
    with pytest.raises(ValueError, match=match):
        build_assignment_artifact_result(
            assignment=assignment,
            structured_output={"message": "complete"},
            artifacts=artifacts,
            structured_output_configs={"AppGenerator": config},
            validator_runner=lambda _validator, _files: True,
        )


def test_malformed_output_and_failed_validator_never_create_result() -> None:
    assignment, config = compiled_assignment()
    with pytest.raises(ValidationError):
        build_assignment_artifact_result(
            assignment=assignment,
            structured_output={"message": {"channel": "runtime"}},
            artifacts={"data/contract.json": "{}\n"},
            structured_output_configs={"AppGenerator": config},
            validator_runner=lambda _validator, _files: True,
        )
    with pytest.raises(ValueError, match="failed"):
        build_assignment_artifact_result(
            assignment=assignment,
            structured_output={"message": "complete"},
            artifacts={"data/contract.json": "{}\n"},
            structured_output_configs={"AppGenerator": config},
            validator_runner=lambda _validator, _files: False,
        )


def test_content_digest_forgery_and_cross_assignment_result_fail() -> None:
    assignment, _config, result = _build()
    artifact = result.artifacts[0].model_dump(mode="json")
    artifact["content_digest"] = "0" * 64
    with pytest.raises(ValidationError, match="content_digest"):
        AssignmentArtifact.model_validate(artifact)

    other, _ = compiled_assignment(base_revision_digest="c" * 64)
    with pytest.raises(ValueError, match="another assignment|stale base"):
        validate_assignment_artifact_result(assignment=other, result=result)


def test_caller_cannot_supply_receipts_or_runtime_fields_to_builder() -> None:
    signature = inspect.signature(build_assignment_artifact_result)
    assert "validation_receipts" not in signature.parameters
    assert "attempt_id" not in AssignmentArtifactResult.model_fields
    assert "status" not in AssignmentArtifactResult.model_fields
    assert "agent_id" not in AssignmentArtifactResult.model_fields

    _assignment, _config, result = _build()
    forged = result.model_dump(mode="json")
    forged["channel"] = "runtime-channel"
    with pytest.raises(ValidationError, match="extra"):
        AssignmentArtifactResult.model_validate(forged)


def test_missing_duplicate_failed_and_forged_receipts_fail() -> None:
    _assignment, _config, result = _build()
    document = result.model_dump(mode="json")
    document["validation_receipts"] = []
    with pytest.raises(ValidationError, match="at least 1"):
        AssignmentArtifactResult.model_validate(document)

    document = result.model_dump(mode="json")
    document["validation_receipts"] *= 2
    with pytest.raises(ValidationError, match="unique"):
        AssignmentArtifactResult.model_validate(document)

    receipt = result.validation_receipts[0]
    failed_payload = {
        "validator": receipt.validator.value,
        "subject_digest": receipt.subject_digest,
        "passed": False,
    }
    failed = ValidatorReceipt(**failed_payload, evidence_digest=stable_digest(failed_payload))
    document = result.model_dump(mode="json")
    document["validation_receipts"] = [failed.model_dump(mode="json")]
    with pytest.raises(ValidationError, match="passing validators"):
        AssignmentArtifactResult.model_validate(document)

    forged_receipt = receipt.model_dump(mode="json")
    forged_receipt["evidence_digest"] = "0" * 64
    with pytest.raises(ValidationError, match="evidence_digest"):
        ValidatorReceipt.model_validate(forged_receipt)


def test_duplicate_artifact_entries_fail_even_with_valid_individual_digests() -> None:
    _assignment, _config, result = _build()
    document = result.model_dump(mode="json")
    document["artifacts"] *= 2
    with pytest.raises(ValidationError, match="unique"):
        AssignmentArtifactResult.model_validate(document)


def test_validator_receipt_cannot_be_transplanted_to_different_exact_result() -> None:
    _assignment, _config, result = _build()
    document = result.model_dump(mode="json")
    document["artifacts"][0]["content"] += "\n# changed after validation\n"
    document["artifacts"][0]["content_digest"] = hashlib.sha256(
        document["artifacts"][0]["content"].encode("utf-8")
    ).hexdigest()
    document["result_digest"] = stable_digest(
        {key: value for key, value in document.items() if key != "result_digest"}
    )

    with pytest.raises(ValidationError, match="receipt subject_digest"):
        AssignmentArtifactResult.model_validate(document)
