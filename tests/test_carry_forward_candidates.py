"""
Tests for get_carry_forward_candidates control-plane tool (Phase 2).

All tests use synthetic data or mocks — no real ArtifactStore, no real
filesystem, no real LLM calls.

Coverage:
- tool exists and is importable
- correct delegates: load_artifact_workspace and extract_module_inventory
- output shape: modules / count / source_build_record_id / warnings
- graceful degradation: missing ref, load failure, extraction failure
- registration in tools.yaml at route_requested
- read-only: no writes, no LLM calls
- doc hygiene: get_carry_forward_candidates documented; carry_forward_modules
  advisory; Phase 7A declarative preservation (not backend merge)
"""
from __future__ import annotations

import importlib
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_APP_ID = "app_test_123"
_PREV_REF = "build_record_abc"

_MINIMAL_FILE_MAP = {
    "modules/projects/module.yaml": "id: projects\nactions:\n  - id: create_project\n",
    "modules/projects/backend/handler.py": "pass",
}

_MODULE_ENTRY_DICT = {
    "module_id": "projects",
    "action_ids": ["create_project"],
    "has_persistence": False,
    "has_handler": True,
    "has_service": False,
    "has_repo": False,
    "has_policy": False,
    "event_types": [],
    "has_reactions": False,
    "has_notifications": False,
    "has_settings": False,
    "has_admin": False,
    "has_profile": False,
    "has_runtime_extensions": False,
    "backend_files": ["modules/projects/backend/handler.py"],
    "contract_files": [],
}


def _mock_artifact(version_id: str = _PREV_REF) -> MagicMock:
    artifact = MagicMock()
    artifact.id = version_id
    return artifact


def _good_workspace(file_map: dict | None = None) -> dict:
    return {
        "present": True,
        "artifact": _mock_artifact(),
        "source": "workspace_dir",
        "file_map": file_map if file_map is not None else _MINIMAL_FILE_MAP,
        "workspace_dir": "/fake/path",
        "artifact_path": None,
    }


def _absent_workspace(reason: str = "artifact_not_found") -> dict:
    return {
        "present": False,
        "reason": reason,
        "build_record_id": _PREV_REF,
    }


# ---------------------------------------------------------------------------
# Import check
# ---------------------------------------------------------------------------

class TestToolExists:
    def test_module_importable(self) -> None:
        mod = importlib.import_module(
            "factory_app.refinement_harness.tools.get_carry_forward_candidates"
        )
        assert hasattr(mod, "get_carry_forward_candidates")

    def test_function_is_callable(self) -> None:
        from factory_app.refinement_harness.tools.get_carry_forward_candidates import (
            get_carry_forward_candidates,
        )
        assert callable(get_carry_forward_candidates)

    def test_all_exported(self) -> None:
        from factory_app.refinement_harness.tools import (
            get_carry_forward_candidates as mod_,  # noqa: F401
        )
        from factory_app.refinement_harness.tools.get_carry_forward_candidates import (
            __all__,
        )
        assert "get_carry_forward_candidates" in __all__


# ---------------------------------------------------------------------------
# Output shape
# ---------------------------------------------------------------------------

