from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from mozaiksai.control_plane.app_context import (
    APP_CONTEXT_MISSING_WARNING,
    AppContextSummary,
    summarize_app_context_version,
)
from mozaiksai.control_plane.app_context_policy import (
    AppContextPolicyDecision,
    AppContextPolicyResult,
    evaluate_app_context_policy,
)
from mozaiksai.control_plane.app_context_refresh import (
    CONTEXT_REFRESH_REQUIRED_INPUTS,
    build_context_refresh_plan,
    build_context_refresh_request,
)
from mozaiksai.core.app_context.models import (
    AppContextMode,
    AppContextStaleStatus,
    AppContextVersion,
    ArtifactRef,
    SourceRef,
    SourceRefKind,
)
from mozaiksai.core.app_context.refresh import (
    BROWNFIELD_DISCOVERY_REFRESH_SEQUENCE,
    CONTEXT_REFRESH_EXPECTED_ARTIFACTS,
    ContextRefreshPlan,
    ContextRefreshRequest,
    ContextRefreshResult,
    ContextRefreshScope,
)
from mozaiksai.core.app_context.store import (
    get_current_app_context_version,
    register_app_context_version,
)
from mozaiksai.core.artifacts.models import (
    BuildRecord,
    BuildRecordStatus,
    BuildRecordValidationStatus,
)

ROOT = Path(__file__).resolve().parents[1]


class _MemoryBuildRecordStore:
    def __init__(self) -> None:
        self.versions: dict[str, BuildRecord] = {}
        self._counter = 0

    async def create_build_record(self, **kwargs: Any) -> BuildRecord:
        self._counter += 1
        record_id = f"br_{self._counter}"
        doc = BuildRecord(
            _id=record_id,
            app_id=kwargs["app_id"],
            build_family=kwargs["build_family"],
            build_key=kwargs["build_key"],
            version_number=self._counter,
            lineage_root_id=record_id,
            source_workflow=kwargs.get("source_workflow"),
            source_chat_id=kwargs.get("source_chat_id"),
            canonical_inputs_version=kwargs.get("canonical_inputs_version") or {},
            lifecycle_status=kwargs.get("lifecycle_status", BuildRecordStatus.DRAFT),
            validation_status=kwargs.get("validation_status", BuildRecordValidationStatus.PENDING),
            files_manifest=kwargs.get("files_manifest") or [],
            commit_metadata=kwargs.get("commit_metadata") or {},
        )
        self.versions[doc.id] = doc
        return doc

    async def get_build_record(
        self,
        *,
        app_id: str,
        build_record_id: str,
    ) -> BuildRecord | None:
        doc = self.versions.get(build_record_id)
        if doc is None or doc.app_id != app_id:
            return None
        return doc

    async def accept_build_record(
        self,
        *,
        app_id: str,
        build_record_id: str,
        commit_metadata: dict[str, Any] | None = None,
    ) -> BuildRecord | None:
        doc = await self.get_build_record(
            app_id=app_id,
            build_record_id=build_record_id,
        )
        if doc is None:
            return None
        for version in self.versions.values():
            if (
                version.app_id == app_id
                and version.build_family == doc.build_family
                and version.build_key == doc.build_key
                and version.id != doc.id
                and version.lifecycle_status is BuildRecordStatus.CURRENT
            ):
                version.lifecycle_status = BuildRecordStatus.SUPERSEDED
        doc.lifecycle_status = BuildRecordStatus.CURRENT
        if commit_metadata is not None:
            doc.commit_metadata = commit_metadata
        return doc

    async def list_build_records(
        self,
        *,
        app_id: str,
        build_family: str | None = None,
        build_key: str | None = None,
        lifecycle_status: BuildRecordStatus | None = None,
        limit: int = 50,
        **_kwargs: Any,
    ) -> list[BuildRecord]:
        rows = [doc for doc in self.versions.values() if doc.app_id == app_id]
        if build_family is not None:
            rows = [doc for doc in rows if doc.build_family == build_family]
        if build_key is not None:
            rows = [doc for doc in rows if doc.build_key == build_key]
        if lifecycle_status is not None:
            rows = [doc for doc in rows if doc.lifecycle_status is lifecycle_status]
        return sorted(rows, key=lambda doc: doc.version_number, reverse=True)[:limit]


