"""Every prompt hook, shared helper and runtime reader fixed here, driven through the live containers.

Four defects in one week shared a root: a reader that only works on a plain
dict. Live workflow context is never a plain dict. Prompt middleware receives
the `ContextVariablesBridge` itself (`execution/middleware.py` hands it to the
hook as `agent.context_variables`), tools receive a `StructuredOutputOverlay`
over that bridge, and before_chat lifecycle tools receive
`_RuntimeContextVariables`. All three freeze reads: a dict comes back as a
`MappingProxyType`, a list as a tuple, and no container exposes `.data`.

A hook that type-tests such a read does not crash. It takes the other branch,
injects nothing (or a `str(mappingproxy)` dump), and the build is quietly
poorer. That is why every case below seeds a real container and reads the
prompt or the result back, instead of handing the reader a dict.

The audit that produced this file called each reader both ways with the keys
it actually reads; the cases here are the ones whose behaviour differed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from mozaiksai.core.workflow.agents.factory import ContextVariablesBridge
from mozaiksai.core.workflow.context.adapter import _RuntimeContextVariables
from mozaiksai.core.workflow.context.frozen import detach
from mozaiksai.core.workflow.context.structured_output_overlay import StructuredOutputOverlay

BINDING = {"build_registry_id": "br_1", "target_app_id": "app_1", "build_id": "b_1", "phase": "genesis"}
BASE_PROMPT = "BASE PROMPT"


class _Capture:
    """What a prompt hook is handed: mirrors `_PromptCapture` in execution/middleware.py."""

    def __init__(self, name: str, context_variables: Any) -> None:
        self.name = name
        self.context_variables = context_variables
        self.system_message = BASE_PROMPT
        self._system_message = BASE_PROMPT

    def update_system_message(self, message: str) -> None:
        self.system_message = message
        self._system_message = message


def _bridge(seed: dict[str, Any]) -> ContextVariablesBridge:
    return ContextVariablesBridge(dict(seed))


def _overlay(seed: dict[str, Any]) -> StructuredOutputOverlay:
    return StructuredOutputOverlay(ContextVariablesBridge(dict(seed)), {})


def _runtime(seed: dict[str, Any]) -> _RuntimeContextVariables:
    return _RuntimeContextVariables(dict(seed))


def _run_hook(hook, agent_name: str, seed: dict[str, Any]) -> str:  # noqa: ANN001
    """Run a prompt hook exactly as the middleware runner does, on the bridge."""
    capture = _Capture(agent_name, _bridge(seed))
    hook(capture, [])
    return capture.system_message


# ---------------------------------------------------------------------------
# Middleware-registered hooks
# ---------------------------------------------------------------------------


def test_context_graph_hook_formats_the_pack_instead_of_dumping_its_repr() -> None:
    """Registered `all` in AgentGenerator, AppGenerator and ExistingAppDiscovery; every agent, every turn."""
    from factory_app.workflows._shared.context_graph.hook_context_graph import (
        inject_context_graph_context,
    )

    prompt = _run_hook(
        inject_context_graph_context,
        "AppPlanAgent",
        {"context_graph_pack": {"status": "loaded", "graph_id": "g1", "relevant_paths": ["app/a.py"],
                                "matched_nodes": [{"label": "Tasks", "node_id": "n1", "node_type": "module"}]}},
    )

    assert "Status: loaded" in prompt
    assert "- app/a.py" in prompt
    assert "- Tasks (module)" in prompt
    assert "mappingproxy(" not in prompt, "the formatter took the str() fallback on a frozen pack"


def test_managed_capabilities_hook_renders_each_pack_and_its_facades() -> None:
    from factory_app.workflows.AppGenerator.tools.hook_managed_capabilities_context import (
        inject_managed_capabilities_context,
    )

    pack = {
        "id": "mozaikspay", "display_name": "MozaiksPay", "capability_source": "managed_capability",
        "capabilities": [{"capability_id": "mozaikspay.billing_portal"}],
        "facades": [{"module_id": "billing_portal", "provider_module": "mozaikspay",
                     "provider_actions": ["billing_portal"], "pages": [{"name": "Billing", "route": "/billing"}]}],
        "pack_source_path": "C:/somewhere/local",
    }
    prompt = _run_hook(inject_managed_capabilities_context, "AppPlanAgent",
                       {"capability_packs": [pack], "operator_contracts": []})

    assert "[capability_pack_id: mozaikspay; capability_source: managed_capability]" in prompt
    assert "billing_portal" in prompt and "/billing" in prompt, "facade guidance is only rendered for dict packs"
    assert "mappingproxy(" not in prompt
    assert "pack_source_path" not in prompt, "a raw repr leaks the operator's filesystem path into the prompt"


def test_managed_capabilities_hook_stays_silent_without_packs() -> None:
    """`capability_packs: []` is the declared default; a frozen tuple is not 'empty' to `isinstance(.., list)`."""
    from factory_app.workflows.AppGenerator.tools.hook_managed_capabilities_context import (
        inject_managed_capabilities_context,
    )

    prompt = _run_hook(inject_managed_capabilities_context, "AppPlanAgent",
                       {"capability_packs": [], "operator_contracts": []})

    assert prompt == BASE_PROMPT


_INTEGRATION = {
    "generated_workflow_capability_id": "proposals-review-workflow",
    "generated_workflow_name": "ProposalsReview",
    "generated_workflow_startup_mode": "BackendOnly",
    "generated_workflow_trigger_events": [{"event_type": "domain.proposals.submitted", "source": "app"}],
}


@pytest.mark.parametrize("make", [_bridge, _overlay, _runtime], ids=["hook", "tool", "before_chat"])
def test_workflow_integration_metadata_keeps_trigger_events(make) -> None:  # noqa: ANN001
    from factory_app.workflows._shared.workflow_integration import (
        workflow_integration_metadata_from_context,
    )

    metadata = workflow_integration_metadata_from_context(make(_INTEGRATION))

    assert metadata is not None
    events = metadata["workflows"][0]["trigger_events"]
    assert [event["event_type"] for event in events] == ["domain.proposals.submitted"]


@pytest.mark.asyncio
async def test_before_chat_hydration_does_not_erase_trigger_events() -> None:
    """The AppGenerator before_chat tool read a tuple, built metadata with no events, and wrote [] back."""
    from factory_app.workflows._shared.workflow_integration import (
        hydrate_workflow_integration_context_from_latest_artifact,
    )

    context = _runtime({**_INTEGRATION, "run_build_binding": BINDING})

    result = await hydrate_workflow_integration_context_from_latest_artifact(context, artifact_store=object())

    assert result["status"] == "hydrated"
    after = detach(context.get("generated_workflow_trigger_events"))
    assert [event["event_type"] for event in after] == ["domain.proposals.submitted"]


def test_workflow_integration_contract_hook_renders_the_reaction_entries() -> None:
    from factory_app.workflows.AppGenerator.tools.hook_workflow_integration_contract import (
        inject_workflow_integration_contract,
    )

    prompt = _run_hook(inject_workflow_integration_contract, "ConfigMiddlewareAgent", _INTEGRATION)

    assert "event_type: domain.proposals.submitted" in prompt
    assert "capability_id: proposals-review-workflow" in prompt
    assert "(none declared" not in prompt, "the hook told ConfigMiddlewareAgent no reactions were needed"


@pytest.mark.parametrize("make", [_bridge, _overlay], ids=["hook", "auto_tool"])
def test_module_contract_gate_audits_the_live_code_files(make) -> None:  # noqa: ANN001
    """`code_files` arrived as a tuple, was treated as empty, and the gate passed every build."""
    from factory_app.workflows.AppGenerator.tools.review_module_contract_quality import (
        review_module_contract_quality,
    )

    context = make({
        "code_files": [{"filename": "modules/tasks/module.yaml", "content": "module:\n  id: tasks\n"}],
        "admin_registry": {"pages": []},
        "module_contract_quality_warnings": ["prior"],
    })

    result = review_module_contract_quality(context_variables=context)

    assert result["module_contract_count"] == 1
    assert result["status"] == "blocked"
    assert "prior" in result["warnings"]
    assert "('prior',)" not in result["warnings"], "a frozen warning list was stringified as a tuple"


def test_ai_pack_workflow_hook_detects_packs_from_the_plan_and_the_concept() -> None:
    from factory_app.workflows.AppGenerator.tools.hook_ai_pack_workflow_context import (
        inject_ai_pack_workflow_context,
    )

    prompt = _run_hook(
        inject_ai_pack_workflow_context,
        "AppPlanAgent",
        {"app_build_plan": {"capability_packs": [{"capability_pack_id": "reviews", "pack_type": "ai_review_pack"}]},
         "concept_blueprint": {"capability_pack_hints": ["ai_analysis_pack"]}},
    )

    assert "[AI PACK WORKFLOW CONTEXT]" in prompt
    assert "ai_review_pack detected" in prompt
    assert "ai_analysis_pack detected" in prompt


def test_ai_pack_archetype_hook_detects_workflow_surfaces() -> None:
    from factory_app.workflows.AgentGenerator.tools.hook_ai_pack_archetype_context import (
        inject_ai_pack_archetype_context,
    )

    prompt = _run_hook(
        inject_ai_pack_archetype_context,
        "PatternAgent",
        {"design_surface_map": {"surfaces": [
            {"surface_id": "proposals_review_workflow", "surface_kind": "workflow",
             "workflow_triggers": ["proposals-review-workflow"]},
        ]}},
    )

    assert "[AI PACK ARCHETYPE CONTEXT]" in prompt
    assert "proposals-review-workflow" in prompt


def test_workflow_archetype_hook_reads_the_task_worker_context() -> None:
    """The task worker's real shape: a WorkflowInPack item (no capability_id) plus design_surface_map (#723)."""
    from factory_app.workflows.AgentGenerator.tools.hook_workflow_archetypes_context import (
        inject_workflow_archetypes_context,
    )

    prompt = _run_hook(
        inject_workflow_archetypes_context,
        "WorkflowBundleBuilderAgent",
        {"current_task": {"name": "ProposalsReviewWorkflow", "role": "primary", "description": "d",
                          "pattern_id": 3, "pattern_name": "Feedback Loop",
                          "initial_agent": "WorkflowBundleBuilderAgent", "initial_message": "m"},
         "design_surface_map": {"surfaces": [{"surface_id": "proposals_review_workflow", "surface_kind": "workflow",
                                              "workflow_triggers": ["proposals-review-workflow"]}]}},
    )

    assert "[WORKFLOW ARCHETYPE]: ai_review" in prompt


