from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from tests.import_utils import import_module_directly

_schema = import_module_directly("mozaiksai.core.workflow.pack.schema")
GlobalPackGraph = _schema.GlobalPackGraph
parse_global_pack_graph = _schema.parse_global_pack_graph


def test_pack_metadata_structured_output_transition_options_match_runtime_contract() -> None:
    path = (
        Path(__file__).resolve().parents[1]
        / "factory_app"
        / "workflows"
        / "AgentGenerator"
        / "structured_outputs.yaml"
    )
    spec = yaml.safe_load(path.read_text(encoding="utf-8"))
    fields = spec["models"]["PackGraphTransitionOption"]["fields"]
    assert set(fields.keys()) == {"id", "route_to", "sequence", "context_variables"}
    ui_fields = spec["models"]["PackGraphTransitionUI"]["fields"]
    assert set(ui_fields.keys()) == {"component", "mode", "shell_mode", "props"}
    assert ui_fields["shell_mode"]["variants"] == ["PackGraphShellMode", "null"]


def test_pack_metadata_structured_output_entrypoints_match_runtime_contract() -> None:
    path = (
        Path(__file__).resolve().parents[1]
        / "factory_app"
        / "workflows"
        / "AgentGenerator"
        / "structured_outputs.yaml"
    )
    spec = yaml.safe_load(path.read_text(encoding="utf-8"))
    fields = spec["models"]["PackGraphEntrypoint"]["fields"]
    assert set(fields.keys()) == {
        "id",
        "path",
        "label",
        "transition",
        "workflow",
        "sequence",
        "requiresAuth",
        "order",
        "meta",
    }


def test_parse_global_pack_graph_valid() -> None:
    graph = parse_global_pack_graph(
        {
            "version": 3,
            "workflows": [{"id": "A"}, {"id": "B"}],
            "transitions": [],
            "workflow_sequences": [
                {"id": "build", "steps": [{"workflows": ["A"]}, {"workflows": ["B"]}]}
            ],
        }
    )
    assert isinstance(graph, GlobalPackGraph)
    assert graph.version == 3
    assert [w.id for w in graph.workflows] == ["A", "B"]


def test_parse_global_pack_graph_accepts_optional_transition() -> None:
    graph = parse_global_pack_graph(
        {
            "version": 3,
            "workflows": [{"id": "AppGenerator"}],
            "transitions": [
                {
                    "id": "build_satisfaction_rating",
                    "transition_type": "user_choice_context",
                    "optional": True,
                    "ui": {"component": "BuildSatisfactionRating", "mode": "screen"},
                    "options": [
                        {"id": "rated", "route_to": "workflow_complete", "context_variables": {}},
                        {"id": "skip", "route_to": "workflow_complete", "context_variables": {}},
                    ],
                },
                {
                    "id": "workflow_complete",
                    "transition_type": "workflow_complete",
                    "ui": {"component": "WorkflowCompletion", "mode": "screen"},
                }
            ],
            "workflow_sequences": [
                {
                    "id": "build",
                    "steps": [
                        {"workflows": ["AppGenerator"]},
                        {"transition": "build_satisfaction_rating"},
                    ],
                }
            ],
        }
    )

    assert graph.transitions[0].id == "build_satisfaction_rating"
    assert graph.transitions[0].optional is True


def test_parse_global_pack_graph_allows_workflow_entrypoints() -> None:
    graph = parse_global_pack_graph(
        {
            "version": 3,
            "workflows": [{"id": "ValueEngine"}],
            "entrypoints": [
                {
                    "id": "create_app",
                    "path": "/create",
                    "label": "Create App",
                    "transition": "app_type_selector",
                    "sequence": "build",
                    "requiresAuth": False,
                    "order": 2,
                    "meta": {"title": "Create App"},
                }
            ],
            "workflow_sequences": [
                {
                    "id": "build",
                    "steps": [
                        {"transition": "app_type_selector"},
                        {"workflows": ["ValueEngine"]},
                    ],
                }
            ],
            "transitions": [
                {
                    "id": "app_type_selector",
                    "transition_type": "user_choice_context",
                    "ui": {"component": "AppTypeSelector", "mode": "screen", "shell_mode": "focused"},
                    "options": [{"id": "new_app", "route_to": "ValueEngine", "context_variables": {"app_type": "new"}}],
                }
            ],
        }
    )

    assert graph.entrypoints[0].path == "/create"
    assert graph.entrypoints[0].transition == "app_type_selector"
    assert graph.transitions[0].ui.shell_mode == "focused"


