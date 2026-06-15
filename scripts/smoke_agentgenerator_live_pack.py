from __future__ import annotations

import argparse
import asyncio
import io
import json
import os
import re
import sys
import time
import uuid
from contextlib import contextmanager, redirect_stdout
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path, PurePosixPath
from typing import Any

import yaml
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from factory_app.workflows._shared.workflow_integration import workflow_name_to_capability_id
from factory_app.workflows.AgentGenerator.tools.workflow_quality_gate import (
    REQUIRED_WORKFLOW_FILES,
    validate_agentgenerator_semantic_drift,
)

DEFAULT_WORKFLOW = "AgentGenerator"
DEFAULT_WORKFLOWS_ROOT = REPO_ROOT / "factory_app" / "workflows"
DEFAULT_PACK_NAME = "Support Operations Automation"
SUPPORTED_TRANSITION_CONDITIONS = {"context_equals", "context_expression", "tool_called"}


def _configure_event_loop_policy() -> None:
    if os.name != "nt":
        return
    selector_policy = getattr(asyncio, "WindowsSelectorEventLoopPolicy", None)
    if selector_policy is not None:
        asyncio.set_event_loop_policy(selector_policy())


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    return str(value)


def _require_live_env() -> None:
    if not str(os.getenv("OPENAI_API_KEY") or "").strip():
        raise RuntimeError("Missing OPENAI_API_KEY. This script loads .env before checking.")


def _pascal_to_snake(value: str) -> str:
    text = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", str(value or "").strip())
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", text)
    text = re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_").lower()
    return text or "workflow_task"


def _safe_generated_relpath(raw_path: Any) -> str | None:
    text = str(raw_path or "").replace("\\", "/").strip().lstrip("/")
    if not text or "\x00" in text:
        return None
    parsed = PurePosixPath(text)
    if parsed.is_absolute():
        return None
    if any(part in {"", ".", "..", "_shared"} for part in parsed.parts):
        return None
    return "/".join(parsed.parts)


def _initialize_workflow_root(workflows_root: Path, workflow_name: str) -> dict[str, Any]:
    from mozaiksai.core.workflow.workflow_manager import get_workflow_manager, initialize_workflows

    effective_root = workflows_root.resolve()
    os.environ["MOZAIKS_WORKFLOWS_PATH"] = str(effective_root)
    os.environ["WORKFLOW_DIR"] = str(effective_root)

    initialize_workflows(base_path=str(effective_root))
    manager = get_workflow_manager()
    info = manager.get_workflow_info(workflow_name) or {}
    if info.get("status") != "loaded":
        raise RuntimeError(f"Workflow failed to load: {workflow_name} -> {info.get('error')}")
    config = manager.get_config(workflow_name)
    if not config:
        raise RuntimeError(f"Workflow loaded without config: {workflow_name}")
    return config


def _workflow_generation_prompt(
    *,
    workflow_name: str,
    role: str,
    description: str,
    pattern_id: int,
    pattern_name: str,
    startup_mode: str,
    human_in_the_loop: bool,
    trigger: str | None = None,
    require_task_batches: bool = False,
    task_batch_id: str | None = None,
    required_agent_names: list[str] | None = None,
    transition_hint: str | None = None,
) -> str:
    root_files = ", ".join(sorted(REQUIRED_WORKFLOW_FILES))
    conveyor_id = str(task_batch_id or f"{_pascal_to_snake(workflow_name)}_tasks").strip()
    capability_id = workflow_name_to_capability_id(workflow_name)
    agent_names = [str(name).strip() for name in required_agent_names or [] if str(name).strip()]
    agent_roster_clause = (
        "Declare these agents exactly in agents.yaml: "
        f"{', '.join(agent_names)}. Every transition_graph.yaml source_agent "
        "and every target_agent except user/terminate must be one of those declared names."
        if agent_names
        else "Every transition_graph.yaml source_agent and every target_agent except user/terminate must match a declared agents.yaml name."
    )
    transition_clause = (
        f"Transition topology requirement: {transition_hint}"
        if transition_hint
        else "Derive the transition topology from the assigned AG2 Network pattern."
    )
    task_batch_clause = (
        "This workflow must decompose heavy intent into downstream task work. "
        "Emit extended_orchestration/task_batches.yaml using the canonical version: 1 "
        "conveyors[] shape, with a decomposition_agent, execution_agents[], and "
        "DecompositionPlan.tasks[] in structured_outputs.yaml. Include at least two "
        "distinct declared execution agents that can be selected by tasks; do not "
        "repeat the same worker name in execution_agents[]. Give each execution "
        "agent a separate specialist role for the downstream parallel work. "
        f"Use conveyor id exactly `{conveyor_id}`. Because that id materializes "
        "runtime state, context_variables.yaml must declare "
        f"`{conveyor_id}_results` with type array and source.state default [] plus "
        f"`{conveyor_id}_status` with type object and source.state default {{}}. "
        "Expose those two variables to any reviewer or synthesis agent that reads "
        "completed downstream work."
        if require_task_batches
        else "Do not emit extended_orchestration/task_batches.yaml unless the pattern requires it."
    )
    trigger_clause = (
        "Declare this exact orchestrator event trigger and keep this workflow backend-only: "
        f"`type: event`, `event: {trigger}`, `capability_id: {capability_id}`, "
        "and a domain-specific `description` that preserves the business meaning "
        "from the workflow description. Never omit or null capability_id. Never "
        "emit a generic description like `Trigger for ... event`."
        if trigger
        else "Use a chat or route trigger only if appropriate for the startup mode."
    )
    return (
        f"Generate a minimal but runnable Mozaiks workflow bundle for {workflow_name}.\n"
        f"Role: {role}. Description: {description}\n"
        f"Use AG2 Network pattern {pattern_id}: {pattern_name}.\n"
        f"Set orchestrator.yaml workflow_startup_mode to {startup_mode}; never emit startup_mode.\n"
        f"Set human_in_the_loop to {str(human_in_the_loop).lower()}.\n"
        f"Required root YAML files: {root_files}.\n"
        f"{agent_roster_clause}\n"
        f"{transition_clause}\n"
        "transition_graph.yaml must use transition_rules with transition_type=after_turn "
        "or condition_type in context_equals, context_expression, tool_called. Never emit a condition field.\n"
        "middleware.yaml must declare prompt middleware or an empty prompt_middleware list.\n"
        "ui_config.yaml is required even for BackendOnly/headless workflows; emit "
        "`visual_agents: []` for BackendOnly workflows and never omit the file.\n"
        "Do not emit handoffs.yaml, hooks.yaml, app module files, backend/models.py, ctx.db, "
        "runtime infrastructure, or _shared imports.\n"
        f"{trigger_clause}\n"
        f"{task_batch_clause}\n"
        "Keep the smoke bundle concise: small agent roster, small structured models, "
        "no external integrations, and no custom UI unless the assigned pattern truly requires it.\n"
        "Keep tool stubs workflow-local under tools/. Raise NotImplementedError for unimplemented stubs.\n"
        "Return only WorkflowBundleBuilderOutput JSON."
    )


