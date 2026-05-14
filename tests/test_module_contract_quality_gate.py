"""
Tests for the module contract quality gate.

Covers:
- audit_module_contracts: YAML validation rules and cross-file consistency
- review_module_contract_quality: status routing and context variable wiring
- hook_module_contract_quality_gate: AG2 hook injection and no-op guard
- hooks.yaml: gate is registered for ModuleContractQualityAgent
- handoffs.yaml: ConfigMiddlewareAgent routes through the gate; gate routes to ModelAgent or user
- context_variables.yaml: module_contract_quality_status and warnings are declared
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List
from unittest.mock import patch

import yaml

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_APPGEN_DIR = (
    Path(__file__).parent.parent
    / "factory_app"
    / "workflows"
    / "AppGenerator"
)
_HOOKS_YAML = _APPGEN_DIR / "hooks.yaml"
_HANDOFFS_YAML = _APPGEN_DIR / "handoffs.yaml"
_CTX_YAML = _APPGEN_DIR / "context_variables.yaml"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_valid_module_yaml(module_id: str = "task_manager") -> str:
    return f"""\
schema_version: mozaiks.module.v1
id: {module_id}
handler: backend.handler:{module_id.title().replace('_', '')}Module
type: standard
actions:
  - id: create_task
    handler_method: create_task
    permissions: []
    emits: []
"""


def _make_valid_events_yaml() -> str:
    return """\
schema_version: mozaiks.events.v1
events:
  - type: domain.task_manager.task_created
    version: 1
    producer: task_manager
    payload_schema:
      task_id:
        type: string
"""


def _code_files(*pairs: tuple) -> List[Dict[str, Any]]:
    """Build a code_files list from (filename, content) pairs."""
    return [{"filename": fn, "content": c} for fn, c in pairs]


class _FakeAgent:
    def __init__(self, name: str, context_variables: Dict[str, Any] | None = None):
        self.name = name
        self.system_message = ""
        self.context_variables = context_variables or {}

    def update_system_message(self, msg: str) -> None:
        self.system_message = msg


# ---------------------------------------------------------------------------
# audit_module_contracts
# ---------------------------------------------------------------------------

class TestAuditModuleContracts:
    def _audit(self, files):
        from factory_app.workflows.AppGenerator.tools.audit_module_contracts import (
            audit_module_contracts,
        )
        return audit_module_contracts(files)

    def test_empty_code_files_passes(self):
        assert self._audit([]) == []

    def test_valid_module_yaml_no_warnings(self):
        files = _code_files(
            ("modules/task_manager/module.yaml", _make_valid_module_yaml()),
        )
        assert self._audit(files) == []

    def test_wrong_schema_version_warns(self):
        content = _make_valid_module_yaml().replace(
            "schema_version: mozaiks.module.v1",
            "schema_version: mozaiks.module.v0",
        )
        files = _code_files(("modules/task_manager/module.yaml", content))
        warnings = self._audit(files)
        assert any("schema_version" in w for w in warnings)

    def test_missing_id_warns(self):
        content = _make_valid_module_yaml().replace("id: task_manager\n", "")
        files = _code_files(("modules/task_manager/module.yaml", content))
        warnings = self._audit(files)
        assert any("missing or empty 'id'" in w for w in warnings)

    def test_missing_handler_warns(self):
        content = _make_valid_module_yaml().replace(
            "handler: backend.handler:TaskManagerModule\n", ""
        )
        files = _code_files(("modules/task_manager/module.yaml", content))
        warnings = self._audit(files)
        assert any("missing 'handler'" in w for w in warnings)

    def test_bad_handler_prefix_warns(self):
        content = _make_valid_module_yaml().replace(
            "handler: backend.handler:TaskManagerModule",
            "handler: tasks.handler:TaskManagerModule",
        )
        files = _code_files(("modules/task_manager/module.yaml", content))
        warnings = self._audit(files)
        assert any("backend.handler:" in w for w in warnings)

    def test_invalid_module_type_warns(self):
        content = _make_valid_module_yaml().replace(
            "type: standard", "type: magic"
        )
        files = _code_files(("modules/task_manager/module.yaml", content))
        warnings = self._audit(files)
        assert any("type" in w and "magic" in w for w in warnings)

    def test_action_missing_handler_method_warns(self):
        content = _make_valid_module_yaml().replace(
            "    handler_method: create_task\n", ""
        )
        files = _code_files(("modules/task_manager/module.yaml", content))
        warnings = self._audit(files)
        assert any("handler_method" in w for w in warnings)

    def test_unparseable_yaml_warns(self):
        files = _code_files(("modules/task_manager/module.yaml", "{{{{bad yaml"))
        warnings = self._audit(files)
        assert any("could not parse as YAML" in w for w in warnings)

    def test_events_yaml_wrong_schema_version(self):
        content = _make_valid_events_yaml().replace(
            "schema_version: mozaiks.events.v1",
            "schema_version: mozaiks.events.v0",
        )
        files = _code_files(
            ("modules/task_manager/module.yaml", _make_valid_module_yaml()),
            ("modules/task_manager/contracts/events.yaml", content),
        )
        warnings = self._audit(files)
        assert any("schema_version" in w and "events.yaml" in w for w in warnings)

    def test_emits_without_events_yaml_warns(self):
        """Actions that declare emits[] need a companion events.yaml."""
        module_content = """\
