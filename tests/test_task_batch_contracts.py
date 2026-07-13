from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from ag2.network.policies import CHANNEL_STATE_DEP

from mozaiksai.core.workflow.task_batches import (
    execute_task_batches_for_trigger,
    load_task_batches_config,
    parse_task_batches_config,
    resolve_path_value,
    workflow_has_task_batches,
)


def _ag2_task_context(kwargs: dict) -> dict:
    state = kwargs["dependencies"][CHANNEL_STATE_DEP]
    return dict(state.context_vars)


def _valid_payload() -> dict:
    return {
        "version": 1,
        "batches": [
            {
                "id": "document_reviews",
                "trigger_agent": "TriageAgent",
                "source": {
                    "kind": "context_variable",
                    "path": "review_plan.tasks",
                    "task_model": "DocumentReviewTask",
                },
                "worker": {
                    "mode": "ag2_agent",
                    "agent_field": "initial_agent",
                    "prompt_field": "initial_message",
                },
                "execution": {
                    "concurrency": 4,
                    "failure_policy": "fail_batch",
                },
                "result": {
                    "context_key": "document_review_results",
                    "status_key": "document_review_status",
                },
            }
        ],
    }


def test_parse_task_batches_config_accepts_canonical_payload() -> None:
    config = parse_task_batches_config(_valid_payload())

    assert config.version == 1
    assert config.batches[0].id == "document_reviews"
    assert config.batches[0].source.task_model == "DocumentReviewTask"
    assert config.batches[0].execution.concurrency == 4


def test_parse_task_batches_config_accepts_minimal_conveyor_payload() -> None:
    config = parse_task_batches_config(
        {
            "version": 1,
            "conveyors": [
                {
                    "id": "workflow_generation",
                    "decomposition_agent": "DecompositionAgent",
                    "execution_agents": ["AgentRosterAgent", "ToolsAgent"],
                    "concurrency": 3,
                }
            ],
        }
    )

    batch = config.batches[0]
    assert batch.id == "workflow_generation"
    assert batch.trigger_agent == "DecompositionAgent"
    assert batch.source.kind == "structured_output"
    assert batch.source.path == "DecompositionPlan.tasks"
    assert batch.source.task_model == "DecomposedTask"
    assert batch.worker.agent_field == "execution_agent"
    assert batch.worker.prompt_field == "task_prompt"
    assert batch.execution.dependency_field == "depends_on"
    assert batch.execution.concurrency == 3
    assert batch.result.context_key == "workflow_generation_results"
    assert batch.result.status_key == "workflow_generation_status"
    assert batch.result.require_owned_paths is False
    assert batch.allowed_execution_agents == ["AgentRosterAgent", "ToolsAgent"]


def test_parse_task_batches_config_rejects_duplicate_batch_ids() -> None:
    payload = _valid_payload()
    payload["batches"].append(dict(payload["batches"][0]))

    with pytest.raises(ValueError, match="duplicate task batch id"):
        parse_task_batches_config(payload)


def test_parse_task_batches_config_rejects_duplicate_conveyor_and_batch_ids() -> None:
    payload = _valid_payload()
    payload["conveyors"] = [
        {
            "id": "document_reviews",
            "decomposition_agent": "TriageAgent",
            "execution_agents": ["ReviewAgent"],
        }
    ]

    with pytest.raises(ValueError, match="duplicate task batch id"):
        parse_task_batches_config(payload)


def test_load_task_batches_config_resolves_workflow_local_file(tmp_path: Path) -> None:
    workflow_dir = tmp_path / "TaskBatchWorkflow"
    batch_dir = workflow_dir / "extended_orchestration"
    batch_dir.mkdir(parents=True)
    (batch_dir / "task_batches.yaml").write_text(
        """
version: 1
batches:
  - id: document_reviews
    trigger_agent: TriageAgent
    source:
      kind: context_variable
      path: review_plan.tasks
      task_model: DocumentReviewTask
    result:
      context_key: document_review_results
      status_key: document_review_status
""".lstrip(),
        encoding="utf-8",
    )

    config = load_task_batches_config("TaskBatchWorkflow", workflows_root=tmp_path)

    assert config is not None
    assert config.batches[0].id == "document_reviews"
    assert workflow_has_task_batches("TaskBatchWorkflow", workflows_root=tmp_path) is True


def test_resolve_path_value_reads_context_variable_paths() -> None:
    payload = {"app_task_batch_items": [{"task_id": "a"}, {"task_id": "b"}]}

    assert resolve_path_value(payload, "app_task_batch_items.1.task_id") == "b"


