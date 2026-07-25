"""
Tests for read_carry_forward_module_contract control-plane / AppGenerator tool (Phase 4).

All tests use synthetic data or mocks — no real ArtifactStore, no real
filesystem, no real LLM calls.

Coverage:
- tool returns module.yaml for existing module
- tool returns selected contract files
- tool rejects backend/*.py requests (disallowed)
- tool rejects path traversal and arbitrary filenames
- tool returns warnings for missing module
- tool lists missing files without raising
- tool delegates workspace loading to load_artifact_workspace
- tool has no writes / no LLM calls / no artifact mutations
- tool is registered in AppGenerator tools.yaml under AppPlanAgent only
- AppPlanAgent guidance mentions read-back tool
- AppPlanAgent guidance says backend source is not read
- AppPlanAgent guidance says no file copying/merge
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_APP_ID = "app_phase4_test"
_PREV_REF = "artifact_version_phase4"

_MODULE_YAML_CONTENT = "id: notifications\nactions:\n  - id: send_notification\n"
_EVENTS_YAML_CONTENT = "events:\n  - type: notification.sent\n"
_REACTIONS_YAML_CONTENT = "schema_version: mozaiks.reactions.v1\nreactions: []\n"
_SETTINGS_YAML_CONTENT = "schema_version: v1\npreferences: []\n"
_ADMIN_YAML_CONTENT = "schema_version: v1\npanels: []\n"
_PROFILE_YAML_CONTENT = "schema_version: mozaiks.profile.v1\npanels: []\n"
_RELATIONSHIPS_YAML_CONTENT = "schema_version: mozaiks.relationships.v1\nproviders: []\n"
_POLICY_HOOKS_YAML_CONTENT = "schema_version: mozaiks.policy_hooks.v1\nhooks: []\n"
_RUNTIME_EXT_CONTENT = "api_router:\n  enabled: false\n"


def _make_file_map(module_id: str = "notifications", **extras: str) -> dict[str, str]:
    """Build a synthetic file_map for one module."""
    base = {
        f"modules/{module_id}/module.yaml": _MODULE_YAML_CONTENT,
        f"modules/{module_id}/contracts/events.yaml": _EVENTS_YAML_CONTENT,
        f"modules/{module_id}/contracts/settings.yaml": _SETTINGS_YAML_CONTENT,
    }
    base.update(extras)
    return base


def _make_workspace(file_map: dict[str, str]) -> dict:
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


def _absent_workspace(reason: str = "artifact_not_found") -> dict:
    return {
        "present": False,
        "reason": reason,
        "artifact_version_id": _PREV_REF,
    }


def _ctx(
    app_id: str = _APP_ID,
    previous_app_bundle_ref: str = _PREV_REF,
    **extra,
) -> dict:
    return {"app_id": app_id, "previous_app_bundle_ref": previous_app_bundle_ref, **extra}


# ---------------------------------------------------------------------------
# 1. Returns module.yaml for existing module
# ---------------------------------------------------------------------------

class TestReturnsModuleYaml:
    @pytest.mark.asyncio
    async def test_module_yaml_content_returned(self) -> None:
        from factory_app.refinement_harness.tools.read_carry_forward_module_contract import (
            read_carry_forward_module_contract,
        )
        file_map = _make_file_map("notifications")

        with patch(
            "factory_app.refinement_harness.tools.read_carry_forward_module_contract.load_artifact_workspace",
            new_callable=AsyncMock,
            return_value=_make_workspace(file_map),
        ):
            result = await read_carry_forward_module_contract(
                "notifications",
                files=["module.yaml"],
                context_variables=_ctx(),
            )

        assert result["module_id"] == "notifications"
        assert "module.yaml" in result["files"]
        assert result["files"]["module.yaml"] == _MODULE_YAML_CONTENT
        assert not result["warnings"]

    @pytest.mark.asyncio
    async def test_module_yaml_in_available_files(self) -> None:
        from factory_app.refinement_harness.tools.read_carry_forward_module_contract import (
            read_carry_forward_module_contract,
        )
        file_map = _make_file_map("notifications")

        with patch(
            "factory_app.refinement_harness.tools.read_carry_forward_module_contract.load_artifact_workspace",
            new_callable=AsyncMock,
            return_value=_make_workspace(file_map),
        ):
            result = await read_carry_forward_module_contract(
                "notifications",
                context_variables=_ctx(),
            )

        assert "module.yaml" in result["available_files"]


# ---------------------------------------------------------------------------
# 2. Returns selected contract files
# ---------------------------------------------------------------------------

class TestSelectedContractFiles:
    @pytest.mark.asyncio
    async def test_returns_only_requested_files(self) -> None:
        from factory_app.refinement_harness.tools.read_carry_forward_module_contract import (
            read_carry_forward_module_contract,
        )
        file_map = _make_file_map(
            "notifications",
            **{
                "modules/notifications/contracts/reactions.yaml": _REACTIONS_YAML_CONTENT,
                "modules/notifications/contracts/admin.yaml": _ADMIN_YAML_CONTENT,
            },
        )

        with patch(
            "factory_app.refinement_harness.tools.read_carry_forward_module_contract.load_artifact_workspace",
            new_callable=AsyncMock,
            return_value=_make_workspace(file_map),
        ):
            result = await read_carry_forward_module_contract(
                "notifications",
                files=["contracts/events.yaml", "contracts/reactions.yaml"],
                context_variables=_ctx(),
            )

        assert set(result["files"].keys()) == {"contracts/events.yaml", "contracts/reactions.yaml"}
        assert result["files"]["contracts/events.yaml"] == _EVENTS_YAML_CONTENT
        assert result["files"]["contracts/reactions.yaml"] == _REACTIONS_YAML_CONTENT

    @pytest.mark.asyncio
    async def test_null_files_returns_all_available(self) -> None:
        from factory_app.refinement_harness.tools.read_carry_forward_module_contract import (
            read_carry_forward_module_contract,
        )
        file_map = _make_file_map("notifications")

        with patch(
            "factory_app.refinement_harness.tools.read_carry_forward_module_contract.load_artifact_workspace",
            new_callable=AsyncMock,
            return_value=_make_workspace(file_map),
        ):
            result = await read_carry_forward_module_contract(
                "notifications",
                files=None,
                context_variables=_ctx(),
            )

        # All files in file_map that are in allowed set should be returned
        for key in result["files"]:
            assert key in result["available_files"]

    @pytest.mark.asyncio
    async def test_runtime_extensions_yaml_returnable(self) -> None:
        from factory_app.refinement_harness.tools.read_carry_forward_module_contract import (
            read_carry_forward_module_contract,
        )
        file_map = _make_file_map(
            "notifications",
            **{"modules/notifications/runtime_extensions.yaml": _RUNTIME_EXT_CONTENT},
        )

        with patch(
            "factory_app.refinement_harness.tools.read_carry_forward_module_contract.load_artifact_workspace",
            new_callable=AsyncMock,
            return_value=_make_workspace(file_map),
        ):
            result = await read_carry_forward_module_contract(
                "notifications",
                files=["runtime_extensions.yaml"],
                context_variables=_ctx(),
            )

        assert "runtime_extensions.yaml" in result["files"]
        assert result["files"]["runtime_extensions.yaml"] == _RUNTIME_EXT_CONTENT

    @pytest.mark.asyncio
    async def test_profile_yaml_returnable(self) -> None:
        from factory_app.refinement_harness.tools.read_carry_forward_module_contract import (
            read_carry_forward_module_contract,
        )
        file_map = _make_file_map(
            "notifications",
            **{"modules/notifications/contracts/profile.yaml": _PROFILE_YAML_CONTENT},
        )

        with patch(
            "factory_app.refinement_harness.tools.read_carry_forward_module_contract.load_artifact_workspace",
            new_callable=AsyncMock,
            return_value=_make_workspace(file_map),
        ):
            result = await read_carry_forward_module_contract(
                "notifications",
                files=["contracts/profile.yaml"],
                context_variables=_ctx(),
            )

        assert "contracts/profile.yaml" in result["files"]

    @pytest.mark.asyncio
    async def test_relationships_yaml_returnable(self) -> None:
        from factory_app.refinement_harness.tools.read_carry_forward_module_contract import (
            read_carry_forward_module_contract,
        )
        file_map = _make_file_map(
            "notifications",
            **{"modules/notifications/contracts/relationships.yaml": _RELATIONSHIPS_YAML_CONTENT},
        )

        with patch(
            "factory_app.refinement_harness.tools.read_carry_forward_module_contract.load_artifact_workspace",
            new_callable=AsyncMock,
            return_value=_make_workspace(file_map),
        ):
            result = await read_carry_forward_module_contract(
                "notifications",
                files=["contracts/relationships.yaml"],
                context_variables=_ctx(),
            )

        assert "contracts/relationships.yaml" in result["files"]
        assert result["files"]["contracts/relationships.yaml"] == _RELATIONSHIPS_YAML_CONTENT

    @pytest.mark.asyncio
    async def test_policy_hooks_yaml_returnable(self) -> None:
        from factory_app.refinement_harness.tools.read_carry_forward_module_contract import (
            read_carry_forward_module_contract,
        )
        file_map = _make_file_map(
            "notifications",
            **{"modules/notifications/contracts/policy_hooks.yaml": _POLICY_HOOKS_YAML_CONTENT},
        )

        with patch(
            "factory_app.refinement_harness.tools.read_carry_forward_module_contract.load_artifact_workspace",
            new_callable=AsyncMock,
            return_value=_make_workspace(file_map),
        ):
            result = await read_carry_forward_module_contract(
                "notifications",
                files=["contracts/policy_hooks.yaml"],
                context_variables=_ctx(),
            )

        assert "contracts/policy_hooks.yaml" in result["files"]
        assert result["files"]["contracts/policy_hooks.yaml"] == _POLICY_HOOKS_YAML_CONTENT


# ---------------------------------------------------------------------------
# 3. Rejects backend/*.py requests
# ---------------------------------------------------------------------------

class TestRejectsDisallowedFiles:
    @pytest.mark.asyncio
    async def test_backend_handler_py_rejected(self) -> None:
        from factory_app.refinement_harness.tools.read_carry_forward_module_contract import (
            read_carry_forward_module_contract,
        )
        file_map = {
            "modules/notifications/module.yaml": _MODULE_YAML_CONTENT,
            "modules/notifications/backend/handler.py": "pass",
        }

        with patch(
            "factory_app.refinement_harness.tools.read_carry_forward_module_contract.load_artifact_workspace",
            new_callable=AsyncMock,
            return_value=_make_workspace(file_map),
        ):
            result = await read_carry_forward_module_contract(
                "notifications",
                files=["backend/handler.py"],
                context_variables=_ctx(),
            )

        assert "backend/handler.py" not in result["files"]
        assert any("disallowed_file" in w for w in result["warnings"])

    @pytest.mark.asyncio
    async def test_backend_service_py_rejected(self) -> None:
        from factory_app.refinement_harness.tools.read_carry_forward_module_contract import (
            read_carry_forward_module_contract,
        )
        file_map = {"modules/auth/module.yaml": _MODULE_YAML_CONTENT}

        with patch(
            "factory_app.refinement_harness.tools.read_carry_forward_module_contract.load_artifact_workspace",
            new_callable=AsyncMock,
            return_value=_make_workspace(file_map),
        ):
            result = await read_carry_forward_module_contract(
                "auth",
                files=["backend/service.py", "backend/repo.py"],
                context_variables=_ctx(),
            )

        assert not result["files"]
        assert len([w for w in result["warnings"] if "disallowed_file" in w]) == 2

    @pytest.mark.asyncio
    async def test_allowed_files_not_blocked_alongside_disallowed(self) -> None:
        """Allowed files are returned even when disallowed ones are mixed in."""
        from factory_app.refinement_harness.tools.read_carry_forward_module_contract import (
            read_carry_forward_module_contract,
        )
        file_map = _make_file_map("notifications")

        with patch(
            "factory_app.refinement_harness.tools.read_carry_forward_module_contract.load_artifact_workspace",
            new_callable=AsyncMock,
            return_value=_make_workspace(file_map),
        ):
            result = await read_carry_forward_module_contract(
                "notifications",
                files=["module.yaml", "backend/handler.py"],
                context_variables=_ctx(),
            )

        assert "module.yaml" in result["files"]
        assert "backend/handler.py" not in result["files"]
        assert any("disallowed_file" in w for w in result["warnings"])


# ---------------------------------------------------------------------------
# 4. Rejects path traversal and arbitrary filenames
# ---------------------------------------------------------------------------

class TestRejectsPathTraversal:
    @pytest.mark.asyncio
    async def test_path_traversal_in_files_rejected(self) -> None:
        from factory_app.refinement_harness.tools.read_carry_forward_module_contract import (
            read_carry_forward_module_contract,
        )
        file_map = {"modules/auth/module.yaml": _MODULE_YAML_CONTENT}

        with patch(
            "factory_app.refinement_harness.tools.read_carry_forward_module_contract.load_artifact_workspace",
            new_callable=AsyncMock,
            return_value=_make_workspace(file_map),
        ):
            result = await read_carry_forward_module_contract(
                "auth",
                files=["../../config/ai.json"],
                context_variables=_ctx(),
            )

        assert not result["files"]
        assert any("disallowed_file" in w for w in result["warnings"])

    @pytest.mark.asyncio
    async def test_invalid_module_id_returns_empty(self) -> None:
        from factory_app.refinement_harness.tools.read_carry_forward_module_contract import (
            read_carry_forward_module_contract,
        )
        with patch(
            "factory_app.refinement_harness.tools.read_carry_forward_module_contract.load_artifact_workspace",
            new_callable=AsyncMock,
        ) as mock_load:
            result = await read_carry_forward_module_contract(
                "../etc/passwd",
                context_variables=_ctx(),
            )

        assert result["files"] == {}
        assert any("invalid_module_id" in w for w in result["warnings"])
        # load_artifact_workspace must NOT be called for invalid module_id
        mock_load.assert_not_called()

    @pytest.mark.asyncio
    async def test_module_id_with_slash_rejected(self) -> None:
        from factory_app.refinement_harness.tools.read_carry_forward_module_contract import (
            read_carry_forward_module_contract,
        )
        with patch(
            "factory_app.refinement_harness.tools.read_carry_forward_module_contract.load_artifact_workspace",
            new_callable=AsyncMock,
        ) as mock_load:
            result = await read_carry_forward_module_contract(
                "auth/../../secret",
                context_variables=_ctx(),
            )

        assert any("invalid_module_id" in w for w in result["warnings"])
        mock_load.assert_not_called()

    @pytest.mark.asyncio
    async def test_arbitrary_filename_rejected(self) -> None:
        from factory_app.refinement_harness.tools.read_carry_forward_module_contract import (
            read_carry_forward_module_contract,
        )
        file_map = {"modules/auth/module.yaml": _MODULE_YAML_CONTENT}

        with patch(
            "factory_app.refinement_harness.tools.read_carry_forward_module_contract.load_artifact_workspace",
            new_callable=AsyncMock,
            return_value=_make_workspace(file_map),
        ):
            result = await read_carry_forward_module_contract(
                "auth",
                files=["app.json", "data/contract.json", "ui/index.js"],
                context_variables=_ctx(),
            )

        assert not result["files"]
        # All three should have disallowed warnings
        disallowed_warnings = [w for w in result["warnings"] if "disallowed_file" in w]
        assert len(disallowed_warnings) == 3


# ---------------------------------------------------------------------------
# 5. Returns warnings for missing module
# ---------------------------------------------------------------------------

class TestMissingModule:
    @pytest.mark.asyncio
    async def test_warning_when_module_not_in_workspace(self) -> None:
        from factory_app.refinement_harness.tools.read_carry_forward_module_contract import (
            read_carry_forward_module_contract,
        )
        file_map = {"modules/settings/module.yaml": _MODULE_YAML_CONTENT}

        with patch(
            "factory_app.refinement_harness.tools.read_carry_forward_module_contract.load_artifact_workspace",
            new_callable=AsyncMock,
            return_value=_make_workspace(file_map),
        ):
            result = await read_carry_forward_module_contract(
                "nonexistent_module",
                context_variables=_ctx(),
            )

        assert result["files"] == {}
        assert result["available_files"] == []
        assert any("module_not_found" in w or "empty" in w for w in result["warnings"])

    @pytest.mark.asyncio
    async def test_empty_workspace_triggers_warning(self) -> None:
        from factory_app.refinement_harness.tools.read_carry_forward_module_contract import (
            read_carry_forward_module_contract,
        )
        with patch(
            "factory_app.refinement_harness.tools.read_carry_forward_module_contract.load_artifact_workspace",
            new_callable=AsyncMock,
            return_value=_make_workspace({}),
        ):
            result = await read_carry_forward_module_contract(
                "notifications",
                context_variables=_ctx(),
            )

        assert result["files"] == {}
        assert result["warnings"]

    @pytest.mark.asyncio
    async def test_absent_workspace_returns_warning(self) -> None:
        from factory_app.refinement_harness.tools.read_carry_forward_module_contract import (
            read_carry_forward_module_contract,
        )
        with patch(
            "factory_app.refinement_harness.tools.read_carry_forward_module_contract.load_artifact_workspace",
            new_callable=AsyncMock,
            return_value=_absent_workspace("artifact_not_found"),
        ):
            result = await read_carry_forward_module_contract(
                "notifications",
                context_variables=_ctx(),
            )

        assert result["files"] == {}
        assert any("workspace_unavailable" in w for w in result["warnings"])


# ---------------------------------------------------------------------------
# 6. Lists missing files without raising
# ---------------------------------------------------------------------------

class TestMissingFiles:
    @pytest.mark.asyncio
    async def test_missing_requested_file_in_missing_files(self) -> None:
        from factory_app.refinement_harness.tools.read_carry_forward_module_contract import (
            read_carry_forward_module_contract,
        )
        # Only module.yaml exists; events.yaml does not
        file_map = {"modules/notifications/module.yaml": _MODULE_YAML_CONTENT}

        with patch(
            "factory_app.refinement_harness.tools.read_carry_forward_module_contract.load_artifact_workspace",
            new_callable=AsyncMock,
            return_value=_make_workspace(file_map),
        ):
            result = await read_carry_forward_module_contract(
                "notifications",
                files=["module.yaml", "contracts/events.yaml"],
                context_variables=_ctx(),
            )

        assert "module.yaml" in result["files"]
        assert "contracts/events.yaml" not in result["files"]
        assert "contracts/events.yaml" in result["missing_files"]
        assert not result["warnings"]

    @pytest.mark.asyncio
    async def test_all_requested_missing_files_listed(self) -> None:
        from factory_app.refinement_harness.tools.read_carry_forward_module_contract import (
            read_carry_forward_module_contract,
        )
        file_map = {"modules/notifications/module.yaml": _MODULE_YAML_CONTENT}

        with patch(
            "factory_app.refinement_harness.tools.read_carry_forward_module_contract.load_artifact_workspace",
            new_callable=AsyncMock,
            return_value=_make_workspace(file_map),
        ):
            result = await read_carry_forward_module_contract(
                "notifications",
                files=["contracts/events.yaml", "contracts/admin.yaml"],
                context_variables=_ctx(),
            )

        assert "contracts/events.yaml" in result["missing_files"]
        assert "contracts/admin.yaml" in result["missing_files"]
        assert result["files"] == {}

    @pytest.mark.asyncio
    async def test_never_raises_on_missing(self) -> None:
        from factory_app.refinement_harness.tools.read_carry_forward_module_contract import (
            read_carry_forward_module_contract,
        )
        with patch(
            "factory_app.refinement_harness.tools.read_carry_forward_module_contract.load_artifact_workspace",
            new_callable=AsyncMock,
            return_value=_make_workspace({}),
        ):
            # Should not raise
            result = await read_carry_forward_module_contract(
                "anything",
                files=["module.yaml", "contracts/events.yaml"],
                context_variables=_ctx(),
            )
        assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# 7. Uses load_artifact_workspace
# ---------------------------------------------------------------------------

class TestDelegatesWorkspaceLoading:
    @pytest.mark.asyncio
    async def test_load_artifact_workspace_called(self) -> None:
        from factory_app.refinement_harness.tools.read_carry_forward_module_contract import (
            read_carry_forward_module_contract,
        )
        with patch(
            "factory_app.refinement_harness.tools.read_carry_forward_module_contract.load_artifact_workspace",
            new_callable=AsyncMock,
            return_value=_make_workspace(_make_file_map()),
        ) as mock_load:
            await read_carry_forward_module_contract(
                "notifications",
                context_variables=_ctx(),
            )

        mock_load.assert_called_once()

    @pytest.mark.asyncio
    async def test_workspace_called_with_correct_ids(self) -> None:
        from factory_app.refinement_harness.tools.read_carry_forward_module_contract import (
            read_carry_forward_module_contract,
        )
        with patch(
            "factory_app.refinement_harness.tools.read_carry_forward_module_contract.load_artifact_workspace",
            new_callable=AsyncMock,
            return_value=_make_workspace(_make_file_map()),
        ) as mock_load:
            await read_carry_forward_module_contract(
                "notifications",
                context_variables={
                    "app_id": "my_app_99",
                    "previous_app_bundle_ref": "ver_xyz",
                },
            )

        call_kwargs = mock_load.call_args.kwargs
        assert call_kwargs["app_id"] == "my_app_99"
        assert call_kwargs["artifact_version_id"] == "ver_xyz"

    @pytest.mark.asyncio
    async def test_missing_app_id_skips_load(self) -> None:
        from factory_app.refinement_harness.tools.read_carry_forward_module_contract import (
            read_carry_forward_module_contract,
        )
        with patch(
            "factory_app.refinement_harness.tools.read_carry_forward_module_contract.load_artifact_workspace",
            new_callable=AsyncMock,
        ) as mock_load:
            result = await read_carry_forward_module_contract(
                "notifications",
                context_variables={"previous_app_bundle_ref": _PREV_REF},
            )

        mock_load.assert_not_called()
        assert any("missing_app_id" in w for w in result["warnings"])

    @pytest.mark.asyncio
    async def test_missing_prev_ref_skips_load(self) -> None:
        from factory_app.refinement_harness.tools.read_carry_forward_module_contract import (
            read_carry_forward_module_contract,
        )
        with patch(
            "factory_app.refinement_harness.tools.read_carry_forward_module_contract.load_artifact_workspace",
            new_callable=AsyncMock,
        ) as mock_load:
            result = await read_carry_forward_module_contract(
                "notifications",
                context_variables={"app_id": _APP_ID},
            )

        mock_load.assert_not_called()
        assert any("missing_previous_app_bundle_ref" in w for w in result["warnings"])

    @pytest.mark.asyncio
    async def test_load_exception_returns_warning_not_raise(self) -> None:
        from factory_app.refinement_harness.tools.read_carry_forward_module_contract import (
            read_carry_forward_module_contract,
        )
        with patch(
            "factory_app.refinement_harness.tools.read_carry_forward_module_contract.load_artifact_workspace",
            new_callable=AsyncMock,
            side_effect=RuntimeError("simulated db failure"),
        ):
            result = await read_carry_forward_module_contract(
                "notifications",
                context_variables=_ctx(),
            )

        assert result["files"] == {}
        assert any("workspace_load_error" in w for w in result["warnings"])


# ---------------------------------------------------------------------------
# 8. Read-only: no writes, no LLM calls, no artifact mutations
# ---------------------------------------------------------------------------

class TestReadOnly:
    def test_module_has_no_write_operations(self) -> None:
        """Source code must not contain file write or artifact write patterns."""
        module_path = (
            Path(__file__).resolve().parents[1]
            / "factory_app"
            / "refinement_harness"
            / "tools"
            / "read_carry_forward_module_contract.py"
        )
        source = module_path.read_text(encoding="utf-8")

        disallowed_patterns = [
            "open(",
            "write(",
            ".write(",
            "artifact_store.save",
            "artifact_store.create",
            "artifact_store.put",
        ]
        for pattern in disallowed_patterns:
            assert pattern not in source, (
                f"read_carry_forward_module_contract.py contains disallowed write pattern: {pattern!r}"
            )

    def test_module_has_no_llm_calls(self) -> None:
        """Source code must not contain LLM or AI client call patterns."""
        module_path = (
            Path(__file__).resolve().parents[1]
            / "factory_app"
            / "refinement_harness"
            / "tools"
            / "read_carry_forward_module_contract.py"
        )
        source = module_path.read_text(encoding="utf-8")

        disallowed = ["openai", "anthropic", "chat.completions", "ChatCompletion"]
        for pattern in disallowed:
            assert pattern not in source, (
                f"read_carry_forward_module_contract.py contains LLM call pattern: {pattern!r}"
            )

    @pytest.mark.asyncio
    async def test_result_is_always_dict(self) -> None:
        """Return type is always dict regardless of inputs."""
        from factory_app.refinement_harness.tools.read_carry_forward_module_contract import (
            read_carry_forward_module_contract,
        )
        # Call with completely missing context
        result = await read_carry_forward_module_contract("notifications")
        assert isinstance(result, dict)
        assert "module_id" in result
        assert "files" in result
        assert "warnings" in result


# ---------------------------------------------------------------------------
# 9. Registration in AppGenerator tools.yaml under AppPlanAgent only
# ---------------------------------------------------------------------------

class TestRegistration:
    def _load_tools_yaml(self) -> list[dict]:
        tools_path = (
            Path(__file__).resolve().parents[1]
            / "factory_app"
            / "workflows"
            / "AppGenerator"
            / "tools.yaml"
        )
        with tools_path.open(encoding="utf-8") as f:
            doc = yaml.safe_load(f)
        if isinstance(doc, list):
            return doc
        if isinstance(doc, dict):
            return doc.get("tools", []) or []
        return []

    def test_tool_registered_in_appgenerator_tools_yaml(self) -> None:
        tools = self._load_tools_yaml()
        registered = [
            t for t in tools
            if t.get("function") == "read_carry_forward_module_contract"
        ]
        assert registered, "read_carry_forward_module_contract not in AppGenerator tools.yaml"

    def test_tool_registered_under_appplanagent_only(self) -> None:
        """read_carry_forward_module_contract must appear only under AppPlanAgent."""
        tools = self._load_tools_yaml()
        registered = [
            t for t in tools
            if t.get("function") == "read_carry_forward_module_contract"
        ]
        agents = {t.get("agent") for t in registered}
        assert agents == {"AppPlanAgent"}, (
            f"read_carry_forward_module_contract registered under unexpected agents: {agents}"
        )

    def test_tool_is_not_auto_tool_call(self) -> None:
        """AppPlanAgent must call this tool explicitly, not auto-invoke it."""
        tools = self._load_tools_yaml()
        registered = next(
            (t for t in tools if t.get("function") == "read_carry_forward_module_contract"),
            None,
        )
        assert registered is not None
        # auto_tool_call should be false or absent
        assert not registered.get("auto_tool_call", False)

    def test_tool_not_registered_in_control_plane_tools_yaml(self) -> None:
        """Phase 4 tool is workflow-level; it should not be a control-plane checkpoint tool."""
        cp_tools_path = (
            Path(__file__).resolve().parents[1]
            / "factory_app"
            / "refinement_harness"
            / "config"
            / "tools.yaml"
        )
        content = cp_tools_path.read_text(encoding="utf-8")
        assert "read_carry_forward_module_contract" not in content, (
            "read_carry_forward_module_contract should not be in refinement_harness/config/tools.yaml "
            "(it is a workflow-level tool, not a checkpoint-level tool)"
        )

    def test_wrapper_module_importable(self) -> None:
        """AppGenerator wrapper must be importable."""
        from factory_app.workflows.AppGenerator.tools.read_carry_forward_module_contract import (
            read_carry_forward_module_contract,
        )
        assert callable(read_carry_forward_module_contract)

    def test_core_module_importable(self) -> None:
        """Core control-plane module must be importable."""
        from factory_app.refinement_harness.tools.read_carry_forward_module_contract import (
            read_carry_forward_module_contract,
        )
        assert callable(read_carry_forward_module_contract)


# ---------------------------------------------------------------------------
# 10-12. AppPlanAgent guidance tests
# ---------------------------------------------------------------------------

class TestAppPlanAgentGuidance:
    def _load_agents_yaml(self) -> str:
        agents_path = (
            Path(__file__).resolve().parents[1]
            / "factory_app"
            / "workflows"
            / "AppGenerator"
            / "agents.yaml"
        )
        return agents_path.read_text(encoding="utf-8")

    def test_guidance_mentions_read_back_tool(self) -> None:
        """AppPlanAgent prompt must reference read_carry_forward_module_contract."""
        content = self._load_agents_yaml()
        assert "read_carry_forward_module_contract" in content

    def test_guidance_says_backend_source_not_read(self) -> None:
        """Guidance must say backend/*.py files are not accessible."""
        content = self._load_agents_yaml()
        assert "backend" in content
        # Should say NOT to request backend files
        assert "backend/*.py" in content or "backend source" in content.lower()

    def test_guidance_says_no_file_copy_or_merge(self) -> None:
        """Guidance must say no file copying or merging."""
        content = self._load_agents_yaml()
        # Existing text already says "Do not reference or copy files"
        assert "copy" in content.lower() or "merge" in content.lower()
        assert "not" in content.lower() or "no" in content.lower()

    def test_guidance_says_carry_forward_classification_available(self) -> None:
        """Guidance must mention carry_forward_classification field."""
        content = self._load_agents_yaml()
        assert "carry_forward_classification" in content

    def test_guidance_says_needs_adaptation_use_case(self) -> None:
        """Guidance must say when to call the read-back tool (needs_adaptation)."""
        content = self._load_agents_yaml()
        assert "needs_adaptation" in content


# ---------------------------------------------------------------------------
# 13. Existing carry-forward tests still pass (import smoke)
# ---------------------------------------------------------------------------

class TestExistingTestsUnaffected:
    def test_get_carry_forward_candidates_still_importable(self) -> None:
        from factory_app.refinement_harness.tools.get_carry_forward_candidates import (
            get_carry_forward_candidates,
        )
        assert callable(get_carry_forward_candidates)

    def test_extract_module_inventory_still_importable(self) -> None:
        from factory_app.refinement_harness.tools._module_inventory import (
            classify_module_carry_forward,
            extract_module_inventory,
        )
        assert callable(extract_module_inventory)
        assert callable(classify_module_carry_forward)

    def test_allowed_files_constant_correct(self) -> None:
        from factory_app.refinement_harness.tools.read_carry_forward_module_contract import (
            _ALLOWED_CONTRACT_FILES,
        )
        required = {
            "module.yaml",
            "runtime_extensions.yaml",
            "contracts/events.yaml",
            "contracts/reactions.yaml",
            "contracts/notifications.yaml",
            "contracts/policy_hooks.yaml",
            "contracts/settings.yaml",
            "contracts/admin.yaml",
            "contracts/profile.yaml",
            "contracts/relationships.yaml",
        }
        assert required == set(_ALLOWED_CONTRACT_FILES)

