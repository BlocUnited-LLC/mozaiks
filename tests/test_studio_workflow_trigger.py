from __future__ import annotations

import asyncio
import hashlib
import json
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from mozaiksai.control_plane import CodingWorkerResult, ControlPlaneConfig, ScopeProposal
from mozaiksai.core.artifacts import (
    ArtifactCommitMetadata,
    ArtifactLifecycleStatus,
    ArtifactValidationStatus,
    ArtifactVersionDoc,
    ChangeClassification,
    ChangeIntentDoc,
    ChangeRequestDoc,
    ImpactSetDoc,
    RefinementRequestPayload,
    RefinementSessionDoc,
    RefinementSessionStatus,
)
from mozaiksai.core.session.build_binding import RunBuildBinding

_BINDING = RunBuildBinding(
    build_registry_id="appreg_1", target_app_id="app_1", build_id="build_1", phase="refinement",
)


class _BaselineStore:
    versions = {}

    async def get_build_record(self, *, app_id, build_record_id):
        return self.versions.get(build_record_id) if app_id == "app_1" else None

    async def get_change_request(self, *, app_id, change_request_id):
        if app_id != "app_1" or change_request_id not in {"cr_core_1", "cr_scope_1"}:
            return None
        return SimpleNamespace(id=change_request_id, created_by_user_id="demo-user")


@pytest.fixture(autouse=True)
def _owned_build_target(monkeypatch, tmp_path):
    from mozaiksai.core.session.router import SessionRouter
    from mozaiksai.hosts import studio
    from tests.test_session_router import _FakePersistence

    record = {
        "build_registry_id": "appreg_1", "app_id": "app_1", "chat_app_id": "factory",
        "lifecycle_state": "review",
        "current_build_run": {"build_id": "build_1", "artifact_version_id": "av_child_1"},
    }
    service = SimpleNamespace(
        get_app_record=AsyncMock(return_value={"app": record}),
        begin_refinement_run=AsyncMock(return_value=_BINDING),
        update_build_status=AsyncMock(return_value={"success": True, "app": record}),
        promote_build=AsyncMock(return_value={"success": True, "app": record}),
    )
    monkeypatch.setattr(studio, "_get_app_registry_service", lambda: service)
    monkeypatch.setattr(studio, "_resolve_studio_scope", lambda *args, **kwargs: ("factory", "demo-user"))
    router = SessionRouter(persistence=_FakePersistence())
    monkeypatch.setattr(studio, "get_session_router", lambda: router)
    monkeypatch.setenv("MOZAIKS_WORKSPACES_PATH", str(tmp_path / "workspaces"))
    archive = tmp_path / "baseline.zip"
    _make_bundle_zip(archive, {
        "app/ui/pages/Dashboard.jsx": "export default function Dashboard() {}",
        "app/ui/components/ExportPanel.jsx": 'export function ExportPanel() { return "old"; }',
        "app/modules/product/backend/handler.py": "# product handler",
        "Dockerfile": "FROM scratch",
    })
    versions = {
        key: _artifact_version(artifact_version_id=key, zip_path=archive)
        for key in ("av_123", "av_456", "av_789", "av_scope_1", "av_core_1", "av_review_1")
    }
    versions["av_review_1"].commit_metadata.metadata["workspace_dir"] = str(tmp_path / "staged")
    monkeypatch.setattr(_BaselineStore, "versions", versions)
    monkeypatch.setattr(studio, "get_artifact_store", lambda: _BaselineStore())
    return service


def _make_bundle_zip(zip_path: Path, files: dict[str, str]) -> None:
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for relative_path, content in files.items():
            archive.writestr(relative_path, content)


def _artifact_version(
    *,
    artifact_version_id: str,
    zip_path: Path,
    build_family: str = "app_bundle",
    build_key: str = "app_bundle",
    version_number: int = 1,
    parent_version_id: str | None = None,
    lifecycle_status: ArtifactLifecycleStatus = ArtifactLifecycleStatus.DRAFT,
    validation_status: ArtifactValidationStatus = ArtifactValidationStatus.PASSED,
    files_manifest: list[dict[str, object]] | None = None,
) -> ArtifactVersionDoc:
    resolved_manifest = files_manifest
    if resolved_manifest is None:
        resolved_manifest = [{
            "path": f"{zip_path.stem}/{zip_path.stem}.zip",
            "size_bytes": zip_path.stat().st_size,
            "sha256": hashlib.sha256(zip_path.read_bytes()).hexdigest(),
            "content_type": "application/zip",
        }]
    return ArtifactVersionDoc.model_validate(
        {
            "_id": artifact_version_id,
            "app_id": "app_1",
            "build_family": build_family,
            "build_key": build_key,
            "version_number": version_number,
            "parent_version_id": parent_version_id,
            "lineage_root_id": parent_version_id or artifact_version_id,
            "source_workflow": "AppGenerator",
            "source_chat_id": "chat_1",
            "canonical_inputs_version": {},
            "lifecycle_status": lifecycle_status.value,
            "validation_status": validation_status.value,
            "app_validation_status": validation_status.value,
            "files_manifest": resolved_manifest,
            "commit_metadata": ArtifactCommitMetadata(
                message="Generated artifact",
                source_workflow="AppGenerator",
                source_chat_id="chat_1",
                metadata={"artifact_path": str(zip_path), "bundle_name": zip_path.stem, **_BINDING.model_dump()},
            ).model_dump(mode="python"),
        }
    )


def _change_request_doc(*, artifact_version_id: str) -> ChangeRequestDoc:
    return ChangeRequestDoc.model_validate(
        {
            "_id": "cr_review_1",
            "app_id": "app_1",
            "build_family": "app_bundle",
            "build_key": "app_bundle",
            "build_record_id": artifact_version_id,
            "raw_user_request": "Update the dashboard title and export controls.",
            "classification": ChangeClassification.PATCH.value,
            "refinement_request": RefinementRequestPayload(
                build_family="app_bundle",
                build_key="app_bundle",
                build_record_id=artifact_version_id,
                raw_user_request="Update the dashboard title and export controls.",
                source_surface="app_build",
            ).model_dump(mode="python"),
            "change_intent": ChangeIntentDoc(
                change_class=ChangeClassification.PATCH,
                source="llm",
                signals=["dashboard_copy"],
                rationale="A local dashboard patch is sufficient.",
                confidence=0.92,
                touches_app_bundle=True,
            ).model_dump(mode="python"),
            "impact_set": ImpactSetDoc(
                affected_workflows=["AppGenerator"],
                affected_bundle_paths=["src/App.jsx"],
                affected_declarative_families=["app_bundle"],
                requires_replanning=False,
                requires_rebuild=True,
                restart_from="AppGenerator",
                scope_summary="Update the dashboard bundle output.",
            ).model_dump(mode="python"),
            "router_decision": {"execution_mode": "coding_worker"},
        }
    )


def _refinement_session_doc(
    *,
    artifact_version_id: str,
    result_artifact_version_id: str,
    status: RefinementSessionStatus = RefinementSessionStatus.VALIDATED,
) -> RefinementSessionDoc:
    return RefinementSessionDoc.model_validate(
        {
            "_id": "rs_review_1",
            "app_id": "app_1",
            "artifact_version_id": artifact_version_id,
            "result_artifact_version_id": result_artifact_version_id,
            "change_request_id": "cr_review_1",
            "provider": "control_plane_coding",
            "status": status.value,
            "metadata": {
                "coding_worker": {
                    "plan": {"summary": "Patch the dashboard title and export area."},
                    "validation_result": {"validation_status": "passed", "preview_url": None},
                    "metadata": {"selected_file_paths": ["src/App.jsx"]},
                }
            },
        }
    )


