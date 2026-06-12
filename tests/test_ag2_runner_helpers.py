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

  _dedupe_symbols (context_graph):
    - empty list → []
    - single symbol → returned
    - duplicate (same name+kind+line) → deduplicated
    - same name+kind, different line → both kept
    - same name, different kind → both kept
    - None line treated as distinct from int line
    - preserves insertion order (first wins)
"""
from __future__ import annotations

from types import SimpleNamespace

from mozaiksai.core.adapters.ag2_network_runner import _envelope_to_dict
from mozaiksai.core.app_context.context_graph import ExtractedSymbol, _dedupe_symbols

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


# ---------------------------------------------------------------------------
# 2. _dedupe_symbols
# ---------------------------------------------------------------------------

def _sym(name: str, kind: str, line: int | None = None) -> ExtractedSymbol:
    return ExtractedSymbol(name=name, kind=kind, line=line)


class TestDedupeSymbols:
    def test_empty_list_returns_empty(self):
        assert _dedupe_symbols([]) == []

    def test_single_symbol_returned(self):
        s = _sym("my_func", "function", 1)
        result = _dedupe_symbols([s])
        assert len(result) == 1
        assert result[0] is s

    def test_duplicate_removed(self):
        s1 = _sym("my_func", "function", 10)
        s2 = _sym("my_func", "function", 10)
        result = _dedupe_symbols([s1, s2])
        assert len(result) == 1
        assert result[0] is s1  # first occurrence kept

    def test_same_name_different_line_both_kept(self):
        s1 = _sym("my_func", "function", 10)
        s2 = _sym("my_func", "function", 20)
        result = _dedupe_symbols([s1, s2])
        assert len(result) == 2

    def test_same_name_different_kind_both_kept(self):
        s1 = _sym("config", "variable", 5)
        s2 = _sym("config", "class", 5)
        result = _dedupe_symbols([s1, s2])
        assert len(result) == 2

    def test_none_line_distinct_from_int_line(self):
        s1 = _sym("my_func", "function", None)
        s2 = _sym("my_func", "function", 10)
        result = _dedupe_symbols([s1, s2])
        assert len(result) == 2

    def test_preserves_insertion_order(self):
        symbols = [_sym(f"func_{i}", "function", i) for i in range(5)]
        result = _dedupe_symbols(symbols)
        assert [s.name for s in result] == [f"func_{i}" for i in range(5)]

    def test_multiple_duplicates_all_collapsed(self):
        s = _sym("target", "function", 1)
        result = _dedupe_symbols([s, s, s, s])
        assert len(result) == 1