def build_seeded_pack_context(*, pack_name: str = DEFAULT_PACK_NAME) -> dict[str, Any]:
    """Build the approved AgentGenerator context used by the live smoke.

    The context starts at AgentGenerator's approved-generation boundary. It avoids
    the interview and review UI path while exercising the same AG2 task batch used
    after a user approves the generated pack plan.
    """

    workflows = [
        {
            "name": "SupportTicketRoutingWorkflow",
            "role": "primary",
            "description": "Classify support tickets and route them to the right handling lane.",
            "pattern_id": 6,
            "pattern_name": "Pipeline",
            "startup_mode": "AgentDriven",
            "human_in_the_loop": False,
            "depends_on": [],
            "require_task_batches": False,
            "required_agent_names": ["ClassifierAgent", "RoutingAgent"],
            "transition_hint": "ClassifierAgent -> RoutingAgent -> terminate.",
        },
        {
            "name": "TicketBatchTriageWorkflow",
            "role": "supporting",
            "description": "Decompose large ticket queues into parallel triage work units.",
            "pattern_id": 9,
            "pattern_name": "Triage with Tasks",
            "startup_mode": "BackendOnly",
            "human_in_the_loop": False,
            "trigger": "domain.support_ticket.batch_requested",
            "depends_on": [],
            "require_task_batches": True,
            "task_batch_id": "ticket_batch_triage_tasks",
        },
    ]

    workflow_specs: list[dict[str, Any]] = []
    for item in workflows:
        spec = {
            "task_id": _pascal_to_snake(item["name"]),
            "name": item["name"],
            "role": item["role"],
            "description": item["description"],
            "pattern_id": item["pattern_id"],
            "pattern_name": item["pattern_name"],
            "initial_agent": "WorkflowBundleBuilderAgent",
            "initial_message": _workflow_generation_prompt(
                workflow_name=item["name"],
                role=item["role"],
                description=item["description"],
                pattern_id=int(item["pattern_id"]),
                pattern_name=item["pattern_name"],
                startup_mode=item["startup_mode"],
                human_in_the_loop=bool(item["human_in_the_loop"]),
                trigger=item.get("trigger"),
                require_task_batches=bool(item.get("require_task_batches")),
                task_batch_id=item.get("task_batch_id"),
                required_agent_names=item.get("required_agent_names"),
                transition_hint=item.get("transition_hint"),
            ),
            "depends_on": list(item.get("depends_on") or []),
            "context_variables": {
                "expected_workflow_startup_mode": item["startup_mode"],
                "expected_human_in_the_loop": bool(item["human_in_the_loop"]),
                "expected_event_trigger": item.get("trigger"),
                "expected_workflow_capability_id": workflow_name_to_capability_id(item["name"]),
                "require_task_batches": bool(item.get("require_task_batches")),
                "expected_task_batch_id": item.get("task_batch_id"),
            },
        }
        workflow_specs.append(spec)

    return {
        "build_mode": "initial",
        "task_run_mode": False,
        "workflow_review_approved": True,
        "concept_overview": (
            "Support Operations Automation is an internal support backbone that "
            "classifies support tickets and triages heavy ticket queues through "
            "decomposed downstream agent work."
        ),
        "backend_design_document": (
            "Modules publish domain.support_ticket.created and "
            "domain.support_ticket.batch_requested after state commits. Workflows "
            "react to those events and may emit workflow.* events, but do not own "
            "domain persistence."
        ),
        "experience_spec_document": (
            "Primary review flows may surface approval and feedback UI. BackendOnly "
            "event workflows run headlessly and must not expose user-facing visual agents."
        ),
        "design_surface_map": {
            "surfaces": [
                {
                    "surface_kind": "workflow",
                    "id": "support_ticket_routing",
                    "workflow_name": "SupportTicketRoutingWorkflow",
                },
                {
                    "surface_kind": "workflow",
                    "id": "ticket_batch_triage",
                    "workflow_name": "TicketBatchTriageWorkflow",
                },
            ]
        },
        "is_multi_workflow": True,
        "pack_name": pack_name,
        "pack_partition_reason": (
            "The pack separates ticket routing from heavy queue decomposition because "
            "each workflow has a distinct runtime boundary."
        ),
        "PatternSelection": {
            "is_multi_workflow": True,
            "pack_partition_reason": (
                "The pack separates ticket routing from heavy queue decomposition because "
                "each workflow has a distinct runtime boundary."
            ),
            "pack_name": pack_name,
            "workflows": workflow_specs,
        },
        "workflows_spec": workflow_specs,
    }