@pytest.mark.parametrize("source_chat_id", [None, "review-original"])
def test_studio_trigger_endpoint_accepts_refinement_trigger_payload(monkeypatch, _owned_build_target, source_chat_id):
    from mozaiksai.core.auth import reset_auth_adapter

    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")
    reset_auth_adapter()
    from mozaiksai.hosts import studio as studio_app

    source_binding = AsyncMock(return_value=_BINDING.model_dump())
    _owned_build_target.repo = SimpleNamespace(get_owned_chat_binding=source_binding)
    captured_prepare: dict = {}
    persisted_changes: list[dict] = []
    persisted_invalidations: list[dict] = []
    updated_router_decisions: list[dict] = []

    async def fake_prepare_routed_workflow_launch(**kwargs):
        captured_prepare.update(kwargs)
        return SimpleNamespace(
            workflow_id="AppGenerator",
            routing_decision=SimpleNamespace(
                requested_workflow_id=None,
                explanation="refinement reroute",
                is_full_restart=False,
                rerouted_by_dependency=False,
            ),
        )

    async def fake_launch_prepared_workflow(launch):  # noqa: ANN001
        return SimpleNamespace(
            chat_id="chat_refine_1",
            workflow_id=launch.workflow_id,
            requested_workflow_id="AppGenerator",
            journey_id="journey_refine_1",
            websocket_url="/ws/AppGenerator/app_1/chat_refine_1/demo-user",
            trigger_source="refinement",
            routing_explanation=launch.routing_decision.explanation,
            rerouted_by_dependency=False,
        )

    class _ArtifactStore(_BaselineStore):
        async def create_change_request(self, **kwargs):
            persisted_changes.append(kwargs)
            return SimpleNamespace(id="cr_123")

        async def invalidate_artifact_version_refs(self, **kwargs):
            persisted_invalidations.append(kwargs)
            return ["av_123"]

        async def update_change_request_router_decision(self, **kwargs):
            updated_router_decisions.append(kwargs)
            return True

    monkeypatch.setattr(studio_app, "prepare_routed_workflow_launch", fake_prepare_routed_workflow_launch)
    monkeypatch.setattr(studio_app, "launch_prepared_workflow", fake_launch_prepared_workflow)
    monkeypatch.setattr(studio_app, "get_artifact_store", lambda: _ArtifactStore())
    monkeypatch.setattr(
        studio_app.get_orchestration_control_harness()._refinement_resolver,
        "_classifier",
        SimpleNamespace(
            classify=_async_classifier(
                change_class="feature",
                rationale="Adding an export action extends the existing app bundle.",
                confidence=0.88,
                signals=["new_capability", "app_extension"],
            )
        ),
    )
    monkeypatch.setattr(
        studio_app.get_orchestration_control_harness(),
        "contract_surface_enabled",
        lambda: False,
    )
    no_inline = AsyncMock(side_effect=AssertionError("Explicit workflow action must bypass inline planning"))
    monkeypatch.setattr(studio_app.get_orchestration_control_harness(), "prepare_coding_request", no_inline)
    monkeypatch.setattr(studio_app.get_orchestration_control_harness(), "prepare_contract_surface_request", no_inline)

    client = TestClient(studio_app.app)
    response = client.post(
        "/api/workflows/trigger",
        json={
            "build_registry_id": "appreg_1",
            "trigger_source": "refinement",
            **({"source_chat_id": source_chat_id} if source_chat_id else {}),
            "trigger_payload": {
                "refinement_request": {
                    "artifact_kind": "app_bundle",
                    "artifact_key": "app_bundle",
                    "artifact_version_id": "av_123",
                    "raw_user_request": "Add an export action",
                    "source_surface": "app_build",
                },
            },
            "context_variables": {"screen": "studio-create"},
        },
    )

    assert response.status_code == 200
    no_inline.assert_not_awaited()
    assert "coding_request" not in captured_prepare["trigger_payload"]
    assert captured_prepare["source_chat_id"] == source_chat_id
    if source_chat_id:
        source_binding.assert_awaited_once_with(
            app_id="factory", owner_user_id="demo-user", chat_id=source_chat_id,
        )
    else:
        source_binding.assert_not_awaited()
    assert response.json() == {
        "execution_mode": "workflow",
        "chat_id": "chat_refine_1",
        "workflow_id": "AppGenerator",
        "requested_workflow_id": "AppGenerator",
        "journey_id": "journey_refine_1",
        "websocket_url": "/ws/AppGenerator/app_1/chat_refine_1/demo-user",
        "trigger_source": "refinement",
        "routing_explanation": "refinement reroute",
        "rerouted_by_dependency": False,
        "harness_decision": {
            "decision_type": "workflow_reentry",
            "message": "Recommended route: AppGenerator.",
            "rationale": "Adding an export action extends the existing app bundle.",
            "confidence": 0.88,
            "recommended_workflow_id": "AppGenerator",
            "selected_paths": [],
            "clarification_question": None,
            "requires_confirmation": False,
            "actions": [
                {
                    "action_id": "run_recommended_workflow",
                    "label": "Continue in AppGenerator",
                    "action_type": "run_workflow",
                    "workflow_id": "AppGenerator",
                    "metadata": {},
                }
            ],
            "metadata": {
                "change_class": "feature",
                "workflow_sequence": "app_revision",
                    "scope_summary": "Extend the existing app bundle within the approved concept using AppGenerator.",
            },
        },
    }
    assert captured_prepare["workflow_id"] is None
    assert captured_prepare["journey_id"] == "app_revision"
    assert captured_prepare["trigger_source"] == "refinement"
    assert captured_prepare["context_variables"] == {"screen": "studio-create"}
    assert captured_prepare["trigger_payload"] == {
        "change_request_id": "cr_123",
        "refinement_request": {
            "request_kind": "refinement",
            "declared_change_class": None,
            "build_family": "app_bundle",
            "build_key": "app_bundle",
            "build_record_id": "av_123",
            "raw_user_request": "Add an export action",
            "source_surface": "app_build",
            "app_id": captured_prepare["app_id"],
            "target_app_id": "app_1",
            "user_id": captured_prepare["user_id"],
            "requested_workflow_id": None,
            "extra": {
                "files_manifest": [entry.model_dump(mode="python") for entry in _BaselineStore.versions["av_123"].files_manifest],
            },
        },
    }
    assert "change_class" not in captured_prepare
    assert "artifact_kind" not in captured_prepare
    assert "artifact_version_id" not in captured_prepare
    assert "raw_user_request" not in captured_prepare
    assert captured_prepare["extra_trigger_meta"] == {
        "action_id": None,
        "change_class": "feature",
        "build_record_id": "av_123",
        "build_family": "app_bundle",
        "workflow_sequence": "app_revision",
    }
    assert persisted_changes == [
        {
            "app_id": "app_1",
            "build_family": "app_bundle",
            "build_key": "app_bundle",
            "build_record_id": "av_123",
            "raw_user_request": "Add an export action",
            "classification": studio_app.ChangeClassification.FEATURE,
            "refinement_request": {
                "request_kind": "refinement",
                "declared_change_class": None,
                "build_family": "app_bundle",
                "build_key": "app_bundle",
                "build_record_id": "av_123",
                "raw_user_request": "Add an export action",
                "source_surface": "app_build",
                "app_id": captured_prepare["app_id"],
                "target_app_id": "app_1",
                "user_id": "demo-user",
                "requested_workflow_id": None,
                "extra": {
                    "files_manifest": [entry.model_dump(mode="python") for entry in _BaselineStore.versions["av_123"].files_manifest],
                    },
            },
            "change_intent": {
                "change_class": "feature",
                "source": "llm",
                "signals": ["new_capability", "app_extension"],
                "rationale": "Adding an export action extends the existing app bundle.",
                "confidence": 0.88,
                "requires_concept_revision": False,
                "touches_app_bundle": True,
                "touches_workflow_bundle": False,
                "touches_design_docs": False,
                "touches_concept": False,
            },
            "impact_set": {
                "workflow_sequence": "app_revision",
                "affected_workflows": ["AppGenerator"],
                "affected_bundle_paths": [
                    "modules/*/module.yaml",
                    "modules/*/contracts/*.yaml",
                    "modules/*/backend/*.py",
                ],
                "affected_declarative_families": ["app_bundle"],
                "requires_replanning": True,
                "requires_rebuild": True,
                "restart_from": "AppGenerator",
                "scope_summary": "Extend the existing app bundle within the approved concept using AppGenerator.",
            },
            "router_decision": {
                "workflow_id": "AppGenerator",
                "workflow_sequence": "app_revision",
                "requested_workflow_id": None,
                "explanation": "Re-entering AppGenerator to extend the app bundle within the current concept.",
                "is_full_restart": False,
                "rerouted_by_dependency": False,
                "execution_mode": "workflow",
                "harness_decision": {
                    "decision_type": "workflow_reentry",
                    "message": "Recommended route: AppGenerator.",
                    "rationale": "Adding an export action extends the existing app bundle.",
                    "confidence": 0.88,
                    "recommended_workflow_id": "AppGenerator",
                    "selected_paths": [],
                    "clarification_question": None,
                    "requires_confirmation": False,
                    "actions": [
                        {
                            "action_id": "run_recommended_workflow",
                            "label": "Continue in AppGenerator",
                            "action_type": "run_workflow",
                            "workflow_id": "AppGenerator",
                            "metadata": {},
                        }
                    ],
                    "metadata": {
                        "change_class": "feature",
                        "workflow_sequence": "app_revision",
                        "scope_summary": "Extend the existing app bundle within the approved concept using AppGenerator.",
                    },
                },
            },
            "created_by_user_id": "demo-user",
        }
    ]
    assert persisted_invalidations == [
        {
            "app_id": "app_1",
            "artifact_version_refs": {"app_bundle": "av_123"},
            "affected_artifact_kinds": ["app_bundle"],
            "reason": "change_request:cr_123",
        }
    ]
    assert updated_router_decisions == [
        {
            "app_id": "app_1",
            "change_request_id": "cr_123",
            "router_decision": {
                "workflow_id": "AppGenerator",
                "workflow_sequence": "app_revision",
                "requested_workflow_id": None,
                "explanation": "refinement reroute",
                "is_full_restart": False,
                "rerouted_by_dependency": False,
                "execution_mode": "workflow",
                "harness_decision": {
                    "decision_type": "workflow_reentry",
                    "message": "Recommended route: AppGenerator.",
                    "rationale": "Adding an export action extends the existing app bundle.",
                    "confidence": 0.88,
                    "recommended_workflow_id": "AppGenerator",
                    "selected_paths": [],
                    "clarification_question": None,
                    "requires_confirmation": False,
                    "actions": [
                        {
                            "action_id": "run_recommended_workflow",
                            "label": "Continue in AppGenerator",
                            "action_type": "run_workflow",
                            "workflow_id": "AppGenerator",
                            "metadata": {},
                        }
                    ],
                    "metadata": {
                        "change_class": "feature",
                        "workflow_sequence": "app_revision",
                        "scope_summary": "Extend the existing app bundle within the approved concept using AppGenerator.",
                    },
                },
            },
        }
    ]


def test_studio_trigger_endpoint_rejects_removed_top_level_refinement_fields(monkeypatch):
    from mozaiksai.core.auth import reset_auth_adapter

    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")
    reset_auth_adapter()
    from mozaiksai.hosts import studio as studio_app

    client = TestClient(studio_app.app)
    response = client.post(
        "/api/workflows/trigger",
        json={
            "build_registry_id": "appreg_1",
            "trigger_source": "refinement",
            "change_class": "patch",
            "artifact_kind": "app_bundle",
            "artifact_version_id": "av_123",
            "raw_user_request": "Add an export action",
        },
    )

    assert response.status_code == 400
    assert "refinement triggers require trigger_payload.refinement_request" in response.json()["detail"]


def test_studio_trigger_endpoint_rejects_refinement_when_control_plane_disabled(monkeypatch):
    from mozaiksai.core.auth import reset_auth_adapter

    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")
    reset_auth_adapter()
    from mozaiksai.hosts import studio as studio_app

    monkeypatch.setattr(
        studio_app.get_orchestration_control_harness(),
        "_config_loader",
        lambda: ControlPlaneConfig(enabled=False),
    )

    client = TestClient(studio_app.app)
    response = client.post(
        "/api/workflows/trigger",
        json={
            "build_registry_id": "appreg_1",
            "trigger_source": "refinement",
            "trigger_payload": {
                "refinement_request": {
                    "artifact_kind": "app_bundle",
                    "artifact_key": "app_bundle",
                    "artifact_version_id": "av_123",
                    "raw_user_request": "Add an export action",
                    "source_surface": "app_build",
                },
            },
        },
    )

    assert response.status_code == 503
    # Internal error details are suppressed; generic message returned to callers
    detail = response.json()["detail"]
    assert detail == "Refinement classification unavailable"
    assert "Control-plane harness" not in detail


