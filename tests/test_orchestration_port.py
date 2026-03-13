# ==============================================================================
# Tests for OrchestrationPort protocol, AG2 adapter contract, and data types.
# ==============================================================================

import pytest
from mozaiksai.core.ports.orchestration import (
    DomainEvent,
    OrchestrationPort,
    ResumeRequest,
    RunRequest,
    RunResult,
    RunStatus,
)


# ---------------------------------------------------------------------------
# RunRequest / ResumeRequest / RunResult
# ---------------------------------------------------------------------------


class TestRunRequest:
    def test_required_fields(self):
        r = RunRequest(
            workflow_name="WritersRoom",
            app_id="app-1",
            chat_id="chat-1",
            user_id="user-1",
        )
        assert r.workflow_name == "WritersRoom"
        assert r.app_id == "app-1"
        assert r.chat_id == "chat-1"
        assert r.user_id == "user-1"
        assert r.initial_message is None
        assert r.initial_agent_name_override is None
        assert r.extra == {}

    def test_optional_fields(self):
        r = RunRequest(
            workflow_name="W",
            app_id="a",
            chat_id="c",
            user_id="u",
            initial_message="hi",
            initial_agent_name_override="Agent1",
            extra={"max_rounds": 5},
        )
        assert r.initial_message == "hi"
        assert r.initial_agent_name_override == "Agent1"
        assert r.extra == {"max_rounds": 5}

    def test_frozen(self):
        r = RunRequest(workflow_name="W", app_id="a", chat_id="c", user_id="u")
        with pytest.raises(AttributeError):
            r.workflow_name = "X"  # type: ignore[misc]


class TestResumeRequest:
    def test_fields(self):
        r = ResumeRequest(
            workflow_name="W",
            app_id="a",
            chat_id="c",
            user_id="u",
            resume_agent="SummaryAgent",
            injected_context={"key": "value"},
        )
        assert r.resume_agent == "SummaryAgent"
        assert r.injected_context == {"key": "value"}


class TestRunResult:
    def test_completed(self):
        r = RunResult(
            status=RunStatus.COMPLETED,
            chat_id="c",
            workflow_name="W",
        )
        assert r.status == RunStatus.COMPLETED
        assert r.handoff_to_user is False
        assert r.error is None

    def test_failed(self):
        r = RunResult(
            status=RunStatus.FAILED,
            chat_id="c",
            workflow_name="W",
            error="boom",
        )
        assert r.status == RunStatus.FAILED
        assert r.error == "boom"


class TestRunStatus:
    def test_all_values(self):
        assert RunStatus.COMPLETED.value == "completed"
        assert RunStatus.HANDOFF_TO_USER.value == "handoff_to_user"
        assert RunStatus.PAUSED.value == "paused"
        assert RunStatus.FAILED.value == "failed"
        assert RunStatus.CANCELLED.value == "cancelled"
        assert RunStatus.IN_PROGRESS.value == "in_progress"


class TestDomainEvent:
    def test_creation(self):
        e = DomainEvent(kind="test.event", payload={"x": 1}, chat_id="c")
        assert e.kind == "test.event"
        assert e.payload == {"x": 1}
        assert e.chat_id == "c"
        assert e.source == "ag2"
        assert e.timestamp  # auto-generated


# ---------------------------------------------------------------------------
# OrchestrationPort protocol
# ---------------------------------------------------------------------------


class TestOrchestrationPortProtocol:
    def test_adapter_implements_protocol(self):
        from mozaiksai.core.adapters.ag2_orchestration import AG2OrchestrationAdapter

        assert isinstance(AG2OrchestrationAdapter(), OrchestrationPort)

    def test_capabilities_shape(self):
        from mozaiksai.core.adapters.ag2_orchestration import get_ag2_adapter

        caps = get_ag2_adapter().capabilities()
        assert caps["engine"] == "ag2"
        assert isinstance(caps["version"], str)
        assert caps["supports_pause"] is True
        assert caps["supports_resume"] is True
        assert caps["supports_fan_out"] is True
        assert caps["supports_cancel"] is True
        assert caps["supports_bidirectional_stream"] is False

    def test_interpret_result_completed(self):
        from mozaiksai.core.adapters.ag2_orchestration import AG2OrchestrationAdapter

        adapter = AG2OrchestrationAdapter()
        req = RunRequest(workflow_name="W", app_id="a", chat_id="c", user_id="u")
        r = adapter._interpret_result(req, {"run_completed": True})
        assert r.status == RunStatus.COMPLETED
        assert r.handoff_to_user is False

    def test_interpret_result_handoff(self):
        from mozaiksai.core.adapters.ag2_orchestration import AG2OrchestrationAdapter

        adapter = AG2OrchestrationAdapter()
        req = RunRequest(workflow_name="W", app_id="a", chat_id="c", user_id="u")
        r = adapter._interpret_result(req, {"handoff_to_user": True})
        assert r.status == RunStatus.HANDOFF_TO_USER
        assert r.handoff_to_user is True

    def test_interpret_result_none(self):
        from mozaiksai.core.adapters.ag2_orchestration import AG2OrchestrationAdapter

        adapter = AG2OrchestrationAdapter()
        req = RunRequest(workflow_name="W", app_id="a", chat_id="c", user_id="u")
        r = adapter._interpret_result(req, None)
        assert r.status == RunStatus.COMPLETED

    def test_singleton(self):
        from mozaiksai.core.adapters.ag2_orchestration import get_ag2_adapter

        a = get_ag2_adapter()
        b = get_ag2_adapter()
        assert a is b