@dataclass
class TaskRunTrace:
    records: list[dict[str, Any]] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        return {
            "task_count": len(self.records),
            "max_overlap": _max_overlapping_task_runs(self.records),
            "tasks": [
                {
                    "task_id": record.get("task_id"),
                    "status": record.get("status"),
                    "duration_seconds": record.get("duration_seconds"),
                }
                for record in self.records
            ],
        }


def _max_overlapping_task_runs(records: list[dict[str, Any]]) -> int:
    events: list[tuple[float, int]] = []
    for record in records:
        start = record.get("started_perf")
        end = record.get("ended_perf")
        if not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
            continue
        events.append((float(start), 1))
        events.append((float(end), -1))
    active = 0
    max_active = 0
    for _, delta in sorted(events, key=lambda item: (item[0], -item[1])):
        active += delta
        max_active = max(max_active, active)
    return max_active


@contextmanager
def _trace_task_batch_runner(trace: TaskRunTrace):
    from mozaiksai.core.adapters import ag2_task_batch_runner as runner_module

    original_run = runner_module.AG2TaskBatchRunner.run

    async def _run_with_trace(self: Any, request: Any) -> Any:
        started_perf = time.perf_counter()
        started_at = datetime.now(UTC).isoformat()
        result = None
        error = None
        try:
            result = await original_run(self, request)
            return result
        except Exception as exc:
            error = str(exc)
            raise
        finally:
            ended_perf = time.perf_counter()
            status = getattr(getattr(result, "status", None), "value", None) if result is not None else None
            trace.records.append(
                {
                    "task_id": str(getattr(request, "task_id", "") or ""),
                    "agent_name": str(getattr(request, "agent_name", "") or ""),
                    "started_at": started_at,
                    "ended_at": datetime.now(UTC).isoformat(),
                    "started_perf": started_perf,
                    "ended_perf": ended_perf,
                    "duration_seconds": round(ended_perf - started_perf, 3),
                    "status": status or ("exception" if error else "unknown"),
                    "error": error,
                }
            )

    runner_module.AG2TaskBatchRunner.run = _run_with_trace
    try:
        yield
    finally:
        runner_module.AG2TaskBatchRunner.run = original_run


def _yaml_load(path: Path) -> Any:
    with path.open(encoding="utf-8-sig") as handle:
        return yaml.safe_load(handle) or {}


def _read_agent_names(agents_payload: Any) -> list[str]:
    agents = agents_payload.get("agents") if isinstance(agents_payload, dict) else agents_payload
    if isinstance(agents, dict):
        return [str(name) for name in agents if str(name or "").strip()]
    if isinstance(agents, list):
        return [
            str(agent.get("name"))
            for agent in agents
            if isinstance(agent, dict) and str(agent.get("name") or "").strip()
        ]
    return []