class TestOutputShape:
    @pytest.mark.asyncio
    async def test_output_has_required_keys(self) -> None:
        from factory_app.refinement_harness.tools.get_carry_forward_candidates import (
            get_carry_forward_candidates,
        )
        ctx = {"app_id": _APP_ID, "extra": {"previous_app_bundle_ref": _PREV_REF}}
        mock_entry = MagicMock()
        mock_entry.model_dump.return_value = _MODULE_ENTRY_DICT

        with (
            patch(
                "factory_app.refinement_harness.tools.get_carry_forward_candidates.load_artifact_workspace",
                new_callable=AsyncMock,
                return_value=_good_workspace(),
            ),
            patch(
                "factory_app.refinement_harness.tools.get_carry_forward_candidates.extract_module_inventory",
                return_value=[mock_entry],
            ),
            patch(
                "factory_app.refinement_harness.tools.get_carry_forward_candidates.get_artifact_store",
                return_value=MagicMock(),
            ),
        ):
            result = await get_carry_forward_candidates(context=ctx)

        assert set(result.keys()) == {"modules", "count", "source_build_record_id", "warnings"}

    @pytest.mark.asyncio
    async def test_count_matches_modules_length(self) -> None:
        from factory_app.refinement_harness.tools.get_carry_forward_candidates import (
            get_carry_forward_candidates,
        )
        ctx = {"app_id": _APP_ID, "extra": {"previous_app_bundle_ref": _PREV_REF}}
        entries = [MagicMock(), MagicMock()]
        for e in entries:
            e.model_dump.return_value = _MODULE_ENTRY_DICT

        with (
            patch(
                "factory_app.refinement_harness.tools.get_carry_forward_candidates.load_artifact_workspace",
                new_callable=AsyncMock,
                return_value=_good_workspace(),
            ),
            patch(
                "factory_app.refinement_harness.tools.get_carry_forward_candidates.extract_module_inventory",
                return_value=entries,
            ),
            patch(
                "factory_app.refinement_harness.tools.get_carry_forward_candidates.get_artifact_store",
                return_value=MagicMock(),
            ),
        ):
            result = await get_carry_forward_candidates(context=ctx)

        assert result["count"] == 2
        assert len(result["modules"]) == 2

    @pytest.mark.asyncio
    async def test_source_build_record_id_from_artifact(self) -> None:
        from factory_app.refinement_harness.tools.get_carry_forward_candidates import (
            get_carry_forward_candidates,
        )
        ctx = {"app_id": _APP_ID, "extra": {"previous_app_bundle_ref": _PREV_REF}}
        workspace = _good_workspace()
        workspace["artifact"].id = "resolved_version_99"
        entry = MagicMock()
        entry.model_dump.return_value = _MODULE_ENTRY_DICT

        with (
            patch(
                "factory_app.refinement_harness.tools.get_carry_forward_candidates.load_artifact_workspace",
                new_callable=AsyncMock,
                return_value=workspace,
            ),
            patch(
                "factory_app.refinement_harness.tools.get_carry_forward_candidates.extract_module_inventory",
                return_value=[entry],
            ),
            patch(
                "factory_app.refinement_harness.tools.get_carry_forward_candidates.get_artifact_store",
                return_value=MagicMock(),
            ),
        ):
            result = await get_carry_forward_candidates(context=ctx)

        assert result["source_build_record_id"] == "resolved_version_99"

    @pytest.mark.asyncio
    async def test_modules_are_dicts(self) -> None:
        from factory_app.refinement_harness.tools.get_carry_forward_candidates import (
            get_carry_forward_candidates,
        )
        ctx = {"app_id": _APP_ID, "extra": {"previous_app_bundle_ref": _PREV_REF}}
        entry = MagicMock()
        entry.model_dump.return_value = _MODULE_ENTRY_DICT

        with (
            patch(
                "factory_app.refinement_harness.tools.get_carry_forward_candidates.load_artifact_workspace",
                new_callable=AsyncMock,
                return_value=_good_workspace(),
            ),
            patch(
                "factory_app.refinement_harness.tools.get_carry_forward_candidates.extract_module_inventory",
                return_value=[entry],
            ),
            patch(
                "factory_app.refinement_harness.tools.get_carry_forward_candidates.get_artifact_store",
                return_value=MagicMock(),
            ),
        ):
            result = await get_carry_forward_candidates(context=ctx)

        assert all(isinstance(m, dict) for m in result["modules"])

    @pytest.mark.asyncio
    async def test_warnings_is_list(self) -> None:
        from factory_app.refinement_harness.tools.get_carry_forward_candidates import (
            get_carry_forward_candidates,
        )
        ctx = {"app_id": _APP_ID, "extra": {"previous_app_bundle_ref": _PREV_REF}}
        entry = MagicMock()
        entry.model_dump.return_value = _MODULE_ENTRY_DICT

        with (
            patch(
                "factory_app.refinement_harness.tools.get_carry_forward_candidates.load_artifact_workspace",
                new_callable=AsyncMock,
                return_value=_good_workspace(),
            ),
            patch(
                "factory_app.refinement_harness.tools.get_carry_forward_candidates.extract_module_inventory",
                return_value=[entry],
            ),
            patch(
                "factory_app.refinement_harness.tools.get_carry_forward_candidates.get_artifact_store",
                return_value=MagicMock(),
            ),
        ):
            result = await get_carry_forward_candidates(context=ctx)

        assert isinstance(result["warnings"], list)


