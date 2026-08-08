from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from mozaiksai.core.app_context.models import (
    AppContextMode,
    AppContextStaleStatus,
    ApplicationInventory,
    BrownfieldRegistration,
    OwnershipBoundary,
    OwnershipClass,
    SourceRef,
    SourceRefKind,
    SurfaceRef,
)
from mozaiksai.core.app_context.store import (
    APP_CONTEXT_VERSION_ARTIFACT_KEY,
    APP_CONTEXT_VERSION_ARTIFACT_KIND,
    BROWNFIELD_APP_CONTEXT_REQUIRED_ARTIFACT_KINDS,
    build_brownfield_app_context_version,
    get_current_app_context_version,
    register_app_context_version,
    set_current_app_context_version,
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
        self.create_calls: list[dict[str, Any]] = []
        self._counter = 0

    async def create_build_record(self, **kwargs: Any) -> BuildRecord:
        self._counter += 1
        record_id = f"br_{self._counter}"
        self.create_calls.append(kwargs)
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
        if doc.lifecycle_status is BuildRecordStatus.CURRENT:
            self._supersede_current_siblings(doc)
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
        self._supersede_current_siblings(doc)
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

    def _supersede_current_siblings(self, target: BuildRecord) -> None:
        for doc in self.versions.values():
            if (
                doc.app_id == target.app_id
                and doc.build_family == target.build_family
                and doc.build_key == target.build_key
                and doc.id != target.id
                and doc.lifecycle_status is BuildRecordStatus.CURRENT
            ):
                doc.lifecycle_status = BuildRecordStatus.SUPERSEDED


def _source_ref() -> SourceRef:
    return SourceRef(
        source_ref_id="src_repo",
        kind=SourceRefKind.REPO,
        uri="https://example.invalid/enterprise-app.git",
        ref="main",
        checksum="sha256:source",
    )


def _inventory() -> ApplicationInventory:
    return ApplicationInventory(
        app_id="ops_studio",
        source_refs=[_source_ref()],
        routes=[SurfaceRef(surface_id="orders", kind="route", location="/orders")],
        api_endpoints=[
            SurfaceRef(surface_id="list_orders", kind="api_endpoint", location="/api/orders")
        ],
    )


def _ownership() -> list[OwnershipBoundary]:
    return [
        OwnershipBoundary(
            path_or_artifact="src/orders",
            ownership=OwnershipClass.READ_ONLY_DISCOVERED,
        )
    ]


def _artifact_refs(suffix: str = "1") -> dict[str, str]:
    return {
        build_family: f"av_{build_family}_{suffix}"
        for build_family in BROWNFIELD_APP_CONTEXT_REQUIRED_ARTIFACT_KINDS
    }


def test_builds_brownfield_app_context_version_from_artifact_refs() -> None:
    context_version = build_brownfield_app_context_version(
        app_id="ops_studio",
        build_record_refs=_artifact_refs(),
        source_refs=[_source_ref()],
        ownership_boundaries=_ownership(),
        application_inventory=_inventory(),
        context_version_id="ctx_ops_1",
    )

    assert context_version.context_version_id == "ctx_ops_1"
    assert context_version.mode is AppContextMode.BROWNFIELD
    assert context_version.stale_status is AppContextStaleStatus.CURRENT
    assert context_version.graph_snapshot_ref == "av_app_context_graph_1"
    assert {ref.build_family for ref in context_version.artifact_refs} == set(
        BROWNFIELD_APP_CONTEXT_REQUIRED_ARTIFACT_KINDS
    )
    assert context_version.surface_indexes.routes[0].location == "/orders"


def test_missing_required_artifact_refs_fail_clearly() -> None:
    refs = _artifact_refs()
    refs.pop("risk_report")

    with pytest.raises(ValueError, match="risk_report"):
        build_brownfield_app_context_version(
            app_id="ops_studio",
            build_record_refs=refs,
            source_refs=[_source_ref()],
            ownership_boundaries=_ownership(),
            application_inventory=_inventory(),
        )


async def test_registers_app_context_version_as_ARTIFACT_KIND() -> None:
    store = _MemoryBuildRecordStore()
    context_version = build_brownfield_app_context_version(
        app_id="ops_studio",
        build_record_refs=_artifact_refs(),
        source_refs=[_source_ref()],
        ownership_boundaries=_ownership(),
        application_inventory=_inventory(),
        context_version_id="ctx_ops_1",
    )

    registered = await register_app_context_version(
        context_version,
        artifact_store=store,
        source_workflow="ExistingAppDiscovery",
        source_chat_id="chat_1",
    )

    assert registered.build_record.build_family == APP_CONTEXT_VERSION_ARTIFACT_KIND
    assert registered.build_record.build_key == APP_CONTEXT_VERSION_ARTIFACT_KEY
    assert registered.build_record.lifecycle_status is BuildRecordStatus.DRAFT
    assert store.create_calls[0]["build_family"] == "app_context_version"


async def test_current_context_selection_can_be_set_and_retrieved() -> None:
    store = _MemoryBuildRecordStore()
    context_version = build_brownfield_app_context_version(
        app_id="ops_studio",
        build_record_refs=_artifact_refs(),
        source_refs=[_source_ref()],
        ownership_boundaries=_ownership(),
        application_inventory=_inventory(),
        context_version_id="ctx_ops_1",
    )
    registered = await register_app_context_version(context_version, artifact_store=store)

    assert await get_current_app_context_version(app_id="ops_studio", artifact_store=store) is None

    current_artifact = await set_current_app_context_version(
        app_id="ops_studio",
        build_record_id=registered.build_record.id,
        artifact_store=store,
    )
    current_context = await get_current_app_context_version(
        app_id="ops_studio",
        artifact_store=store,
    )

    assert current_artifact is not None
    assert current_artifact.lifecycle_status is BuildRecordStatus.CURRENT
    assert current_context is not None
    assert current_context.context_version_id == "ctx_ops_1"


async def test_new_current_context_supersedes_prior_current_context() -> None:
    store = _MemoryBuildRecordStore()
    first = build_brownfield_app_context_version(
        app_id="ops_studio",
        build_record_refs=_artifact_refs("1"),
        source_refs=[_source_ref()],
        ownership_boundaries=_ownership(),
        application_inventory=_inventory(),
        context_version_id="ctx_ops_1",
    )
    second = build_brownfield_app_context_version(
        app_id="ops_studio",
        build_record_refs=_artifact_refs("2"),
        source_refs=[_source_ref()],
        ownership_boundaries=_ownership(),
        application_inventory=_inventory(),
        context_version_id="ctx_ops_2",
    )

    first_registered = await register_app_context_version(
        first,
        artifact_store=store,
        make_current=True,
    )
    second_registered = await register_app_context_version(
        second,
        artifact_store=store,
        make_current=True,
    )
    current_context = await get_current_app_context_version(
        app_id="ops_studio",
        artifact_store=store,
    )

    assert first_registered.build_record.lifecycle_status is BuildRecordStatus.SUPERSEDED
    assert second_registered.build_record.lifecycle_status is BuildRecordStatus.CURRENT
    assert current_context is not None
    assert current_context.context_version_id == "ctx_ops_2"


def test_brownfield_registration_references_context_version_id() -> None:
    registration = BrownfieldRegistration(
        registration_id="reg_ops",
        app_id="ops_studio",
        source_refs=[_source_ref()],
        context_version_id="ctx_ops_1",
    )

    assert registration.context_version_id == "ctx_ops_1"


def test_registration_store_has_no_graph_database_or_proprietary_dependency() -> None:
    paths = [
        ROOT / "mozaiksai/core/app_context/store.py",
        ROOT / "tests/test_app_context_registration.py",
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


def test_registration_store_does_not_canonicalize_legacy_placeholders() -> None:
    text = (ROOT / "mozaiksai/core/app_context/store.py").read_text(encoding="utf-8")

    assert "native_migration" not in text
    assert "module_decomposition_plan" not in text