def _source_ref() -> SourceRef:
    return SourceRef(
        source_ref_id="src_repo",
        kind=SourceRefKind.REPO,
        uri="https://example.invalid/ops-studio.git",
        ref="main",
        checksum="sha256:source",
    )


def _stale_brownfield_summary() -> AppContextSummary:
    context_version = AppContextVersion(
        context_version_id="ctx_ops_stale",
        app_id="ops_studio",
        mode=AppContextMode.BROWNFIELD,
        source_refs=[_source_ref()],
        stale_status=AppContextStaleStatus.STALE,
        stale_reasons=["source ref changed"],
    )
    return summarize_app_context_version(context_version)


def _missing_summary() -> AppContextSummary:
    return AppContextSummary(
        app_id="ops_studio",
        available=False,
        warnings=[APP_CONTEXT_MISSING_WARNING],
    )


def _refresh_block_policy(summary: AppContextSummary) -> AppContextPolicyResult:
    return evaluate_app_context_policy(
        app_context_summary=summary,
        change_class="feature",
        refinement_lane="data_model_migration",
        affected_bundle_paths=["data/migrations/add_status.json"],
    )


def test_can_create_context_refresh_request() -> None:
    request = ContextRefreshRequest(
        app_id="ops_studio",
        current_context_version_id="ctx_ops_stale",
        reason="Refresh before data model migration.",
        source_refs=[_source_ref()],
        requested_by="user_1",
        refresh_scope=ContextRefreshScope.DISCOVERY_INDEXING,
    )

    assert request.app_id == "ops_studio"
    assert request.current_context_version_id == "ctx_ops_stale"
    assert request.source_refs[0].kind is SourceRefKind.REPO
    assert request.requested_by == "user_1"


def test_can_create_context_refresh_plan() -> None:
    plan = ContextRefreshPlan(
        app_id="ops_studio",
        current_context_version_id="ctx_ops_stale",
        target_source_refs=[_source_ref()],
        required_inputs=list(CONTEXT_REFRESH_REQUIRED_INPUTS),
    )

    assert plan.workflow_sequence == BROWNFIELD_DISCOVERY_REFRESH_SEQUENCE
    assert plan.mutation_allowed is False
    assert plan.current_context_version_id == "ctx_ops_stale"


def test_refresh_result_contract_records_new_context() -> None:
    result = ContextRefreshResult(
        app_id="ops_studio",
        previous_context_version_id="ctx_old",
        new_context_version_id="ctx_new",
        stale_resolved=True,
        artifacts_created=[
            ArtifactRef(
                build_family="app_context_version",
                build_record_id="av_ctx_new",
            )
        ],
    )

    assert result.previous_context_version_id == "ctx_old"
    assert result.new_context_version_id == "ctx_new"
    assert result.stale_resolved is True
    assert result.artifacts_created[0].build_family == "app_context_version"


def test_plan_lists_expected_canonical_brownfield_artifacts() -> None:
    plan = ContextRefreshPlan(
        app_id="ops_studio",
        current_context_version_id="ctx_ops_stale",
        target_source_refs=[_source_ref()],
    )

    assert set(CONTEXT_REFRESH_EXPECTED_ARTIFACTS).issubset(set(plan.expected_artifacts))
    assert "module_decomposition_plan" not in plan.expected_artifacts


def test_block_requires_context_refresh_policy_can_build_refresh_plan() -> None:
    summary = _stale_brownfield_summary()
    policy = _refresh_block_policy(summary)

    plan = build_context_refresh_plan(
        policy_result=policy,
        app_context_summary=summary,
        requested_by="user_1",
    )

    assert policy.decision is AppContextPolicyDecision.BLOCK_REQUIRES_CONTEXT_REFRESH
    assert plan.current_context_version_id == "ctx_ops_stale"
    assert plan.target_source_refs[0].uri == "https://example.invalid/ops-studio.git"
    assert plan.mutation_allowed is False
    assert plan.workflow_sequence == BROWNFIELD_DISCOVERY_REFRESH_SEQUENCE


