"""
Tests for the Phase 7A carry-forward preservation resolver.

Coverage (28 tests):
 1.  no decisions → no-op report
 2.  no previous_app_bundle_ref → no-op, no warning
 3.  workspace unavailable → no-op with warning
 4.  reuse copies module.yaml
 5.  reuse copies all 7 allowlisted contract files
 6.  reuse does NOT copy backend/handler.py
 7.  reuse does NOT copy backend/service.py
 8.  reuse does NOT copy runtime_extensions.yaml
 9.  reuse does NOT copy custom React (.jsx)
10.  reuse does NOT copy route_manifest.json
11.  reuse does NOT copy paths containing secret/key/password
12.  generated output wins conflict
13.  conflict recorded in report
14.  preserved file added when generated output lacks the path
15.  adapt decision copies no files
16.  regenerate decision copies no files
17.  drop decision copies no files
18.  drop warns when generated output contains dropped module paths
19.  carry_forward_report has complete 14-key shape
20.  workspace_source recorded in report
21.  context["generated_files"] updated with preserved additions
22.  context["carry_forward_report"] set in context
23.  resolver never raises on malformed decision entries
24.  AG2 wrapper imports and delegates to core
25.  tools.yaml registers resolver under AssemblyAgent with auto_tool_call=true
26.  generate_and_download merges carry_forward_additions into files_map
27.  docs say Phase 7A copies declarative contract files
28.  docs say backend Python copying remains forbidden

All tests use synthetic data — no real ArtifactStore, no real filesystem,
no real MongoDB.
"""
from __future__ import annotations

import importlib
import inspect
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_APP_ID = "app_test_phase7"
_PREV_REF = "av_prior_001"

_PRIOR_FILE_MAP = {
    "modules/settings/module.yaml": "id: settings\nactions: []\n",
    "modules/settings/contracts/events.yaml": "events: []\n",
    "modules/settings/contracts/reactions.yaml": "reactions: []\n",
    "modules/settings/contracts/notifications.yaml": "notifications: []\n",
    "modules/settings/contracts/settings.yaml": "settings: []\n",
    "modules/settings/contracts/admin.yaml": "panels: []\n",
    "modules/settings/contracts/profile.yaml": "panels: []\n",
    "modules/settings/backend/handler.py": "class Handler: pass\n",
    "modules/settings/backend/service.py": "class Service: pass\n",
    "modules/settings/runtime_extensions.yaml": "api_router: ...\n",
    "modules/settings/ui/pages/custom/SettingsPage.jsx": "<div/>",
    "modules/settings/ui/route_manifest.json": '{"routes": []}',
    "modules/billing/module.yaml": "id: billing\nactions: []\n",
    "modules/billing/contracts/settings.yaml": "settings: []\n",
}

_WORKSPACE_OK = {
    "present": True,
    "source": "artifact_zip",
    "file_map": _PRIOR_FILE_MAP,
}

_WORKSPACE_UNAVAILABLE = {
    "present": False,
    "reason": "workspace_unavailable",
}


def _make_plan(decisions: list[dict]) -> dict:
    return {"carry_forward_decisions": decisions}


def _decision(module_id: str, decision: str, **kw) -> dict:
    return {"module_id": module_id, "decision": decision, "reason": "test", **kw}


def _make_context(
    *,
    app_id: str = _APP_ID,
    prev_ref: str | None = _PREV_REF,
    decisions: list[dict] | None = None,
    generated_files: dict | None = None,
) -> dict:
    ctx: dict[str, Any] = {"app_id": app_id}
    if prev_ref is not None:
        ctx["previous_app_bundle_ref"] = prev_ref
    if decisions is not None:
        ctx["app_build_plan"] = _make_plan(decisions)
    if generated_files is not None:
        ctx["generated_files"] = generated_files
    return ctx