def test_ai_pack_surface_hook_detects_packs_from_the_concept() -> None:
    from factory_app.workflows.DesignDocs.tools.hook_ai_pack_surface_context import (
        inject_ai_pack_surface_context,
    )

    prompt = _run_hook(inject_ai_pack_surface_context, "DesignDocsAgent",
                       {"concept_blueprint": {"capability_pack_hints": ["ai_review_pack"]}})

    assert "[AI PACK SURFACE DECLARATIONS]" in prompt
    assert "ai_review_pack detected" in prompt


def test_domain_scoring_reads_the_concept_from_a_live_container() -> None:
    from factory_app.workflows.AppGenerator.tools.hook_domain_catalog_context import (
        _collect_concept_text,
    )

    text = _collect_concept_text(
        _bridge({"concept_blueprint": {"app_name": "Invoice Ledger", "summary": "invoicing and payments"}}), [],
    )

    assert "invoice ledger" in text
    assert "invoicing and payments" in text


def test_ui_quality_review_keeps_earlier_warnings() -> None:
    from factory_app.workflows.AppGenerator.tools.ui_quality import review_ui_quality

    context = _bridge({"app_ui_quality_warnings": ["w1"]})
    result = review_ui_quality(context_variables=context)

    assert "w1" in result["warnings"]
    assert detach(context.get("app_ui_quality_warnings")) == result["warnings"]