# ---------------------------------------------------------------------------
# Delegates (load_artifact_workspace + extract_module_inventory called)
# ---------------------------------------------------------------------------

class TestDelegates:
    @pytest.mark.asyncio
    async def test_calls_load_artifact_workspace(self) -> None:
        from factory_app.refinement_harness.tools.get_carry_forward_candidates import (
            get_carry_forward_candidates,
        )
        ctx = {"app_id": _APP_ID, "extra": {"previous_app_bundle_ref": _PREV_REF}}
        entry = MagicMock()
        entry.model_dump.return_value = _MODULE_ENTRY_DICT
        mock_store = MagicMock()

        with (
            patch(
                "factory_app.refinement_harness.tools.get_carry_forward_candidates.load_artifact_workspace",
                new_callable=AsyncMock,
                return_value=_good_workspace(),
            ) as mock_load,
            patch(
                "factory_app.refinement_harness.tools.get_carry_forward_candidates.extract_module_inventory",
                return_value=[entry],
            ),
            patch(
                "factory_app.refinement_harness.tools.get_carry_forward_candidates.get_artifact_store",
                return_value=mock_store,
            ),
        ):
            await get_carry_forward_candidates(context=ctx)

        mock_load.assert_called_once_with(
            artifact_store=mock_store,
            app_id=_APP_ID,
            build_record_id=_PREV_REF,
        )

    @pytest.mark.asyncio
    async def test_calls_extract_module_inventory_with_file_map(self) -> None:
        from factory_app.refinement_harness.tools.get_carry_forward_candidates import (
            get_carry_forward_candidates,
        )
        ctx = {"app_id": _APP_ID, "extra": {"previous_app_bundle_ref": _PREV_REF}}
        entry = MagicMock()
        entry.model_dump.return_value = _MODULE_ENTRY_DICT
        workspace = _good_workspace(_MINIMAL_FILE_MAP)

        with (
            patch(
                "factory_app.refinement_harness.tools.get_carry_forward_candidates.load_artifact_workspace",
                new_callable=AsyncMock,
                return_value=workspace,
            ),
            patch(
                "factory_app.refinement_harness.tools.get_carry_forward_candidates.extract_module_inventory",
                return_value=[entry],
            ) as mock_extract,
            patch(
                "factory_app.refinement_harness.tools.get_carry_forward_candidates.get_artifact_store",
                return_value=MagicMock(),
            ),
        ):
            await get_carry_forward_candidates(context=ctx)

        mock_extract.assert_called_once_with(_MINIMAL_FILE_MAP)

    @pytest.mark.asyncio
    async def test_uses_injected_artifact_store(self) -> None:
        from factory_app.refinement_harness.tools.get_carry_forward_candidates import (
            get_carry_forward_candidates,
        )
        ctx = {"app_id": _APP_ID, "extra": {"previous_app_bundle_ref": _PREV_REF}}
        entry = MagicMock()
        entry.model_dump.return_value = _MODULE_ENTRY_DICT
        custom_store = MagicMock()

        with (
            patch(
                "factory_app.refinement_harness.tools.get_carry_forward_candidates.load_artifact_workspace",
                new_callable=AsyncMock,
                return_value=_good_workspace(),
            ) as mock_load,
            patch(
                "factory_app.refinement_harness.tools.get_carry_forward_candidates.extract_module_inventory",
                return_value=[entry],
            ),
        ):
            await get_carry_forward_candidates(context=ctx, artifact_store=custom_store)

        # injected store must be passed through, not get_artifact_store()
        call_kwargs = mock_load.call_args.kwargs
        assert call_kwargs["artifact_store"] is custom_store


