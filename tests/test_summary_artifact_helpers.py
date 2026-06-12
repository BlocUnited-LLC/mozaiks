"""
Summary artifact pure helper unit tests.

Covers:
  _json_default:
    - datetime → ISO string
    - date → ISO string
    - Pydantic BaseModel → model_dump(mode="python")
    - non-serializable object → str()
    - string → str() (passthrough as str)
    - int → str()

  _summary_bytes:
    - dict → deterministic JSON bytes
    - keys sorted alphabetically
    - datetime value in dict → ISO string
    - result is bytes
    - round-trip json.loads gives original

  extract_summary_payload:
    - None → None
    - artifact with no commit_metadata → None
    - Mapping with commit_metadata.metadata.summary_payload → returned
    - Mapping with no summary_payload key → None
    - Mapping with non-Mapping metadata → None
    - Pydantic-like object with commit_metadata attrs → returned
    - raw dict nested 3 levels deep
"""
from __future__ import annotations

import json
from datetime import date, datetime

from pydantic import BaseModel

from mozaiksai.core.artifacts.summary_artifacts import (
    _json_default,
    _summary_bytes,
    extract_summary_payload,
)

# ---------------------------------------------------------------------------
# 1. _json_default
# ---------------------------------------------------------------------------

class TestJsonDefault:
    def test_datetime_returns_iso_string(self):
        dt = datetime(2024, 6, 1, 12, 0, 0)
        result = _json_default(dt)
        assert isinstance(result, str)
        assert "2024-06-01" in result

    def test_date_returns_iso_string(self):
        d = date(2024, 6, 1)
        result = _json_default(d)
        assert isinstance(result, str)
        assert "2024-06-01" in result

    def test_pydantic_model_returns_dict(self):
        class MyModel(BaseModel):
            x: int = 1
            y: str = "hello"

        result = _json_default(MyModel())
        assert isinstance(result, dict)
        assert result["x"] == 1
        assert result["y"] == "hello"

    def test_pydantic_model_dump_failure_falls_back_to_str(self):
        class BrokenModel(BaseModel):
            def model_dump(self, **kw):
                raise RuntimeError("boom")

        result = _json_default(BrokenModel())
        assert isinstance(result, str)

    def test_unknown_object_returns_str(self):
        class Weird:
            def __str__(self):
                return "weird_obj"

        result = _json_default(Weird())
        assert result == "weird_obj"

    def test_integer_falls_through_to_str(self):
        # int is JSON-serializable natively, but if passed to _json_default:
        result = _json_default(42)
        assert result == "42"


# ---------------------------------------------------------------------------
# 2. _summary_bytes
# ---------------------------------------------------------------------------

class TestSummaryBytes:
    def test_returns_bytes(self):
        result = _summary_bytes({"a": 1})
        assert isinstance(result, bytes)

    def test_deterministic_for_same_payload(self):
        payload = {"b": 2, "a": 1}
        r1 = _summary_bytes(payload)
        r2 = _summary_bytes(payload)
        assert r1 == r2

    def test_keys_sorted_alphabetically(self):
        payload = {"z": 1, "a": 2}
        result = _summary_bytes(payload)
        decoded = result.decode("utf-8")
        assert decoded.index('"a"') < decoded.index('"z"')

    def test_round_trip(self):
        payload = {"name": "test", "value": 42}
        result = json.loads(_summary_bytes(payload))
        assert result == payload

    def test_datetime_serialized_as_iso(self):
        dt = datetime(2024, 6, 1, 12, 0, 0)
        result = _summary_bytes({"ts": dt})
        decoded = json.loads(result)
        assert "2024-06-01" in decoded["ts"]

    def test_different_payloads_differ(self):
        r1 = _summary_bytes({"a": 1})
        r2 = _summary_bytes({"a": 2})
        assert r1 != r2

    def test_empty_dict_gives_empty_json_bytes(self):
        result = _summary_bytes({})
        assert result == b"{}"


# ---------------------------------------------------------------------------
# 3. extract_summary_payload
# ---------------------------------------------------------------------------

class TestExtractSummaryPayload:
    def test_none_returns_none(self):
        assert extract_summary_payload(None) is None

    def test_dict_with_no_commit_metadata_returns_none(self):
        assert extract_summary_payload({}) is None

    def test_dict_nested_summary_payload_returned(self):
        artifact = {
            "commit_metadata": {
                "metadata": {
                    "summary_payload": {"key": "value"}
                }
            }
        }
        result = extract_summary_payload(artifact)
        assert result == {"key": "value"}

    def test_dict_missing_summary_payload_returns_none(self):
        artifact = {
            "commit_metadata": {
                "metadata": {}
            }
        }
        assert extract_summary_payload(artifact) is None

    def test_dict_non_mapping_metadata_returns_none(self):
        artifact = {
            "commit_metadata": {
                "metadata": "not_a_dict"
            }
        }
        assert extract_summary_payload(artifact) is None

    def test_dict_non_mapping_commit_metadata_returns_none(self):
        artifact = {"commit_metadata": "not_a_dict"}
        assert extract_summary_payload(artifact) is None

    def test_summary_payload_none_value_returned_as_none(self):
        artifact = {
            "commit_metadata": {
                "metadata": {
                    "summary_payload": None
                }
            }
        }
        assert extract_summary_payload(artifact) is None

    def test_pydantic_like_object_with_attrs(self):
        class Meta:
            def __init__(self, data):
                self.metadata = data

        class CommitMeta:
            def __init__(self, meta):
                self.metadata = meta

        class FakeArtifact:
            def __init__(self, payload):
                self.commit_metadata = CommitMeta({"summary_payload": payload})

        artifact = FakeArtifact({"x": 123})
        result = extract_summary_payload(artifact)
        assert result == {"x": 123}

    def test_string_payload_returned(self):
        artifact = {
            "commit_metadata": {
                "metadata": {
                    "summary_payload": "raw_string"
                }
            }
        }
        assert extract_summary_payload(artifact) == "raw_string"
