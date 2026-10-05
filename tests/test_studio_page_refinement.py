from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from mozaiksai.control_plane import CodingWorkerResult, ControlPlaneConfig
from mozaiksai.control_plane.implementations.contract_surface_planner import (
    ContractSurfaceClassification,
)
from mozaiksai.control_plane.implementations.surface_regeneration_worker import (
    SurfaceRegenerationResponse,
)
from tests.test_studio_workflow_trigger import (
    _BINDING,
    _artifact_version,
    _async_classifier,
    _BaselineStore,
    _make_bundle_zip,
    _owned_build_target,  # noqa: F401 -- shared owner/archive/session fixture
)

_PAGE = "ui/pages/custom/dashboard.jsx"


@pytest.fixture
def page_refinement(monkeypatch, tmp_path, request):
    from mozaiksai.core.auth import reset_auth_adapter
    from mozaiksai.hosts import studio

    registry = request.getfixturevalue("_owned_build_target")
    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")
    reset_auth_adapter()
    files = {
        "app.json": json.dumps({"appId": "app_1", "name": "Example"}),
        "ui/route_manifest.json": json.dumps({"pages": [
            {"id": "Dashboard", "path": "/", "component": "Dashboard"},
            {"id": "Help", "path": "/help", "component": "Help"},
        ]}),
        "ui/index.js": (
            "import Dashboard from './pages/custom/dashboard.jsx';\n"
            "import Help from './pages/custom/help.jsx';\n"
            "export function register(registerComponent) {\n"
            "  registerComponent('Dashboard', Dashboard);\n"
            "  registerComponent('Help', Help);\n"
            "}\n"
        ),
        _PAGE: 'export default function Dashboard() { return <h1>Saved dashboard</h1>; }',
        "ui/pages/custom/help.jsx": 'export default function Help() { return <h1>Help</h1>; }',
    }
    archive = tmp_path / "registered-page.zip"
    _make_bundle_zip(archive, files)
    version = _artifact_version(artifact_version_id="av_page_1", zip_path=archive)
    _BaselineStore.versions[version.id] = version

    class Store(_BaselineStore):
        async def create_change_request(self, **kwargs):
            return SimpleNamespace(id="cr_page_1")

        async def create_refinement_session(self, **kwargs):
            return SimpleNamespace(id="rs_page_1")

        async def invalidate_artifact_version_refs(self, **kwargs):
            return []

    monkeypatch.setattr(studio, "get_artifact_store", Store)
    harness = studio.get_orchestration_control_harness()
    monkeypatch.setattr(harness, "_config_loader", lambda: ControlPlaneConfig(
        enabled=True, classifier={"enabled": True}, coding={"enabled": True},
        contract_surface={"enabled": True},
    ))
    classifier = AsyncMock(side_effect=_async_classifier(
        change_class="design", rationale="Improve the existing page layout", confidence=0.95, signals=[],
    ))
    monkeypatch.setattr(harness._refinement_resolver, "_classifier", SimpleNamespace(classify=classifier))
    classification = {
        "surfaces": [{
            "kind": "page_binding", "target_id": "Dashboard", "target_kind": "page",
            "rationale": "Restyle the registered dashboard", "confidence": 0.95,
            "generation_hint": "Make the dashboard easier to read",
        }],
        "summary": "Improve dashboard layout", "confidence": 0.95,
        "requires_schema_migration": False, "fallback_to_workflow": False, "fallback_reason": None,
    }
    planner = AsyncMock(side_effect=lambda **kwargs: ContractSurfaceClassification.model_validate(classification))
    generator = AsyncMock(return_value=SurfaceRegenerationResponse(
        summary="Improved layout", rationale="Clearer hierarchy",
        updated_files=[{"path": _PAGE, "content": 'export default function Dashboard() { return <h1>Clear dashboard</h1>; }'}],
    ))
    monkeypatch.setattr(harness._contract_surface_planner, "_agent_runner", SimpleNamespace(run=planner))
    monkeypatch.setattr(harness._surface_regeneration_worker, "_agent_runner", SimpleNamespace(run=generator))
    finalized = []

    async def finalize(**kwargs):
        finalized.append(kwargs)
        child_archive = tmp_path / "page-child.zip"
        _make_bundle_zip(child_archive, {**kwargs["workspace_files"], **kwargs["result"].all_files})
        child = _artifact_version(
            artifact_version_id="av_page_child", zip_path=child_archive, parent_version_id=version.id,
        )
        _BaselineStore.versions[child.id] = child
        return CodingWorkerResult(eligible=True, status="validated", metadata={"build_record_id": child.id})

    monkeypatch.setattr(harness, "finalize_surface_output", finalize)
    launcher = AsyncMock(side_effect=AssertionError("Selected-page refinement cannot launch a wider workflow"))
    monkeypatch.setattr(studio, "prepare_routed_workflow_launch", launcher)
    payload = {
        "build_registry_id": "appreg_1", "trigger_source": "refinement",
        "trigger_payload": {
            "refinement_request": {
                "build_family": "app_bundle", "build_key": "app_bundle", "build_record_id": version.id,
                "raw_user_request": "Make this dashboard easier to use on a phone", "source_surface": "app_workbench",
            },
            "coding_request": {"files": {_PAGE: "UNTRUSTED_BROWSER_CONTENT"}},
            "workspace_files": {"ui/index.js": "UNTRUSTED_BROWSER_REGISTRY"},
        },
    }
    return SimpleNamespace(
        client=TestClient(studio.app), payload=payload, files=files, archive=archive,
        classification=classification, classifier=classifier, planner=planner, generator=generator,
        finalized=finalized, launcher=launcher, registry=registry,
    )


