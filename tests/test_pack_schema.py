"""
Tests for pack schema models (schema.py) and config integration
================================================================

Covers:
1. Pydantic model validation (valid, missing required, invalid types)
2. Version detection (v1 legacy, v2, v3, unknown)
3. v2 → v3 conversion (flat dict → typed MidFlightJourney)
4. v3 → v2 conversion (typed MidFlightJourney → flat dict round-trip)
5. PerWorkflowPackGraph trigger normalization (.triggers property)
6. Global pack config parsing (PackGlobalConfig)
7. Coordinator _resolve_triggers compatibility
8. Backward compat: nested_chats, journeys, mid_flight_journeys all work
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError


# ---------------------------------------------------------------------------
# Direct imports from schema.py to avoid __init__.py chain
# ---------------------------------------------------------------------------
import importlib.util
import sys
import types
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent


def _direct_import(module_name: str, file_path: Path):
    parts = module_name.split(".")
    for i in range(1, len(parts)):
        parent = ".".join(parts[:i])
        if parent not in sys.modules:
            m = types.ModuleType(parent)
            m.__path__ = [str(_ROOT / parent.replace(".", "/"))]
            m.__package__ = parent
            sys.modules[parent] = m
    spec = importlib.util.spec_from_file_location(module_name, str(file_path))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)
    return mod


# Pre-register namespace stubs
for _ns in [
    "mozaiksai",
    "mozaiksai.core",
    "mozaiksai.core.workflow",
    "mozaiksai.core.workflow.pack",
]:
    if _ns not in sys.modules:
        _m = types.ModuleType(_ns)
        _m.__path__ = [str(_ROOT / _ns.replace(".", "/"))]
        _m.__package__ = _ns
        sys.modules[_ns] = _m

_schema = _direct_import(
    "mozaiksai.core.workflow.pack.schema",
    _ROOT / "mozaiksai" / "core" / "workflow" / "pack" / "schema.py",
)

# Pull symbols
MFJContract = _schema.MFJContract
MFJFanOutConfig = _schema.MFJFanOutConfig
MFJFanInConfig = _schema.MFJFanInConfig
MidFlightJourney = _schema.MidFlightJourney
PackGraphV2Entry = _schema.PackGraphV2Entry
PerWorkflowPackGraph = _schema.PerWorkflowPackGraph
WorkflowEntry = _schema.WorkflowEntry
GateEntry = _schema.GateEntry
SequentialJourney = _schema.SequentialJourney
PackGlobalConfig = _schema.PackGlobalConfig
SpawnMode = _schema.SpawnMode
MergeMode = _schema.MergeMode
PartialFailureStrategy = _schema.PartialFailureStrategy
detect_schema_version = _schema.detect_schema_version
parse_pack_graph = _schema.parse_pack_graph
parse_global_config = _schema.parse_global_config
_v2_entry_to_mfj = _schema._v2_entry_to_mfj
_mfj_to_v2_dict = _schema._mfj_to_v2_dict


# ===========================================================================
# 1. Pydantic model validation
# ===========================================================================

class TestMFJContract:
    def test_defaults(self):
        c = MFJContract()
        assert c.required == []
        assert c.optional == []

    def test_with_values(self):
        c = MFJContract(required=["a", "b"], optional=["c"])
        assert c.required == ["a", "b"]
        assert c.optional == ["c"]

    def test_extra_fields_forbidden(self):
        with pytest.raises(ValidationError):
            MFJContract(required=["a"], bogus="x")


class TestMFJFanOutConfig:
    def test_defaults(self):
        fo = MFJFanOutConfig()
        assert fo.spawn_mode == SpawnMode.WORKFLOW
        assert fo.generator_workflow is None
        assert fo.max_children == 0
        assert fo.timeout_seconds is None

    def test_generator_subrun(self):
        fo = MFJFanOutConfig(
            spawn_mode="generator_subrun",
            generator_workflow="AgentGenerator",
        )
        assert fo.spawn_mode == SpawnMode.GENERATOR_SUBRUN
        assert fo.generator_workflow == "AgentGenerator"

    def test_timeout_must_be_positive(self):
        with pytest.raises(ValidationError):
            MFJFanOutConfig(timeout_seconds=-5)

    def test_max_children_non_negative(self):
        with pytest.raises(ValidationError):
            MFJFanOutConfig(max_children=-1)


class TestMFJFanInConfig:
    def test_defaults(self):
        fi = MFJFanInConfig()
        assert fi.merge_mode == MergeMode.CONCATENATE
        assert fi.inject_as == "child_results"
        assert fi.on_partial_failure == PartialFailureStrategy.RESUME_WITH_AVAILABLE
        assert fi.resume_agent is None

    def test_structured_merge(self):
        fi = MFJFanInConfig(merge_mode="structured", resume_agent="Overview")
        assert fi.merge_mode == MergeMode.STRUCTURED
        assert fi.resume_agent == "Overview"


class TestMidFlightJourney:
    def test_minimal_valid(self):
        mfj = MidFlightJourney(id="mfj_1", trigger_agent="PatternAgent")
        assert mfj.id == "mfj_1"
        assert mfj.trigger_agent == "PatternAgent"
        assert mfj.requires == []
        assert mfj.fan_out.spawn_mode == SpawnMode.WORKFLOW
        assert mfj.fan_in.merge_mode == MergeMode.CONCATENATE

    def test_full_config(self):
        mfj = MidFlightJourney(
            id="planning_phase",
            description="Fan out to plan each domain",
            trigger_agent="PatternSelector",
            trigger_on="structured_output_ready",
            requires=["intake_phase"],
            fan_out=MFJFanOutConfig(
                spawn_mode="generator_subrun",
                generator_workflow="AgentGenerator",
                child_initial_agent="PlannerAgent",
                timeout_seconds=300,
                input_contract=MFJContract(required=["InterviewTranscript"]),
            ),
            fan_in=MFJFanInConfig(
                resume_agent="ProjectOverview",
                merge_mode="structured",
                inject_as="planning_results",
                on_partial_failure="prompt_user",
                output_contract=MFJContract(required=["plan_json"]),
            ),
        )
        assert mfj.fan_out.spawn_mode == SpawnMode.GENERATOR_SUBRUN
        assert mfj.fan_out.input_contract.required == ["InterviewTranscript"]
        assert mfj.fan_in.inject_as == "planning_results"
        assert mfj.fan_in.on_partial_failure == PartialFailureStrategy.PROMPT_USER

    def test_empty_id_rejected(self):
        with pytest.raises(ValidationError):
            MidFlightJourney(id="", trigger_agent="Agent")

    def test_empty_trigger_agent_rejected(self):
        with pytest.raises(ValidationError):
            MidFlightJourney(id="x", trigger_agent="  ")

    def test_extra_fields_forbidden(self):
        with pytest.raises(ValidationError):
            MidFlightJourney(id="x", trigger_agent="A", unknown_field=True)


# ===========================================================================
# 2. Version detection
# ===========================================================================

class TestVersionDetection:
    def test_v3_explicit(self):
        assert detect_schema_version({"version": 3}) == 3

    def test_v3_by_key(self):
        assert detect_schema_version({"mid_flight_journeys": []}) == 3

    def test_v2_journeys(self):
        assert detect_schema_version({"journeys": []}) == 2

    def test_v1_nested_chats(self):
        assert detect_schema_version({"nested_chats": []}) == 1

    def test_unknown_empty(self):
        assert detect_schema_version({}) == 0

    def test_v3_takes_priority_over_v2(self):
        assert detect_schema_version({"mid_flight_journeys": [], "journeys": []}) == 3

    def test_explicit_version_overrides(self):
        assert detect_schema_version({"version": 3, "journeys": []}) == 3


# ===========================================================================
# 3. v2 → v3 conversion
# ===========================================================================

class TestV2ToV3Conversion:
    def test_minimal_entry(self):
        entry = {"trigger_agent": "PatternAgent"}
        mfj = _v2_entry_to_mfj(entry)
        assert mfj.trigger_agent == "PatternAgent"
        assert mfj.id == "trigger_PatternAgent"
        assert mfj.fan_out.spawn_mode == SpawnMode.WORKFLOW

    def test_full_entry(self):
        entry = {
            "id": "planning",
            "trigger_agent": "PatternAgent",
            "requires": ["intake"],
            "required_context": ["transcript"],
            "expected_output_keys": ["plan"],
            "spawn_mode": "generator_subrun",
            "generator_workflow": "AgentGen",
            "child_initial_agent": "Planner",
            "resume_agent": "Overview",
            "merge_mode": "structured",
            "timeout_seconds": 120,
            "on_partial_failure": "fail_all",
        }
        mfj = _v2_entry_to_mfj(entry)
        assert mfj.id == "planning"
        assert mfj.requires == ["intake"]
        assert mfj.fan_out.spawn_mode == SpawnMode.GENERATOR_SUBRUN
        assert mfj.fan_out.generator_workflow == "AgentGen"
        assert mfj.fan_out.child_initial_agent == "Planner"
        assert mfj.fan_out.timeout_seconds == 120
        assert mfj.fan_out.input_contract.required == ["transcript"]
        assert mfj.fan_in.resume_agent == "Overview"
        assert mfj.fan_in.merge_mode == MergeMode.STRUCTURED
        assert mfj.fan_in.on_partial_failure == PartialFailureStrategy.FAIL_ALL
        assert mfj.fan_in.output_contract.required == ["plan"]

    def test_missing_trigger_agent_raises(self):
        with pytest.raises(ValueError, match="trigger_agent"):
            _v2_entry_to_mfj({})

    def test_invalid_spawn_mode_ignored(self):
        entry = {"trigger_agent": "A", "spawn_mode": "bogus"}
        mfj = _v2_entry_to_mfj(entry)
        assert mfj.fan_out.spawn_mode == SpawnMode.WORKFLOW  # default

    def test_invalid_merge_mode_ignored(self):
        entry = {"trigger_agent": "A", "merge_mode": "bogus"}
        mfj = _v2_entry_to_mfj(entry)
        assert mfj.fan_in.merge_mode == MergeMode.CONCATENATE  # default


# ===========================================================================
# 4. v3 → v2 round-trip
# ===========================================================================

class TestV3ToV2Roundtrip:
    def test_round_trip_minimal(self):
        mfj = MidFlightJourney(id="t1", trigger_agent="Agent")
        d = _mfj_to_v2_dict(mfj)
        assert d["id"] == "t1"
        assert d["trigger_agent"] == "Agent"
        # Default values should NOT appear in v2 dict
        assert "spawn_mode" not in d
        assert "merge_mode" not in d
        assert "on_partial_failure" not in d

    def test_round_trip_full(self):
        mfj = MidFlightJourney(
            id="gen",
            trigger_agent="P",
            description="Generate files",
            requires=["plan"],
            fan_out=MFJFanOutConfig(
                spawn_mode="generator_subrun",
                generator_workflow="AG",
                child_initial_agent="Init",
                timeout_seconds=60,
                input_contract=MFJContract(required=["ctx_a"]),
            ),
            fan_in=MFJFanInConfig(
                resume_agent="Download",
                merge_mode="collect_all",
                on_partial_failure="prompt_user",
                output_contract=MFJContract(required=["files"]),
            ),
        )
        d = _mfj_to_v2_dict(mfj)
        assert d["spawn_mode"] == "generator_subrun"
        assert d["generator_workflow"] == "AG"
        assert d["child_initial_agent"] == "Init"
        assert d["timeout_seconds"] == 60
        assert d["required_context"] == ["ctx_a"]
        assert d["resume_agent"] == "Download"
        assert d["merge_mode"] == "collect_all"
        assert d["on_partial_failure"] == "prompt_user"
        assert d["expected_output_keys"] == ["files"]
        assert d["requires"] == ["plan"]

        # Re-convert back to MFJ and verify
        mfj2 = _v2_entry_to_mfj(d)
        assert mfj2.id == mfj.id
        assert mfj2.fan_out.spawn_mode == mfj.fan_out.spawn_mode
        assert mfj2.fan_in.merge_mode == mfj.fan_in.merge_mode


# ===========================================================================
# 5. PerWorkflowPackGraph normalization
# ===========================================================================

class TestPerWorkflowPackGraph:
    def test_v3_triggers(self):
        data = {
            "version": 3,
            "mid_flight_journeys": [
                {"id": "mfj1", "trigger_agent": "PatternAgent"},
            ],
        }
        pg = parse_pack_graph(data)
        assert pg.detected_version == 3
        triggers = pg.triggers
        assert len(triggers) == 1
        assert triggers[0].id == "mfj1"

    def test_v2_triggers(self):
        data = {
            "journeys": [
                {"trigger_agent": "PatternAgent", "id": "j1"},
            ],
        }
        pg = parse_pack_graph(data)
        assert pg.detected_version == 2
        triggers = pg.triggers
        assert len(triggers) == 1
        assert triggers[0].id == "j1"
        assert triggers[0].trigger_agent == "PatternAgent"

    def test_legacy_nested_chats(self):
        data = {
            "nested_chats": [
                {"trigger_agent": "Agent"},
            ],
        }
        pg = parse_pack_graph(data)
        assert pg.detected_version == 2  # no version, no mid_flight_journeys
        triggers = pg.triggers
        assert len(triggers) == 1
        assert triggers[0].trigger_agent == "Agent"

    def test_empty_v3(self):
        data = {"version": 3, "mid_flight_journeys": []}
        pg = parse_pack_graph(data)
        assert pg.triggers == []

    def test_raw_journeys_v3(self):
        """raw_journeys property converts v3 MFJ objects to v2 flat dicts."""
        data = {
            "version": 3,
            "mid_flight_journeys": [
                {"id": "t", "trigger_agent": "A", "fan_in": {"merge_mode": "structured"}},
            ],
        }
        pg = parse_pack_graph(data)
        raw = pg.raw_journeys
        assert len(raw) == 1
        assert raw[0]["trigger_agent"] == "A"
        assert raw[0]["merge_mode"] == "structured"

    def test_bad_v2_entries_skipped(self):
        """Entries that can't parse are silently skipped."""
        data = {
            "journeys": [
                {"trigger_agent": "Good"},
                {"no_trigger": True},  # missing trigger_agent
                "not_a_dict",
            ],
        }
        pg = parse_pack_graph(data)
        triggers = pg.triggers
        assert len(triggers) == 1
        assert triggers[0].trigger_agent == "Good"

    def test_extra_fields_preserved(self):
        """Extra fields on PerWorkflowPackGraph are preserved (extra='allow')."""
        data = {"journeys": [], "custom_field": 42}
        pg = parse_pack_graph(data)
        assert pg.model_extra.get("custom_field") == 42


