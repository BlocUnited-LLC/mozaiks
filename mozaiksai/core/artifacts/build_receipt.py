"""Immutable run-bound terminal build receipts.

A workflow run that reaches its terminal build outcome writes exactly one
closed receipt binding the outcome to the run's immutable identity and to the
persisted lineage it produced. Chat-scoped progress markers (for example
``download_status``) remain UI projections only — a receipt is the sole
success/failure authority the completion bridge may act on, and a receipt for
one run can never complete another run even inside the same chat.

The receipt is deliberately minimal and cold-verifiable: identity fields plus
persisted-lineage references plus a content digest over those fields. It
carries no chat history, prompts, agent objects, provider/channel identity,
free-form metadata, timestamps-as-identity, Git state, or hosted state.

This is temporary current-product hardening: the BuildRecord references here
follow the temporary pre-5D publication authority, which ADR 0007 Slice 5D
will later replace with the ArtifactRevision/ApplicationPublication authority.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

TERMINAL_RECEIPT_CONTEXT_KEY = "build_terminal_receipt"

RECEIPT_SCHEMA_VERSION = "mozaiks.build_receipt.v1"

BuildFailureCode = Literal[
    "bundle_generation_failed",
    "lineage_registration_failed",
    "bundle_acceptance_failed",
]


class ReceiptValidationError(ValueError):
    """A terminal receipt is malformed, mistyped, or fails digest verification."""


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _digest_payload(data: dict[str, Any]) -> str:
    canonical = json.dumps(
        {key: value for key, value in data.items() if key != "receipt_digest"},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class _ReceiptBase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["mozaiks.build_receipt.v1"]
    scope: Literal["server"]
    app_id: str
    workflow_name: str
    workflow_run_id: str
    build_id: str
    receipt_digest: str

    @field_validator("app_id", "workflow_name", "workflow_run_id", "build_id")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        value = str(value or "").strip()
        if not value:
            raise ValueError("receipt identity fields must be non-empty")
        return value

    def verify_digest(self) -> None:
        expected = _digest_payload(self.model_dump(mode="json"))
        if expected != self.receipt_digest:
            raise ReceiptValidationError(
                "terminal receipt digest mismatch: receipt was altered after issue"
            )


class _LineageReceipt(_ReceiptBase):
    """Shared required lineage fields for terminal receipts that reference
    persisted build closure. No optional fields — absence means invalid."""

    build_record_id: str
    app_context_version_id: str
    app_context_record_id: str
    bundle_digest: str

    @field_validator("build_record_id", "app_context_version_id", "app_context_record_id")
    @classmethod
    def _refs_non_empty(cls, value: str) -> str:
        value = str(value or "").strip()
        if not value:
            raise ValueError("terminal receipt lineage references must be non-empty")
        return value

    @field_validator("bundle_digest")
    @classmethod
    def _digest_shape(cls, value: str) -> str:
        value = str(value or "").strip()
        if not _SHA256_RE.match(value):
            raise ValueError("bundle_digest must be a lowercase 64-hex sha256 digest")
        return value


class BuildSuccessReceipt(_LineageReceipt):
    """Genesis terminal success: the complete CURRENT lineage for exactly one run.

    Every field is required — there is no nullable half-success shape. The
    referenced app-bundle BuildRecord must be CURRENT and carry this exact
    run/build binding; the referenced AppContextVersion record must be
    CURRENT, carry the same binding, and its logical version and bundle
    cross-reference must agree. ``bundle_digest`` is the required lowercase
    SHA-256 of the bundle zip and must agree with the persisted manifest.
    """

    kind: Literal["success"]
    status: Literal["succeeded"]


class BuildRevisionCandidateReceipt(_LineageReceipt):
    """Refinement terminal outcome: a persisted non-current revision candidate.

    A distinct closed variant so a refinement candidate can never be confused
    with a Genesis CURRENT success. The referenced BuildRecord must be a
    DRAFT that carries parent lineage plus this exact run/build binding; all
    lineage and digest requirements match the success receipt otherwise.
    """

    kind: Literal["revision_candidate"]
    status: Literal["candidate"]


class BuildFailureReceipt(_ReceiptBase):
    """Terminal failure: exact run/build identity plus a finite failure code.

    Carries no lineage references — a failed run has no accepted lineage and
    the receipt must not fabricate one.
    """

    kind: Literal["failure"]
    status: Literal["failed"]
    error_code: BuildFailureCode


TerminalBuildReceipt = Annotated[
    BuildSuccessReceipt | BuildRevisionCandidateReceipt | BuildFailureReceipt,
    Field(discriminator="kind"),
]


def _issue_lineage_receipt(
    model_cls: type[_LineageReceipt],
    *,
    kind: str,
    status: str,
    app_id: str,
    workflow_name: str,
    workflow_run_id: str,
    build_id: str,
    build_record_id: str,
    app_context_version_id: str,
    app_context_record_id: str,
    bundle_digest: str,
) -> Any:
    payload: dict[str, Any] = {
        "schema_version": RECEIPT_SCHEMA_VERSION,
        "kind": kind,
        "scope": "server",
        "status": status,
        "app_id": app_id,
        "workflow_name": workflow_name,
        "workflow_run_id": workflow_run_id,
        "build_id": build_id,
        "build_record_id": build_record_id,
        "app_context_version_id": app_context_version_id,
        "app_context_record_id": app_context_record_id,
        "bundle_digest": bundle_digest,
        "receipt_digest": "",
    }
    model = model_cls.model_validate(
        {**payload, "receipt_digest": _digest_payload({**payload, "receipt_digest": ""})}
    )
    # Recompute over the validated/normalized dump so verification is stable.
    dumped = model.model_dump(mode="json")
    return model_cls.model_validate({**dumped, "receipt_digest": _digest_payload(dumped)})


def issue_success_receipt(
    *,
    app_id: str,
    workflow_name: str,
    workflow_run_id: str,
    build_id: str,
    build_record_id: str,
    app_context_version_id: str,
    app_context_record_id: str,
    bundle_digest: str,
) -> BuildSuccessReceipt:
    receipt: BuildSuccessReceipt = _issue_lineage_receipt(
        BuildSuccessReceipt,
        kind="success",
        status="succeeded",
        app_id=app_id,
        workflow_name=workflow_name,
        workflow_run_id=workflow_run_id,
        build_id=build_id,
        build_record_id=build_record_id,
        app_context_version_id=app_context_version_id,
        app_context_record_id=app_context_record_id,
        bundle_digest=bundle_digest,
    )
    return receipt


def issue_revision_candidate_receipt(
    *,
    app_id: str,
    workflow_name: str,
    workflow_run_id: str,
    build_id: str,
    build_record_id: str,
    app_context_version_id: str,
    app_context_record_id: str,
    bundle_digest: str,
) -> BuildRevisionCandidateReceipt:
    receipt: BuildRevisionCandidateReceipt = _issue_lineage_receipt(
        BuildRevisionCandidateReceipt,
        kind="revision_candidate",
        status="candidate",
        app_id=app_id,
        workflow_name=workflow_name,
        workflow_run_id=workflow_run_id,
        build_id=build_id,
        build_record_id=build_record_id,
        app_context_version_id=app_context_version_id,
        app_context_record_id=app_context_record_id,
        bundle_digest=bundle_digest,
    )
    return receipt


def issue_failure_receipt(
    *,
    app_id: str,
    workflow_name: str,
    workflow_run_id: str,
    build_id: str,
    error_code: BuildFailureCode,
) -> BuildFailureReceipt:
    payload: dict[str, Any] = {
        "schema_version": RECEIPT_SCHEMA_VERSION,
        "kind": "failure",
        "scope": "server",
        "status": "failed",
        "app_id": app_id,
        "workflow_name": workflow_name,
        "workflow_run_id": workflow_run_id,
        "build_id": build_id,
        "error_code": error_code,
        "receipt_digest": "",
    }
    model = BuildFailureReceipt.model_validate(
        {**payload, "receipt_digest": _digest_payload({**payload, "receipt_digest": ""})}
    )
    dumped = model.model_dump(mode="json")
    signed: BuildFailureReceipt = BuildFailureReceipt.model_validate(
        {**dumped, "receipt_digest": _digest_payload(dumped)}
    )
    return signed


def parse_terminal_receipt(
    raw: Any,
) -> BuildSuccessReceipt | BuildRevisionCandidateReceipt | BuildFailureReceipt:
    """Parse and digest-verify a persisted terminal receipt.

    Raises :class:`ReceiptValidationError` for anything that is not a valid,
    unaltered receipt. Callers treat that as "no usable receipt" and fail
    closed — a malformed receipt never authorizes a lifecycle claim.
    """
    if not isinstance(raw, dict):
        raise ReceiptValidationError("terminal receipt must be a mapping")
    kind = raw.get("kind")
    try:
        receipt: BuildSuccessReceipt | BuildRevisionCandidateReceipt | BuildFailureReceipt
        if kind == "success":
            receipt = BuildSuccessReceipt.model_validate(raw)
        elif kind == "revision_candidate":
            receipt = BuildRevisionCandidateReceipt.model_validate(raw)
        elif kind == "failure":
            receipt = BuildFailureReceipt.model_validate(raw)
        else:
            raise ReceiptValidationError(f"unknown terminal receipt kind: {kind!r}")
    except ReceiptValidationError:
        raise
    except Exception as exc:
        raise ReceiptValidationError(f"invalid terminal receipt: {type(exc).__name__}") from exc
    receipt.verify_digest()
    return receipt


__all__ = [
    "TERMINAL_RECEIPT_CONTEXT_KEY",
    "RECEIPT_SCHEMA_VERSION",
    "BuildFailureCode",
    "BuildFailureReceipt",
    "BuildRevisionCandidateReceipt",
    "BuildSuccessReceipt",
    "ReceiptValidationError",
    "TerminalBuildReceipt",
    "issue_failure_receipt",
    "issue_revision_candidate_receipt",
    "issue_success_receipt",
    "parse_terminal_receipt",
]