def validate_generated_workflow_bundle(
    *,
    bundle_root: Path,
    expected_workflows: list[dict[str, Any]],
) -> dict[str, Any]:
    from mozaiksai.core.workflow.execution.network_graph import compile_transition_rules_to_graph
    from mozaiksai.core.workflow.task_batches import parse_task_batches_config

    errors: list[str] = []
    workflow_reports: list[dict[str, Any]] = []
    expected_by_name = {str(item.get("name") or ""): item for item in expected_workflows}

    if not bundle_root.exists():
        errors.append(f"bundle root does not exist: {bundle_root}")
        return {"valid": False, "errors": errors, "workflows": workflow_reports}

    for workflow_name, spec in expected_by_name.items():
        workflow_dir = bundle_root / workflow_name
        report: dict[str, Any] = {"workflow_name": workflow_name, "path": str(workflow_dir), "errors": []}
        workflow_reports.append(report)
        if not workflow_dir.is_dir():
            report["errors"].append("workflow directory missing")
            continue

        emitted_files = {
            str(path.relative_to(workflow_dir)).replace("\\", "/")
            for path in workflow_dir.rglob("*")
            if path.is_file()
        }
        invalid_paths = sorted(path for path in emitted_files if _safe_generated_relpath(path) is None)
        if invalid_paths:
            report["errors"].append(f"unsafe generated paths: {invalid_paths}")

        missing = sorted(REQUIRED_WORKFLOW_FILES.difference(emitted_files))
        if missing:
            report["errors"].append(f"missing required workflow files: {missing}")

        stale_files = sorted({"handoffs.yaml", "hooks.yaml"}.intersection(emitted_files))
        if stale_files:
            report["errors"].append(f"stale workflow files emitted: {stale_files}")

        yaml_payloads: dict[str, Any] = {}
        for relpath in sorted(path for path in emitted_files if path.endswith(".yaml")):
            try:
                yaml_payloads[relpath] = _yaml_load(workflow_dir / relpath)
            except Exception as exc:
                report["errors"].append(f"{relpath} is not valid YAML: {exc}")

        orchestrator = yaml_payloads.get("orchestrator.yaml")
        if isinstance(orchestrator, dict):
            if orchestrator.get("workflow_name") != workflow_name:
                report["errors"].append(
                    f"orchestrator.workflow_name={orchestrator.get('workflow_name')!r} does not match {workflow_name!r}"
                )
            if "startup_mode" in orchestrator:
                report["errors"].append("orchestrator.yaml must not use startup_mode")
            if "visual_agents" in orchestrator:
                report["errors"].append("orchestrator.yaml must not contain visual_agents; use ui_config.yaml")
            startup_mode = orchestrator.get("workflow_startup_mode")
            if not startup_mode:
                report["errors"].append("orchestrator.yaml missing workflow_startup_mode")
            expected_mode = (spec.get("context_variables") or {}).get("expected_workflow_startup_mode")
            if expected_mode and startup_mode and startup_mode != expected_mode:
                report["errors"].append(
                    f"workflow_startup_mode={startup_mode!r} does not match expected {expected_mode!r}"
                )
            trigger_keys = {"type", "event", "capability_id", "endpoint", "method", "description"}
            triggers = orchestrator.get("triggers")
            if isinstance(triggers, list):
                for index, trigger in enumerate(triggers):
                    if not isinstance(trigger, dict):
                        report["errors"].append(f"orchestrator trigger {index} must be a mapping")
                        continue
                    extra_keys = sorted(set(trigger).difference(trigger_keys))
                    if extra_keys:
                        report["errors"].append(
                            f"orchestrator trigger {index} uses unsupported keys: {extra_keys}"
                        )
        else:
            report["errors"].append("orchestrator.yaml must contain a mapping")
            startup_mode = None

        transition_graph = yaml_payloads.get("transition_graph.yaml")
        agents_payload = yaml_payloads.get("agents.yaml")
        if isinstance(transition_graph, dict):
            transition_rules = transition_graph.get("transition_rules")
            if not isinstance(transition_rules, list) or not transition_rules:
                report["errors"].append("transition_graph.yaml must define non-empty transition_rules")
            else:
                for index, rule in enumerate(transition_rules):
                    if not isinstance(rule, dict):
                        report["errors"].append(f"transition rule {index} must be a mapping")
                        continue
                    if "condition" in rule:
                        report["errors"].append(f"transition rule {index} uses removed condition field")
                    if str(rule.get("transition_type") or "").strip() == "condition":
                        condition_type = str(rule.get("condition_type") or "").strip()
                        if condition_type not in SUPPORTED_TRANSITION_CONDITIONS:
                            report["errors"].append(
                                f"transition rule {index} uses unsupported condition_type={condition_type!r}"
                            )
                agent_names = _read_agent_names(agents_payload)
                initial_agent = str(orchestrator.get("initial_agent") or "").strip() if isinstance(orchestrator, dict) else ""
                if agent_names and initial_agent:
                    try:
                        compile_transition_rules_to_graph(
                            transition_rules,
                            initial_agent_name=initial_agent,
                            agent_id_by_name={name: name for name in agent_names},
                            max_turns=orchestrator.get("max_turns") if isinstance(orchestrator, dict) else None,
                        )
                    except Exception as exc:
                        report["errors"].append(f"transition graph does not compile through AG2 adapter: {exc}")
        else:
            report["errors"].append("transition_graph.yaml must contain a mapping")

        ui_config = yaml_payloads.get("ui_config.yaml")
        if isinstance(ui_config, dict):
            visual_agents = ui_config.get("visual_agents")
            if startup_mode == "BackendOnly" and visual_agents not in (None, []):
                report["errors"].append("BackendOnly workflow must not expose visual_agents")
        else:
            report["errors"].append("ui_config.yaml must contain a mapping")

        context_variables = yaml_payloads.get("context_variables.yaml")
        context_definitions = (
            set(context_variables.get("definitions") or {})
            if isinstance(context_variables, dict)
            else set()
        )
        if not isinstance(context_variables, dict):
            report["errors"].append("context_variables.yaml must contain a mapping")

        requires_task_batches = bool((spec.get("context_variables") or {}).get("require_task_batches"))
        task_batches_path = workflow_dir / "extended_orchestration" / "task_batches.yaml"
        if requires_task_batches:
            if not task_batches_path.exists():
                report["errors"].append("required extended_orchestration/task_batches.yaml missing")
            else:
                try:
                    parsed = parse_task_batches_config(_yaml_load(task_batches_path))
                    if not parsed.conveyors:
                        report["errors"].append("task_batches.yaml must declare conveyors[]")
                    if not parsed.batches:
                        report["errors"].append("task_batches.yaml did not materialize executable batches")
                    expected_task_batch_id = (spec.get("context_variables") or {}).get("expected_task_batch_id")
                    if expected_task_batch_id:
                        batch_ids = {batch.id for batch in parsed.batches}
                        if expected_task_batch_id not in batch_ids:
                            report["errors"].append(
                                f"task_batches.yaml must declare expected conveyor id {expected_task_batch_id!r}"
                            )
                    for batch in parsed.batches:
                        for key in (batch.result.context_key, batch.result.status_key):
                            if key not in context_definitions:
                                report["errors"].append(
                                    f"task_batches {batch.id!r} writes undeclared context variable {key!r}"
                                )
                except Exception as exc:
                    report["errors"].append(f"task_batches.yaml is invalid: {exc}")

        errors.extend(f"{workflow_name}: {message}" for message in report["errors"])

    return {"valid": not errors, "errors": errors, "workflows": workflow_reports}


