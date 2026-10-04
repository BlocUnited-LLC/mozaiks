"""A run that cannot continue ends with one terminal event that says why.

Live acceptance run at a22cf4dc, AppGenerator chat ed0d00b0: bundle repair was
blocked and the turn went back to the user. Each driver reply re-ran the same
validation on the same bundle, 13 times, until AG2 closed the channel on
max_turns. The runner had already reported that last turn as a pause, so no
failed ``chat.run_complete`` was ever sent and the session stayed in progress.
The next message hit the closed channel and got "An internal error occurred".

These drive the real runner and AG2 Hub, the real AppGenerator transition
graph and validation gate, and the real SimpleTransport send path. AG2 runs on
a virtual clock, so a store with Mongo-like write latency costs nothing.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml
from ag2 import Agent
from ag2.knowledge import MemoryKnowledgeStore

from factory_app.workflows.AppGenerator.tools.app_validation import (
    _blocking_errors,
    _readable_error,
    _record_validation_outcome,
    validate_app_bundle_from_request,
)
from mozaiksai.core.adapters.ag2_network_runner import (
    CHANNEL_TERMINAL_ERROR,
    AG2NetworkRunner,
    AG2NetworkRunnerRequest,
    AG2NetworkRunnerResult,
)
from mozaiksai.core.events import unified_event_dispatcher as _dispatcher_mod
from mozaiksai.core.ports.orchestration import RunStatus
from mozaiksai.core.transport import workflow_bridge as _bridge_mod
from mozaiksai.core.transport.simple_transport import SimpleTransport
from mozaiksai.core.workflow.agents.factory import ContextVariablesBridge, _workflow_tool_invocation
from mozaiksai.core.workflow.context.authority import build_context_authority_policy
from mozaiksai.core.workflow.orchestration_patterns import _run_complete_event
from tests.test_ag2_network_execution_alignment import _DeterministicAgent
from tests.test_ag2_network_idle_deadline import _run_on_virtual_clock
from tests.test_generated_app_functional_acceptance import _basic_crud_files

APPGEN = Path(__file__).resolve().parents[1] / "factory_app" / "workflows" / "AppGenerator"
# The release driver's reply whenever a run hands it the turn.
DRIVER_REPLY = "Confirmed. Proceed with the approved scope and complete the build."
APP_ID = "run-end-app"
USER_ID = "run-end-user"
WORKFLOW = "RunEndProbe"


class _DurableStoreLatency(MemoryKnowledgeStore):
    """Writes take as long as a durable store's round trip.

    The hub awaits its store between delivering a packet and appending the close
    that accepting the packet decided. Production's Mongo store yields there; the
    plain memory store does not, which hid the race.
    """

    async def write(self, path: str, content: str) -> None:
        await asyncio.sleep(0.4)
        await super().write(path, content)


def _validator_and_user_rules() -> list[dict[str, Any]]:
    return [
        {"source_agent": "Validator", "target_agent": "user", "transition_type": "after_turn",
         "transition_target": "RevertToUserTarget"},
        {"source_agent": "user", "target_agent": "Validator", "transition_type": "after_turn"},
    ]


class _Persistence:
    """The session seams the live continuation and the terminal guard touch."""

    def __init__(self) -> None:
        self.status = 0
        self.failed: list[str] = []
        self.user_messages: list[str] = []
        self.assistant_messages: list[str] = []

    async def chat_has_resumable_run(self, chat_id, app_id, workflow_name=None):  # noqa: ANN001
        return False

    async def assert_chat_resumable(self, chat_id, app_id) -> None:  # noqa: ANN001
        from mozaiksai.core.data.models import WorkflowStatus
        from mozaiksai.core.data.persistence.persistence_manager import ChatSessionTerminalError

        if self.status:
            raise ChatSessionTerminalError(WorkflowStatus(self.status))

    async def get_pending_input_request(self, **kwargs):  # noqa: ANN003
        return None

    async def clear_pending_input_request(self, **kwargs) -> None:  # noqa: ANN003
        return None

    async def append_run_user_message(self, **kwargs) -> None:  # noqa: ANN003
        self.user_messages.append(kwargs["content"])

    async def append_run_assistant_message(self, **kwargs) -> None:  # noqa: ANN003
        self.assistant_messages.append(kwargs["content"])

    async def persist_context_variables(self, **kwargs) -> None:  # noqa: ANN003
        return None

    async def mark_chat_completed(self, chat_id, app_id=None) -> bool:  # noqa: ANN001
        raise AssertionError("a run that ended early was marked completed")

    async def mark_chat_failed(self, chat_id, app_id=None) -> bool:  # noqa: ANN001
        self.failed.append(chat_id)
        self.status = 2
        return True


@pytest.fixture
def transport_path(monkeypatch):
    """A real transport whose only stubs are the websocket, the session store and hooks."""
    transport = SimpleTransport()
    persistence = _Persistence()
    broadcast: list[dict[str, Any]] = []
    dispatched: list[dict[str, Any]] = []
    on_fail: list[dict[str, Any]] = []
    revisions_failed: list[str] = []

    async def _record_broadcast(envelope, chat_id=None):  # noqa: ANN001
        broadcast.append(envelope)

    async def _record_emit(_self, event_name, payload=None, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        if event_name == "runtime.process_completed":
            dispatched.append(payload)

    class _Lifecycle:
        async def execute_trigger(self, trigger, **kwargs):  # noqa: ANN001, ANN003
            on_fail.append({"trigger": trigger, **kwargs})

    async def _router_for_chat(**kwargs):  # noqa: ANN003
        async def fail_active_revision(**kw):  # noqa: ANN003
            revisions_failed.append(kw["workflow_id"])
        return SimpleNamespace(fail_active_revision=fail_active_revision)

    from mozaiksai.core.session import router as _router_mod
    from mozaiksai.core.workflow.execution import lifecycle as _lifecycle_mod

    monkeypatch.setattr(type(_dispatcher_mod.get_event_dispatcher()), "emit", _record_emit)
    monkeypatch.setattr(transport, "_broadcast_to_websockets", _record_broadcast)
    monkeypatch.setattr(transport, "_get_or_create_persistence_manager", lambda: persistence)
    monkeypatch.setattr(transport, "_apply_user_text_context_updates", _no_user_text_updates)
    monkeypatch.setattr(_bridge_mod, "get_workflow_lifecycle_hooks", lambda _name: {})
    monkeypatch.setattr(_lifecycle_mod, "get_lifecycle_manager", lambda _name: _Lifecycle())
    monkeypatch.setattr(_router_mod, "get_session_router_for_chat", _router_for_chat)

    def of_type(event_type: str) -> list[dict[str, Any]]:
        return [envelope.get("data") or {} for envelope in broadcast if envelope.get("type") == event_type]

    async def send(chat_id: str) -> dict[str, Any]:
        result = await transport.handle_user_input_from_api(
            chat_id=chat_id, user_id=USER_ID, workflow_name=WORKFLOW,
            message=DRIVER_REPLY, app_id=APP_ID,
        )
        await asyncio.sleep(0)  # let the detached completion dispatch run
        return result

    return SimpleNamespace(
        transport=transport, persistence=persistence, of_type=of_type, send=send,
        dispatched=dispatched, on_fail=on_fail, revisions_failed=revisions_failed,
    )


async def _no_user_text_updates(**kwargs) -> dict[str, Any]:  # noqa: ANN003
    return {}


async def _paused_validator_run(
    chat_id: str, *, max_turns: int | None = None, store: MemoryKnowledgeStore | None = None,
) -> tuple[_DeterministicAgent, AG2NetworkRunnerResult]:
    validator = _DeterministicAgent("Validator", "Running validation checks.")
    result = await AG2NetworkRunner().run(AG2NetworkRunnerRequest(
        workflow_name=WORKFLOW, chat_id=chat_id, app_id=APP_ID,
        agents={"Validator": validator}, transition_rules=_validator_and_user_rules(),
        initial_agent_name="Validator", initial_message="Begin.", max_turns=max_turns,
        knowledge_store=store or MemoryKnowledgeStore(),
    ))
    assert result.status is RunStatus.PAUSED, result.error
    return validator, result


def test_max_turns_close_after_a_user_bound_packet_settles_once_as_failed(transport_path) -> None:
    """(a) The turn AG2 closes on max_turns is the run's end, not a pause."""
    chat_id = "chat-max-turns"

    async def scenario() -> tuple[_DeterministicAgent, dict[str, Any], dict[str, Any]]:
        validator, paused = await _paused_validator_run(chat_id, max_turns=3, store=_DurableStoreLatency())
        transport_path.transport.register_live_ag2_workflow_run(chat_id, paused.live_run)
        ending = await transport_path.send(chat_id)
        after_end = await transport_path.send(chat_id)
        return validator, ending, after_end

    validator, ending, after_end = _run_on_virtual_clock(scenario())

    # The reply that used the last turn ends the run; the next one reaches no agent.
    assert len(validator.ask_calls) == 2
    assert ending["run_status"] == "failed"
    # Only the reply's own turn is projected, once.
    assert transport_path.persistence.assistant_messages == ["Running validation checks."]
    assert len(transport_path.of_type("chat.stream_end")) == 1
    completions = transport_path.of_type("chat.run_complete")
    assert len(completions) == 1, completions
    assert completions[0]["status"] == "failed"
    assert completions[0]["run_completed"] is False
    assert completions[0]["awaiting_user_input"] is False
    assert completions[0]["close_reason"] == "max_turns"
    assert completions[0]["error"] == "max_turns"
    assert transport_path.of_type("chat.awaiting_reply") == []
    assert [payload["status"] for payload in transport_path.dispatched] == ["failed"]
    assert transport_path.persistence.failed == [chat_id]
    assert [call["trigger"] for call in transport_path.on_fail] == ["on_fail"]
    assert transport_path.revisions_failed == [WORKFLOW]
    assert transport_path.transport.get_live_ag2_workflow_run(chat_id) is None

    assert after_end["error_code"] == "WORKFLOW_SESSION_TERMINAL"
    assert after_end["reason"] == "max_turns"
    [refusal] = transport_path.of_type("error")
    assert refusal["error_code"] == "WORKFLOW_SESSION_TERMINAL"
    assert refusal["message"].startswith("This workflow session has ended: max_turns")