def test_selected_page_design_uses_verified_registry_and_bounded_worker(page_refinement):
    case = page_refinement
    response = case.client.post("/api/workflows/trigger", json=case.payload)
    assert response.status_code == 200, response.text
    assert response.json()["execution_mode"] == "surface_regeneration"
    assert response.json()["surface_result"]["status"] == "success"
    case.planner.assert_awaited_once()
    case.generator.assert_awaited_once()
    prompt = case.generator.await_args.kwargs["user_prompt"]
    assert "Saved dashboard" in prompt
    assert "UNTRUSTED_BROWSER" not in prompt
    assert "ui/route_manifest.json" in prompt and "ui/index.js" in prompt
    assert case.finalized[0]["workspace_files"] == case.files
    assert set(case.finalized[0]["result"].all_files) == {_PAGE}
    assert case.finalized[0]["run_build_binding"] == _BINDING
    case.registry.begin_refinement_run.assert_awaited_once()
    case.registry.promote_build.assert_not_awaited()
    case.launcher.assert_not_awaited()


@pytest.mark.parametrize("unresolved", ["unknown_page", "outside_scope", "migration", "fallback", "planner_failure"])
def test_selected_page_design_never_falls_through_to_unapproved_generation(page_refinement, unresolved):
    case = page_refinement
    if unresolved in {"unknown_page", "outside_scope"}:
        case.classification["surfaces"][0]["target_id"] = "Missing" if unresolved == "unknown_page" else "Help"
    elif unresolved == "migration":
        case.classification["requires_schema_migration"] = True
    elif unresolved == "fallback":
        case.classification["fallback_to_workflow"] = True
    else:
        case.planner.side_effect = RuntimeError("Planner unavailable")
    response = case.client.post("/api/workflows/trigger", json=case.payload)
    assert response.status_code == 409, response.text
    assert "selected files" in response.json()["detail"]
    case.generator.assert_not_awaited()
    case.registry.begin_refinement_run.assert_not_awaited()
    case.launcher.assert_not_awaited()
    assert not case.finalized


def test_selected_page_corrupt_archive_stops_before_classification_or_planning(page_refinement):
    case = page_refinement
    case.archive.write_bytes(b"changed after save")
    response = case.client.post("/api/workflows/trigger", json=case.payload)
    assert response.status_code == 409, response.text
    case.classifier.assert_not_awaited()
    case.planner.assert_not_awaited()
    case.generator.assert_not_awaited()
    case.registry.begin_refinement_run.assert_not_awaited()
    case.launcher.assert_not_awaited()