def test_agent_backend_hook_renders_the_websocket_endpoints() -> None:
    from factory_app.workflows.AppGenerator.tools.hook_agent_backend_context import (
        inject_agent_backend_context,
    )

    prompt = _run_hook(
        inject_agent_backend_context,
        "AppPlanAgent",
        {"agent_websocket_url": "ws://x/ws", "websocket_config": {"endpoints": {"chat": {"path": "/ws/chat"}}}},
    )

    assert "- chat: /ws/chat" in prompt


# ---------------------------------------------------------------------------
# Runtime helpers that take context_variables
# ---------------------------------------------------------------------------

_PLAN = {
    "app_build_plan": {
        "external_integrations": [{"service": "stripe", "kind": "api_key", "provider": "stripe"}],
        "capability_packs": [{"capability_pack_id": "mail", "required_integrations": ["sendgrid"]}],
        "build_tasks": [{"task_id": "t1", "integration_needs": [{"service": "twilio"}]}],
    },
    "current_build_task": {"task_id": "t2", "integration_needs": [{"service": "slack"}]},
    "integration_needs": [{"service": "openai", "kind": "api_key"}],
}


def test_collect_integration_needs_reads_the_live_plan() -> None:
    from mozaiksai.core.workflow.generator_support.connector_request import (
        collect_integration_needs,
    )

    services = sorted(need["service"] for need in collect_integration_needs(_overlay(_PLAN)))

    assert services == ["sendgrid", "slack", "stripe", "twilio"]