def test_a_message_to_a_closed_channel_settles_the_run_and_gets_the_reason(transport_path) -> None:
    """(c) A paused run whose channel AG2 closed refuses input with the close reason.

    Reproduces the live state: a registered paused run over a channel AG2 had
    closed on max_turns, with no terminal event sent for it.
    """
    chat_id = "chat-closed-channel"

    async def scenario() -> tuple[_DeterministicAgent, dict[str, Any], dict[str, Any]]:
        validator, paused = await _paused_validator_run(chat_id)
        live_run = paused.live_run
        transport_path.transport.register_live_ag2_workflow_run(chat_id, live_run)
        await live_run._hub.close_channel(paused.channel_id, reason="max_turns")
        refused = await transport_path.send(chat_id)
        refused_again = await transport_path.send(chat_id)
        return validator, refused, refused_again

    validator, refused, refused_again = _run_on_virtual_clock(scenario())

    assert len(validator.ask_calls) == 1
    # The message could not be delivered, so it is not recorded as run input,
    # and the turn the paused run already reported is not projected again.
    assert transport_path.persistence.user_messages == []
    assert transport_path.persistence.assistant_messages == []
    for event_type in ("chat.stream_chunk", "chat.stream_end", "chat.text"):
        assert transport_path.of_type(event_type) == [], event_type
    for response in (refused, refused_again):
        assert response["error_code"] == "WORKFLOW_SESSION_TERMINAL"
        assert response["route"] == "terminal_session"
        assert response["reason"] == "max_turns"
    errors = transport_path.of_type("error")
    assert [error["error_code"] for error in errors] == ["WORKFLOW_SESSION_TERMINAL"] * 2
    assert all(error["message"].startswith("This workflow session has ended: max_turns") for error in errors)
    assert all(error["reason"] == "max_turns" for error in errors)
    assert not any("internal error" in str(error.get("message")).lower() for error in errors)

    # The run's unannounced end is announced exactly once, with its close reason.
    completions = transport_path.of_type("chat.run_complete")
    assert len(completions) == 1, completions
    assert completions[0]["status"] == "failed"
    assert completions[0]["close_reason"] == "max_turns"
    assert completions[0]["error"] == "max_turns"
    assert [payload["status"] for payload in transport_path.dispatched] == ["failed"]
    assert transport_path.persistence.failed == [chat_id]
    assert transport_path.transport.get_live_ag2_workflow_run(chat_id) is None


