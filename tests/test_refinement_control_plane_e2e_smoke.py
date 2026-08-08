from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from mozaiksai.control_plane import (
    ControlPlaneConfig,
    RefinementTriggerRouteResolver,
    load_control_plane_config,
    load_refinement_harness,
)

APP_ROOT = Path(__file__).resolve().parents[1] / "factory_app" / "app"


class _FakeChangeClassifier:
    def __init__(self, *, change_class: str, rationale: str, signals: list[str] | None = None) -> None:
        self.calls: list[dict] = []
        self._result = SimpleNamespace(
            change_class=change_class,
            rationale=rationale,
            confidence=0.93,
            signals=signals or ["smoke_signal"],
        )

    async def classify(self, **kwargs):  # noqa: ANN003
        self.calls.append(kwargs)
        return self._result


def _resolver(classifier: _FakeChangeClassifier) -> RefinementTriggerRouteResolver:
    return RefinementTriggerRouteResolver(
        classifier=classifier,
        pack_loader=lambda: load_refinement_harness(app_root=APP_ROOT),
    )


def _base_manifest() -> list[dict[str, str]]:
    return [
        {"path": "app.json"},
        {"path": "config/shell.json"},
        {"path": "data/contract.json"},
        {"path": "data/migrations/001_initial.json"},
        {"path": "services/integrations/analytics_provider_client.py"},
        {"path": "modules/projects/module.yaml"},
        {"path": "modules/projects/contracts/events.yaml"},
        {"path": "modules/projects/contracts/admin.yaml"},
        {"path": "modules/projects/backend/handler.py"},
        {"path": "modules/projects/backend/service.py"},
        {"path": "modules/projects/backend/repo.py"},
        {"path": "modules/projects/backend/policy.py"},
        {"path": "modules/projects/backend/schemas.py"},
        {"path": "modules/reports/module.yaml"},
        {
            "path": "modules/reports/backend/service.py",
            "content": "CONNECTOR_ID = 'analytics_provider'\n",
        },
        {
            "path": "modules/reports/backend/schemas.py",
            "content": "connector_id = 'analytics_provider'\n",
        },
        {"path": "ui/pages/dashboard.yaml"},
        {"path": "ui/pages/reports.yaml"},
        {"path": "ui/route_manifest.json"},
        {"path": "ui/index.js"},
        {"path": ".env"},
    ]


def _managed_manifest() -> list[dict[str, str]]:
    return [
        {"path": "services/integrations/managed_analytics_client.py"},
        {"path": "modules/analytics_dashboard/module.yaml"},
        {"path": "modules/analytics_dashboard/backend/service.py"},
        {"path": "modules/analytics_dashboard/backend/handler.py"},
        {"path": "modules/analytics_dashboard/contracts/events.yaml"},
        {"path": "modules/managed_analytics/module.yaml"},
        {
            "path": "ui/pages/analytics.yaml",
            "content": "api_endpoint: /api/modules/analytics_dashboard/get_metrics\n",
        },
    ]


def _request_payload(raw_user_request: str, files_manifest: list[dict[str, str]]) -> dict:
    return {
        "refinement_request": {
            "build_family": "app_bundle",
            "build_record_id": "av_smoke_1",
            "raw_user_request": raw_user_request,
            "extra": {"files_manifest": files_manifest},
        }
    }


@pytest.mark.asyncio
async def test_refinement_control_plane_smoke_routes_experience_refinement_without_backend_paths() -> None:
    classifier = _FakeChangeClassifier(
        change_class="design",
        rationale="Dashboard experience revision stays in app surface planning.",
    )
    resolver = _resolver(classifier)
    request = resolver.request_from_payload(
        payload=_request_payload(
            "Change the dashboard experience to highlight reports first.",
            _base_manifest(),
        ),
        app_id="neutral_app",
        requested_workflow_id="AppGenerator",
    )

    assert request is not None
    decision = await resolver.route(request)

    assert len(classifier.calls) == 1
    assert decision.workflow_id == "DesignDocs"
    assert decision.workflow_sequence == "app_surface_revision"
    assert decision.impact_set.affected_declarative_families == ["experience_spec", "app_bundle"]
    assert "ui/pages/dashboard.yaml" in decision.impact_set.affected_bundle_paths
    assert "ui/route_manifest.json" in decision.impact_set.affected_bundle_paths
    assert "config/shell.json" not in decision.impact_set.affected_bundle_paths
    assert not any(path.startswith("modules/") for path in decision.impact_set.affected_bundle_paths)