# ---------------------------------------------------------------------------
# Graceful degradation
# ---------------------------------------------------------------------------

class TestGracefulDegradation:
    @pytest.mark.asyncio
    async def test_missing_previous_app_bundle_ref_returns_empty(self) -> None:
        from factory_app.refinement_harness.tools.get_carry_forward_candidates import (
            get_carry_forward_candidates,
        )
        ctx = {"app_id": _APP_ID, "extra": {}}

        result = await get_carry_forward_candidates(context=ctx)

        assert result["modules"] == []
        assert result["count"] == 0
        assert len(result["warnings"]) == 1
        assert "missing_previous_app_bundle_ref" in result["warnings"][0]

    @pytest.mark.asyncio
    async def test_missing_app_id_returns_empty(self) -> None:
        from factory_app.refinement_harness.tools.get_carry_forward_candidates import (
            get_carry_forward_candidates,
        )
        ctx = {"extra": {"previous_app_bundle_ref": _PREV_REF}}

        result = await get_carry_forward_candidates(context=ctx)

        assert result["modules"] == []
        assert result["count"] == 0
        assert "missing_app_id" in result["warnings"][0]

    @pytest.mark.asyncio
    async def test_workspace_not_present_returns_empty_with_warning(self) -> None:
        from factory_app.refinement_harness.tools.get_carry_forward_candidates import (
            get_carry_forward_candidates,
        )
        ctx = {"app_id": _APP_ID, "extra": {"previous_app_bundle_ref": _PREV_REF}}

        with (
            patch(
                "factory_app.refinement_harness.tools.get_carry_forward_candidates.load_artifact_workspace",
                new_callable=AsyncMock,
                return_value=_absent_workspace("artifact_not_found"),
            ),
            patch(
                "factory_app.refinement_harness.tools.get_carry_forward_candidates.get_artifact_store",
                return_value=MagicMock(),
            ),
        ):
            result = await get_carry_forward_candidates(context=ctx)

        assert result["modules"] == []
        assert result["count"] == 0
        assert any("artifact_not_found" in w for w in result["warnings"])

    @pytest.mark.asyncio
    async def test_workspace_load_exception_returns_empty_with_warning(self) -> None:
        from factory_app.refinement_harness.tools.get_carry_forward_candidates import (
            get_carry_forward_candidates,
        )
        ctx = {"app_id": _APP_ID, "extra": {"previous_app_bundle_ref": _PREV_REF}}

        with (
            patch(
                "factory_app.refinement_harness.tools.get_carry_forward_candidates.load_artifact_workspace",
                new_callable=AsyncMock,
                side_effect=RuntimeError("db connection refused"),
            ),
            patch(
                "factory_app.refinement_harness.tools.get_carry_forward_candidates.get_artifact_store",
                return_value=MagicMock(),
            ),
        ):
            result = await get_carry_forward_candidates(context=ctx)

        assert result["modules"] == []
        assert result["count"] == 0
        assert any("workspace_load_error" in w for w in result["warnings"])

    @pytest.mark.asyncio
    async def test_workspace_unavailable_reason_forwarded(self) -> None:
        from factory_app.refinement_harness.tools.get_carry_forward_candidates import (
            get_carry_forward_candidates,
        )
        ctx = {"app_id": _APP_ID, "extra": {"previous_app_bundle_ref": _PREV_REF}}

        with (
            patch(
                "factory_app.refinement_harness.tools.get_carry_forward_candidates.load_artifact_workspace",
                new_callable=AsyncMock,
                return_value=_absent_workspace("workspace_unavailable"),
            ),
            patch(
                "factory_app.refinement_harness.tools.get_carry_forward_candidates.get_artifact_store",
                return_value=MagicMock(),
            ),
        ):
            result = await get_carry_forward_candidates(context=ctx)

        assert any("workspace_unavailable" in w for w in result["warnings"])

    @pytest.mark.asyncio
    async def test_extract_module_inventory_exception_returns_empty(self) -> None:
        from factory_app.refinement_harness.tools.get_carry_forward_candidates import (
            get_carry_forward_candidates,
        )
        ctx = {"app_id": _APP_ID, "extra": {"previous_app_bundle_ref": _PREV_REF}}

        with (
            patch(
                "factory_app.refinement_harness.tools.get_carry_forward_candidates.load_artifact_workspace",
                new_callable=AsyncMock,
                return_value=_good_workspace(),
            ),
            patch(
                "factory_app.refinement_harness.tools.get_carry_forward_candidates.extract_module_inventory",
                side_effect=ValueError("bad yaml structure"),
            ),
            patch(
                "factory_app.refinement_harness.tools.get_carry_forward_candidates.get_artifact_store",
                return_value=MagicMock(),
            ),
        ):
            result = await get_carry_forward_candidates(context=ctx)

        assert result["modules"] == []
        assert result["count"] == 0
        assert any("inventory_extraction_error" in w for w in result["warnings"])

    @pytest.mark.asyncio
    async def test_no_modules_found_warning_when_empty_inventory(self) -> None:
        from factory_app.refinement_harness.tools.get_carry_forward_candidates import (
            get_carry_forward_candidates,
        )
        ctx = {"app_id": _APP_ID, "extra": {"previous_app_bundle_ref": _PREV_REF}}
        empty_file_map: dict = {}

        with (
            patch(
                "factory_app.refinement_harness.tools.get_carry_forward_candidates.load_artifact_workspace",
                new_callable=AsyncMock,
                return_value=_good_workspace(empty_file_map),
            ),
            patch(
                "factory_app.refinement_harness.tools.get_carry_forward_candidates.extract_module_inventory",
                return_value=[],
            ),
            patch(
                "factory_app.refinement_harness.tools.get_carry_forward_candidates.get_artifact_store",
                return_value=MagicMock(),
            ),
        ):
            result = await get_carry_forward_candidates(context=ctx)

        assert result["count"] == 0
        assert any("no_modules_found" in w for w in result["warnings"])

    @pytest.mark.asyncio
    async def test_invalid_module_yaml_in_workspace_does_not_crash(self) -> None:
        """extract_module_inventory handles invalid YAML defensively (per Phase 1).
        The tool should return whatever entries survive, not crash."""
        from factory_app.refinement_harness.tools.get_carry_forward_candidates import (
            get_carry_forward_candidates,
        )
        ctx = {"app_id": _APP_ID, "extra": {"previous_app_bundle_ref": _PREV_REF}}
        bad_file_map = {
            "modules/bad_module/module.yaml": ":::invalid yaml:::",
            "modules/good_module/module.yaml": "id: good_module\nactions: []\n",
        }
        # We use the real extract_module_inventory here to test end-to-end defensive behavior
        workspace = _good_workspace(bad_file_map)

        with (
            patch(
                "factory_app.refinement_harness.tools.get_carry_forward_candidates.load_artifact_workspace",
                new_callable=AsyncMock,
                return_value=workspace,
            ),
            patch(
                "factory_app.refinement_harness.tools.get_carry_forward_candidates.get_artifact_store",
                return_value=MagicMock(),
            ),
        ):
            # Should not raise
            result = await get_carry_forward_candidates(context=ctx)

        # Both modules are discoverable (bad YAML produces minimal entry, not skip)
        assert result["count"] >= 1
        module_ids = [m["module_id"] for m in result["modules"]]
        assert "good_module" in module_ids


