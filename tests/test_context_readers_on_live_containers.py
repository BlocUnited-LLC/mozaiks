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
    """WorkflowBundleBuilderAgent is the task worker; `_build_task_context` sets `current_task` on its bridge."""
    from factory_app.workflows.AgentGenerator.tools.hook_workflow_archetypes_context import (
        inject_workflow_archetypes_context,
    )

    prompt = _run_hook(
        inject_workflow_archetypes_context,
        "WorkflowBundleBuilderAgent",
        {"current_task": {"task_id": "t1", "capability_id": "proposals-review-workflow"}},
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


def test_ui_quality_warning_append_keeps_earlier_warnings() -> None:
    from factory_app.workflows.AppGenerator.tools.hook_app_ui_quality_gate import _append_warning

    context = _bridge({"app_ui_quality_warnings": ["w1"]})
    _append_warning(context, "w2")

    assert detach(context.get("app_ui_quality_warnings")) == ["w1", "w2"]


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
