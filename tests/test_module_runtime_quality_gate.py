"""Tests for AppGenerator's module runtime quality gate."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

import yaml

_APPGEN_DIR = (
    Path(__file__).parent.parent
    / "factory_app"
    / "workflows"
    / "AppGenerator"
)
_HOOKS_YAML = _APPGEN_DIR / "hooks.yaml"
_HANDOFFS_YAML = _APPGEN_DIR / "handoffs.yaml"
_TOOLS_YAML = _APPGEN_DIR / "tools.yaml"
_CTX_YAML = _APPGEN_DIR / "context_variables.yaml"
_STRUCTURED_OUTPUTS_YAML = _APPGEN_DIR / "structured_outputs.yaml"


def _code_files(*pairs: tuple[str, str]) -> List[Dict[str, Any]]:
    return [{"filename": filename, "content": content} for filename, content in pairs]


def _repo_backed_summary() -> str:
    return """\
class MarketplaceService:
    def __init__(self, repo):
        self.repo = repo

    async def get_marketplace_summary(self, *, context):
        live_listings = await self.repo.count_live_listings(context=context)
        submitted = await self.repo.count_submitted_listings(context=context)
        return {
            "live_listings": live_listings,
            "live_listings_trend": None,
            "submitted": submitted,
            "submitted_trend": None,
        }
"""


def _placeholder_summary() -> str:
    return """\
SAMPLE_SUMMARY = {
    "live_listings": 3,
    "live_listings_trend": "+12.4% change",
}


class MarketplaceService:
    async def get_marketplace_summary(self, *, context):
        return {
            "live_listings": 3,
            "live_listings_trend": "+12.4% change",
        }
