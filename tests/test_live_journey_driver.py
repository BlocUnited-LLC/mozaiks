"""The unattended journey driver against a fake host speaking the real wire shapes.

Run-complete, pause, text, usage and UI-tool frames are built by the server's
own producers (``_run_complete_event`` and the outbound envelope builder), so
the fake cannot drift from what a real host sends. The contract tests at the
end pin the driver's constants to the server source they mirror.
"""

from __future__ import annotations

import ast
import asyncio
import base64
import contextlib
import importlib.util
import json
import os
import socket
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import uvicorn
import yaml
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

from mozaiksai.core.events.unified_event_dispatcher import get_event_dispatcher
from mozaiksai.core.ports.orchestration import RunStatus
from mozaiksai.core.workflow.orchestration_patterns import _run_complete_event

ROOT = Path(__file__).resolve().parents[1]
DRIVER_PATH = ROOT / "scripts" / "live_journey_driver.py"
_spec = importlib.util.spec_from_file_location("live_journey_driver", DRIVER_PATH)
assert _spec is not None and _spec.loader is not None
driver = importlib.util.module_from_spec(_spec)
# Register before exec: dataclass field resolution reads sys.modules.
sys.modules["live_journey_driver"] = driver
_spec.loader.exec_module(driver)

APP = "mozaiks-platform"
USER = "dev-user"
BUILD = "build-registry-1"
ARTIFACT = "artifact-version-1"
PROMPT = "Build a team task tracker where a paid Pro plan unlocks weekly summary reports."
THEME_REPLY = "A calm blue brand for small teams."
PRICING = {
    "schema_version": "mozaiks.usage_pricing.v1",
    "models": {"gpt-4o-mini": {"input_per_1m_usd": 0.15, "output_per_1m_usd": 0.6, "cached_input_per_1m_usd": 0.075}},
}
SCRIPT: dict[str, Any] = {
    "schema_version": "mozaiks.journey_script.v1",
    "journey_id": "build",
    "entry_transition": "app_type_selector",
    "prompt": PROMPT,
    "transition_options": {"app_type_selector": "greenfield_app", "coding_journey_selector": "autonomous"},
    "replies": [{"workflow": "ThemeCapture", "agent": "ThemeInterviewAgent", "text": THEME_REPLY}],
    "ui_responses": [
        {
            "workflow": "ValueEngine",
            "component": "ConceptBlueprint",
            "response": {"status": "submitted", "action": "approve", "approved": True},
        }
    ],
}
PROMOTED = {"promoted": True, "app_registry": {"success": True, "lifecycle_state": "active"}}
FORBIDDEN_TOP_LEVEL = {
    "mozaiksai", "factory_app", "logs", "mozaiks", "mozaiks_cli", "mozaiks_chat_ui", "web_shell", "scripts",
}


# --------------------------------------------------------------------------- frames


def envelope(raw: dict[str, Any]) -> dict[str, Any]:
    built = get_event_dispatcher().build_outbound_event_envelope(raw_event=raw, chat_id=raw.get("chat_id"))
    assert built is not None
    return built


def run_complete(chat_id: str, workflow: str, status: RunStatus, **result: Any) -> dict[str, Any]:
    agent = result.pop("agent", None)
    runner_result = SimpleNamespace(
        status=status,
        close_reason=result.get("close_reason"),
        failure_message=None,
        error=result.get("error"),
    )
    return envelope(_run_complete_event(
        workflow_name=workflow, chat_id=chat_id, runner_result=runner_result, pause_agent=agent,
    ))


def pause(chat_id: str, workflow: str, agent: str) -> list[dict[str, Any]]:
    # The order orchestration uses: awaiting_reply, then the paused run_complete.
    return [
        envelope({
            "kind": "awaiting_reply", "workflow": workflow, "chat_id": chat_id, "source_agent": agent,
            "reason": "awaiting_user_reply", "display": "composer", "interaction_type": "input_request",
        }),
        run_complete(chat_id, workflow, RunStatus.PAUSED, agent=agent),
    ]


def says(chat_id: str, agent: str, content: str) -> dict[str, Any]:
    return envelope({"kind": "text", "agent": agent, "content": content, "chat_id": chat_id})


def usage(chat_id: str, *, model: str = "gpt-4o-mini", prompt: int = 1000, completion: int = 100) -> dict[str, Any]:
    return envelope({
        "kind": "usage_delta", "chat_id": chat_id, "model_name": model, "prompt_tokens": prompt,
        "completion_tokens": completion, "total_tokens": prompt + completion, "cached_prompt_tokens": 0,
    })


def tool_call(
    tool_call_id: str,
    component: str,
    *,
    awaiting: bool,
    payload: dict[str, Any] | None = None,
    agent: str | None = None,
) -> dict[str, Any]:
    # Mirrors UIToolsMixin.send_tool_call_event before envelope mapping.
    raw: dict[str, Any] = {
        "kind": "tool_call", "tool_call_id": tool_call_id, "corr": tool_call_id, "tool_name": component,
        "component_type": component, "workflow_name": None, "display": "artifact", "display_type": "artifact",
        "awaiting_response": awaiting, "interaction_type": "ui_tool" if awaiting else "ui_surface",
        "payload": payload or {},
    }
    if agent:
        raw["agent"] = agent
        raw["agent_name"] = agent
    return envelope(raw)


def chat_meta(chat_id: str, workflow: str) -> dict[str, Any]:
    return envelope({
        "kind": "chat_meta", "chat_id": chat_id, "workflow_name": workflow, "app_id": APP, "user_id": USER,
        "status": 0, "run_history_count": 0,
    })


def ui_event(event_type: str, **data: Any) -> dict[str, Any]:
    return {"schema_version": "mozaiks.ui.event.v1", "type": event_type, "data": data, "timestamp": "2026-10-04T00:00:00+00:00"}


