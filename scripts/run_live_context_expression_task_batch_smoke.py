from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

DEFAULT_WORKFLOW = "RuntimeContextExpressionTaskBatchSmoke"
DEFAULT_WORKFLOWS_ROOT = REPO_ROOT / "factory_app" / "workflows"
DEFAULT_PROMPT = (
    "Build a social media app with profiles, feed ranking, creator onboarding, "
    "moderation, notifications, and an admin dashboard. Decompose it into bounded "
    "module, page, and integration work units, run the task batch, and summarize "
    "the result."
)


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
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    return str(value)


def _routing_context() -> dict[str, Any]:
    return {
        "task_batch_route_ready": True,
        "routing_mode": "task_batch",
        "selected_capabilities": ["module", "page", "integration"],
    }


def _require_live_env() -> None:
    if not str(os.getenv("OPENAI_API_KEY") or "").strip():
        raise RuntimeError("Missing OPENAI_API_KEY. The script loads .env before this check.")


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


def _preflight_context_expression_route(config: dict[str, Any]) -> str | None:
    from mozaiksai.core.workflow.execution.network_graph import (
        compile_transition_rules_to_graph,
        resolve_next_agent,
    )

    agents_config = config.get("agents") or {}
    agent_entries = agents_config.get("agents") if isinstance(agents_config, dict) else agents_config
    if isinstance(agent_entries, dict):
        agent_entries = list(agent_entries.values())
    if not isinstance(agent_entries, list):
        agent_entries = []
    agent_names = [
        str(agent.get("name") or "").strip()
        for agent in agent_entries
        if isinstance(agent, dict) and str(agent.get("name") or "").strip()
    ]
    transition_rules = (config.get("transition_graph") or {}).get("transition_rules") or []
    initial_agent = str(config.get("initial_agent") or "").strip() or "TaskPlannerAgent"
    graph = compile_transition_rules_to_graph(
        transition_rules,
        initial_agent_name=initial_agent,
        agent_id_by_name={name: name for name in agent_names},
        max_turns=config.get("max_turns"),
    )
    return resolve_next_agent(
        graph,
        current_agent_name="TaskPlannerAgent",
        context_variables=_routing_context(),
        agent_name_by_id={name: name for name in agent_names},
        participant_order=agent_names,
    )