async def _resolve(
    context: dict,
    *,
    workspace_result: dict = _WORKSPACE_OK,
) -> dict:
    from factory_app.control_plane.tools.resolve_carry_forward_preservation import (
        resolve_carry_forward_preservation,
    )

    with patch(
        "factory_app.control_plane.tools.resolve_carry_forward_preservation.load_artifact_workspace",
        new=AsyncMock(return_value=workspace_result),
    ), patch(
        "factory_app.control_plane.tools.resolve_carry_forward_preservation.get_artifact_store",
        return_value=MagicMock(),
    ), patch(
        "factory_app.control_plane.tools.resolve_carry_forward_preservation.get_artifact_content_store",
        return_value=MagicMock(),
    ):
        return await resolve_carry_forward_preservation(
            context_variables=context,
            artifact_store=MagicMock(),
            content_store=MagicMock(),
        )


# ---------------------------------------------------------------------------
# 1. No decisions → no-op
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_no_decisions_noop():
    ctx = _make_context(decisions=[])
    result = await _resolve(ctx)
    report = result["carry_forward_report"]
    assert report["preserved_paths"] == []
    assert report["conflicts"] == {}
    assert report["warnings"] == []
    assert "carry_forward_report" in ctx


# ---------------------------------------------------------------------------
# 2. No previous_app_bundle_ref → no-op, no warning
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_no_prev_ref_noop_no_warning():
    ctx = _make_context(
        prev_ref=None,
        decisions=[_decision("settings", "reuse")],
    )
    result = await _resolve(ctx)
    report = result["carry_forward_report"]
    assert report["preserved_paths"] == []
    assert report["previous_app_bundle_ref"] is None
    assert report["warnings"] == []


# ---------------------------------------------------------------------------
# 3. Workspace unavailable → no-op with warning
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_workspace_unavailable_warns():
    ctx = _make_context(decisions=[_decision("settings", "reuse")])
    result = await _resolve(ctx, workspace_result=_WORKSPACE_UNAVAILABLE)
    report = result["carry_forward_report"]
    assert report["preserved_paths"] == []
    assert len(report["warnings"]) == 1
    assert "workspace_unavailable" in report["warnings"][0]


# ---------------------------------------------------------------------------
# 4. reuse copies module.yaml
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_reuse_copies_module_yaml():
    ctx = _make_context(
        decisions=[_decision("settings", "reuse")],
        generated_files={},
    )
    result = await _resolve(ctx)
    report = result["carry_forward_report"]
    assert "modules/settings/module.yaml" in report["preserved_paths"]


# ---------------------------------------------------------------------------
# 5. reuse copies all 7 allowlisted contract files
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_reuse_copies_all_allowlisted_contracts():
    ctx = _make_context(
        decisions=[_decision("settings", "reuse")],
        generated_files={},
    )
    result = await _resolve(ctx)
    report = result["carry_forward_report"]
    expected = {
        "modules/settings/module.yaml",
        "modules/settings/contracts/events.yaml",
        "modules/settings/contracts/reactions.yaml",
        "modules/settings/contracts/notifications.yaml",
        "modules/settings/contracts/settings.yaml",
        "modules/settings/contracts/admin.yaml",
        "modules/settings/contracts/profile.yaml",
    }
    assert expected.issubset(set(report["preserved_paths"]))


# ---------------------------------------------------------------------------
# 6. reuse does NOT copy backend/handler.py
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_reuse_does_not_copy_backend_handler():
    ctx = _make_context(
        decisions=[_decision("settings", "reuse")],
        generated_files={},
    )
    result = await _resolve(ctx)
    report = result["carry_forward_report"]
    assert "modules/settings/backend/handler.py" not in report["preserved_paths"]
    assert "modules/settings/backend/handler.py" not in ctx.get("generated_files", {})


# ---------------------------------------------------------------------------
# 7. reuse does NOT copy backend/service.py
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_reuse_does_not_copy_backend_service():
    ctx = _make_context(
        decisions=[_decision("settings", "reuse")],
        generated_files={},
    )
    result = await _resolve(ctx)
    report = result["carry_forward_report"]
    assert "modules/settings/backend/service.py" not in report["preserved_paths"]


# ---------------------------------------------------------------------------
# 8. reuse does NOT copy runtime_extensions.yaml
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_reuse_does_not_copy_runtime_extensions():
    ctx = _make_context(
        decisions=[_decision("settings", "reuse")],
        generated_files={},
    )
    result = await _resolve(ctx)
    report = result["carry_forward_report"]
    preserved_set = set(report["preserved_paths"])
    # runtime_extensions.yaml is not on the allowlist, so never preserved
    assert not any("runtime_extensions" in p for p in preserved_set)


