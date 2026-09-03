"""RunBuildBinding contract and cold-verification matrix.

The exact server-owned relation ``workflow_run_id -> build_id`` is the only
authority for build-specific claims. These tests prove the sealed binding
contract (issue/parse/digest), the run-level WorkflowRunFailure terminal
shape, and cold verification against persisted BuildRecords: run A binds
build A, run B binds build B, a pre-build run has no binding, and a foreign
build, wrong run, wrong app, wrong family/key, wrong version, or stale
digest never verifies.
"""
from __future__ import annotations

import json
from typing import Any

import pytest
from pydantic import ValidationError

from mozaiksai.core.artifacts.build_receipt import (
    BuildFailureReceipt,
    ReceiptValidationError,
    WorkflowRunFailure,
    issue_failure_receipt,
    issue_run_failure,
    issue_success_receipt,
    parse_terminal_receipt,
)
from mozaiksai.core.artifacts.closure import (
    verify_run_build_binding_closure,
    verify_terminal_receipt_closure,
)
from mozaiksai.core.artifacts.models import (
    ArtifactCommitMetadata,
    BuildRecord,
    BuildRecordFileEntry,
    BuildRecordStatus,
)
from mozaiksai.core.artifacts.run_build_binding import (
    BindingValidationError,
    RunBuildBinding,
    issue_run_build_binding,
    parse_run_build_binding,
)

pytestmark = pytest.mark.asyncio

_APP = "app_field_service"
_WF = "AppGenerator"
_DIGEST_A = "a" * 64
_DIGEST_B = "b" * 64


class _FakeStore:
    """Minimal persisted-record resolver for cold verification."""

    def __init__(self) -> None:
        self.records: dict[tuple[str, str], BuildRecord] = {}

    def add(self, record: BuildRecord) -> BuildRecord:
        self.records[(record.app_id, record.id)] = record
        return record

    async def get_build_record(
        self, *, app_id: str, build_record_id: str
    ) -> BuildRecord | None:
        return self.records.get((str(app_id), str(build_record_id)))


def _record(
    *,
    record_id: str,
    workflow_run_id: str | None,
    build_id: str | None,
    app_id: str = _APP,
    build_family: str = "app_bundle",
    build_key: str = "app_bundle",
    version_number: int = 1,
    bundle_digest: str | None = _DIGEST_A,
    lifecycle_status: BuildRecordStatus = BuildRecordStatus.CURRENT,
    parent_build_record_id: str | None = None,
    summary_payload: dict[str, Any] | None = None,
) -> BuildRecord:
    manifest = (
        [BuildRecordFileEntry(path="bundle.zip", sha256=bundle_digest)]
        if bundle_digest
        else []
    )
    metadata: dict[str, Any] = {}
    if summary_payload is not None:
        metadata["summary_payload"] = summary_payload
    return BuildRecord(
        _id=record_id,
        app_id=app_id,
        build_family=build_family,
        build_key=build_key,
        version_number=version_number,
        parent_build_record_id=parent_build_record_id,
        lineage_root_id=record_id,
        workflow_run_id=workflow_run_id,
        build_id=build_id,
        lifecycle_status=lifecycle_status,
        files_manifest=manifest,
        commit_metadata=ArtifactCommitMetadata(metadata=metadata),
    )


def _binding(
    *,
    workflow_run_id: str,
    build_id: str,
    record_id: str,
    app_id: str = _APP,
    workflow_name: str = _WF,
    build_family: str = "app_bundle",
    build_key: str = "app_bundle",
    version_number: int = 1,
    bundle_digest: str | None = _DIGEST_A,
) -> RunBuildBinding:
    return issue_run_build_binding(
        app_id=app_id,
        workflow_name=workflow_name,
        workflow_run_id=workflow_run_id,
        build_id=build_id,
        build_record_id=record_id,
        build_family=build_family,
        build_key=build_key,
        version_number=version_number,
        bundle_digest=bundle_digest,
    )


