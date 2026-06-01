from __future__ import annotations

from pathlib import Path
from typing import Any

from mozaiksai.core.app_context.models import AppContextMode, OwnershipClass
from mozaiksai.core.app_context.store import (
    APP_CONTEXT_VERSION_ARTIFACT_KIND,
    GREENFIELD_APP_CONTEXT_ARTIFACT_KINDS,
    build_greenfield_app_context_from_app_bundle,
    get_current_app_context_version,
    register_greenfield_app_context_version,
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
        for version in self.versions.values():
            if (
                version.app_id == app_id
                and version.artifact_kind == doc.artifact_kind
                and version.artifact_key == doc.artifact_key
                and version.id != doc.id
                and version.lifecycle_status is ArtifactLifecycleStatus.CURRENT
            ):
                version.lifecycle_status = ArtifactLifecycleStatus.SUPERSEDED
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


def _file_manifest() -> list[dict[str, Any]]:
    paths = [
        "GeneratedApp/app/app.json",
        "GeneratedApp/app/ui/route_manifest.json",
        "GeneratedApp/app/ui/pages/home.yaml",
        "GeneratedApp/app/ui/pages/work_orders.yaml",
        "GeneratedApp/app/ui/components/WorkOrderTable.jsx",
        "GeneratedApp/app/modules/work_orders/module.yaml",
        "GeneratedApp/workflows/WorkOrderSummary/orchestrator.yaml",
        "GeneratedApp/workflows/WorkOrderSummary/agents.yaml",
        "GeneratedApp/app/services/integrations/email_gateway_client.py",
        "GeneratedApp/app/config/integrations/email_gateway.json",
        "GeneratedApp/app/config/data.json",
        "GeneratedApp/app/config/data_migrations/add_work_orders.json",
        "GeneratedApp/app/config/shell.json",
        "GeneratedApp/app/brand/theme_config.json",
    ]
    return [
        {
            "path": path,
            "sha256": f"sha256:{index}",
            "size_bytes": 10 + index,
            "content_type": "text/plain",
        }
        for index, path in enumerate(paths, start=1)
    ]


def _app_bundle_artifact() -> ArtifactVersionDoc:
    return ArtifactVersionDoc(
        _id="av_app_bundle_1",
        app_id="field_service",
        artifact_kind="app_bundle",
        artifact_key="app_bundle",
        version_number=1,
        lineage_root_id="av_app_bundle_1",
        lifecycle_status=ArtifactLifecycleStatus.CURRENT,
        validation_status=ArtifactValidationStatus.PASSED,
        files_manifest=_file_manifest(),
        commit_metadata={
            "metadata": {
                "artifact_path": "generated/apps/field_service/build_1/app.zip",
                "bundle_name": "GeneratedApp",
            }
        },
    )


def test_greenfield_app_bundle_manifest_maps_to_inventory() -> None:
    drafts = build_greenfield_app_context_from_app_bundle(
        app_bundle_artifact=_app_bundle_artifact()
    )

    inventory = drafts.application_inventory
    assert inventory.app_id == "field_service"
    assert {page.location for page in inventory.pages} >= {
        "ui/pages/home.yaml",
        "ui/pages/work_orders.yaml",
    }
    assert any(route.location == "ui/route_manifest.json" for route in inventory.routes)
    assert inventory.modules[0].label == "work_orders"
    assert inventory.workflows[0].label == "WorkOrderSummary"
    assert inventory.components[0].location == "ui/components/WorkOrderTable.jsx"
    assert inventory.data_entities


def test_greenfield_integrations_are_secret_free_and_config_aware() -> None:
    drafts = build_greenfield_app_context_from_app_bundle(
        app_bundle_artifact=_app_bundle_artifact()
    )

    integration = drafts.integration_inventory[0]
    assert integration.integration_id == "email_gateway"
    assert integration.provider_type == "generated_integration"
    assert integration.secret_required is False
    assert integration.frontend_safe_config == {}
    assert integration.readiness_status.value == "ready"


def test_greenfield_ownership_defaults_generated_files_to_generated_overlay() -> None:
    drafts = build_greenfield_app_context_from_app_bundle(
        app_bundle_artifact=_app_bundle_artifact()
    )

    file_boundaries = [
        boundary
        for boundary in drafts.ownership_boundaries
        if not boundary.path_or_artifact.startswith("integration:")
    ]
    assert file_boundaries
    assert {boundary.ownership for boundary in file_boundaries} == {
        OwnershipClass.GENERATED_OVERLAY
    }
    assert OwnershipClass.READ_ONLY_DISCOVERED not in {
        boundary.ownership for boundary in file_boundaries
    }
    assert any(
        boundary.ownership is OwnershipClass.EXTERNAL_SYSTEM
        and boundary.path_or_artifact == "integration:email_gateway"
        for boundary in drafts.ownership_boundaries
    )


def test_greenfield_app_context_graph_is_deterministic_and_source_ref_backed() -> None:
    first = build_greenfield_app_context_from_app_bundle(app_bundle_artifact=_app_bundle_artifact())
    second = build_greenfield_app_context_from_app_bundle(app_bundle_artifact=_app_bundle_artifact())

    assert first.app_context_graph.graph_hash == second.app_context_graph.graph_hash
    node_types = {node.node_type.value for node in first.app_context_graph.nodes}
    assert {"app", "file", "module", "route", "integration", "generated_overlay"} <= node_types
    assert all(node.source_ref_id for node in first.app_context_graph.nodes)
    assert all(edge.source_ref_id for edge in first.app_context_graph.edges)
    assert all(edge.artifact_version_id == "av_app_bundle_1" for edge in first.app_context_graph.edges)


async def test_register_greenfield_app_context_version_persists_and_sets_current() -> None:
    store = _MemoryArtifactStore()
    app_bundle = _app_bundle_artifact()

    registered = await register_greenfield_app_context_version(
        app_bundle_artifact=app_bundle,
        artifact_store=store,
        source_workflow="AppGenerator",
        source_chat_id="chat_greenfield_1",
    )
    current = await get_current_app_context_version(
        app_id="field_service",
        artifact_store=store,
    )

    persisted_kinds = {call["artifact_kind"] for call in store.create_calls}
    assert persisted_kinds == {
        *GREENFIELD_APP_CONTEXT_ARTIFACT_KINDS,
        APP_CONTEXT_VERSION_ARTIFACT_KIND,
    }
    assert registered.context_version.mode is AppContextMode.GREENFIELD
    assert registered.artifact_version.lifecycle_status is ArtifactLifecycleStatus.CURRENT
    assert current is not None
    assert current.mode is AppContextMode.GREENFIELD
    assert any(ref.artifact_kind == "app_bundle" for ref in current.artifact_refs)
    assert any(
        ref.artifact_kind == "app_bundle" and ref.artifact_version_id == "av_app_bundle_1"
        for ref in current.artifact_refs
    )


def test_greenfield_registration_has_no_graph_database_or_proprietary_dependency() -> None:
    paths = [
        ROOT / "mozaiksai/core/app_context/store.py",
        ROOT / "tests/test_greenfield_app_context_registration.py",
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
