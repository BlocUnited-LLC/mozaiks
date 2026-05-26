from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from mozaiksai.control_plane import app_context_refresh_execution as refresh_execution
from mozaiksai.core.app_context.models import (
    AppContextStaleStatus,
    ApplicationInventory,
    OwnershipBoundary,
    OwnershipClass,
    SourceRef,
    SourceRefKind,
    SurfaceRef,
)
from mozaiksai.core.app_context.refresh import (
    CONTEXT_REFRESH_EXPECTED_ARTIFACTS,
    ContextRefreshPlan,
    ContextRefreshResultStatus,
)
from mozaiksai.core.app_context.store import (
    BROWNFIELD_APP_CONTEXT_REQUIRED_ARTIFACT_KINDS,
    build_brownfield_app_context_version,
    get_current_app_context_version,
    register_app_context_version,
)
from mozaiksai.core.artifacts.models import (
    ArtifactLifecycleStatus,
    ArtifactValidationStatus,
    ArtifactVersionDoc,
)

ROOT = Path(__file__).resolve().parents[1]


class _MemoryArtifactStore:
    def __init__(self) -> None:
        self.versions: dict[str, ArtifactVersionDoc] = {}
        self._counter = 0

    async def create_artifact_version(self, **kwargs: Any) -> ArtifactVersionDoc:
        self._counter += 1
        version_id = f"av_{self._counter}"
        doc = ArtifactVersionDoc(
            _id=version_id,
            app_id=kwargs["app_id"],
            artifact_kind=kwargs["artifact_kind"],
            artifact_key=kwargs["artifact_key"],
            version_number=self._counter,
            parent_version_id=kwargs.get("parent_version_id"),
            lineage_root_id=version_id,
            source_workflow=kwargs.get("source_workflow"),
            source_chat_id=kwargs.get("source_chat_id"),
            canonical_inputs_version=kwargs.get("canonical_inputs_version") or {},
            lifecycle_status=kwargs.get("lifecycle_status", ArtifactLifecycleStatus.DRAFT),
            validation_status=kwargs.get("validation_status", ArtifactValidationStatus.PENDING),
            files_manifest=kwargs.get("files_manifest") or [],
            commit_metadata=kwargs.get("commit_metadata") or {},
        )
        self.versions[doc.id] = doc
        return doc

    async def get_artifact_version(
        self,
        *,
        app_id: str,
        artifact_version_id: str,
    ) -> ArtifactVersionDoc | None:
        doc = self.versions.get(artifact_version_id)
        if doc is None or doc.app_id != app_id:
            return None
        return doc

    async def accept_artifact_version(
        self,
        *,
        app_id: str,
        artifact_version_id: str,
        commit_metadata: dict[str, Any] | None = None,
    ) -> ArtifactVersionDoc | None:
        doc = await self.get_artifact_version(
            app_id=app_id,
            artifact_version_id=artifact_version_id,
        )
        if doc is None:
            return None
        for candidate in self.versions.values():
            if (
                candidate.app_id == app_id
                and candidate.artifact_kind == doc.artifact_kind
                and candidate.artifact_key == doc.artifact_key
                and candidate.id != doc.id
                and candidate.lifecycle_status is ArtifactLifecycleStatus.CURRENT
            ):
                candidate.lifecycle_status = ArtifactLifecycleStatus.SUPERSEDED
        doc.lifecycle_status = ArtifactLifecycleStatus.CURRENT
        if commit_metadata is not None:
            doc.commit_metadata = commit_metadata
        return doc

    async def list_artifact_versions(
        self,
        *,
        app_id: str,
        artifact_kind: str | None = None,
        artifact_key: str | None = None,
        lifecycle_status: ArtifactLifecycleStatus | None = None,
        limit: int = 50,
        **_kwargs: Any,
    ) -> list[ArtifactVersionDoc]:
        rows = [doc for doc in self.versions.values() if doc.app_id == app_id]
        if artifact_kind is not None:
            rows = [doc for doc in rows if doc.artifact_kind == artifact_kind]
        if artifact_key is not None:
            rows = [doc for doc in rows if doc.artifact_key == artifact_key]
        if lifecycle_status is not None:
            rows = [doc for doc in rows if doc.lifecycle_status is lifecycle_status]
        return sorted(rows, key=lambda doc: doc.version_number, reverse=True)[:limit]


def _source_ref() -> SourceRef:
    return SourceRef(
        source_ref_id="src_repo",
        kind=SourceRefKind.REPO,
        uri="https://example.invalid/field-service.git",
        ref="main",
        checksum="sha256:source",
    )


def _inventory() -> ApplicationInventory:
    return ApplicationInventory(
        app_id="field_service",
        source_refs=[_source_ref()],
        routes=[SurfaceRef(surface_id="orders", kind="route", location="/orders")],
    )


def _ownership() -> list[OwnershipBoundary]:
    return [
        OwnershipBoundary(
            path_or_artifact="src/orders",
            ownership=OwnershipClass.READ_ONLY_DISCOVERED,
        )
    ]


def _artifact_refs(suffix: str) -> dict[str, str]:
    return {
        artifact_kind: f"av_{artifact_kind}_{suffix}"
        for artifact_kind in BROWNFIELD_APP_CONTEXT_REQUIRED_ARTIFACT_KINDS
    }


def _context_version(
    *,
    context_version_id: str,
    suffix: str,
    stale_status: AppContextStaleStatus = AppContextStaleStatus.CURRENT,
):
    return build_brownfield_app_context_version(
        app_id="field_service",
        artifact_version_refs=_artifact_refs(suffix),
        source_refs=[_source_ref()],
        ownership_boundaries=_ownership(),
        application_inventory=_inventory(),
        context_version_id=context_version_id,
        stale_status=stale_status,
    )