def server_error(code: str, message: str, chat_id: str) -> dict[str, Any]:
    # SimpleTransport.send_error
    return ui_event("error", message=message, error_code=code, chat_id=chat_id)


def context_switched(from_chat: str, to_chat: str, workflow: str) -> dict[str, Any]:
    return ui_event(
        "chat.context_switched", from_chat_id=from_chat, to_chat_id=to_chat, workflow_name=workflow,
        app_id=APP, journey_id="journey-instance-1", journey_key="build",
    )


def transition_requested(transition_id: str, from_chat: str) -> dict[str, Any]:
    return ui_event(
        "chat.transition_requested", transition_id=transition_id, from_chat_id=from_chat, app_id=APP,
        journey_id="journey-instance-1", journey_key="build", journey_position=3, context_variables={},
    )


def ack(tool_call_id: str, status: str = "accepted") -> dict[str, Any]:
    return ui_event("ack.tool_call_response", tool_call_id=tool_call_id, status=status)


def launch(chat_id: str, workflow: str, kind: str = "workflow") -> dict[str, Any]:
    return {
        "resolution_type": kind, "chat_id": chat_id, "workflow_id": workflow, "journey_id": "build",
        "websocket_url": f"/ws/{workflow}/{APP}/{chat_id}/{USER}", "context_variables": {},
    }


def submits(text: str) -> Callable[[dict[str, Any]], bool]:
    return lambda frame: frame.get("type") == "user.input.submit" and frame.get("text") == text


def responds(tool_call_id: str, **fields: Any) -> Callable[[dict[str, Any]], bool]:
    def matches(frame: dict[str, Any]) -> bool:
        response = frame.get("response") or {}
        return (
            frame.get("type") == "tool_call_response"
            and frame.get("tool_call_id") == tool_call_id
            and all(response.get(key) == value for key, value in fields.items())
        )

    return matches


# --------------------------------------------------------------------------- fake host


@dataclass
class Send:
    frame: dict[str, Any]


@dataclass
class Expect:
    label: str
    matches: Callable[[dict[str, Any]], bool]


@dataclass
class SetState:
    values: dict[str, Any]


@dataclass
class FakeHost:
    """Plays one scripted frame sequence per WebSocket and records all traffic."""

    resolve: dict[str, list[dict[str, Any]]]
    sockets: dict[str, list[Any]]
    trigger: list[dict[str, Any]] = field(default_factory=list)
    session_state: dict[str, Any] = field(default_factory=lambda: {
        "sequence_status": "in_progress", "journey_key": "build", "current_chat_id": None,
    })
    promote: tuple[int, dict[str, Any]] = (200, PROMOTED)
    transitions: dict[str, dict[str, Any]] = field(default_factory=lambda: {
        "app_review": {"id": "app_review", "transition_type": "chat_session", "route_to": "AppReview"},
    })
    log: list[dict[str, Any]] = field(default_factory=list)
    violations: list[str] = field(default_factory=list)

    def build(self) -> FastAPI:
        app = FastAPI()

        @app.post("/api/transitions/resolve")
        async def resolve(request: Request) -> Any:
            body = await request.json()
            self.log.append({"event": "resolve", "body": body, "authorization": request.headers.get("authorization")})
            queue = self.resolve.get(body.get("transition_id"))
            if not queue:
                return JSONResponse({"detail": "unknown transition"}, status_code=400)
            return queue.pop(0)

        @app.post("/api/workflows/trigger")
        async def trigger(request: Request) -> Any:
            body = await request.json()
            self.log.append({"event": "trigger", "body": body})
            if not self.trigger:
                return JSONResponse({"detail": "Select a registered build target"}, status_code=400)
            return self.trigger.pop(0)

        @app.get("/api/transitions/{transition_id}")
        async def get_transition(transition_id: str) -> Any:
            self.log.append({"event": "get_transition", "transition_id": transition_id})
            if transition_id not in self.transitions:
                return JSONResponse({"detail": "not found"}, status_code=404)
            return self.transitions[transition_id]

        @app.get("/api/session/state")
        async def session_state(request: Request) -> Any:
            self.log.append({"event": "session_state", "params": dict(request.query_params)})
            return {"session_state": dict(self.session_state)}

        @app.post("/api/studio/build/artifacts/{artifact_version_id}/promote")
        async def promote(artifact_version_id: str, request: Request) -> Any:
            self.log.append({
                "event": "promote", "artifact_version_id": artifact_version_id,
                "params": dict(request.query_params), "body": await request.json(),
            })
            status, body = self.promote
            return JSONResponse(body, status_code=status)

        @app.websocket("/ws/{workflow}/{app_id}/{chat_id}/{user_id}")
        async def workflow_socket(websocket: WebSocket, workflow: str, app_id: str, chat_id: str, user_id: str) -> None:
            offered = list(websocket.scope.get("subprotocols") or [])
            self.log.append({"event": "connect", "chat_id": chat_id, "workflow": workflow, "subprotocols": offered})
            await websocket.accept(subprotocol=offered[0] if offered else None)
            try:
                for action in self.sockets.get(chat_id, []):
                    if isinstance(action, Send):
                        await websocket.send_text(json.dumps(action.frame))
                    elif isinstance(action, Expect):
                        frame = await self._receive(websocket, chat_id)
                        if not action.matches(frame):
                            self.violations.append(f"{chat_id}: expected {action.label}, got {frame}")
                            await websocket.close(code=1008)
                            return
                    elif isinstance(action, SetState):
                        self.session_state.update(action.values)
                while True:
                    await self._receive(websocket, chat_id)
            except WebSocketDisconnect:
                return

        return app

    async def _receive(self, websocket: WebSocket, chat_id: str) -> dict[str, Any]:
        frame = json.loads(await websocket.receive_text())
        self.log.append({"event": "received", "chat_id": chat_id, "frame": frame})
        return frame

    def received(self) -> list[dict[str, Any]]:
        return [entry["frame"] for entry in self.log if entry["event"] == "received"]

    def events(self, name: str) -> list[dict[str, Any]]:
        return [entry for entry in self.log if entry["event"] == name]


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@contextlib.asynccontextmanager
async def running(host: FakeHost):
    server = uvicorn.Server(uvicorn.Config(
        host.build(), host="127.0.0.1", port=_free_port(), log_level="warning", access_log=False,
        lifespan="off", timeout_graceful_shutdown=1,
    ))

    async def serve() -> None:
        with contextlib.suppress(SystemExit):
            await server.serve()

    task = asyncio.create_task(serve())
    deadline = time.monotonic() + 15
    while not server.started:
        if task.done() or time.monotonic() > deadline:
            raise RuntimeError("the fake host did not start")
        await asyncio.sleep(0.01)
    try:
        yield f"http://127.0.0.1:{server.config.port}"
    finally:
        server.should_exit = True
        with contextlib.suppress(BaseException):
            await asyncio.wait_for(task, timeout=10)