def test_a_refused_message_through_background_execution_is_announced_once(transport_path) -> None:
    """A start routed through background execution adds no second outcome event."""
    chat_id = "chat-closed-background"

    async def scenario() -> dict[str, Any]:
        _, paused = await _paused_validator_run(chat_id)
        transport_path.transport.register_live_ag2_workflow_run(chat_id, paused.live_run)
        await paused.live_run._hub.close_channel(paused.channel_id, reason="max_turns")
        result = await transport_path.transport._run_workflow_background(
            chat_id=chat_id, workflow_name=WORKFLOW, app_id=APP_ID, user_id=USER_ID,
            ws_id=None, initial_message=DRIVER_REPLY,
        )
        for _ in range(3):
            await asyncio.sleep(0)
        return result

    result = _run_on_virtual_clock(scenario())

    assert result["error_code"] == "WORKFLOW_SESSION_TERMINAL"
    assert len(transport_path.of_type("chat.run_complete")) == 1
    assert [payload["status"] for payload in transport_path.dispatched] == ["failed"]


def test_run_end_reasons_keep_only_the_most_recent_failures() -> None:
    from mozaiksai.core.transport.workflow_bridge import _RUN_END_REASON_LIMIT

    transport = SimpleTransport()
    for index in range(_RUN_END_REASON_LIMIT + 5):
        transport._record_run_end(f"chat-{index}", {"status": "failed", "error": f"reason {index}"})
    transport._record_run_end("chat-paused", {"status": "paused"})

    reasons = transport._run_end_reasons
    assert len(reasons) == _RUN_END_REASON_LIMIT
    assert "chat-0" not in reasons and "chat-paused" not in reasons
    last = f"chat-{_RUN_END_REASON_LIMIT + 4}"
    assert reasons[last] == f"reason {_RUN_END_REASON_LIMIT + 4}"


