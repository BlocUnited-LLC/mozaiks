"""
Fixture-replay and pipeline-safety tests for the conceptual_replan carry-forward smoke.

Three test classes:

  TestExplicitVariant (skipped unless fixture present or env var set)
    - Replays assertions for the explicit carry_forward_modules override variant.

  TestAutoVariant (skipped unless fixture present or env var set)
    - Replays assertions for the auto-populated carry_forward_modules variant.
    - Validates get_carry_forward_candidates was called, all CRM modules returned,
      and current behavior (all module IDs returned unfiltered at route time) is
      documented truthfully.

  TestPipelineSafety (always run -- no fixture, no LLM, no MongoDB)
    - Runs resolve_carry_forward_preservation directly with a synthetic CRM workspace.
    - Deterministic safety assertions: no backend Python, no runtime_extensions,
      no custom React, only reuse-decision modules preserved.

Create the fixture with:
    python scripts/smoke_conceptual_replan_carry_forward.py --mode both --save-fixture

Run:
    python -m pytest tests/test_live_conceptual_replan_carry_forward.py -q
    python -m pytest tests/test_live_conceptual_replan_carry_forward.py -q -k TestPipelineSafety
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = REPO_ROOT / "tests" / "fixtures" / "conceptual_replan_carry_forward_output.json"

_FIXTURE_AVAILABLE = FIXTURE_PATH.exists()
_LIVE_ENV = os.environ.get("MOZAIKS_LIVE_CARRY_FORWARD_SMOKE", "").strip() == "1"
_RUN_FIXTURE_TESTS = _FIXTURE_AVAILABLE or _LIVE_ENV

_SKIP_REASON = (
    "Fixture not found and MOZAIKS_LIVE_CARRY_FORWARD_SMOKE=1 not set. "
    "Run: python scripts/smoke_conceptual_replan_carry_forward.py --mode both --save-fixture"
)


def _load_fixture() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _explicit(fx: dict) -> dict:
    return fx.get("variants", {}).get("explicit", {})


def _auto(fx: dict) -> dict:
    return fx.get("variants", {}).get("auto", {})


# ---------------------------------------------------------------------------
# Helpers shared by TestPipelineSafety
# ---------------------------------------------------------------------------

_SAFETY_CRM_FILES = {
    "modules/settings/module.yaml": "id: settings\nactions: []\n",
    "modules/settings/contracts/settings.yaml": "version: 1\n",
    "modules/settings/contracts/events.yaml": "version: 1\nevents: []\n",
    "modules/settings/backend/service.py": "# backend -- must NOT be copied\n",
    "modules/notifications/module.yaml": "id: notifications\nactions: []\n",
    "modules/notifications/contracts/events.yaml": "version: 1\nevents: []\n",
    "modules/notifications/backend/handler.py": "# backend -- must NOT be copied\n",
    "modules/contacts/module.yaml": "id: contacts\nactions: []\n",
    "modules/contacts/backend/repo.py": "# contacts backend -- must NOT be copied\n",
    "modules/pipeline/module.yaml": "id: pipeline\nactions: []\n",
    "modules/pipeline/runtime_extensions.yaml": "api_router: pipeline_router\n",
}

_SAFETY_DECISIONS = [
    {"module_id": "settings", "decision": "reuse"},
    {"module_id": "notifications", "decision": "reuse"},
    {"module_id": "contacts", "decision": "drop"},
    {"module_id": "pipeline", "decision": "drop"},
]

_SAFETY_GENERATED = {
    "modules/listings/module.yaml": "id: listings\n",
    "modules/orders/module.yaml": "id: orders\n",
}


def _run_preservation(tmp_path: Path) -> dict:
    from factory_app.refinement_harness.tools.resolve_carry_forward_preservation import (
        resolve_carry_forward_preservation,
    )
    from mozaiksai.core.artifacts.models import ArtifactCommitMetadata, BuildRecord

    doc = BuildRecord.model_validate({
        "_id": "av_safety_v1",
        "app_id": "smoke-safety-app",
        "build_family": "app_bundle",
        "build_key": "app_bundle",
        "version_number": 1,
        "lineage_root_id": "av_safety_v1",
        "commit_metadata": ArtifactCommitMetadata(
            metadata={"workspace_dir": str(tmp_path)}
        ).model_dump(),
    })
    mock_store = MagicMock()
    mock_store.get_build_record = AsyncMock(return_value=doc)
    workspace_result = {
        "present": True,
        "source": "workspace_dir",
        "file_map": dict(_SAFETY_CRM_FILES),
        "workspace_dir": str(tmp_path),
        "artifact_path": None,
        "content_ref": None,
        "content_backend": None,
    }

    context_variables: dict = {
        "app_id": "smoke-safety-app",
        "previous_app_bundle_ref": "av_safety_v1",
        "app_build_plan": {"carry_forward_decisions": _SAFETY_DECISIONS},
        "generated_files": dict(_SAFETY_GENERATED),
    }

    async def _inner():
        with patch(
            "factory_app.refinement_harness.tools.resolve_carry_forward_preservation.load_artifact_workspace",
            new=AsyncMock(return_value=workspace_result),
        ):
            return await resolve_carry_forward_preservation(
                context_variables=context_variables,
                artifact_store=mock_store,
            )

    result = asyncio.run(_inner())
    return result.get("carry_forward_report", {})


# ---------------------------------------------------------------------------
# TestExplicitVariant
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _RUN_FIXTURE_TESTS, reason=_SKIP_REASON)
class TestExplicitVariant:
    """Explicit carry_forward_modules override path."""

    @pytest.fixture(scope="class")
    def fx(self):
        return _load_fixture()

    @pytest.fixture(scope="class")
    def v(self, fx):
        return _explicit(fx)

    def test_schema_version_v2(self, fx):
        assert fx.get("schema_version") == "mozaiks.conceptual_replan_carry_forward_smoke.v2"

    def test_smoke_success(self, fx):
        assert fx.get("success") is True, f"violations: {fx.get('violations')}"

    def test_explicit_variant_present(self, fx):
        assert "explicit" in (fx.get("variants") or {}), "explicit variant missing from fixture"

    def test_source_is_explicit_override(self, v):
        assert v.get("carry_forward_modules_source") == "explicit_override"

    def test_explicit_override_used_true(self, v):
        assert v.get("explicit_override_used") is True

    def test_auto_populated_false(self, v):
        assert v.get("auto_populated") is False

    def test_workflow_sequence_conceptual_replan(self, v):
        assert (v.get("routing") or {}).get("workflow_sequence") == "conceptual_replan"

    def test_change_class_core(self, v):
        assert (v.get("routing") or {}).get("change_class") == "core"

    def test_pivot_description_present(self, v):
        pd = (v.get("context_seed") or {}).get("pivot_description") or ""
        assert pd.strip()

    def test_preserve_families_present(self, v):
        families = (v.get("context_seed") or {}).get("preserve_families")
        assert isinstance(families, list) and len(families) > 0

    def test_previous_app_bundle_ref_present(self, v):
        ref = (v.get("context_seed") or {}).get("previous_app_bundle_ref")
        assert ref and isinstance(ref, str)

    def test_carry_forward_modules_is_explicit_subset(self, v):
        mods = sorted((v.get("context_seed") or {}).get("carry_forward_modules") or [])
        assert mods == ["notifications", "settings"], (
            f"Explicit path must return exactly ['notifications', 'settings'], got {mods}"
        )

    def test_llm_profile_architecture(self, v):
        assert (v.get("context_seed") or {}).get("llm_profile") == "architecture"

    def test_no_backend_python_preserved(self, v):
        preserved = (v.get("carry_forward_report") or {}).get("preserved_paths") or []
        bad = [p for p in preserved if "/backend/" in p and p.endswith(".py")]
        assert bad == [], f"Backend Python in preserved_paths: {bad}"

    def test_no_runtime_extensions_preserved(self, v):
        preserved = (v.get("carry_forward_report") or {}).get("preserved_paths") or []
        bad = [p for p in preserved if "runtime_extensions.yaml" in p]
        assert bad == [], f"runtime_extensions.yaml in preserved_paths: {bad}"

    def test_settings_in_reused_modules(self, v):
        reused = (v.get("carry_forward_report") or {}).get("reused_modules") or []
        assert "settings" in reused

    def test_notifications_in_reused_modules(self, v):
        reused = (v.get("carry_forward_report") or {}).get("reused_modules") or []
        assert "notifications" in reused

    def test_contacts_in_dropped_modules(self, v):
        dropped = (v.get("carry_forward_report") or {}).get("dropped_modules") or []
        assert "contacts" in dropped

    def test_pipeline_in_dropped_modules(self, v):
        dropped = (v.get("carry_forward_report") or {}).get("dropped_modules") or []
        assert "pipeline" in dropped

    def test_studio_panel_shape_valid(self, fx):
        ui = fx.get("studio_ui") or {}
        assert ui.get("panel_renders") is True
        assert ui.get("report_shape_valid") is True


# ---------------------------------------------------------------------------
# TestAutoVariant
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _RUN_FIXTURE_TESTS, reason=_SKIP_REASON)
class TestAutoVariant:
    """Auto-populated carry_forward_modules via get_carry_forward_candidates."""

    @pytest.fixture(scope="class")
    def fx(self):
        return _load_fixture()

    @pytest.fixture(scope="class")
    def v(self, fx):
        return _auto(fx)

    # 1. explicit variant still passes (covered by TestExplicitVariant -- spot check here)
    def test_explicit_variant_also_succeeds(self, fx):
        explicit = _explicit(fx)
        assert explicit.get("routing", {}).get("workflow_sequence") == "conceptual_replan"

    # 2. auto variant exists
    def test_auto_variant_present(self, fx):
        assert "auto" in (fx.get("variants") or {}), "auto variant missing from fixture"

    # 3. explicit_override_used false
    def test_explicit_override_used_false(self, v):
        assert v.get("explicit_override_used") is False

    # 4. source is get_carry_forward_candidates
    def test_carry_forward_modules_source(self, v):
        assert v.get("carry_forward_modules_source") == "get_carry_forward_candidates"

    # 4b. candidates tool was actually called (hit the artifact store)
    def test_candidates_tool_called(self, v):
        assert v.get("candidates_tool_called") is True, (
            "get_carry_forward_candidates did not hit the artifact store; "
            "auto-extraction may have short-circuited"
        )

    # 5. carry_forward_modules populated
    def test_carry_forward_modules_not_empty(self, v):
        mods = (v.get("context_seed") or {}).get("carry_forward_modules") or []
        assert len(mods) > 0, "auto carry_forward_modules is empty"

    # Current implementation: all module IDs returned unfiltered at route time
    def test_auto_includes_all_crm_modules(self, v):
        """Auto-extraction returns all module IDs from the prior workspace.
        Filtering (reuse/drop) happens in AppPlanAgent at plan time, not at route time.
        This test documents the current truthful behavior.
        """
        mods = set((v.get("context_seed") or {}).get("carry_forward_modules") or [])
        crm_modules = {"settings", "notifications", "contacts", "pipeline"}
        assert crm_modules.issubset(mods), (
            f"Auto-extraction must return all CRM modules; got {mods}"
        )

    def test_auto_is_superset_of_explicit(self, fx):
        """Auto set includes everything explicit set has, plus more."""
        explicit_mods = set(
            (_explicit(fx).get("context_seed") or {}).get("carry_forward_modules") or []
        )
        auto_mods = set(
            (_auto(fx).get("context_seed") or {}).get("carry_forward_modules") or []
        )
        assert explicit_mods.issubset(auto_mods), (
            f"Auto set {auto_mods} must be a superset of explicit set {explicit_mods}"
        )

    # 6. auto includes previous_app_bundle_ref
    def test_auto_previous_app_bundle_ref(self, v):
        ref = (v.get("context_seed") or {}).get("previous_app_bundle_ref")
        assert ref and isinstance(ref, str)

    def test_auto_workflow_sequence_conceptual_replan(self, v):
        assert (v.get("routing") or {}).get("workflow_sequence") == "conceptual_replan"

    def test_auto_pivot_description_present(self, v):
        pd = (v.get("context_seed") or {}).get("pivot_description") or ""
        assert pd.strip()

    def test_auto_preserve_families_present(self, v):
        families = (v.get("context_seed") or {}).get("preserve_families")
        assert isinstance(families, list) and len(families) > 0

    # 7. auto carry_forward_report still only preserves reuse decisions
    def test_auto_report_preserves_only_reuse(self, v):
        report = v.get("carry_forward_report") or {}
        reused = set(report.get("reused_modules") or [])
        for path in report.get("preserved_paths") or []:
            parts = path.split("/")
            if len(parts) >= 2 and parts[0] == "modules":
                module_id = parts[1]
                assert module_id in reused, (
                    f"Preserved path {path!r} belongs to module {module_id!r} "
                    f"not in reused_modules {reused}"
                )

    def test_auto_settings_in_reused(self, v):
        reused = (v.get("carry_forward_report") or {}).get("reused_modules") or []
        assert "settings" in reused

    def test_auto_notifications_in_reused(self, v):
        reused = (v.get("carry_forward_report") or {}).get("reused_modules") or []
        assert "notifications" in reused

    # 8. no backend Python copied
    def test_auto_no_backend_python_preserved(self, v):
        preserved = (v.get("carry_forward_report") or {}).get("preserved_paths") or []
        bad = [p for p in preserved if "/backend/" in p and p.endswith(".py")]
        assert bad == [], f"Backend Python in preserved_paths: {bad}"

    # 9. panel report shape valid
    def test_auto_panel_report_shape(self, v):
        report = v.get("carry_forward_report") or {}
        required = {
            "previous_app_bundle_ref", "workspace_available", "preserved_paths",
            "conflicts", "skipped_paths", "reused_modules", "dropped_modules", "warnings",
        }
        missing = required - set(report.keys())
        assert not missing, f"carry_forward_report missing keys: {missing}"

    # 10. warnings are safe strings (if any)
    def test_auto_warnings_are_strings(self, v):
        warnings = v.get("carry_forward_warnings") or []
        for w in warnings:
            assert isinstance(w, str), f"Warning is not a string: {w!r}"
        report_warnings = (v.get("carry_forward_report") or {}).get("warnings") or []
        for w in report_warnings:
            assert isinstance(w, str), f"Report warning is not a string: {w!r}"

    def test_auto_no_proprietary_terms_in_warnings(self, v):
        all_warnings = list(v.get("carry_forward_warnings") or [])
        all_warnings += list((v.get("carry_forward_report") or {}).get("warnings") or [])
        bad_terms = ("app zero", "app_zero", "mozaiks-app", "blocunited")
        for w in all_warnings:
            for term in bad_terms:
                assert term not in str(w).lower(), f"Proprietary term in warning: {w!r}"

    def test_behavior_note_present(self, v):
        """Smoke must document current auto-extraction behavior truthfully."""
        note = v.get("behavior_note") or ""
        assert "all module" in note.lower() or "unfiltered" in note.lower(), (
            "behavior_note must describe unfiltered auto-extraction behavior"
        )


# ---------------------------------------------------------------------------
# TestPipelineSafety -- always run, no fixture needed
# ---------------------------------------------------------------------------


class TestPipelineSafety:
    """Deterministic safety assertions using the real Phase 7A resolver."""

    @pytest.fixture(scope="class")
    def report(self, tmp_path_factory):
        tmp = tmp_path_factory.mktemp("crm_ws")
        return _run_preservation(tmp)

    def test_workspace_available(self, report):
        assert report.get("workspace_available") is True

    def test_report_has_required_keys(self, report):
        required = {
            "previous_app_bundle_ref", "workspace_available", "preserved_paths",
            "conflicts", "skipped_paths", "reused_modules", "dropped_modules", "warnings",
        }
        missing = required - set(report.keys())
        assert not missing, f"Report missing keys: {missing}"

    def test_settings_module_yaml_preserved(self, report):
        assert "modules/settings/module.yaml" in report.get("preserved_paths", [])

    def test_notifications_module_yaml_preserved(self, report):
        assert "modules/notifications/module.yaml" in report.get("preserved_paths", [])

    def test_contacts_not_preserved(self, report):
        bad = [p for p in report.get("preserved_paths", []) if "/contacts/" in p]
        assert bad == [], f"contacts files in preserved_paths: {bad}"

    def test_pipeline_not_preserved(self, report):
        bad = [p for p in report.get("preserved_paths", []) if "/pipeline/" in p]
        assert bad == [], f"pipeline files in preserved_paths: {bad}"

    def test_no_backend_python(self, report):
        bad = [p for p in report.get("preserved_paths", []) if "/backend/" in p and p.endswith(".py")]
        assert bad == [], f"backend Python in preserved_paths: {bad}"

    def test_no_runtime_extensions(self, report):
        bad = [p for p in report.get("preserved_paths", []) if "runtime_extensions.yaml" in p]
        assert bad == [], f"runtime_extensions.yaml in preserved_paths: {bad}"

    def test_no_custom_react(self, report):
        bad = [p for p in report.get("preserved_paths", []) if p.endswith((".jsx", ".tsx", ".js"))]
        assert bad == [], f"Custom React in preserved_paths: {bad}"

    def test_only_reuse_decisions_preserved(self, report):
        reused = set(report.get("reused_modules", []))
        for path in report.get("preserved_paths", []):
            parts = path.split("/")
            if len(parts) >= 2 and parts[0] == "modules":
                module_id = parts[1]
                assert module_id in reused, (
                    f"Preserved path {path!r} belongs to {module_id!r} not in reused_modules {reused}"
                )

    def test_settings_in_reused(self, report):
        assert "settings" in report.get("reused_modules", [])

    def test_notifications_in_reused(self, report):
        assert "notifications" in report.get("reused_modules", [])

    def test_contacts_in_dropped(self, report):
        assert "contacts" in report.get("dropped_modules", [])

    def test_pipeline_in_dropped(self, report):
        assert "pipeline" in report.get("dropped_modules", [])

    def test_preserved_paths_all_allowlisted(self, report):
        from factory_app.refinement_harness.tools.resolve_carry_forward_preservation import (
            _PHASE_7A_MODULE_ALLOWLIST,
        )
        for p in report.get("preserved_paths", []):
            assert any(p.endswith(a) for a in _PHASE_7A_MODULE_ALLOWLIST), (
                f"Non-allowlisted path in preserved_paths: {p!r}"
            )

    def test_no_conflicts_in_this_scenario(self, report):
        conflicts = report.get("conflicts", {})
        assert conflicts == {}, f"Unexpected conflicts: {list(conflicts.keys())}"

    def test_no_warnings(self, report):
        assert report.get("warnings", []) == [], f"Unexpected warnings: {report.get('warnings')}"

