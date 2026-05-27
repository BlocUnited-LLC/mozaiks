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
    ArtifactLifecycleStatus,
    ArtifactValidationStatus,
    ArtifactVersionDoc,
)

ROOT = Path(__file__).resolve().parents[1]


class _MemoryArtifactStore:
    def __init__(self) -> None:
        self.versions: dict[str, ArtifactVersionDoc] = {}
        self.create_calls: list[dict[str, Any]] = []
        self._counter = 0

    async def create_artifact_version(self, **kwargs: Any) -> ArtifactVersionDoc:
        self._counter += 1
        version_id = f"av_{self._counter}"
        self.create_calls.append(kwargs)
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
        if doc.lifecycle_status is ArtifactLifecycleStatus.CURRENT:
            self._supersede_current_siblings(doc)
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
        self._supersede_current_siblings(doc)
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

    def _supersede_current_siblings(self, target: ArtifactVersionDoc) -> None:
        for doc in self.versions.values():
            if (
                doc.app_id == target.app_id
                and doc.artifact_kind == target.artifact_kind
                and doc.artifact_key == target.artifact_key
                and doc.id != target.id
                and doc.lifecycle_status is ArtifactLifecycleStatus.CURRENT
            ):
                doc.lifecycle_status = ArtifactLifecycleStatus.SUPERSEDED


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
        artifact_kind: f"av_{artifact_kind}_{suffix}"
        for artifact_kind in BROWNFIELD_APP_CONTEXT_REQUIRED_ARTIFACT_KINDS
    }


def test_builds_brownfield_app_context_version_from_artifact_refs() -> None:
    context_version = build_brownfield_app_context_version(
        app_id="ops_studio",
        artifact_version_refs=_artifact_refs(),
        source_refs=[_source_ref()],
        ownership_boundaries=_ownership(),
        application_inventory=_inventory(),
        context_version_id="ctx_ops_1",
    )

    assert context_version.context_version_id == "ctx_ops_1"
    assert context_version.mode is AppContextMode.BROWNFIELD
    assert context_version.stale_status is AppContextStaleStatus.CURRENT
    assert context_version.graph_snapshot_ref == "av_app_context_graph_1"
    assert {ref.artifact_kind for ref in context_version.artifact_refs} == set(
        BROWNFIELD_APP_CONTEXT_REQUIRED_ARTIFACT_KINDS
    )
    assert context_version.surface_indexes.routes[0].location == "/orders"


def test_missing_required_artifact_refs_fail_clearly() -> None:
    refs = _artifact_refs()
    refs.pop("risk_report")

    with pytest.raises(ValueError, match="risk_report"):
        build_brownfield_app_context_version(
            app_id="ops_studio",
            artifact_version_refs=refs,
            source_refs=[_source_ref()],
            ownership_boundaries=_ownership(),
            application_inventory=_inventory(),
        )


async def test_registers_app_context_version_as_artifact_kind() -> None:
    store = _MemoryArtifactStore()
    context_version = build_brownfield_app_context_version(
        app_id="ops_studio",
        artifact_version_refs=_artifact_refs(),
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

    assert registered.artifact_version.artifact_kind == APP_CONTEXT_VERSION_ARTIFACT_KIND
    assert registered.artifact_version.artifact_key == APP_CONTEXT_VERSION_ARTIFACT_KEY
    assert registered.artifact_version.lifecycle_status is ArtifactLifecycleStatus.DRAFT
    assert store.create_calls[0]["artifact_kind"] == "app_context_version"


async def test_current_context_selection_can_be_set_and_retrieved() -> None:
    store = _MemoryArtifactStore()
    context_version = build_brownfield_app_context_version(
        app_id="ops_studio",
        artifact_version_refs=_artifact_refs(),
        source_refs=[_source_ref()],
        ownership_boundaries=_ownership(),
        application_inventory=_inventory(),
        context_version_id="ctx_ops_1",
    )
    registered = await register_app_context_version(context_version, artifact_store=store)

    assert await get_current_app_context_version(app_id="ops_studio", artifact_store=store) is None

    current_artifact = await set_current_app_context_version(
        app_id="ops_studio",
        artifact_version_id=registered.artifact_version.id,
        artifact_store=store,
    )
    current_context = await get_current_app_context_version(
        app_id="ops_studio",
        artifact_store=store,
    )

    assert current_artifact is not None
    assert current_artifact.lifecycle_status is ArtifactLifecycleStatus.CURRENT
    assert current_context is not None
    assert current_context.context_version_id == "ctx_ops_1"


async def test_new_current_context_supersedes_prior_current_context() -> None:
    store = _MemoryArtifactStore()
    first = build_brownfield_app_context_version(
        app_id="ops_studio",
        artifact_version_refs=_artifact_refs("1"),
        source_refs=[_source_ref()],
        ownership_boundaries=_ownership(),
        application_inventory=_inventory(),
        context_version_id="ctx_ops_1",
    )
    second = build_brownfield_app_context_version(
        app_id="ops_studio",
        artifact_version_refs=_artifact_refs("2"),
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

    assert first_registered.artifact_version.lifecycle_status is ArtifactLifecycleStatus.SUPERSEDED
    assert second_registered.artifact_version.lifecycle_status is ArtifactLifecycleStatus.CURRENT
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