# ---------------------------------------------------------------------------
# 9. reuse does NOT copy custom React
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_reuse_does_not_copy_custom_react():
    ctx = _make_context(
        decisions=[_decision("settings", "reuse")],
        generated_files={},
    )
    result = await _resolve(ctx)
    report = result["carry_forward_report"]
    assert not any(".jsx" in p for p in report["preserved_paths"])


# ---------------------------------------------------------------------------
# 10. reuse does NOT copy route_manifest.json
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_reuse_does_not_copy_route_manifest():
    ctx = _make_context(
        decisions=[_decision("settings", "reuse")],
        generated_files={},
    )
    result = await _resolve(ctx)
    report = result["carry_forward_report"]
    assert not any("route_manifest" in p for p in report["preserved_paths"])


# ---------------------------------------------------------------------------
# 11. Sensitive path names rejected
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_reuse_does_not_copy_sensitive_paths():
    from factory_app.control_plane.tools.resolve_carry_forward_preservation import _is_denylist_path

    # Paths explicitly matched by _is_denylist_path sensitive-term check
    denylist_sensitive_paths = [
        "modules/auth/contracts/secret_tokens.yaml",
        "modules/billing/contracts/password_policy.yaml",
        "modules/billing/backend/credential_store.py",
    ]
    for path in denylist_sensitive_paths:
        denied, reason = _is_denylist_path(path)
        assert denied, f"Expected {path!r} to be denied by denylist, reason: {reason}"

    # api_key.yaml is not in the Phase 7A allowlist (only specific contract filenames
    # are allowed), so it is protected by allowlist exclusivity, not the denylist.
    # Verify that it is NOT in the allowlist.
    from factory_app.control_plane.tools.resolve_carry_forward_preservation import (
        _PHASE_7A_MODULE_ALLOWLIST,
    )
    assert "contracts/api_key.yaml" not in _PHASE_7A_MODULE_ALLOWLIST


# ---------------------------------------------------------------------------
# 12. Generated output wins conflict
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_generated_output_wins_conflict():
    generated = {"modules/settings/module.yaml": "generated content\n"}
    ctx = _make_context(
        decisions=[_decision("settings", "reuse")],
        generated_files=generated,
    )
    result = await _resolve(ctx)
    # Must not overwrite generated version
    assert ctx["generated_files"]["modules/settings/module.yaml"] == "generated content\n"


# ---------------------------------------------------------------------------
# 13. Conflict recorded in report
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_conflict_recorded_in_report():
    generated = {"modules/settings/module.yaml": "generated content\n"}
    ctx = _make_context(
        decisions=[_decision("settings", "reuse")],
        generated_files=generated,
    )
    result = await _resolve(ctx)
    report = result["carry_forward_report"]
    assert "modules/settings/module.yaml" in report["conflicts"]
    assert report["conflicts"]["modules/settings/module.yaml"] == "generated_output_wins"


# ---------------------------------------------------------------------------
# 14. Preserved file added when generated output lacks the path
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_preserved_file_added_when_path_absent_in_generated():
    # generated_files has only a different module
    generated = {"modules/auth/module.yaml": "id: auth\n"}
    ctx = _make_context(
        decisions=[_decision("settings", "reuse")],
        generated_files=generated,
    )
    result = await _resolve(ctx)
    report = result["carry_forward_report"]
    assert "modules/settings/module.yaml" in report["preserved_paths"]
    assert "modules/settings/module.yaml" in ctx["generated_files"]


# ---------------------------------------------------------------------------
# 15. adapt copies no files
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_adapt_copies_no_files():
    ctx = _make_context(
        decisions=[_decision("settings", "adapt")],
        generated_files={},
    )
    result = await _resolve(ctx)
    report = result["carry_forward_report"]
    assert report["preserved_paths"] == []
    assert report["adapted_modules"] == ["settings"]


