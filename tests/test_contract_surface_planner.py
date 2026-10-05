"""Tests for the contract surface planner.

Covers:
- ContractSurface contract types and canonical paths
- ContractSurfacePlan and ContractSurfaceUpdate models
- ContractSurfacePlanner eligibility and surface resolution
- HarnessDecision for_contract_surface_plan (targeted_regeneration and fallback)
- Dependency ordering
- Path resolution with and without workspace context
"""

from __future__ import annotations

import json
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from mozaiksai.control_plane.config import ControlPlaneCapabilityConfig, ControlPlaneConfig
from mozaiksai.control_plane.contracts import (
    CONTRACT_SURFACE_CANONICAL_PATHS,
    CONTRACT_SURFACE_DEPENDENCY_ORDER,
    ContractSurfacePlan,
    ContractSurfaceUpdate,
    HarnessDecision,
)
from mozaiksai.control_plane.implementations.contract_surface_planner import (
    ContractSurfaceClassification,
    ContractSurfacePlanner,
    resolve_contract_surface_paths,
    validate_contract_surface_plan,
)
from mozaiksai.control_plane.implementations.refinement_router import (
    ArtifactKind,
    ChangeClass,
    ChangeIntent,
    ImpactSet,
    RefinementRequest,
    RefinementRoutingDecision,
)
from mozaiksai.control_plane.schema import (
    AG2_CHECKPOINT_OUTPUT_CONTRACTS,
    ControlPlaneCheckpointManifest,
    ControlPlaneManifest,
    ControlPlanePromptDefinition,
    ControlPlanePromptsManifest,
    ControlPlaneToolsManifest,
    LoadedControlPlanePack,
)

# ---------------------------------------------------------------------------
# Contract type smoke tests
# ---------------------------------------------------------------------------


def test_contract_surface_canonical_paths_all_kinds_have_entries():
    expected_kinds = {
        "module_action",
        "module_contract",
        "page_binding",
        "data_schema",
        "workflow_tool",
        "workflow_agent",
        "ui_component",
        "app_config",
    }
    assert set(CONTRACT_SURFACE_CANONICAL_PATHS.keys()) == expected_kinds


def test_contract_surface_canonical_paths_target_id_substitution():
    paths = CONTRACT_SURFACE_CANONICAL_PATHS["module_action"]
    resolved = [p.replace("{target_id}", "projects") for p in paths]
    assert "modules/projects/module.yaml" in resolved
    assert "modules/projects/backend/handler.py" in resolved
    assert "modules/projects/backend/service.py" in resolved
    assert "modules/projects/backend/schemas.py" in resolved


def test_contract_surface_dependency_order_schema_before_action():
    assert CONTRACT_SURFACE_DEPENDENCY_ORDER["data_schema"] < CONTRACT_SURFACE_DEPENDENCY_ORDER["module_action"]


def test_contract_surface_dependency_order_action_before_page():
    assert CONTRACT_SURFACE_DEPENDENCY_ORDER["module_action"] < CONTRACT_SURFACE_DEPENDENCY_ORDER["page_binding"]


def test_contract_surface_dependency_order_tool_before_agent():
    assert CONTRACT_SURFACE_DEPENDENCY_ORDER["workflow_tool"] <= CONTRACT_SURFACE_DEPENDENCY_ORDER["workflow_agent"]


# ---------------------------------------------------------------------------
# ContractSurfacePlan model
# ---------------------------------------------------------------------------


def test_contract_surface_plan_empty_surfaces():
    plan = ContractSurfacePlan(
        surfaces=[],
        summary="nothing to update",
        fallback_to_workflow=True,
        fallback_reason="too broad",
        confidence=0.0,
    )
    assert plan.fallback_to_workflow is True
    assert plan.surfaces == []


def test_contract_surface_update_model():
    update = ContractSurfaceUpdate(
        kind="module_action",
        target_id="projects",
        target_kind="module",
        affected_paths=["modules/projects/module.yaml", "modules/projects/backend/handler.py"],
        dependency_order=2,
        rationale="add export action",
        confidence=0.9,
        generation_hint="Add export_projects action to module.yaml and handler.py",
    )
    assert update.kind == "module_action"
    assert update.target_id == "projects"
    assert "modules/projects/module.yaml" in update.affected_paths