@dataclass
class Run:
    exit_code: int
    report: dict[str, Any]
    evidence: Path
    elapsed: float

    def transitions(self) -> dict[str, Any]:
        return json.loads((self.evidence / "transitions.json").read_text(encoding="utf-8"))

    def websocket_log(self) -> list[dict[str, Any]]:
        lines = (self.evidence / "journey-websocket.jsonl").read_text(encoding="utf-8").splitlines()
        return [json.loads(line) for line in lines]


def _argv(base_url: str, tmp_path: Path, script: dict[str, Any], *, max_seconds: float, max_usd: float) -> list[str]:
    prompt_file = tmp_path / "journey.json"
    prompt_file.write_text(json.dumps(script), encoding="utf-8")
    pricing_file = tmp_path / "pricing.json"
    pricing_file.write_text(json.dumps(PRICING), encoding="utf-8")
    return [
        "--base-url", base_url, "--app-id", APP, "--user-id", USER, "--build-registry-id", BUILD,
        "--prompt-file", str(prompt_file), "--pricing-file", str(pricing_file),
        "--evidence-dir", str(tmp_path / "evidence"),
        "--max-seconds", str(max_seconds), "--max-usd", str(max_usd),
    ]


async def drive(
    host: FakeHost,
    tmp_path: Path,
    script: dict[str, Any] | None = None,
    *,
    max_seconds: float = 30,
    max_usd: float = 1.0,
) -> Run:
    async with running(host) as base_url:
        started = time.monotonic()
        exit_code = await driver.run_async(_argv(base_url, tmp_path, script or SCRIPT, max_seconds=max_seconds, max_usd=max_usd))
        elapsed = time.monotonic() - started
    evidence = tmp_path / "evidence"
    report = json.loads((evidence / "exit-reason.json").read_text(encoding="utf-8"))
    assert report["exit_code"] == exit_code
    return Run(exit_code, report, evidence, elapsed)