@pytest.mark.parametrize("continuation_case", ["none", "ready", "unavailable"])
def test_studio_trigger_endpoint_can_short_circuit_to_coding_worker(monkeypatch, _owned_build_target, continuation_case):
    from mozaiksai.core.auth import reset_auth_adapter

    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")
    reset_auth_adapter()
    from mozaiksai.hosts import studio as studio_app

    continuation = None
    continuation_error = None
    if continuation_case != "none":
        _owned_build_target.repo = SimpleNamespace(get_owned_chat_binding=AsyncMock(return_value=_BINDING.model_dump()))
        continuation = {"chat_id": "review-child", "workflow_id": "AppReview", "artifact_version_id": "av_child_code_1"}
        reopen = AsyncMock(return_value=continuation)
        if continuation_case == "unavailable":
            reopen.side_effect = ValueError("superseded")
            continuation = None
            continuation_error = (
                "The edit finished, but its review chat could not be reopened. "
                "Open the app's saved builds to review the result."
            )
        monkeypatch.setattr(studio_app, "continue_inline_app_review", reopen)

    persisted_changes: list[dict] = []
    persisted_sessions: list[dict] = []

    async def fail_prepare(**kwargs):  # noqa: ANN003
        raise AssertionError("workflow launch should not run for coding worker execution")

    async def fail_launch(launch):  # noqa: ANN001
        raise AssertionError("workflow launch should not run for coding worker execution")

    class _ArtifactStore(_BaselineStore):
        async def create_change_request(self, **kwargs):
            persisted_changes.append(kwargs)
            return SimpleNamespace(id="cr_code_1")

        async def invalidate_artifact_version_refs(self, **kwargs):
            return [kwargs["artifact_version_refs"]["app_bundle"]]

        async def create_refinement_session(self, **kwargs):
            persisted_sessions.append(kwargs)
            return SimpleNamespace(id="rs_code_1")

    monkeypatch.setattr(studio_app, "prepare_routed_workflow_launch", fail_prepare)
    monkeypatch.setattr(studio_app, "launch_prepared_workflow", fail_launch)
    monkeypatch.setattr(studio_app, "get_artifact_store", lambda: _ArtifactStore())
    monkeypatch.setattr(
        studio_app.get_orchestration_control_harness(),
        "_config_loader",
        lambda: ControlPlaneConfig(
            enabled=True,
            llm_profiles={
                "classifier": {"llm_config": {"model": "test-classifier"}},
                "codegen": {"llm_config": {"model": "gpt-5.2-codex", "temperature": 0.1}},
            },
            classifier={"enabled": True, "llm_profile": "classifier"},
            coding={"enabled": True, "llm_profile": "codegen"},
        ),
    )
    monkeypatch.setattr(
        studio_app.get_orchestration_control_harness()._refinement_resolver,
        "_classifier",
        SimpleNamespace(
            classify=_async_classifier(
                change_class="patch",
                rationale="This is a narrow patch.",
                confidence=0.95,
                signals=["bug_fix"],
            )
        ),
    )
    monkeypatch.setattr(
        studio_app.get_orchestration_control_harness()._coding_worker,
        "execute",
        _async_coding_worker(
            {
                "eligible": True,
                "execution_mode": "coding_worker",
                "status": "validated",
                "provider": "control_plane_coding",
                "plan": {
                    "summary": "Patch the dashboard component.",
                    "owned_paths": ["app/ui/pages/Dashboard.jsx"],
                    "updated_files": [
                        {
                            "path": "app/ui/pages/Dashboard.jsx",
                            "content": "export default function Dashboard() {}",
                        }
                    ],
                    "validation_strategy": "skip",
                    "validation_commands": [],
                    "start_preview": False,
                    "needs_human_review": False,
                    "rationale": "Single-file UI fix.",
                },
                "validation_result": {"validation_status": "skipped", "preview_url": None},
                "blocked_reason": None,
                "error": None,
                "metadata": {"build_record_id": "av_child_code_1"},
            }
        ),
    )

    client = TestClient(studio_app.app)
    response = client.post(
        "/api/workflows/trigger",
        json={
            "build_registry_id": "appreg_1",
            "trigger_source": "refinement",
            **({"source_chat_id": "review-parent"} if continuation_case != "none" else {}),
            "trigger_payload": {
                "refinement_request": {
                    "artifact_kind": "app_bundle",
                    "artifact_key": "app_bundle",
                    "artifact_version_id": "av_456",
                    "raw_user_request": "Fix the dashboard spacing",
                    "source_surface": "app_build",
                },
                "coding_request": {
                    "files": {"app/ui/pages/Dashboard.jsx": "export default function Dashboard() {}"},
                    "validation_strategy": "skip",
                },
            },
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "execution_mode": "coding_worker",
        "chat_id": None,
        "workflow_id": "AppGenerator",
        "requested_workflow_id": "AppGenerator",
        "websocket_url": None,
        "trigger_source": "refinement",
        "routing_explanation": "Re-entering AppGenerator to apply a scoped patch to the app bundle.",
        "rerouted_by_dependency": False,
        "refinement_session_id": "rs_code_1",
        "review_continuation": continuation,
        "review_continuation_error": continuation_error,
            "harness_decision": {
                "decision_type": "auto_patch",
                "message": "Scoped patch staged for review.",
                "rationale": "This is a narrow patch.",
                "confidence": 0.95,
                "recommended_workflow_id": "AppGenerator",
                "selected_paths": [
                    "app/ui/pages/Dashboard.jsx",
                ],
                "clarification_question": None,
                "requires_confirmation": False,
                "actions": [
                    {
                        "action_id": "review_patch",
                        "label": "Review patch",
                        "action_type": "review_patch",
                        "workflow_id": "AppGenerator",
                        "metadata": {},
                    }
                ],
                "metadata": {
                    "scope_origin": "staged",
                    "coding_status": "validated",
                },
            },
            "coding_worker": {
                "eligible": True,
                "execution_mode": "coding_worker",
                "status": "validated",
                "provider": "control_plane_coding",
                "plan": {
                    "summary": "Patch the dashboard component.",
                    "owned_paths": ["app/ui/pages/Dashboard.jsx"],
                    "updated_files": [
                        {
                            "path": "app/ui/pages/Dashboard.jsx",
                            "content": "export default function Dashboard() {}",
                        }
                    ],
                    "validation_strategy": "skip",
                    "validation_commands": [],
                    "start_preview": False,
                    "needs_human_review": False,
                    "rationale": "Single-file UI fix.",
                },
                "applied_files": {},
                "validation_result": {"validation_status": "skipped", "preview_url": None},
                "blocked_reason": None,
                "error": None,
                "metadata": {"build_record_id": "av_child_code_1"},
            },
    }
    assert persisted_changes[0]["router_decision"]["execution_mode"] == "coding_worker"
    if continuation_case != "none":
        reopen.assert_awaited_once()
        assert reopen.await_args.kwargs["source_chat_id"] == "review-parent"
        assert reopen.await_args.kwargs["baseline_id"] == "av_456"
        assert reopen.await_args.kwargs["binding"] == _BINDING
    assert persisted_sessions == [
        {
            "app_id": persisted_changes[0]["app_id"],
            "build_record_id": "av_456",
            "change_request_id": "cr_code_1",
            "result_build_record_id": "av_child_code_1",
            "provider": "control_plane_coding",
            "status": studio_app.RefinementSessionStatus.VALIDATED,
            "preview_url": None,
            "metadata": {
                "coding_worker": response.json()["coding_worker"],
                "workflow_id": "AppGenerator",
            },
        }
    ]


def test_studio_trigger_endpoint_can_auto_scope_before_coding_worker(monkeypatch):
    from mozaiksai.core.auth import reset_auth_adapter

    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")
    reset_auth_adapter()
    from mozaiksai.hosts import studio as studio_app

    persisted_changes: list[dict] = []

    async def fail_prepare(**kwargs):  # noqa: ANN003
        raise AssertionError("workflow launch should not run for coding worker execution")

    async def fail_launch(launch):  # noqa: ANN001
        raise AssertionError("workflow launch should not run for coding worker execution")

    class _ArtifactStore(_BaselineStore):
        async def create_change_request(self, **kwargs):
            persisted_changes.append(kwargs)
            return SimpleNamespace(id="cr_code_auto_1")

        async def invalidate_artifact_version_refs(self, **kwargs):
            return [kwargs["artifact_version_refs"]["app_bundle"]]

        async def create_refinement_session(self, **kwargs):
            return SimpleNamespace(id="rs_code_auto_1")

    async def _fake_propose(**kwargs):  # noqa: ANN003
        return ScopeProposal.model_validate(
            {
                "resolution": "scoped_files",
                "selected_paths": ["app/ui/pages/Dashboard.jsx"],
                "rationale": "Dashboard is the only affected surface.",
                "confidence": 0.91,
                "clarification_question": None,
                "signals": ["dashboard_surface"],
            }
        )

    async def _fake_materialize(**kwargs):  # noqa: ANN003
        return {"app/ui/pages/Dashboard.jsx": "export default function Dashboard() {}"}

    async def _fake_execute(request):  # noqa: ANN001
        assert request.files == {"app/ui/pages/Dashboard.jsx": "export default function Dashboard() {}"}
        assert request.usage_context.app_id == "factory"
        assert request.usage_context.user_id == "demo-user"
        assert request.usage_context.chat_id is None
        assert request.run_build_binding == _BINDING
        assert "usage_context" not in request.context_seed["refinement_request"]
        assert request.metadata["scope_proposal"]["selected_paths"] == ["app/ui/pages/Dashboard.jsx"]
        child = _BaselineStore.versions[request.build_record_id].model_copy(deep=True, update={"id": "av_child_multi_1"})
        child.commit_metadata.metadata.update(request.run_build_binding.model_dump())
        _BaselineStore.versions[child.id] = child
        return CodingWorkerResult.model_validate(
            {
                "eligible": True,
                "execution_mode": "coding_worker",
                "status": "validated",
                "provider": "control_plane_coding",
                "plan": {
                    "summary": "Patch the dashboard component.",
                    "owned_paths": ["app/ui/pages/Dashboard.jsx"],
                    "updated_files": [
                        {
                            "path": "app/ui/pages/Dashboard.jsx",
                            "content": 'export default function Dashboard() { return "patched"; }',
                        }
                    ],
                    "validation_strategy": "skip",
                    "validation_commands": [],
                    "start_preview": False,
                    "needs_human_review": False,
                    "rationale": "Single-file UI fix.",
                },
                "applied_files": {
                    "app/ui/pages/Dashboard.jsx": 'export default function Dashboard() { return "patched"; }'
                },
                "validation_result": {"validation_status": "skipped", "preview_url": None},
                "blocked_reason": None,
                "error": None,
                "metadata": {"build_record_id": "av_child_multi_1"},
            }
        )

    monkeypatch.setattr(studio_app, "prepare_routed_workflow_launch", fail_prepare)
    monkeypatch.setattr(studio_app, "launch_prepared_workflow", fail_launch)
    monkeypatch.setattr(studio_app, "get_artifact_store", lambda: _ArtifactStore())
    monkeypatch.setattr(
        studio_app.get_orchestration_control_harness(),
        "_config_loader",
        lambda: ControlPlaneConfig(
            enabled=True,
            llm_profiles={
                "classifier": {"llm_config": {"model": "test-classifier"}},
                "codegen": {"llm_config": {"model": "gpt-5.2-codex", "temperature": 0.1}},
            },
            classifier={"enabled": True, "llm_profile": "classifier"},
            coding={"enabled": True, "llm_profile": "codegen"},
        ),
    )
    monkeypatch.setattr(
        studio_app.get_orchestration_control_harness()._refinement_resolver,
        "_classifier",
        SimpleNamespace(
            classify=_async_classifier(
                change_class="patch",
                rationale="This is a narrow patch.",
                confidence=0.95,
                signals=["bug_fix"],
            )
        ),
    )
    monkeypatch.setattr(studio_app.get_orchestration_control_harness()._scope_proposer, "propose", _fake_propose)
    monkeypatch.setattr(
        studio_app.get_orchestration_control_harness()._scope_proposer,
        "materialize_files",
        _fake_materialize,
    )
    monkeypatch.setattr(
        studio_app.get_orchestration_control_harness()._coding_worker,
        "execute",
        _fake_execute,
    )

    client = TestClient(studio_app.app)
    response = client.post(
        "/api/workflows/trigger",
        json={
            "build_registry_id": "appreg_1",
            "trigger_source": "refinement",
            "trigger_payload": {
                "refinement_request": {
                    "artifact_kind": "app_bundle",
                    "artifact_key": "app_bundle",
                    "artifact_version_id": "av_789",
                    "raw_user_request": "Fix the dashboard spacing",
                    "source_surface": "app_build",
                },
                "coding_request": {
                    "validation_strategy": "skip",
                },
            },
        },
    )

    assert response.status_code == 200
    assert response.json()["execution_mode"] == "coding_worker"
    assert persisted_changes[0]["router_decision"]["execution_mode"] == "coding_worker"


@pytest.mark.parametrize("confirmation", ["exact", "restore", "race", "changed_request", "changed_artifact", "changed_paths", "malformed"])
def test_studio_trigger_endpoint_can_confirm_proposed_multi_file_scope(monkeypatch, confirmation):
    from mozaiksai.core.auth import reset_auth_adapter

    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")
    reset_auth_adapter()
    from mozaiksai.hosts import studio as studio_app

    persisted_changes: list[dict] = []
    proposal_count = 0
    execution_count = 0

    async def fail_prepare(**kwargs):  # noqa: ANN003
        raise AssertionError("workflow launch should not run for coding worker execution")

    async def fail_launch(launch):  # noqa: ANN001
        raise AssertionError("workflow launch should not run for coding worker execution")

    class _ArtifactStore(_BaselineStore):
        async def create_change_request(self, **kwargs):
            persisted_changes.append(kwargs)
            return SimpleNamespace(id=f"cr_scope_{len(persisted_changes)}")

        async def invalidate_artifact_version_refs(self, **kwargs):
            return [kwargs["artifact_version_refs"]["app_bundle"]]

        async def create_refinement_session(self, **kwargs):
            return SimpleNamespace(id="rs_scope_1")

    async def _fake_propose(**kwargs):  # noqa: ANN003
        nonlocal proposal_count
        proposal_count += 1
        assert proposal_count == 1, "Approval must not authorize a freshly proposed scope"
        return ScopeProposal.model_validate(
            {
                "resolution": "scoped_files",
                "selected_paths": [
                    "app/ui/pages/Dashboard.jsx",
                    "app/ui/components/ExportPanel.jsx",
                ],
                "rationale": "Both the dashboard page and export panel need the copy update.",
                "confidence": 0.91,
                "clarification_question": None,
                "signals": ["multi_file_scope"],
            }
        )

    async def _fake_materialize(**kwargs):  # noqa: ANN003
        return {
            "app/ui/pages/Dashboard.jsx": 'export default function Dashboard() { return "old"; }',
            "app/ui/components/ExportPanel.jsx": 'export function ExportPanel() { return "old"; }',
        }

    async def _fake_execute(request):  # noqa: ANN001
        nonlocal execution_count
        execution_count += 1
        assert sorted(request.files.keys()) == [
            "app/ui/components/ExportPanel.jsx",
            "app/ui/pages/Dashboard.jsx",
        ]
        assert request.files["app/ui/pages/Dashboard.jsx"] == "export default function Dashboard() {}"
        child = _BaselineStore.versions[request.build_record_id].model_copy(deep=True, update={"id": "av_child_multi_1"})
        child.commit_metadata.metadata.update(request.run_build_binding.model_dump())
        _BaselineStore.versions[child.id] = child
        return CodingWorkerResult.model_validate(
            {
                "eligible": True,
                "execution_mode": "coding_worker",
                "status": "validated",
                "provider": "control_plane_coding",
                "plan": {
                    "summary": "Patch the dashboard title and export panel copy.",
                    "owned_paths": [
                        "app/ui/pages/Dashboard.jsx",
                        "app/ui/components/ExportPanel.jsx",
                    ],
                    "updated_files": [
                        {
                            "path": "app/ui/pages/Dashboard.jsx",
                            "content": 'export default function Dashboard() { return "patched"; }',
                        },
                        {
                            "path": "app/ui/components/ExportPanel.jsx",
                            "content": 'export function ExportPanel() { return "patched"; }',
                        },
                    ],
                    "validation_strategy": "skip",
                    "validation_commands": [],
                    "start_preview": False,
                    "needs_human_review": False,
                    "rationale": "Two-file UI patch.",
                },
                "applied_files": {
                    "app/ui/pages/Dashboard.jsx": 'export default function Dashboard() { return "patched"; }',
                    "app/ui/components/ExportPanel.jsx": 'export function ExportPanel() { return "patched"; }',
                },
                "validation_result": {"validation_status": "skipped", "preview_url": None},
                "blocked_reason": None,
                "error": None,
                "metadata": {"build_record_id": "av_child_multi_1"},
            }
        )

    monkeypatch.setattr(studio_app, "prepare_routed_workflow_launch", fail_prepare)
    monkeypatch.setattr(studio_app, "launch_prepared_workflow", fail_launch)
    monkeypatch.setattr(studio_app, "get_artifact_store", lambda: _ArtifactStore())
    monkeypatch.setattr(
        studio_app.get_orchestration_control_harness(),
        "_config_loader",
        lambda: ControlPlaneConfig(
            enabled=True,
            llm_profiles={
                "classifier": {"llm_config": {"model": "test-classifier"}},
                "codegen": {"llm_config": {"model": "gpt-5.2-codex", "temperature": 0.1}},
            },
            classifier={"enabled": True, "llm_profile": "classifier"},
            coding={"enabled": True, "llm_profile": "codegen"},
        ),
    )
    monkeypatch.setattr(
        studio_app.get_orchestration_control_harness()._refinement_resolver,
        "_classifier",
        SimpleNamespace(
            classify=_async_classifier(
                change_class="patch",
                rationale="This is still a patch, but it spans two nearby files.",
                confidence=0.95,
                signals=["multi_file_scope"],
            )
        ),
    )
    monkeypatch.setattr(studio_app.get_orchestration_control_harness()._scope_proposer, "propose", _fake_propose)
    monkeypatch.setattr(
        studio_app.get_orchestration_control_harness()._scope_proposer,
        "materialize_files",
        _fake_materialize,
    )
    monkeypatch.setattr(
        studio_app.get_orchestration_control_harness()._coding_worker,
        "execute",
        _fake_execute,
    )

    client = TestClient(studio_app.app)
    first = client.post(
        "/api/workflows/trigger",
        json={
            "build_registry_id": "appreg_1",
            "trigger_source": "refinement",
            "trigger_payload": {
                "refinement_request": {
                    "artifact_kind": "app_bundle",
                    "artifact_key": "app_bundle",
                    "artifact_version_id": "av_scope_1",
                    "raw_user_request": "Update the dashboard and export panel copy",
                    "source_surface": "app_build",
                },
                "coding_request": {
                    "validation_strategy": "skip",
                },
            },
        },
    )

    assert first.status_code == 200
    first_body = first.json()
    assert first_body["execution_mode"] == "harness_decision"
    assert first_body["harness_decision"]["decision_type"] == "clarify_scope"
    assert first_body["harness_decision"]["actions"][0]["action_id"] == "apply_proposed_scope"
    assert first_body["change_request_id"] == "cr_scope_1"
    assert first_body["revision_id"]
    confirmed_files = dict.fromkeys(first_body["harness_decision"]["selected_paths"], "untrusted browser content")
    if confirmation == "changed_paths":
        confirmed_files = {"Dockerfile": "FROM untrusted"}

    confirmation_body = {
            "build_registry_id": "appreg_1",
            "trigger_source": "refinement",
            "trigger_payload": {
                "refinement_request": {
                    "artifact_kind": "app_bundle",
                    "artifact_key": "app_bundle",
                    "artifact_version_id": "av_123" if confirmation == "changed_artifact" else "av_scope_1",
                    "raw_user_request": "Different change" if confirmation == "changed_request" else "Update the dashboard and export panel copy",
                    "source_surface": "app_build",
                },
                "change_request_id": first_body["change_request_id"],
                "revision_id": first_body["revision_id"],
                "coding_request": "invalid" if confirmation == "malformed" else {
                    "validation_strategy": "skip",
                    **({"files": confirmed_files} if confirmation != "restore" else {}),
                },
                "harness_action": {
                    "action_id": "apply_proposed_scope",
                },
            },
        }
    if confirmation == "race":
        import httpx

        from mozaiksai.core.session.router import SessionRouter

        original_resolve = SessionRouter.resolve_pending_harness_decision

        async def race_confirmations():
            both_ready = asyncio.Event()
            arrivals = 0

            async def delayed_resolve(self, **kwargs):
                nonlocal arrivals
                arrivals += 1
                if arrivals == 2:
                    both_ready.set()
                await asyncio.wait_for(both_ready.wait(), timeout=5)
                return await original_resolve(self, **kwargs)

            monkeypatch.setattr(SessionRouter, "resolve_pending_harness_decision", delayed_resolve)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=studio_app.app), base_url="http://testserver") as parallel:
                return await asyncio.gather(*(
                    parallel.post("/api/workflows/trigger", json=confirmation_body) for _ in range(2)
                ))

        responses = asyncio.run(race_confirmations())
        assert sorted(response.status_code for response in responses) == [200, 409]
        assert execution_count == 1
        assert proposal_count == 1
        return
    second = client.post("/api/workflows/trigger", json=confirmation_body)

    assert proposal_count == 1
    if confirmation not in {"exact", "restore"}:
        assert second.status_code == (400 if confirmation == "malformed" else 409), second.text
        assert len(persisted_changes) == 1
        return
    assert second.status_code == 200, second.text
    second_body = second.json()
    assert second_body["execution_mode"] == "coding_worker"
    assert second_body["coding_worker"]["metadata"]["build_record_id"] == "av_child_multi_1"
    assert execution_count == 1
    replay = client.post("/api/workflows/trigger", json=confirmation_body)
    assert replay.status_code == 409, replay.text
    assert execution_count == 1
    snapshot = asyncio.run(studio_app.get_session_router().for_target("app_1").get_session_snapshot(
        app_id="factory", user_id="demo-user",
    ))
    assert snapshot["pending_harness_decision"] is None
    assert snapshot["lifecycle_state"] == "active"