@pytest.mark.asyncio
async def test_record_integration_need_keeps_earlier_needs() -> None:
    from mozaiksai.core.workflow.generator_support.connector_request import record_integration_need

    result = await record_integration_need(service="github", context_variables=_overlay(_PLAN))

    assert sorted(need["service"] for need in result["integration_needs"]) == ["github", "openai"]


def test_generated_integrations_yaml_lists_the_plan_requirements() -> None:
    from factory_app.workflows.AppGenerator.tools.materialize_app_config_contracts import (
        _materialize_integrations_yaml,
    )

    rendered = _materialize_integrations_yaml(app_id="app_1", context_variables=_overlay(_PLAN))

    assert "integration_id: stripe" in rendered
    assert "requirements: []" not in rendered


def test_media_asset_append_keeps_earlier_assets() -> None:
    from mozaiksai.core.media.middleware import _append_generated_assets_context

    context = _bridge({"generated_media_assets": [{"asset_id": "m1"}]})
    _append_generated_assets_context(context, [])

    assert detach(context.get("generated_media_assets")) == [{"asset_id": "m1"}]


def test_runtime_context_get_returns_plain_data() -> None:
    from mozaiksai.core.utils.context_vars import context_get

    value = context_get(_bridge({"items": [{"a": 1}]}), "items")

    assert value == [{"a": 1}]
    assert isinstance(value, list) and isinstance(value[0], dict)


# ---------------------------------------------------------------------------
# Tools the #712 sweep did not reach (its check was bound to two helper names)
# ---------------------------------------------------------------------------


def test_admin_registry_save_keeps_the_earlier_code_files() -> None:
    """`code_files` arrived as a tuple, failed the list test, and every earlier file was dropped."""
    from factory_app.workflows.AppGenerator.tools.save_admin_registry import save_admin_registry

    context = _overlay({"code_files": [{"filename": "modules/tasks/module.yaml", "content": "module:\n  id: tasks\n"}]})
    registry = {
        "schema_version": "mozaiks.admin.registry.v1",
        "pages": [{"id": "reports", "label": "Reports", "path": "/reports", "icon": "chart",
                   "scope": "app", "order": 10, "enabled": True}],
    }

    save_admin_registry(
        admin_registry=registry,
        code_files=[{"filename": "admin/admin_registry.yaml",
                     "content": "schema_version: mozaiks.admin.registry.v1\npages:\n  - id: reports\n"}],
        context_variables=context,
    )

    names = sorted(entry["filename"] for entry in detach(context.get("code_files")))
    assert names == ["admin/admin_registry.yaml", "modules/tasks/module.yaml"]


def test_discovery_reads_the_preloaded_intelligence_snapshot() -> None:
    from factory_app.workflows.ExistingAppDiscovery.tools import (
        source_context_retrieval as retrieval,
    )

    context = _overlay({"app_intelligence_snapshot": {"summary": "s"}})

    assert retrieval._app_intelligence_snapshot(context) == {"summary": "s"}


@pytest.mark.asyncio
async def test_discovery_reads_the_preloaded_source_bundle() -> None:
    """A frozen bundle failed the dict test and the tool fell through to a store lookup it could not make."""
    from factory_app.workflows.ExistingAppDiscovery.tools import (
        source_context_retrieval as retrieval,
    )

    context = _overlay({"source_context_bundle": {"files": [{"path": "a.py"}]}})

    assert await retrieval._source_context_bundle(context) == {"files": [{"path": "a.py"}]}


@pytest.mark.asyncio
async def test_integration_tests_resolve_the_in_context_generated_files() -> None:
    from factory_app.workflows.AppGenerator.tools.integration_tests import _resolve_files

    files, _, _ = await _resolve_files(
        files=None, context_variables=_overlay({"generated_files": {"app/a.py": "print(1)"}}),
    )

    assert files == {"app/a.py": "print(1)"}


# ---------------------------------------------------------------------------
# before_chat lifecycle tools, run by the real LifecycleToolManager on the
# production container: create_context_container() with the workflow's real
# authority policy and the runtime-system writer (context/variables.py).
# ---------------------------------------------------------------------------

_WORKFLOWS = Path(__file__).resolve().parents[1] / "factory_app" / "workflows"
_BASE = {"chat_id": "chat_1", "app_id": "app_1", "user_id": "u_1", "run_build_binding": BINDING}