# ---------------------------------------------------------------------------
# Sealed contract: issue / parse / digest
# ---------------------------------------------------------------------------


async def test_binding_round_trips_through_persistence_and_seals_its_body() -> None:
    binding = _binding(workflow_run_id="wfrun_a", build_id="build_a", record_id="rec_a")

    # Cold resolution: the binding survives a JSON persistence round trip
    # (restart) and parses back to the identical sealed object.
    persisted = json.loads(json.dumps(binding.model_dump(mode="json")))
    restored = parse_run_build_binding(persisted)
    assert restored == binding
    assert restored.workflow_run_id == "wfrun_a"
    assert restored.build_id == "build_a"
    assert restored.build_record_id == "rec_a"

    # Any altered field fails digest verification — the seal covers the body.
    tampered = dict(persisted)
    tampered["build_id"] = "build_of_another_run"
    with pytest.raises(BindingValidationError):
        parse_run_build_binding(tampered)

    with pytest.raises(BindingValidationError):
        parse_run_build_binding({**persisted, "binding_digest": "0" * 64})

    with pytest.raises(BindingValidationError):
        parse_run_build_binding("not-a-mapping")

    with pytest.raises(BindingValidationError):
        parse_run_build_binding({**persisted, "extra_claim": True})


async def test_binding_requires_complete_closed_identity() -> None:
    for missing in (
        "app_id",
        "workflow_name",
        "workflow_run_id",
        "build_id",
        "build_record_id",
        "build_family",
        "build_key",
    ):
        kwargs = dict(
            app_id=_APP,
            workflow_name=_WF,
            workflow_run_id="wfrun_a",
            build_id="build_a",
            build_record_id="rec_a",
            build_family="app_bundle",
            build_key="app_bundle",
            version_number=1,
        )
        kwargs[missing] = "  "
        with pytest.raises(ValidationError):
            issue_run_build_binding(**kwargs)  # type: ignore[arg-type]

    with pytest.raises(ValidationError):
        _binding(
            workflow_run_id="wfrun_a",
            build_id="build_a",
            record_id="rec_a",
            version_number=0,
        )
    with pytest.raises(ValidationError):
        _binding(
            workflow_run_id="wfrun_a",
            build_id="build_a",
            record_id="rec_a",
            bundle_digest="not-a-sha",
        )


# ---------------------------------------------------------------------------
# Run-level terminal failure: no build, no fabrication
# ---------------------------------------------------------------------------


async def test_workflow_run_failure_carries_no_build_identity() -> None:
    outcome = issue_run_failure(
        app_id=_APP,
        workflow_name=_WF,
        workflow_run_id="wfrun_prebuild",
        error_code="run_failed_before_build",
    )
    assert isinstance(outcome, WorkflowRunFailure)
    assert "build_id" not in type(outcome).model_fields

    parsed = parse_terminal_receipt(outcome.model_dump(mode="json"))
    assert isinstance(parsed, WorkflowRunFailure)
    assert parsed.workflow_run_id == "wfrun_prebuild"
    assert parsed.error_code == "run_failed_before_build"

    # Injecting a build claim into the run-level shape is rejected outright.
    forged = {**outcome.model_dump(mode="json"), "build_id": "build_stolen"}
    with pytest.raises(ReceiptValidationError):
        parse_terminal_receipt(forged)


async def test_build_failure_receipt_requires_exact_build_identity() -> None:
    receipt = issue_failure_receipt(
        app_id=_APP,
        workflow_name=_WF,
        workflow_run_id="wfrun_b",
        build_id="build_b",
        error_code="lineage_registration_failed",
    )
    assert isinstance(receipt, BuildFailureReceipt)
    assert receipt.build_id == "build_b"

    with pytest.raises(ValidationError):
        issue_failure_receipt(
            app_id=_APP,
            workflow_name=_WF,
            workflow_run_id="wfrun_b",
            build_id="  ",
            error_code="lineage_registration_failed",
        )