@pytest.mark.parametrize("change_class", ["design", "feature", "core"])
def test_selected_file_scope_rejects_broader_classification_when_surface_refinement_is_unavailable(monkeypatch, _owned_build_target, change_class):
    from mozaiksai.core.auth import reset_auth_adapter
    from mozaiksai.hosts import studio

    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")
    reset_auth_adapter()
    harness = studio.get_orchestration_control_harness()
    monkeypatch.setattr(harness, "_config_loader", lambda: ControlPlaneConfig(
        enabled=True,
        llm_profiles={"codegen": {"llm_config": {"model": "test-model"}}},
        classifier={"enabled": True, "llm_profile": "codegen"},
        coding={"enabled": True, "llm_profile": "codegen"},
        contract_surface={"enabled": False},
    ))
    classifier = AsyncMock(side_effect=_async_classifier(
        change_class=change_class, rationale="Requires broader contracts", confidence=0.95, signals=[],
    ))
    monkeypatch.setattr(harness._refinement_resolver, "_classifier", SimpleNamespace(classify=classifier))
    planner = AsyncMock(side_effect=AssertionError("Must not plan beyond the selected file"))
    coder = AsyncMock(side_effect=AssertionError("Must not call coding for an ineligible class"))
    launcher = AsyncMock(side_effect=AssertionError("Must not launch a wider workflow"))
    monkeypatch.setattr(harness, "prepare_contract_surface_request", planner)
    monkeypatch.setattr(harness._coding_worker, "execute", coder)
    monkeypatch.setattr(studio, "prepare_routed_workflow_launch", launcher)
    response = TestClient(studio.app).post("/api/workflows/trigger", json={
        "build_registry_id": "appreg_1", "trigger_source": "refinement",
        "trigger_payload": {
            "refinement_request": {
                "artifact_kind": "app_bundle", "artifact_key": "app_bundle", "artifact_version_id": "av_123",
                "raw_user_request": "Polish this page", "source_surface": "app_workbench",
            },
            "coding_request": {"files": {"app/ui/pages/Dashboard.jsx": "browser content"}},
        },
    })
    assert response.status_code == 409, response.text
    assert "selected files" in response.json()["detail"]
    classifier.assert_awaited_once()
    planner.assert_not_awaited()
    coder.assert_not_awaited()
    launcher.assert_not_awaited()
    _owned_build_target.begin_refinement_run.assert_not_awaited()


@pytest.mark.parametrize("action", ["apply_proposed_scope", "run_recommended_workflow", "confirm_recommended_workflow"])
def test_scope_confirmation_requires_pending_decision(monkeypatch, _owned_build_target, action):
    from mozaiksai.core.auth import reset_auth_adapter
    from mozaiksai.hosts import studio

    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")
    reset_auth_adapter()
    classify = AsyncMock(side_effect=AssertionError("Unbound confirmation must fail before classification"))
    monkeypatch.setattr(studio.get_orchestration_control_harness(), "route_refinement_request", classify)
    response = TestClient(studio.app).post("/api/workflows/trigger", json={
        "build_registry_id": "appreg_1", "trigger_source": "refinement",
        "trigger_payload": {
            "refinement_request": {
                "artifact_kind": "app_bundle", "artifact_key": "app_bundle", "artifact_version_id": "av_123",
                "raw_user_request": "Edit this page", "source_surface": "app_workbench",
            },
            "harness_action": {"action_id": action},
            "coding_request": {"files": {"app/ui/pages/Dashboard.jsx": "browser content"}},
        },
    })
    assert response.status_code == 409, response.text
    assert "scope is no longer current" in response.json()["detail"]
    classify.assert_not_awaited()
    _owned_build_target.begin_refinement_run.assert_not_awaited()