@pytest.mark.asyncio
async def test_execute_task_batches_for_trigger_collects_worker_outputs() -> None:
    config = parse_task_batches_config(
        {
            "version": 1,
            "batches": [
                {
                    "id": "app_build_tasks",
                    "trigger_agent": "AppPlanAgent",
                    "source": {
                        "kind": "context_variable",
                        "path": "app_task_batch_items",
                        "task_model": "AppBuildTask",
                    },
                    "worker": {
                        "mode": "ag2_agent",
                        "agent_field": "initial_agent",
                        "prompt_field": "initial_message",
                        "context_fields": ["task_type"],
                    },
                    "execution": {"concurrency": 2, "failure_policy": "fail_batch"},
                    "result": {
                        "context_key": "app_task_batch_results",
                        "status_key": "app_task_batch_status",
                    },
                }
            ],
        }
    )

    seen_variables: list[dict] = []

    class _FakeAgent:
        async def ask(self, message, **kwargs):
            variables = _ag2_task_context(kwargs)
            seen_variables.append(variables)
            task_id = variables["current_build_task_id"]
            return SimpleNamespace(
                body=json.dumps(
                    {
                        "agent_message": f"done {task_id}",
                        "code_files": [
                            {
                                "filename": f"modules/{task_id}/module.yaml",
                                "content": "name: test",
                            }
                        ],
                    }
                )
            )

    context = {
        "app_task_batch_items": [
            {
                "task_id": "profiles",
                "task_type": "module_contract",
                "initial_agent": "WorkerAgent",
                "initial_message": "Build profiles.",
                "owned_paths": ["modules/profiles/module.yaml"],
            },
            {
                "task_id": "feed",
                "task_type": "module_contract",
                "initial_agent": "WorkerAgent",
                "initial_message": "Build feed.",
                "depends_on": ["profiles"],
                "owned_paths": ["modules/feed/module.yaml"],
            },
        ],
    }

    result = await execute_task_batches_for_trigger(
        workflow_name="AppGenerator",
        trigger_agent="AppPlanAgent",
        batches_config=config,
        agents={"WorkerAgent": _FakeAgent()},
        context_variables=context,
        fresh_agents_per_task=False,
    )

    assert result["app_build_tasks"]["status"] == "completed"
    assert context["app_task_batch_status"] == "completed"
    assert sorted(k for k in context["app_task_batch_results"] if not k.startswith("_")) == [
        "feed",
        "profiles",
    ]
    assert context["app_task_batch_results"]["_meta"]["concurrency"] == 2
    assert context["app_task_batch_results"]["profiles"]["code_files"][0]["filename"] == (
        "modules/profiles/module.yaml"
    )
    assert seen_variables[0]["task_run_mode"] is True
    assert seen_variables[0]["current_build_task_type"] == "module_contract"
    assert seen_variables[0]["dependency_task_outputs"] == {}
    feed_variables = next(
        variables
        for variables in seen_variables
        if variables["current_build_task_id"] == "feed"
    )
    assert "profiles" in feed_variables["dependency_task_outputs"]
    assert feed_variables["dependency_task_outputs"]["profiles"]["code_files"][0]["filename"] == (
        "modules/profiles/module.yaml"
    )
    assert feed_variables["app_task_batch_results"]["_meta"]["status"] == "running"
    assert feed_variables["app_task_batch_results"]["_meta"]["completed_tasks"] == ["profiles"]


