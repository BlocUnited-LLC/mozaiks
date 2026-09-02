"""Build lifecycle artifact persistence — unit tests.

Verifies that AppGenerator.emit_build_completed and
AgentGenerator.emit_build_completed call persist_summary_artifact()
with the correct artifact_kind and revision_mode, and that failures
in artifact persistence do not propagate (best-effort).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

# ── helpers ────────────────────────────────────────────────────────────────────

def _make_shared_emit(return_value="outbox_1"):
    return AsyncMock(return_value=return_value)


def _make_persist():
    return AsyncMock(return_value=None)


# ── AppGenerator ───────────────────────────────────────────────────────────────

class TestAppGeneratorEmitBuildCompleted:
    @pytest.mark.asyncio
    async def test_persists_app_bundle_artifact_on_complete(self) -> None:
        from factory_app.workflows.AppGenerator.tools.platform import build_lifecycle as mod

        shared_emit = _make_shared_emit("outbox_app_1")
        persist = _make_persist()

        with (
            patch.object(mod, "_shared_emit_build_completed", shared_emit),
            patch.object(mod, "_read_build_mode", AsyncMock(return_value=None)),
            patch.object(mod, "_persist_app_bundle_artifact", persist),
        ):
            result = await mod.emit_build_completed(
                app_id="app-1",
                chat_id="chat-1",
                user_id="user-1",
                workflow_name="AppGenerator",
                context_variables={"download_status": "ready"},
            )

        assert result == "outbox_app_1"
        shared_emit.assert_awaited_once()
        persist.assert_awaited_once_with(
            app_id="app-1",
            chat_id="chat-1",
            user_id="user-1",
            workflow_name="AppGenerator",
            build_mode=None,
        )

    @pytest.mark.asyncio
    async def test_passes_revision_mode_to_persist(self) -> None:
        from factory_app.workflows.AppGenerator.tools.platform import build_lifecycle as mod

        persist = _make_persist()

        with (
            patch.object(mod, "_shared_emit_build_completed", _make_shared_emit()),
            patch.object(mod, "_read_build_mode", AsyncMock(return_value="revision")),
            patch.object(mod, "_persist_app_bundle_artifact", persist),
        ):
            await mod.emit_build_completed(
                app_id="app-1",
                chat_id="chat-1",
                user_id="user-1",
                workflow_name="AppGenerator",
                context_variables={"download_status": "ready"},
            )

        persist.assert_awaited_once_with(
            app_id="app-1",
            chat_id="chat-1",
            user_id="user-1",
            workflow_name="AppGenerator",
            build_mode="revision",
        )

    @pytest.mark.asyncio
    async def test_persist_failure_does_not_propagate(self) -> None:
        from factory_app.workflows.AppGenerator.tools.platform import build_lifecycle as mod

        with (
            patch.object(mod, "_shared_emit_build_completed", _make_shared_emit("outbox_2")),
            patch.object(mod, "_read_build_mode", AsyncMock(return_value=None)),
            patch.object(mod, "_persist_app_bundle_artifact", AsyncMock(side_effect=RuntimeError("db down"))),
        ):
            result = await mod.emit_build_completed(
                app_id="app-1",
                chat_id="chat-1",
                user_id="user-1",
                workflow_name="AppGenerator",
                context_variables={"download_status": "ready"},
            )

        # outbox_event_id still returned — persistence failure is best-effort
        assert result == "outbox_2"

    @pytest.mark.asyncio
    async def test_persist_app_bundle_artifact_uses_correct_kind(self) -> None:
        from factory_app.workflows.AppGenerator.tools.platform.build_lifecycle import (
            _persist_app_bundle_artifact,
        )

        captured: dict = {}

        async def _fake_persist(**kwargs):
            captured.update(kwargs)

        # persist_summary_artifact is imported inside the function body, so patch
        # it at the source module level.
        with patch("mozaiksai.core.artifacts.summary_artifacts.persist_summary_artifact", _fake_persist):
            await _persist_app_bundle_artifact(
                app_id="app-1",
                chat_id="chat-1",
                user_id="user-1",
                workflow_name="AppGenerator",
                build_mode=None,
            )

        assert captured.get("artifact_kind") == "app_bundle"
        assert captured.get("revision_mode") is False
        input_kinds = captured.get("input_artifact_kinds") or []
        assert "design_docs" in input_kinds
        assert "workflow_bundle" in input_kinds
        assert "theme_capture" in input_kinds
        assert "brand" not in input_kinds
        assert "app_bundle" not in input_kinds  # not a circular self-reference


# ── AgentGenerator ─────────────────────────────────────────────────────────────

class TestAgentGeneratorEmitBuildCompleted:
    @pytest.mark.asyncio
    async def test_persists_workflow_bundle_artifact_on_complete(self) -> None:
        from factory_app.workflows.AgentGenerator.tools.platform import build_lifecycle as mod

        shared_emit = _make_shared_emit("outbox_ag_1")
        persist = _make_persist()

        with (
            patch.object(mod, "_shared_emit_build_completed", shared_emit),
            patch.object(mod, "_read_build_mode", AsyncMock(return_value=None)),
            patch.object(mod, "_persist_workflow_bundle_artifact", persist),
        ):
            result = await mod.emit_build_completed(
                app_id="app-1",
                chat_id="chat-1",
                user_id="user-1",
                workflow_name="AgentGenerator",
            )

        assert result == "outbox_ag_1"
        shared_emit.assert_awaited_once()
        persist.assert_awaited_once_with(
            app_id="app-1",
            chat_id="chat-1",
            user_id="user-1",
            workflow_name="AgentGenerator",
            build_mode=None,
        )

    @pytest.mark.asyncio
    async def test_passes_revision_mode_to_persist(self) -> None:
        from factory_app.workflows.AgentGenerator.tools.platform import build_lifecycle as mod

        persist = _make_persist()

        with (
            patch.object(mod, "_shared_emit_build_completed", _make_shared_emit()),
            patch.object(mod, "_read_build_mode", AsyncMock(return_value="revision")),
            patch.object(mod, "_persist_workflow_bundle_artifact", persist),
        ):
            await mod.emit_build_completed(
                app_id="app-1",
                chat_id="chat-1",
                user_id="user-1",
                workflow_name="AgentGenerator",
            )

        persist.assert_awaited_once_with(
            app_id="app-1",
            chat_id="chat-1",
            user_id="user-1",
            workflow_name="AgentGenerator",
            build_mode="revision",
        )

    @pytest.mark.asyncio
    async def test_persist_failure_does_not_propagate(self) -> None:
        from factory_app.workflows.AgentGenerator.tools.platform import build_lifecycle as mod

        with (
            patch.object(mod, "_shared_emit_build_completed", _make_shared_emit("outbox_ag_2")),
            patch.object(mod, "_read_build_mode", AsyncMock(return_value=None)),
            patch.object(mod, "_persist_workflow_bundle_artifact", AsyncMock(side_effect=RuntimeError("db down"))),
        ):
            result = await mod.emit_build_completed(
                app_id="app-1",
                chat_id="chat-1",
                user_id="user-1",
                workflow_name="AgentGenerator",
            )

        assert result == "outbox_ag_2"

    @pytest.mark.asyncio
    async def test_persist_workflow_bundle_artifact_includes_integration_metadata(self) -> None:
        from factory_app.workflows.AgentGenerator.tools.platform import build_lifecycle as mod

        captured: dict = {}
        workflow_integration_metadata = {
            "contract_version": "1.0",
            "workflows": [
                {
                    "workflow_name": "TicketBatchTriageWorkflow",
                    "capability_id": "ticket-batch-triage-workflow",
                    "startup_mode": "BackendOnly",
                    "trigger_events": [
                        {
                            "event_type": "domain.support_ticket.batch_requested",
                            "source": "domain",
                            "capability_id": "ticket-batch-triage-workflow",
                        }
                    ],
                }
            ],
        }

        async def _fake_persist(**kwargs):
            captured.update(kwargs)

        with (
            patch.object(mod, "_read_workflow_integration_metadata", AsyncMock(return_value=workflow_integration_metadata)),
            patch("mozaiksai.core.artifacts.summary_artifacts.persist_summary_artifact", _fake_persist),
        ):
            await mod._persist_workflow_bundle_artifact(
                app_id="app-1",
                chat_id="chat-1",
                user_id="user-1",
                workflow_name="AgentGenerator",
                build_mode=None,
            )

        assert captured["artifact_kind"] == "workflow_bundle"
        assert captured["summary_payload"]["source_workflow"] == "AgentGenerator"
        assert captured["summary_payload"]["source_chat_id"] == "chat-1"
        assert captured["summary_payload"]["workflow_integration_metadata"] == workflow_integration_metadata


class TestAppGeneratorCompletionGate:
    """build.completed only after terminal success; build.failed only on the failed marker.

    The completion hook fires on every completed workflow turn, so states
    other than the two terminal markers (intermediate turns, cancelled
    downloads, absent context) must claim nothing at all.
    """

    @pytest.mark.asyncio
    async def test_failed_marker_emits_failed_not_completed(self) -> None:
        from factory_app.workflows.AppGenerator.tools.platform import build_lifecycle as mod

        shared_emit = _make_shared_emit("outbox_should_not_happen")
        persist = _make_persist()
        failed = AsyncMock(return_value="outbox_failed_1")

        with (
            patch.object(mod, "_shared_emit_build_completed", shared_emit),
            patch.object(mod, "_persist_app_bundle_artifact", persist),
            patch.object(mod, "emit_build_failed", failed),
        ):
            result = await mod.emit_build_completed(
                app_id="app-1",
                chat_id="chat-1",
                user_id="user-1",
                workflow_name="AppGenerator",
                context_variables={"download_status": "failed", "app_download_ready": False},
            )

        assert result == "outbox_failed_1"
        shared_emit.assert_not_awaited()
        persist.assert_not_awaited()
        failed.assert_awaited_once()
        assert "terminal success" in failed.await_args.kwargs["error"]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "context_variables",
        [
            {},
            {"download_status": "cancelled", "app_download_ready": False},
            None,
        ],
        ids=["intermediate-turn", "cancelled-download", "no-context"],
    )
    async def test_nonterminal_states_claim_nothing(self, context_variables) -> None:
        from factory_app.workflows.AppGenerator.tools.platform import build_lifecycle as mod

        shared_emit = _make_shared_emit()
        persist = _make_persist()
        failed = AsyncMock()

        with (
            patch.object(mod, "_shared_emit_build_completed", shared_emit),
            patch.object(mod, "_persist_app_bundle_artifact", persist),
            patch.object(mod, "emit_build_failed", failed),
        ):
            result = await mod.emit_build_completed(
                app_id="app-1",
                workflow_name="AppGenerator",
                context_variables=context_variables,
            )

        assert result is None
        shared_emit.assert_not_awaited()
        persist.assert_not_awaited()
        failed.assert_not_awaited()