@dataclass
class _InMemoryPersistence:
    sessions: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict)
    messages: list[dict[str, Any]] = field(default_factory=list)
    final_context: dict[str, Any] = field(default_factory=dict)
    completed: bool = False

    async def load_run_history(self, *, chat_id: str, app_id: str) -> list[dict[str, Any]]:
        session = self.sessions.get((app_id, chat_id)) or {}
        return list(session.get("messages") or [])

    async def create_chat_session(
        self,
        *,
        chat_id: str,
        app_id: str,
        workflow_name: str,
        user_id: str,
        extra_fields: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        session = {
            "_id": chat_id,
            "chat_id": chat_id,
            "app_id": app_id,
            "workflow_name": workflow_name,
            "user_id": user_id,
            "messages": [],
            "status": 0,
        }
        if extra_fields:
            session.update(dict(extra_fields))
        self.sessions[(app_id, chat_id)] = session
        return session

    async def get_or_assign_cache_seed(self, chat_id: str, app_id: str) -> int:
        return abs(hash((app_id, chat_id))) % (2**31)

    async def fetch_chat_session_extra_context(self, *, chat_id: str, app_id: str) -> dict[str, Any]:
        return {}

    async def append_run_assistant_message(
        self,
        *,
        chat_id: str,
        app_id: str,
        content: str,
        agent_name: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        message = {
            "role": "assistant",
            "name": agent_name,
            "agent": agent_name,
            "content": content,
            "metadata": dict(metadata or {}),
        }
        self.messages.append(message)
        session = self.sessions.setdefault(
            (app_id, chat_id),
            {"_id": chat_id, "chat_id": chat_id, "app_id": app_id, "messages": [], "status": 0},
        )
        session.setdefault("messages", []).append(message)
        return message

    async def persist_context_variables(self, *, chat_id: str, app_id: str, variables: dict[str, Any]) -> None:
        self.final_context = dict(variables or {})
        session = self.sessions.setdefault(
            (app_id, chat_id),
            {"_id": chat_id, "chat_id": chat_id, "app_id": app_id, "messages": [], "status": 0},
        )
        session.update(dict(variables or {}))

    async def mark_chat_completed(self, chat_id: str, *, app_id: str) -> bool:
        self.completed = True
        session = self.sessions.setdefault(
            (app_id, chat_id),
            {"_id": chat_id, "chat_id": chat_id, "app_id": app_id, "messages": [], "status": 0},
        )
        session["status"] = 1
        return True


@dataclass
class _InMemoryTransport:
    connections: dict[str, dict[str, Any]] = field(default_factory=dict)
    events: list[dict[str, Any]] = field(default_factory=list)
    derived_context_managers: dict[str, Any] = field(default_factory=dict)

    async def send_event_to_ui(self, event: dict[str, Any], chat_id: str) -> None:
        self.events.append({"chat_id": chat_id, "event": _json_safe(event)})

    def register_derived_context_manager(self, chat_id: str, manager: Any) -> None:
        self.derived_context_managers[chat_id] = manager

    def unregister_derived_context_manager(self, chat_id: str) -> None:
        self.derived_context_managers.pop(chat_id, None)


def _install_runtime_shims(
    *,
    persistence: _InMemoryPersistence,
    transport: _InMemoryTransport,
) -> Callable[[], None]:
    import mozaiksai.core.workflow.orchestration_patterns as orchestration_patterns
    from mozaiksai.core.transport.simple_transport import SimpleTransport

    original_persistence_factory = orchestration_patterns.AG2PersistenceManager
    original_transport_instance = getattr(SimpleTransport, "_instance", None)
    original_get_instance = SimpleTransport.__dict__["get_instance"]

    orchestration_patterns.AG2PersistenceManager = lambda: persistence

    async def _get_instance(cls, *args: Any, **kwargs: Any) -> _InMemoryTransport:
        return transport

    SimpleTransport._instance = transport
    SimpleTransport.get_instance = classmethod(_get_instance)

    def _restore() -> None:
        orchestration_patterns.AG2PersistenceManager = original_persistence_factory
        SimpleTransport._instance = original_transport_instance
        SimpleTransport.get_instance = original_get_instance

    return _restore


def _latest_synthesis_output(result: dict[str, Any] | None) -> dict[str, Any]:
    structured_outputs = (result or {}).get("structured_outputs") or []
    for entry in reversed(structured_outputs):
        if not isinstance(entry, dict):
            continue
        if entry.get("agent") != "SynthesisAgent":
            continue
        data = entry.get("structured_data")
        return dict(data) if isinstance(data, dict) else {}
    return {}


def _validation_errors(
    *,
    result: dict[str, Any] | None,
    synthesis_output: dict[str, Any],
    final_context: dict[str, Any],
    route_target: str | None,
) -> list[str]:
    errors: list[str] = []
    task_batch_results = final_context.get("runtime_smoke_tasks_results")
    task_batch_meta = task_batch_results.get("_meta") if isinstance(task_batch_results, dict) else None
    if route_target != "SynthesisAgent":
        errors.append(f"context_expression preflight routed to {route_target!r}, expected 'SynthesisAgent'")
    if not isinstance(result, dict) or result.get("run_completed") is not True:
        errors.append(f"workflow did not complete: run_status={(result or {}).get('run_status')!r}")
    if final_context.get("runtime_smoke_tasks_status") != "completed":
        errors.append(
            "runtime task batch did not persist completed status: "
            f"{final_context.get('runtime_smoke_tasks_status')!r}"
        )
    if not isinstance(task_batch_meta, dict):
        errors.append("runtime task batch did not persist runtime_smoke_tasks_results._meta")
    if synthesis_output.get("task_batch_execution_used") is not True:
        errors.append("SynthesisAgent did not confirm task_batch_execution_used=true")
    if synthesis_output.get("all_units_succeeded") is not True:
        errors.append("SynthesisAgent did not confirm all_units_succeeded=true")
    try:
        work_unit_count = int(synthesis_output.get("work_unit_count") or 0)
    except (TypeError, ValueError):
        work_unit_count = 0
    meta_task_count = int((task_batch_meta or {}).get("task_count") or 0)
    if meta_task_count and work_unit_count != meta_task_count:
        errors.append(f"work_unit_count={work_unit_count} did not match executor task_count={meta_task_count}")
    if work_unit_count < 3:
        errors.append(f"expected at least 3 work units, got {work_unit_count}")
    try:
        failure_count = int(synthesis_output.get("failure_count") or 0)
    except (TypeError, ValueError):
        failure_count = 0
    meta_failed_tasks = list((task_batch_meta or {}).get("failed_tasks") or [])
    if isinstance(task_batch_meta, dict) and failure_count != len(meta_failed_tasks):
        errors.append(
            f"failure_count={failure_count} did not match executor failed_tasks={len(meta_failed_tasks)}"
        )
    if failure_count != 0:
        errors.append(f"expected failure_count=0, got {failure_count}")
    meta_completed_tasks = list((task_batch_meta or {}).get("completed_tasks") or [])
    if meta_completed_tasks and synthesis_output.get("executed_task_ids") != meta_completed_tasks:
        errors.append("executed_task_ids did not match executor completed_tasks")
    meta_concurrency = (task_batch_meta or {}).get("concurrency")
    if meta_concurrency is not None and synthesis_output.get("max_parallelism") != meta_concurrency:
        errors.append("max_parallelism did not match executor concurrency")
    meta_result_context_key = (task_batch_meta or {}).get("result_context_key")
    if meta_result_context_key and synthesis_output.get("result_context_key") != meta_result_context_key:
        errors.append("result_context_key did not match executor metadata")
    return errors


def _message_summaries(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    for message in messages:
        content = str(message.get("content") or "").strip()
        parsed: Any | None = None
        if content.startswith("{") and content.endswith("}"):
            try:
                parsed = json.loads(content)
            except json.JSONDecodeError:
                parsed = None
        summaries.append(
            {
                "agent": str(message.get("agent") or message.get("name") or ""),
                "content": _json_safe(parsed if parsed is not None else content[:1200]),
            }
        )
    return summaries


async def run_live_context_expression_task_batch_smoke(
    *,
    prompt: str = DEFAULT_PROMPT,
    timeout_seconds: float = 300.0,
    workflow_name: str = DEFAULT_WORKFLOW,
    workflows_root: Path = DEFAULT_WORKFLOWS_ROOT,
    enable_telemetry: bool = False,
) -> dict[str, Any]:
    load_dotenv(REPO_ROOT / ".env")
    _require_live_env()
    if enable_telemetry:
        os.environ["AG2_OTEL_ENABLED"] = "true"
    # Default usage metering off for hermetic runs, but honour an explicit
    # USAGE_EVENTS_ENABLED=true so live smokes can record per-build token
    # costs in the runtime usage ledger when a Mongo sink is available.
    os.environ.setdefault("USAGE_EVENTS_ENABLED", "false")
    os.environ["MOZAIKS_LLM_CONFIG_SKIP_MONGO"] = "true"
    try:
        cache_ttl = int(os.getenv("LLM_CONFIG_CACHE_TTL") or "0")
    except ValueError:
        cache_ttl = 0
    if cache_ttl <= 0:
        os.environ["LLM_CONFIG_CACHE_TTL"] = "300"

    config = _initialize_workflow_root(workflows_root, workflow_name)
    route_target = _preflight_context_expression_route(config)

    app_id = f"live-context-task-batch-{uuid.uuid4().hex[:8]}"
    chat_id = f"chat_{workflow_name.lower()}_{uuid.uuid4().hex[:8]}"
    user_id = "live-smoke-user"
    persistence = _InMemoryPersistence()
    transport = _InMemoryTransport()
    transport.connections[chat_id] = {
        "workflow_name": workflow_name,
        "frontend_context": _routing_context(),
    }
    restore = _install_runtime_shims(persistence=persistence, transport=transport)

    try:
        from mozaiksai.core.workflow.orchestration_patterns import run_workflow_orchestration

        result = await asyncio.wait_for(
            run_workflow_orchestration(
                workflow_name=workflow_name,
                app_id=app_id,
                chat_id=chat_id,
                user_id=user_id,
                initial_message=prompt,
                context_factory=lambda: dict(_routing_context()),
            ),
            timeout=timeout_seconds,
        )
    finally:
        restore()

    synthesis_output = _latest_synthesis_output(result)
    validation_errors = _validation_errors(
        result=result,
        synthesis_output=synthesis_output,
        final_context=persistence.final_context,
        route_target=route_target,
    )
    task_batch_results = persistence.final_context.get("runtime_smoke_tasks_results")
    task_batch_meta = task_batch_results.get("_meta") if isinstance(task_batch_results, dict) else None
    return _json_safe(
        {
            "success": not validation_errors,
            "workflow_name": workflow_name,
            "app_id": app_id,
            "chat_id": chat_id,
            "context_expression_route_target": route_target,
            "run_status": (result or {}).get("run_status") if isinstance(result, dict) else None,
            "run_completed": (result or {}).get("run_completed") if isinstance(result, dict) else False,
            "synthesis_output": synthesis_output,
            "task_batch_meta": task_batch_meta if isinstance(task_batch_meta, dict) else None,
            "final_context_keys": sorted(persistence.final_context.keys()),
            "task_batch_status": persistence.final_context.get("runtime_smoke_tasks_status"),
            "assistant_messages": _message_summaries(persistence.messages),
            "event_kinds": [
                str((event.get("event") or {}).get("kind") or "")
                for event in transport.events
                if isinstance(event, dict)
            ],
            "event_count": len(transport.events),
            "validation_errors": validation_errors,
        }
    )


def main() -> int:
    _configure_event_loop_policy()
    parser = argparse.ArgumentParser(
        description="Run a live AG2 context-expression + task-batch smoke without Mongo or websocket server."
    )
    parser.add_argument("--workflow", default=DEFAULT_WORKFLOW)
    parser.add_argument("--workflows-root", default=str(DEFAULT_WORKFLOWS_ROOT))
    parser.add_argument("--prompt", default=DEFAULT_PROMPT)
    parser.add_argument("--prompt-file", default=None)
    parser.add_argument("--timeout-seconds", type=float, default=300.0)
    parser.add_argument(
        "--enable-telemetry",
        action="store_true",
        help="Opt into AG2 telemetry middleware during this smoke. Disabled by default.",
    )
    args = parser.parse_args()

    prompt = str(args.prompt)
    if args.prompt_file:
        prompt = Path(args.prompt_file).read_text(encoding="utf-8").strip()
        if not prompt:
            raise RuntimeError("prompt file is empty")

    payload = asyncio.run(
        run_live_context_expression_task_batch_smoke(
            prompt=prompt,
            timeout_seconds=args.timeout_seconds,
            workflow_name=str(args.workflow),
            workflows_root=Path(args.workflows_root),
            enable_telemetry=bool(args.enable_telemetry),
        )
    )
    print(json.dumps(payload, indent=2), flush=True)
    return 0 if payload.get("success") else 1


if __name__ == "__main__":
    raise SystemExit(main())