@pytest.mark.asyncio
async def test_conveyor_executes_dependency_graph_and_injects_upstream_outputs() -> None:
    config = parse_task_batches_config(
        {
            "version": 1,
            "conveyors": [
                {
                    "id": "workflow_generation",
                    "decomposition_agent": "DecompositionAgent",
                    "execution_agents": ["AgentRosterAgent", "TransitionGraphAgent"],
                    "concurrency": 2,
                }
            ],
        }
    )

    seen_variables: dict[str, dict] = {}

    class _FakeAgent:
        def __init__(self, name: str) -> None:
            self.name = name

        async def ask(self, message, **kwargs):
            variables = _ag2_task_context(kwargs)
            task_id = variables["current_task_id"]
            seen_variables[task_id] = variables
            if self.name == "AgentRosterAgent":
                return SimpleNamespace(
                    body=json.dumps(
                        {
                            "task_id": task_id,
                            "agents": ["IntakeAgent", "ReviewAgent"],
                            "summary": "Agent roster complete.",
                        }
                    )
                )
            dependency_outputs = variables["dependency_task_outputs"]
            roster = dependency_outputs["agent_roster"]["agents"]
            return SimpleNamespace(
                body=json.dumps(
                    {
                        "task_id": task_id,
                        "transition_sources": roster,
                        "summary": "Transition graph used upstream roster.",
                    }
                )
            )

    context = {}
    structured_output = {
        "DecompositionPlan": {
            "tasks": [
                {
                    "task_id": "agent_roster",
                    "execution_agent": "AgentRosterAgent",
                    "task_prompt": "Create the agent roster.",
                    "depends_on": [],
                },
                {
                    "task_id": "transition_graph",
                    "execution_agent": "TransitionGraphAgent",
                    "task_prompt": "Create transitions from the agent roster.",
                    "depends_on": ["agent_roster"],
                },
            ]
        }
    }

    await execute_task_batches_for_trigger(
        workflow_name="AgentGenerator",
        trigger_agent="DecompositionAgent",
        batches_config=config,
        agents={
            "AgentRosterAgent": _FakeAgent("AgentRosterAgent"),
            "TransitionGraphAgent": _FakeAgent("TransitionGraphAgent"),
        },
        context_variables=context,
        structured_output=structured_output,
        fresh_agents_per_task=False,
    )

    assert context["workflow_generation_status"] == "completed"
    assert context["workflow_generation_results"]["transition_graph"]["transition_sources"] == [
        "IntakeAgent",
        "ReviewAgent",
    ]
    transition_context = seen_variables["transition_graph"]
    assert transition_context["decomposition_plan"]["tasks"][0]["task_id"] == "agent_roster"
    assert transition_context["dependency_task_outputs"] == {
        "agent_roster": context["workflow_generation_results"]["agent_roster"]
    }
    assert transition_context["completed_task_outputs"]["agent_roster"]["summary"] == (
        "Agent roster complete."
    )
    assert transition_context["workflow_generation_results"]["_meta"]["completed_tasks"] == [
        "agent_roster"
    ]


@pytest.mark.asyncio
async def test_conveyor_rejects_task_for_agent_outside_allowed_execution_agents() -> None:
    config = parse_task_batches_config(
        {
            "version": 1,
            "conveyors": [
                {
                    "id": "workflow_generation",
                    "decomposition_agent": "DecompositionAgent",
                    "execution_agents": ["AgentRosterAgent"],
                }
            ],
        }
    )

    with pytest.raises(RuntimeError, match="outside allowed conveyor agents"):
        await execute_task_batches_for_trigger(
            workflow_name="AgentGenerator",
            trigger_agent="DecompositionAgent",
            batches_config=config,
            agents={"ToolsAgent": object()},
            context_variables={},
            structured_output={
                "DecompositionPlan": {
                    "tasks": [
                        {
                            "task_id": "tools",
                            "execution_agent": "ToolsAgent",
                            "task_prompt": "Create tools.",
                        }
                    ]
                }
            },
            fresh_agents_per_task=False,
        )


@pytest.mark.asyncio
async def test_execute_task_batches_rejects_overlapping_owned_paths() -> None:
    config = parse_task_batches_config(
        {
            "version": 1,
            "batches": [
                {
                    "id": "app_build_tasks",
                    "trigger_agent": "AppPlanAgent",
                    "source": {
                        "kind": "context_variable",
                        "path": "app_task_batch_items",
                        "task_model": "AppBuildTask",
                    },
                    "result": {
                        "context_key": "app_task_batch_results",
                        "status_key": "app_task_batch_status",
                        "require_owned_paths": True,
                    },
                }
            ],
        }
    )

    context = {
        "app_task_batch_items": [
            {
                "task_id": "profiles",
                "initial_agent": "WorkerAgent",
                "initial_message": "Build profiles.",
                "owned_paths": ["modules/shared/module.yaml"],
            },
            {
                "task_id": "feed",
                "initial_agent": "WorkerAgent",
                "initial_message": "Build feed.",
                "owned_paths": ["modules/shared/module.yaml"],
            },
        ],
    }

    with pytest.raises(ValueError, match="owned_paths declared by multiple tasks"):
        await execute_task_batches_for_trigger(
            workflow_name="AppGenerator",
            trigger_agent="AppPlanAgent",
            batches_config=config,
            agents={"WorkerAgent": object()},
            context_variables=context,
            fresh_agents_per_task=False,
        )