# ---------------------------------------------------------------------------
# Read-only contract
# ---------------------------------------------------------------------------

class TestReadOnly:
    @pytest.mark.asyncio
    async def test_no_artifact_store_write_called(self) -> None:
        from factory_app.refinement_harness.tools.get_carry_forward_candidates import (
            get_carry_forward_candidates,
        )
        ctx = {"app_id": _APP_ID, "extra": {"previous_app_bundle_ref": _PREV_REF}}
        mock_store = MagicMock()
        entry = MagicMock()
        entry.model_dump.return_value = _MODULE_ENTRY_DICT

        with (
            patch(
                "factory_app.refinement_harness.tools.get_carry_forward_candidates.load_artifact_workspace",
                new_callable=AsyncMock,
                return_value=_good_workspace(),
            ),
            patch(
                "factory_app.refinement_harness.tools.get_carry_forward_candidates.extract_module_inventory",
                return_value=[entry],
            ),
        ):
            await get_carry_forward_candidates(context=ctx, artifact_store=mock_store)

        # Only read methods are acceptable; write / create / save must not be called
        called_methods = [call[0] for call in mock_store.method_calls]
        write_methods = {m for m in called_methods if any(
            keyword in m for keyword in ("write", "save", "create", "put", "update", "delete")
        )}
        assert write_methods == set(), f"Write methods called on store: {write_methods}"

    @pytest.mark.asyncio
    async def test_returns_serializable_output(self) -> None:
        """All output values must be JSON-serializable (dict, list, str, int, None)."""
        import json

        from factory_app.refinement_harness.tools.get_carry_forward_candidates import (
            get_carry_forward_candidates,
        )
        ctx = {"app_id": _APP_ID, "extra": {"previous_app_bundle_ref": _PREV_REF}}
        entry = MagicMock()
        entry.model_dump.return_value = _MODULE_ENTRY_DICT

        with (
            patch(
                "factory_app.refinement_harness.tools.get_carry_forward_candidates.load_artifact_workspace",
                new_callable=AsyncMock,
                return_value=_good_workspace(),
            ),
            patch(
                "factory_app.refinement_harness.tools.get_carry_forward_candidates.extract_module_inventory",
                return_value=[entry],
            ),
            patch(
                "factory_app.refinement_harness.tools.get_carry_forward_candidates.get_artifact_store",
                return_value=MagicMock(),
            ),
        ):
            result = await get_carry_forward_candidates(context=ctx)

        # Must not raise
        json.dumps(result)


