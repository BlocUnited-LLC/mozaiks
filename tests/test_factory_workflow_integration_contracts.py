from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from mozaiksai.core.workflow.execution.middleware import _resolve_import
from mozaiksai.core.workflow.execution.network_graph import compile_transition_rules_to_graph
from mozaiksai.core.workflow.workflow_manager import UnifiedWorkflowManager

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS_ROOT = REPO_ROOT / "factory_app" / "workflows"
SKIP_WORKFLOW_DIRS = {"_shared", "extended_orchestration", "__pycache__"}
REQUIRED_WORKFLOW_FILES = {
    "orchestrator.yaml",
    "agents.yaml",
    "transition_graph.yaml",
    "context_variables.yaml",
    "structured_outputs.yaml",
    "tools.yaml",
    "ui_config.yaml",
}
SPECIAL_TARGETS = {"user", "terminate"}


def _workflow_dirs() -> list[Path]:
    return [
        path
        for path in sorted(WORKFLOWS_ROOT.iterdir())
        if path.is_dir() and path.name not in SKIP_WORKFLOW_DIRS
    ]


def _read_yaml(path: Path) -> dict[str, Any]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8-sig")) or {}
    assert isinstance(payload, dict), f"{path} must have an object root"
    return payload


def _agent_names(workflow_dir: Path) -> set[str]:
    agents = _read_yaml(workflow_dir / "agents.yaml").get("agents") or []
    return {
        str(agent.get("name")).strip()
        for agent in agents
        if isinstance(agent, dict) and str(agent.get("name") or "").strip()
    }


def test_factory_workflows_are_complete_loadable_and_cross_referenced() -> None:
    UnifiedWorkflowManager._instance = None
    manager = UnifiedWorkflowManager(workflows_base_path=str(WORKFLOWS_ROOT))
    failures: list[str] = []

    for workflow_dir in _workflow_dirs():
        missing = sorted(
            filename
            for filename in REQUIRED_WORKFLOW_FILES
            if not (workflow_dir / filename).exists()
        )
        if missing:
            failures.append(f"{workflow_dir.name}: missing required files {missing}")
            continue

        validation = manager.validate_workflow(workflow_dir.name)
        if not validation.get("valid"):
            failures.append(f"{workflow_dir.name}: manager validation failed {validation.get('errors')}")

        agents = _agent_names(workflow_dir)
        if not agents:
            failures.append(f"{workflow_dir.name}: agents.yaml declares no agents")

        orchestrator = _read_yaml(workflow_dir / "orchestrator.yaml")
        initial_agent = str(orchestrator.get("initial_agent") or "").strip()
        if initial_agent and initial_agent not in agents and initial_agent not in SPECIAL_TARGETS:
            failures.append(
                f"{workflow_dir.name}: initial_agent {initial_agent!r} is not declared"
            )

        context = _read_yaml(workflow_dir / "context_variables.yaml")
        definitions = context.get("definitions") or {}
        agent_context = context.get("agents") or {}
        for agent_name, block in agent_context.items():
            if agent_name not in agents:
                failures.append(
                    f"{workflow_dir.name}: context_variables.yaml references unknown agent {agent_name!r}"
                )
            variables = block.get("variables") if isinstance(block, dict) else []
            for variable in variables or []:
                if variable not in definitions:
                    failures.append(
                        f"{workflow_dir.name}: context variable {variable!r} for {agent_name!r} is undefined"
                    )

        structured_outputs = _read_yaml(workflow_dir / "structured_outputs.yaml")
        for agent_name in (structured_outputs.get("registry") or {}):
            if agent_name not in agents:
                failures.append(
                    f"{workflow_dir.name}: structured_outputs registry references unknown agent {agent_name!r}"
                )

        ui_config = _read_yaml(workflow_dir / "ui_config.yaml")
        for agent_name in ui_config.get("visual_agents") or []:
            if agent_name not in agents and agent_name != "user":
                failures.append(
                    f"{workflow_dir.name}: ui_config visual agent {agent_name!r} is not declared"
                )

        transition_graph = _read_yaml(workflow_dir / "transition_graph.yaml")
        transition_rules = transition_graph.get("transition_rules") or []
        for index, rule in enumerate(transition_rules):
            source = str(rule.get("source_agent") or "").strip()
            target = str(rule.get("target_agent") or "").strip()
            if source and source not in agents and source not in SPECIAL_TARGETS:
                failures.append(
                    f"{workflow_dir.name}: transition {index} source_agent {source!r} is not declared"
                )
            if target and target not in agents and target not in SPECIAL_TARGETS:
                failures.append(
                    f"{workflow_dir.name}: transition {index} target_agent {target!r} is not declared"
                )
        try:
            compile_transition_rules_to_graph(
                transition_rules,
                initial_agent_name=initial_agent or sorted(agents)[0],
                agent_id_by_name={name: name for name in agents},
            )
        except Exception as exc:
            failures.append(f"{workflow_dir.name}: transition_graph.yaml does not compile: {exc}")

        middleware_path = workflow_dir / "middleware.yaml"
        if middleware_path.exists():
            middleware = _read_yaml(middleware_path)
            for index, entry in enumerate(middleware.get("prompt_middleware") or []):
                agent_name = str(entry.get("agent") or "").strip()
                if agent_name and agent_name != "all" and agent_name not in agents:
                    failures.append(
                        f"{workflow_dir.name}: middleware {index} references unknown agent {agent_name!r}"
                    )
                function_name = str(entry.get("function") or "").strip()
                if not function_name:
                    failures.append(f"{workflow_dir.name}: middleware {index} is missing function")
                    continue
                fn, label = _resolve_import(
                    workflow_dir.name,
                    entry.get("filename"),
                    function_name,
                    workflow_dir,
                )
                if fn is None:
                    failures.append(f"{workflow_dir.name}: middleware {index} unresolved {label}")

    assert not failures, "\n".join(failures)