def promote_and_load_generated_workflows(
    *,
    bundle_root: Path,
    expected_workflows: list[dict[str, Any]],
    active_root: Path,
) -> dict[str, Any]:
    from factory_app.workflows.AgentGenerator.tools.workflow_converter import (
        promote_generated_workflow,
    )
    from mozaiksai.core.workflow.workflow_manager import UnifiedWorkflowManager

    errors: list[str] = []
    promotions: list[dict[str, Any]] = []
    for spec in expected_workflows:
        workflow_name = str(spec.get("name") or "").strip()
        if not workflow_name:
            continue
        source_dir = bundle_root / workflow_name
        try:
            promotions.append(promote_generated_workflow(source_dir, active_root))
        except Exception as exc:
            errors.append(f"{workflow_name}: promotion failed: {exc}")

    original_instance = UnifiedWorkflowManager._instance
    try:
        UnifiedWorkflowManager._instance = None
        manager = UnifiedWorkflowManager(workflows_base_path=str(active_root))
        loaded: dict[str, Any] = {}
        for spec in expected_workflows:
            workflow_name = str(spec.get("name") or "").strip()
            info = manager.get_workflow_info(workflow_name) or {}
            loaded[workflow_name] = {
                "status": info.get("status"),
                "error": info.get("error"),
            }
            if info.get("status") != "loaded":
                errors.append(f"{workflow_name}: loader status={info.get('status')!r} error={info.get('error')!r}")
    finally:
        UnifiedWorkflowManager._instance = original_instance

    return {
        "valid": not errors,
        "errors": errors,
        "promotions": promotions,
        "active_root": str(active_root),
        "loaded": loaded if "loaded" in locals() else {},
    }


class _Context:
    def __init__(self, initial: dict[str, Any]) -> None:
        self.data = dict(initial)

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self.data[key] = value

    def to_dict(self) -> dict[str, Any]:
        return dict(self.data)