# ---------------------------------------------------------------------------
# AppGenerator: blocked repair and a validation rerun that cannot progress
# ---------------------------------------------------------------------------


def _appgenerator_contract() -> tuple[list[dict[str, Any]], Any, dict[str, Any]]:
    rules = yaml.safe_load((APPGEN / "transition_graph.yaml").read_text(encoding="utf-8"))["transition_rules"]
    definitions = yaml.safe_load((APPGEN / "context_variables.yaml").read_text(encoding="utf-8"))["definitions"]
    orchestrator = yaml.safe_load((APPGEN / "orchestrator.yaml").read_text(encoding="utf-8"))
    policy = build_context_authority_policy(
        workflow_name="AppGenerator", definitions=definitions, transition_rules=rules,
    )
    return rules, policy, orchestrator


def _appgen_bundle(files: dict[str, str]) -> dict[str, Any]:
    return {
        "generated_files": files, "coding_participation": "autonomous",
        "app_assembly_status": "passed",
        "app_build_plan": {"build_tasks": [{
            "task_id": "app_schema", "task_type": "page_bundle", "initial_agent": "AppSchemaAgent",
            "owned_paths": ["app.json"], "depends_on": [],
        }]},
        "app_task_batch_results": {
            "app_schema": {"code_files": [{"filename": "app.json", "content": files["app.json"]}]},
            "_meta": {"status": "completed"},
        },
    }


def _bundle_missing_a_handler_method() -> dict[str, Any]:
    """An assembled bundle whose failing module file no approved task owns."""
    files = _basic_crud_files()
    files["modules/orders/backend/handler.py"] = (
        "class OrdersModule:\n"
        "    def __init__(self):\n"
        "        pass\n\n"
        "    async def list_orders(self, ctx, **params):\n"
        "        return {'orders': []}\n"
    )
    return _appgen_bundle(files)