def _production_container(workflow: str, seed: dict[str, Any]) -> Any:
    from mozaiksai.core.workflow.context.adapter import create_context_container
    from mozaiksai.core.workflow.context.authority import (
        RUNTIME_SYSTEM_WRITER,
        build_context_authority_policy,
    )
    from mozaiksai.core.workflow.context.variables import (
        _load_workflow_plan,
        _task_batch_context_keys,
    )
    from mozaiksai.core.workflow.workflow_manager import get_workflow_manager

    plan, _ = _load_workflow_plan(workflow)
    config = get_workflow_manager().get_config(workflow) or {}
    rules = (config.get("transition_graph") or {}).get("transition_rules") or []
    context = create_context_container(dict(seed))
    context._mozaiks_context_authority_policy = build_context_authority_policy(
        workflow_name=workflow, definitions=plan.definitions or {}, transition_rules=rules,
        task_batch_context_keys=_task_batch_context_keys(workflow),
    )
    context._mozaiks_context_writer_id = RUNTIME_SYSTEM_WRITER
    return context


def _run_before_chat_tool(
    monkeypatch: pytest.MonkeyPatch, workflow: str, function: str, context: Any,
) -> list[tuple[str, Any]]:
    """Load the workflow's lifecycle tools the way the runner does and run one; return the UI surfaces it emitted."""
    import asyncio

    import mozaiksai.core.workflow.ui_tools as ui_tools
    from mozaiksai.core.workflow.execution.lifecycle import LifecycleToolManager, LifecycleTrigger

    monkeypatch.setenv("MOZAIKS_WORKFLOWS_PATH", str(_WORKFLOWS))
    emitted: list[tuple[str, Any]] = []

    async def _emit(tool_id: str, payload: Any = None, **_: Any) -> str:
        emitted.append((tool_id, payload))
        return f"evt_{len(emitted)}"

    # Tool files are exec'd per load and bind emit_ui_surface at import, so patch before loading.
    monkeypatch.setattr(ui_tools, "emit_ui_surface", _emit)
    manager = LifecycleToolManager(workflow)
    manager.load_lifecycle_tools()
    tool = next(t for t in manager.tools[LifecycleTrigger.BEFORE_CHAT] if t.function == function)
    asyncio.run(tool.callable(context_variables=context))
    return emitted


def test_the_repo_access_recovery_card_emits_on_the_production_container(monkeypatch: pytest.MonkeyPatch) -> None:
    """It read repo_access_recovery frozen, _dict_value rejected it, and the card never showed (#723)."""
    context = _production_container("ExistingAppDiscovery", {
        **_BASE, "workflow_name": "ExistingAppDiscovery", "github_repo": "acme/ledger",
        "repo_access_recovery": {"provider": "github", "code": "github_repo_access_required",
                                 "github_repo": "acme/ledger", "http_status": 404,
                                 "recovery_actions": [{"id": "connect_github"}]},
    })

    emitted = _run_before_chat_tool(monkeypatch, "ExistingAppDiscovery", "emit_repo_access_recovery_card", context)

    assert [tool_id for tool_id, _ in emitted] == ["RepoAccessRecoveryCard"]
    assert emitted[0][1]["http_status"] == 404
    assert emitted[0][1]["recovery_actions"] == [{"id": "connect_github"}]


def test_the_overview_card_uses_the_catalog_on_the_production_container(monkeypatch: pytest.MonkeyPatch) -> None:
    """The collector writes app_intelligence_catalog; the card read it back frozen and showed the fallback."""
    catalog = {"schema_version": "mozaiks.app_intelligence.catalog.v1", "app_name": "Ledger"}
    context = _production_container("ExistingAppDiscovery", {
        **_BASE, "workflow_name": "ExistingAppDiscovery", "github_repo": "acme/ledger",
        "app_intelligence_catalog": catalog,
    })

    emitted = _run_before_chat_tool(monkeypatch, "ExistingAppDiscovery", "emit_app_intelligence_overview_card", context)

    assert [tool_id for tool_id, _ in emitted] == ["AppIntelligenceOverviewCard"]
    assert emitted[0][1]["app_intelligence_catalog"]["app_name"] == "Ledger"