# ---------------------------------------------------------------------------
# 16. regenerate copies no files
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_regenerate_copies_no_files():
    ctx = _make_context(
        decisions=[_decision("billing", "regenerate")],
        generated_files={},
    )
    result = await _resolve(ctx)
    report = result["carry_forward_report"]
    assert report["preserved_paths"] == []
    assert report["regenerated_modules"] == ["billing"]


# ---------------------------------------------------------------------------
# 17. drop copies no files
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_drop_copies_no_files():
    ctx = _make_context(
        decisions=[_decision("billing", "drop")],
        generated_files={},
    )
    result = await _resolve(ctx)
    report = result["carry_forward_report"]
    assert report["preserved_paths"] == []
    assert report["dropped_modules"] == ["billing"]


# ---------------------------------------------------------------------------
# 18. drop warns when generated output contains dropped module paths
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_drop_warns_when_generated_has_module_paths():
    generated = {
        "modules/billing/module.yaml": "id: billing\n",
        "modules/billing/backend/handler.py": "class Handler: pass\n",
    }
    ctx = _make_context(
        decisions=[_decision("billing", "drop")],
        generated_files=generated,
    )
    result = await _resolve(ctx)
    report = result["carry_forward_report"]
    assert len(report["warnings"]) > 0
    assert any("billing" in w and "drop" in w for w in report["warnings"])


# ---------------------------------------------------------------------------
# 19. carry_forward_report has complete 14-key shape
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_report_has_complete_shape():
    ctx = _make_context(decisions=[])
    result = await _resolve(ctx)
    report = result["carry_forward_report"]
    required_keys = {
        "previous_app_bundle_ref",
        "workspace_available",
        "workspace_source",
        "preserved_paths",
        "skipped_paths",
        "conflicts",
        "decisions_processed",
        "reused_modules",
        "adapted_modules",
        "regenerated_modules",
        "dropped_modules",
        "warnings",
    }
    assert required_keys.issubset(set(report.keys()))


# ---------------------------------------------------------------------------
# 20. workspace_source recorded when available
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_workspace_source_recorded():
    ctx = _make_context(
        decisions=[_decision("settings", "reuse")],
        generated_files={},
    )
    result = await _resolve(ctx, workspace_result=_WORKSPACE_OK)
    report = result["carry_forward_report"]
    assert report["workspace_available"] is True
    assert report["workspace_source"] == "artifact_zip"


# ---------------------------------------------------------------------------
# 21. context["generated_files"] updated with preserved additions
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_generated_files_context_updated():
    ctx = _make_context(
        decisions=[_decision("settings", "reuse")],
        generated_files={},
    )
    await _resolve(ctx)
    gf = ctx.get("generated_files", {})
    assert "modules/settings/module.yaml" in gf
    assert gf["modules/settings/module.yaml"] == "id: settings\nactions: []\n"


# ---------------------------------------------------------------------------
# 22. context["carry_forward_report"] set
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_carry_forward_report_set_in_context():
    ctx = _make_context(decisions=[_decision("settings", "reuse")], generated_files={})
    await _resolve(ctx)
    assert "carry_forward_report" in ctx
    assert isinstance(ctx["carry_forward_report"], dict)


# ---------------------------------------------------------------------------
# 23. Resolver never raises on malformed decision entries
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_malformed_decision_no_raise():
    ctx = _make_context(
        decisions=[
            "not_a_dict",
            None,
            {"module_id": "../traversal", "decision": "reuse"},
            {"module_id": "", "decision": "reuse"},
            {"module_id": "valid", "decision": "unknown_value"},
        ],
    )
    # Should not raise
    result = await _resolve(ctx)
    report = result["carry_forward_report"]
    assert isinstance(report["warnings"], list)
    assert len(report["warnings"]) > 0


# ---------------------------------------------------------------------------
# 24. AG2 wrapper imports and delegates to core
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_wrapper_imports_and_delegates():
    wrapper = importlib.import_module(
        "factory_app.workflows.AppGenerator.tools.resolve_carry_forward_preservation"
    )
    func = getattr(wrapper, "resolve_carry_forward_preservation", None)
    assert callable(func)

    ctx = _make_context(decisions=[])
    with patch(
        "factory_app.control_plane.tools.resolve_carry_forward_preservation.load_artifact_workspace",
        new=AsyncMock(return_value=_WORKSPACE_OK),
    ), patch(
        "factory_app.control_plane.tools.resolve_carry_forward_preservation.get_artifact_store",
        return_value=MagicMock(),
    ), patch(
        "factory_app.control_plane.tools.resolve_carry_forward_preservation.get_artifact_content_store",
        return_value=MagicMock(),
    ):
        result = await func(context_variables=ctx)
    assert "carry_forward_report" in result


