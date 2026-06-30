from __future__ import annotations

import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace

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


def _make_bundle_zip(zip_path: Path, files: dict[str, str]) -> None:
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for relative_path, content in files.items():
            archive.writestr(relative_path, content)


def _artifact_version(
    *,
    artifact_version_id: str,
    zip_path: Path,
    artifact_kind: str = "app_bundle",
    artifact_key: str = "app_bundle",
    version_number: int = 1,
    parent_version_id: str | None = None,
    lifecycle_status: ArtifactLifecycleStatus = ArtifactLifecycleStatus.DRAFT,
    validation_status: ArtifactValidationStatus = ArtifactValidationStatus.PASSED,
    files_manifest: list[dict[str, object]] | None = None,
) -> ArtifactVersionDoc:
    resolved_manifest = files_manifest
    if resolved_manifest is None:
        with zipfile.ZipFile(zip_path, "r") as archive:
            resolved_manifest = [
                {"path": info.filename, "size_bytes": info.file_size}
                for info in archive.infolist()
                if not info.is_dir()
            ]
    return ArtifactVersionDoc.model_validate(
        {
            "_id": artifact_version_id,
            "app_id": "app_1",
            "artifact_kind": artifact_kind,
            "artifact_key": artifact_key,
            "version_number": version_number,
            "parent_version_id": parent_version_id,
            "lineage_root_id": parent_version_id or artifact_version_id,
            "source_workflow": "AppGenerator",
            "source_chat_id": "chat_1",
            "canonical_inputs_version": {},
            "lifecycle_status": lifecycle_status.value,
            "validation_status": validation_status.value,
            "files_manifest": resolved_manifest,
            "commit_metadata": ArtifactCommitMetadata(
                message="Generated artifact",
                source_workflow="AppGenerator",
                source_chat_id="chat_1",
                metadata={"artifact_path": str(zip_path)},
            ).model_dump(mode="python"),
        }
    )