def test_the_theme_collector_reads_the_parent_theme_on_the_production_container(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _production_container("ThemeCapture", {
        **_BASE, "workflow_name": "ThemeCapture",
        "parent_theme_config": {"identity": {"app_name": "Ledger"}, "theme": {"appearance": "light"},
                                "colors": {"primary": {"main": "#1144aa"}},
                                "fonts": {"body": {"family": "Inter"}}},
    })

    _run_before_chat_tool(monkeypatch, "ThemeCapture", "collect_prechat_theme_context", context)

    assert context.get("preload_status") == "ready", "the parent theme was ignored"
    evidence = detach(context.get("theme_capture_evidence"))
    assert evidence["sources"] == ["parent_theme_config"]
    assert "#1144aa" in evidence["colors"]


def test_the_discovery_collector_keeps_its_launch_inputs_on_the_production_container(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """discovery_inputs was read frozen and dropped, so a launch that passed its repo there indexed nothing."""
    (tmp_path / "package.json").write_text('{"name": "ledger", "dependencies": {"react": "18.0.0"}}', encoding="utf-8")
    context = _production_container("ExistingAppDiscovery", {
        **_BASE, "workflow_name": "ExistingAppDiscovery",
        "discovery_inputs": {"repo_path": str(tmp_path), "discovery_mode": "guided"},
    })

    emitted = _run_before_chat_tool(monkeypatch, "ExistingAppDiscovery", "collect_prechat_discovery_context", context)

    assert context.get("repo_path") == str(tmp_path)
    assert detach(context.get("repo_summary")).get("success") is True
    assert emitted, "the progress card read app_intelligence_progress back frozen and skipped every emission"
    assert all(tool_id == "AppIntelligenceProgressCard" for tool_id, _ in emitted)


# ---------------------------------------------------------------------------
# Data references read the fields their writer stores
# ---------------------------------------------------------------------------


def test_every_workflow_exports_reference_reads_a_field_the_export_writes() -> None:
    """generated_workflow_trigger_events projected `trigger_events`; the export stores `workflow_trigger_events`.

    _load_data_reference_value returns None when the document exists but lacks the field (the declared
    default applies only when there is no document), so every live AppGenerator run saw no trigger events,
    and the before_chat hydration then wrote [] and told ConfigMiddlewareAgent no reactions were needed.
    """
    import ast

    import yaml

    root = Path(__file__).resolve().parents[1]
    written = {"app_id", "appId", "user_id", "userId", "workflow_type", "workflowType", "repo_url", "repoUrl",
               "job_id", "jobId", "meta", "created_at_utc", "createdAt", "updated_at_utc", "updatedAt"}
    for path in (root / "factory_app").rglob("*.py"):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # build-context templates carry {{PLACEHOLDER}} tokens
            continue
        for node in ast.walk(tree):
            name = getattr(node, "func", None)
            name = getattr(name, "id", getattr(name, "attr", None))
            if isinstance(node, ast.Call) and name == "record_workflow_export":
                for keyword in node.keywords:
                    if keyword.arg == "extra_fields" and isinstance(keyword.value, ast.Dict):
                        written.update(k.value for k in keyword.value.keys if isinstance(k, ast.Constant))
    projected = []
    for path in sorted(_WORKFLOWS.glob("*/context_variables.yaml")):
        definitions = (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("definitions") or {}
        for name, definition in definitions.items():
            source = (definition or {}).get("source") or {}
            if source.get("type") == "data_reference" and source.get("collection") == "WorkflowExports":
                projected.extend((path.parent.name, name, field) for field in source.get("fields") or [])
    assert projected, "no WorkflowExports references found; the scan is broken"
    missing = [f"{wf}.{name} <- {field}" for wf, name, field in projected if field not in written]
    assert not missing, f"these read a field no record_workflow_export call writes: {missing}"


def test_the_archetype_worker_fixture_is_a_real_workflow_in_pack_item() -> None:
    """The archetype tests seed current_task; it must be the shape PatternAgent can actually emit."""
    from mozaiksai.core.workflow.outputs.structured import load_workflow_structured_outputs

    models, _ = load_workflow_structured_outputs("AgentGenerator")
    workflow_in_pack = models["WorkflowInPack"]
    item = {"name": "ProposalsReviewWorkflow", "role": "primary", "description": "d", "pattern_id": 3,
            "pattern_name": "Feedback Loop", "initial_agent": "WorkflowBundleBuilderAgent", "initial_message": "m"}

    workflow_in_pack.model_validate(item)
    with pytest.raises(Exception, match="extra_forbidden|Extra inputs"):
        workflow_in_pack.model_validate({**item, "capability_id": "proposals-review-workflow"})