def test_parse_global_pack_graph_allows_transition_option_sequence_override() -> None:
    graph = parse_global_pack_graph(
        {
            "version": 3,
            "workflows": [{"id": "ValueEngine"}, {"id": "ExistingAppDiscovery"}],
            "entrypoints": [
                {
                    "id": "create_app",
                    "path": "/create",
                    "label": "Create App",
                    "transition": "app_type_selector",
                    "sequence": "build",
                }
            ],
            "workflow_sequences": [
                {
                    "id": "build",
                    "steps": [
                        {"transition": "app_type_selector"},
                        {"workflows": ["ValueEngine"]},
                    ],
                },
                {
                    "id": "brownfield_app_adoption",
                    "steps": [
                        {"transition": "app_type_selector"},
                        {"transition": "brownfield_path_selector"},
                        {"transition": "brownfield_repo_input"},
                        {"workflows": ["ExistingAppDiscovery"]},
                    ],
                },
            ],
            "transitions": [
                {
                    "id": "app_type_selector",
                    "transition_type": "user_choice_context",
                    "ui": {"component": "AppTypeSelector", "mode": "screen"},
                    "options": [
                        {"id": "greenfield_app", "route_to": "ValueEngine", "sequence": "build"},
                        {
                            "id": "brownfield_app",
                            "route_to": "brownfield_path_selector",
                            "sequence": "brownfield_app_adoption",
                        },
                    ],
                },
                {
                    "id": "brownfield_path_selector",
                    "transition_type": "user_choice_context",
                    "ui": {"component": "BrownfieldPathSelector", "mode": "screen"},
                    "options": [
                        {
                            "id": "light_integration",
                            "route_to": "brownfield_repo_input",
                            "sequence": "brownfield_app_adoption",
                        },
                        {
                            "id": "full_migration",
                            "route_to": "brownfield_repo_input",
                            "sequence": "brownfield_app_adoption",
                        },
                    ],
                },
                {
                    "id": "brownfield_repo_input",
                    "transition_type": "user_choice_context",
                    "ui": {"component": "BrownfieldRepoInput", "mode": "screen"},
                    "options": [
                        {
                            "id": "start_discovery",
                            "route_to": "ExistingAppDiscovery",
                            "sequence": "brownfield_app_adoption",
                        },
                    ],
                },
            ],
        }
    )

    option_map = {option.id: option for option in graph.transitions[0].options}
    assert option_map["greenfield_app"].sequence == "build"
    assert option_map["brownfield_app"].sequence == "brownfield_app_adoption"
    assert option_map["brownfield_app"].route_to == "brownfield_path_selector"


def test_parse_global_pack_graph_rejects_unknown_transition_option_sequence() -> None:
    with pytest.raises(ValueError):
        parse_global_pack_graph(
            {
                "version": 3,
                "workflows": [{"id": "ValueEngine"}],
                "workflow_sequences": [
                    {
                        "id": "build",
                        "steps": [
                            {"transition": "app_type_selector"},
                            {"workflows": ["ValueEngine"]},
                        ],
                    }
                ],
                "transitions": [
                    {
                        "id": "app_type_selector",
                        "transition_type": "user_choice_context",
                        "ui": {"component": "AppTypeSelector", "mode": "screen"},
                        "options": [
                            {
                                "id": "greenfield_app",
                                "route_to": "ValueEngine",
                                "sequence": "missing_sequence",
                            }
                        ],
                    }
                ],
            }
        )