class _FailingBuildSandbox:
    """A sandbox whose shell build fails, with stderr naming a fresh temp dir each run."""

    def __init__(self) -> None:
        self.builds = 0

    async def create_session(self, **kwargs: Any) -> SimpleNamespace:
        return SimpleNamespace(session_id=f"sandbox-{self.builds}", provider="docker")

    async def write_files(self, **kwargs: Any) -> None:
        return None

    async def run_command(self, *, command: str, **kwargs: Any) -> SimpleNamespace:
        self.builds += 1
        workdir = Path(tempfile.gettempdir()) / f"mozaiks-app-validation-{self.builds}x7f"
        stderr = (
            f"error during build:\n[vite]: Rollup failed to resolve import \"./Orders\" from "
            f"\"{workdir / 'src' / 'main.jsx'}\".\n" + "    at resolve (node_modules/vite/dist/chunk.js)\n" * 40
        )
        return SimpleNamespace(success=False, stdout="", stderr=stderr)

    async def terminate_session(self, **kwargs: Any) -> bool:
        return True


async def _run_appgenerator_validation(
    initial: dict[str, Any],
    *,
    chat_id: str,
    on_validation: Any = None,
    validation_request: dict[str, Any] | None = None,
) -> SimpleNamespace:
    """Run AppGenerator from AppValidationAgent with the real graph and gate.

    A pause is answered the way the release driver answered it, so a run that
    hands the turn back keeps validating until it ends.
    """
    rules, policy, orchestrator = _appgenerator_contract()
    bridge = ContextVariablesBridge(initial, authority_policy=policy)
    speakers: list[str] = []
    validations: list[dict[str, Any]] = []
    pauses: list[dict[str, Any]] = []

    class _Agent(Agent):
        async def ask(self, *msg: Any, **kwargs: Any) -> SimpleNamespace:
            speakers.append(self.name)
            return SimpleNamespace(body="Running validation checks.")

    async def output_hook(agent_name: str, envelope: Any) -> None:
        if agent_name != "AppValidationAgent":
            raise AssertionError(f"unexpected turn: {agent_name}")
        with _workflow_tool_invocation(bridge):
            if on_validation is not None:
                validations.append(on_validation(bridge))
            else:
                validations.append(await validate_app_bundle_from_request(
                    validation_request or {"validation_strategy": "skip", "start_dev_server": False},
                    context_variables=bridge,
                ))

    names = {
        name for rule in rules for name in (rule["source_agent"], rule["target_agent"])
        if name not in {"user", "terminate"}
    }
    agents = {name: _Agent(name, prompt="Deterministic test response") for name in names}
    for agent in agents.values():
        agent._mozaiks_context_bridge = bridge
    result = await AG2NetworkRunner().run(AG2NetworkRunnerRequest(
        workflow_name="AppGenerator", chat_id=chat_id, app_id="appgen-run-end",
        agents=agents, initial_agent_name="AppValidationAgent",
        initial_message="Validate the assembled bundle.", transition_rules=rules,
        context_variables=initial, agent_output_handler=output_hook,
        context_authority_policy=policy, max_turns=orchestrator["max_turns"],
        failure_message_key=orchestrator["failure_message_key"],
        knowledge_store=MemoryKnowledgeStore(), idle_timeout_seconds=60.0,
    ))
    while result.status is RunStatus.PAUSED and len(pauses) < orchestrator["max_turns"]:
        pauses.append(dict(result.context_variables))
        result = await result.live_run.continue_with_user_message(DRIVER_REPLY)
    if result.live_run is not None:
        await result.live_run.close()
    return SimpleNamespace(result=result, speakers=speakers, validations=validations, pauses=pauses)


def _assert_readable(message: str) -> None:
    """Each listed error is one bounded line, with no host temp path in it."""
    temp_root = tempfile.gettempdir()
    assert temp_root not in message and temp_root.replace("\\", "/") not in message
    for line in message.splitlines()[2:]:
        assert line.startswith("- ") and len(line) <= 2 + 300, line