# ---------------------------------------------------------------------------
# ContractSurfacePlanner eligibility
# ---------------------------------------------------------------------------


def test_planner_eligible_feature_app_bundle():
    planner = ContractSurfacePlanner.__new__(ContractSurfacePlanner)
    assert planner.eligible(change_class="feature", build_family="app_bundle") is True


def test_planner_eligible_design_app_bundle():
    planner = ContractSurfacePlanner.__new__(ContractSurfacePlanner)
    assert planner.eligible(change_class="design", build_family="app_bundle") is True


def test_planner_not_eligible_patch():
    planner = ContractSurfacePlanner.__new__(ContractSurfacePlanner)
    assert planner.eligible(change_class="patch", build_family="app_bundle") is False


def test_planner_not_eligible_core():
    planner = ContractSurfacePlanner.__new__(ContractSurfacePlanner)
    assert planner.eligible(change_class="core", build_family="app_bundle") is False


def test_planner_not_eligible_unknown_artifact():
    planner = ContractSurfacePlanner.__new__(ContractSurfacePlanner)
    assert planner.eligible(change_class="feature", build_family="concept") is False


# ---------------------------------------------------------------------------
# Surface resolution
# ---------------------------------------------------------------------------


def _make_classification(**kwargs: Any) -> ContractSurfaceClassification:
    defaults = {
        "surfaces": [],
        "summary": "test",
        "confidence": 0.9,
        "requires_schema_migration": False,
        "fallback_to_workflow": False,
        "fallback_reason": None,
    }
    defaults.update(kwargs)
    return ContractSurfaceClassification.model_validate(defaults)


def test_checkpoint_contract_name_resolves_to_public_model() -> None:
    assert (
        AG2_CHECKPOINT_OUTPUT_CONTRACTS["contract_surface_requested"]
        == ContractSurfaceClassification.__name__
    )


