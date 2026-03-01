"""
Tests for Frozen Event Schemas — Phase 3 validation
====================================================

These tests verify:
1. The frozen transport envelope schema has x-contract-frozen: true
2. Every event in the NDJSON fixture validates against the envelope schema
3. seq numbers are strictly monotonically increasing in fixture streams
4. Transport-specific events (snapshot, replay_boundary) validate against
   their dedicated schemas
5. Deprecated alias event types are NOT present in reference fixtures
6. causation_id is nullable (first event may have null)
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #
_ROOT = Path(__file__).resolve().parent.parent
_SCHEMAS = _ROOT / "mozaiksai" / "core" / "contracts" / "schemas" / "v1"
_TRANSPORT = _SCHEMAS / "transport"
_FIXTURES = _SCHEMAS / "fixtures"


def _load(path: Path) -> dict | list:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _load_ndjson(path: Path) -> list[dict]:
    events: list[dict] = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                events.append(json.loads(line))
    return events


def _load_aliases() -> dict[str, str]:
    alias_file = _TRANSPORT / "event_type_aliases.json"
    data = _load(alias_file)
    return data.get("aliases", {})


# --------------------------------------------------------------------------- #
# Fixtures (pytest)
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def envelope_schema() -> dict:
    return _load(_TRANSPORT / "event_envelope.schema.json")


@pytest.fixture(scope="module")
def full_run_events() -> list[dict]:
    return _load_ndjson(_FIXTURES / "example_full_run.stream.ndjson")


@pytest.fixture(scope="module")
def reference_events() -> list[dict]:
    return _load(_FIXTURES / "reference_stream_10_events.json")


@pytest.fixture(scope="module")
def ui_tool_events() -> list[dict]:
    return _load_ndjson(_FIXTURES / "ui_tool_mock_sequence_10.ndjson")


# --------------------------------------------------------------------------- #
# 1. Frozen flag
# --------------------------------------------------------------------------- #
class TestFrozenSchema:
    def test_envelope_is_frozen(self, envelope_schema: dict):
        assert envelope_schema.get("x-contract-frozen") is True, (
            "event_envelope.schema.json must declare x-contract-frozen: true"
        )

    def test_schema_version_is_present(self, envelope_schema: dict):
        props = envelope_schema.get("properties", {})
        sv = props.get("schema_version", {})
        assert sv.get("const") == "1.0.0", (
            "Frozen envelope schema must pin schema_version to 1.0.0"
        )


# --------------------------------------------------------------------------- #
# 2. NDJSON fixtures validate against envelope
# --------------------------------------------------------------------------- #
class TestEnvelopeValidation:
    """Validate every fixture event has the required envelope fields."""

    REQUIRED_KEYS = {"event_type", "seq", "occurred_at", "payload", "schema_version",
                     "correlation_id", "process_id"}

    def _check_event(self, event: dict, idx: int, source: str):
        missing = self.REQUIRED_KEYS - set(event.keys())
        assert not missing, (
            f"{source} event[{idx}] missing required keys: {missing}"
        )
        assert event["schema_version"] == "1.0.0", (
            f"{source} event[{idx}] schema_version != 1.0.0"
        )

    def test_full_run_events_valid(self, full_run_events: list[dict]):
        assert len(full_run_events) == 36, "Expected 36 events in full run"
        for i, evt in enumerate(full_run_events):
            self._check_event(evt, i, "full_run")

    def test_reference_events_valid(self, reference_events: list[dict]):
        assert len(reference_events) == 10, "Expected 10 events in reference stream"
        for i, evt in enumerate(reference_events):
            self._check_event(evt, i, "reference")

    def test_ui_tool_events_valid(self, ui_tool_events: list[dict]):
        assert len(ui_tool_events) == 10, "Expected 10 events in UI tool fixture"
        for i, evt in enumerate(ui_tool_events):
            self._check_event(evt, i, "ui_tool")


# --------------------------------------------------------------------------- #
# 3. Seq monotonicity
# --------------------------------------------------------------------------- #
class TestSeqMonotonicity:
    def _check_monotonic(self, events: list[dict], label: str):
        seqs = [e["seq"] for e in events]
        for i in range(1, len(seqs)):
            assert seqs[i] > seqs[i - 1], (
                f"{label}: seq[{i}]={seqs[i]} is not > seq[{i-1}]={seqs[i-1]}"
            )

    def test_full_run_monotonic(self, full_run_events):
        self._check_monotonic(full_run_events, "full_run")

    def test_reference_monotonic(self, reference_events):
        self._check_monotonic(reference_events, "reference")

    def test_ui_tool_monotonic(self, ui_tool_events):
        self._check_monotonic(ui_tool_events, "ui_tool")


# --------------------------------------------------------------------------- #
# 4. Transport events match dedicated schemas
# --------------------------------------------------------------------------- #
class TestTransportEvents:
    def test_snapshot_events_have_required_fields(self, full_run_events):
        snapshots = [e for e in full_run_events if e["event_type"] == "transport.snapshot"]
        assert len(snapshots) >= 1, "Expected at least one transport.snapshot event"
        for s in snapshots:
            payload = s["payload"]
            assert "snapshot_id" in payload
            assert "snapshot_uri" in payload or "state_hash" in payload

    def test_replay_boundary_events_have_required_fields(self, full_run_events):
        boundaries = [e for e in full_run_events if e["event_type"] == "transport.replay_boundary"]
        assert len(boundaries) >= 1, "Expected at least one transport.replay_boundary event"
        for b in boundaries:
            payload = b["payload"]
            assert "boundary" in payload
            assert "replay_complete" in payload


# --------------------------------------------------------------------------- #
# 5. No deprecated aliases in reference fixtures
# --------------------------------------------------------------------------- #
class TestNoDeprecatedAliases:
    def test_full_run_no_aliases(self, full_run_events):
        aliases = _load_aliases()
        alias_set = set(aliases.keys())
        for i, evt in enumerate(full_run_events):
            assert evt["event_type"] not in alias_set, (
                f"full_run event[{i}] uses deprecated alias '{evt['event_type']}' "
                f"→ should be '{aliases[evt['event_type']]}'"
            )

    def test_reference_no_aliases(self, reference_events):
        aliases = _load_aliases()
        alias_set = set(aliases.keys())
        for i, evt in enumerate(reference_events):
            assert evt["event_type"] not in alias_set, (
                f"reference event[{i}] uses deprecated alias '{evt['event_type']}'"
            )


# --------------------------------------------------------------------------- #
# 6. causation_id nullable on first event
# --------------------------------------------------------------------------- #
class TestCausationId:
    def test_first_event_causation_id_null(self, full_run_events):
        first = full_run_events[0]
        assert first["causation_id"] is None, (
            "First event (process.started) should have causation_id=null"
        )

    def test_subsequent_events_have_causation_id(self, full_run_events):
        for i, evt in enumerate(full_run_events[1:], start=1):
            assert evt.get("causation_id") is not None, (
                f"Event[{i}] ({evt['event_type']}) should have a non-null causation_id"
            )


# --------------------------------------------------------------------------- #
# Bonus: jsonschema validation (if installed)
# --------------------------------------------------------------------------- #
class TestJsonSchemaValidation:
    """Full Draft2020-12 validation — skipped if jsonschema not installed."""

    @pytest.fixture(autouse=True)
    def _skip_without_jsonschema(self):
        pytest.importorskip("jsonschema")

    def _validator(self, schema: dict):
        from jsonschema import Draft202012Validator
        Draft202012Validator.check_schema(schema)
        return Draft202012Validator(schema)

    def test_envelope_schema_is_valid_json_schema(self, envelope_schema):
        self._validator(envelope_schema)

    def test_full_run_validates_against_envelope(self, envelope_schema, full_run_events):
        v = self._validator(envelope_schema)
        for i, evt in enumerate(full_run_events):
            errors = list(v.iter_errors(evt))
            assert not errors, (
                f"full_run event[{i}] failed schema validation: "
                f"{errors[0].message if errors else ''}"
            )

    def test_reference_validates_against_envelope(self, envelope_schema, reference_events):
        v = self._validator(envelope_schema)
        for i, evt in enumerate(reference_events):
            errors = list(v.iter_errors(evt))
            assert not errors, (
                f"reference event[{i}] failed schema validation: "
                f"{errors[0].message if errors else ''}"
            )