def test_factory_workflow_sequences_reference_existing_workflows_and_transitions() -> None:
    registry = json.loads(
        (WORKFLOWS_ROOT / "extended_orchestration" / "extension_registry.json").read_text(
            encoding="utf-8"
        )
    )
    workflows = {
        item["id"]
        for item in registry.get("workflows", [])
        if isinstance(item, dict) and item.get("id")
    }
    workflow_dirs = {path.name for path in _workflow_dirs()}
    transitions = {
        item["id"]
        for item in registry.get("transitions", [])
        if isinstance(item, dict) and item.get("id")
    }
    sequences = {
        item["id"]
        for item in registry.get("workflow_sequences", [])
        if isinstance(item, dict) and item.get("id")
    }
    failures: list[str] = []

    for workflow_id in workflows:
        if workflow_id not in workflow_dirs:
            failures.append(f"registry workflow {workflow_id!r} has no workflow directory")

    for sequence in registry.get("workflow_sequences", []) or []:
        sequence_id = sequence.get("id")
        for index, step in enumerate(sequence.get("steps") or []):
            for workflow_id in step.get("workflows") or []:
                if workflow_id not in workflows:
                    failures.append(
                        f"sequence {sequence_id!r} step {index} references unknown workflow {workflow_id!r}"
                    )
            transition_id = step.get("transition")
            if transition_id and transition_id not in transitions:
                failures.append(
                    f"sequence {sequence_id!r} step {index} references unknown transition {transition_id!r}"
                )

    for transition in registry.get("transitions", []) or []:
        transition_id = transition.get("id")
        for option in transition.get("options") or []:
            route_to = option.get("route_to")
            if route_to and route_to not in workflows and route_to not in transitions and route_to != "workflow_complete":
                failures.append(
                    f"transition {transition_id!r} option {option.get('id')!r} routes to unknown target {route_to!r}"
                )
            sequence_id = option.get("sequence")
            if sequence_id and sequence_id not in sequences:
                failures.append(
                    f"transition {transition_id!r} option {option.get('id')!r} references unknown sequence {sequence_id!r}"
                )

    assert not failures, "\n".join(failures)