class _FakeAgentRunner:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.calls: list[dict[str, Any]] = []

    async def run(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return kwargs["response_schema"].model_validate(self.payload)


def _enabled_contract_surface_config() -> ControlPlaneConfig:
    return ControlPlaneConfig(
        enabled=True,
        contract_surface=ControlPlaneCapabilityConfig(
            enabled=True,
            llm_config={"model": "gpt-5.2-codex", "temperature": 0.1},
        ),
    )


def _planner_pack() -> LoadedControlPlanePack:
    return LoadedControlPlanePack(
        path="factory_app/refinement_harness",
        manifest=ControlPlaneManifest(
            schema_version="mozaiks.refinement_harness.v1",
            checkpoints=[
                ControlPlaneCheckpointManifest(
                    event="contract_surface_requested",
                    prompt_id="contract_surface_selection_system",
                )
            ],
        ),
        prompts=ControlPlanePromptsManifest(
            schema_version="mozaiks.refinement_harness.v1.prompts",
            prompts=[
                ControlPlanePromptDefinition(
                    id="contract_surface_selection_system",
                    content="contract surface prompt from pack",
                )
            ],
        ),
        tools=ControlPlaneToolsManifest(schema_version="mozaiks.refinement_harness.tools.v1"),
    )


def _planner_request() -> RefinementRequest:
    return RefinementRequest(
        request_kind="refinement",
        artifact_kind=ArtifactKind.APP_BUNDLE,
        artifact_key="app_bundle",
        artifact_version_id="av_123",
        raw_user_request="Add exports to the projects module and page.",
        app_id="app_1", user_id="user_1",
        requested_workflow_id="AppGenerator",
    )


def _planner_routing_decision() -> RefinementRoutingDecision:
    request = _planner_request()
    return RefinementRoutingDecision(
        workflow_id="AppGenerator",
        workflow_sequence="app_revision",
        refinement_request=request,
        change_intent=ChangeIntent(
            change_class=ChangeClass.FEATURE,
            source="llm",
            signals=["new_action"],
            rationale="Adds a new module action and page binding.",
            confidence=0.9,
            touches_app_bundle=True,
        ),
        impact_set=ImpactSet(
            workflow_sequence="app_revision",
            affected_workflows=["AppGenerator"],
            affected_declarative_families=["app_bundle"],
            requires_replanning=True,
            requires_rebuild=True,
            restart_from="AppGenerator",
            scope_summary="Feature update in app bundle.",
        ),
    )


@pytest.mark.asyncio
async def test_propose_uses_ag2_runner_for_contract_surface_classification():
    agent_runner = _FakeAgentRunner(
        {
            "surfaces": [
                {
                    "kind": "module_action",
                    "target_id": "projects",
                    "target_kind": "module",
                    "rationale": "add export action",
                    "confidence": 0.9,
                    "generation_hint": "add export_projects action",
                }
            ],
            "summary": "Add export support to projects.",
            "confidence": 0.9,
            "requires_schema_migration": False,
            "fallback_to_workflow": False,
            "fallback_reason": None,
        }
    )
    planner = ContractSurfacePlanner(
        agent_runner=agent_runner,
        config_loader=_enabled_contract_surface_config,
        pack_loader=_planner_pack,
    )

    plan = await planner.propose(
        refinement_request=_planner_request(),
        routing_decision=_planner_routing_decision(),
        context_graph_catalog=None,
        workspace_files=_saved_bundle(),
    )

    assert plan.fallback_to_workflow is False
    assert plan.summary == "Add export support to projects."
    assert plan.surfaces[0].kind == "module_action"
    assert plan.surfaces[0].target_id == "projects"
    assert agent_runner.calls[0]["agent_name"] == "ContractSurfacePlanner"
    assert agent_runner.calls[0]["system_prompt"] == "contract surface prompt from pack"
    assert agent_runner.calls[0]["llm_config"] == {"model": "gpt-5.2-codex", "temperature": 0.1}


def _saved_bundle() -> dict[str, str]:
    return {
        "app.json": json.dumps({"appId": "app_1"}),
        "modules/projects/module.yaml": "module:\n  id: projects\n",
        "modules/projects/backend/handler.py": "class Projects: pass",
        "modules/projects/backend/schemas.py": "class ExportRequest: pass",
        "ui/pages/projects.yaml": yaml.safe_dump({
            "schema_version": "mozaiks.app_page.v1", "name": "projects", "route": "/projects",
            "title": "Projects", "page_type": "record_list", "layout": "full-width", "sections": [{"id": "heading", "primitive": "PageHeader", "config": {"title": "Projects"}}],
        }),
        "ui/route_manifest.json": json.dumps({"pages": [
            {"id": "Focus", "path": "/", "component": "Focus"},
            {"path": "/projects", "component": "SchemaPage", "schema": "projects"},
        ]}),
        "ui/index.js": "import { FocusPage as FocusView } from './pages/custom/focus.jsx';\nregisterComponent('Focus', FocusView);",
        "ui/pages/custom/focus.jsx": "export function FocusPage() { return <main>Focus</main>; }",
    }


def _entry(kind="page_binding", target_id="Focus", target_kind="page") -> dict[str, Any]:
    return dict(kind=kind, target_id=target_id, target_kind=target_kind,
                rationale="Update saved surface", confidence=0.9, generation_hint="Improve the layout")


def _resolve(files=None, *, kind="page_binding", target_id="Focus", target_kind="page", family="app_bundle"):
    return resolve_contract_surface_paths(
        kind=kind, target_id=target_id, target_kind=target_kind, build_family=family,
        workspace_files=_saved_bundle() if files is None else files, artifact_app_id="app_1",
    )


def _plan(**changes) -> ContractSurfacePlan:
    payload = dict(surfaces=[dict(**_entry(), affected_paths=["ui/pages/custom/focus.jsx"])],
                   summary="Improve focus", change_class="feature", build_family="app_bundle")
    payload.update(changes)
    return ContractSurfacePlan.model_validate(payload)


def _admit(plan=None, *, files=None, allowed_paths=None):
    validate_contract_surface_plan(
        plan=plan or _plan(), refinement_request=_planner_request(), routing_decision=_planner_routing_decision(),
        workspace_files=_saved_bundle() if files is None else files, allowed_paths=allowed_paths,
    )


def test_custom_page_uses_exact_registered_alias_and_lowercase_filename():
    assert _resolve() == ["ui/pages/custom/focus.jsx"]
    _admit(allowed_paths=["ui/pages/custom/focus.jsx"])


@pytest.mark.parametrize("target", ["custom/focus", "focus", "../Focus", "Focus/extra", "Missing", " Focus"])
def test_custom_page_rejects_guessed_or_unsafe_identity(target):
    with pytest.raises(ValueError):
        _resolve(target_id=target)


def test_schema_page_and_module_paths_resolve_only_saved_contracts():
    assert _resolve(target_id="projects") == ["ui/pages/projects.yaml"]
    assert _resolve(kind="module_action", target_kind="module", target_id="projects") == [
        "modules/projects/module.yaml", "modules/projects/backend/handler.py", "modules/projects/backend/schemas.py",
    ]


@pytest.mark.parametrize("suffix", [".yaml", ".yml", "/page.yaml", "/page.yml"])
def test_schema_page_uses_runtime_supported_source_layout(suffix):
    files = _saved_bundle()
    files["ui/pages/projects" + suffix] = files.pop("ui/pages/projects.yaml")
    assert _resolve(files, target_id="projects") == ["ui/pages/projects" + suffix]


@pytest.mark.parametrize("suffix", [".yaml", ".yml", "/page.yaml", "/page.yml"])
def test_schema_page_display_name_preserves_runtime_file_identity(tmp_path, suffix):
    from mozaiksai.core.runtime.app.page_schema import load_app_page_schemas

    path = "ui/pages/support_tickets" + suffix
    files = {path: yaml.safe_dump({
        "schema_version": "mozaiks.app_page.v1", "name": "Support Tickets",
        "route": "/support", "title": "Support", "page_type": "record_list",
        "layout": "full-width", "sections": [{"id": "heading", "primitive": "PageHeader",
                                                "config": {"title": "Support"}}],
    })}
    saved = tmp_path / path
    saved.parent.mkdir(parents=True)
    saved.write_text(files[path], encoding="utf-8")
    assert list(load_app_page_schemas(tmp_path)) == ["support_tickets"]
    assert _resolve(files, target_id="support_tickets") == [path]
    with pytest.raises(ValueError):
        _resolve(files, target_id="SupportTickets")


@pytest.mark.asyncio
async def test_schema_page_planner_advertises_runtime_identity_not_display_name():
    files = _saved_bundle()
    page = yaml.safe_load(files["ui/pages/projects.yaml"])
    page["name"] = "Projects"
    files["ui/pages/projects.yaml"] = yaml.safe_dump(page)
    runner = _FakeAgentRunner(_make_classification(surfaces=[_entry(target_id="projects")]).model_dump())
    planner = ContractSurfacePlanner(agent_runner=runner, config_loader=_enabled_contract_surface_config,
                                     pack_loader=_planner_pack)
    plan = await planner.propose(refinement_request=_planner_request(),
                                 routing_decision=_planner_routing_decision(), workspace_files=files,
                                 allowed_paths=["ui/pages/projects.yaml"])
    assert plan.fallback_to_workflow is False
    assert plan.surfaces[0].affected_paths == ["ui/pages/projects.yaml"]
    prompt = runner.calls[0]["user_prompt"]
    payload = json.loads(prompt.split("payload_json:\n", 1)[1].split("\n\nReturn", 1)[0])
    assert {"kind": "page_binding", "target_kind": "page", "target_id": "projects",
            "affected_paths": ["ui/pages/projects.yaml"]} in payload["available_targets"]
    assert not any(target["target_id"] == "Projects" for target in payload["available_targets"])


def test_schema_page_duplicate_realizations_rejected():
    files = _saved_bundle()
    files["ui/pages/projects/page.yaml"] = files["ui/pages/projects.yaml"]
    with pytest.raises(ValueError, match="exactly one"):
        _resolve(files, target_id="projects")


@pytest.mark.parametrize("kind", ["workflow_tool", "workflow_agent", "ui_component"])
def test_workflow_surfaces_require_saved_workflow_declaration(kind):
    files = {"workflows/Review/orchestrator.yaml": "workflow_name: Review"}
    paths = [path.replace("{target_id}", "Review") for path in CONTRACT_SURFACE_CANONICAL_PATHS[kind]]
    files.update({path: "saved: true" for path in paths})
    assert _resolve(files, kind=kind, target_kind="workflow", target_id="Review", family="workflow_bundle") == paths
    with pytest.raises(ValueError, match="requires workflow_bundle"):
        _resolve(files, kind=kind, target_kind="workflow", target_id="Review")


def test_app_config_requires_exact_saved_app_identity():
    assert _resolve(kind="app_config", target_kind="app", target_id="app_1") == ["app.json"]
    with pytest.raises(ValueError, match="artifact app identity"):
        _resolve(kind="app_config", target_kind="app", target_id="other")


def test_app_config_uses_bound_artifact_identity_when_manifest_omits_app_id():
    files = _saved_bundle()
    files["app.json"] = '{"name": "My app"}'
    plan = _plan(surfaces=[dict(**_entry("app_config", "app_1", "app"), affected_paths=["app.json"])])
    _admit(plan, files=files)
    prompt = ContractSurfacePlanner._build_user_prompt(
        request=_planner_request(), routing_decision=_planner_routing_decision(),
        context_graph_catalog=None, workspace_files=files, allowed_paths=None,
    )
    assert '"target_id": "app_1"' in prompt


def test_app_config_rejects_conflicting_manifest_identity():
    files = _saved_bundle()
    files["app.json"] = '{"appId": "different-app"}'
    plan = _plan(surfaces=[dict(**_entry("app_config", "app_1", "app"), affected_paths=["app.json"])])
    with pytest.raises(ValueError, match="conflicts"):
        _admit(plan, files=files)


def test_app_config_requires_authoritative_binding_not_manifest_alone():
    with pytest.raises(ValueError, match="authoritative"):
        resolve_contract_surface_paths(
            kind="app_config", target_id="app_1", target_kind="app", build_family="app_bundle",
            workspace_files=_saved_bundle(),
        )


@pytest.mark.parametrize("kind,target_kind", [
    ("ui_component", "page"), ("page_binding", "workflow"), ("module_action", "page"),
    ("app_config", "module"), ("not_a_surface", "module"),
])
def test_classifier_and_plan_share_finite_surface_target_pairs(kind, target_kind):
    entry = _entry(kind=kind, target_kind=target_kind)
    with pytest.raises(ValidationError):
        _make_classification(surfaces=[_entry(), entry])
    with pytest.raises(ValidationError):
        ContractSurfaceUpdate.model_validate(entry)


def test_empty_target_is_rejected_not_silently_skipped():
    with pytest.raises(ValidationError):
        _make_classification(surfaces=[_entry(target_id="")])


@pytest.mark.parametrize("missing", ["ui/route_manifest.json", "ui/index.js", "ui/pages/custom/focus.jsx"])
def test_custom_page_requires_all_saved_ownership_sources(missing):
    files = _saved_bundle()
    del files[missing]
    with pytest.raises(ValueError):
        _resolve(files)


@pytest.mark.parametrize("source", [
    "", "import Focus from './pages/custom/focus.jsx'; registerComponent(name, Focus);",
    "import Focus from './pages/custom/focus.jsx'; registerComponent('Focus', () => Focus);",
    "import Focus from './pages/custom/focus.jsx'; registerComponent('Focus', Focus); registerComponent('Focus', Focus);",
    "import Focus from './pages/custom/focus.jsx'; registerComponent('Focus', Focus); registerComponent(variable, Other);",
    "function Focus() {} registerComponent('Focus', Focus);",
    "import Focus from '@mozaiks/chat-ui'; registerComponent('Focus', Focus);",
    "import Focus from './missing.jsx'; registerComponent('Focus', Focus);",
    "import Focus from '../../ui/pages/custom/focus.jsx'; registerComponent('Focus', Focus);",
])
def test_custom_page_rejects_unresolved_or_ambiguous_registry(source):
    files = _saved_bundle()
    files["ui/index.js"] = source
    with pytest.raises(ValueError):
        _resolve(files)


def test_other_index_cannot_authorize_page_registration():
    files = _saved_bundle()
    files["workflows/Other/ui/index.js"] = files.pop("ui/index.js")
    with pytest.raises(ValueError, match="ui/index.js"):
        _resolve(files)


@pytest.mark.parametrize("component", ["LoginPage", "AuthCallbackPage", "ChatPage", "UnknownPage"])
def test_platform_components_do_not_become_custom_page_writes(component):
    files = _saved_bundle()
    files["ui/route_manifest.json"] = json.dumps({"pages": [{"id": "Focus", "path": "/", "component": component}]})
    with pytest.raises(ValueError):
        _resolve(files)


@pytest.mark.parametrize("duplicate", [
    {"id": "Focus", "path": "/other", "component": "Focus"},
    {"id": "Other", "path": "/", "component": "Focus"},
])
def test_duplicate_route_identity_or_path_is_ambiguous(duplicate):
    files = _saved_bundle()
    manifest = json.loads(files["ui/route_manifest.json"])
    manifest["pages"].append(duplicate)
    files["ui/route_manifest.json"] = json.dumps(manifest)
    with pytest.raises(ValueError, match="Ambiguous"):
        _resolve(files)


@pytest.mark.parametrize("path", ["../escape.jsx", "/absolute.jsx", "ui/../escape.jsx", "C:/escape.jsx", "ui\\escape.jsx"])
def test_saved_snapshot_requires_canonical_safe_paths(path):
    files = _saved_bundle()
    files[path] = "source"
    with pytest.raises(ValueError, match="unsafe"):
        _resolve(files)


@pytest.mark.parametrize("path", ["ui/pages/custom/focus.jsx", "ui/index.js"])
def test_empty_required_source_fails(path):
    files = _saved_bundle()
    files[path] = " "
    with pytest.raises(ValueError):
        _resolve(files)


@pytest.mark.parametrize("changes", [
    {"build_family": "workflow_bundle"}, {"change_class": "design"},
    {"fallback_to_workflow": True}, {"surfaces": []},
    {"surfaces": [dict(**_entry(), affected_paths=["ui/index.js"])]},
    {"surfaces": [dict(**_entry(), affected_paths=[])]},
])
def test_direct_plan_cannot_bypass_request_and_source_admission(changes):
    with pytest.raises(ValueError):
        _admit(_plan(**changes))


def test_plan_rejects_later_invalid_surface_without_partial_admission():
    plan = _plan(surfaces=[dict(**_entry(), affected_paths=["ui/pages/custom/focus.jsx"]),
                          dict(**_entry(target_id="Unknown"), affected_paths=["ui/pages/projects.yaml"])])
    with pytest.raises(ValueError):
        _admit(plan)


@pytest.mark.parametrize("allowed", [[], ["ui/pages/projects.yaml"], ["ui/pages/custom/focus.jsx", "ui/pages/custom/focus.jsx"], ["missing.jsx"]])
def test_explicit_allowed_paths_remain_hard_write_boundary(allowed):
    with pytest.raises(ValueError):
        _admit(allowed_paths=allowed)


def test_missing_snapshot_never_creates_guessed_paths():
    with pytest.raises(ValueError, match="verified saved bundle"):
        _resolve({})


def test_resolve_surfaces_dependency_ordering():
    classification = _make_classification(surfaces=[
        _entry(), _entry("module_action", "projects", "module"), _entry("data_schema", "projects", "module"),
    ])
    surfaces = ContractSurfacePlanner._resolve_surfaces(classification=classification, build_family="app_bundle", workspace_files=_saved_bundle())
    assert [surface.kind for surface in surfaces] == ["data_schema", "module_action", "page_binding"]


@pytest.mark.asyncio
async def test_planner_prompt_uses_verified_inventory_not_graph_write_authority():
    runner = _FakeAgentRunner(_make_classification(surfaces=[_entry()]).model_dump())
    planner = ContractSurfacePlanner(agent_runner=runner, config_loader=_enabled_contract_surface_config, pack_loader=_planner_pack)
    plan = await planner.propose(
        refinement_request=_planner_request(), routing_decision=_planner_routing_decision(),
        workspace_files=_saved_bundle(), allowed_paths=["ui/pages/custom/focus.jsx"],
        context_graph_catalog={"candidate_files": [{"path": "workflows/custom/focus/ui_config.yaml"}]},
    )
    assert plan.surfaces[0].affected_paths == ["ui/pages/custom/focus.jsx"]
    prompt = runner.calls[0]["user_prompt"]
    payload = json.loads(prompt.split("payload_json:\n", 1)[1].split("\n\nReturn", 1)[0])
    assert payload["allowed_paths"] == ["ui/pages/custom/focus.jsx"]
    assert {"kind": "page_binding", "target_kind": "page", "target_id": "Focus", "affected_paths": ["ui/pages/custom/focus.jsx"]} in payload["available_targets"]


@pytest.mark.asyncio
async def test_planner_rejects_missing_snapshot_before_classifier():
    runner = _FakeAgentRunner({})
    planner = ContractSurfacePlanner(agent_runner=runner, config_loader=_enabled_contract_surface_config, pack_loader=_planner_pack)
    with pytest.raises(ValueError):
        await planner.propose(refinement_request=_planner_request(), routing_decision=_planner_routing_decision(), workspace_files={})
    assert runner.calls == []


# ---------------------------------------------------------------------------
# HarnessDecision for_contract_surface_plan
# ---------------------------------------------------------------------------


def _make_routing_decision(change_class: str = "feature", workflow_id: str = "AppGenerator") -> Any:
    from unittest.mock import MagicMock

    from mozaiksai.control_plane.implementations.refinement_router import ChangeClass

    intent = MagicMock()
    intent.change_class = ChangeClass(change_class)
    intent.rationale = "user wants to add export controls"
    intent.confidence = 0.9

    rd = MagicMock()
    rd.change_intent = intent
    rd.workflow_id = workflow_id
    rd.workflow_sequence = "app_revision"
    return rd


def test_for_contract_surface_plan_targeted_regeneration():
    from mozaiksai.control_plane.implementations.harness_decision import (
        FirstPartyHarnessDecisionPolicy,
    )

    policy = FirstPartyHarnessDecisionPolicy.__new__(FirstPartyHarnessDecisionPolicy)
    plan = ContractSurfacePlan(
        surfaces=[
            ContractSurfaceUpdate(
                kind="module_action",
                target_id="projects",
                target_kind="module",
                affected_paths=["modules/projects/module.yaml"],
                dependency_order=2,
                rationale="add export action",
                confidence=0.9,
            ),
            ContractSurfaceUpdate(
                kind="page_binding",
                target_id="projects",
                target_kind="page",
                affected_paths=["ui/pages/projects.yaml"],
                dependency_order=3,
                rationale="add export section",
                confidence=0.85,
            ),
        ],
        summary="Add export_projects action and expose on projects page.",
        change_class="feature",
        artifact_kind="app_bundle",
        confidence=0.88,
        fallback_to_workflow=False,
    )
    routing_decision = _make_routing_decision()
    decision: HarnessDecision = policy.for_contract_surface_plan(
        routing_decision=routing_decision,
        plan=plan,
    )
    assert decision.decision_type == "targeted_regeneration"
    assert decision.metadata.get("contract_surface_plan") is not None
    assert len(decision.actions) == 2
    action_ids = [a.action_id for a in decision.actions]
    assert "review_surface_plan" in action_ids
    assert "apply_surface_plan" in action_ids


def test_for_contract_surface_plan_fallback_to_workflow_reentry():
    from mozaiksai.control_plane.implementations.harness_decision import (
        FirstPartyHarnessDecisionPolicy,
    )

    policy = FirstPartyHarnessDecisionPolicy.__new__(FirstPartyHarnessDecisionPolicy)
    plan = ContractSurfacePlan(
        summary="Change too broad.",
        change_class="feature",
        artifact_kind="app_bundle",
        fallback_to_workflow=True,
        fallback_reason="confidence below threshold",
        confidence=0.45,
    )
    routing_decision = _make_routing_decision()
    decision: HarnessDecision = policy.for_contract_surface_plan(
        routing_decision=routing_decision,
        plan=plan,
    )
    assert decision.decision_type == "workflow_reentry"
    assert decision.metadata.get("contract_surface_fallback") is True


def test_for_contract_surface_plan_empty_surfaces_falls_back():
    from mozaiksai.control_plane.implementations.harness_decision import (
        FirstPartyHarnessDecisionPolicy,
    )

    policy = FirstPartyHarnessDecisionPolicy.__new__(FirstPartyHarnessDecisionPolicy)
    plan = ContractSurfacePlan(
        surfaces=[],
        summary="no surfaces resolved",
        change_class="feature",
        artifact_kind="app_bundle",
        fallback_to_workflow=False,  # planner didn't set fallback but surfaces are empty
        confidence=0.8,
    )
    routing_decision = _make_routing_decision()
    decision: HarnessDecision = policy.for_contract_surface_plan(
        routing_decision=routing_decision,
        plan=plan,
    )
    # Empty surfaces should still fall back
    assert decision.decision_type == "workflow_reentry"