def test_parse_global_pack_graph_rejects_entrypoint_unknown_transition() -> None:
    with pytest.raises(ValueError):
        parse_global_pack_graph(
            {
                "version": 3,
                "workflows": [{"id": "ValueEngine"}],
                "entrypoints": [
                    {"id": "create_app", "path": "/create", "transition": "missing"}
                ],
                "workflow_sequences": [],
                "transitions": [],
            }
        )


def test_parse_global_pack_graph_accepts_workflow_sequences() -> None:
    graph = parse_global_pack_graph(
        {
            "version": 3,
            "workflows": [{"id": "A"}, {"id": "B"}],
            "transitions": [],
            "workflow_sequences": [
                {
                    "id": "build",
                    "affected_declarative_families": ["concept", "", "app_bundle"],
                    "steps": [{"workflows": ["A"]}, {"workflows": ["B"]}],
                }
            ],
        }
    )
    assert isinstance(graph, GlobalPackGraph)
    assert graph.journeys[0].id == "build"
    assert graph.journeys[0].affected_declarative_families == ["concept", "app_bundle"]


def test_parse_global_pack_graph_rejects_removed_journeys_key() -> None:
    with pytest.raises(ValueError):
        parse_global_pack_graph(
            {
                "version": 3,
                "workflows": [{"id": "A"}, {"id": "B"}],
                "transitions": [],
                "journeys": [{"id": "build", "steps": [{"workflows": ["A"]}, {"workflows": ["B"]}]}],
            }
        )


def test_parse_global_pack_graph_duplicate_workflow_ids_fails() -> None:
    with pytest.raises(ValueError):
        parse_global_pack_graph(
            {
                "version": 3,
                "workflows": [{"id": "A"}, {"id": "A"}],
                "transitions": [],
                "workflow_sequences": [],
            }
        )


def test_parse_global_pack_graph_rejects_unknown_dependency() -> None:
    with pytest.raises(ValueError):
        parse_global_pack_graph(
            {
                "version": 3,
                "workflows": [{"id": "AppGenerator", "dependencies": ["MissingWorkflow"]}],
                "transitions": [],
                "workflow_sequences": [],
            }
        )


def test_parse_global_pack_graph_rejects_duplicate_workflow_in_journey() -> None:
    with pytest.raises(ValueError):
        parse_global_pack_graph(
            {
                "version": 3,
                "workflows": [{"id": "ValueEngine"}, {"id": "DesignDocs"}],
                "transitions": [],
                "workflow_sequences": [
                    {
                        "id": "build",
                        "steps": [
                            {"workflows": ["ValueEngine"]},
                            {"workflows": ["DesignDocs", "ValueEngine"]},
                        ],
                    }
                ],
            }
        )


def test_parse_global_pack_graph_rejects_transition_ui_component_file_path() -> None:
    with pytest.raises(ValueError):
        parse_global_pack_graph(
            {
                "version": 3,
                "workflows": [{"id": "A"}, {"id": "B"}],
                "transitions": [
                    {
                        "id": "choose_path",
                        "transition_type": "user_choice",
                        "ui": {"component": "ui/screens/ChoosePath.jsx", "mode": "screen"},
                        "options": [{"id": "to_b", "route_to": "B"}],
                    }
                ],
                "workflow_sequences": [
                    {"id": "build", "steps": [{"workflows": ["A"]}, {"workflows": ["B"]}]}
                ],
            }
        )


def test_parse_global_pack_graph_rejects_transition_presentation_fields() -> None:
    with pytest.raises(ValueError):
        parse_global_pack_graph(
            {
                "version": 3,
                "workflows": [{"id": "A"}, {"id": "B"}],
                "transitions": [
                    {
                        "id": "choose_path",
                        "transition_type": "user_choice",
                        "ui": {"component": "LauncherScreen", "mode": "screen"},
                        "config": {"title": "Choose"},
                        "options": [{"id": "to_b", "label": "Go", "route_to": "B"}],
                    }
                ],
                "workflow_sequences": [],
            }
        )


