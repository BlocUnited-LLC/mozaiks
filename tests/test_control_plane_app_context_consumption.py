from __future__ import annotations

from pathlib import Path
from typing import Any

from mozaiksai.control_plane import dry_run
from mozaiksai.control_plane.app_context import (
    APP_CONTEXT_MISSING_WARNING,
    APP_CONTEXT_STALE_WARNING,
    AppContextSummary,
    get_current_app_context_summary,
    summarize_app_context_version,
)
from mozaiksai.core.app_context.models import (
    AppContextMode,
    AppContextStaleStatus,
    AppContextVersion,
    ApplicationInventory,
    OwnershipBoundary,
    OwnershipClass,
    SourceRef,
    SourceRefKind,
    SurfaceRef,
)
from mozaiksai.core.app_context.store import (
    build_brownfield_app_context_version,
    register_app_context_version,
    register_greenfield_app_context_version,
)
from mozaiksai.core.artifacts.models import (
    ArtifactLifecycleStatus,
    ArtifactValidationStatus,
    ArtifactVersionDoc,
)

ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = ROOT / "factory_app" / "app"


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
            build_family=kwargs.get("build_family") or kwargs.get("artifact_kind"),
            build_key=kwargs.get("build_key") or kwargs.get("artifact_key"),
            version_number=self._counter,
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

    async def get_build_record(
        self,
        *,
        app_id: str,
        build_record_id: str,
    ) -> ArtifactVersionDoc | None:
        doc = self.versions.get(build_record_id)
        if doc is None or doc.app_id != app_id:
            return None
        return doc

    async def get_artifact_version(
        self,
        *,
        app_id: str,
        artifact_version_id: str,
    ) -> ArtifactVersionDoc | None:
        return await self.get_build_record(app_id=app_id, build_record_id=artifact_version_id)

    async def accept_artifact_version(
        self,
        *,
        app_id: str,
        artifact_version_id: str,
        commit_metadata: dict[str, Any] | None = None,
    ) -> ArtifactVersionDoc | None:
        doc = await self.get_build_record(
            app_id=app_id,
            build_record_id=artifact_version_id,
        )
        if doc is None:
            return None
        for version in self.versions.values():
            if (
                version.app_id == app_id
                and version.build_family == doc.build_family
                and version.build_key == doc.build_key
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
            rows = [doc for doc in rows if doc.build_family == artifact_kind]
        if artifact_key is not None:
            rows = [doc for doc in rows if doc.build_key == artifact_key]
        if lifecycle_status is not None:
            rows = [doc for doc in rows if doc.lifecycle_status is lifecycle_status]
        return sorted(rows, key=lambda doc: doc.version_number, reverse=True)[:limit]


def _greenfield_bundle() -> ArtifactVersionDoc:
    return ArtifactVersionDoc(
        _id="av_app_bundle_1",
        app_id="greenfield_app",
        build_family="app_bundle",
        build_key="app_bundle",
        version_number=1,
        lineage_root_id="av_app_bundle_1",
        lifecycle_status=ArtifactLifecycleStatus.CURRENT,
        validation_status=ArtifactValidationStatus.PASSED,
        files_manifest=[
            {"path": "app/ui/route_manifest.json"},
            {"path": "app/ui/pages/home.yaml"},
            {"path": "app/modules/accounts/module.yaml"},
            {"path": "app/services/integrations/email_gateway_client.py"},
            {"path": "app/config/integrations/email_gateway.json"},
        ],
        commit_metadata={"metadata": {"artifact_path": "generated/apps/greenfield/app.zip"}},
    )


def _brownfield_source_ref() -> SourceRef:
    return SourceRef(
        source_ref_id="src_repo",
        kind=SourceRefKind.REPO,
        uri="https://example.invalid/enterprise-app.git",
    )


async def test_helper_returns_greenfield_context_summary_with_app_bundle_ref() -> None:
    store = _MemoryArtifactStore()
    await register_greenfield_app_context_version(
        app_bundle_artifact=_greenfield_bundle(),
        artifact_store=store,
    )

    summary = await get_current_app_context_summary(
        app_id="greenfield_app",
        artifact_store=store,
    )

    assert summary.available is True
    assert summary.mode == "greenfield"
    assert any(ref.kind == "app_bundle" and ref.ref_id == "av_app_bundle_1" for ref in summary.artifact_refs)
    assert summary.ownership.by_ownership["generated_overlay"] >= 1


async def test_helper_returns_brownfield_context_summary_with_source_refs() -> None:
    store = _MemoryArtifactStore()
    source_ref = _brownfield_source_ref()
    inventory = ApplicationInventory(
        app_id="brownfield_app",
        source_refs=[source_ref],
        routes=[SurfaceRef(surface_id="orders", kind="route", location="/orders")],
    )
    context_version = build_brownfield_app_context_version(
        app_id="brownfield_app",
        artifact_version_refs={
            "application_inventory": "av_inventory",
            "ownership_boundary": "av_ownership",
            "integration_inventory": "av_integrations",
            "risk_report": "av_risk",
            "adoption_plan": "av_plan",
            "brownfield_registration": "av_registration",
            "app_context_graph": "av_graph",
        },
        source_refs=[source_ref],
        ownership_boundaries=[
            OwnershipBoundary(
                path_or_artifact="src/orders",
                ownership=OwnershipClass.READ_ONLY_DISCOVERED,
            )
        ],
        application_inventory=inventory,
        context_version_id="ctx_brownfield",
    )
    await register_app_context_version(context_version, artifact_store=store, make_current=True)

    summary = await get_current_app_context_summary(
        app_id="brownfield_app",
        artifact_store=store,
    )

    assert summary.available is True
    assert summary.mode == "brownfield"
    assert summary.source_refs[0].kind == "repo"
    assert summary.ownership.by_ownership["read_only_discovered"] == 1


async def test_missing_context_warns_without_failure() -> None:
    summary = await get_current_app_context_summary(
        app_id="missing_app",
        artifact_store=_MemoryArtifactStore(),
    )

    assert summary.available is False
    assert APP_CONTEXT_MISSING_WARNING in summary.warnings


def test_stale_context_summary_carries_warning() -> None:
    context_version = AppContextVersion(
        context_version_id="ctx_stale",
        app_id="stale_app",
        mode=AppContextMode.GREENFIELD,
        stale_status=AppContextStaleStatus.STALE,
        stale_reasons=["source ref changed"],
    )
    summary = summarize_app_context_version(context_version)

    assert summary.available is True
    assert APP_CONTEXT_STALE_WARNING in summary.warnings


async def test_execution_plan_includes_context_summary_when_available(monkeypatch) -> None:
    async def _fake_context_summary(**_kwargs):
        return AppContextSummary(
            app_id="sample_app",
            available=True,
            context_version_id="ctx_sample",
            mode="greenfield",
            stale_status="current",
            artifact_refs=[
                {
                    "ref_id": "av_app_bundle_1",
                    "kind": "app_bundle",
                    "target": "app_bundle",
                    "lifecycle_status": "current",
                }
            ],
        )

    monkeypatch.setattr(dry_run, "get_current_app_context_summary", _fake_context_summary)

    plan = await dry_run.build_refinement_execution_plan(
        request="Add an archive_project action to the projects module API.",
        change_class="feature",
        app_root=APP_ROOT,
        app_id="sample_app",
    )

    assert plan.app_context_summary is not None
    assert plan.app_context_summary.mode == "greenfield"
    assert any(ref.kind == "app_bundle" for ref in plan.app_context_summary.artifact_refs)


async def test_missing_and_stale_context_only_add_warnings_and_do_not_reroute(monkeypatch) -> None:
    async def _missing_context(**_kwargs):
        return AppContextSummary(
            app_id="sample_app",
            available=False,
            warnings=[APP_CONTEXT_MISSING_WARNING],
        )

    async def _stale_context(**_kwargs):
        return AppContextSummary(
            app_id="sample_app",
            available=True,
            context_version_id="ctx_stale",
            mode="greenfield",
            stale_status="stale",
            warnings=[APP_CONTEXT_STALE_WARNING],
        )

    monkeypatch.setattr(dry_run, "get_current_app_context_summary", _missing_context)
    missing_plan = await dry_run.build_refinement_execution_plan(
        request="Change the dashboard experience to highlight reports first.",
        change_class="design",
        app_root=APP_ROOT,
        app_id="sample_app",
    )
    monkeypatch.setattr(dry_run, "get_current_app_context_summary", _stale_context)
    stale_plan = await dry_run.build_refinement_execution_plan(
        request="Change the dashboard experience to highlight reports first.",
        change_class="design",
        app_root=APP_ROOT,
        app_id="sample_app",
    )

    assert missing_plan.workflow_sequence == stale_plan.workflow_sequence == "app_surface_revision"
    assert missing_plan.target_workflow == stale_plan.target_workflow
    assert missing_plan.affected_bundle_paths == stale_plan.affected_bundle_paths
    assert APP_CONTEXT_MISSING_WARNING in missing_plan.warnings
    assert APP_CONTEXT_STALE_WARNING in stale_plan.warnings
    assert missing_plan.mutation_allowed is False
    assert stale_plan.mutation_allowed is False


def test_control_plane_app_context_consumption_has_no_graph_database_or_proprietary_terms() -> None:
    paths = [
        ROOT / "mozaiksai/control_plane/app_context.py",
        ROOT / "tests/test_control_plane_app_context_consumption.py",
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