"""


class _FakeAgent:
    def __init__(self, name: str, context_variables: Dict[str, Any] | None = None):
        self.name = name
        self.system_message = ""
        self.context_variables = context_variables or {}

    def update_system_message(self, msg: str) -> None:
        self.system_message = msg


class TestAuditModuleRuntimeQuality:
    def _audit(self, files):
        from factory_app.workflows.AppGenerator.tools.module_runtime_quality import (
            audit_module_runtime_quality,
        )

        return audit_module_runtime_quality(files)

    def test_repo_backed_summary_passes(self):
        warnings = self._audit(
            _code_files(
                ("modules/investor_marketplace/backend/service.py", _repo_backed_summary())
            )
        )
        assert warnings == []

    def test_placeholder_summary_warns(self):
        warnings = self._audit(
            _code_files(
                ("modules/investor_marketplace/backend/service.py", _placeholder_summary())
            )
        )
        assert any("placeholder variable 'SAMPLE_SUMMARY'" in warning for warning in warnings)
        assert any("static trend/change" in warning for warning in warnings)
        assert any("static metrics without repo/db" in warning for warning in warnings)

    def test_ignores_non_module_python_files(self):
        warnings = self._audit(
            _code_files(
                ("backend/scripts/dev_seed.py", "SAMPLE_SUMMARY = {'total': 3}\n")
            )
        )
        assert warnings == []


class TestReviewModuleRuntimeQuality:
    def _review(self, code_files=None, prior_warnings=None, revision_count=None):
        from factory_app.workflows.AppGenerator.tools.module_runtime_quality import (
            review_module_runtime_quality,
        )

        ctx: Dict[str, Any] = {}
        if code_files is not None:
            ctx["code_files"] = code_files
        if prior_warnings is not None:
            ctx["module_runtime_quality_warnings"] = prior_warnings
        if revision_count is not None:
            ctx["module_runtime_quality_revision_count"] = revision_count
        result = review_module_runtime_quality(context_variables=ctx)
        return result, ctx

    def test_valid_runtime_passes(self):
        result, ctx = self._review(
            _code_files(
                ("modules/investor_marketplace/backend/service.py", _repo_backed_summary())
            )
        )
        assert result["status"] == "passed"
        assert ctx["module_runtime_quality_status"] == "passed"

    def test_placeholder_runtime_needs_revision_first(self):
        result, ctx = self._review(
            _code_files(
                ("modules/investor_marketplace/backend/service.py", _placeholder_summary())
            )
        )
        assert result["status"] == "needs_revision"
        assert ctx["module_runtime_quality_revision_count"] == 1
        assert ctx["module_runtime_quality_revision_request"]

    def test_placeholder_runtime_blocks_after_revision_budget(self):
        result, ctx = self._review(
            _code_files(
                ("modules/investor_marketplace/backend/service.py", _placeholder_summary())
            ),
            revision_count=1,
        )
        assert result["status"] == "blocked"
        assert ctx["module_runtime_quality_status"] == "blocked"

    def test_fixed_revision_clears_prior_warnings(self):
        result, ctx = self._review(
            _code_files(
                ("modules/investor_marketplace/backend/service.py", _repo_backed_summary())
            ),
            prior_warnings=["stale warning from previous ServiceAgent output"],
            revision_count=1,
        )
        assert result["status"] == "passed"
        assert ctx["module_runtime_quality_warnings"] == []


class TestModuleRuntimeQualityHook:
    def _run_hook(self, agent, messages=None):
        from factory_app.workflows.AppGenerator.tools.hook_module_runtime_quality_gate import (
            run_module_runtime_quality_gate,
        )

        run_module_runtime_quality_gate(agent, messages or [])

    def test_hook_extracts_service_output_and_sets_context(self):
        payload = {
            "python_files": [
                {
                    "path": "modules/investor_marketplace/backend/service.py",
                    "kind": "service",
                    "purpose": "Service.",
                    "contract_refs": [],
                    "content": _placeholder_summary(),
                }
            ],
            "code_files": [],
            "agent_message": "Implemented service.",
        }
        agent = _FakeAgent("ModuleRuntimeQualityAgent", {})
        self._run_hook(
            agent,
            [{"name": "ServiceAgent", "content": json.dumps(payload)}],
        )
        assert "[MODULE RUNTIME QUALITY GATE]" in agent.system_message
        assert agent.context_variables["code_files"]
        assert agent.context_variables["module_runtime_quality_status"] == "needs_revision"
        assert "static metrics" in "\n".join(agent.context_variables["module_runtime_quality_warnings"])

    def test_noop_for_wrong_agent(self):
        agent = _FakeAgent("ServiceAgent", {})
        self._run_hook(agent)
        assert agent.system_message == ""

    def test_repeated_call_replaces_not_appends(self):
        agent = _FakeAgent("ModuleRuntimeQualityAgent", {"code_files": []})
        self._run_hook(agent)
        self._run_hook(agent)
        assert agent.system_message.count("[MODULE RUNTIME QUALITY GATE]") == 1


class TestWorkflowWiring:
    def test_hooks_yaml_registers_module_runtime_gate(self):
        hooks = yaml.safe_load(_HOOKS_YAML.read_text(encoding="utf-8")).get("hooks") or []
        assert any(
            hook.get("hook_agent") == "ModuleRuntimeQualityAgent"
            and hook.get("filename") == "hook_module_runtime_quality_gate.py"
            and hook.get("function") == "run_module_runtime_quality_gate"
            for hook in hooks
        )

    def test_tools_yaml_registers_runtime_quality_auto_tool(self):
        tools = yaml.safe_load(_TOOLS_YAML.read_text(encoding="utf-8")).get("tools") or []
        assert any(
            tool.get("agent") == "ModuleRuntimeQualityAgent"
            and tool.get("function") == "review_module_runtime_quality"
            and tool.get("auto_tool_call") is True
            for tool in tools
        )

    def test_handoffs_route_through_module_runtime_gate(self):
        handoffs = yaml.safe_load(_HANDOFFS_YAML.read_text(encoding="utf-8")).get(
            "handoff_rules", []
        )
        assert any(
            rule.get("source_agent") == "ServiceAgent"
            and rule.get("target_agent") == "ModuleRuntimeQualityAgent"
            for rule in handoffs
        )
        assert any(
            rule.get("source_agent") == "ModuleRuntimeQualityAgent"
            and rule.get("target_agent") == "ServiceAgent"
            and "needs_revision" in str(rule.get("condition"))
            for rule in handoffs
        )
        assert any(
            rule.get("source_agent") == "ModuleRuntimeQualityAgent"
            and rule.get("target_agent") == "FrontendStubAgent"
            and "passed" in str(rule.get("condition"))
            for rule in handoffs
        )
        assert any(
            rule.get("source_agent") == "ModuleRuntimeQualityAgent"
            and rule.get("target_agent") == "user"
            and "blocked" in str(rule.get("condition"))
            for rule in handoffs
        )

    def test_context_variables_declare_runtime_gate(self):
        data = yaml.safe_load(_CTX_YAML.read_text(encoding="utf-8"))
        defs = data.get("definitions") or {}
        agents = data.get("agents") or {}
        assert "module_runtime_quality_status" in defs
        assert "module_runtime_quality_warnings" in defs
        assert "module_runtime_quality_revision_request" in defs
        assert "ModuleRuntimeQualityAgent" in agents
        assert "module_runtime_quality_status" in agents["ModuleRuntimeQualityAgent"]["variables"]
        assert "module_runtime_quality_revision_request" in agents["ServiceAgent"]["variables"]

    def test_structured_outputs_register_runtime_quality_agent(self):
        data = yaml.safe_load(_STRUCTURED_OUTPUTS_YAML.read_text(encoding="utf-8"))
        assert "ModuleRuntimeQualityReviewRequest" in data["models"]
        assert data["registry"]["ModuleRuntimeQualityAgent"] == "ModuleRuntimeQualityReviewRequest"