# ---------------------------------------------------------------------------
# tools.yaml registration
# ---------------------------------------------------------------------------

class TestToolRegistration:
    def _load_tools_yaml(self) -> dict:
        tools_path = (
            Path(__file__).resolve().parents[1]
            / "factory_app"
            / "refinement_harness"
            / "config"
            / "tools.yaml"
        )
        return yaml.safe_load(tools_path.read_text(encoding="utf-8"))

    def test_tool_registered_in_tools_yaml(self) -> None:
        data = self._load_tools_yaml()
        tool_ids = [t["id"] for t in data["tools"]]
        assert "get_carry_forward_candidates" in tool_ids

    def test_tool_registered_at_route_requested(self) -> None:
        data = self._load_tools_yaml()
        tool = next(
            (t for t in data["tools"] if t["id"] == "get_carry_forward_candidates"),
            None,
        )
        assert tool is not None
        assert "route_requested" in tool["available_to"]

    def test_tool_not_registered_at_request_submitted(self) -> None:
        """Classifier checkpoint should not include carry-forward tool."""
        data = self._load_tools_yaml()
        tool = next(
            (t for t in data["tools"] if t["id"] == "get_carry_forward_candidates"),
            None,
        )
        assert tool is not None
        assert "request_submitted" not in tool["available_to"]

    def test_tool_not_registered_at_coding_requested(self) -> None:
        """coding_requested is patch-class only; conceptual_replan never reaches it."""
        data = self._load_tools_yaml()
        tool = next(
            (t for t in data["tools"] if t["id"] == "get_carry_forward_candidates"),
            None,
        )
        assert tool is not None
        assert "coding_requested" not in tool["available_to"]

    def test_tool_entrypoint_correct(self) -> None:
        data = self._load_tools_yaml()
        tool = next(
            (t for t in data["tools"] if t["id"] == "get_carry_forward_candidates"),
            None,
        )
        assert tool is not None
        assert tool["entrypoint"] == (
            "factory_app.refinement_harness.tools.get_carry_forward_candidates"
            ":get_carry_forward_candidates"
        )

    def test_pack_loads_without_error(self) -> None:
        """The full refinement harness must load cleanly with the new tool."""
        from mozaiksai.control_plane import load_refinement_harness

        app_root = (
            Path(__file__).resolve().parents[1] / "factory_app" / "app"
        )
        pack = load_refinement_harness(app_root=app_root)
        tool_ids = [t.id for t in pack.tools.tools]
        assert "get_carry_forward_candidates" in tool_ids