@contextmanager
def _patched_download_tool():
    from factory_app.workflows.AgentGenerator.tools import generate_and_download as download_module

    originals = {
        "use_ui_tool": download_module.use_ui_tool,
        "record_workflow_export": download_module.record_workflow_export,
        "record_workflow_artifacts": download_module.record_workflow_artifacts,
        "_register_workflow_bundle_artifact_version": download_module._register_workflow_bundle_artifact_version,
        "resolve_agent_api_url": download_module.resolve_agent_api_url,
        "resolve_agent_websocket_url": download_module.resolve_agent_websocket_url,
        "_promote_workflow_to_app_workspace": download_module._promote_workflow_to_app_workspace,
    }

    async def _fake_use_ui_tool(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {
            "status": "completed",
            "download_accepted": True,
            "data": {"download_accepted": True},
            "agentContext": {},
        }

    async def _noop_async(*args: Any, **kwargs: Any) -> None:
        return None

    async def _fake_artifact_version(*args: Any, **kwargs: Any) -> Any:
        return type("ArtifactVersion", (), {"id": "live_smoke_artifact"})()

    download_module.use_ui_tool = _fake_use_ui_tool
    download_module.record_workflow_export = _noop_async
    download_module.record_workflow_artifacts = _noop_async
    download_module._register_workflow_bundle_artifact_version = _fake_artifact_version
    download_module.resolve_agent_api_url = lambda app_id: f"https://api.local/{app_id}"
    download_module.resolve_agent_websocket_url = lambda app_id: f"wss://ws.local/{app_id}"
    download_module._promote_workflow_to_app_workspace = lambda *args, **kwargs: None
    try:
        yield download_module
    finally:
        for name, value in originals.items():
            setattr(download_module, name, value)


async def _export_workflow_bundle(
    *,
    context: dict[str, Any],
    generated_root: Path,
) -> dict[str, Any]:
    old_generated_root = os.environ.get("MOZAIKS_GENERATED_ARTIFACTS_PATH")
    os.environ["MOZAIKS_GENERATED_ARTIFACTS_PATH"] = str(generated_root)
    try:
        with _patched_download_tool() as download_module:
            stdout_buffer = io.StringIO()
            with redirect_stdout(stdout_buffer):
                return await download_module.generate_and_download(
                    DownloadRequest={"confirmation_only": False, "storage_backend": "none"},
                    agent_message="Workflow bundle ready.",
                    context_variables=_Context(context),
                )
    finally:
        if old_generated_root is None:
            os.environ.pop("MOZAIKS_GENERATED_ARTIFACTS_PATH", None)
        else:
            os.environ["MOZAIKS_GENERATED_ARTIFACTS_PATH"] = old_generated_root


async def run_live_agentgenerator_pack_smoke(
    *,
    timeout_seconds: float = 600.0,
    workflow_name: str = DEFAULT_WORKFLOW,
    workflows_root: Path = DEFAULT_WORKFLOWS_ROOT,
    generated_root: Path | None = None,
    active_workflows_root: Path | None = None,
    enable_telemetry: bool = False,
) -> dict[str, Any]:
    load_dotenv(REPO_ROOT / ".env")
    _require_live_env()
    if not enable_telemetry:
        os.environ["MOZAIKS_AG2_TELEMETRY_ENABLED"] = "false"
    os.environ["USAGE_EVENTS_ENABLED"] = "false"
    os.environ["MOZAIKS_LLM_CONFIG_SKIP_MONGO"] = "true"
    if int(str(os.getenv("LLM_CONFIG_CACHE_TTL") or "0") or "0") <= 0:
        os.environ["LLM_CONFIG_CACHE_TTL"] = "300"

    _initialize_workflow_root(workflows_root, workflow_name)

    app_id = f"live-agentgenerator-pack-{uuid.uuid4().hex[:8]}"
    chat_id = f"chat_{workflow_name.lower()}_{uuid.uuid4().hex[:8]}"
    user_id = "live-smoke-user"
    generated_root = (generated_root or (REPO_ROOT / ".tmp" / "agentgenerator_live_pack" / app_id / "generated")).resolve()
    active_workflows_root = (
        active_workflows_root
        or (REPO_ROOT / ".tmp" / "agentgenerator_live_pack" / app_id / "active_workflows")
    ).resolve()
    generated_root.mkdir(parents=True, exist_ok=True)
    active_workflows_root.mkdir(parents=True, exist_ok=True)

    context = build_seeded_pack_context()
    context.update(
        {
            "workflow_name": workflow_name,
            "app_id": app_id,
            "chat_id": chat_id,
            "user_id": user_id,
        }
    )

    async def _run() -> dict[str, Any]:
        from mozaiksai.core.adapters.ag2_task_batch_runner import (
            AG2TaskBatchRunner,
            AG2TaskBatchRunnerRequest,
        )
        from mozaiksai.core.ports.orchestration import RunStatus
        from mozaiksai.core.workflow.agents import create_agents
        from mozaiksai.core.workflow.outputs.structured import load_workflow_structured_outputs
        from mozaiksai.core.workflow.task_batches import (
            execute_task_batches_for_trigger,
            load_task_batches_config,
        )

        _, structured_registry = load_workflow_structured_outputs(workflow_name)

        async def _run_generation_batch(batch_label: str) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
            batch_agents = await create_agents(workflow_name, context_variables=context, cache_seed=None)
            if not batch_agents:
                raise RuntimeError("AgentGenerator created no AG2 agents")

            expected_names = [str(item["name"]) for item in context["workflows_spec"]]
            expected_count = len(expected_names)
            coordinator_result = await AG2TaskBatchRunner().run(
                AG2TaskBatchRunnerRequest(
                    workflow_name=workflow_name,
                    batch_id=f"live_agentgenerator_pack_boundary_{batch_label}",
                    task_id=f"pack_build_coordinator_{batch_label}",
                    chat_id=chat_id,
                    app_id=app_id,
                    agent_name="PackBuildCoordinator",
                    agent=batch_agents["PackBuildCoordinator"],
                    prompt=(
                        "The workflow plan has been approved. "
                        f"The authoritative workflows_spec contains exactly {expected_count} workflows: "
                        f"{', '.join(expected_names)}. "
                        f"Emit workflow_count={expected_count} and start parallel workflow bundle generation."
                    ),
                    context_variables=context,
                    structured_registry={"PackBuildCoordinator": structured_registry["PackBuildCoordinator"]},
                    timeout_seconds=120,
                )
            )
            if coordinator_result.status is not RunStatus.COMPLETED:
                raise RuntimeError(f"PackBuildCoordinator failed: {coordinator_result.error}")
            coordinator_payload = coordinator_result.output if isinstance(coordinator_result.output, dict) else {}
            if int(coordinator_payload.get("workflow_count") or 0) != expected_count:
                raise RuntimeError(
                    "PackBuildCoordinator workflow_count did not match workflows_spec length: "
                    f"{coordinator_payload!r}"
                )

            task_batches_config = load_task_batches_config(workflow_name, workflows_root=workflows_root)
            if task_batches_config is None:
                raise RuntimeError("AgentGenerator task_batches.yaml did not load")
            for batch in task_batches_config.batches:
                batch.execution.retry_limit = 0

            trace = TaskRunTrace()
            with _trace_task_batch_runner(trace):
                batch_payload = await execute_task_batches_for_trigger(
                    workflow_name=workflow_name,
                    trigger_agent="PackBuildCoordinator",
                    batches_config=task_batches_config,
                    agents=batch_agents,
                    context_variables=context,
                    structured_output=coordinator_payload,
                    chat_id=chat_id,
                    app_id=app_id,
                    user_id=user_id,
                    transport=None,
                    wf_logger=None,
                    fresh_agents_per_task=True,
                )

            if context.get("workflow_bundle_status") != "completed":
                raise RuntimeError(f"workflow_bundle_status={context.get('workflow_bundle_status')!r}")
            return coordinator_payload, batch_payload or {}, trace.summary()

        async def _run_pack_metadata(batch_label: str) -> tuple[dict[str, Any], list[str]]:
            metadata_agents = await create_agents(workflow_name, context_variables=context, cache_seed=None)
            expected_specs = context.get("workflows_spec") or []
            if context.get("workflow_bundle_repair_active") is True:
                original_specs = context.get("workflow_bundle_repair_original_workflows_spec")
                if isinstance(original_specs, list) and original_specs:
                    expected_specs = original_specs
            expected_names = [str(item["name"]) for item in expected_specs]
            expected_metadata_payload = [
                {
                    "id": str(item["name"]),
                    "startup_mode": (item.get("context_variables") or {}).get("expected_workflow_startup_mode"),
                    "depends_on": list(item.get("depends_on") or []),
                }
                for item in expected_specs
            ]
            metadata_result = await AG2TaskBatchRunner().run(
                AG2TaskBatchRunnerRequest(
                    workflow_name=workflow_name,
                    batch_id=f"live_agentgenerator_pack_boundary_{batch_label}",
                    task_id=f"pack_metadata_{batch_label}",
                    chat_id=chat_id,
                    app_id=app_id,
                    agent_name="PackMetadataAgent",
                    agent=metadata_agents["PackMetadataAgent"],
                    prompt=(
                        "Generate PackMetadata for the completed workflow_bundle_results in context. "
                        f"Pack name must be {context['pack_name']!r}. "
                        f"Workflow ids must exactly match: {', '.join(expected_names)}. "
                        "Use these exact startup modes and dependencies: "
                        f"{json.dumps(expected_metadata_payload, sort_keys=True)}. "
                        "Do not use placeholder or example workflow names. Do not infer dependencies."
                    ),
                    context_variables=context,
                    structured_registry={"PackMetadataAgent": structured_registry["PackMetadataAgent"]},
                    timeout_seconds=180,
                )
            )
            if metadata_result.status is not RunStatus.COMPLETED:
                raise RuntimeError(f"PackMetadataAgent failed: {metadata_result.error}")
            metadata_payload = metadata_result.output if isinstance(metadata_result.output, dict) else {}
            if not metadata_payload.get("PackMetadata"):
                raise RuntimeError("PackMetadataAgent completed without PackMetadata structured output")
            context["PackMetadata"] = metadata_payload["PackMetadata"]
            return metadata_payload, expected_names

        coordinator_output, batch_results, initial_trace = await _run_generation_batch("initial")
        metadata_output, expected_workflow_names = await _run_pack_metadata("initial")

        repair_attempts: list[dict[str, Any]] = []
        download_result = await _export_workflow_bundle(context=context, generated_root=generated_root)
        while download_result.get("status") != "success":
            repair_result = download_result.get("workflow_bundle_repair")
            if not isinstance(repair_result, dict) or repair_result.get("status") != "needs_revision":
                raise RuntimeError(f"generate_and_download failed: {download_result}")
            attempt = int(repair_result.get("attempt") or len(repair_attempts) + 1)
            coordinator_repair, batch_repair, repair_trace = await _run_generation_batch(f"repair_{attempt}")
            metadata_output, expected_workflow_names = await _run_pack_metadata(f"repair_{attempt}")
            repair_attempts.append(
                {
                    "repair_result": repair_result,
                    "coordinator_output": coordinator_repair,
                    "task_batch_result_ids": sorted((batch_repair or {}).keys()),
                    "task_run_trace": repair_trace,
                    "merge_result": context.get("workflow_bundle_repair_merge_result"),
                }
            )
            download_result = await _export_workflow_bundle(context=context, generated_root=generated_root)

        bundle_root = generated_root / "workflows" / app_id
        validation = validate_generated_workflow_bundle(
            bundle_root=bundle_root,
            expected_workflows=context["workflows_spec"],
        )
        semantic_drift = validate_agentgenerator_semantic_drift(
            bundle_root=bundle_root,
            expected_workflows=context["workflows_spec"],
        )
        promotion = promote_and_load_generated_workflows(
            bundle_root=bundle_root,
            expected_workflows=context["workflows_spec"],
            active_root=active_workflows_root,
        )

        task_meta = (context.get("workflow_bundle_results") or {}).get("_meta")
        validation_errors = []
        validation_errors.extend(validation.get("errors") or [])
        validation_errors.extend(semantic_drift.get("errors") or [])
        validation_errors.extend(promotion.get("errors") or [])
        if not isinstance(task_meta, dict):
            validation_errors.append("workflow_bundle_results._meta missing")
        else:
            expected_count = len(context["workflows_spec"])
            if int(task_meta.get("task_count") or 0) != expected_count:
                validation_errors.append(
                    f"task_count={task_meta.get('task_count')!r} did not match expected {expected_count}"
                )
            if int(task_meta.get("concurrency") or 0) < 2:
                validation_errors.append(f"task batch concurrency too low: {task_meta.get('concurrency')!r}")
            if int(initial_trace.get("max_overlap") or 0) < 2:
                validation_errors.append("task batch worker AG2 calls did not overlap")

        metadata_graph = ((metadata_output.get("PackMetadata") or {}).get("workflow_graph") or {})
        metadata_workflows = [
            item for item in metadata_graph.get("workflows", []) if isinstance(item, dict)
        ]
        metadata_workflow_ids = [str(item.get("id") or "") for item in metadata_workflows]
        if metadata_graph.get("pack_name") != context.get("pack_name"):
            validation_errors.append(
                f"PackMetadata pack_name={metadata_graph.get('pack_name')!r} did not match {context.get('pack_name')!r}"
            )
        if sorted(metadata_workflow_ids) != sorted(expected_workflow_names):
            validation_errors.append(
                "PackMetadata workflow ids did not match generated workflows: "
                f"{metadata_workflow_ids!r} vs {expected_workflow_names!r}"
            )
        expected_specs_by_name = {str(item["name"]): item for item in context["workflows_spec"]}
        for metadata_workflow in metadata_workflows:
            workflow_id = str(metadata_workflow.get("id") or "")
            expected_spec = expected_specs_by_name.get(workflow_id)
            if not expected_spec:
                continue
            expected_mode = (expected_spec.get("context_variables") or {}).get("expected_workflow_startup_mode")
            if expected_mode and metadata_workflow.get("startup_mode") != expected_mode:
                validation_errors.append(
                    f"PackMetadata {workflow_id} startup_mode={metadata_workflow.get('startup_mode')!r} "
                    f"did not match {expected_mode!r}"
                )
            expected_dependencies = sorted(str(item) for item in expected_spec.get("depends_on", []) or [])
            actual_dependencies = sorted(
                str(item.get("id") or "")
                for item in metadata_workflow.get("dependencies", [])
                if isinstance(item, dict) and str(item.get("id") or "")
            )
            if actual_dependencies != expected_dependencies:
                validation_errors.append(
                    f"PackMetadata {workflow_id} dependencies={actual_dependencies!r} "
                    f"did not match {expected_dependencies!r}"
                )

        return {
            "success": not validation_errors,
            "workflow_name": workflow_name,
            "app_id": app_id,
            "chat_id": chat_id,
            "pack_name": context.get("pack_name"),
            "generated_root": generated_root,
            "bundle_root": bundle_root,
            "active_workflows_root": active_workflows_root,
            "coordinator_output": coordinator_output,
            "metadata_output": metadata_output,
            "task_batch_result_ids": sorted((batch_results or {}).keys()),
            "task_batch_meta": task_meta,
            "task_run_trace": initial_trace,
            "repair_attempts": repair_attempts,
            "download_result": download_result,
            "validation": validation,
            "semantic_drift": semantic_drift,
            "promotion": promotion,
            "validation_errors": validation_errors,
        }

    try:
        return _json_safe(await asyncio.wait_for(_run(), timeout=timeout_seconds))
    except Exception as exc:
        return _json_safe(
            {
                "success": False,
                "workflow_name": workflow_name,
                "app_id": app_id,
                "chat_id": chat_id,
                "pack_name": context.get("pack_name"),
                "generated_root": generated_root,
                "active_workflows_root": active_workflows_root,
                "validation_errors": [str(exc)],
            }
        )


def main() -> int:
    _configure_event_loop_policy()
    parser = argparse.ArgumentParser(
        description="Run a live AG2 AgentGenerator pack smoke from approved plan through export and loader validation."
    )
    parser.add_argument("--workflow", default=DEFAULT_WORKFLOW)
    parser.add_argument("--workflows-root", default=str(DEFAULT_WORKFLOWS_ROOT))
    parser.add_argument("--timeout-seconds", type=float, default=600.0)
    parser.add_argument("--generated-root", default=None)
    parser.add_argument("--active-workflows-root", default=None)
    parser.add_argument(
        "--enable-telemetry",
        action="store_true",
        help="Opt into AG2 telemetry middleware during the smoke. Disabled by default.",
    )
    args = parser.parse_args()

    payload = asyncio.run(
        run_live_agentgenerator_pack_smoke(
            timeout_seconds=float(args.timeout_seconds),
            workflow_name=str(args.workflow),
            workflows_root=Path(args.workflows_root),
            generated_root=Path(args.generated_root) if args.generated_root else None,
            active_workflows_root=Path(args.active_workflows_root) if args.active_workflows_root else None,
            enable_telemetry=bool(args.enable_telemetry),
        )
    )
    print(json.dumps(payload, indent=2), flush=True)
    return 0 if payload.get("success") else 1


if __name__ == "__main__":
    raise SystemExit(main())