# ===========================================================================
# 6. Global pack config parsing
# ===========================================================================

class TestPackGlobalConfig:
    def test_minimal(self):
        data = {"workflows": [], "journeys": [], "gates": []}
        cfg = parse_global_config(data)
        assert cfg.workflows == []
        assert cfg.journeys == []
        assert cfg.gates == []

    def test_workflows(self):
        data = {
            "workflows": [
                {"id": "HelloWorld", "label": "Hello World"},
                {"id": "AppGen", "dependencies": ["HelloWorld"]},
            ],
        }
        cfg = parse_global_config(data)
        assert len(cfg.workflows) == 2
        assert cfg.workflows[0].id == "HelloWorld"
        assert cfg.workflows[1].id == "AppGen"

    def test_sequential_journey(self):
        data = {
            "journeys": [
                {
                    "id": "onboarding",
                    "steps": ["HelloWorld", ["AppA", "AppB"], "Summary"],
                    "auto_advance": True,
                },
            ],
        }
        cfg = parse_global_config(data)
        assert len(cfg.journeys) == 1
        assert cfg.journeys[0].id == "onboarding"
        assert cfg.journeys[0].auto_advance is True

    def test_gates(self):
        data = {
            "gates": [
                {"from": "HelloWorld", "to": "AppGen", "gating": "required"},
            ],
        }
        cfg = parse_global_config(data)
        assert len(cfg.gates) == 1
        assert cfg.gates[0].from_workflow == "HelloWorld"
        assert cfg.gates[0].to == "AppGen"

    def test_extra_fields_preserved(self):
        data = {"workflows": [], "settings": {"debug": True}}
        cfg = parse_global_config(data)
        assert cfg.model_extra.get("settings") == {"debug": True}