@pytest.mark.asyncio
async def test_blocked_repair_ends_the_run_after_one_validation_with_its_blocking_errors() -> None:
    """(b) Blocked repair is the end of the run, reported with its blocking errors."""
    run = await _run_appgenerator_validation(_bundle_missing_a_handler_method(), chat_id="appgen-blocked")
    result = run.result

    assert run.speakers == ["AppValidationAgent"]
    [validation] = run.validations
    repair = validation["bundle_repair"]
    assert repair["status"] == "blocked" and repair["target_agent"] is None
    assert result.status is RunStatus.FAILED
    assert result.close_reason == "workflow_failed"
    assert result.live_run is None

    message = result.failure_message
    assert message is not None
    assert message.startswith(
        "The app build cannot continue: validation found errors that no repair step can fix.\nBlocking errors:\n- "
    )
    assert repair["errors"], repair
    for error in repair["errors"][:10]:
        assert f"- {_readable_error(error)}" in message
    _assert_readable(message)
    assert result.context_variables["app_build_failure_message"] == message
    # The terminal event carries it as the error the UI shows.
    event = _run_complete_event(workflow_name="AppGenerator", chat_id="appgen-blocked",
                                runner_result=result, pause_agent=None)
    assert event["status"] == "failed"
    assert event["error"] == message
    assert event["close_reason"] == "workflow_failed"


@pytest.mark.asyncio
async def test_validating_an_unchanged_bundle_again_with_the_same_result_ends_the_run(monkeypatch) -> None:
    """(b) A failed build, a user reply, the same failed build: the run ends.

    The bundle passes acceptance and its build fails, so validation hands the
    turn to the user. Nothing in a reply changes the bundle. The second build
    names a different temp workspace, which is per-run noise, not a new result.
    """
    from mozaiksai.core import adapters

    sandbox = _FailingBuildSandbox()
    monkeypatch.setattr(adapters, "get_sandbox_adapter", lambda strategy: sandbox)

    run = await _run_appgenerator_validation(
        _appgen_bundle(_basic_crud_files()), chat_id="appgen-no-progress",
        validation_request={"validation_strategy": "docker", "start_dev_server": False},
    )
    result = run.result

    assert run.speakers == ["AppValidationAgent", "AppValidationAgent"]
    assert [item["app_bundle_acceptance_result"]["passed"] for item in run.validations] == [True, True]
    first, second = (item["app_validation_result"]["errors"] for item in run.validations)
    assert first != second  # the raw stderr names a different temp workspace each time
    [pause] = run.pauses
    assert pause["app_validation_no_progress"] is False
    assert pause["app_build_failure_message"] is None
    assert result.status is RunStatus.FAILED
    assert result.close_reason == "workflow_failed"
    assert result.context_variables["app_validation_no_progress"] is True
    message = result.failure_message
    assert message.startswith(
        "The app build cannot continue: validation ran again on an unchanged bundle and failed the same way.\n"
        "Blocking errors:\n- "
    )
    assert "<temp>" in message
    _assert_readable(message)


@pytest.mark.asyncio
async def test_unavailable_validation_infrastructure_ends_the_run_as_an_environment_problem(monkeypatch) -> None:
    """(b) An environment outage ends the run without blaming the app.

    The bundle passes acceptance; E2B is requested with no key configured, so
    the gate reports the validation infrastructure unavailable both times.
    """
    monkeypatch.delenv("E2B_API_KEY", raising=False)
    monkeypatch.delenv("MOZAIKS_APP_VALIDATION_STRATEGY", raising=False)

    run = await _run_appgenerator_validation(
        _appgen_bundle(_basic_crud_files()), chat_id="appgen-infrastructure",
        validation_request={"validation_strategy": "e2b", "start_dev_server": False},
    )
    result = run.result

    assert run.speakers == ["AppValidationAgent", "AppValidationAgent"]
    assert [item["app_bundle_acceptance_result"]["passed"] for item in run.validations] == [True, True]
    assert [item["app_validation_result"]["errors"] for item in run.validations] == (
        [["Validation infrastructure unavailable."]] * 2
    )
    [pause] = run.pauses
    assert pause["app_build_failure_message"] is None
    assert result.status is RunStatus.FAILED
    assert result.close_reason == "workflow_failed"
    assert result.failure_message == (
        "The app build cannot continue: the validation environment was unavailable. "
        "This is an environment problem, not a defect in the app; retry the build "
        "once validation infrastructure is available.\n"
        "Validation environment errors:\n"
        "- Validation infrastructure unavailable."
    )


