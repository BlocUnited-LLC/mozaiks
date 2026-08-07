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
        "GeneratedApp/app/data/contract.json",
        "GeneratedApp/app/data/migrations/add_work_orders.json",
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


def _app_bundle_artifact() -> BuildRecord:
    return BuildRecord(
        _id="av_app_bundle_1",
        app_id="field_service",
        build_family="app_bundle",
        build_key="app_bundle",
        version_number=1,
        lineage_root_id="av_app_bundle_1",
        lifecycle_status=BuildRecordStatus.CURRENT,
        validation_status=BuildRecordValidationStatus.PASSED,
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
    store = _MemoryBuildRecordStore()
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

    persisted_kinds = {call["build_family"] for call in store.create_calls}
    assert persisted_kinds == {
        *GREENFIELD_APP_CONTEXT_ARTIFACT_KINDS,
        APP_CONTEXT_VERSION_ARTIFACT_KIND,
    }
    assert registered.context_version.mode is AppContextMode.GREENFIELD
    assert registered.artifact_version.lifecycle_status is BuildRecordStatus.CURRENT
    assert current is not None
    assert current.mode is AppContextMode.GREENFIELD
    assert any(ref.artifact_kind == "app_bundle" for ref in current.artifact_refs)
    assert any(
        ref.artifact_kind == "app_bundle" and ref.artifact_version_id == "av_app_bundle_1"
        for ref in current.artifact_refs
    )


async def test_register_greenfield_app_context_version_indexes_source_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "GeneratedApp"
    (workspace / "app" / "ui" / "pages").mkdir(parents=True)
    (workspace / "app" / "ui" / "pages" / "home.yaml").write_text("title: Home\n", encoding="utf-8")
    (workspace / "app" / "ui" / "components").mkdir(parents=True)
    (workspace / "app" / "ui" / "components" / "Home.jsx").write_text(
        "export default function Home() { return <main>Home</main>; }\n",
        encoding="utf-8",
    )
    (workspace / "app" / "app.json").write_text('{"app_id": "field_service"}\n', encoding="utf-8")
    app_bundle = BuildRecord(
        _id="av_app_bundle_1",
        app_id="field_service",
        build_family="app_bundle",
        build_key="app_bundle",
        version_number=1,
        lineage_root_id="av_app_bundle_1",
        lifecycle_status=BuildRecordStatus.CURRENT,
        validation_status=BuildRecordValidationStatus.PASSED,
        files_manifest=_file_manifest(),
        commit_metadata={
            "metadata": {
                "workspace_dir": str(workspace),
                "bundle_name": "GeneratedApp",
            }
        },
    )
    store = _MemoryBuildRecordStore()

    registered = await register_greenfield_app_context_version(
        app_bundle_artifact=app_bundle,
        artifact_store=store,
        source_workflow="AppGenerator",
        source_chat_id="chat_greenfield_1",
    )

    persisted_kinds = [call["build_family"] for call in store.create_calls]
    assert "source_context_bundle" in persisted_kinds
    assert registered.context_version.graph_snapshot_ref
    assert any(ref.artifact_kind == "source_context_bundle" for ref in registered.context_version.artifact_refs)
    source_payload = next(
        doc.commit_metadata.metadata["summary_payload"]
        for doc in store.versions.values()
        if doc.build_family == "source_context_bundle"
    )
    assert source_payload["file_contents"]["ui/components/Home.jsx"]
    assert source_payload["schema_version"] == "mozaiks.source_context.bundle.v1"


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