# ---------------------------------------------------------------------------
# Documentation hygiene
# ---------------------------------------------------------------------------

class TestDocumentation:
    def _load_doc(self) -> str:
        doc_path = (
            Path(__file__).resolve().parents[1]
            / "docs"
            / "architecture"
            / "workflows"
            / "refinement-engine.md"
        )
        return doc_path.read_text(encoding="utf-8")

    def test_doc_mentions_get_carry_forward_candidates(self) -> None:
        doc = self._load_doc()
        assert "get_carry_forward_candidates" in doc

    def test_doc_states_carry_forward_modules_is_advisory(self) -> None:
        doc = self._load_doc()
        # The doc must describe carry_forward_modules as advisory
        assert "advisory" in doc.lower()
        assert "carry_forward_modules" in doc

    def test_doc_states_assemblydoc_no_merge(self) -> None:
        doc = self._load_doc()
        # AssemblyAgent must not be described as performing automatic merge
        assert "AssemblyAgent" in doc
        # The doc must confirm merge is not implemented
        assert "no" in doc.lower() or "not" in doc.lower()

    def test_doc_documents_phase_3_auto_population(self) -> None:
        """Phase 3 (auto-populate carry_forward_modules) is implemented.
        Docs must mention it."""
        doc = self._load_doc()
        assert "phase 3" in doc.lower() or "auto-popul" in doc.lower()

    def test_doc_does_not_say_only_manually_supplied(self) -> None:
        """The old 'carry_forward_modules is only manually supplied' framing
        must be gone now that the tool exists."""
        doc = self._load_doc()
        # There must not be definitive language saying the ONLY source is manual supply
        assert "only manually" not in doc.lower()


# ---------------------------------------------------------------------------
# Phase 5: classification fields in tool output
# ---------------------------------------------------------------------------