# ---------------------------------------------------------------------------
# Cold verification against persisted records
# ---------------------------------------------------------------------------


async def test_run_a_and_run_b_each_verify_only_their_own_build() -> None:
    store = _FakeStore()
    store.add(
        _record(record_id="rec_a", workflow_run_id="wfrun_a", build_id="build_a")
    )
    store.add(
        _record(
            record_id="rec_b",
            workflow_run_id="wfrun_b",
            build_id="build_b",
            version_number=2,
            bundle_digest=_DIGEST_B,
        )
    )
    binding_a = _binding(workflow_run_id="wfrun_a", build_id="build_a", record_id="rec_a")
    binding_b = _binding(
        workflow_run_id="wfrun_b",
        build_id="build_b",
        record_id="rec_b",
        version_number=2,
        bundle_digest=_DIGEST_B,
    )

    assert await verify_run_build_binding_closure(
        binding_a, workflow_run_id="wfrun_a", app_id=_APP, workflow_name=_WF, store=store
    )
    assert await verify_run_build_binding_closure(
        binding_b, workflow_run_id="wfrun_b", app_id=_APP, workflow_name=_WF, store=store
    )

    # Foreign build: run B presenting run A's binding (and vice versa) never
    # verifies — the relation belongs to exactly one run.
    assert not await verify_run_build_binding_closure(
        binding_a, workflow_run_id="wfrun_b", app_id=_APP, workflow_name=_WF, store=store
    )
    assert not await verify_run_build_binding_closure(
        binding_b, workflow_run_id="wfrun_a", app_id=_APP, workflow_name=_WF, store=store
    )


async def test_binding_closure_fails_closed_on_every_disagreement() -> None:
    store = _FakeStore()
    store.add(
        _record(record_id="rec_a", workflow_run_id="wfrun_a", build_id="build_a")
    )

    async def _verifies(binding: RunBuildBinding, **overrides: Any) -> bool:
        kwargs = dict(
            workflow_run_id="wfrun_a", app_id=_APP, workflow_name=_WF, store=store
        )
        kwargs.update(overrides)
        return await verify_run_build_binding_closure(binding, **kwargs)

    good = _binding(workflow_run_id="wfrun_a", build_id="build_a", record_id="rec_a")
    assert await _verifies(good)

    # Wrong current run / app / workflow scope.
    assert not await _verifies(good, workflow_run_id="wfrun_other")
    assert not await _verifies(good, workflow_run_id="")
    assert not await _verifies(good, app_id="other_app")
    assert not await _verifies(good, workflow_name="OtherWorkflow")

    # Referenced record does not exist (or exists under another app only).
    assert not await _verifies(
        _binding(workflow_run_id="wfrun_a", build_id="build_a", record_id="rec_missing")
    )

    # Unbound record: persisted without any run binding can never satisfy one.
    store.add(_record(record_id="rec_unbound", workflow_run_id=None, build_id=None))
    assert not await _verifies(
        _binding(workflow_run_id="wfrun_a", build_id="build_a", record_id="rec_unbound")
    )

    # Record stamped for a different run or different build.
    store.add(
        _record(record_id="rec_other_run", workflow_run_id="wfrun_z", build_id="build_a")
    )
    assert not await _verifies(
        _binding(workflow_run_id="wfrun_a", build_id="build_a", record_id="rec_other_run")
    )
    store.add(
        _record(
            record_id="rec_other_build", workflow_run_id="wfrun_a", build_id="build_z"
        )
    )
    assert not await _verifies(
        _binding(
            workflow_run_id="wfrun_a", build_id="build_a", record_id="rec_other_build"
        )
    )

    # Family/key/version vocabulary disagreement.
    assert not await _verifies(
        _binding(
            workflow_run_id="wfrun_a",
            build_id="build_a",
            record_id="rec_a",
            build_family="workflow_bundle",
        )
    )
    assert not await _verifies(
        _binding(
            workflow_run_id="wfrun_a",
            build_id="build_a",
            record_id="rec_a",
            build_key="other_key",
        )
    )
    assert not await _verifies(
        _binding(
            workflow_run_id="wfrun_a",
            build_id="build_a",
            record_id="rec_a",
            version_number=7,
        )
    )

    # Stale digest: the binding's bundle digest is absent from the persisted
    # manifest authority.
    assert not await _verifies(
        _binding(
            workflow_run_id="wfrun_a",
            build_id="build_a",
            record_id="rec_a",
            bundle_digest=_DIGEST_B,
        )
    )

    # A binding without an established digest still verifies on identity.
    assert await _verifies(
        _binding(
            workflow_run_id="wfrun_a",
            build_id="build_a",
            record_id="rec_a",
            bundle_digest=None,
        )
    )