def test_failure_message_errors_are_one_bounded_line_without_host_temp_paths() -> None:
    workspace = Path(tempfile.gettempdir()) / "mozaiks-app-runtime-load-5f2c" / "bundle"
    stderr = "npm run build failed: " + "\n".join(f"  at frame {index} ({workspace})" for index in range(200))
    errors = _blocking_errors(
        {"bundle_repair": {"diagnostics": [
            {"error": f"app_runtime_load: app.json not found in {workspace}"},
            {"error": stderr},
        ]}},
        None,
    )
    assert errors[0] == "app_runtime_load: app.json not found in <temp>"
    assert len(errors[1]) == 300 and errors[1].endswith("...") and "\n" not in errors[1]
    assert str(workspace) not in " ".join(errors)


@pytest.mark.parametrize("outcome,ends_run", [
    ({"bundle_repair": {"status": "blocked", "target_agent": None}, "task_recovery_request": None}, True),
    ({"bundle_repair": {"status": "blocked", "target_agent": None},
      "task_recovery_request": {"request_id": "recover-1"}}, False),
    ({"bundle_repair": {"status": "needs_revision", "target_agent": "ServiceAgent"},
      "task_recovery_request": None}, False),
])
def test_failure_message_is_written_only_when_the_outcome_ends_the_run(
    outcome: dict[str, Any], ends_run: bool,
) -> None:
    """Recovery or a selected repair runs next, so the run goes on: no message."""
    _, policy, _ = _appgenerator_contract()
    bridge = ContextVariablesBridge({}, authority_policy=policy)
    bridge._bind_run(("AppGenerator", "appgen-run-end", "message-only-when-ending"), policy)
    acceptance = {"status": "failed", "validation_evidence": {"failed": ["module_implementation"]},
                  **outcome}
    acceptance["bundle_repair"] = {**acceptance["bundle_repair"], "diagnostics": [{"error": "handler missing"}]}
    with _workflow_tool_invocation(bridge):
        bridge.set("app_build_failure_message", "a previous run's message")
        _record_validation_outcome(bridge, files={"app.json": "{}"}, acceptance=acceptance,
                                   validation=None, passed=False)
    message = bridge.get("app_build_failure_message")
    if ends_run:
        assert message == (
            "The app build cannot continue: validation found errors that no repair step can fix.\n"
            "Blocking errors:\n- handler missing"
        )
    else:
        assert message is None


# The validation the live run repeated, as recorded in its AG2 WAL (channel
# 18da8a40716eb4304f9d6ebddbcae38f, envelope 80): the keys AppValidationAgent's
# routing reads and the bundle repair result.
_LIVE_DIAGNOSTICS = [
    {
        "block_reason": "task_failure_requires_batch_recovery", "code": "TASK_FAILED",
        "error": "task_tasks_management_business_services: invalid syntax. Perhaps you forgot a comma? (<unknown>, line 7)",
        "owner_agent": "ServiceAgent", "path": "", "task_id": "task_tasks_management_business_services",
    },
    {
        "block_reason": "task_failure_requires_batch_recovery",
        "error": "module_action_handler_method_missing: modules/tasks_management/module.yaml action "
                 "'summarize_tasks' declares handler_method 'summarize_tasks', but class "
                 "'TasksManagementModule' does not implement it.",
        "owner_agent": "ServiceAgent", "path": "modules/tasks_management/backend/handler.py",
        "task_id": "task_tasks_management_business_services", "test": "module_action_handler_method_missing",
    },
    {
        "block_reason": "task_failure_requires_batch_recovery",
        "error": "app_runtime_module_load: Handler class 'TasksManagementModule' is missing action method(s): "
                 "summarize_tasks",
        "owner_agent": "ServiceAgent", "path": "modules/tasks_management/backend/handler.py",
        "task_id": "task_tasks_management_business_services", "test": "app_runtime_module_load",
    },
    {
        "block_reason": "task_failure_requires_batch_recovery",
        "error": "MISSING_MODULE_ACTION: modules/tasks_management/module.yaml action 'summarize_tasks' "
                 "declares handler_method 'summarize_tasks', but modules/tasks_management/backend/handler.py "
                 "class 'TasksManagementModule' does not implement it.",
        "owner_agent": "ServiceAgent", "path": "modules/tasks_management/backend/handler.py",
        "task_id": "task_tasks_management_business_services", "test": "MISSING_MODULE_ACTION",
    },
]
_LIVE_BUNDLE_REPAIR = {
    "status": "blocked", "repairable": False, "target_agent": None, "attempt": 0, "max_attempts": 2,
    "errors": [item["error"] for item in _LIVE_DIAGNOSTICS], "diagnostics": _LIVE_DIAGNOSTICS,
    "no_progress": False, "repair_request": None, "active": None, "history": [],
}
# Recorded state other writers own (task batch, assembly), present before the turn.
_LIVE_STATE = {
    "app_task_recovery_status": "idle", "app_assembly_status": "passed",
    "app_task_batch_status": "partial", "app_validation_status": "pending",
    "integration_tests_passed": False,
}