# ===========================================================================
# 7. Enum validation
# ===========================================================================

class TestEnums:
    def test_spawn_mode_values(self):
        assert SpawnMode.WORKFLOW.value == "workflow"
        assert SpawnMode.GENERATOR_SUBRUN.value == "generator_subrun"

    def test_merge_mode_values(self):
        assert MergeMode.CONCATENATE.value == "concatenate"
        assert MergeMode.STRUCTURED.value == "structured"
        assert MergeMode.COLLECT_ALL.value == "collect_all"

    def test_partial_failure_values(self):
        assert PartialFailureStrategy.RESUME_WITH_AVAILABLE.value == "resume_with_available"
        assert PartialFailureStrategy.FAIL_ALL.value == "fail_all"
        assert PartialFailureStrategy.RETRY_FAILED.value == "retry_failed"
        assert PartialFailureStrategy.PROMPT_USER.value == "prompt_user"


# ===========================================================================
# 8. Backward compat: all key layouts work
# ===========================================================================

class TestBackwardCompat:
    def test_nested_chats_only(self):
        """Legacy nested_chats-only config should parse and produce triggers."""
        data = {"nested_chats": [{"trigger_agent": "Legacy"}]}
        pg = parse_pack_graph(data)
        assert len(pg.triggers) == 1
        assert pg.triggers[0].trigger_agent == "Legacy"

    def test_journeys_only(self):
        """v2 journeys config should parse."""
        data = {"journeys": [{"trigger_agent": "V2Agent", "id": "v2"}]}
        pg = parse_pack_graph(data)
        assert len(pg.triggers) == 1
        assert pg.triggers[0].id == "v2"

    def test_mid_flight_journeys_only(self):
        """v3 config should parse."""
        data = {
            "version": 3,
            "mid_flight_journeys": [
                {"id": "v3", "trigger_agent": "V3Agent"},
            ],
        }
        pg = parse_pack_graph(data)
        assert len(pg.triggers) == 1
        assert pg.triggers[0].id == "v3"

    def test_empty_config(self):
        """Totally empty config should parse with no triggers."""
        data = {}
        pg = parse_pack_graph(data)
        assert pg.triggers == []
        assert pg.raw_journeys == []
