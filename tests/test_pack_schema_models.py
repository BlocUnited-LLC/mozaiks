from __future__ import annotations

import pytest

from tests.import_utils import import_module_directly

_schema = import_module_directly("mozaiksai.core.workflow.pack.schema")
GlobalPackGraph = _schema.GlobalPackGraph
WorkflowPackGraph = _schema.WorkflowPackGraph
parse_global_pack_graph = _schema.parse_global_pack_graph
parse_workflow_pack_graph = _schema.parse_workflow_pack_graph


def test_parse_global_pack_graph_valid() -> None:
    graph = parse_global_pack_graph(
        {
            "version": 2,
            "workflows": [{"id": "A"}, {"id": "B"}],
            "journeys": [{"id": "build", "steps": ["A", ["B"]]}],
        }
    )
    assert isinstance(graph, GlobalPackGraph)
    assert graph.version == 2
    assert [w.id for w in graph.workflows] == ["A", "B"]


def test_parse_global_pack_graph_duplicate_workflow_ids_fails() -> None:
    with pytest.raises(ValueError):
        parse_global_pack_graph(
            {
                "version": 2,
                "workflows": [{"id": "A"}, {"id": "A"}],
                "journeys": [],
            }
        )


def test_parse_workflow_pack_graph_rejects_legacy_keys() -> None:
    with pytest.raises(ValueError):
        parse_workflow_pack_graph(
            {
                "version": 3,
                "mid_flight_journeys": [],
                "nested_chats": [],
            }
        )


def test_parse_workflow_pack_graph_accepts_custom_strategy_name() -> None:
    graph = parse_workflow_pack_graph(
        {
            "version": 3,
            "mid_flight_journeys": [
                {
                    "id": "mfj",
                    "trigger_agent": "Planner",
                    "trigger_on": "structured_output",
                    "fan_out": {
                        "spawn_mode": "workflow",
                        "max_children": 3,
                        "timeout_seconds": 10,
                        "input_contract": {"required": [], "optional": []},
                    },
                    "fan_in": {
                        "resume_agent": "ResumeAgent",
                        "resume_entry_agent": "ResumeRouterAgent",
                        "aggregation_strategy": "custom:merge_vote",
                        "inject_as": "mfj_outputs",
                        "on_partial_failure": "resume_with_available",
                        "timeout_seconds": 10,
                    },
                    "output_contract": {"required": [], "optional": []},
                }
            ],
        }
    )
    assert isinstance(graph, WorkflowPackGraph)
    assert graph.mid_flight_journeys[0].fan_in.aggregation_strategy == "custom:merge_vote"


def test_parse_workflow_pack_graph_requires_resume_entry_agent() -> None:
    with pytest.raises(ValueError):
        parse_workflow_pack_graph(
            {
                "version": 3,
                "mid_flight_journeys": [
                    {
                        "id": "mfj",
                        "trigger_agent": "Planner",
                        "trigger_on": "structured_output",
                        "fan_out": {
                            "spawn_mode": "workflow",
                            "max_children": 3,
                            "timeout_seconds": 10,
                            "input_contract": {"required": [], "optional": []},
                        },
                        "fan_in": {
                            "resume_agent": "HostAgent",
                            "aggregation_strategy": "collect_all",
                            "inject_as": "mfj_outputs",
                            "on_partial_failure": "resume_with_available",
                            "timeout_seconds": 10,
                        },
                        "output_contract": {"required": [], "optional": []},
                    }
                ],
            }
        )


def test_parse_workflow_pack_graph_rejects_non_mfj_inject_key() -> None:
    with pytest.raises(ValueError):
        parse_workflow_pack_graph(
            {
                "version": 3,
                "mid_flight_journeys": [
                    {
                        "id": "mfj",
                        "trigger_agent": "Planner",
                        "trigger_on": "structured_output",
                        "fan_out": {
                            "spawn_mode": "workflow",
                            "max_children": 3,
                            "timeout_seconds": 10,
                            "input_contract": {"required": [], "optional": []},
                        },
                        "fan_in": {
                            "resume_agent": "HostAgent",
                            "resume_entry_agent": "ResumeRouterAgent",
                            "aggregation_strategy": "collect_all",
                            "inject_as": "planning_outputs",
                            "on_partial_failure": "resume_with_available",
                            "timeout_seconds": 10,
                        },
                        "output_contract": {"required": [], "optional": []},
                    }
                ],
            }
        )


def test_parse_workflow_pack_graph_requires_authoring_workflow_for_authoring_subrun() -> None:
    with pytest.raises(ValueError):
        parse_workflow_pack_graph(
            {
                "version": 3,
                "mid_flight_journeys": [
                    {
                        "id": "mfj",
                        "trigger_agent": "Planner",
                        "trigger_on": "structured_output",
                        "fan_out": {
                            "spawn_mode": "workflow_authoring_subrun",
                            "max_children": 3,
                            "timeout_seconds": 10,
                            "input_contract": {"required": [], "optional": []},
                        },
                        "fan_in": {
                            "resume_agent": "HostAgent",
                            "resume_entry_agent": "ResumeRouterAgent",
                            "aggregation_strategy": "collect_all",
                            "inject_as": "mfj_outputs",
                            "on_partial_failure": "resume_with_available",
                            "timeout_seconds": 10,
                        },
                        "output_contract": {"required": [], "optional": []},
                    }
                ],
            }
        )