def _replay_live_validation(bridge: Any) -> dict[str, Any]:
    """Write what the gate wrote on that turn, then decide the run's outcome."""
    for key, value in {
        "bundle_repair_status": "blocked", "bundle_repair_target": None,
        "bundle_repair_result": _LIVE_BUNDLE_REPAIR,
    }.items():
        bridge.set(key, value)
    acceptance = {
        "status": "failed", "bundle_repair": _LIVE_BUNDLE_REPAIR, "task_recovery_request": None,
        "validation_evidence": {
            "completed": ["agent_backend", "bundle_scan", "module_runtime_quality", "module_wiring",
                          "schema_quality", "workflow_integration"],
            "failed": ["app_runtime_load", "app_runtime_smoke", "functional_completeness",
                       "module_implementation", "planned_completeness"],
            "skipped": [],
        },
    }
    _record_validation_outcome(
        bridge, files={"recorded": "a22cf4dc"}, acceptance=acceptance,
        validation={"validation_status": "pending", "errors": []}, passed=False,
    )
    return {"bundle_repair": _LIVE_BUNDLE_REPAIR}


@pytest.mark.asyncio
async def test_replaying_the_live_blocked_validation_ends_the_run_with_its_four_errors() -> None:
    """(b) The recorded live result, through the real AppGenerator graph, ends at once."""
    initial = {
        "coding_participation": "autonomous",
        "app_task_batch_results": {"_meta": {"status": "partial"}},
        **_LIVE_STATE,
    }
    run = await _run_appgenerator_validation(
        initial, chat_id="appgen-live-replay", on_validation=_replay_live_validation,
    )
    result = run.result

    # Live, this validation ran 13 times between driver replies until max_turns.
    assert run.speakers == ["AppValidationAgent"]
    assert len(run.validations) == 1
    assert result.status is RunStatus.FAILED
    assert result.close_reason == "workflow_failed"
    expected = "\n".join([
        "The app build cannot continue: validation found errors that no repair step can fix.",
        "Blocking errors:",
        *(f"- {item['error']}" for item in _LIVE_DIAGNOSTICS),
    ])
    assert result.failure_message == expected


@pytest.mark.parametrize("definition", [None, {"type": "object"}])
def test_failure_message_key_must_name_a_declared_string(definition: dict[str, Any] | None) -> None:
    from mozaiksai.core.workflow.contract_validation import validate_workflow_context_contract

    config = {
        "failure_message_key": "why_it_failed",
        "context_variables": {"definitions": {"why_it_failed": definition} if definition else {}},
    }
    with pytest.raises(ValueError, match="failure_message_key 'why_it_failed' must name a declared string"):
        validate_workflow_context_contract(workflow_name="Probe", workflow_config=config)

    config["context_variables"]["definitions"]["why_it_failed"] = {"type": "string"}
    validate_workflow_context_contract(workflow_name="Probe", workflow_config=config)


def test_run_complete_reports_a_closed_channel_by_its_close_reason() -> None:
    """A call refused by a closed channel tells the user why AG2 closed it."""
    ended = AG2NetworkRunnerResult(
        status=RunStatus.FAILED, workflow_name=WORKFLOW, chat_id="chat", app_id=APP_ID,
        close_reason="max_turns", error=CHANNEL_TERMINAL_ERROR,
    )
    event = _run_complete_event(workflow_name=WORKFLOW, chat_id="chat", runner_result=ended, pause_agent=None)
    assert event == {
        "kind": "run_complete", "workflow": WORKFLOW, "chat_id": "chat", "run_completed": False,
        "awaiting_user_input": False, "status": "failed", "reason": "failed",
        "error": "max_turns", "close_reason": "max_turns",
    }
    assert json.loads(json.dumps(event)) == event