@pytest.fixture(autouse=True)
def fast_driver(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(driver, "REPLY_SETTLE_SECONDS", 0.0)
    monkeypatch.setattr(driver, "SESSION_POLL_SECONDS", 0.05)
    monkeypatch.setattr(driver, "HANDOFF_WINDOW_SECONDS", 1.0)
    monkeypatch.delenv(driver.ACCESS_TOKEN_ENV, raising=False)


def single_step(chat_id: str, workflow: str, *actions: Any) -> FakeHost:
    """A host whose entry transition launches one workflow that plays ``actions``."""
    return FakeHost(
        resolve={"app_type_selector": [launch(chat_id, workflow)]},
        sockets={chat_id: [Send(chat_meta(chat_id, workflow)), *actions]},
    )


# --------------------------------------------------------------------------- the whole journey


def full_journey(*, final_state: dict[str, Any] | None = None, promote: tuple[int, dict[str, Any]] = (200, PROMOTED)) -> FakeHost:
    signal = driver.PROMOTION_SIGNAL.format(artifact_version_id=ARTIFACT, build_registry_id=BUILD)
    workbench = {
        "stage": "files_ready", "artifact_version_id": ARTIFACT, "build_registry_id": BUILD,
        "app_bundle_acceptance_status": "passed", "files": [{"name": "GeneratedApp.zip"}],
        "generated_files": {"app.json": "{}"},
    }
    summary = {
        "artifact_version_id": ARTIFACT, "build_registry_id": BUILD, "can_promote": True,
        "app_bundle_acceptance_status": "passed",
    }
    return FakeHost(
        resolve={
            "app_type_selector": [launch("c-value", "ValueEngine")],
            "coding_journey_selector": [launch("c-design", "DesignDocs")],
            "app_review": [launch("c-review", "AppReview", kind="chat_session")],
        },
        sockets={
            "c-value": [
                Send(chat_meta("c-value", "ValueEngine")),
                Send(ui_event("ping")),
                Expect("a pong", lambda frame: frame.get("type") == "client.pong"),
                Send(says("c-value", "ValueInterviewAgent", "What would you like to build?")),
                *map(Send, pause("c-value", "ValueEngine", "ValueInterviewAgent")),
                Expect("the prompt", submits(PROMPT)),
                Send(ui_event("chat.input_ack", chat_id="c-value", status="accepted")),
                Send(usage("c-value")),
                Send(tool_call("concept-1", "ConceptBlueprint", awaiting=True, agent="GapAnalysisAgent")),
                Expect("the concept approval", responds("concept-1", action="approve")),
                Send(ack("concept-1")),
                Send(run_complete("c-value", "ValueEngine", RunStatus.COMPLETED)),
                Send(context_switched("c-value", "c-theme", "ThemeCapture")),
                Send(says("c-theme", "ThemeInterviewAgent", "Describe the brand.")),
                *map(Send, pause("c-theme", "ThemeCapture", "ThemeInterviewAgent")),
                Expect("the theme reply", submits(THEME_REPLY)),
                Send(tool_call("theme-preview", "ThemePreviewCard", awaiting=False)),
                Send(run_complete("c-theme", "ThemeCapture", RunStatus.COMPLETED)),
                Send(transition_requested("coding_journey_selector", "c-theme")),
            ],
            "c-design": [
                Send(chat_meta("c-design", "DesignDocs")),
                Send(run_complete("c-design", "DesignDocs", RunStatus.COMPLETED)),
                Send(context_switched("c-design", "c-app", "AppGenerator")),
                Send(tool_call("workbench-1", "AppWorkbench", awaiting=True, payload=workbench, agent="DownloadAgent")),
                Expect("the workbench Continue", responds(
                    "workbench-1", status="submitted", action="download_complete", download_accepted=True,
                )),
                Send(ack("workbench-1")),
                Send(run_complete("c-app", "AppGenerator", RunStatus.COMPLETED)),
                Send(context_switched("c-app", "c-security", "SecurityReadiness")),
                Send(run_complete("c-security", "SecurityReadiness", RunStatus.COMPLETED)),
                Send(transition_requested("app_review", "c-security")),
            ],
            "c-review": [
                Send(chat_meta("c-review", "AppReview")),
                Send(tool_call("summary-1", "AppReviewSummary", awaiting=False, payload=summary, agent="ReviewAgent")),
                Send(says("c-review", "ReviewAgent", "Promote this build or describe changes.")),
                *map(Send, pause("c-review", "AppReview", "ReviewAgent")),
                Expect("the promotion signal", submits(signal)),
                Send(run_complete("c-review", "AppReview", RunStatus.COMPLETED)),
                SetState(final_state if final_state is not None else {
                    "sequence_status": "completed", "lifecycle_state": "completed", "current_chat_id": "c-review",
                }),
            ],
        },
        promote=promote,
    )


async def test_journey_exits_zero_only_after_the_host_reports_the_last_step_complete(tmp_path: Path) -> None:
    host = full_journey()
    run = await drive(host, tmp_path)

    assert host.violations == []
    assert (run.exit_code, run.report["reason"]) == (driver.EXIT_COMPLETED, "journey_completed")
    steps = run.transitions()["steps"]
    assert [(step["workflow"], step["outcome"]) for step in steps] == [
        ("ValueEngine", "completed"), ("ThemeCapture", "completed"), ("DesignDocs", "completed"),
        ("AppGenerator", "completed"), ("SecurityReadiness", "completed"), ("AppReview", "completed"),
    ]
    # The entry binds the build target; later transitions name the chat that requested them.
    resolves = [entry["body"] for entry in host.events("resolve")]
    assert [(body["transition_id"], body["option_id"]) for body in resolves] == [
        ("app_type_selector", "greenfield_app"), ("coding_journey_selector", "autonomous"), ("app_review", None),
    ]
    assert resolves[0]["build_registry_id"] == BUILD and resolves[0]["journey_id"] == "build"
    assert resolves[1]["source_chat_id"] == "c-theme" and "build_registry_id" not in resolves[1]
    assert resolves[2]["source_chat_id"] == "c-security"
    # app_review has no options, so the driver looked it up rather than guessing.
    assert [entry["transition_id"] for entry in host.events("get_transition")] == ["app_review"]
    # Promotion happened before the signal, for the reviewed artifact and this build.
    ordered = [entry for entry in host.log if entry["event"] in {"promote", "received"}]
    promote_at = next(i for i, entry in enumerate(ordered) if entry["event"] == "promote")
    signal_at = next(
        i for i, entry in enumerate(ordered)
        if entry["event"] == "received" and str(entry["frame"].get("text", "")).startswith("Promotion confirmed")
    )
    assert promote_at < signal_at
    [promote] = host.events("promote")
    assert promote["artifact_version_id"] == ARTIFACT
    assert promote["params"] == {"build_registry_id": BUILD, "app_id": APP}
    assert run.report["promotion"]["signal_sent"] is True
    assert run.report["prompt_sent_to"] == {"workflow": "ValueEngine", "agent": "ValueInterviewAgent", "chat_id": "c-value"}
    assert run.report["spend"]["usd"] == pytest.approx(1000 * 0.15e-6 + 100 * 0.6e-6)
    assert run.report["unused_script"] == {"replies": {}, "ui_responses": {}}
    assert run.report["server_run_state"] == "ended"
    workbench = steps[3]["workbench"][0]
    assert workbench["app_bundle_acceptance_status"] == "passed" and "generated_files" not in workbench
    directions = {entry["direction"] for entry in run.websocket_log()}
    assert directions == {"received", "sent"}


async def test_last_step_completing_is_not_enough_without_the_host_reporting_the_journey_complete(tmp_path: Path) -> None:
    # The host still names an earlier chat as current: a completed run_complete alone must not exit 0.
    host = full_journey(final_state={"sequence_status": "completed", "current_chat_id": "c-older-journey"})
    run = await drive(host, tmp_path)

    assert host.violations == []
    assert (run.exit_code, run.report["reason"]) == (driver.EXIT_FAILED, "journey_stalled")
    assert run.report["session_state"]["current_chat_id"] == "c-older-journey"
    assert run.elapsed < 60


# --------------------------------------------------------------------------- terminal events


async def test_a_failed_run_complete_stops_the_driver_with_a_failure(tmp_path: Path) -> None:
    host = single_step("c-1", "ValueEngine", Send(run_complete("c-1", "ValueEngine", RunStatus.FAILED, error="model call refused")))
    run = await drive(host, tmp_path)

    assert (run.exit_code, run.report["reason"]) == (driver.EXIT_FAILED, "run_failed")
    assert "model call refused" in run.report["detail"]
    assert run.report["terminal_event"]["type"] == "chat.run_complete"
    assert run.report["server_run_state"] == "ended"
    assert run.elapsed < 60


async def test_chat_failed_stops_the_driver(tmp_path: Path) -> None:
    host = single_step("c-1", "ValueEngine", Send(ui_event("chat.failed", chat_id="c-1", message="the run died")))
    run = await drive(host, tmp_path)

    assert (run.exit_code, run.report["reason"]) == (driver.EXIT_FAILED, "chat_failed")
    assert run.elapsed < 60


@pytest.mark.parametrize(
    ("code", "reason"),
    [
        ("WORKFLOW_SESSION_TERMINAL", "workflow_session_terminal"),
        ("WORKFLOW_EXECUTION_FAILED", "workflow_execution_failed"),
        ("CHAT_BUSY", "server_error"),
    ],
)
async def test_every_error_event_stops_the_driver(tmp_path: Path, code: str, reason: str) -> None:
    host = single_step("c-1", "ValueEngine", Send(server_error(code, "the host refused", "c-1")))
    run = await drive(host, tmp_path)

    assert (run.exit_code, run.report["reason"]) == (driver.EXIT_FAILED, reason)
    assert run.report["last_step"]["errors"][0]["error_code"] == code
    assert run.elapsed < 60


async def test_the_wall_clock_limit_stops_a_silent_journey(tmp_path: Path) -> None:
    host = single_step("c-1", "ValueEngine")
    run = await drive(host, tmp_path, max_seconds=1)

    assert (run.exit_code, run.report["reason"]) == (driver.EXIT_LIMIT, "wall_clock_limit")
    assert "the first turn of ValueEngine" in run.report["detail"]
    assert run.report["server_run_state"] == "may_still_be_running"
    assert run.elapsed < 60


async def test_the_spend_limit_stops_the_driver(tmp_path: Path) -> None:
    host = single_step("c-1", "ValueEngine", Send(usage("c-1", prompt=2_000_000, completion=0)))
    run = await drive(host, tmp_path, max_usd=0.25)

    assert (run.exit_code, run.report["reason"]) == (driver.EXIT_LIMIT, "spend_limit")
    assert run.report["spend"]["usd"] == pytest.approx(0.30)
    assert run.elapsed < 60


async def test_usage_the_pricing_file_cannot_price_stops_the_driver(tmp_path: Path) -> None:
    host = single_step("c-1", "ValueEngine", Send(usage("c-1", model="unpriced-model")))
    run = await drive(host, tmp_path)

    assert (run.exit_code, run.report["reason"]) == (driver.EXIT_INVALID, "spend_unpriceable")
    assert run.elapsed < 60


async def test_the_fresh_session_resume_defect_is_named_not_worked_around(tmp_path: Path) -> None:
    # What current main does when the first message reaches a session that never ran (#642).
    host = single_step(
        "c-1", "ValueEngine",
        Send(run_complete("c-1", "ValueEngine", RunStatus.FAILED, error=driver.FRESH_SESSION_ERROR)),
    )
    run = await drive(host, tmp_path)

    assert (run.exit_code, run.report["reason"]) == (driver.EXIT_FAILED, "fresh_session_resumed_as_interrupted_run")
    assert run.report["known_defect"]["fixed_by"] == "PR #816"
    assert run.report["evidence"]["agent_output_seen"] is False
    assert host.received() == []


async def test_a_rejected_ui_tool_response_stops_the_driver(tmp_path: Path) -> None:
    host = single_step(
        "c-1", "AppGenerator",
        Send(tool_call("workbench-1", "AppWorkbench", awaiting=True)),
        Expect("the workbench Continue", responds("workbench-1", action="download_complete")),
        Send(ack("workbench-1", status="rejected")),
    )
    run = await drive(host, tmp_path)

    assert (run.exit_code, run.report["reason"]) == (driver.EXIT_FAILED, "tool_response_rejected")


async def test_a_closed_socket_mid_step_stops_the_driver(tmp_path: Path) -> None:
    host = single_step("c-1", "ValueEngine", Expect("nothing", lambda frame: False))
    host.sockets["c-1"].insert(1, Send(ui_event("ping")))
    run = await drive(host, tmp_path)

    # The pong did not match, so the fake closed the socket (1008).
    assert (run.exit_code, run.report["reason"]) == (driver.EXIT_FAILED, "websocket_closed")
    assert run.report["evidence"]["close_code"] == 1008


# --------------------------------------------------------------------------- pauses and human assists


async def test_a_validation_agent_never_receives_free_text(tmp_path: Path) -> None:
    host = single_step(
        "c-1", "AppGenerator",
        Send(says("c-1", "AppValidationAgent", "Validation found problems. Should I continue?")),
        *map(Send, pause("c-1", "AppGenerator", "AppValidationAgent")),
    )
    run = await drive(host, tmp_path)

    assert (run.exit_code, run.report["reason"]) == (driver.EXIT_HUMAN_ASSIST, "human_assist_needed")
    [assist] = run.report["human_assists"]
    assert assist["kind"] == "validation_or_review_agent"
    assert assist["last_agent_message"] == "Validation found problems. Should I continue?"
    assert run.report["human_assist_needed"] == 1
    # Not even the prompt went out: nothing was sent after the pause.
    assert host.received() == []
    assert run.report["server_run_state"] == "paused_waiting_for_input"


async def test_an_unscripted_pause_needs_a_human_and_gets_no_generic_reply(tmp_path: Path) -> None:
    host = single_step(
        "c-1", "ValueEngine",
        *map(Send, pause("c-1", "ValueEngine", "ValueInterviewAgent")),
        Expect("the prompt", submits(PROMPT)),
        Send(says("c-1", "GapAnalysisAgent", "Which customers pay for this?")),
        *map(Send, pause("c-1", "ValueEngine", "GapAnalysisAgent")),
    )
    run = await drive(host, tmp_path)

    assert host.violations == []
    assert (run.exit_code, run.report["reason"]) == (driver.EXIT_HUMAN_ASSIST, "human_assist_needed")
    [assist] = run.report["human_assists"]
    assert (assist["kind"], assist["agent"]) == ("unscripted_pause", "GapAnalysisAgent")
    assert assist["last_agent_message"] == "Which customers pay for this?"
    assert [frame["text"] for frame in host.received()] == [PROMPT]


async def test_an_unscripted_ui_tool_needs_a_human(tmp_path: Path) -> None:
    payload = {"agent_message": "Add the payment keys.", "actions": [{"id": "submit_keys"}, {"id": "skip"}]}
    host = single_step(
        "c-1", "AppGenerator", Send(tool_call("keys-1", "AgentAPIKeysBundleInput", awaiting=True, payload=payload)),
    )
    run = await drive(host, tmp_path)

    assert (run.exit_code, run.report["reason"]) == (driver.EXIT_HUMAN_ASSIST, "human_assist_needed")
    [assist] = run.report["human_assists"]
    assert assist["component"] == "AgentAPIKeysBundleInput"
    # What the operator needs to script it: the choices and the request.
    assert (assist["actions"], assist["agent_message"]) == (["submit_keys", "skip"], "Add the payment keys.")
    assert host.received() == []


async def test_an_unscripted_transition_choice_needs_a_human(tmp_path: Path) -> None:
    host = single_step(
        "c-1", "ThemeCapture",
        Send(run_complete("c-1", "ThemeCapture", RunStatus.COMPLETED)),
        Send(transition_requested("database_setup_selector", "c-1")),
    )
    host.transitions["database_setup_selector"] = {"id": "database_setup_selector", "options": [{"id": "local"}]}
    run = await drive(host, tmp_path)

    assert (run.exit_code, run.report["reason"]) == (driver.EXIT_HUMAN_ASSIST, "human_assist_needed")
    assert run.report["human_assists"][0]["options"] == ["local"]


async def test_ui_response_secrets_come_from_the_environment_and_stay_out_of_the_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JOURNEY_DRIVER_TEST_KEY", "sk-test-secret-value")
    script = {
        **SCRIPT,
        "ui_responses": [{
            "workflow": "AppGenerator", "component": "AgentAPIKeysBundleInput",
            "response": {"status": "submitted", "values": {"PAY_KEY": {"$env": "JOURNEY_DRIVER_TEST_KEY"}}},
        }],
    }
    host = single_step(
        "c-1", "AppGenerator",
        Send(tool_call("keys-1", "AgentAPIKeysBundleInput", awaiting=True)),
        Expect("the keys", lambda frame: frame.get("response", {}).get("values") == {"PAY_KEY": "sk-test-secret-value"}),
        Send(ack("keys-1")),
        Send(run_complete("c-1", "AppGenerator", RunStatus.FAILED, error="stop here")),
    )
    run = await drive(host, tmp_path, script)

    assert host.violations == []
    assert run.report["reason"] == "run_failed"
    for path in run.evidence.iterdir():
        assert "sk-test-secret-value" not in path.read_text(encoding="utf-8"), path.name


# --------------------------------------------------------------------------- AppReview


async def test_a_refused_promotion_stops_without_a_promotion_signal(tmp_path: Path) -> None:
    host = full_journey(promote=(409, {"detail": "Only app registry records in review can be promoted."}))
    run = await drive(host, tmp_path)

    assert (run.exit_code, run.report["reason"]) == (driver.EXIT_FAILED, "promotion_failed")
    assert run.report["promotion"]["status_code"] == 409
    assert run.report["promotion"]["signal_sent"] is False
    assert not any(frame.get("text", "").startswith("Promotion confirmed") for frame in host.received())


async def test_the_driver_promotes_only_its_own_build_target(tmp_path: Path) -> None:
    summary = {"artifact_version_id": ARTIFACT, "build_registry_id": "someone-elses-build", "can_promote": True}
    host = single_step(
        "c-review", "AppReview",
        Send(tool_call("summary-1", "AppReviewSummary", awaiting=False, payload=summary)),
        *map(Send, pause("c-review", "AppReview", "ReviewAgent")),
    )
    run = await drive(host, tmp_path)

    assert (run.exit_code, run.report["reason"]) == (driver.EXIT_FAILED, "review_target_mismatch")
    assert host.events("promote") == []


async def test_review_asking_again_after_the_signal_needs_a_human(tmp_path: Path) -> None:
    summary = {"artifact_version_id": ARTIFACT, "build_registry_id": BUILD, "can_promote": True}
    signal = driver.PROMOTION_SIGNAL.format(artifact_version_id=ARTIFACT, build_registry_id=BUILD)
    host = single_step(
        "c-review", "AppReview",
        Send(tool_call("summary-1", "AppReviewSummary", awaiting=False, payload=summary)),
        *map(Send, pause("c-review", "AppReview", "ReviewAgent")),
        Expect("the promotion signal", submits(signal)),
        *map(Send, pause("c-review", "AppReview", "ReviewAgent")),
    )
    run = await drive(host, tmp_path)

    assert (run.exit_code, run.report["reason"]) == (driver.EXIT_HUMAN_ASSIST, "human_assist_needed")
    assert run.report["human_assists"][0]["kind"] == "review_after_promotion"
    assert [frame["text"] for frame in host.received()] == [signal]


# --------------------------------------------------------------------------- entry


async def test_an_entry_workflow_starts_through_the_studio_trigger_route(tmp_path: Path) -> None:
    # A draft build target has no build session yet; only the trigger route opens one.
    script = {key: value for key, value in SCRIPT.items() if key != "entry_transition"}
    script["entry_workflow"] = "ValueEngine"
    host = FakeHost(
        resolve={},
        trigger=[{**launch("c-1", "ValueEngine"), "execution_mode": "workflow", "trigger_source": "route"}],
        sockets={"c-1": [
            Send(chat_meta("c-1", "ValueEngine")),
            Send(run_complete("c-1", "ValueEngine", RunStatus.FAILED, error="no model key")),
        ]},
    )
    run = await drive(host, tmp_path, script)

    [trigger] = host.events("trigger")
    assert trigger["body"] == {
        "workflow_id": "ValueEngine", "trigger_source": "route", "journey_id": "build",
        "build_registry_id": BUILD, "app_id": APP, "user_id": USER, "context_variables": {},
    }
    assert host.events("resolve") == []
    assert (run.exit_code, run.report["reason"]) == (driver.EXIT_FAILED, "run_failed")
    assert run.report["run"]["entry"] == {"workflow": "ValueEngine"}
    assert run.transitions()["steps"][0]["entered_via"] == {"kind": "trigger", "workflow": "ValueEngine"}


@pytest.mark.parametrize("entries", [{}, {"entry_transition": "app_type_selector", "entry_workflow": "ValueEngine"}])
def test_a_prompt_file_names_exactly_one_entry(tmp_path: Path, entries: dict[str, str]) -> None:
    script = {key: value for key, value in SCRIPT.items() if key != "entry_transition"}
    path = tmp_path / "journey.json"
    path.write_text(json.dumps({**script, **entries}), encoding="utf-8")
    with pytest.raises(driver.InvalidInput, match="exactly one of entry_transition and entry_workflow"):
        driver.load_script(path)


# --------------------------------------------------------------------------- invocation


async def test_a_reply_to_a_review_agent_is_refused_before_anything_is_sent(tmp_path: Path) -> None:
    script = {**SCRIPT, "replies": [{"workflow": "AppReview", "agent": "ReviewAgent", "text": "Looks good, proceed."}]}
    host = single_step("c-1", "ValueEngine")
    run = await drive(host, tmp_path, script)

    assert (run.exit_code, run.report["reason"]) == (driver.EXIT_INVALID, "invalid_input")
    assert "never sends free text" in run.report["detail"]
    assert host.log == []


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"surprise": True}, "unknown keys"),
        ({"schema_version": "v0"}, "schema_version"),
        ({"prompt": "  "}, "prompt must be a non-empty string"),
        ({"ui_responses": [{"workflow": "AppGenerator", "component": "AppWorkbench", "response": {"a": 1}}]},
         "answers AppWorkbench itself"),
        ({"ui_responses": [{"workflow": "X", "component": "Y", "response": {"key": {"$env": "JD_UNSET_VARIABLE"}}}]},
         "unset environment variables"),
        ({"replies": [{"workflow": "X", "agent": "Y"}]}, "exactly"),
    ],
)
def test_invalid_prompt_files_are_refused(tmp_path: Path, change: dict[str, Any], message: str) -> None:
    path = tmp_path / "journey.json"
    path.write_text(json.dumps({**SCRIPT, **change}), encoding="utf-8")
    with pytest.raises(driver.InvalidInput, match=message):
        driver.load_script(path)