def _change_request_doc(*, artifact_version_id: str) -> ChangeRequestDoc:
    return ChangeRequestDoc.model_validate(
        {
            "_id": "cr_review_1",
            "app_id": "app_1",
            "artifact_kind": "app_bundle",
            "artifact_key": "app_bundle",
            "artifact_version_id": artifact_version_id,
            "raw_user_request": "Update the dashboard title and export controls.",
            "classification": ChangeClassification.PATCH.value,
            "refinement_request": RefinementRequestPayload(
                artifact_kind="app_bundle",
                artifact_key="app_bundle",
                artifact_version_id=artifact_version_id,
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


def test_studio_trigger_endpoint_accepts_refinement_trigger_payload(monkeypatch):
    from mozaiksai.core.auth import reset_auth_adapter

    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")
    reset_auth_adapter()
    sys.modules.pop("factory_app", None)

    from mozaiksai.hosts import studio as studio_app

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

    class _ArtifactStore:
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

    client = TestClient(studio_app.app)
    response = client.post(
        "/api/workflows/trigger",
        json={
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
            "context_variables": {"screen": "studio-create"},
        },
    )

    assert response.status_code == 200
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
            "artifact_kind": "app_bundle",
            "artifact_key": "app_bundle",
            "artifact_version_id": "av_123",
            "raw_user_request": "Add an export action",
            "source_surface": "app_build",
            "app_id": captured_prepare["app_id"],
            "user_id": captured_prepare["user_id"],
            "requested_workflow_id": None,
            "extra": {},
        },
    }
    assert "change_class" not in captured_prepare
    assert "artifact_kind" not in captured_prepare
    assert "artifact_version_id" not in captured_prepare
    assert "raw_user_request" not in captured_prepare
    assert captured_prepare["extra_trigger_meta"] == {
        "action_id": None,
        "change_class": "feature",
        "artifact_version_id": "av_123",
        "artifact_kind": "app_bundle",
        "workflow_sequence": "app_revision",
    }
    assert persisted_changes == [
        {
            "app_id": captured_prepare["app_id"],
            "artifact_kind": "app_bundle",
            "artifact_key": "app_bundle",
            "artifact_version_id": "av_123",
            "raw_user_request": "Add an export action",
            "classification": studio_app.ChangeClassification.FEATURE,
            "refinement_request": {
                "request_kind": "refinement",
                "declared_change_class": None,
                "artifact_kind": "app_bundle",
                "artifact_key": "app_bundle",
                "artifact_version_id": "av_123",
                "raw_user_request": "Add an export action",
                "source_surface": "app_build",
                "app_id": captured_prepare["app_id"],
                "user_id": "demo-user",
                "requested_workflow_id": None,
                "extra": {},
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
            "app_id": captured_prepare["app_id"],
            "artifact_version_refs": {"app_bundle": "av_123"},
            "affected_artifact_kinds": ["app_bundle"],
            "reason": "change_request:cr_123",
        }
    ]
    assert updated_router_decisions == [
        {
            "app_id": captured_prepare["app_id"],
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
    sys.modules.pop("factory_app", None)

    from mozaiksai.hosts import studio as studio_app

    client = TestClient(studio_app.app)
    response = client.post(
        "/api/workflows/trigger",
        json={
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
    sys.modules.pop("factory_app", None)

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


def test_studio_trigger_endpoint_can_short_circuit_to_coding_worker(monkeypatch):
    from mozaiksai.core.auth import reset_auth_adapter

    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")
    reset_auth_adapter()
    sys.modules.pop("factory_app", None)

    from mozaiksai.hosts import studio as studio_app

    persisted_changes: list[dict] = []
    persisted_sessions: list[dict] = []

    async def fail_prepare(**kwargs):  # noqa: ANN003
        raise AssertionError("workflow launch should not run for coding worker execution")

    async def fail_launch(launch):  # noqa: ANN001
        raise AssertionError("workflow launch should not run for coding worker execution")

    class _ArtifactStore:
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
            classifier={"enabled": True},
            coding={"enabled": True, "llm_config": {"model": "gpt-5.2-codex", "temperature": 0.1}},
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
                "metadata": {"artifact_version_id": "av_child_code_1"},
            }
        ),
    )

    client = TestClient(studio_app.app)
    response = client.post(
        "/api/workflows/trigger",
        json={
            "trigger_source": "refinement",
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
            "harness_decision": {
                "decision_type": "auto_patch",
                "message": "Scoped patch applied.",
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
                    "scope_origin": "applied",
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
                "metadata": {"artifact_version_id": "av_child_code_1"},
            },
    }
    assert persisted_changes[0]["router_decision"]["execution_mode"] == "coding_worker"
    assert persisted_sessions == [
        {
            "app_id": persisted_changes[0]["app_id"],
            "artifact_version_id": "av_456",
            "change_request_id": "cr_code_1",
            "result_artifact_version_id": "av_child_code_1",
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
    sys.modules.pop("factory_app", None)

    from mozaiksai.hosts import studio as studio_app

    persisted_changes: list[dict] = []

    async def fail_prepare(**kwargs):  # noqa: ANN003
        raise AssertionError("workflow launch should not run for coding worker execution")

    async def fail_launch(launch):  # noqa: ANN001
        raise AssertionError("workflow launch should not run for coding worker execution")

    class _ArtifactStore:
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
        assert request.metadata["scope_proposal"]["selected_paths"] == ["app/ui/pages/Dashboard.jsx"]
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
                "metadata": {},
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
            classifier={"enabled": True},
            coding={"enabled": True, "llm_config": {"model": "gpt-5.2-codex", "temperature": 0.1}},
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


def test_studio_trigger_endpoint_can_confirm_proposed_multi_file_scope(monkeypatch):
    from mozaiksai.core.auth import reset_auth_adapter

    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")
    reset_auth_adapter()
    sys.modules.pop("factory_app", None)

    from mozaiksai.hosts import studio as studio_app

    persisted_changes: list[dict] = []

    async def fail_prepare(**kwargs):  # noqa: ANN003
        raise AssertionError("workflow launch should not run for coding worker execution")

    async def fail_launch(launch):  # noqa: ANN001
        raise AssertionError("workflow launch should not run for coding worker execution")

    class _ArtifactStore:
        async def create_change_request(self, **kwargs):
            persisted_changes.append(kwargs)
            return SimpleNamespace(id=f"cr_scope_{len(persisted_changes)}")

        async def invalidate_artifact_version_refs(self, **kwargs):
            return [kwargs["artifact_version_refs"]["app_bundle"]]

        async def create_refinement_session(self, **kwargs):
            return SimpleNamespace(id="rs_scope_1")

    async def _fake_propose(**kwargs):  # noqa: ANN003
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
        assert sorted(request.files.keys()) == [
            "app/ui/components/ExportPanel.jsx",
            "app/ui/pages/Dashboard.jsx",
        ]
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
                "metadata": {"artifact_version_id": "av_child_multi_1"},
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
            classifier={"enabled": True},
            coding={"enabled": True, "llm_config": {"model": "gpt-5.2-codex", "temperature": 0.1}},
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

    second = client.post(
        "/api/workflows/trigger",
        json={
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
                "harness_action": {
                    "action_id": "apply_proposed_scope",
                },
            },
        },
    )

    assert second.status_code == 200
    second_body = second.json()
    assert second_body["execution_mode"] == "coding_worker"
    assert second_body["coding_worker"]["metadata"]["artifact_version_id"] == "av_child_multi_1"


def test_studio_trigger_endpoint_returns_core_harness_decision_before_launch(monkeypatch):
    from mozaiksai.core.auth import reset_auth_adapter

    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")
    reset_auth_adapter()
    sys.modules.pop("factory_app", None)

    from mozaiksai.hosts import studio as studio_app

    async def fail_prepare(**kwargs):  # noqa: ANN003
        raise AssertionError("workflow launch should not run before a core confirmation")

    async def fail_launch(launch):  # noqa: ANN001
        raise AssertionError("workflow launch should not run before a core confirmation")

    class _ArtifactStore:
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
            "trigger_source": "refinement",
            "trigger_payload": {
                "refinement_request": {
                    "artifact_kind": "app_bundle",
                    "artifact_key": "app_bundle",
                    "artifact_version_id": "av_core_1",
                    "raw_user_request": "Add blockchain support to the product.",
                    "source_surface": "app_build",
                },
            },
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "execution_mode": "harness_decision",
        "chat_id": None,
        "workflow_id": "ValueEngine",
        "requested_workflow_id": "ValueEngine",
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
                    "scope_summary": "Restart from ValueEngine and invalidate downstream outputs that depend on the app bundle.",
            },
        },
    }


def test_studio_trigger_endpoint_reuses_prelaunch_revision_intent_on_confirm(monkeypatch):
    from mozaiksai.core.auth import reset_auth_adapter

    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")
    reset_auth_adapter()
    sys.modules.pop("factory_app", None)

    from mozaiksai.hosts import studio as studio_app

    persisted_state = {
        "active_change_request_id": None,
        "active_revision_id": None,
    }
    create_calls: list[dict] = []
    captured_prepare: dict = {}
    captured_pending_harness_decision: dict = {}

    async def fake_get_session_snapshot(*, app_id, user_id):  # noqa: ANN001
        return {
            "session_id": f"session_router::{app_id}::{user_id}",
            "app_id": app_id,
            "user_id": user_id,
            "active_change_request_id": persisted_state["active_change_request_id"],
            "active_revision_id": persisted_state["active_revision_id"],
        }

    async def fake_persist_revision_intent(*, trigger, decision, pending_harness_decision=None):  # noqa: ANN001
        if pending_harness_decision is not None:
            captured_pending_harness_decision.update(
                {
                    "trigger_source": pending_harness_decision.trigger_source,
                    "requested_workflow_id": pending_harness_decision.requested_workflow_id,
                    "journey_id": pending_harness_decision.journey_id,
                    "context_variables": dict(pending_harness_decision.context_variables or {}),
                    "trigger_payload": dict(pending_harness_decision.trigger_payload or {}),
                }
            )
        persisted_state["active_change_request_id"] = (
            str(
                decision.context_seed.get("change_request_id")
                or getattr(pending_harness_decision, "change_request_id", None)
                or ""
            ).strip()
            or None
        )
        persisted_state["active_revision_id"] = (
            str(
                decision.context_seed.get("revision_id")
                or getattr(pending_harness_decision, "revision_id", None)
                or ""
            ).strip()
            or "rev_core_1"
        )
        return {
            "session_id": f"session_router::{trigger.app_id}::{trigger.user_id}",
            "app_id": trigger.app_id,
            "user_id": trigger.user_id,
            "active_change_request_id": persisted_state["active_change_request_id"],
            "active_revision_id": persisted_state["active_revision_id"],
        }

    async def fake_prepare_routed_workflow_launch(**kwargs):
        captured_prepare.update(kwargs)
        return SimpleNamespace(
            workflow_id="ValueEngine",
            routing_decision=SimpleNamespace(
                requested_workflow_id="ValueEngine",
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
            requested_workflow_id="ValueEngine",
            journey_id=None,
            websocket_url="/ws/ValueEngine/app_1/chat_value_1/demo-user",
            trigger_source="refinement",
            routing_explanation=launch.routing_decision.explanation,
            rerouted_by_dependency=False,
        )

    class _ArtifactStore:
        async def create_change_request(self, **kwargs):
            create_calls.append(kwargs)
            return SimpleNamespace(id="cr_core_1")

        async def invalidate_artifact_version_refs(self, **kwargs):
            return [kwargs["artifact_version_refs"]["app_bundle"]]

        async def update_change_request_router_decision(self, **kwargs):
            return True

    router_double = SimpleNamespace(
        get_session_snapshot=fake_get_session_snapshot,
        persist_revision_intent=fake_persist_revision_intent,
    )

    monkeypatch.setattr(studio_app, "get_session_router", lambda: router_double)
    monkeypatch.setattr(studio_app, "prepare_routed_workflow_launch", fake_prepare_routed_workflow_launch)
    monkeypatch.setattr(studio_app, "launch_prepared_workflow", fake_launch_prepared_workflow)
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
    first = client.post(
        "/api/workflows/trigger",
        json={
            "trigger_source": "refinement",
            "trigger_payload": {
                "refinement_request": {
                    "artifact_kind": "app_bundle",
                    "artifact_key": "app_bundle",
                    "artifact_version_id": "av_core_1",
                    "raw_user_request": "Add blockchain support to the product.",
                    "source_surface": "app_build",
                },
            },
        },
    )

    assert first.status_code == 200
    assert first.json()["execution_mode"] == "harness_decision"
    assert create_calls and len(create_calls) == 1
    assert persisted_state["active_change_request_id"] == "cr_core_1"
    assert persisted_state["active_revision_id"]
    assert captured_pending_harness_decision["trigger_source"] == "refinement"
    assert captured_pending_harness_decision["requested_workflow_id"] is None
    assert captured_pending_harness_decision["journey_id"] == "full_rebuild"
    assert captured_pending_harness_decision["context_variables"] == {}
    assert captured_pending_harness_decision["trigger_payload"]["change_request_id"] == "cr_core_1"
    assert captured_pending_harness_decision["trigger_payload"]["revision_id"] == persisted_state["active_revision_id"]
    assert captured_pending_harness_decision["trigger_payload"]["refinement_request"]["artifact_kind"] == "app_bundle"
    assert captured_pending_harness_decision["trigger_payload"]["refinement_request"]["artifact_version_id"] == "av_core_1"

    second = client.post(
        "/api/workflows/trigger",
        json={
            "trigger_source": "refinement",
            "trigger_payload": {
                "refinement_request": {
                    "artifact_kind": "app_bundle",
                    "artifact_key": "app_bundle",
                    "artifact_version_id": "av_core_1",
                    "raw_user_request": "Add blockchain support to the product.",
                    "source_surface": "app_build",
                },
                "harness_action": {
                    "action_id": "confirm_recommended_workflow",
                },
            },
        },
    )

    assert second.status_code == 200
    assert second.json()["execution_mode"] == "workflow"
    assert len(create_calls) == 1
    assert captured_prepare["journey_id"] == "full_rebuild"
    assert captured_prepare["trigger_payload"]["change_request_id"] == "cr_core_1"
    assert captured_prepare["trigger_payload"]["revision_id"] == persisted_state["active_revision_id"]


def test_app_review_revision_trigger_preserves_staged_bundle_context(monkeypatch):
    from mozaiksai.core.auth import reset_auth_adapter

    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")
    reset_auth_adapter()
    sys.modules.pop("factory_app", None)

    from mozaiksai.hosts import studio as studio_app

    staged_bundle_path = "C:/Repos/BlocUnitedRepo/mozaiks/generated/apps/app_1/build_1/app"
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

    class _ArtifactStore:
        async def create_change_request(self, **kwargs):
            create_calls.append(kwargs)
            return SimpleNamespace(id="cr_app_review_1")

        async def invalidate_artifact_version_refs(self, **kwargs):
            return [kwargs["artifact_version_refs"]["app_bundle"]]

    monkeypatch.setattr(
        studio_app,
        "get_session_router",
        lambda: SimpleNamespace(persist_revision_intent=fake_persist_revision_intent),
    )
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
                        "bundle_path": staged_bundle_path,
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
    assert create_calls[0]["artifact_version_id"] == "av_review_1"
    assert create_calls[0]["refinement_request"]["source_surface"] == "app_review"
    assert create_calls[0]["refinement_request"]["extra"]["bundle_path"] == staged_bundle_path

    seed = captured_pending["decision_context_seed"]
    assert seed["artifact_version_id"] == "av_review_1"
    assert seed["artifact_root"] == staged_bundle_path
    assert seed["lifecycle_state"] == "review"
    assert seed["refinement_request_meta"]["source_surface"] == "app_review"
    assert seed["refinement_request_meta"]["extra"]["build_registry_id"] == "appreg_review_1"

    pending_request = captured_pending["pending_trigger_payload"]["refinement_request"]
    assert pending_request["artifact_version_id"] == "av_review_1"
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

    async def get_artifact_version(self, **kwargs):  # noqa: ANN003
        artifact_version_id = kwargs.get("artifact_version_id")
        if artifact_version_id == self.child_version.id:
            return self.child_version
        if artifact_version_id == self.parent_version.id:
            return self.parent_version
        return None

    async def list_refinement_sessions(self, **kwargs):  # noqa: ANN003
        if kwargs.get("result_artifact_version_id") == self.child_version.id:
            return [self.session]
        return []

    async def get_change_request(self, **kwargs):  # noqa: ANN003
        if kwargs.get("change_request_id") == self.change_request.id:
            return self.change_request
        return None

    async def list_change_requests(self, **kwargs):  # noqa: ANN003
        if kwargs.get("artifact_version_id") == self.parent_version.id:
            return [self.change_request]
        return []

    async def accept_artifact_version(self, **kwargs):  # noqa: ANN003
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


def _build_review_store(tmp_path: Path, *, lifecycle_status: ArtifactLifecycleStatus) -> _ReviewArtifactStore:
    parent_zip = tmp_path / "parent_bundle.zip"
    child_zip = tmp_path / "child_bundle.zip"
    _make_bundle_zip(
        parent_zip,
        {
            "GeneratedApp/src/App.jsx": 'export default function App() { return <div>Old title</div>; }\n',
            "GeneratedApp/package.json": '{"name":"demo"}\n',
        },
    )
    _make_bundle_zip(
        child_zip,
        {
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


def test_studio_artifact_bundle_endpoint_returns_workbench_payload(monkeypatch, tmp_path: Path):
    from mozaiksai.core.auth import reset_auth_adapter

    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")
    reset_auth_adapter()
    sys.modules.pop("factory_app", None)

    from mozaiksai.hosts import studio as studio_app

    store = _build_review_store(tmp_path, lifecycle_status=ArtifactLifecycleStatus.DRAFT)
    monkeypatch.setattr(studio_app, "get_artifact_store", lambda: store)

    client = TestClient(studio_app.app)
    response = client.get("/api/studio/build/artifacts/av_child_1/bundle")

    assert response.status_code == 200
    body = response.json()
    assert body["artifact_version_id"] == "av_child_1"
    assert body["artifact_kind"] == "app_bundle"
    assert body["generated_files"]["src/App.jsx"].startswith("export default function App")
    assert body["generated_files"]["package.json"] == '{"name":"demo"}\n'
    assert body["workbench"]["artifact_version_id"] == "av_child_1"
    assert body["workbench"]["artifact_kind"] == "app_bundle"
    assert body["review"]["changed_file_count"] == 1
    assert body["review"]["selected_paths"] == ["src/App.jsx"]
    assert body["change_request"]["classification"] == "patch"


def test_studio_artifact_review_endpoint_returns_diff_and_session_context(monkeypatch, tmp_path: Path):
    from mozaiksai.core.auth import reset_auth_adapter

    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")
    reset_auth_adapter()
    sys.modules.pop("factory_app", None)

    from mozaiksai.hosts import studio as studio_app

    store = _build_review_store(tmp_path, lifecycle_status=ArtifactLifecycleStatus.DRAFT)
    monkeypatch.setattr(studio_app, "get_artifact_store", lambda: store)

    client = TestClient(studio_app.app)
    response = client.get("/api/studio/build/artifacts/av_child_1/review")

    assert response.status_code == 200
    body = response.json()
    assert body["review"]["review_status"] == "validated"
    assert body["review"]["can_accept"] is True
    assert body["review"]["can_promote"] is False
    assert body["review"]["changed_files"][0]["path"] == "src/App.jsx"
    assert "Builder Workspace" in body["review"]["changed_files"][0]["diff_preview"]
    assert body["refinement_session"]["status"] == "validated"


def test_studio_artifact_accept_endpoint_marks_current_and_updates_session(monkeypatch, tmp_path: Path):
    from mozaiksai.core.auth import reset_auth_adapter

    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")
    reset_auth_adapter()
    sys.modules.pop("factory_app", None)

    from mozaiksai.hosts import studio as studio_app

    store = _build_review_store(tmp_path, lifecycle_status=ArtifactLifecycleStatus.DRAFT)
    monkeypatch.setattr(studio_app, "get_artifact_store", lambda: store)

    client = TestClient(studio_app.app)
    response = client.post("/api/studio/build/artifacts/av_child_1/accept")

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
    sys.modules.pop("factory_app", None)

    from mozaiksai.hosts import studio as studio_app

    store = _build_review_store(tmp_path, lifecycle_status=ArtifactLifecycleStatus.DRAFT)
    monkeypatch.setattr(studio_app, "get_artifact_store", lambda: store)

    client = TestClient(studio_app.app)
    response = client.post("/api/studio/build/artifacts/av_child_1/reject")

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
    sys.modules.pop("factory_app", None)

    from mozaiksai.hosts import studio as studio_app

    store = _build_review_store(tmp_path, lifecycle_status=ArtifactLifecycleStatus.CURRENT)
    runtime_root = tmp_path / "runtime_app"
    monkeypatch.setattr(studio_app, "get_artifact_store", lambda: store)
    monkeypatch.setattr(studio_app, "resolve_app_root", lambda: runtime_root)

    client = TestClient(studio_app.app)
    response = client.post("/api/studio/build/artifacts/av_child_1/promote")

    assert response.status_code == 200
    body = response.json()
    assert body["promoted"] is True
    assert body["target_path"] == str(runtime_root)
    assert (runtime_root / "GeneratedApp" / "src" / "App.jsx").exists()
    assert store.update_calls[-1]["status"] == RefinementSessionStatus.PROMOTED


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
        return CodingWorkerResult.model_validate(result)

    return _run