def _plan(previous_context_version_id: str | None = "ctx_previous") -> ContextRefreshPlan:
    return ContextRefreshPlan(
        app_id="field_service",
        current_context_version_id=previous_context_version_id,
        target_source_refs=[_source_ref()],
        expected_artifacts=list(CONTEXT_REFRESH_EXPECTED_ARTIFACTS),
    )


@pytest.mark.asyncio
async def test_new_current_context_version_resolves_stale_context() -> None:
    store = _MemoryArtifactStore()
    await register_app_context_version(
        _context_version(context_version_id="ctx_previous", suffix="1"),
        artifact_store=store,
        make_current=True,
    )
    await register_app_context_version(
        _context_version(context_version_id="ctx_new", suffix="2"),
        artifact_store=store,
        make_current=True,
    )

    result = await refresh_execution.complete_context_refresh(
        _plan("ctx_previous"),
        artifact_store=store,
        app_id="field_service",
    )

    assert result.status is ContextRefreshResultStatus.COMPLETED
    assert result.previous_context_version_id == "ctx_previous"
    assert result.new_context_version_id == "ctx_new"
    assert result.stale_resolved is True


@pytest.mark.asyncio
async def test_same_context_version_does_not_resolve_stale_context() -> None:
    store = _MemoryArtifactStore()
    await register_app_context_version(
        _context_version(context_version_id="ctx_previous", suffix="1"),
        artifact_store=store,
        make_current=True,
    )

    result = await refresh_execution.complete_context_refresh(
        _plan("ctx_previous"),
        artifact_store=store,
        app_id="field_service",
    )

    assert result.status is ContextRefreshResultStatus.NO_CHANGE
    assert result.stale_resolved is False
    assert any("did not create a new AppContextVersion" in warning for warning in result.warnings)


@pytest.mark.asyncio
async def test_missing_current_context_returns_missing_context_status() -> None:
    result = await refresh_execution.complete_context_refresh(
        _plan("ctx_previous"),
        artifact_store=_MemoryArtifactStore(),
        app_id="field_service",
    )

    assert result.status is ContextRefreshResultStatus.MISSING_CONTEXT
    assert result.new_context_version_id is None
    assert result.stale_resolved is False
    assert any("No current AppContextVersion" in warning for warning in result.warnings)


@pytest.mark.asyncio
async def test_stale_new_context_does_not_resolve_stale_context() -> None:
    store = _MemoryArtifactStore()
    await register_app_context_version(
        _context_version(context_version_id="ctx_previous", suffix="1"),
        artifact_store=store,
        make_current=True,
    )
    await register_app_context_version(
        _context_version(
            context_version_id="ctx_new",
            suffix="2",
            stale_status=AppContextStaleStatus.STALE,
        ),
        artifact_store=store,
        make_current=True,
    )

    result = await refresh_execution.complete_context_refresh(
        _plan("ctx_previous"),
        artifact_store=store,
        app_id="field_service",
    )

    assert result.status is ContextRefreshResultStatus.COMPLETED
    assert result.new_context_version_id == "ctx_new"
    assert result.stale_resolved is False
    assert any("stale_status is 'stale'" in warning for warning in result.warnings)


@pytest.mark.asyncio
async def test_result_lists_artifacts_from_app_context_version_refs() -> None:
    store = _MemoryArtifactStore()
    await register_app_context_version(
        _context_version(context_version_id="ctx_new", suffix="2"),
        artifact_store=store,
        make_current=True,
    )

    result = await refresh_execution.complete_context_refresh(
        _plan("ctx_previous"),
        artifact_store=store,
        app_id="field_service",
    )

    assert {ref.artifact_kind for ref in result.artifacts_created} == set(
        CONTEXT_REFRESH_EXPECTED_ARTIFACTS
    )


@pytest.mark.asyncio
async def test_completion_helper_does_not_run_workflows(monkeypatch) -> None:
    store = _MemoryArtifactStore()
    await register_app_context_version(
        _context_version(context_version_id="ctx_new", suffix="2"),
        artifact_store=store,
        make_current=True,
    )

    async def fail_launch(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("completion must not launch refresh")

    monkeypatch.setattr(refresh_execution, "launch_context_refresh_plan", fail_launch)
    result = await refresh_execution.complete_context_refresh(
        _plan("ctx_previous"),
        artifact_store=store,
        app_id="field_service",
    )

    assert result.status is ContextRefreshResultStatus.COMPLETED


@pytest.mark.asyncio
async def test_completion_helper_does_not_mutate_current_context() -> None:
    store = _MemoryArtifactStore()
    await register_app_context_version(
        _context_version(context_version_id="ctx_new", suffix="2"),
        artifact_store=store,
        make_current=True,
    )
    before = await get_current_app_context_version(
        app_id="field_service",
        artifact_store=store,
    )

    await refresh_execution.complete_context_refresh(
        _plan("ctx_previous"),
        artifact_store=store,
        app_id="field_service",
    )
    after = await get_current_app_context_version(
        app_id="field_service",
        artifact_store=store,
    )

    assert before is not None
    assert after is not None
    assert before.context_version_id == after.context_version_id == "ctx_new"


def test_completion_helper_does_not_call_generation_workflows() -> None:
    source = (ROOT / "mozaiksai/control_plane/app_context_refresh_execution.py").read_text(
        encoding="utf-8"
    )

    assert "AppGenerator" not in source
    assert "AgentGenerator" not in source


def test_context_refresh_result_handling_has_no_graph_database_or_proprietary_terms() -> None:
    paths = [
        ROOT / "mozaiksai/core/app_context/refresh.py",
        ROOT / "mozaiksai/control_plane/app_context_refresh_execution.py",
        ROOT / "tests/test_context_refresh_result_handling.py",
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