schema_version: mozaiks.module.v1
id: task_manager
handler: backend.handler:TaskManagerModule
type: standard
actions:
  - id: create_task
    handler_method: create_task
    permissions: []
    emits:
      - domain.task_manager.task_created
"""
        files = _code_files(
            ("modules/task_manager/module.yaml", module_content),
            # No events.yaml included
        )
        warnings = self._audit(files)
        assert any("emits" in w and "events.yaml" in w for w in warnings)

    def test_emits_with_events_yaml_no_warning(self):
        module_content = """\
schema_version: mozaiks.module.v1
id: task_manager
handler: backend.handler:TaskManagerModule
type: standard
actions:
  - id: create_task
    handler_method: create_task
    permissions: []
    emits:
      - domain.task_manager.task_created
"""
        files = _code_files(
            ("modules/task_manager/module.yaml", module_content),
            ("modules/task_manager/contracts/events.yaml", _make_valid_events_yaml()),
        )
        assert self._audit(files) == []

    def test_non_module_yaml_files_ignored(self):
        files = _code_files(
            ("backend/config.py", "some python"),
            ("ui/pages/Dashboard.yaml", "name: Dashboard\n"),
        )
        assert self._audit(files) == []


# ---------------------------------------------------------------------------
# review_module_contract_quality
# ---------------------------------------------------------------------------

class TestReviewModuleContractQuality:
    def _review(self, code_files=None, prior_warnings=None):
        from factory_app.workflows.AppGenerator.tools.review_module_contract_quality import (
            review_module_contract_quality,
        )
        ctx: Dict[str, Any] = {}
        if code_files is not None:
            ctx["code_files"] = code_files
        if prior_warnings is not None:
            ctx["module_contract_quality_warnings"] = prior_warnings
        result = review_module_contract_quality(context_variables=ctx)
        return result, ctx

    def test_no_code_files_passes(self):
        result, ctx = self._review(code_files=[])
        assert result["status"] == "passed"
        assert ctx["module_contract_quality_status"] == "passed"

    def test_valid_contract_passes(self):
        files = _code_files(
            ("modules/task_manager/module.yaml", _make_valid_module_yaml()),
        )
        result, ctx = self._review(code_files=files)
        assert result["status"] == "passed"
        assert result["module_contract_count"] == 1

    def test_bad_schema_version_blocks(self):
        content = _make_valid_module_yaml().replace(
            "schema_version: mozaiks.module.v1",
            "schema_version: mozaiks.module.v0",
        )
        files = _code_files(("modules/task_manager/module.yaml", content))
        result, ctx = self._review(code_files=files)
        assert result["status"] == "blocked"
        assert ctx["module_contract_quality_status"] == "blocked"

    def test_missing_handler_blocks(self):
        content = _make_valid_module_yaml().replace(
            "handler: backend.handler:TaskManagerModule\n", ""
        )
        files = _code_files(("modules/task_manager/module.yaml", content))
        result, _ = self._review(code_files=files)
        assert result["status"] == "blocked"

    def test_missing_id_blocks(self):
        content = _make_valid_module_yaml().replace("id: task_manager\n", "")
        files = _code_files(("modules/task_manager/module.yaml", content))
        result, _ = self._review(code_files=files)
        assert result["status"] == "blocked"

    def test_non_critical_warning_does_not_block(self):
        """Invalid type string is advisory, not blocking."""
        content = _make_valid_module_yaml().replace("type: standard", "type: unknown")
        files = _code_files(("modules/task_manager/module.yaml", content))
        result, _ = self._review(code_files=files)
        assert result["status"] == "passed"
        assert any("type" in w for w in result["warnings"])

    def test_prior_warnings_merged(self):
        """Warnings stored in context before the tool runs are included."""
        result, ctx = self._review(
            code_files=[],
            prior_warnings=["modules/foo/module.yaml: schema_version is None; expected 'mozaiks.module.v1'"],
        )
        assert result["status"] == "blocked"
        assert len(result["warnings"]) == 1

    def test_context_variables_set(self):
        result, ctx = self._review(
            code_files=_code_files(
                ("modules/task_manager/module.yaml", _make_valid_module_yaml()),
            )
        )
        assert "module_contract_quality_status" in ctx
        assert "module_contract_quality_warnings" in ctx
        assert "module_contract_quality_result" in ctx

    def test_deduplicates_warnings(self):
        """Running twice does not duplicate warnings."""
        content = _make_valid_module_yaml().replace(
            "schema_version: mozaiks.module.v1", "schema_version: bad"
        )
        files = _code_files(("modules/task_manager/module.yaml", content))
        result1, ctx = self._review(code_files=files)
        # Simulate second call with same warnings already in context
        from factory_app.workflows.AppGenerator.tools.review_module_contract_quality import (
            review_module_contract_quality,
        )
        result2 = review_module_contract_quality(context_variables=ctx)
        assert result2["warnings"].count(result2["warnings"][0]) == 1


# ---------------------------------------------------------------------------
# hook_module_contract_quality_gate
# ---------------------------------------------------------------------------

class TestHookModuleContractQualityGate:
    def _run_hook(self, agent, messages=None):
        from factory_app.workflows.AppGenerator.tools.hook_module_contract_quality_gate import (
            run_module_contract_quality_gate,
        )
        run_module_contract_quality_gate(agent, messages or [])

    def test_injects_gate_header_for_quality_agent(self):
        agent = _FakeAgent(
            "ModuleContractQualityAgent",
            {"code_files": []},
        )
        self._run_hook(agent)
        assert "[MODULE CONTRACT QUALITY GATE]" in agent.system_message

    def test_shows_status_in_injected_block(self):
        agent = _FakeAgent(
            "ModuleContractQualityAgent",
            {"code_files": []},
        )
        self._run_hook(agent)
        assert "module_contract_quality_status" in agent.system_message

    def test_noop_for_wrong_agent(self):
        for name in ("AppPlanAgent", "ConfigMiddlewareAgent", "ModelAgent", ""):
            agent = _FakeAgent(name)
            self._run_hook(agent)
            assert agent.system_message == "", (
                f"Hook must not modify system_message for {name!r}"
            )

    def test_repeated_call_replaces_not_appends(self):
        agent = _FakeAgent(
            "ModuleContractQualityAgent",
            {"code_files": []},
        )
        self._run_hook(agent)
        self._run_hook(agent)
        count = agent.system_message.count("[MODULE CONTRACT QUALITY GATE]")
        assert count == 1, f"Expected 1 occurrence; got {count}"

    def test_gate_shows_warnings_when_blocked(self):
        content = _make_valid_module_yaml().replace(
            "schema_version: mozaiks.module.v1", "schema_version: bad"
        )
        files = _code_files(("modules/task_manager/module.yaml", content))
        agent = _FakeAgent(
            "ModuleContractQualityAgent",
            {"code_files": files},
        )
        self._run_hook(agent)
        assert "schema_version" in agent.system_message

    def test_preserves_trailing_sections(self):
        agent = _FakeAgent(
            "ModuleContractQualityAgent",
            {"code_files": []},
        )
        agent.system_message = (
            "[MODULE CONTRACT QUALITY GATE]\nold body\n\n[OTHER SECTION]\nother content"
        )
        self._run_hook(agent)
        assert "[OTHER SECTION]" in agent.system_message
        assert "other content" in agent.system_message


# ---------------------------------------------------------------------------
# hooks.yaml registration
# ---------------------------------------------------------------------------

class TestHooksYamlRegistration:
    def test_hooks_yaml_registers_module_contract_gate(self):
        data = yaml.safe_load(_HOOKS_YAML.read_text(encoding="utf-8"))
        hooks = data.get("hooks") or []
        matching = [
            h for h in hooks
            if h.get("filename") == "hook_module_contract_quality_gate.py"
            and h.get("function") == "run_module_contract_quality_gate"
        ]
        assert matching, (
            "hooks.yaml must register run_module_contract_quality_gate "
            "from hook_module_contract_quality_gate.py"
        )

    def test_hook_targets_module_contract_quality_agent(self):
        data = yaml.safe_load(_HOOKS_YAML.read_text(encoding="utf-8"))
        hooks = data.get("hooks") or []
        for h in hooks:
            if h.get("filename") == "hook_module_contract_quality_gate.py":
                assert h.get("hook_agent") == "ModuleContractQualityAgent"
                assert h.get("hook_type") == "update_agent_state"
                break


# ---------------------------------------------------------------------------
# handoffs.yaml routing
# ---------------------------------------------------------------------------

class TestHandoffsYamlRouting:
    def _handoffs(self):
        return yaml.safe_load(_HANDOFFS_YAML.read_text(encoding="utf-8")).get(
            "handoff_rules", []
        )

    def test_config_middleware_routes_to_quality_gate(self):
        handoffs = self._handoffs()
        after_work = [
            h for h in handoffs
            if h.get("source_agent") == "ConfigMiddlewareAgent"
            and h.get("handoff_type") == "after_work"
        ]
        assert after_work, "ConfigMiddlewareAgent must have an after_work handoff"
        target = after_work[0].get("target_agent")
        assert target == "ModuleContractQualityAgent", (
            f"ConfigMiddlewareAgent after_work must target ModuleContractQualityAgent; got {target!r}"
        )

    def test_quality_gate_passed_routes_to_model_agent(self):
        handoffs = self._handoffs()
        matching = [
            h for h in handoffs
            if h.get("source_agent") == "ModuleContractQualityAgent"
            and h.get("target_agent") == "ModelAgent"
        ]
        assert matching, "ModuleContractQualityAgent must route to ModelAgent when passed"
        assert 'passed' in matching[0].get("condition", "")

    def test_quality_gate_blocked_routes_to_user(self):
        handoffs = self._handoffs()
        matching = [
            h for h in handoffs
            if h.get("source_agent") == "ModuleContractQualityAgent"
            and h.get("target_agent") == "user"
        ]
        assert matching, "ModuleContractQualityAgent must route to user when blocked"
        assert "blocked" in matching[0].get("condition", "")


# ---------------------------------------------------------------------------
# context_variables.yaml declarations
# ---------------------------------------------------------------------------

class TestContextVariablesDeclarations:
    def _data(self):
        return yaml.safe_load(_CTX_YAML.read_text(encoding="utf-8"))

    def test_module_contract_quality_status_declared(self):
        data = self._data()
        defs = data.get("definitions") or {}
        assert "module_contract_quality_status" in defs

    def test_module_contract_quality_warnings_declared(self):
        data = self._data()
        defs = data.get("definitions") or {}
        assert "module_contract_quality_warnings" in defs

    def test_module_contract_quality_agent_in_agents_section(self):
        data = self._data()
        agents = data.get("agents") or {}
        assert "ModuleContractQualityAgent" in agents

    def test_agent_has_status_variable(self):
        data = self._data()
        variables = (data.get("agents") or {}).get("ModuleContractQualityAgent", {}).get("variables", [])
        assert "module_contract_quality_status" in variables

    def test_agent_has_warnings_variable(self):
        data = self._data()
        variables = (data.get("agents") or {}).get("ModuleContractQualityAgent", {}).get("variables", [])
        assert "module_contract_quality_warnings" in variables
