"""Tests for the mozaiks.ui.event.v1 canonical event contract."""
from mozaiksai.core.transport.event_contract import (
    MozaiksEventEnvelope,
    MozaiksEventType,
    compute_event_id,
)


def test_event_type_strings_are_namespaced():
    """All event types must have a dot-namespaced format."""
    for et in MozaiksEventType:
        assert "." in et, f"{et} is not namespaced"


def test_ui_event_types_present():
    assert MozaiksEventType.UI_RENDER == "ui.render"
    assert MozaiksEventType.UI_UPDATE == "ui.update"
    assert MozaiksEventType.UI_DISMISS == "ui.dismiss"


def test_chat_event_types_present():
    assert MozaiksEventType.CHAT_TEXT == "chat.text"
    assert MozaiksEventType.CHAT_TOOL_CALL == "chat.tool_call"
    assert MozaiksEventType.CHAT_RUN_COMPLETE == "chat.run_complete"


def test_all_ns_map_kinds_covered():
    """Every chat.* and ui.* kind emitted by the ns_map must be a MozaiksEventType member."""
    expected_outbound = {
        "chat.print", "chat.text", "chat.input_ack", "chat.input_timeout",
        "chat.select_speaker", "chat.resume_boundary", "chat.usage_delta",
        "chat.usage_summary", "chat.run_complete", "chat.error", "chat.tool_call",
        "chat.tool_response", "chat.tool_progress", "chat.token_budget_alert",
        "chat.agent_output_validated", "chat.run_start", "chat.tool_call_dismiss",
        "chat.awaiting_reply", "chat.activity", "chat.attachment_uploaded",
        "chat.stream_chunk", "chat.stream_end", "chat.custom_event",
        "chat.greeting_echo", "ui.render", "ui.update", "ui.dismiss",
    }
    valid = set(MozaiksEventType)
    for type_str in expected_outbound:
        assert type_str in valid, f"{type_str!r} from ns_map is not in MozaiksEventType"


def test_compute_event_id_is_deterministic():
    id1 = compute_event_id("tenant_a", "run_1", None, 0, MozaiksEventType.CHAT_TEXT)
    id2 = compute_event_id("tenant_a", "run_1", None, 0, MozaiksEventType.CHAT_TEXT)
    assert id1 == id2
    assert isinstance(id1, str)


def test_compute_event_id_changes_with_sequence():
    id1 = compute_event_id("t", "r", None, 0, MozaiksEventType.CHAT_TEXT)
    id2 = compute_event_id("t", "r", None, 1, MozaiksEventType.CHAT_TEXT)
    assert id1 != id2


def test_compute_event_id_changes_with_event_type():
    id1 = compute_event_id("t", "r", None, 0, MozaiksEventType.CHAT_TEXT)
    id2 = compute_event_id("t", "r", None, 0, MozaiksEventType.CHAT_TOOL_CALL)
    assert id1 != id2


def test_compute_event_id_uses_session_when_no_run():
    id1 = compute_event_id("t", None, "sess_1", 0, MozaiksEventType.CHAT_TEXT)
    id2 = compute_event_id("t", None, "sess_1", 0, MozaiksEventType.CHAT_TEXT)
    assert id1 == id2


def test_envelope_accepts_extra_fields():
    env = MozaiksEventEnvelope(type=MozaiksEventType.CHAT_TEXT, text="hello", seq=5)
    assert env.type == "chat.text"


def test_envelope_type_compares_as_string():
    """MozaiksEventType is a StrEnum — it must compare equal to its string value."""
    env = MozaiksEventEnvelope(type=MozaiksEventType.UI_RENDER)
    assert env.type == "ui.render"
    assert env.type == MozaiksEventType.UI_RENDER


def test_ns_map_uses_canonical_types():
    """ns_map values in the dispatcher must all be MozaiksEventType members."""
    # The ns_map is built inline inside build_outbound_event_envelope.
    # Inspect the source to find all MozaiksEventType.XYZ references and
    # verify each one resolves to a defined enum member.
    import inspect
    import re

    from mozaiksai.core.events.unified_event_dispatcher import UnifiedEventDispatcher

    src = inspect.getsource(UnifiedEventDispatcher.build_outbound_event_envelope)
    # Extract string values that look like event type strings from the ns_map block.
    # We match 'MozaiksEventType.XYZ' patterns and verify they resolve correctly.
    enum_refs = re.findall(r"MozaiksEventType\.(\w+)", src)
    assert len(enum_refs) > 0, "No MozaiksEventType references found in build_outbound_event_envelope"
    for ref in enum_refs:
        assert hasattr(MozaiksEventType, ref), f"MozaiksEventType.{ref} referenced in ns_map but not defined"