def test_parse_global_pack_graph_allows_transition_ui_props() -> None:
    graph = parse_global_pack_graph(
        {
            "version": 3,
            "workflows": [{"id": "ValueEngine"}, {"id": "AgentGenerator"}],
            "transitions": [
                {
                    "id": "entry",
                    "transition_type": "user_choice",
                    "ui": {
                        "component": "LauncherScreen",
                        "mode": "screen",
                        "props": {
                            "title": "Choose Path",
                            "options": {
                                "new_app": {
                                    "label": "New App",
                                    "description": "Start from scratch",
                                }
                            },
                        },
                    },
                    "options": [{"id": "new_app", "route_to": "ValueEngine"}],
                }
            ],
            "workflow_sequences": [],
        }
    )

    assert graph.transitions[0].ui is not None
    assert graph.transitions[0].ui.props["title"] == "Choose Path"


def test_parse_global_pack_graph_allows_transition_context_variables() -> None:
    graph = parse_global_pack_graph(
        {
            "version": 3,
            "workflows": [{"id": "ValueEngine"}],
            "workflow_sequences": [],
            "transitions": [
                {
                    "id": "entry",
                    "transition_type": "user_choice_context",
                    "ui": {"component": "LauncherScreen", "mode": "screen"},
                    "options": [
                        {
                            "id": "new_app",
                            "route_to": "ValueEngine",
                            "context_variables": {"app_type": "new"},
                        }
                    ],
                }
            ],
        }
    )

    option = graph.transitions[0].options[0]
    assert option.context_variables == {"app_type": "new"}
    assert option.route_to == "ValueEngine"


def test_parse_global_pack_graph_rejects_top_level_route_to_for_user_choice_context() -> None:
    with pytest.raises(ValueError, match=r"options\[\]\.route_to"):
        parse_global_pack_graph(
            {
                "version": 3,
                "workflows": [{"id": "ValueEngine"}],
                "workflow_sequences": [],
                "transitions": [
                    {
                        "id": "entry",
                        "transition_type": "user_choice_context",
                        "ui": {"component": "LauncherScreen", "mode": "screen"},
                        "route_to": "ValueEngine",
                        "options": [
                            {
                                "id": "new_app",
                                "route_to": "ValueEngine",
                                "context_variables": {"app_type": "new"},
                            }
                        ],
                    }
                ],
            }
        )


def test_parse_global_pack_graph_allows_transition_steps_in_workflow_sequences() -> None:
    graph = parse_global_pack_graph(
        {
            "version": 3,
            "workflows": [{"id": "ValueEngine"}, {"id": "DesignDocs"}],
            "workflow_sequences": [
                {
                    "id": "build",
                    "steps": [
                        {"workflows": ["ValueEngine"]},
                        {"transition": "coding_journey_selector"},
                        {"workflows": ["DesignDocs"]},
                    ],
                }
            ],
            "transitions": [
                {
                    "id": "coding_journey_selector",
                    "transition_type": "user_choice_context",
                    "ui": {"component": "CodingJourneySelector", "mode": "screen"},
                    "options": [{"id": "guided", "route_to": "DesignDocs", "context_variables": {"design_docs_hitl": True}}],
                }
            ],
        }
    )

    assert graph.journeys[0].steps[1].transition == "coding_journey_selector"


def test_parse_global_pack_graph_rejects_same_phase_required_dependency() -> None:
    with pytest.raises(ValueError):
        parse_global_pack_graph(
            {
                "version": 3,
                "workflows": [
                    {"id": "AgentGenerator"},
                    {"id": "AppGenerator", "dependencies": ["AgentGenerator"]},
                ],
                "transitions": [],
                "workflow_sequences": [
                    {
                        "id": "build",
                        "steps": [{"workflows": ["AgentGenerator", "AppGenerator"]}],
                    }
                ],
            }
        )