@pytest.mark.asyncio
async def test_refinement_control_plane_smoke_scopes_module_backend_refinement_to_named_module() -> None:
    classifier = _FakeChangeClassifier(
        change_class="feature",
        rationale="Projects module API needs a scoped feature extension.",
    )
    resolver = _resolver(classifier)
    request = resolver.request_from_payload(
        payload=_request_payload(
            "Add an archive_project action to the projects module API.",
            _base_manifest(),
        ),
        app_id="neutral_app",
        requested_workflow_id="AppGenerator",
    )

    assert request is not None
    decision = await resolver.route(request)

    assert decision.workflow_sequence == "app_revision"
    for path in [
        "modules/projects/module.yaml",
        "modules/projects/backend/handler.py",
        "modules/projects/backend/service.py",
        "modules/projects/backend/repo.py",
        "modules/projects/backend/schemas.py",
    ]:
        assert path in decision.impact_set.affected_bundle_paths
    assert not any(path.startswith("modules/reports/") for path in decision.impact_set.affected_bundle_paths)
    assert "data/contract.json" not in decision.impact_set.affected_bundle_paths


@pytest.mark.asyncio
async def test_refinement_control_plane_smoke_marks_external_integration_readiness_impact() -> None:
    classifier = _FakeChangeClassifier(
        change_class="feature",
        rationale="Analytics provider connector behavior needs a scoped revision.",
    )
    resolver = _resolver(classifier)
    request = resolver.request_from_payload(
        payload=_request_payload(
            "Change the analytics_provider connector sync behavior.",
            _base_manifest(),
        ),
        app_id="neutral_app",
        requested_workflow_id="AppGenerator",
    )

    assert request is not None
    decision = await resolver.route(request)

    assert "services/integrations/analytics_provider_client.py" in decision.impact_set.affected_bundle_paths
    assert "modules/reports/backend/service.py" in decision.impact_set.affected_bundle_paths
    assert "modules/reports/backend/schemas.py" in decision.impact_set.affected_bundle_paths
    assert "Integration readiness may need to be rechecked." in decision.impact_set.scope_summary
    assert ".env" not in decision.impact_set.affected_bundle_paths
    assert not any("secret" in path.lower() for path in decision.impact_set.affected_bundle_paths)


@pytest.mark.asyncio
async def test_refinement_control_plane_smoke_marks_data_model_migration_review_impact() -> None:
    classifier = _FakeChangeClassifier(
        change_class="feature",
        rationale="Projects data model requires migration planning.",
    )
    resolver = _resolver(classifier)
    request = resolver.request_from_payload(
        payload=_request_payload(
            "Add a required project phase field and migrate existing project records.",
            _base_manifest(),
        ),
        app_id="neutral_app",
        requested_workflow_id="AppGenerator",
    )

    assert request is not None
    decision = await resolver.route(request)

    for path in [
        "data/contract.json",
        "data/migrations/001_initial.json",
        "modules/projects/module.yaml",
        "modules/projects/backend/schemas.py",
        "modules/projects/backend/repo.py",
        "modules/projects/backend/policy.py",
    ]:
        assert path in decision.impact_set.affected_bundle_paths
    assert "Data model migration impact detected." in decision.impact_set.scope_summary
    assert "Destructive changes require explicit review." in decision.impact_set.scope_summary


@pytest.mark.asyncio
async def test_refinement_control_plane_smoke_scopes_managed_capability_to_app_owned_facade() -> None:
    classifier = _FakeChangeClassifier(
        change_class="feature",
        rationale="Managed analytics dashboard display needs a scoped revision.",
    )
    resolver = _resolver(classifier)
    request = resolver.request_from_payload(
        payload=_request_payload(
            "Change managed analytics dashboard display.",
            _managed_manifest(),
        ),
        app_id="neutral_app",
        requested_workflow_id="AppGenerator",
    )

    assert request is not None
    decision = await resolver.route(request)

    assert "services/integrations/managed_analytics_client.py" in decision.impact_set.affected_bundle_paths
    assert "modules/analytics_dashboard/module.yaml" in decision.impact_set.affected_bundle_paths
    assert "modules/analytics_dashboard/backend/service.py" in decision.impact_set.affected_bundle_paths
    assert "ui/pages/analytics.yaml" in decision.impact_set.affected_bundle_paths
    assert "modules/managed_analytics/module.yaml" not in decision.impact_set.affected_bundle_paths


def test_refinement_control_plane_smoke_resolves_llm_profiles_without_provider_assumptions() -> None:
    config = load_control_plane_config(APP_ROOT)

    classifier_config = config.resolve_capability_llm_config("classifier")
    codegen_config = config.resolve_capability_llm_config("coding")

    assert classifier_config == config.llm_profiles["classifier"].llm_config
    assert codegen_config == config.llm_profiles["codegen"].llm_config

    missing_reference = ControlPlaneConfig.model_validate(
        {"enabled": True, "classifier": {"enabled": True, "llm_profile": "classifier"}}
    )
    with pytest.raises(ValueError, match="references unknown LLM profile 'classifier'"):
        missing_reference.resolve_capability_llm_config("classifier")