def test_the_packaged_pricing_catalog_prices_the_default_model() -> None:
    prices = driver.load_pricing(ROOT / "mozaiksai" / "core" / "usage" / "catalogs" / "usage-pricing.generated.json")
    meter = driver.SpendMeter(prices, limit_usd=1.0)
    cost = meter.add({"model_name": "openai/gpt-4o-mini", "prompt_tokens": 1_000_000, "completion_tokens": 0})
    assert cost > 0 and not meter.over_limit()


async def test_a_used_evidence_directory_is_refused(tmp_path: Path) -> None:
    host = single_step("c-1", "ValueEngine", Send(run_complete("c-1", "ValueEngine", RunStatus.FAILED, error="x")))
    await drive(host, tmp_path)
    async with running(host) as base_url:
        exit_code = await driver.run_async(_argv(base_url, tmp_path, SCRIPT, max_seconds=5, max_usd=1))
    assert exit_code == driver.EXIT_INVALID


async def test_a_bearer_token_reaches_http_and_the_websocket(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(driver.ACCESS_TOKEN_ENV, "token-for-dev-user")
    host = single_step("c-1", "ValueEngine", Send(run_complete("c-1", "ValueEngine", RunStatus.FAILED, error="x")))
    run = await drive(host, tmp_path)

    assert run.report["run"]["signed_in"] is True
    assert host.events("resolve")[0]["authorization"] == "Bearer token-for-dev-user"
    subprotocols = host.events("connect")[0]["subprotocols"]
    assert subprotocols[0] == driver.WS_BEARER_SUBPROTOCOL
    padded = subprotocols[1] + "=" * (-len(subprotocols[1]) % 4)
    assert base64.urlsafe_b64decode(padded).decode() == "token-for-dev-user"
    assert "token-for-dev-user" not in "".join(p.read_text(encoding="utf-8") for p in run.evidence.iterdir())


async def test_the_command_line_runs_standalone_and_exits_non_zero_on_failure(tmp_path: Path) -> None:
    host = single_step("c-1", "ValueEngine", Send(run_complete("c-1", "ValueEngine", RunStatus.FAILED, error="boom")))
    async with running(host) as base_url:
        env = {key: value for key, value in os.environ.items() if key not in {"PYTHONPATH", driver.ACCESS_TOKEN_ENV}}
        env["PYTHON_DOTENV_DISABLED"] = "1"
        started = time.monotonic()
        # A thread keeps the fake host serving on this loop while the process runs.
        result = await asyncio.to_thread(
            subprocess.run,
            [sys.executable, str(DRIVER_PATH), *_argv(base_url, tmp_path, SCRIPT, max_seconds=30, max_usd=1)],
            cwd=str(tmp_path), env=env, capture_output=True, text=True, timeout=60, check=False,
        )
    assert result.returncode == driver.EXIT_FAILED, result.stderr
    assert json.loads(result.stdout.strip().splitlines()[-1])["reason"] == "run_failed"
    assert time.monotonic() - started < 60


# --------------------------------------------------------------------------- isolation and contracts


def test_the_driver_imports_nothing_from_the_mozaiks_packages() -> None:
    source = DRIVER_PATH.read_text(encoding="utf-8")
    imported: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, "relative import"
            imported.add(str(node.module).split(".")[0])
        elif isinstance(node, ast.Call):
            name = getattr(node.func, "attr", None) or getattr(node.func, "id", None)
            assert name not in {"import_module", "__import__", "run_path"}, "dynamic import"
    assert imported & FORBIDDEN_TOP_LEVEL == set()
    assert "sys.path" not in source


def test_loading_the_driver_pulls_in_no_mozaiks_module(tmp_path: Path) -> None:
    # A clean interpreter outside the repository: the editable install would let
    # any such import succeed, so it would show up in sys.modules.
    probe = (
        "import json, runpy, sys\n"
        "runpy.run_path(sys.argv[1], run_name='journey_driver_probe')\n"
        "forbidden = set(json.loads(sys.argv[2]))\n"
        "print(json.dumps(sorted({name.split('.')[0] for name in sys.modules} & forbidden)))\n"
    )
    env = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    env["PYTHON_DOTENV_DISABLED"] = "1"
    result = subprocess.run(
        [sys.executable, "-c", probe, str(DRIVER_PATH), json.dumps(sorted(FORBIDDEN_TOP_LEVEL))],
        cwd=str(tmp_path), env=env, capture_output=True, text=True, timeout=120, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout.strip().splitlines()[-1]) == []


def test_the_bearer_subprotocol_matches_the_host() -> None:
    from mozaiksai.core.auth.websocket_auth import WS_BEARER_SUBPROTOCOL

    assert driver.WS_BEARER_SUBPROTOCOL == WS_BEARER_SUBPROTOCOL


def test_the_named_server_signals_exist_in_the_server_source() -> None:
    runner = (ROOT / "mozaiksai" / "core" / "adapters" / "ag2_network_runner.py").read_text(encoding="utf-8")
    assert f'error="{driver.FRESH_SESSION_ERROR}"' in runner
    server = "".join(
        (ROOT / path).read_text(encoding="utf-8")
        for path in (
            "mozaiksai/core/transport/workflow_bridge.py",
            "mozaiksai/core/workflow/pack/journey_orchestrator.py",
        )
    )
    for code in driver.ERROR_REASONS:
        assert f'"{code}"' in server, code
    transport = (ROOT / "mozaiksai" / "core" / "transport" / "simple_transport.py").read_text(encoding="utf-8")
    assert '"client.pong"' in transport
    studio = (ROOT / "mozaiksai" / "hosts" / "studio.py").read_text(encoding="utf-8")
    assert '@app.post("/api/studio/build/artifacts/{artifact_version_id}/promote")' in studio
    assert '@app.post("/api/workflows/trigger")' in studio
    trigger_model = studio.split("class WorkflowTriggerRequest(BaseModel):", 1)[1].split("\n\n", 1)[0]
    for name in ("workflow_id", "trigger_source", "journey_id", "context_variables", "app_id", "user_id",
                 "build_registry_id"):
        assert f"    {name}:" in trigger_model, name
    transitions = (ROOT / "mozaiksai" / "hosts" / "routers" / "transitions.py").read_text(encoding="utf-8")
    assert '@router.post("/api/transitions/resolve")' in transitions
    assert '@router.get("/api/session/state")' in transitions
    resolve_model = transitions.split("class TransitionResolveRequest(BaseModel):", 1)[1].split("\n\n", 1)[0]
    for name in ("transition_id", "option_id", "journey_id", "context_variables", "app_id", "user_id",
                 "build_registry_id", "source_chat_id"):
        assert f"    {name}:" in resolve_model, name
    summary = (ROOT / "factory_app" / "workflows" / "AppReview" / "ui" / "AppReview" / "AppReviewSummary.jsx").read_text(
        encoding="utf-8",
    )
    assert "/api/studio/build/artifacts/" in summary and "/promote" in summary


def test_the_workbench_answer_is_its_declared_continue_action() -> None:
    tools = yaml.safe_load((ROOT / "factory_app" / "workflows" / "AppGenerator" / "tools.yaml").read_text(encoding="utf-8"))
    [workbench] = [tool for tool in tools["tools"] if (tool.get("ui") or {}).get("component") == driver.WORKBENCH_COMPONENT]
    actions = {action["id"]: action for action in workbench["ui_contract"]["actions_schema"]}
    assert actions[driver.WORKBENCH_RESPONSE["action"]].get("approved") is True


def test_every_validation_and_review_agent_that_hands_to_the_user_is_kept_from_free_text() -> None:
    workflows = ("ValueEngine", "ThemeCapture", "DesignDocs", "SubscriptionContractDesigner", "AgentGenerator",
                 "AppGenerator", "SecurityReadiness", "AppReview")
    agents: set[tuple[str, str]] = set()
    checkers: set[tuple[str, str]] = set()
    for workflow in workflows:
        folder = ROOT / "factory_app" / "workflows" / workflow
        names = {agent["name"] for agent in yaml.safe_load((folder / "agents.yaml").read_text(encoding="utf-8"))["agents"]}
        agents.update((workflow, name) for name in names)
        rules = yaml.safe_load((folder / "transition_graph.yaml").read_text(encoding="utf-8")).get("transition_rules") or []
        to_user = {rule["source_agent"] for rule in rules if rule.get("target_agent") == "user"}
        checkers.update(
            (workflow, name) for name in to_user
            if name.endswith(("ValidationAgent", "QualityAgent", "ReviewAgent"))
        )
    assert driver.NO_FREE_TEXT_AGENTS <= agents
    assert checkers <= driver.NO_FREE_TEXT_AGENTS