def test_parse_global_pack_graph_allows_serial_required_dependency() -> None:
    graph = parse_global_pack_graph(
        {
            "version": 3,
            "workflows": [
                {"id": "AgentGenerator"},
                {"id": "AppGenerator", "dependencies": ["AgentGenerator"]},
            ],
            "transitions": [],
            "workflow_sequences": [
                {
                    "id": "build",
                    "steps": [
                        {"workflows": ["AgentGenerator"]},
                        {"workflows": ["AppGenerator"]},
                    ],
                }
            ],
        }
    )

    assert graph.journeys[0].steps[0].workflows == ["AgentGenerator"]
    assert graph.journeys[0].steps[1].workflows == ["AppGenerator"]


def test_parse_global_pack_graph_allows_same_phase_optional_dependency() -> None:
    graph = parse_global_pack_graph(
        {
            "version": 3,
            "workflows": [
                {"id": "ThemeCapture"},
                {
                    "id": "ExistingAppDiscovery",
                    "dependencies": [{"id": "ThemeCapture", "gating": "optional"}],
                },
            ],
            "transitions": [],
            "workflow_sequences": [
                {
                    "id": "existing_app_onboarding",
                    "steps": [{"workflows": ["ThemeCapture", "ExistingAppDiscovery"]}],
                }
            ],
        }
    )

    assert graph.journeys[0].steps[0].workflows == ["ThemeCapture", "ExistingAppDiscovery"]


# ---------------------------------------------------------------------------
# startup_mode — Gap #8: BackendOnly enforcement
# ---------------------------------------------------------------------------


def test_parse_global_pack_graph_allows_backend_only_workflow() -> None:
    """BackendOnly workflow registered in workflows[] is valid on its own."""
    graph = parse_global_pack_graph(
        {
            "version": 3,
            "workflows": [
                {"id": "ProposalReviewWorkflow", "startup_mode": "BackendOnly"},
                {"id": "AppGenerator"},
            ],
            "transitions": [],
            "workflow_sequences": [
                {"id": "build", "steps": [{"workflows": ["AppGenerator"]}]}
            ],
        }
    )
    backend_entry = next(w for w in graph.workflows if w.id == "ProposalReviewWorkflow")
    assert backend_entry.startup_mode == "BackendOnly"


def test_parse_global_pack_graph_rejects_backend_only_in_sequence() -> None:
    """BackendOnly workflow must not appear in a workflow_sequence step."""
    with pytest.raises(ValueError, match="BackendOnly"):
        parse_global_pack_graph(
            {
                "version": 3,
                "workflows": [
                    {"id": "ProposalReviewWorkflow", "startup_mode": "BackendOnly"},
                    {"id": "AppGenerator"},
                ],
                "transitions": [],
                "workflow_sequences": [
                    {
                        "id": "build",
                        "steps": [
                            {"workflows": ["AppGenerator"]},
                            {"workflows": ["ProposalReviewWorkflow"]},
                        ],
                    }
                ],
            }
        )


def test_parse_global_pack_graph_rejects_backend_only_entrypoint() -> None:
    """BackendOnly workflow must not be the target of an entrypoint."""
    with pytest.raises(ValueError, match="BackendOnly"):
        parse_global_pack_graph(
            {
                "version": 3,
                "workflows": [
                    {"id": "DocumentsAnalysisWorkflow", "startup_mode": "BackendOnly"},
                ],
                "entrypoints": [
                    {
                        "id": "start_analysis",
                        "path": "/analysis",
                        "label": "Start Analysis",
                        "workflow": "DocumentsAnalysisWorkflow",
                    }
                ],
                "transitions": [],
                "workflow_sequences": [],
            }
        )


def test_parse_global_pack_graph_startup_mode_round_trips() -> None:
    """UserDriven and AgentDriven startup_mode values parse correctly."""
    graph = parse_global_pack_graph(
        {
            "version": 3,
            "workflows": [
                {"id": "ValueEngine", "startup_mode": "UserDriven"},
                {"id": "AppGenerator", "startup_mode": "AgentDriven"},
            ],
            "transitions": [],
            "workflow_sequences": [
                {
                    "id": "build",
                    "steps": [
                        {"workflows": ["ValueEngine"]},
                        {"workflows": ["AppGenerator"]},
                    ],
                }
            ],
        }
    )
    mode_map = {w.id: w.startup_mode for w in graph.workflows}
    assert mode_map["ValueEngine"] == "UserDriven"
    assert mode_map["AppGenerator"] == "AgentDriven"