async def test_pre_build_run_resolves_no_binding() -> None:
    """A run that never established a build has nothing to parse — absence is
    the truthful state, never an invitation to derive one from session data."""
    for absent in (None, {}, ""):
        with pytest.raises(BindingValidationError):
            parse_run_build_binding(absent)


# ---------------------------------------------------------------------------
# Terminal receipt closure through the OSS verifier
# ---------------------------------------------------------------------------


def _closure_fixture() -> tuple[_FakeStore, Any]:
    store = _FakeStore()
    store.add(
        _record(record_id="rec_bundle", workflow_run_id="wfrun_a", build_id="build_a")
    )
    store.add(
        _record(
            record_id="rec_acv",
            workflow_run_id="wfrun_a",
            build_id="build_a",
            build_family="app_context_version",
            build_key="app_context_version",
            bundle_digest=None,
            summary_payload={
                "context_version_id": "acv_logical_1",
                "app_id": _APP,
                "artifact_refs": [
                    {"artifact_kind": "app_bundle", "artifact_version_id": "rec_bundle"}
                ],
            },
        )
    )
    receipt = issue_success_receipt(
        app_id=_APP,
        workflow_name=_WF,
        workflow_run_id="wfrun_a",
        build_id="build_a",
        build_record_id="rec_bundle",
        app_context_version_id="acv_logical_1",
        app_context_record_id="rec_acv",
        bundle_digest=_DIGEST_A,
    )
    return store, receipt


async def test_success_receipt_closure_verifies_exact_persisted_lineage() -> None:
    store, receipt = _closure_fixture()
    assert await verify_terminal_receipt_closure(receipt, app_id=_APP, store=store)


async def test_success_receipt_closure_fails_closed_on_substitution() -> None:
    store, receipt = _closure_fixture()

    # A real but unrelated CURRENT record under another run never suffices.
    store.add(
        _record(record_id="rec_bundle", workflow_run_id="wfrun_z", build_id="build_z")
    )
    assert not await verify_terminal_receipt_closure(receipt, app_id=_APP, store=store)

    # Restore, then break the AppContextVersion cross-reference.
    store, receipt = _closure_fixture()
    store.add(
        _record(
            record_id="rec_acv",
            workflow_run_id="wfrun_a",
            build_id="build_a",
            build_family="app_context_version",
            build_key="app_context_version",
            bundle_digest=None,
            summary_payload={
                "context_version_id": "acv_logical_1",
                "app_id": _APP,
                "artifact_refs": [
                    {"artifact_kind": "app_bundle", "artifact_version_id": "rec_other"}
                ],
            },
        )
    )
    assert not await verify_terminal_receipt_closure(receipt, app_id=_APP, store=store)

    # Restore, then demote the bundle record: a non-CURRENT record cannot
    # satisfy a success receipt.
    store, receipt = _closure_fixture()
    store.add(
        _record(
            record_id="rec_bundle",
            workflow_run_id="wfrun_a",
            build_id="build_a",
            lifecycle_status=BuildRecordStatus.DRAFT,
        )
    )
    assert not await verify_terminal_receipt_closure(receipt, app_id=_APP, store=store)