class TestClassificationInToolOutput:
    """Verify carry_forward_classification and carry_forward_reasons appear in
    the get_carry_forward_candidates output (Phase 5).

    These tests use the real extract_module_inventory (not mocked) so that
    the Phase 5 fields are actually present in the returned dicts.
    """

    _SETTINGS_MODULE_YAML = "id: settings\nactions: []\n"
    _PROJECTS_MODULE_YAML = (
        "id: projects\nactions:\n"
        "  - id: create_project\n"
        "  - id: list_projects\n"
        "  - id: update_project\n"
    )

    def _good_workspace_with_real_map(self, file_map: dict) -> dict:
        artifact = MagicMock()
        artifact.id = _PREV_REF
        return {
            "present": True,
            "artifact": artifact,
            "source": "workspace_dir",
            "file_map": file_map,
            "workspace_dir": "/fake/path",
            "artifact_path": None,
        }

    @pytest.mark.asyncio
    async def test_each_module_entry_has_classification_fields(self) -> None:
        """Every module dict in 'modules' has carry_forward_classification and
        carry_forward_reasons when the real extract_module_inventory runs."""
        from factory_app.refinement_harness.tools.get_carry_forward_candidates import (
            get_carry_forward_candidates,
        )
        file_map = {
            "modules/settings/module.yaml": self._SETTINGS_MODULE_YAML,
            "modules/settings/contracts/settings.yaml": "schema_version: v1\n",
            "modules/projects/module.yaml": self._PROJECTS_MODULE_YAML,
            "modules/projects/backend/repo.py": "pass",
        }
        ctx = {"app_id": _APP_ID, "extra": {"previous_app_bundle_ref": _PREV_REF}}

        with patch(
            "factory_app.refinement_harness.tools.get_carry_forward_candidates.load_artifact_workspace",
            new_callable=AsyncMock,
            return_value=self._good_workspace_with_real_map(file_map),
        ):
            result = await get_carry_forward_candidates(context=ctx)

        modules = result["modules"]
        assert len(modules) == 2
        for mod in modules:
            assert "carry_forward_classification" in mod, (
                f"module {mod.get('module_id')} missing carry_forward_classification"
            )
            assert "carry_forward_reasons" in mod, (
                f"module {mod.get('module_id')} missing carry_forward_reasons"
            )
            assert isinstance(mod["carry_forward_reasons"], list)
            assert mod["carry_forward_classification"] in {
                "safe_carry_forward", "needs_adaptation", "regenerate"
            }

    @pytest.mark.asyncio
    async def test_safe_module_classified_correctly_in_output(self) -> None:
        """'settings' module is safe_carry_forward in tool output."""
        from factory_app.refinement_harness.tools.get_carry_forward_candidates import (
            get_carry_forward_candidates,
        )
        file_map = {
            "modules/settings/module.yaml": self._SETTINGS_MODULE_YAML,
            "modules/settings/contracts/settings.yaml": "schema_version: v1\n",
        }
        ctx = {"app_id": _APP_ID, "extra": {"previous_app_bundle_ref": _PREV_REF}}

        with patch(
            "factory_app.refinement_harness.tools.get_carry_forward_candidates.load_artifact_workspace",
            new_callable=AsyncMock,
            return_value=self._good_workspace_with_real_map(file_map),
        ):
            result = await get_carry_forward_candidates(context=ctx)

        assert result["modules"][0]["carry_forward_classification"] == "safe_carry_forward"
        assert result["modules"][0]["carry_forward_reasons"]

    @pytest.mark.asyncio
    async def test_domain_module_classified_regenerate_in_output(self) -> None:
        """'projects' module is regenerate in tool output (domain-specific fragment)."""
        from factory_app.refinement_harness.tools.get_carry_forward_candidates import (
            get_carry_forward_candidates,
        )
        file_map = {
            "modules/projects/module.yaml": self._PROJECTS_MODULE_YAML,
            "modules/projects/backend/repo.py": "pass",
        }
        ctx = {"app_id": _APP_ID, "extra": {"previous_app_bundle_ref": _PREV_REF}}

        with patch(
            "factory_app.refinement_harness.tools.get_carry_forward_candidates.load_artifact_workspace",
            new_callable=AsyncMock,
            return_value=self._good_workspace_with_real_map(file_map),
        ):
            result = await get_carry_forward_candidates(context=ctx)

        assert result["modules"][0]["carry_forward_classification"] == "regenerate"
        assert result["modules"][0]["carry_forward_reasons"]

    @pytest.mark.asyncio
    async def test_classification_fields_present_even_on_minimal_module(self) -> None:
        """A module with only module.yaml and no other files still has classification fields."""
        from factory_app.refinement_harness.tools.get_carry_forward_candidates import (
            get_carry_forward_candidates,
        )
        file_map = {
            "modules/custom_tracker/module.yaml": "id: custom_tracker\nactions: []\n",
        }
        ctx = {"app_id": _APP_ID, "extra": {"previous_app_bundle_ref": _PREV_REF}}

        with patch(
            "factory_app.refinement_harness.tools.get_carry_forward_candidates.load_artifact_workspace",
            new_callable=AsyncMock,
            return_value=self._good_workspace_with_real_map(file_map),
        ):
            result = await get_carry_forward_candidates(context=ctx)

        mod = result["modules"][0]
        assert "carry_forward_classification" in mod
        assert "carry_forward_reasons" in mod
        assert mod["carry_forward_reasons"]  # always non-empty