@pytest.mark.asyncio
async def test_execute_task_batches_materializes_typed_worker_files_before_validation() -> None:
    config = parse_task_batches_config(
        {
            "version": 1,
            "batches": [
                {
                    "id": "app_build_tasks",
                    "trigger_agent": "AppPlanAgent",
                    "source": {
                        "kind": "context_variable",
                        "path": "app_task_batch_items",
                        "task_model": "AppBuildTask",
                    },
                    "result": {
                        "context_key": "app_task_batch_results",
                        "status_key": "app_task_batch_status",
                        "require_owned_paths": True,
                    },
                }
            ],
        }
    )

    class _FakeAgent:
        async def ask(self, message, **kwargs):
            return SimpleNamespace(
                body=json.dumps(
                    {
                        "DatabaseOutput": {
                            "database_files": [
                                {
                                    "path": "data/contract.json",
                                    "kind": "data_contract_json",
                                    "purpose": "Data contract artifact.",
                                    "entity_refs": ["ticket"],
                                    "content": "{\"surfaces\":[]}\n",
                                }
                            ],
                            "pending_schema_migration": None,
                            "agent_message": "Staged data contract.",
                        }
                    }
                )
            )

    context = {
        "app_task_batch_items": [
            {
                "task_id": "data_contract",
                "initial_agent": "WorkerAgent",
                "initial_message": "Build data contract.",
                "owned_paths": ["data/contract.json"],
            },
        ],
    }

    await execute_task_batches_for_trigger(
        workflow_name="AppGenerator",
        trigger_agent="AppPlanAgent",
        batches_config=config,
        agents={"WorkerAgent": _FakeAgent()},
        context_variables=context,
        fresh_agents_per_task=False,
    )

    output = context["app_task_batch_results"]["data_contract"]
    assert output["code_files"] == [
        {"filename": "data/contract.json", "content": "{\"surfaces\":[]}\n"}
    ]


@pytest.mark.asyncio
async def test_page_bundle_task_normalizes_owned_pages_from_app_plan() -> None:
    config = parse_task_batches_config(
        {
            "version": 1,
            "batches": [
                {
                    "id": "app_build_tasks",
                    "trigger_agent": "AppPlanAgent",
                    "source": {
                        "kind": "context_variable",
                        "path": "app_task_batch_items",
                        "task_model": "AppBuildTask",
                    },
                    "result": {
                        "context_key": "app_task_batch_results",
                        "status_key": "app_task_batch_status",
                        "require_owned_paths": True,
                    },
                }
            ],
        }
    )

    class _FakeAgent:
        async def ask(self, message, **kwargs):
            return SimpleNamespace(
                body=json.dumps(
                    {
                        "manifest": {
                            "app_name": "Support",
                            "default_route": "/tickets",
                            "auth_strategy": "role-based",
                        },
                        "pages": [
                            {
                                "name": "Tickets",
                                "route": "/tickets",
                                "sections": [
                                    {
                                        "id": "bad-table",
                                        "primitive": "DataTable",
                                        "config": {"api_endpoint": "/api/tickets"},
                                    }
                                ],
                            }
                        ],
                        "agent_message": "Generated pages.",
                    }
                )
            )

    context = {
        "app_build_plan": {
            "pages": [
                {
                    "name": "Tickets",
                    "route": "/tickets",
                    "purpose": "Review tickets.",
                    "sections_hint": [
                        {
                            "primitive": "ResourceTable",
                            "section_id_hint": "tickets-table",
                            "config_hint": "{\"api_endpoint\": \"/api/modules/tickets/list_tickets\"}",
                        }
                    ],
                },
                {
                    "name": "Settings",
                    "route": "/settings",
                    "purpose": "Configure queue defaults.",
                    "sections_hint": [
                        {
                            "primitive": "PageHeader",
                            "section_id_hint": "settings-header",
                            "config_hint": "{\"title\": \"Settings\"}",
                        }
                    ],
                },
            ]
        },
        "app_task_batch_items": [
            {
                "task_id": "pages",
                "task_type": "page_bundle",
                "initial_agent": "WorkerAgent",
                "initial_message": "Build pages.",
                "owned_paths": ["app.json", "ui/pages/tickets.yaml", "ui/pages/settings.yaml"],
            },
        ],
    }

    await execute_task_batches_for_trigger(
        workflow_name="AppGenerator",
        trigger_agent="AppPlanAgent",
        batches_config=config,
        agents={"WorkerAgent": _FakeAgent()},
        context_variables=context,
        fresh_agents_per_task=False,
    )

    code_files = context["app_task_batch_results"]["pages"]["code_files"]
    output = context["app_task_batch_results"]["pages"]
    file_map = {entry["filename"]: entry["content"] for entry in code_files}
    assert set(file_map) >= {"ui/pages/tickets.yaml", "ui/pages/settings.yaml"}
    assert output["_page_materialization_source"] == "app_build_plan.pages"
    assert output["_page_materialized_paths"] == [
        "ui/pages/tickets.yaml",
        "ui/pages/settings.yaml",
    ]
    tickets_page = yaml.safe_load(file_map["ui/pages/tickets.yaml"])
    assert tickets_page["sections"][0]["primitive"] == "ResourceTable"
    assert tickets_page["sections"][0]["config"]["api_endpoint"] == "/api/modules/tickets/list_tickets"
    assert yaml.safe_load(file_map["ui/pages/settings.yaml"])["route"] == "/settings"


