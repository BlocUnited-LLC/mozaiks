"""
Deterministic end-to-end smoke: conceptual_replan carry-forward pipeline.

Scenario: CRM workspace → Marketplace workspace
  Old modules: settings, notifications, contacts, pipeline
  Expected:
    settings    → safe_carry_forward → reuse → declarative contracts preserved
    notifications → safe_carry_forward → reuse → declarative contracts preserved
    contacts    → needs_adaptation (persistence, no domain-fragment match)
    pipeline    → regenerate ("pipeline" in _DOMAIN_FRAGMENTS)

Decision fixture:
    settings: reuse, notifications: reuse, contacts: drop, pipeline: drop

Levels:
  A — inventory/candidates (extract_module_inventory + classify_module_carry_forward)
  B — context seed (get_carry_forward_candidates with mocked workspace)
  C — AppBuildPlan validation with carry_forward_decisions fixture
  D — preservation resolver: correct files preserved, backend never copied
  E — conflict: generated output wins
  F — carry_forward_report shape
  G — final bundle safety

No live LLM. No live ArtifactStore. No mozaiks-app references. No Phase 7B.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

WORKSPACE = Path(__file__).resolve().parents[1]

# ---------------------------------------------------------------------------
# Fixture data
# ---------------------------------------------------------------------------

_APP_ID = "crm_to_marketplace_smoke"
_PREV_REF = "av_crm_bundle_001"

_CRM_FILE_MAP: dict[str, str] = {
    "modules/settings/module.yaml": "id: settings\nactions: []\n",
    "modules/settings/contracts/settings.yaml": "schema_version: mozaiks.settings.v1\n",
    "modules/settings/contracts/events.yaml": "events: []\n",
    "modules/settings/contracts/notifications.yaml": "notifications: []\n",
    "modules/settings/contracts/admin.yaml": "panels: []\n",
    "modules/settings/contracts/profile.yaml": "panels: []\n",
    "modules/settings/backend/handler.py": "class SettingsHandler: pass\n",
    "modules/settings/backend/service.py": "class SettingsService: pass\n",
    "modules/settings/runtime_extensions.yaml": "api_router: settings.backend.router\n",
    "modules/settings/ui/pages/custom/SettingsPage.jsx": "<div>Settings</div>",
    "modules/notifications/module.yaml": "id: notifications\nactions: []\n",
    "modules/notifications/contracts/events.yaml": "events:\n  - type: notification.sent\n",
    "modules/notifications/contracts/notifications.yaml": "notifications: []\n",
    "modules/notifications/backend/handler.py": "class NotifHandler: pass\n",
    "modules/notifications/backend/service.py": "class NotifService: pass\n",
    "modules/contacts/module.yaml": (
        "id: contacts\nactions:\n"
        "  - id: create_contact\n    emits: contact.created\n"
        "  - id: list_contacts\n  - id: delete_contact\n"
    ),
    "modules/contacts/contracts/events.yaml": "events:\n  - type: contact.created\n",
    "modules/contacts/backend/handler.py": "class ContactHandler: pass\n",
    "modules/contacts/backend/service.py": "class ContactService: pass\n",
    "modules/contacts/backend/repo.py": "class ContactRepo: pass\n",
    "modules/contacts/backend/policy.py": "class ContactPolicy: pass\n",
    "modules/pipeline/module.yaml": (
        "id: pipeline\nactions:\n"
        "  - id: create_deal\n    emits: pipeline.deal_created\n"
        "  - id: list_deals\n  - id: archive_deal\n"
    ),
    "modules/pipeline/contracts/events.yaml": "events:\n  - type: pipeline.deal_created\n",
    "modules/pipeline/backend/handler.py": "class PipelineHandler: pass\n",
    "modules/pipeline/backend/service.py": "class PipelineService: pass\n",
    "modules/pipeline/backend/repo.py": "class PipelineRepo: pass\n",
}

_WORKSPACE_OK: dict[str, Any] = {"present": True, "source": "artifact_zip", "file_map": _CRM_FILE_MAP}
_WORKSPACE_UNAVAILABLE: dict[str, Any] = {"present": False, "reason": "artifact_not_found"}

_CARRY_FORWARD_DECISIONS = [
    {"module_id": "settings", "decision": "reuse", "reason": "Generic infra.", "source": "carry_forward_candidate", "affected_build_tasks": []},
    {"module_id": "notifications", "decision": "reuse", "reason": "Concept-independent.", "source": "carry_forward_candidate", "affected_build_tasks": []},
    {"module_id": "contacts", "decision": "drop", "reason": "CRM-specific.", "source": "carry_forward_candidate", "affected_build_tasks": []},
    {"module_id": "pipeline", "decision": "drop", "reason": "CRM-specific.", "source": "carry_forward_candidate", "affected_build_tasks": []},
]

_MARKETPLACE_FILES: dict[str, str] = {
    "modules/listings/module.yaml": "id: listings\nactions: [create_listing]\n",
    "modules/listings/backend/handler.py": "class ListingsHandler: pass\n",
    "modules/orders/module.yaml": "id: orders\nactions: [place_order]\n",
    "ui/pages/home.yaml": "name: Home\nroute: /\n",
}


def _make_context(
    *,
    app_id: str = _APP_ID,
    prev_ref: str | None = _PREV_REF,
    decisions: list[dict] | None = None,
    generated_files: dict[str, str] | None = None,
) -> dict[str, Any]:
    ctx: dict[str, Any] = {"app_id": app_id}
    if prev_ref is not None:
        ctx["previous_app_bundle_ref"] = prev_ref
    if decisions is not None:
        ctx["app_build_plan"] = {"carry_forward_decisions": decisions}
    if generated_files is not None:
        ctx["generated_files"] = dict(generated_files)
    return ctx


async def _resolve(context: dict[str, Any], *, workspace_result: dict[str, Any] = _WORKSPACE_OK) -> dict[str, Any]:
    from factory_app.control_plane.tools.resolve_carry_forward_preservation import resolve_carry_forward_preservation
    with (
        patch("factory_app.control_plane.tools.resolve_carry_forward_preservation.load_artifact_workspace", new=AsyncMock(return_value=workspace_result)),
        patch("factory_app.control_plane.tools.resolve_carry_forward_preservation.get_artifact_store", return_value=MagicMock()),
        patch("factory_app.control_plane.tools.resolve_carry_forward_preservation.get_artifact_content_store", return_value=MagicMock()),
    ):
        return await resolve_carry_forward_preservation(context_variables=context, artifact_store=MagicMock(), content_store=MagicMock())


def _load_app_build_plan_module():
    path = WORKSPACE / "factory_app" / "workflows" / "AppGenerator" / "tools" / "app_build_plan.py"
    spec = importlib.util.spec_from_file_location("tests._app_build_plan_smoke", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _minimal_plan(**overrides) -> dict[str, Any]:
    base: dict[str, Any] = {
        "agent_message": "CRM-to-Marketplace conceptual replan.",
        "app_kind": "marketplace",
        "pages": [{"name": "Home", "route": "/", "purpose": "Landing"}],
        "entities": [], "roles": ["user"], "service_scope": [], "frontend_scope": [],
        "capability_packs": [], "external_integrations": [], "agent_backend_required": False,
        "build_tasks": [], "generation_order": [],
    }
    base.update(overrides)
    return base


# ===========================================================================
# Level A — Inventory / Candidates
# ===========================================================================

class TestLevelA_Inventory:
    def _extract(self):
        from factory_app.control_plane.tools._module_inventory import extract_module_inventory
        return extract_module_inventory(_CRM_FILE_MAP)

    def test_a01_finds_all_four_modules(self):
        assert {e.module_id for e in self._extract()} == {"settings", "notifications", "contacts", "pipeline"}

    def test_a02_settings_safe_carry_forward(self):
        entries = {e.module_id: e for e in self._extract()}
        assert entries["settings"].carry_forward_classification == "safe_carry_forward"

    def test_a03_notifications_safe_carry_forward(self):
        entries = {e.module_id: e for e in self._extract()}
        assert entries["notifications"].carry_forward_classification == "safe_carry_forward"

    def test_a04_pipeline_regenerate(self):
        entries = {e.module_id: e for e in self._extract()}
        assert entries["pipeline"].carry_forward_classification == "regenerate"

    def test_a05_contacts_not_safe_carry_forward(self):
        entries = {e.module_id: e for e in self._extract()}
        assert entries["contacts"].carry_forward_classification != "safe_carry_forward"

    def test_a06_contacts_has_persistence(self):
        entries = {e.module_id: e for e in self._extract()}
        assert entries["contacts"].has_persistence is True

    def test_a07_pipeline_domain_reason(self):
        entries = {e.module_id: e for e in self._extract()}
        assert any("pipeline" in r for r in entries["pipeline"].carry_forward_reasons)

    def test_a08_settings_has_settings_contract(self):
        entries = {e.module_id: e for e in self._extract()}
        assert entries["settings"].has_settings is True

    def test_a09_notifications_has_notifications_contract(self):
        entries = {e.module_id: e for e in self._extract()}
        assert entries["notifications"].has_notifications is True

    def test_a10_all_entries_have_reasons(self):
        for entry in self._extract():
            assert entry.carry_forward_reasons


# ===========================================================================
# Level B — Context Seed
# ===========================================================================

class TestLevelB_ContextSeed:
    async def _call(self, *, extra=None, workspace_result=None):
        from factory_app.control_plane.tools.get_carry_forward_candidates import get_carry_forward_candidates
        from mozaiksai.control_plane.contracts import ControlPlaneToolContext
        ws = workspace_result if workspace_result is not None else _WORKSPACE_OK
        ctx = ControlPlaneToolContext(app_id=_APP_ID, extra={"previous_app_bundle_ref": _PREV_REF, **(extra or {})})
        with patch("factory_app.control_plane.tools.get_carry_forward_candidates.load_artifact_workspace", new=AsyncMock(return_value=ws)), \
             patch("factory_app.control_plane.tools.get_carry_forward_candidates.get_artifact_store", return_value=MagicMock()):
            return await get_carry_forward_candidates(context=ctx, artifact_store=MagicMock())

    @pytest.mark.asyncio
    async def test_b01_returns_four_modules(self):
        result = await self._call()
        assert {m["module_id"] for m in result["modules"]} == {"settings", "notifications", "contacts", "pipeline"}

    @pytest.mark.asyncio
    async def test_b02_no_warnings_on_healthy_workspace(self):
        result = await self._call()
        assert result["warnings"] == []

    @pytest.mark.asyncio
    async def test_b03_unavailable_workspace_returns_empty_with_warning(self):
        result = await self._call(workspace_result=_WORKSPACE_UNAVAILABLE)
        assert result["modules"] == []
        assert result["warnings"]

    @pytest.mark.asyncio
    async def test_b04_missing_prev_ref_returns_empty(self):
        from factory_app.control_plane.tools.get_carry_forward_candidates import get_carry_forward_candidates
        from mozaiksai.control_plane.contracts import ControlPlaneToolContext
        ctx = ControlPlaneToolContext(app_id=_APP_ID, extra={})
        result = await get_carry_forward_candidates(context=ctx)
        assert result["modules"] == []
        assert result["warnings"]

    @pytest.mark.asyncio
    async def test_b05_each_module_has_classification(self):
        result = await self._call()
        valid = {"safe_carry_forward", "needs_adaptation", "regenerate"}
        for m in result["modules"]:
            assert m["carry_forward_classification"] in valid


# ===========================================================================
# Level C — AppBuildPlan fixture
# ===========================================================================

class TestLevelC_AppBuildPlan:
    def _call(self, plan_dict):
        mod = _load_app_build_plan_module()
        ctx = MagicMock()
        ctx.get = MagicMock(return_value=None)
        ctx.set = MagicMock()
        return mod.app_build_plan(AppBuildPlan=plan_dict, context_variables=ctx)

    def test_c01_accepts_reuse_and_drop_decisions(self):
        result = self._call(_minimal_plan(carry_forward_decisions=_CARRY_FORWARD_DECISIONS))
        assert isinstance(result, str)

    def test_c02_decision_values_valid(self):
        valid = {"reuse", "adapt", "regenerate", "drop"}
        for d in _CARRY_FORWARD_DECISIONS:
            assert d["decision"] in valid

    def test_c03_settings_notifications_reuse(self):
        reuse = {d["module_id"] for d in _CARRY_FORWARD_DECISIONS if d["decision"] == "reuse"}
        assert {"settings", "notifications"} == reuse

    def test_c04_contacts_pipeline_drop(self):
        dropped = {d["module_id"] for d in _CARRY_FORWARD_DECISIONS if d["decision"] == "drop"}
        assert {"contacts", "pipeline"} == dropped

    def test_c05_source_values_valid(self):
        valid = {"carry_forward_candidate", "human_override", "planner"}
        for d in _CARRY_FORWARD_DECISIONS:
            if d.get("source"):
                assert d["source"] in valid


# ===========================================================================
# Level D — Preservation
# ===========================================================================

class TestLevelD_Preservation:
    @pytest.mark.asyncio
    async def test_d01_settings_module_yaml_preserved(self):
        ctx = _make_context(decisions=_CARRY_FORWARD_DECISIONS, generated_files=dict(_MARKETPLACE_FILES))
        await _resolve(ctx)
        assert "modules/settings/module.yaml" in ctx.get("carry_forward_additions", {})

    @pytest.mark.asyncio
    async def test_d02_settings_settings_yaml_preserved(self):
        ctx = _make_context(decisions=_CARRY_FORWARD_DECISIONS, generated_files=dict(_MARKETPLACE_FILES))
        await _resolve(ctx)
        assert "modules/settings/contracts/settings.yaml" in ctx.get("carry_forward_additions", {})

    @pytest.mark.asyncio
    async def test_d03_notifications_module_yaml_preserved(self):
        ctx = _make_context(decisions=_CARRY_FORWARD_DECISIONS, generated_files=dict(_MARKETPLACE_FILES))
        await _resolve(ctx)
        assert "modules/notifications/module.yaml" in ctx.get("carry_forward_additions", {})

    @pytest.mark.asyncio
    async def test_d04_notifications_events_yaml_preserved(self):
        ctx = _make_context(decisions=_CARRY_FORWARD_DECISIONS, generated_files=dict(_MARKETPLACE_FILES))
        await _resolve(ctx)
        assert "modules/notifications/contracts/events.yaml" in ctx.get("carry_forward_additions", {})

    @pytest.mark.asyncio
    async def test_d05_no_backend_python_in_additions(self):
        ctx = _make_context(decisions=_CARRY_FORWARD_DECISIONS, generated_files=dict(_MARKETPLACE_FILES))
        await _resolve(ctx)
        py = [p for p in ctx.get("carry_forward_additions", {}) if p.endswith(".py")]
        assert py == [], f"Backend Python must never be preserved: {py}"

    @pytest.mark.asyncio
    async def test_d06_settings_backend_not_preserved(self):
        ctx = _make_context(decisions=_CARRY_FORWARD_DECISIONS, generated_files=dict(_MARKETPLACE_FILES))
        await _resolve(ctx)
        adds = ctx.get("carry_forward_additions", {})
        assert "modules/settings/backend/service.py" not in adds
        assert "modules/settings/backend/handler.py" not in adds

    @pytest.mark.asyncio
    async def test_d07_runtime_extensions_not_preserved(self):
        ctx = _make_context(decisions=_CARRY_FORWARD_DECISIONS, generated_files=dict(_MARKETPLACE_FILES))
        await _resolve(ctx)
        assert "modules/settings/runtime_extensions.yaml" not in ctx.get("carry_forward_additions", {})

    @pytest.mark.asyncio
    async def test_d08_no_contacts_files_preserved(self):
        ctx = _make_context(decisions=_CARRY_FORWARD_DECISIONS, generated_files=dict(_MARKETPLACE_FILES))
        await _resolve(ctx)
        bad = [p for p in ctx.get("carry_forward_additions", {}) if "contacts" in p]
        assert bad == []

    @pytest.mark.asyncio
    async def test_d09_no_pipeline_files_preserved(self):
        ctx = _make_context(decisions=_CARRY_FORWARD_DECISIONS, generated_files=dict(_MARKETPLACE_FILES))
        await _resolve(ctx)
        bad = [p for p in ctx.get("carry_forward_additions", {}) if "pipeline" in p]
        assert bad == []

    @pytest.mark.asyncio
    async def test_d10_no_custom_react_preserved(self):
        ctx = _make_context(decisions=_CARRY_FORWARD_DECISIONS, generated_files=dict(_MARKETPLACE_FILES))
        await _resolve(ctx)
        jsx = [p for p in ctx.get("carry_forward_additions", {}) if p.endswith(".jsx")]
        assert jsx == []


# ===========================================================================
# Level E — Conflict
# ===========================================================================

class TestLevelE_Conflict:
    @pytest.mark.asyncio
    async def test_e01_generated_settings_module_yaml_wins(self):
        generated = {**_MARKETPLACE_FILES, "modules/settings/module.yaml": "id: settings\nactions: [list_settings]\n"}
        ctx = _make_context(decisions=_CARRY_FORWARD_DECISIONS, generated_files=generated)
        result = await _resolve(ctx)
        report = result["carry_forward_report"]
        assert "modules/settings/module.yaml" in report["conflicts"]
        assert report["conflicts"]["modules/settings/module.yaml"] == "generated_output_wins"

    @pytest.mark.asyncio
    async def test_e02_generated_content_unchanged(self):
        content = "id: settings\nactions: [list_settings]\n"
        generated = {**_MARKETPLACE_FILES, "modules/settings/module.yaml": content}
        ctx = _make_context(decisions=_CARRY_FORWARD_DECISIONS, generated_files=generated)
        await _resolve(ctx)
        assert ctx["generated_files"]["modules/settings/module.yaml"] == content

    @pytest.mark.asyncio
    async def test_e03_non_conflicting_paths_still_preserved(self):
        generated = {**_MARKETPLACE_FILES, "modules/settings/module.yaml": "id: settings\n"}
        ctx = _make_context(decisions=_CARRY_FORWARD_DECISIONS, generated_files=generated)
        await _resolve(ctx)
        assert "modules/settings/contracts/settings.yaml" in ctx.get("carry_forward_additions", {})

    @pytest.mark.asyncio
    async def test_e04_conflict_does_not_block_other_module(self):
        generated = {**_MARKETPLACE_FILES, "modules/settings/module.yaml": "id: settings\n"}
        ctx = _make_context(decisions=_CARRY_FORWARD_DECISIONS, generated_files=generated)
        await _resolve(ctx)
        assert "modules/notifications/module.yaml" in ctx.get("carry_forward_additions", {})


# ===========================================================================
# Level F — Report shape
# ===========================================================================

class TestLevelF_Report:
    @pytest.mark.asyncio
    async def test_f01_report_has_required_keys(self):
        ctx = _make_context(decisions=_CARRY_FORWARD_DECISIONS, generated_files=dict(_MARKETPLACE_FILES))
        result = await _resolve(ctx)
        required = {"previous_app_bundle_ref", "workspace_available", "preserved_paths",
                    "conflicts", "reused_modules", "dropped_modules", "warnings"}
        assert required.issubset(result["carry_forward_report"].keys())

    @pytest.mark.asyncio
    async def test_f02_prev_ref_matches(self):
        ctx = _make_context(decisions=_CARRY_FORWARD_DECISIONS, generated_files=dict(_MARKETPLACE_FILES))
        result = await _resolve(ctx)
        assert result["carry_forward_report"]["previous_app_bundle_ref"] == _PREV_REF

    @pytest.mark.asyncio
    async def test_f03_reused_modules_correct(self):
        ctx = _make_context(decisions=_CARRY_FORWARD_DECISIONS, generated_files=dict(_MARKETPLACE_FILES))
        result = await _resolve(ctx)
        assert set(result["carry_forward_report"]["reused_modules"]) == {"settings", "notifications"}

    @pytest.mark.asyncio
    async def test_f04_dropped_modules_correct(self):
        ctx = _make_context(decisions=_CARRY_FORWARD_DECISIONS, generated_files=dict(_MARKETPLACE_FILES))
        result = await _resolve(ctx)
        assert set(result["carry_forward_report"]["dropped_modules"]) == {"contacts", "pipeline"}

    @pytest.mark.asyncio
    async def test_f05_preserved_paths_non_empty(self):
        ctx = _make_context(decisions=_CARRY_FORWARD_DECISIONS, generated_files=dict(_MARKETPLACE_FILES))
        result = await _resolve(ctx)
        assert len(result["carry_forward_report"]["preserved_paths"]) > 0

    @pytest.mark.asyncio
    async def test_f06_workspace_available_false_on_missing(self):
        ctx = _make_context(decisions=_CARRY_FORWARD_DECISIONS, generated_files={})
        result = await _resolve(ctx, workspace_result=_WORKSPACE_UNAVAILABLE)
        report = result["carry_forward_report"]
        assert report["workspace_available"] is False
        assert report["preserved_paths"] == []

    @pytest.mark.asyncio
    async def test_f07_report_written_to_context(self):
        ctx = _make_context(decisions=_CARRY_FORWARD_DECISIONS, generated_files=dict(_MARKETPLACE_FILES))
        await _resolve(ctx)
        assert "carry_forward_report" in ctx
        assert ctx["carry_forward_report"]["workspace_available"] is True

    @pytest.mark.asyncio
    async def test_f08_preserved_paths_all_allowlisted(self):
        from factory_app.control_plane.tools.resolve_carry_forward_preservation import _PHASE_7A_MODULE_ALLOWLIST
        ctx = _make_context(decisions=_CARRY_FORWARD_DECISIONS, generated_files=dict(_MARKETPLACE_FILES))
        result = await _resolve(ctx)
        for path in result["carry_forward_report"]["preserved_paths"]:
            parts = path.split("/", 2)
            assert len(parts) >= 3
            rel = "/".join(parts[2:])
            assert rel in _PHASE_7A_MODULE_ALLOWLIST


# ===========================================================================
# Level G — Bundle safety
# ===========================================================================

class TestLevelG_BundleSafety:
    @pytest.mark.asyncio
    async def test_g01_no_crm_backend_python_in_generated_files(self):
        ctx = _make_context(decisions=_CARRY_FORWARD_DECISIONS, generated_files=dict(_MARKETPLACE_FILES))
        await _resolve(ctx)
        crm_py = [p for p in ctx.get("generated_files", {}) if p.endswith(".py") and
                  any(m in p for m in ("settings", "notifications", "contacts", "pipeline"))]
        assert crm_py == []

    @pytest.mark.asyncio
    async def test_g02_no_contacts_in_generated_files(self):
        ctx = _make_context(decisions=_CARRY_FORWARD_DECISIONS, generated_files=dict(_MARKETPLACE_FILES))
        await _resolve(ctx)
        bad = [p for p in ctx.get("generated_files", {}) if p.startswith("modules/contacts/")]
        assert bad == []

    @pytest.mark.asyncio
    async def test_g03_no_pipeline_in_generated_files(self):
        ctx = _make_context(decisions=_CARRY_FORWARD_DECISIONS, generated_files=dict(_MARKETPLACE_FILES))
        await _resolve(ctx)
        bad = [p for p in ctx.get("generated_files", {}) if p.startswith("modules/pipeline/")]
        assert bad == []

    @pytest.mark.asyncio
    async def test_g04_marketplace_files_untouched(self):
        ctx = _make_context(decisions=_CARRY_FORWARD_DECISIONS, generated_files=dict(_MARKETPLACE_FILES))
        await _resolve(ctx)
        for path, content in _MARKETPLACE_FILES.items():
            assert ctx["generated_files"][path] == content

    @pytest.mark.asyncio
    async def test_g05_crm_preserved_paths_all_allowlisted(self):
        from factory_app.control_plane.tools.resolve_carry_forward_preservation import _PHASE_7A_MODULE_ALLOWLIST
        crm_module_ids = {"settings", "notifications", "contacts", "pipeline"}
        ctx = _make_context(decisions=_CARRY_FORWARD_DECISIONS, generated_files=dict(_MARKETPLACE_FILES))
        await _resolve(ctx)
        for path in ctx.get("generated_files", {}):
            parts = path.split("/")
            if len(parts) >= 2 and parts[0] == "modules" and parts[1] in crm_module_ids:
                rel = "/".join(parts[2:])
                assert rel in _PHASE_7A_MODULE_ALLOWLIST, f"CRM path {path!r} not in allowlist"

    @pytest.mark.asyncio
    async def test_g06_noop_standard_build_no_carry_forward(self):
        ctx = _make_context(decisions=[], generated_files=dict(_MARKETPLACE_FILES))
        result = await _resolve(ctx)
        report = result["carry_forward_report"]
        assert report["preserved_paths"] == []
        assert report["workspace_available"] is False
        for path, content in _MARKETPLACE_FILES.items():
            assert ctx["generated_files"][path] == content