@pytest.mark.parametrize("extra_action", [None, "confirm_recommended_workflow", "run_recommended_workflow", "apply_proposed_scope"])
def test_studio_trigger_endpoint_returns_core_harness_decision_before_launch(monkeypatch, extra_action):
    from mozaiksai.core.auth import reset_auth_adapter

    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")
    reset_auth_adapter()
    from mozaiksai.hosts import studio as studio_app

    async def fail_prepare(**kwargs):  # noqa: ANN003
        raise AssertionError("workflow launch should not run before a core confirmation")

    async def fail_launch(launch):  # noqa: ANN001
        raise AssertionError("workflow launch should not run before a core confirmation")

    class _ArtifactStore(_BaselineStore):
        async def create_change_request(self, **kwargs):
            return SimpleNamespace(id="cr_core_1")

        async def invalidate_artifact_version_refs(self, **kwargs):
            return [kwargs["artifact_version_refs"]["app_bundle"]]

    monkeypatch.setattr(studio_app, "prepare_routed_workflow_launch", fail_prepare)
    monkeypatch.setattr(studio_app, "launch_prepared_workflow", fail_launch)
    monkeypatch.setattr(studio_app, "get_artifact_store", lambda: _ArtifactStore())
    monkeypatch.setattr(
        studio_app.get_orchestration_control_harness()._refinement_resolver,
        "_classifier",
        SimpleNamespace(
            classify=_async_classifier(
                change_class="core",
                rationale="Adding blockchain changes the product direction.",
                confidence=0.94,
                signals=["concept_shift", "new_capability"],
            )
        ),
    )

    client = TestClient(studio_app.app)
    response = client.post(
        "/api/workflows/trigger",
        json={
            "build_registry_id": "appreg_1",
            "trigger_source": "refinement",
            "trigger_payload": {
                "refinement_request": {
                    "artifact_kind": "app_bundle",
                    "artifact_key": "app_bundle",
                    "artifact_version_id": "av_core_1",
                    "raw_user_request": "Add blockchain support to the product.",
                    "source_surface": "app_build",
                    "extra": {"harness_action": {"action_id": extra_action}} if extra_action else {},
                },
            },
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "execution_mode": "harness_decision",
        "build_registry_id": "appreg_1",
        "change_request_id": "cr_core_1",
        "revision_id": response.json()["revision_id"],
        "chat_id": None,
        "workflow_id": "ValueEngine",
        "requested_workflow_id": None,
        "websocket_url": None,
        "trigger_source": "refinement",
        "routing_explanation": "Core concept change detected for app bundle; restarting from ValueEngine.",
        "rerouted_by_dependency": False,
        "harness_decision": {
            "decision_type": "core_restart",
            "message": "This looks like a concept-level change. Recommended route: ValueEngine.",
            "rationale": "Adding blockchain changes the product direction.",
            "confidence": 0.94,
            "recommended_workflow_id": "ValueEngine",
            "selected_paths": [],
            "clarification_question": None,
            "requires_confirmation": True,
            "actions": [
                {
                    "action_id": "confirm_recommended_workflow",
                    "label": "Run ValueEngine",
                    "action_type": "confirm_workflow",
                    "workflow_id": "ValueEngine",
                    "metadata": {},
                }
            ],
            "metadata": {
                "change_class": "core",
                "workflow_sequence": "full_rebuild",
                "build_registry_id": "appreg_1",
                "source_chat_id": None,
                    "scope_summary": "Restart from ValueEngine and invalidate downstream outputs that depend on the app bundle.",
            },
        },
    }
    snapshot = asyncio.run(studio_app.get_session_router().for_target("app_1").get_session_snapshot(
        app_id="factory", user_id="demo-user",
    ))
    pending = snapshot["pending_harness_decision"]
    assert pending["metadata"]["build_registry_id"] == "appreg_1"
    assert pending["metadata"]["source_chat_id"] is None
    assert pending["requested_workflow_id"] is None
    assert "harness_action" not in pending["trigger_payload"]["refinement_request"]["extra"]


@pytest.mark.parametrize("continuation", ["run_recommended_workflow", "confirm_recommended_workflow"])
@pytest.mark.parametrize("mutation", [None, "request", "artifact", "revision", "action", "context", "route", "source", "source_missing"])
def test_studio_trigger_endpoint_reuses_prelaunch_revision_intent_on_confirm(monkeypatch, _owned_build_target, mutation, continuation):
    from mozaiksai.core.auth import reset_auth_adapter

    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")
    reset_auth_adapter()
    from mozaiksai.hosts import studio as studio_app

    _owned_build_target.repo = SimpleNamespace(get_owned_chat_binding=AsyncMock(return_value=_BINDING.model_dump()))

    expected_workflow = "ValueEngine" if continuation == "confirm_recommended_workflow" else "AppGenerator"
    expected_sequence = "full_rebuild" if continuation == "confirm_recommended_workflow" else "app_revision"
    create_calls: list[dict] = []
    captured_prepare: dict = {}

    async def fake_prepare_routed_workflow_launch(**kwargs):
        captured_prepare.update(kwargs)
        return SimpleNamespace(
            workflow_id=expected_workflow,
            routing_decision=SimpleNamespace(
                requested_workflow_id=expected_workflow,
                explanation="Core concept change detected for app bundle; restarting from ValueEngine.",
                is_full_restart=True,
                rerouted_by_dependency=False,
            ),
            journey_id=None,
        )

    async def fake_launch_prepared_workflow(launch):  # noqa: ANN001
        return SimpleNamespace(
            chat_id="chat_value_1",
            workflow_id=launch.workflow_id,
            requested_workflow_id=expected_workflow,
            journey_id=None,
            websocket_url="/ws/ValueEngine/app_1/chat_value_1/demo-user",
            trigger_source="refinement",
            routing_explanation=launch.routing_decision.explanation,
            rerouted_by_dependency=False,
        )

    class _ArtifactStore(_BaselineStore):
        async def create_change_request(self, **kwargs):
            create_calls.append(kwargs)
            return SimpleNamespace(id="cr_core_1")

        async def invalidate_artifact_version_refs(self, **kwargs):
            return [kwargs["artifact_version_refs"]["app_bundle"]]

        async def update_change_request_router_decision(self, **kwargs):
            return True

    monkeypatch.setattr(studio_app, "prepare_routed_workflow_launch", fake_prepare_routed_workflow_launch)
    monkeypatch.setattr(studio_app, "launch_prepared_workflow", fake_launch_prepared_workflow)
    monkeypatch.setattr(studio_app, "get_artifact_store", lambda: _ArtifactStore())
    classifier = AsyncMock(side_effect=_async_classifier(
        change_class="core" if continuation == "confirm_recommended_workflow" else "patch", rationale="Adding blockchain changes the product direction.",
        confidence=0.94, signals=["concept_shift", "new_capability"],
    ))
    harness = studio_app.get_orchestration_control_harness()
    monkeypatch.setattr(harness._refinement_resolver, "_classifier", SimpleNamespace(classify=classifier))
    no_inline = AsyncMock(side_effect=AssertionError("Workflow continuation must bypass inline planning"))
    monkeypatch.setattr(harness._scope_proposer, "propose", AsyncMock(return_value=ScopeProposal(
        resolution="workflow", selected_paths=[], rationale="A workflow is needed.", confidence=0.95,
        clarification_question=None, signals=[],
    )))

    client = TestClient(studio_app.app)
    first = client.post(
        "/api/workflows/trigger",
        json={
            "build_registry_id": "appreg_1",
            "trigger_source": "refinement",
            "source_chat_id": "review-original",
            "trigger_payload": {
                "refinement_request": {
                    "artifact_kind": "app_bundle",
                    "artifact_key": "app_bundle",
                    "artifact_version_id": "av_core_1",
                    "raw_user_request": "Add blockchain support to the product.",
                    "source_surface": "app_build",
                },
                "coding_request": {},
            },
        },
    )

    assert first.status_code == 200
    assert first.json()["execution_mode"] == "harness_decision"
    assert first.json()["harness_decision"]["metadata"]["source_chat_id"] == "review-original"
    assert create_calls and len(create_calls) == 1
    target_router = studio_app.get_session_router().for_target("app_1")
    persisted_state = asyncio.run(target_router.get_session_snapshot(app_id="factory", user_id="demo-user"))
    captured_pending_harness_decision = persisted_state["pending_harness_decision"]
    assert persisted_state["active_change_request_id"] == "cr_core_1"
    assert persisted_state["active_revision_id"]
    assert captured_pending_harness_decision["trigger_payload"]["refinement_request"]["build_record_id"] == "av_core_1"
    assert captured_pending_harness_decision["metadata"]["source_chat_id"] == "review-original"

    monkeypatch.setattr(harness, "prepare_coding_request", no_inline)
    monkeypatch.setattr(harness, "prepare_contract_surface_request", no_inline)
    confirmed = json.loads(first.request.content)
    trigger = confirmed["trigger_payload"]
    trigger.update(
        change_request_id=first.json()["change_request_id"], revision_id=first.json()["revision_id"],
        harness_action={"action_id": continuation}, coding_request={},
    )
    if mutation == "request":
        trigger["refinement_request"]["raw_user_request"] = "A different product direction"
    elif mutation == "artifact":
        trigger["refinement_request"]["artifact_version_id"] = "av_123"
    elif mutation == "revision":
        trigger["revision_id"] = "stale-revision"
    elif mutation == "action":
        trigger["harness_action"]["action_id"] = "apply_proposed_scope"
    elif mutation == "context":
        confirmed["context_variables"] = {"extra_scope": "different request"}
    elif mutation == "route":
        classifier.side_effect = _async_classifier(
            change_class="feature" if continuation == "confirm_recommended_workflow" else "core", rationale="Different route", confidence=0.95, signals=[],
        )
    elif mutation == "source":
        confirmed["source_chat_id"] = "another-owned-review"
    elif mutation == "source_missing":
        confirmed.pop("source_chat_id")
    second = client.post("/api/workflows/trigger", json=confirmed)
    no_inline.assert_not_awaited()
    if mutation is not None:
        assert second.status_code == 409, second.text
        assert classifier.await_count == (2 if mutation == "route" else 1)
        assert not captured_prepare
        return
    assert second.status_code == 200, second.text
    assert second.json()["execution_mode"] == "workflow"
    assert len(create_calls) == 1
    assert classifier.await_count == 2
    assert "coding_request" not in captured_prepare["trigger_payload"]
    assert captured_prepare["journey_id"] == expected_sequence
    assert captured_prepare["trigger_payload"]["change_request_id"] == "cr_core_1"
    assert captured_prepare["trigger_payload"]["revision_id"] == persisted_state["active_revision_id"]
    replay = client.post("/api/workflows/trigger", json=confirmed)
    assert replay.status_code == 409, replay.text
    assert classifier.await_count == 2


def test_app_review_revision_trigger_preserves_staged_bundle_context(monkeypatch):
    from mozaiksai.core.auth import reset_auth_adapter

    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")
    reset_auth_adapter()
    from mozaiksai.hosts import studio as studio_app

    staged_bundle_path = _BaselineStore.versions["av_review_1"].commit_metadata.metadata["workspace_dir"]
    captured_pending: dict = {}
    create_calls: list[dict] = []

    async def fail_prepare(**kwargs):  # noqa: ANN003
        raise AssertionError("workflow launch should not run before AppReview core confirmation")

    async def fail_launch(launch):  # noqa: ANN001
        raise AssertionError("workflow launch should not run before AppReview core confirmation")

    async def fake_persist_revision_intent(*, trigger, decision, pending_harness_decision=None):  # noqa: ANN001
        captured_pending.update(
            {
                "trigger_source": trigger.trigger_source,
                "decision_context_seed": dict(decision.context_seed or {}),
                "trigger_payload": dict(trigger.trigger_payload or {}),
                "pending_trigger_payload": (
                    dict(pending_harness_decision.trigger_payload or {})
                    if pending_harness_decision is not None
                    else {}
                ),
            }
        )
        return {
            "session_id": f"session_router::{trigger.app_id}::{trigger.user_id}",
            "app_id": trigger.app_id,
            "user_id": trigger.user_id,
            "active_change_request_id": "cr_app_review_1",
            "active_revision_id": "rev_app_review_1",
        }

    class _ArtifactStore(_BaselineStore):
        async def create_change_request(self, **kwargs):
            create_calls.append(kwargs)
            return SimpleNamespace(id="cr_app_review_1")

        async def invalidate_artifact_version_refs(self, **kwargs):
            return [kwargs["artifact_version_refs"]["app_bundle"]]

    router_double = SimpleNamespace(persist_revision_intent=fake_persist_revision_intent)
    router_double.for_target = lambda target: router_double
    monkeypatch.setattr(studio_app, "get_session_router", lambda: router_double)
    monkeypatch.setattr(studio_app, "prepare_routed_workflow_launch", fail_prepare)
    monkeypatch.setattr(studio_app, "launch_prepared_workflow", fail_launch)
    monkeypatch.setattr(studio_app, "get_artifact_store", lambda: _ArtifactStore())
    monkeypatch.setattr(
        studio_app.get_orchestration_control_harness()._refinement_resolver,
        "_classifier",
        SimpleNamespace(
            classify=_async_classifier(
                change_class="core",
                rationale="Changing the product from CRM to marketplace changes the concept.",
                confidence=0.95,
                signals=["concept_shift"],
            )
        ),
    )

    client = TestClient(studio_app.app)
    response = client.post(
        "/api/workflows/trigger",
        json={
            "build_registry_id": "appreg_1",
            "trigger_source": "refinement",
            "app_id": "app_1",
            "user_id": "demo-user",
            "trigger_payload": {
                "refinement_request": {
                    "artifact_kind": "app_bundle",
                    "artifact_key": "app_bundle",
                    "artifact_version_id": "av_review_1",
                    "raw_user_request": "Turn this into a marketplace instead of a CRM.",
                    "source_surface": "app_review",
                    "extra": {
                        "lifecycle_state": "review",
                        "bundle_path": "/caller/cannot/select/this",
                        "build_registry_id": "appreg_review_1",
                        "build_id": "build_review_1",
                        "app_validation_status": "skipped",
                        "app_validation_strategy_used": "skip",
                        "integration_tests_passed": True,
                    },
                },
            },
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["execution_mode"] == "harness_decision"
    assert body["workflow_id"] == "ValueEngine"
    assert body["harness_decision"]["decision_type"] == "core_restart"
    assert create_calls[0]["build_record_id"] == "av_review_1"
    assert create_calls[0]["refinement_request"]["source_surface"] == "app_review"
    assert create_calls[0]["refinement_request"]["extra"]["bundle_path"] == staged_bundle_path

    seed = captured_pending["decision_context_seed"]
    assert seed["build_record_id"] == "av_review_1"
    assert seed["artifact_root"] == staged_bundle_path
    assert seed["lifecycle_state"] == "review"
    assert seed["refinement_request_meta"]["source_surface"] == "app_review"
    assert "build_registry_id" not in seed["refinement_request_meta"]["extra"]

    pending_request = captured_pending["pending_trigger_payload"]["refinement_request"]
    assert pending_request["build_record_id"] == "av_review_1"
    assert pending_request["source_surface"] == "app_review"
    assert pending_request["extra"]["bundle_path"] == staged_bundle_path


class _ReviewArtifactStore:
    def __init__(
        self,
        *,
        parent_version: ArtifactVersionDoc,
        child_version: ArtifactVersionDoc,
        change_request: ChangeRequestDoc,
        session: RefinementSessionDoc,
    ) -> None:
        self.parent_version = parent_version
        self.child_version = child_version
        self.change_request = change_request
        self.session = session
        self.update_calls: list[dict] = []

    async def get_build_record(self, **kwargs):  # noqa: ANN003
        build_record_id = kwargs.get("build_record_id")
        if build_record_id == self.child_version.id:
            return self.child_version
        if build_record_id == self.parent_version.id:
            return self.parent_version
        return None

    async def list_refinement_sessions(self, **kwargs):  # noqa: ANN003
        if kwargs.get("result_build_record_id") == self.child_version.id:
            return [self.session]
        return []

    async def get_change_request(self, **kwargs):  # noqa: ANN003
        if kwargs.get("change_request_id") == self.change_request.id:
            return self.change_request
        return None

    async def list_change_requests(self, **kwargs):  # noqa: ANN003
        if kwargs.get("build_record_id") == self.parent_version.id:
            return [self.change_request]
        return []

    async def accept_build_record(self, **kwargs):  # noqa: ANN003
        self.child_version = self.child_version.model_copy(
            update={"lifecycle_status": ArtifactLifecycleStatus.CURRENT}
        )
        return self.child_version

    async def reject_artifact_version(self, **kwargs):  # noqa: ANN003
        self.child_version = self.child_version.model_copy(
            update={
                "lifecycle_status": ArtifactLifecycleStatus.ARCHIVED,
                "invalidation_reason": kwargs.get("reason"),
            }
        )
        return True

    async def update_refinement_session(self, **kwargs):  # noqa: ANN003
        self.update_calls.append(dict(kwargs))
        status = kwargs.get("status")
        ended_at = kwargs.get("ended_at")
        updates = {}
        if status is not None:
            updates["status"] = status
        if ended_at is not None:
            updates["ended_at"] = ended_at
        if updates:
            self.session = self.session.model_copy(update=updates)
        return True


def _build_review_store(
    tmp_path: Path,
    *,
    lifecycle_status: ArtifactLifecycleStatus,
    validation_status: ArtifactValidationStatus = ArtifactValidationStatus.PASSED,
) -> _ReviewArtifactStore:
    parent_zip = tmp_path / "parent_bundle.zip"
    child_zip = tmp_path / "child_bundle.zip"
    _make_bundle_zip(
        parent_zip,
        {
            "GeneratedApp/app.json": '{"appId":"app_1"}',
            "GeneratedApp/src/App.jsx": 'export default function App() { return <div>Old title</div>; }\n',
            "GeneratedApp/package.json": '{"name":"demo"}\n',
        },
    )
    _make_bundle_zip(
        child_zip,
        {
            "GeneratedApp/app.json": '{"appId":"app_1"}',
            "GeneratedApp/src/App.jsx": 'export default function App() { return <div>Builder Workspace</div>; }\n',
            "GeneratedApp/package.json": '{"name":"demo"}\n',
        },
    )
    parent_version = _artifact_version(
        artifact_version_id="av_parent_1",
        zip_path=parent_zip,
        version_number=1,
        lifecycle_status=ArtifactLifecycleStatus.SUPERSEDED,
    )
    child_version = _artifact_version(
        artifact_version_id="av_child_1",
        zip_path=child_zip,
        version_number=2,
        parent_version_id="av_parent_1",
        lifecycle_status=lifecycle_status,
        validation_status=validation_status,
    )
    change_request = _change_request_doc(artifact_version_id="av_parent_1")
    session_status = (
        RefinementSessionStatus.ACCEPTED
        if lifecycle_status == ArtifactLifecycleStatus.CURRENT
        else RefinementSessionStatus.VALIDATED
    )
    session = _refinement_session_doc(
        artifact_version_id="av_parent_1",
        result_artifact_version_id="av_child_1",
        status=session_status,
    )
    return _ReviewArtifactStore(
        parent_version=parent_version,
        child_version=child_version,
        change_request=change_request,
        session=session,
    )


@pytest.mark.parametrize("app_name, expected_title", [("FocusSprint", "FocusSprint"), (None, "Saved app"), ("  ", "Saved app")])
def test_studio_artifact_bundle_endpoint_returns_workbench_payload(monkeypatch, tmp_path: Path, _owned_build_target, app_name, expected_title):
    from mozaiksai.core.auth import reset_auth_adapter

    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")
    reset_auth_adapter()
    from mozaiksai.hosts import studio as studio_app

    store = _build_review_store(tmp_path, lifecycle_status=ArtifactLifecycleStatus.DRAFT)
    monkeypatch.setattr(studio_app, "get_artifact_store", lambda: store)
    _owned_build_target.get_app_record.return_value["app"]["name"] = app_name

    client = TestClient(studio_app.app)
    response = client.get("/api/studio/build/artifacts/av_child_1/bundle?build_registry_id=appreg_1")

    assert response.status_code == 200
    body = response.json()
    assert body["artifact_version_id"] == "av_child_1"
    assert body["build_family"] == "app_bundle"
    assert body["generated_files"]["src/App.jsx"].startswith("export default function App")
    assert body["generated_files"]["package.json"] == '{"name":"demo"}\n'
    assert body["workbench"]["artifact_version_id"] == "av_child_1"
    assert body["workbench"]["build_family"] == "app_bundle"
    assert body["workbench"]["title"] == expected_title
    assert "description" not in body["workbench"]
    assert body["review"]["changed_file_count"] == 1
    assert body["review"]["selected_paths"] == ["src/App.jsx"]
    assert body["change_request"]["classification"] == "patch"


def test_studio_artifact_review_endpoint_returns_diff_and_session_context(monkeypatch, tmp_path: Path):
    from mozaiksai.core.auth import reset_auth_adapter

    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")
    reset_auth_adapter()
    from mozaiksai.hosts import studio as studio_app

    store = _build_review_store(tmp_path, lifecycle_status=ArtifactLifecycleStatus.DRAFT)
    monkeypatch.setattr(studio_app, "get_artifact_store", lambda: store)

    client = TestClient(studio_app.app)
    response = client.get("/api/studio/build/artifacts/av_child_1/review?build_registry_id=appreg_1")

    assert response.status_code == 200
    body = response.json()
    assert body["review"]["review_status"] == "validated"
    assert body["review"]["schema_version"] == "mozaiks.refinement.review_package.v1"
    assert body["review"]["change_class"] == "patch"
    assert body["review"]["affected_paths"] == ["src/App.jsx"]
    assert body["review"]["write_back_mode"] == "generated_artifact"
    assert body["review"]["write_back_label"] == "Update app version"
    assert body["review"]["route_decision"]["execution_mode"] == "coding_worker"
    assert body["review"]["route_decision"]["scope_summary"] == "Update the dashboard bundle output."
    assert body["review"]["can_accept"] is True
    assert body["review"]["can_promote"] is False
    assert body["review"]["actions"][0]["id"] == "accept"
    assert body["review"]["actions"][0]["enabled"] is True
    assert body["review"]["changed_files"][0]["path"] == "src/App.jsx"
    assert "Builder Workspace" in body["review"]["changed_files"][0]["diff_preview"]
    assert body["refinement_session"]["status"] == "validated"
    assert any(action["id"] == "reroute" and action["enabled"] is False for action in body["review"]["actions"])


@pytest.mark.parametrize("endpoint", ["review", "bundle"])
def test_studio_review_reads_verified_durable_archive(monkeypatch, tmp_path, endpoint):
    from mozaiksai.core.artifacts import content_store
    from mozaiksai.core.auth import reset_auth_adapter
    from mozaiksai.hosts import studio

    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")
    reset_auth_adapter()
    store = _build_review_store(tmp_path, lifecycle_status=ArtifactLifecycleStatus.DRAFT)
    blobs = {}
    for version in (store.parent_version, store.child_version):
        path = Path(version.commit_metadata.metadata["artifact_path"])
        blobs[version.id] = path.read_bytes()
        version.commit_metadata.metadata.update(content_ref=version.id, content_backend="test-durable")
        path.unlink()
    backend = SimpleNamespace(backend_name="test-durable", get_bundle=AsyncMock(side_effect=lambda ref: blobs[ref]))
    monkeypatch.setattr(content_store, "get_artifact_content_store", lambda: backend)
    monkeypatch.setattr(studio, "get_artifact_store", lambda: store)
    client = TestClient(studio.app)
    response = client.get(f"/api/studio/build/artifacts/av_child_1/{endpoint}?build_registry_id=appreg_1")
    assert response.status_code == 200
    assert response.json()["review"]["changed_file_count"] == 1
    blobs[store.child_version.id] += b"changed after validation"
    assert client.get(f"/api/studio/build/artifacts/av_child_1/{endpoint}?build_registry_id=appreg_1").status_code == 409


def test_reloaded_workbench_does_not_inherit_source_only_success(monkeypatch, tmp_path):
    from mozaiksai.core.auth import reset_auth_adapter
    from mozaiksai.hosts import studio

    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")
    reset_auth_adapter()
    store = _build_review_store(tmp_path, lifecycle_status=ArtifactLifecycleStatus.DRAFT)
    store.child_version.app_validation_status = None
    monkeypatch.setattr(studio, "get_artifact_store", lambda: store)
    response = TestClient(studio.app).get("/api/studio/build/artifacts/av_child_1/bundle?build_registry_id=appreg_1")
    assert response.status_code == 200
    assert response.json()["workbench"]["app_validation_status"] == "pending"


def test_studio_artifact_review_marks_skipped_validation_as_override_required(monkeypatch, tmp_path: Path):
    from mozaiksai.core.auth import reset_auth_adapter

    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")
    reset_auth_adapter()
    from mozaiksai.hosts import studio as studio_app

    store = _build_review_store(
        tmp_path,
        lifecycle_status=ArtifactLifecycleStatus.DRAFT,
        validation_status=ArtifactValidationStatus.SKIPPED,
    )
    monkeypatch.setattr(studio_app, "get_artifact_store", lambda: store)

    client = TestClient(studio_app.app)
    response = client.get("/api/studio/build/artifacts/av_child_1/review?build_registry_id=appreg_1")

    assert response.status_code == 200
    body = response.json()
    assert body["review"]["can_accept"] is False
    assert body["review"]["validation_override_required"] is True
    assert "Required checks have not passed" in body["review"]["validation_blocker"]
    assert body["review"]["actions"][0]["id"] == "accept"
    assert body["review"]["actions"][0]["enabled"] is False
    assert "Required checks have not passed" in body["review"]["actions"][0]["reason"]


@pytest.mark.parametrize("endpoint", ["accept", "reject"])
@pytest.mark.parametrize("archive_owner", ["parent", "child"])
@pytest.mark.parametrize("damage", ["missing", "changed"])
def test_review_mutations_verify_archives_before_changing_state(monkeypatch, tmp_path, endpoint, archive_owner, damage):
    from mozaiksai.core.auth import reset_auth_adapter
    from mozaiksai.hosts import studio

    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")
    reset_auth_adapter()
    store = _build_review_store(tmp_path, lifecycle_status=ArtifactLifecycleStatus.DRAFT)
    monkeypatch.setattr(studio, "get_artifact_store", lambda: store)
    version = getattr(store, f"{archive_owner}_version")
    archive = Path(version.commit_metadata.metadata["artifact_path"])
    if damage == "missing":
        archive.unlink()
    else:
        archive.write_bytes(archive.read_bytes() + b"unverified change")
    before = store.child_version.model_dump(), store.session.model_dump()

    response = TestClient(studio.app).post(f"/api/studio/build/artifacts/av_child_1/{endpoint}?build_registry_id=appreg_1")

    assert response.status_code == 409, response.text
    assert (store.child_version.model_dump(), store.session.model_dump()) == before
    assert store.update_calls == []


@pytest.mark.parametrize("endpoint", ["accept", "reject"])
def test_review_mutations_reuse_verified_snapshot_for_response(monkeypatch, tmp_path, endpoint):
    from mozaiksai.core.auth import reset_auth_adapter
    from mozaiksai.hosts import studio

    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")
    reset_auth_adapter()
    store = _build_review_store(tmp_path, lifecycle_status=ArtifactLifecycleStatus.DRAFT)
    monkeypatch.setattr(studio, "get_artifact_store", lambda: store)
    method_name = "accept_build_record" if endpoint == "accept" else "reject_artifact_version"
    mutate = getattr(store, method_name)

    async def mutate_then_change_archive(**kwargs):
        result = await mutate(**kwargs)
        for version in (store.parent_version, store.child_version):
            Path(version.commit_metadata.metadata["artifact_path"]).write_bytes(b"changed after mutation")
        return result

    monkeypatch.setattr(store, method_name, mutate_then_change_archive)
    response = TestClient(studio.app).post(f"/api/studio/build/artifacts/av_child_1/{endpoint}?build_registry_id=appreg_1")
    assert response.status_code == 200, response.text
    assert response.json()["review"]["changed_file_count"] == 1
    assert "Builder Workspace" in response.json()["review"]["changed_files"][0]["diff_preview"]


def test_studio_artifact_accept_endpoint_marks_current_and_updates_session(monkeypatch, tmp_path: Path):
    from mozaiksai.core.auth import reset_auth_adapter

    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")
    reset_auth_adapter()
    from mozaiksai.hosts import studio as studio_app

    store = _build_review_store(tmp_path, lifecycle_status=ArtifactLifecycleStatus.DRAFT)
    monkeypatch.setattr(studio_app, "get_artifact_store", lambda: store)

    client = TestClient(studio_app.app)
    response = client.post("/api/studio/build/artifacts/av_child_1/accept?build_registry_id=appreg_1")

    assert response.status_code == 200
    body = response.json()
    assert body["accepted"] is True
    assert body["review"]["lifecycle_status"] == "current"
    assert store.child_version.lifecycle_status == ArtifactLifecycleStatus.CURRENT
    assert store.update_calls[-1]["status"] == RefinementSessionStatus.ACCEPTED


def test_studio_artifact_reject_endpoint_archives_and_updates_session(monkeypatch, tmp_path: Path):
    from mozaiksai.core.auth import reset_auth_adapter

    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")
    reset_auth_adapter()
    from mozaiksai.hosts import studio as studio_app

    store = _build_review_store(tmp_path, lifecycle_status=ArtifactLifecycleStatus.DRAFT)
    monkeypatch.setattr(studio_app, "get_artifact_store", lambda: store)

    client = TestClient(studio_app.app)
    response = client.post("/api/studio/build/artifacts/av_child_1/reject?build_registry_id=appreg_1")

    assert response.status_code == 200
    body = response.json()
    assert body["rejected"] is True
    assert body["review"]["lifecycle_status"] == "archived"
    assert store.child_version.lifecycle_status == ArtifactLifecycleStatus.ARCHIVED
    assert store.update_calls[-1]["status"] == RefinementSessionStatus.REJECTED


def test_studio_artifact_promote_endpoint_restores_bundle_and_updates_session(monkeypatch, tmp_path: Path):
    from mozaiksai.core.auth import reset_auth_adapter

    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")
    reset_auth_adapter()
    from mozaiksai.hosts import studio as studio_app

    store = _build_review_store(tmp_path, lifecycle_status=ArtifactLifecycleStatus.CURRENT)
    bundle_path = Path(store.child_version.commit_metadata.metadata["artifact_path"])
    store.child_version = ArtifactVersionDoc.model_validate({
        **store.child_version.model_dump(mode="python"),
        "app_validation_status": "passed",
        "files_manifest": [{
            "path": "GeneratedApp/GeneratedApp.zip", "content_type": "application/zip",
            "sha256": hashlib.sha256(bundle_path.read_bytes()).hexdigest(),
        }],
        "commit_metadata": {
            **store.child_version.commit_metadata.model_dump(mode="python"),
            "metadata": {**store.child_version.commit_metadata.metadata, "bundle_name": "GeneratedApp"},
        },
    })
    runtime_root = tmp_path / "runtime_app"
    monkeypatch.setattr(studio_app, "get_artifact_store", lambda: store)
    monkeypatch.setattr(studio_app, "resolve_app_root", lambda: runtime_root)
    index_start = AsyncMock(return_value={"status": "queued"})
    monkeypatch.setattr(studio_app, "_start_studio_app_intelligence_index_job", index_start)

    client = TestClient(studio_app.app)
    response = client.post("/api/studio/build/artifacts/av_child_1/promote?build_registry_id=appreg_1")

    assert response.status_code == 200
    body = response.json()
    assert body["promoted"] is True
    target = tmp_path / "workspaces" / "app_1" / "av_child_1"
    assert body["target_path"] == str(target)
    assert (target / "app" / "src" / "App.jsx").exists()
    assert not runtime_root.exists()
    assert store.update_calls[-1]["status"] == RefinementSessionStatus.PROMOTED
    index_start.assert_awaited_once()
    assert index_start.await_args.kwargs["app_id"] == "app_1"
    assert index_start.await_args.kwargs["body"].workspace_root == str(target)


@pytest.mark.asyncio
async def test_studio_index_failure_is_persisted_without_logging_failure(monkeypatch, tmp_path):
    from mozaiksai.hosts import studio

    job = studio.create_app_intelligence_index_job(
        app_id="app_1", requested_by="demo-user", workspace_root=str(tmp_path),
    )
    monkeypatch.setattr(studio, "get_app_intelligence_index_job", AsyncMock(return_value=job))
    save = AsyncMock(side_effect=lambda value: value)
    monkeypatch.setattr(studio, "save_app_intelligence_index_job", save)

    def reject_source(**kwargs):
        raise ValueError("source inspection rejected")

    monkeypatch.setattr(studio, "resolve_source_import", reject_source)
    await studio._run_studio_app_intelligence_index_job("app_1", job.job_id)
    save.assert_awaited_once()
    failed = save.await_args.args[0]
    assert failed.status == "failed"
    assert failed.error == "source inspection rejected"
    assert failed.completed_at is not None


def _async_classifier(*, change_class: str, rationale: str, confidence: float, signals: list[str]):
    async def _run(**kwargs):  # noqa: ANN003
        return SimpleNamespace(
            change_class=change_class,
            rationale=rationale,
            confidence=confidence,
            signals=signals,
        )

    return _run


def _async_coding_worker(result: dict):
    async def _run(request):  # noqa: ANN001
        result_id = result["metadata"]["build_record_id"]
        source = _BaselineStore.versions[request.build_record_id]
        child = source.model_copy(deep=True, update={"id": result_id})
        child.commit_metadata.metadata.update(request.run_build_binding.model_dump())
        _BaselineStore.versions[result_id] = child
        return CodingWorkerResult.model_validate(result)

    return _run



@pytest.mark.parametrize("validation_status", ["passed", "failed", "pending"])
def test_studio_trigger_endpoint_invokes_surface_regeneration_for_feature_changes(monkeypatch, validation_status):
    from mozaiksai.control_plane.contracts import (
        ContractSurfacePlan,
        ContractSurfaceUpdate,
        HarnessDecision,
        HarnessDecisionAction,
        SurfaceExecutionRecord,
        SurfacePlanExecutionResult,
    )
    from mozaiksai.core.auth import reset_auth_adapter

    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")
    reset_auth_adapter()
    from mozaiksai.hosts import studio as studio_app

    _plan = ContractSurfacePlan(
        summary="Add export action to product module",
        change_class="feature",
        artifact_kind="app_bundle",
        confidence=0.92,
        surfaces=[
            ContractSurfaceUpdate(
                kind="module_action",
                target_id="product",
                target_kind="module",
                affected_paths=["app/modules/product/backend/handler.py"],
                dependency_order=0,
                rationale="Add export action handler",
                confidence=0.92,
            )
        ],
    )
    _harness_decision = HarnessDecision(
        decision_type="targeted_regeneration",
        message="Executing targeted surface regeneration.",
        rationale="Adding an export action extends the existing app bundle.",
        confidence=0.92,
        recommended_workflow_id=None,
        actions=[
            HarnessDecisionAction(
                action_id="review_surface",
                label="Review surface changes",
                action_type="review_surface",
            )
        ],
        metadata={"contract_surface_plan": _plan.model_dump(mode="python")},
    )
    _surface_result = SurfacePlanExecutionResult(
        status="success",
        surfaces_executed=[
            SurfaceExecutionRecord(
                kind="module_action",
                target_id="product",
                status="success",
                applied_files={"app/modules/product/backend/handler.py": "# updated handler"},
            )
        ],
        all_files={"app/modules/product/backend/handler.py": "# updated handler"},
        requires_schema_migration=False,
    )

    persisted_sessions: list[dict] = []
    persisted_changes: list[dict] = []

    class _ArtifactStore(_BaselineStore):
        async def create_change_request(self, **kwargs):
            persisted_changes.append(kwargs)
            return SimpleNamespace(id="cr_surface_1")

        async def create_refinement_session(self, **kwargs):
            persisted_sessions.append(kwargs)
            return SimpleNamespace(id="rs_surface_1")

        async def invalidate_artifact_version_refs(self, **kwargs):
            return []

        async def update_change_request_router_decision(self, **kwargs):
            return True

    monkeypatch.setattr(studio_app, "get_artifact_store", lambda: _ArtifactStore())
    monkeypatch.setattr(
        studio_app.get_orchestration_control_harness()._refinement_resolver,
        "_classifier",
        SimpleNamespace(
            classify=_async_classifier(
                change_class="feature",
                rationale="Adding an export action extends the existing app bundle.",
                confidence=0.92,
                signals=["new_capability", "app_extension"],
            )
        ),
    )

    async def _fake_prepare_contract_surface(**kwargs):
        usage = kwargs["refinement_request"].usage_context
        assert usage.app_id == "factory" and usage.user_id == "demo-user"
        assert usage.run_build_binding is None
        return _plan, _harness_decision

    async def _fake_execute_surface_plan(**kwargs):
        usage = kwargs["refinement_request"].usage_context
        assert usage.app_id == "factory" and usage.user_id == "demo-user"
        assert usage.run_build_binding == _BINDING
        assert usage.chat_id is usage.workflow_name is None
        assert kwargs["workspace_files"]["app/modules/product/backend/handler.py"] == "# product handler"
        return _surface_result

    async def _fake_finalize_surface_output(**kwargs):
        assert kwargs["run_build_binding"] == _BINDING
        assert "Dockerfile" in kwargs["workspace_files"]
        child = _BaselineStore.versions["av_456"].model_copy(update={
            "id": "surface_child", "validation_status": ArtifactValidationStatus(validation_status),
        })
        child.commit_metadata = child.commit_metadata.model_copy(update={
            "metadata": {**child.commit_metadata.metadata, **_BINDING.model_dump()},
        })
        _BaselineStore.versions[child.id] = child
        return CodingWorkerResult(
            eligible=True, status={"passed": "validated", "pending": "planned"}.get(validation_status, "failed"),
            metadata={"build_record_id": child.id},
        )

    harness = studio_app.get_orchestration_control_harness()
    monkeypatch.setattr(harness, "contract_surface_enabled", lambda: True)
    monkeypatch.setattr(harness, "prepare_contract_surface_request", _fake_prepare_contract_surface)
    monkeypatch.setattr(harness, "execute_surface_plan", _fake_execute_surface_plan)
    monkeypatch.setattr(harness, "finalize_surface_output", _fake_finalize_surface_output)

    client = TestClient(studio_app.app)
    response = client.post(
        "/api/workflows/trigger",
        json={
            "build_registry_id": "appreg_1",
            "trigger_source": "refinement",
            "trigger_payload": {
                "refinement_request": {
                    "artifact_kind": "app_bundle",
                    "artifact_key": "app_bundle",
                    "artifact_version_id": "av_456",
                    "raw_user_request": "Add an export action to the product module",
                    "source_surface": "app_build",
                },
            },
            "context_variables": {"screen": "studio-create"},
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["execution_mode"] == "surface_regeneration"
    assert body["chat_id"] is None
    assert body["websocket_url"] is None
    assert body["trigger_source"] == "refinement"
    assert body["rerouted_by_dependency"] is False
    assert body["harness_decision"]["decision_type"] == "targeted_regeneration"
    assert body["surface_result"]["status"] == {"passed": "success", "pending": "partial", "failed": "failed"}[validation_status]
    assert body["surface_result"]["metadata"]["build_record_id"] == "surface_child"
    assert "app/modules/product/backend/handler.py" in body["surface_result"]["all_files"]
    assert body["refinement_session_id"] == "rs_surface_1"
    assert len(persisted_sessions) == 1
    assert persisted_sessions[0]["provider"] == "contract_surface_regeneration"
    assert persisted_sessions[0]["build_record_id"] == "av_456"
    assert persisted_sessions[0]["change_request_id"] == "cr_surface_1"
    assert persisted_sessions[0]["result_build_record_id"] == "surface_child"
    assert persisted_sessions[0]["status"].value == {"passed": "validated", "pending": "pending", "failed": "failed"}[validation_status]


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel_at", ["coding", "surface_execution", "surface_finalization"])
async def test_cancelled_inline_refinement_releases_bound_build_without_promotion(
    monkeypatch, _owned_build_target, cancel_at,
):
    import anyio
    import httpx

    from mozaiksai.control_plane.implementations import orchestration_control
    from mozaiksai.core.auth import reset_auth_adapter
    from mozaiksai.hosts import studio

    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")
    reset_auth_adapter()
    started = asyncio.Event()
    events = []
    cleanup = []

    async def wait_for_cancellation(*args, **kwargs):
        started.set()
        await asyncio.Event().wait()

    async def record_event(**kwargs):
        await anyio.lowlevel.checkpoint()
        events.append(kwargs)

    async def save_status(**kwargs):
        await anyio.lowlevel.checkpoint()
        cleanup.append(kwargs)
        return {"success": True}

    _owned_build_target.update_build_status.side_effect = save_status
    monkeypatch.setattr(orchestration_control, "record_refinement_event", record_event)
    harness = studio.get_orchestration_control_harness()
    monkeypatch.setattr(harness, "_config_loader", lambda: ControlPlaneConfig(
        enabled=True,
        llm_profiles={"codegen": {"llm_config": {"model": "test-model"}}},
        classifier={"enabled": True, "llm_profile": "codegen"},
        coding={"enabled": True, "llm_profile": "codegen"},
    ))
    monkeypatch.setattr(harness, "contract_surface_enabled", lambda: cancel_at != "coding")
    monkeypatch.setattr(harness._refinement_resolver, "_classifier", SimpleNamespace(
        classify=_async_classifier(
            change_class="patch" if cancel_at == "coding" else "feature",
            rationale="Requested scoped change", confidence=0.95, signals=["test"],
        ),
    ))
    if cancel_at == "coding":
        monkeypatch.setattr(harness, "_coding_worker", SimpleNamespace(execute=wait_for_cancellation))
    else:
        monkeypatch.setattr(harness, "prepare_contract_surface_request", AsyncMock(return_value=(
            SimpleNamespace(requires_schema_migration=False), None,
        )))
        monkeypatch.setattr(harness, "execute_surface_plan", AsyncMock(
            side_effect=wait_for_cancellation if cancel_at == "surface_execution" else None,
            return_value=SimpleNamespace(status="success"),
        ))
        monkeypatch.setattr(harness, "finalize_surface_output", wait_for_cancellation)
    original = _BaselineStore.versions["av_456"].model_dump(mode="json")
    trigger_payload = {
        "refinement_request": {
            "artifact_kind": "app_bundle", "artifact_key": "app_bundle", "artifact_version_id": "av_456",
            "raw_user_request": "Update dashboard", "source_surface": "app_build",
        },
    }
    if cancel_at == "coding":
        trigger_payload["coding_request"] = {"files": {"app/ui/pages/Dashboard.jsx": "ignored"}}
    assert any(middleware.cls.__name__ == "BaseHTTPMiddleware" for middleware in studio.app.user_middleware)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=studio.app), base_url="http://test") as client:
        task = asyncio.create_task(client.post("/api/workflows/trigger", json={
            "build_registry_id": "appreg_1", "trigger_source": "refinement", "trigger_payload": trigger_payload,
        }))
        try:
            await asyncio.wait_for(started.wait(), timeout=5)
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, timeout=5)

    _owned_build_target.update_build_status.assert_awaited_once_with(
        owner_user_id="demo-user", build_registry_id="appreg_1",
        expected_build_id="build_1", status="needs_revision",
    )
    _owned_build_target.promote_build.assert_not_awaited()
    assert _BaselineStore.versions["av_456"].model_dump(mode="json") == original
    assert cleanup == [{
        "owner_user_id": "demo-user", "build_registry_id": "appreg_1",
        "expected_build_id": "build_1", "status": "needs_revision",
    }]
    if cancel_at == "coding":
        cancelled = [event for event in events if event["event_kind"] == "cancelled"]
        received = [event for event in events if event["event_kind"] == "request_received"]
        assert len(cancelled) == len(received) == 1
        assert cancelled[0]["request_id"] == received[0]["request_id"]
        assert cancelled[0]["request_id"] != "unknown"
        assert cancelled[0]["outcome"] == "cancelled"


@pytest.mark.parametrize("strategy", ["unsupported", "automatic"])
def test_studio_rejects_coding_validation_strategy_before_classification(monkeypatch, _owned_build_target, strategy):
    from mozaiksai.core.auth import reset_auth_adapter
    from mozaiksai.hosts import studio

    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")
    reset_auth_adapter()
    harness = studio.get_orchestration_control_harness()
    monkeypatch.setattr(harness, "coding_enabled", lambda: True)
    classify = AsyncMock()
    execute = AsyncMock()
    monkeypatch.setattr(harness, "route_refinement_request", classify)
    monkeypatch.setattr(harness, "execute_coding_request", execute)

    response = TestClient(studio.app).post("/api/workflows/trigger", json={
        "build_registry_id": "appreg_1", "trigger_source": "refinement",
        "trigger_payload": {
            "refinement_request": {
                "artifact_kind": "app_bundle", "artifact_version_id": "av_456",
                "raw_user_request": "Update dashboard", "source_surface": "app_build",
            },
            "coding_request": {
                "files": {"app/ui/pages/Dashboard.jsx": "ignored"}, "validation_strategy": strategy,
            },
        },
    })

    assert response.status_code == 400
    assert response.json()["detail"] == f"Unsupported coding validation strategy: {strategy}"
    classify.assert_not_awaited()
    execute.assert_not_awaited()
    _owned_build_target.begin_refinement_run.assert_not_awaited()


def test_tampered_refinement_baseline_cannot_start_coding(monkeypatch, _owned_build_target):
    from mozaiksai.core.auth import reset_auth_adapter
    from mozaiksai.hosts import studio

    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")
    reset_auth_adapter()
    harness = studio.get_orchestration_control_harness()
    monkeypatch.setattr(harness, "_config_loader", lambda: ControlPlaneConfig(
        enabled=True,
        llm_profiles={"codegen": {"llm_config": {"model": "test-model"}}},
        classifier={"enabled": True, "llm_profile": "codegen"},
        coding={"enabled": True, "llm_profile": "codegen"},
    ))
    monkeypatch.setattr(harness, "contract_surface_enabled", lambda: False)
    monkeypatch.setattr(harness._refinement_resolver, "_classifier", SimpleNamespace(
        classify=_async_classifier(change_class="patch", rationale="Scoped change", confidence=0.95, signals=["test"]),
    ))
    execute = AsyncMock()
    monkeypatch.setattr(harness, "execute_coding_request", execute)
    parent = _BaselineStore.versions["av_456"]
    archive = Path(parent.commit_metadata.metadata["artifact_path"])
    _make_bundle_zip(archive, {"app/ui/pages/Dashboard.jsx": "tampered after registration"})

    response = TestClient(studio.app).post("/api/workflows/trigger", json={
        "build_registry_id": "appreg_1", "trigger_source": "refinement",
        "trigger_payload": {
            "refinement_request": {
                "artifact_kind": "app_bundle", "artifact_version_id": parent.id,
                "raw_user_request": "Update dashboard", "source_surface": "app_build",
            },
            "coding_request": {"files": {"app/ui/pages/Dashboard.jsx": "ignored"}},
        },
    })
    assert response.status_code == 409
    assert "could not be verified" in response.json()["detail"]
    execute.assert_not_awaited()
    _owned_build_target.begin_refinement_run.assert_not_awaited()