@pytest.mark.asyncio
async def test_module_contract_task_allows_optional_contract_family_outputs() -> None:
    config = parse_task_batches_config(
        {
            "version": 1,
            "batches": [
                {
                    "id": "app_build_tasks",
                    "trigger_agent": "AppPlanAgent",
                    "source": {
                        "kind": "context_variable",
                        "path": "app_task_batch_items",
                        "task_model": "AppBuildTask",
                    },
                    "result": {
                        "context_key": "app_task_batch_results",
                        "status_key": "app_task_batch_status",
                        "require_owned_paths": True,
                    },
                }
            ],
        }
    )

    class _FakeAgent:
        async def ask(self, message, **kwargs):
            return SimpleNamespace(
                body=json.dumps(
                    {
                        "code_files": [
                            {
                                "filename": "modules/tickets/module.yaml",
                                "content": "id: tickets\n",
                            },
                            {
                                "filename": "modules/tickets/contracts/events.yaml",
                                "content": "events: []\n",
                            },
                            {
                                "filename": "modules/tickets/contracts/reactions.yaml",
                                "content": "reactions: []\n",
                            },
                        ]
                    }
                )
            )

    context = {
        "app_task_batch_items": [
            {
                "task_id": "tickets_contract",
                "task_type": "module_contract",
                "capability_pack_id": "tickets",
                "initial_agent": "WorkerAgent",
                "initial_message": "Build tickets contract.",
                "owned_paths": [
                    "modules/tickets/module.yaml",
                    "modules/tickets/contracts/events.yaml",
                    "modules/tickets/contracts/notifications.yaml",
                ],
            },
        ],
    }

    await execute_task_batches_for_trigger(
        workflow_name="AppGenerator",
        trigger_agent="AppPlanAgent",
        batches_config=config,
        agents={"WorkerAgent": _FakeAgent()},
        context_variables=context,
        fresh_agents_per_task=False,
    )

    assert context["app_task_batch_status"] == "completed"


@pytest.mark.asyncio
async def test_execute_task_batches_rejects_worker_output_outside_owned_paths() -> None:
    config = parse_task_batches_config(
        {
            "version": 1,
            "batches": [
                {
                    "id": "app_build_tasks",
                    "trigger_agent": "AppPlanAgent",
                    "source": {
                        "kind": "context_variable",
                        "path": "app_task_batch_items",
                        "task_model": "AppBuildTask",
                    },
                    "execution": {"retry_limit": 0},
                    "result": {
                        "context_key": "app_task_batch_results",
                        "status_key": "app_task_batch_status",
                        "require_owned_paths": True,
                    },
                }
            ],
        }
    )

    class _FakeAgent:
        async def ask(self, message, **kwargs):
            return SimpleNamespace(
                body=json.dumps(
                    {
                        "agent_message": "done",
                        "code_files": [
                            {
                                "filename": "modules/feed/module.yaml",
                                "content": "name: feed",
                            }
                        ],
                    }
                )
            )

    context = {
        "app_task_batch_items": [
            {
                "task_id": "profiles",
                "initial_agent": "WorkerAgent",
                "initial_message": "Build profiles.",
                "owned_paths": ["modules/profiles/module.yaml"],
            },
        ],
    }

    with pytest.raises(RuntimeError, match="outside owned_paths"):
        await execute_task_batches_for_trigger(
            workflow_name="AppGenerator",
            trigger_agent="AppPlanAgent",
            batches_config=config,
            agents={"WorkerAgent": _FakeAgent()},
            context_variables=context,
            fresh_agents_per_task=False,
        )