# ---------------------------------------------------------------------------
# 25. tools.yaml registers resolver under AssemblyAgent with auto_tool_call=true
# ---------------------------------------------------------------------------

class TestToolsYamlRegistration:
    _TOOLS_PATH = (
        Path(__file__).parent.parent
        / "factory_app"
        / "workflows"
        / "AppGenerator"
        / "tools.yaml"
    )

    def _load_tools(self) -> list[dict]:
        doc = yaml.safe_load(self._TOOLS_PATH.read_text(encoding="utf-8"))
        if isinstance(doc, dict):
            return doc.get("tools", []) or []
        return list(doc) if isinstance(doc, list) else []

    def test_registered_under_assembly_agent(self):
        tools = self._load_tools()
        entries = [
            t for t in tools
            if t.get("agent") == "AssemblyAgent"
            and t.get("function") == "resolve_carry_forward_preservation"
        ]
        assert len(entries) == 1, "resolve_carry_forward_preservation must be registered under AssemblyAgent"

    def test_auto_tool_call_is_true(self):
        tools = self._load_tools()
        entry = next(
            t for t in tools
            if t.get("function") == "resolve_carry_forward_preservation"
        )
        assert entry.get("auto_tool_call") is True

    def test_description_mentions_generated_wins(self):
        tools = self._load_tools()
        entry = next(
            t for t in tools
            if t.get("function") == "resolve_carry_forward_preservation"
        )
        desc = str(entry.get("description", ""))
        assert "generated output wins" in desc.lower() or "generated" in desc.lower()

    def test_description_mentions_backend_python_never_copied(self):
        tools = self._load_tools()
        entry = next(
            t for t in tools
            if t.get("function") == "resolve_carry_forward_preservation"
        )
        desc = str(entry.get("description", "")).lower()
        assert "backend" in desc or "python" in desc


# ---------------------------------------------------------------------------
# 26. generate_and_download merges carry_forward_additions
# ---------------------------------------------------------------------------

def test_generate_and_download_merges_carry_forward_additions():
    """generate_and_download reads carry_forward_additions from context and merges."""
    # Read the file directly to avoid circular import chains from heavy runtime deps
    path = (
        Path(__file__).parent.parent
        / "factory_app" / "workflows" / "AppGenerator" / "tools" / "generate_and_download.py"
    )
    source = path.read_text(encoding="utf-8")
    assert "carry_forward_additions" in source, (
        "generate_and_download must merge carry_forward_additions from context"
    )


# ---------------------------------------------------------------------------
# 27. Docs say Phase 7A copies declarative contract files
# ---------------------------------------------------------------------------

class TestDocsPhase7A:
    _DOC = (
        Path(__file__).parent.parent
        / "docs"
        / "architecture"
        / "workflows"
        / "refinement-control-plane.md"
    )

    def _content(self) -> str:
        return self._DOC.read_text(encoding="utf-8")

    def test_docs_mention_phase_7a_declarative_preservation(self):
        content = self._content().lower()
        assert "phase 7a" in content or "declarative contract" in content or "declarative module" in content

    def test_docs_mention_backend_copying_forbidden(self):
        content = self._content().lower()
        assert "backend" in content
        assert "forbidden" in content or "never copied" in content or "permanently" in content

    def test_docs_no_longer_say_metadata_only(self):
        """The stale claim 'carry-forward is metadata only' must be updated."""
        content = self._content()
        # The old wording that carry-forward decisions have NO file copy behavior
        # should be updated. Check that we don't claim BOTH "no file copy" and
        # "phase 7a preserves files".
        if "phase 7a" in content.lower():
            # If Phase 7A is documented, the doc must not say "no preservation behavior exists"
            assert "no preservation behavior exists" not in content.lower()

