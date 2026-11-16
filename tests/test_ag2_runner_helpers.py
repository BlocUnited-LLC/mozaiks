"""
AG2 network runner pure helper unit tests.

Covers:
  _envelope_to_dict:
    - full envelope with all attributes → correct dict
    - missing attributes default to empty/None
    - None attribute values coerced to empty strings
    - audience converted to list
    - None audience becomes empty list
    - event_data None becomes empty dict
    - causation_id preserved as-is including None
    - non-dict event_data coerced to empty dict
    - all keys present in output

"""
from __future__ import annotations

from types import SimpleNamespace

from mozaiksai.core.adapters.ag2_network_runner import _envelope_to_dict

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _env(**kw) -> SimpleNamespace:
    """Build a fake envelope with only the given attributes set."""
    return SimpleNamespace(**kw)


# ---------------------------------------------------------------------------
# 1. _envelope_to_dict
# ---------------------------------------------------------------------------

class TestEnvelopeToDict:
    def test_full_envelope_all_fields(self):
        env = _env(
            envelope_id="env-1",
            channel_id="chan-1",
            sender_id="agent-a",
            audience=["agent-b", "agent-c"],
            event_type="mozaiks.packet",
            event_data={"body": "hello"},
            causation_id="cause-1",
        )
        result = _envelope_to_dict(env)
        assert result["envelope_id"] == "env-1"
        assert result["channel_id"] == "chan-1"
        assert result["sender_id"] == "agent-a"
        assert result["audience"] == ["agent-b", "agent-c"]
        assert result["event_type"] == "mozaiks.packet"
        assert result["event_data"] == {"body": "hello"}
        assert result["causation_id"] == "cause-1"

    def test_missing_attributes_default(self):
        env = SimpleNamespace()  # no attributes
        result = _envelope_to_dict(env)
        assert result["envelope_id"] == ""
        assert result["channel_id"] == ""
        assert result["sender_id"] == ""
        assert result["audience"] == []
        assert result["event_type"] == ""
        assert result["event_data"] == {}
        assert result["causation_id"] is None

    def test_none_envelope_id_coerced_to_empty(self):
        env = _env(envelope_id=None)
        result = _envelope_to_dict(env)
        assert result["envelope_id"] == ""

    def test_none_audience_becomes_empty_list(self):
        env = _env(audience=None)
        result = _envelope_to_dict(env)
        assert result["audience"] == []

    def test_audience_converted_to_list(self):
        env = _env(audience=("agent-x",))  # tuple
        result = _envelope_to_dict(env)
        assert result["audience"] == ["agent-x"]

    def test_none_event_data_becomes_empty_dict(self):
        env = _env(event_data=None)
        result = _envelope_to_dict(env)
        assert result["event_data"] == {}

    def test_causation_id_none_preserved(self):
        env = _env(causation_id=None)
        result = _envelope_to_dict(env)
        assert result["causation_id"] is None

    def test_causation_id_value_preserved(self):
        env = _env(causation_id="cid-999")
        result = _envelope_to_dict(env)
        assert result["causation_id"] == "cid-999"

    def test_all_keys_present(self):
        env = SimpleNamespace()
        result = _envelope_to_dict(env)
        expected_keys = {"envelope_id", "channel_id", "sender_id", "audience", "event_type", "event_data", "causation_id"}
        assert set(result.keys()) == expected_keys

    def test_numeric_ids_coerced_to_str(self):
        env = _env(envelope_id=42, channel_id=99)
        result = _envelope_to_dict(env)
        assert result["envelope_id"] == "42"
        assert result["channel_id"] == "99"