def test_missing_app_context_can_build_refresh_plan_with_warnings() -> None:
    summary = _missing_summary()
    policy = _refresh_block_policy(summary)

    plan = build_context_refresh_plan(
        policy_result=policy,
        app_context_summary=summary,
        app_id="ops_studio",
    )

    assert plan.current_context_version_id is None
    assert plan.target_source_refs == []
    assert any("No current AppContextVersion is available" in warning for warning in plan.warnings)
    assert any("no source refs" in warning for warning in plan.warnings)
    assert plan.mutation_allowed is False


def test_refresh_request_can_be_built_from_policy_and_summary() -> None:
    summary = _stale_brownfield_summary()
    policy = _refresh_block_policy(summary)

    request = build_context_refresh_request(
        app_context_summary=summary,
        policy_result=policy,
        requested_by="user_1",
        refresh_scope=ContextRefreshScope.SOURCE_REF_RESCAN,
    )

    assert request.app_id == "ops_studio"
    assert request.current_context_version_id == "ctx_ops_stale"
    assert request.reason == "Refinement requires current App Intelligence context."
    assert request.refresh_scope is ContextRefreshScope.SOURCE_REF_RESCAN


def test_refresh_plan_rejects_non_refresh_policy() -> None:
    policy = AppContextPolicyResult(decision=AppContextPolicyDecision.ALLOW)

    with pytest.raises(ValueError, match="block_requires_context_refresh"):
        build_context_refresh_plan(
            policy_result=policy,
            app_context_summary=_stale_brownfield_summary(),
        )


def test_refresh_plan_does_not_execute_workflows() -> None:
    helper_text = (ROOT / "mozaiksai/control_plane/app_context_refresh.py").read_text(
        encoding="utf-8"
    )

    plan = build_context_refresh_plan(
        policy_result=_refresh_block_policy(_stale_brownfield_summary()),
        app_context_summary=_stale_brownfield_summary(),
    )

    assert plan.workflow_sequence == BROWNFIELD_DISCOVERY_REFRESH_SEQUENCE
    assert plan.mutation_allowed is False
    assert "run_workflow" not in helper_text
    assert "execute_workflow" not in helper_text
    assert "WorkflowManager" not in helper_text


async def test_refresh_plan_does_not_change_current_context() -> None:
    store = _MemoryBuildRecordStore()
    context_version = AppContextVersion(
        context_version_id="ctx_ops_stale",
        app_id="ops_studio",
        mode=AppContextMode.BROWNFIELD,
        source_refs=[_source_ref()],
        stale_status=AppContextStaleStatus.STALE,
        stale_reasons=["source ref changed"],
    )
    await register_app_context_version(
        context_version,
        artifact_store=store,
        make_current=True,
    )
    before = await get_current_app_context_version(
        app_id="ops_studio",
        artifact_store=store,
    )
    assert before is not None

    summary = summarize_app_context_version(before)
    build_context_refresh_plan(
        policy_result=_refresh_block_policy(summary),
        app_context_summary=summary,
    )
    after = await get_current_app_context_version(
        app_id="ops_studio",
        artifact_store=store,
    )

    assert after is not None
    assert before.context_version_id == after.context_version_id == "ctx_ops_stale"


def test_context_refresh_contract_has_no_graph_database_or_proprietary_terms() -> None:
    paths = [
        ROOT / "mozaiksai/core/app_context/refresh.py",
        ROOT / "mozaiksai/control_plane/app_context_refresh.py",
        ROOT / "tests/test_app_context_refresh_contract.py",
    ]
    forbidden_terms = (
        "Fal" + "kor",
        "app " + "zero",
        "app_" + "zero",
        "mozaiks" + "-app",
        "mozaiks" + "pay",
    )

    for path in paths:
        text = path.read_text(encoding="utf-8").lower()
        for term in forbidden_terms:
            assert term.lower() not in text