def test_parse_global_pack_graph_launch_taxonomy_round_trips() -> None:
    """Workflow registry entries preserve first-party launch taxonomy fields."""
    graph = parse_global_pack_graph(
        {
            "version": 3,
            "workflows": [
                {
                    "id": "ValueEngine",
                    "startup_mode": "UserDriven",
                    "interaction_mode": "user_guided",
                    "launch_behavior": "auto_start",
                    "handoff_style": "continuous_chat",
                },
                {
                    "id": "AuditWorkflow",
                    "startup_mode": "BackendOnly",
                    "interaction_mode": "backend_only",
                    "launch_behavior": "none",
                    "handoff_style": "background",
                },
            ],
            "transitions": [],
            "workflow_sequences": [
                {
                    "id": "build",
                    "steps": [{"workflows": ["ValueEngine"]}],
                }
            ],
        }
    )

    by_id = {workflow.id: workflow for workflow in graph.workflows}
    assert by_id["ValueEngine"].interaction_mode == "user_guided"
    assert by_id["ValueEngine"].launch_behavior == "auto_start"
    assert by_id["ValueEngine"].handoff_style == "continuous_chat"
    assert by_id["AuditWorkflow"].interaction_mode == "backend_only"
    assert by_id["AuditWorkflow"].launch_behavior == "none"
    assert by_id["AuditWorkflow"].handoff_style == "background"


def test_parse_global_pack_graph_rejects_backend_only_chat_taxonomy() -> None:
    with pytest.raises(ValueError, match="BackendOnly workflows must use"):
        parse_global_pack_graph(
            {
                "version": 3,
                "workflows": [
                    {
                        "id": "AuditWorkflow",
                        "startup_mode": "BackendOnly",
                        "interaction_mode": "user_guided",
                    },
                ],
                "transitions": [],
                "workflow_sequences": [],
            }
        )


def test_pack_metadata_structured_output_workflow_has_launch_taxonomy() -> None:
    """AgentGenerator PackGraphWorkflow must declare launch taxonomy fields."""
    path = (
        Path(__file__).resolve().parents[1]
        / "factory_app"
        / "workflows"
        / "AgentGenerator"
        / "structured_outputs.yaml"
    )
    spec = yaml.safe_load(path.read_text(encoding="utf-8"))
    wf_fields = spec["models"]["PackGraphWorkflow"]["fields"]
    assert "startup_mode" in wf_fields, "PackGraphWorkflow must declare startup_mode"
    sm = wf_fields["startup_mode"]
    assert sm["type"] == "union"
    assert "null" in sm["variants"]
    # Must reference the PackGraphStartupMode literal or a string type
    non_null = [v for v in sm["variants"] if v != "null"]
    assert non_null, "startup_mode union must have a non-null variant"
    # PackGraphStartupMode literal must exist and include BackendOnly
    assert "PackGraphStartupMode" in spec["models"]
    values = spec["models"]["PackGraphStartupMode"]["values"]
    assert "BackendOnly" in values
    assert "UserDriven" in values
    assert "AgentDriven" in values

    expected_literals = {
        "interaction_mode": ("PackGraphInteractionMode", {"agent_directed", "user_guided", "backend_only"}),
        "launch_behavior": ("PackGraphLaunchBehavior", {"auto_start", "wait_for_user", "none"}),
        "handoff_style": ("PackGraphHandoffStyle", {"continuous_chat", "separate_chat", "background"}),
    }
    for field_name, (literal_name, literal_values) in expected_literals.items():
        assert field_name in wf_fields, f"PackGraphWorkflow must declare {field_name}"
        field = wf_fields[field_name]
        assert field["type"] == "union"
        assert "null" in field["variants"]
        assert literal_name in field["variants"]
        assert literal_name in spec["models"]
        assert set(spec["models"][literal_name]["values"]) == literal_values


