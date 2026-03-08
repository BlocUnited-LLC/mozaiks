from __future__ import annotations

from pathlib import Path

from autogen.events.base_event import BaseEvent, wrap_event

from mozaiksai.engine.capabilities import get_ag2_capability_report
from mozaiksai.engine.events.serialization import EventBuildContext, build_ui_event_payload
from mozaiksai.kernel.dispatcher import get_event_dispatcher


class _NoopLogger:
    def debug(self, *args, **kwargs):
        return None

    def info(self, *args, **kwargs):
        return None


@wrap_event
class PipelineStageEvent(BaseEvent):
    stage_name: str
    records_processed: int
    validation_passed: bool


def test_capability_report_exposes_modern_ag2_event_features():
    caps = get_ag2_capability_report()
    assert caps["engine"] == "ag2"
    assert caps["version"] not in {"", "unknown"}
    assert caps["events_module"] is True
    assert caps["groupchat_async_run"] is True
    assert caps["agent_run_iter"] is True
    assert caps["custom_events"] is True
    assert caps["runtime_logging"] is True


def test_custom_base_event_is_mapped_to_chat_custom_event_envelope():
    ctx = EventBuildContext(
        workflow_name="workflow",
        turn_agent="RuntimeAgent",
        tool_call_initiators={},
        tool_names_by_id={},
        workflow_name_upper="WORKFLOW",
        wf_logger=_NoopLogger(),
    )

    custom_event = PipelineStageEvent(
        stage_name="validation",
        records_processed=1000,
        validation_passed=True,
    )
    payload = build_ui_event_payload(ev=custom_event, ctx=ctx)

    assert payload is not None
    assert payload["kind"] == "custom_event"
    assert payload["event_name"] == "PipelineStageEvent"

    envelope = get_event_dispatcher().build_outbound_event_envelope(
        raw_event=payload,
        chat_id="chat-123",
    )
    assert envelope is not None
    assert envelope["type"] == "chat.custom_event"


def test_transport_layer_has_no_direct_autogen_imports():
    repo_root = Path(__file__).resolve().parent.parent
    handler_source = (repo_root / "mozaiksai" / "transport" / "websocket" / "handler.py").read_text(encoding="utf-8")
    factory_source = (repo_root / "mozaiksai" / "transport" / "factory.py").read_text(encoding="utf-8")

    assert "from autogen" not in handler_source
    assert "import autogen" not in handler_source
    assert "from autogen" not in factory_source
    assert "import autogen" not in factory_source
