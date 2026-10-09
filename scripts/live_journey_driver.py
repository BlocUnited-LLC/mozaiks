"""Drive one Mozaiks build journey, idea to promotion, as a plain HTTP/WebSocket client.

The driver uses only the running host's existing APIs, the ones the chat UI
uses. It starts the journey through the host's entry routes, follows every
step over the workflow WebSocket, answers the pauses its prompt file scripts,
promotes the reviewed build through Studio's promote endpoint, and stops on
every terminal event.

It has no execution, retry, promotion-policy or lifecycle authority. It never
restarts or resumes a run, never resends a refused request, never cancels
server work, and imports nothing from ``mozaiksai`` or ``factory_app``.
Stopping the driver does not stop a server-side run: stop the host to end one.

Usage::

    python scripts/live_journey_driver.py \\
        --base-url http://127.0.0.1:8123 --app-id <execution app id> \\
        --user-id <user id> --build-registry-id <registered build target> \\
        --prompt-file journey.json --pricing-file usage-pricing.generated.json \\
        --evidence-dir evidence/run-1 --max-seconds 1800 --max-usd 0.50

With sign-in enabled, set ``MOZAIKS_DRIVER_ACCESS_TOKEN`` to a bearer token
issued to ``--user-id``. With sign-in off, the host trusts the user id the
driver sends, and Studio's promote endpoint acts for the host's default user
(``MOZAIKS_DEFAULT_USER_ID``), so ``--user-id`` must be that user.

The pricing file uses the ``mozaiks.usage_pricing.v1`` catalog shape
(``mozaiksai/core/usage/catalogs/usage-pricing.generated.json`` in the
installed package). Usage from a model it cannot price stops the run, because
the spend limit could not be enforced.

Prompt file (JSON, schema ``mozaiks.journey_script.v1``)::

    {
      "schema_version": "mozaiks.journey_script.v1",
      "journey_id": "build",
      "entry_transition": "app_type_selector",
      "prompt": "The idea. It answers the first pause of the journey.",
      "transition_options": {"app_type_selector": "greenfield_app",
                             "coding_journey_selector": "autonomous"},
      "replies": [{"workflow": "ThemeCapture", "agent": "ThemeInterviewAgent",
                   "text": "A calm blue brand for small teams."}],
      "ui_responses": [{"workflow": "ValueEngine", "component": "ConceptBlueprint",
                        "response": {"status": "submitted", "action": "approve",
                                     "approved": true}}]
    }

- Name exactly one entry. ``entry_transition`` resolves the journey's first
  transition with ``POST /api/transitions/resolve``, which needs a build target
  that already has a build session. ``entry_workflow`` (for example
  ``"ValueEngine"``) starts that workflow through Studio's
  ``POST /api/workflows/trigger``, which also opens a build for a draft target.
- Each reply answers one pause from that workflow's agent, in file order, and
  each UI response answers one request from that workflow's component. A pause
  or request with nothing scripted stops the run as ``human_assist_needed``:
  the driver has no fallback text and never answers with a generic "proceed".
- Validation and review agents never receive text from the driver. The one
  exception is the AppReview promotion signal below.
- AppWorkbench is answered by the driver with its "Continue" action.
- When AppReview's ReviewAgent pauses, the driver calls the promote endpoint
  for the reviewed artifact and, only after a 200 that reports the promotion,
  sends ``PROMOTION_SIGNAL`` once.
- A transition with options needs a ``transition_options`` entry; one without
  options (such as ``app_review``) resolves without one.
- A UI response value ``{"$env": "NAME"}`` is read from the environment when it
  is sent and is logged as the reference, never as the value.

Evidence written to ``--evidence-dir``:

- ``journey-websocket.jsonl``: every frame received and sent
- ``journey-http.jsonl``: every HTTP request and response
- ``transitions.json``: the journey steps and transitions observed
- ``exit-reason.json``: why the driver stopped, spend and elapsed time

Exit codes: 0 the journey completed; 1 the journey failed or stopped on a
terminal event; 2 invalid invocation, prompt file or pricing; 3 a pause needed
a human; 4 the wall-clock or spend limit was reached.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import contextlib
import hashlib
import json
import os
import sys
import time
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit

import httpx
import websockets
from websockets.exceptions import ConnectionClosed, InvalidHandshake

SCRIPT_SCHEMA_VERSION = "mozaiks.journey_script.v1"
TRANSITIONS_SCHEMA_VERSION = "mozaiks.journey_driver.transitions.v1"
EXIT_SCHEMA_VERSION = "mozaiks.journey_driver.exit.v1"
ACCESS_TOKEN_ENV = "MOZAIKS_DRIVER_ACCESS_TOKEN"
# The host's WebSocket bearer subprotocol (mozaiksai.core.auth.websocket_auth).
WS_BEARER_SUBPROTOCOL = "mozaiks.bearer.v1"

EXIT_COMPLETED = 0
EXIT_FAILED = 1
EXIT_INVALID = 2
EXIT_HUMAN_ASSIST = 3
EXIT_LIMIT = 4

WEBSOCKET_FILE = "journey-websocket.jsonl"
HTTP_FILE = "journey-http.jsonl"
TRANSITIONS_FILE = "transitions.json"
EXIT_REASON_FILE = "exit-reason.json"

# Agents whose turns are checks, not conversations. Free text from an
# unattended client only re-runs the check (a scripted "Confirmed. Proceed"
# looped AppValidationAgent 13 times), so a pause from one of them stops the run.
NO_FREE_TEXT_AGENTS = frozenset({
    ("AppGenerator", "AppValidationAgent"),
    ("AppGenerator", "AppUIQualityAgent"),
    ("AppGenerator", "ModuleContractQualityAgent"),
    ("AppGenerator", "ModuleRuntimeQualityAgent"),
    ("SecurityReadiness", "SecurityReadinessAgent"),
    ("AppReview", "ReviewAgent"),
})
REVIEW_WORKFLOW = "AppReview"
REVIEW_AGENT = "ReviewAgent"
WORKBENCH_COMPONENT = "AppWorkbench"
REVIEW_SUMMARY_COMPONENT = "AppReviewWorkspace"
INPUT_REQUEST_COMPONENT = "UserInputRequest"
# The AppWorkbench "Continue" action, as the chat UI sends it.
WORKBENCH_RESPONSE: dict[str, Any] = {
    "status": "submitted",
    "action": "download_complete",
    "approved": True,
    "download_accepted": True,
    "errors": [],
}
# AppReviewSummary's Promote button calls the promote endpoint and tells the
# agent nothing, so the review only ends once the user says it happened. This
# is the driver's whole message: the outcome the promote endpoint returned.
PROMOTION_SIGNAL = (
    "Promotion confirmed: artifact version {artifact_version_id} of build "
    "{build_registry_id} was promoted and the app is active."
)
# What AG2 reports when asked to resume a channel that never existed: the host
# took a session that had not run yet for an interrupted run (#642, PR #816).
FRESH_SESSION_ERROR = "ag2_network_channel_not_found"
ERROR_REASONS = {
    "WORKFLOW_EXECUTION_FAILED": "workflow_execution_failed",
    "WORKFLOW_SESSION_TERMINAL": "workflow_session_terminal",
    "JOURNEY_ADVANCE_FAILED": "journey_advance_failed",
    "WORKFLOW_PREREQS_NOT_MET": "workflow_prerequisites_not_met",
}

# A pause is announced before the server releases the run; an answer inside
# that window is refused as CHAT_BUSY, so the driver waits before answering.
REPLY_SETTLE_SECONDS = 2.0
# How long a completed step may wait for the next step or the journey's end.
HANDOFF_WINDOW_SECONDS = 30.0
SESSION_POLL_SECONDS = 1.0
HTTP_TIMEOUT_SECONDS = 60.0
WS_OPEN_TIMEOUT_SECONDS = 30.0
WS_MAX_FRAME_BYTES = 64 * 1024 * 1024
REVIEW_FIELDS = (
    "artifact_version_id",
    "build_registry_id",
    "can_promote",
    "app_bundle_acceptance_status",
    "app_validation_status",
    "app_validation_strategy_used",
    "integration_tests_passed",
    "security_readiness_summary",
)
WORKBENCH_FIELDS = (
    "stage",
    "artifact_version_id",
    "build_registry_id",
    "target_app_id",
    "app_bundle_acceptance_status",
    "app_validation_status",
    "app_validation_strategy_used",
    "integration_tests_passed",
    "files",
)


class InvalidInput(Exception):
    """The prompt file or the pricing file cannot drive a run."""


class JourneyStop(Exception):
    """The journey ended: the driver stops and reports why."""

    def __init__(self, exit_code: int, reason: str, detail: str, **evidence: Any) -> None:
        super().__init__(detail)
        self.exit_code = exit_code
        self.reason = reason
        self.detail = detail
        self.evidence = evidence


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# --------------------------------------------------------------------------- prompt file


@dataclass
class JourneyScript:
    """A validated prompt file. Replies and UI responses are consumed as used."""

    path: str
    sha256: str
    journey_id: str
    entry_transition: str | None
    entry_workflow: str | None
    prompt: str
    transition_options: dict[str, str | None]
    replies: dict[tuple[str, str], deque[str]]
    ui_responses: dict[tuple[str, str], deque[dict[str, Any]]]

    def unused(self) -> dict[str, Any]:
        return {
            "replies": {f"{wf}/{agent}": len(q) for (wf, agent), q in self.replies.items() if q},
            "ui_responses": {f"{wf}/{name}": len(q) for (wf, name), q in self.ui_responses.items() if q},
        }


_SCRIPT_KEYS = frozenset({
    "schema_version", "journey_id", "entry_transition", "entry_workflow", "prompt",
    "transition_options", "replies", "ui_responses",
})


def _text(value: Any, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InvalidInput(f"{where} must be a non-empty string")
    return value.strip()


def _items(value: Any, where: str, keys: frozenset[str]) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise InvalidInput(f"{where} must be a list")
    for index, item in enumerate(value):
        if not isinstance(item, dict) or set(item) != keys:
            raise InvalidInput(f"{where}[{index}] must be an object with exactly {sorted(keys)}")
    return value


def _env_reference(value: Any) -> str | None:
    if isinstance(value, dict) and set(value) == {"$env"}:
        name = value["$env"]
        if isinstance(name, str) and name.strip():
            return name.strip()
        raise InvalidInput("an $env reference must name an environment variable")
    return None


def _env_references(value: Any) -> list[str]:
    name = _env_reference(value)
    if name is not None:
        return [name]
    if isinstance(value, dict):
        return [ref for item in value.values() for ref in _env_references(item)]
    if isinstance(value, list):
        return [ref for item in value for ref in _env_references(item)]
    return []


def resolve_env_references(value: Any) -> Any:
    """Return ``value`` with each ``{"$env": NAME}`` replaced by that variable's value."""
    name = _env_reference(value)
    if name is not None:
        return os.environ[name]
    if isinstance(value, dict):
        return {key: resolve_env_references(item) for key, item in value.items()}
    if isinstance(value, list):
        return [resolve_env_references(item) for item in value]
    return value


def load_script(path: Path) -> JourneyScript:
    """Read and validate a prompt file; raise InvalidInput on any problem."""
    try:
        raw = path.read_bytes()
        data = json.loads(raw.decode("utf-8"))
    except OSError as exc:
        raise InvalidInput(f"cannot read the prompt file: {exc}") from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InvalidInput(f"the prompt file is not UTF-8 JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise InvalidInput("the prompt file must hold a JSON object")
    unknown = sorted(set(data) - _SCRIPT_KEYS)
    if unknown:
        raise InvalidInput(f"the prompt file has unknown keys: {unknown}")
    if data.get("schema_version") != SCRIPT_SCHEMA_VERSION:
        raise InvalidInput(f"schema_version must be {SCRIPT_SCHEMA_VERSION!r}")

    options_raw = data.get("transition_options") or {}
    if not isinstance(options_raw, dict):
        raise InvalidInput("transition_options must map a transition id to an option id")
    options: dict[str, str | None] = {}
    for transition_id, option_id in options_raw.items():
        key = _text(transition_id, "a transition_options key")
        options[key] = None if option_id is None else _text(option_id, f"transition_options[{key!r}]")

    replies: dict[tuple[str, str], deque[str]] = {}
    for index, item in enumerate(_items(data.get("replies"), "replies", frozenset({"workflow", "agent", "text"}))):
        workflow = _text(item["workflow"], f"replies[{index}].workflow")
        agent = _text(item["agent"], f"replies[{index}].agent")
        if (workflow, agent) in NO_FREE_TEXT_AGENTS:
            raise InvalidInput(
                f"replies[{index}] targets {workflow}/{agent}: the driver never sends free text "
                "to validation or review agents"
            )
        replies.setdefault((workflow, agent), deque()).append(_text(item["text"], f"replies[{index}].text"))

    ui_responses: dict[tuple[str, str], deque[dict[str, Any]]] = {}
    missing_env: list[str] = []
    for index, item in enumerate(
        _items(data.get("ui_responses"), "ui_responses", frozenset({"workflow", "component", "response"}))
    ):
        workflow = _text(item["workflow"], f"ui_responses[{index}].workflow")
        component = _text(item["component"], f"ui_responses[{index}].component")
        if component == WORKBENCH_COMPONENT:
            raise InvalidInput(f"ui_responses[{index}]: the driver answers {WORKBENCH_COMPONENT} itself")
        if component == REVIEW_SUMMARY_COMPONENT:
            raise InvalidInput(
                f"ui_responses[{index}]: {REVIEW_SUMMARY_COMPONENT} takes no response; "
                "the driver promotes through the promote endpoint"
            )
        response = item["response"]
        if not isinstance(response, dict) or not response:
            raise InvalidInput(f"ui_responses[{index}].response must be a non-empty object")
        missing_env.extend(name for name in _env_references(response) if name not in os.environ)
        ui_responses.setdefault((workflow, component), deque()).append(response)
    if missing_env:
        raise InvalidInput(f"the prompt file reads unset environment variables: {sorted(set(missing_env))}")
    if ("entry_transition" in data) == ("entry_workflow" in data):
        raise InvalidInput("the prompt file must name exactly one of entry_transition and entry_workflow")
    entry_transition = data.get("entry_transition")
    entry_workflow = data.get("entry_workflow")

    return JourneyScript(
        path=str(path),
        sha256=hashlib.sha256(raw).hexdigest(),
        journey_id=_text(data.get("journey_id"), "journey_id"),
        entry_transition=None if entry_transition is None else _text(entry_transition, "entry_transition"),
        entry_workflow=None if entry_workflow is None else _text(entry_workflow, "entry_workflow"),
        prompt=_text(data.get("prompt"), "prompt"),
        transition_options=options,
        replies=replies,
        ui_responses=ui_responses,
    )


# --------------------------------------------------------------------------- spend


@dataclass(frozen=True)
class Rates:
    input_usd_per_token: float
    output_usd_per_token: float
    cached_input_usd_per_token: float


def _rate(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        rate = float(value)
    except (TypeError, ValueError):
        return None
    return rate if rate >= 0 else None


def load_pricing(path: Path) -> dict[str, Rates]:
    """Read per-model rates from a usage-pricing catalog (``*_per_1m_usd`` fields)."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise InvalidInput(f"cannot read the pricing file: {exc}") from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InvalidInput(f"the pricing file is not UTF-8 JSON: {exc}") from exc
    models = data.get("models", data) if isinstance(data, dict) else None
    if not isinstance(models, dict):
        raise InvalidInput("the pricing file must map model names to rates")
    prices: dict[str, Rates] = {}
    for name, row in models.items():
        if not isinstance(row, dict):
            continue
        input_rate = _rate(row.get("input_per_1m_usd"))
        output_rate = _rate(row.get("output_per_1m_usd"))
        if input_rate is None or output_rate is None:
            continue
        cached_rate = _rate(row.get("cached_input_per_1m_usd"))
        prices[str(name).strip().lower()] = Rates(
            input_rate / 1_000_000,
            output_rate / 1_000_000,
            (input_rate if cached_rate is None else cached_rate) / 1_000_000,
        )
    if not prices:
        raise InvalidInput("the pricing file has no model with input_per_1m_usd and output_per_1m_usd")
    return prices


def _count(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


class SpendMeter:
    """Prices each ``chat.usage_delta`` the host reports and totals the journey."""

    def __init__(self, prices: Mapping[str, Rates], limit_usd: float) -> None:
        self.prices = prices
        self.limit_usd = limit_usd
        self.usd = 0.0
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.cached_prompt_tokens = 0
        self.by_model: dict[str, dict[str, float]] = {}

    def rates_for(self, model: Any) -> Rates | None:
        key = str(model or "").strip().lower()
        if not key:
            return None
        return self.prices.get(key) or self.prices.get(key.rsplit("/", 1)[-1])

    def add(self, delta: Mapping[str, Any]) -> float:
        """Record one usage delta and return its cost; unpriceable usage stops the run."""
        model = str(delta.get("model_name") or "").strip()
        prompt = _count(delta.get("prompt_tokens"))
        completion = _count(delta.get("completion_tokens"))
        cached = min(_count(delta.get("cached_prompt_tokens")), prompt)
        self.prompt_tokens += prompt
        self.completion_tokens += completion
        self.cached_prompt_tokens += cached
        rates = self.rates_for(model)
        if rates is None:
            raise JourneyStop(
                EXIT_INVALID,
                "spend_unpriceable",
                f"The host reported usage for model {model or '(unnamed)'!r}, which the pricing "
                "file cannot price, so the spend limit cannot be enforced.",
                usage_delta=dict(delta),
            )
        cost = (
            (prompt - cached) * rates.input_usd_per_token
            + cached * rates.cached_input_usd_per_token
            + completion * rates.output_usd_per_token
        )
        self.usd += cost
        row = self.by_model.setdefault(model, {"usd": 0.0, "prompt_tokens": 0, "completion_tokens": 0})
        row["usd"] += cost
        row["prompt_tokens"] += prompt
        row["completion_tokens"] += completion
        return cost

    def over_limit(self) -> bool:
        return self.usd > self.limit_usd

    def as_dict(self) -> dict[str, Any]:
        return {
            "usd": round(self.usd, 6),
            "limit_usd": self.limit_usd,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "cached_prompt_tokens": self.cached_prompt_tokens,
            "by_model": {
                name: {**row, "usd": round(row["usd"], 6)} for name, row in self.by_model.items()
            },
        }


# --------------------------------------------------------------------------- evidence


class Evidence:
    """Writes what the driver observed; the JSON files are rewritten atomically."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self._started = time.monotonic()
        self._websocket = (directory / WEBSOCKET_FILE).open("a", encoding="utf-8")
        self._http = (directory / HTTP_FILE).open("a", encoding="utf-8")

    def elapsed(self) -> float:
        return time.monotonic() - self._started

    def _line(self, handle: Any, record: dict[str, Any]) -> None:
        handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        handle.flush()

    def websocket(self, direction: str, frame: Any, **where: Any) -> None:
        self._line(self._websocket, {
            "at": _now(), "elapsed_seconds": round(self.elapsed(), 3), "direction": direction,
            **where, "frame": frame,
        })

    def http(self, record: dict[str, Any]) -> None:
        self._line(self._http, {"at": _now(), "elapsed_seconds": round(self.elapsed(), 3), **record})

    def write_json(self, name: str, payload: dict[str, Any]) -> None:
        target = self.directory / name
        temporary = self.directory / f".{name}.tmp"
        temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        os.replace(temporary, target)

    def close(self) -> None:
        self._websocket.close()
        self._http.close()


# --------------------------------------------------------------------------- journey


@dataclass(frozen=True)
class DriverConfig:
    base_url: str
    app_id: str
    user_id: str
    build_registry_id: str
    max_seconds: float
    max_usd: float
    access_token: str | None = None


@dataclass
class Launch:
    chat_id: str
    workflow: str
    websocket_url: str | None
    entered_via: dict[str, Any]


@dataclass
class Step:
    index: int
    workflow: str
    chat_id: str
    entered_via: dict[str, Any]
    started_at: str
    socket: int
    ended_at: str | None = None
    outcome: str = "running"
    chat_meta: dict[str, Any] | None = None
    agents: list[str] = field(default_factory=list)
    last_agent_message: dict[str, str] = field(default_factory=dict)
    pauses: list[dict[str, Any]] = field(default_factory=list)
    ui_tools: list[dict[str, Any]] = field(default_factory=list)
    run_completes: list[dict[str, Any]] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)
    messages_sent: int = 0
    usage_usd: float = 0.0
    workbench: list[dict[str, Any]] = field(default_factory=list)
    review: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "workflow": self.workflow,
            "chat_id": self.chat_id,
            "entered_via": self.entered_via,
            "socket": self.socket,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "outcome": self.outcome,
            "chat_meta": self.chat_meta,
            "agents": self.agents,
            "pauses": self.pauses,
            "ui_tools": self.ui_tools,
            "run_completes": self.run_completes,
            "errors": self.errors,
            "messages_sent": self.messages_sent,
            "usage_usd": round(self.usage_usd, 6),
            "workbench": self.workbench,
            "review": self.review,
        }


@dataclass
class Outcome:
    exit_code: int
    reason: str
    detail: str
    evidence: dict[str, Any] = field(default_factory=dict)


_JOURNEY_DONE = object()


def _run_status(data: Mapping[str, Any]) -> str:
    raw = data.get("status")
    text = str(raw).strip().lower() if raw is not None else ""
    if text == "failed" or data.get("failed") is True:
        return "failed"
    if text == "paused" or data.get("awaiting_user_input") is True:
        return "paused"
    if text in {"completed", "1"}:
        return "completed"
    return text or "missing"


def _short(value: Any, limit: int = 600) -> str:
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    return text if len(text) <= limit else text[:limit] + "..."


class JourneyDriver:
    """Follows one journey over the host's HTTP and WebSocket APIs."""

    def __init__(
        self,
        config: DriverConfig,
        script: JourneyScript,
        meter: SpendMeter,
        evidence: Evidence,
    ) -> None:
        self.config = config
        self.script = script
        self.meter = meter
        self.evidence = evidence
        headers = {"Authorization": f"Bearer {config.access_token}"} if config.access_token else {}
        self.http = httpx.AsyncClient(base_url=config.base_url, headers=headers, timeout=HTTP_TIMEOUT_SECONDS)
        self.steps: list[Step] = []
        self.transitions: list[dict[str, Any]] = []
        self.human_assists: list[dict[str, Any]] = []
        self.promotion: dict[str, Any] | None = None
        self.prompt_sent_to: dict[str, Any] | None = None
        self.session_state: dict[str, Any] | None = None
        self.terminal_event: dict[str, Any] | None = None
        self.waiting_for = "the entry transition to resolve"
        self.socket_index = 0
        self.ws: Any = None
        self.awaiting_handoff_since: float | None = None
        self.last_poll = 0.0
        self.completed_chats: set[str] = set()
        self.pause_agent: str | None = None

    # ------------------------------------------------------------------ lifecycle

    async def run(self) -> Outcome:
        task = asyncio.create_task(self._journey())
        try:
            done, _ = await asyncio.wait({task}, timeout=self.config.max_seconds)
            if not done:
                task.cancel()
                with contextlib.suppress(BaseException):
                    await task
                return Outcome(
                    EXIT_LIMIT,
                    "wall_clock_limit",
                    f"The journey did not finish within {self.config.max_seconds:g} seconds; "
                    f"the driver was waiting for {self.waiting_for}.",
                )
            exc = task.exception()
            if exc is None:
                return Outcome(
                    EXIT_COMPLETED,
                    "journey_completed",
                    "Every journey step completed and the host reports the journey complete.",
                )
            if isinstance(exc, JourneyStop):
                return Outcome(exc.exit_code, exc.reason, exc.detail, exc.evidence)
            return Outcome(EXIT_FAILED, "driver_error", f"{type(exc).__name__}: {exc}")
        finally:
            if self.ws is not None:
                with contextlib.suppress(Exception):
                    await self.ws.close()
            await self.http.aclose()

    async def _journey(self) -> None:
        launch: Launch | None
        if self.script.entry_workflow is not None:
            launch = await self._trigger_workflow(self.script.entry_workflow)
        else:
            assert self.script.entry_transition is not None
            launch = await self._resolve_transition(self.script.entry_transition, source_chat_id=None, context={})
        while launch is not None:
            launch = await self._follow(launch)

    @property
    def step(self) -> Step:
        return self.steps[-1]

    def _begin_step(self, launch: Launch) -> None:
        if self.steps and self.step.outcome == "running":
            self.step.outcome = "left_running"
            self.step.ended_at = _now()
        self.steps.append(Step(
            index=len(self.steps),
            workflow=launch.workflow,
            chat_id=launch.chat_id,
            entered_via=launch.entered_via,
            started_at=_now(),
            socket=self.socket_index,
        ))
        self.pause_agent = None
        self.awaiting_handoff_since = None
        self.waiting_for = f"the first turn of {launch.workflow}"
        self.write_transitions()

    def _step_for(self, chat_id: Any) -> Step:
        for step in reversed(self.steps):
            if step.chat_id == chat_id:
                return step
        return self.step

    def write_transitions(self) -> None:
        self.evidence.write_json(TRANSITIONS_FILE, {
            "schema_version": TRANSITIONS_SCHEMA_VERSION,
            "journey_id": self.script.journey_id,
            "steps": [step.as_dict() for step in self.steps],
            "transitions": self.transitions,
        })

    # ------------------------------------------------------------------ HTTP

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        body: Any = None,
    ) -> tuple[int, Any]:
        record: dict[str, Any] = {"method": method, "path": path, "params": params, "request": body}
        try:
            response = await self.http.request(method, path, params=params, json=body)
        except httpx.HTTPError as exc:
            self.evidence.http({**record, "error": f"{type(exc).__name__}: {exc}"})
            raise JourneyStop(
                EXIT_FAILED, "http_error", f"{method} {path} did not complete: {type(exc).__name__}: {exc}",
            ) from exc
        try:
            payload: Any = response.json()
        except ValueError:
            payload = response.text
        self.evidence.http({**record, "status": response.status_code, "response": payload})
        return response.status_code, payload

    async def _require_json(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        status, payload = await self._request(method, path, **kwargs)
        if not 200 <= status < 300 or not isinstance(payload, dict):
            raise JourneyStop(
                EXIT_FAILED,
                "http_refused",
                f"{method} {path} answered {status}: {_short(payload)}",
                status=status,
                response=payload,
            )
        return payload

    async def _choose_option(self, transition_id: str) -> str | None:
        if transition_id in self.script.transition_options:
            return self.script.transition_options[transition_id]
        definition = await self._require_json("GET", f"/api/transitions/{quote(transition_id, safe='')}")
        options = [opt.get("id") for opt in definition.get("options") or [] if isinstance(opt, dict)]
        if options:
            raise self._human_assist(
                "unscripted_transition_choice",
                f"Transition {transition_id} offers options {options} and the prompt file chooses none.",
                transition_id=transition_id,
                options=options,
            )
        return None

    async def _resolve_transition(
        self,
        transition_id: str,
        *,
        source_chat_id: str | None,
        context: dict[str, Any],
    ) -> Launch:
        journey_id: str | None = self.script.journey_id
        while True:
            self.waiting_for = f"transition {transition_id} to resolve"
            option_id = await self._choose_option(transition_id)
            body: dict[str, Any] = {
                "transition_id": transition_id,
                "option_id": option_id,
                "context_variables": context,
                "app_id": self.config.app_id,
                "user_id": self.config.user_id,
            }
            if source_chat_id is None:
                # The journey's entry: bind the build target and name the sequence.
                body["journey_id"] = journey_id
                body["build_registry_id"] = self.config.build_registry_id
            else:
                body["source_chat_id"] = source_chat_id
            response = await self._require_json("POST", "/api/transitions/resolve", body=body)
            self.transitions.append({
                "kind": "resolve",
                "at": _now(),
                "transition_id": transition_id,
                "option_id": option_id,
                "source_chat_id": source_chat_id,
                "resolution_type": response.get("resolution_type"),
                "chat_id": response.get("chat_id"),
                "workflow_id": response.get("workflow_id"),
                "journey_id": response.get("journey_id"),
            })
            kind = response.get("resolution_type")
            if kind == "transition":
                transition_id = str(
                    response.get("next_transition_id") or (response.get("transition") or {}).get("id") or ""
                )
                if not transition_id:
                    raise JourneyStop(EXIT_FAILED, "protocol_error", "A transition resolved to an unnamed transition.")
                context = dict(response.get("context_variables") or {})
                journey_id = response.get("journey_id") or journey_id
                continue
            chat_id = str(response.get("chat_id") or "")
            workflow = str(response.get("workflow_id") or "")
            websocket_url = str(response.get("websocket_url") or "")
            if kind not in {"workflow", "chat_session"} or not (chat_id and workflow and websocket_url):
                raise JourneyStop(
                    EXIT_FAILED,
                    "protocol_error",
                    f"Transition {transition_id} returned no workflow launch: {_short(response)}",
                )
            return Launch(chat_id, workflow, websocket_url, {
                "kind": "transition", "transition_id": transition_id, "option_id": option_id,
            })

    async def _trigger_workflow(self, workflow: str) -> Launch:
        """Start the journey at ``workflow`` for the build target, as Studio's entry routes do.

        A transition can only resume a build target that already has a build
        session; this route can open one for a draft target.
        """
        self.waiting_for = f"{workflow} to start"
        response = await self._require_json("POST", "/api/workflows/trigger", body={
            "workflow_id": workflow,
            "trigger_source": "route",
            "journey_id": self.script.journey_id,
            "build_registry_id": self.config.build_registry_id,
            "app_id": self.config.app_id,
            "user_id": self.config.user_id,
            "context_variables": {},
        })
        chat_id = str(response.get("chat_id") or "")
        workflow_id = str(response.get("workflow_id") or "")
        websocket_url = str(response.get("websocket_url") or "")
        self.transitions.append({
            "kind": "trigger",
            "at": _now(),
            "workflow": workflow,
            "chat_id": chat_id or None,
            "workflow_id": workflow_id or None,
            "journey_id": response.get("journey_id"),
            "rerouted_by_dependency": response.get("rerouted_by_dependency"),
        })
        if not (chat_id and workflow_id and websocket_url):
            raise JourneyStop(
                EXIT_FAILED, "protocol_error", f"Starting {workflow} returned no workflow launch: {_short(response)}",
            )
        return Launch(chat_id, workflow_id, websocket_url, {"kind": "trigger", "workflow": workflow})

    async def _session_state(self) -> dict[str, Any]:
        payload = await self._require_json("GET", "/api/session/state", params={
            "app_id": self.config.app_id,
            "user_id": self.config.user_id,
            "source_chat_id": self.step.chat_id,
        })
        state = payload.get("session_state")
        self.session_state = state if isinstance(state, dict) else {}
        return self.session_state

    # ------------------------------------------------------------------ WebSocket

    def _websocket_url(self, path: str) -> str:
        if not path.startswith("/ws/"):
            raise JourneyStop(EXIT_FAILED, "protocol_error", f"The host returned an unexpected WebSocket path {path!r}.")
        base = urlsplit(self.config.base_url)
        scheme = "wss" if base.scheme == "https" else "ws"
        return urlunsplit((scheme, base.netloc, base.path.rstrip("/") + path, "", ""))

    def _subprotocols(self) -> list[str] | None:
        token = self.config.access_token
        if not token:
            return None
        encoded = base64.urlsafe_b64encode(token.encode("utf-8")).decode("ascii").rstrip("=")
        return [WS_BEARER_SUBPROTOCOL, encoded]

    def _where(self) -> dict[str, Any]:
        current = self.steps[-1] if self.steps else None
        return {
            "socket": self.socket_index,
            "chat_id": current.chat_id if current else None,
            "workflow": current.workflow if current else None,
        }

    async def _follow(self, launch: Launch) -> Launch | None:
        """Follow one WebSocket until the journey moves to another socket or ends."""
        assert launch.websocket_url is not None
        url = self._websocket_url(launch.websocket_url)
        self.socket_index += 1
        self._begin_step(launch)
        try:
            self.ws = await websockets.connect(
                url,
                subprotocols=self._subprotocols(),  # type: ignore[arg-type]
                open_timeout=WS_OPEN_TIMEOUT_SECONDS,
                close_timeout=5,
                max_size=WS_MAX_FRAME_BYTES,
                ping_interval=None,
            )
        except InvalidHandshake as exc:
            raise JourneyStop(
                EXIT_FAILED, "websocket_refused", f"The host refused the {launch.workflow} WebSocket: {exc}",
            ) from exc
        except (OSError, TimeoutError) as exc:
            raise JourneyStop(
                EXIT_FAILED,
                "websocket_unreachable",
                f"Could not open the {launch.workflow} WebSocket: {type(exc).__name__}: {exc}",
            ) from exc
        try:
            while True:
                try:
                    frame = await self._receive()
                except JourneyStop as stop:
                    # A socket closed after the last step is fine if the journey is complete.
                    if stop.reason == "websocket_closed" and self.awaiting_handoff_since is not None:
                        if await self._poll_handoff(final=True) is _JOURNEY_DONE:
                            return None
                    raise
                if frame is not None:
                    result = await self._handle(frame)
                    if isinstance(result, Launch):
                        return result
                if self.awaiting_handoff_since is not None and (frame is None or self._poll_due()):
                    if await self._poll_handoff() is _JOURNEY_DONE:
                        return None
        finally:
            with contextlib.suppress(Exception):
                await self.ws.close()
            self.ws = None

    async def _receive(self) -> dict[str, Any] | None:
        """The next frame, or None when a completed step has been quiet for a poll interval."""
        try:
            if self.awaiting_handoff_since is None:
                raw = await self.ws.recv()
            else:
                raw = await asyncio.wait_for(self.ws.recv(), timeout=SESSION_POLL_SECONDS)
        except TimeoutError:
            return None
        except ConnectionClosed as exc:
            close = exc.rcvd or exc.sent
            code = close.code if close is not None else None
            reason = close.reason if close is not None else ""
            raise JourneyStop(
                EXIT_FAILED,
                "websocket_closed",
                f"The host closed the {self.step.workflow} WebSocket (code {code}{': ' + reason if reason else ''}).",
                close_code=code,
                close_reason=reason,
            ) from exc
        text = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)
        try:
            frame = json.loads(text)
        except json.JSONDecodeError:
            frame = None
        self.evidence.websocket("received", frame if frame is not None else text, **self._where())
        if not isinstance(frame, dict):
            raise JourneyStop(EXIT_FAILED, "protocol_error", f"The host sent a frame that is not a JSON object: {_short(text)}")
        return frame

    async def _send(self, frame: dict[str, Any], *, logged: dict[str, Any] | None = None) -> None:
        self.evidence.websocket("sent", logged if logged is not None else frame, **self._where())
        try:
            await self.ws.send(json.dumps(frame))
        except ConnectionClosed as exc:
            raise JourneyStop(EXIT_FAILED, "websocket_closed", f"The host closed the WebSocket while the driver was sending: {exc}") from exc

    async def _send_text(self, text: str) -> None:
        await asyncio.sleep(REPLY_SETTLE_SECONDS)
        await self._send({
            "type": "user.input.submit",
            "chat_id": self.step.chat_id,
            "text": text,
            "context": {"source": "live_journey_driver", "conversation_mode": "workflow"},
        })
        self.step.messages_sent += 1

    # ------------------------------------------------------------------ frames

    async def _handle(self, frame: dict[str, Any]) -> Any:
        frame_type = str(frame.get("type") or "")
        raw_data = frame.get("data")
        data: dict[str, Any] = raw_data if isinstance(raw_data, dict) else {}
        if frame_type == "ping":
            await self._send({"type": "client.pong", "chat_id": self.step.chat_id})
        elif frame_type == "chat_meta":
            self.step.chat_meta = {
                key: data.get(key) for key in ("chat_id", "workflow_name", "status", "run_history_count")
            }
        elif frame_type in {"error", "chat.error", "chat.failed"}:
            self._stop_on_error(frame_type, data, frame)
        elif frame_type in {"chat.text", "chat.stream_end", "chat.print"}:
            self._on_text(data)
        elif frame_type == "chat.usage_delta":
            self._on_usage(data)
        elif frame_type == "chat.tool_call":
            await self._on_tool_call(data)
        elif frame_type == "ack.tool_call_response":
            if data.get("status") != "accepted":
                self.terminal_event = frame
                raise JourneyStop(
                    EXIT_FAILED, "tool_response_rejected",
                    f"The host rejected the driver's response to UI tool {data.get('tool_call_id')}.",
                )
        elif frame_type == "chat.awaiting_reply":
            self.pause_agent = str(data.get("source_agent") or "") or None
        elif frame_type == "chat.run_complete":
            return await self._on_run_complete(data, frame)
        elif frame_type == "chat.context_switched":
            self._on_context_switched(data)
        elif frame_type == "chat.transition_requested":
            return await self._on_transition_requested(data)
        return None

    def _stop_on_error(self, frame_type: str, data: Mapping[str, Any], frame: dict[str, Any]) -> None:
        code = str(data.get("error_code") or data.get("code") or "")
        message = str(data.get("message") or "")
        self.step.errors.append({"type": frame_type, "error_code": code, "message": message, "at": _now()})
        self.step.outcome = "failed"
        self.terminal_event = frame
        if frame_type == "chat.failed":
            reason = "chat_failed"
        elif FRESH_SESSION_ERROR in f"{data.get('reason') or ''} {message}":
            reason = "fresh_session_resumed_as_interrupted_run"
        else:
            reason = ERROR_REASONS.get(code, "server_error")
        raise JourneyStop(
            EXIT_FAILED,
            reason,
            f"The host reported {frame_type}{' ' + code if code else ''} in {self.step.workflow}: {message or _short(data)}",
        )

    def _on_text(self, data: Mapping[str, Any]) -> None:
        agent = str(data.get("agent") or data.get("sender") or data.get("name") or "").strip()
        content = data.get("content") or data.get("full_content")
        if not agent or agent.lower() == "user" or data.get("_mozaiks_hide"):
            return
        step = self._step_for(data.get("chat_id"))
        if agent not in step.agents:
            step.agents.append(agent)
        if isinstance(content, str) and content.strip():
            step.last_agent_message[agent] = content.strip()[:2000]

    def _on_usage(self, data: Mapping[str, Any]) -> None:
        cost = self.meter.add(data)
        self._step_for(data.get("chat_id")).usage_usd += cost
        if self.meter.over_limit():
            raise JourneyStop(
                EXIT_LIMIT,
                "spend_limit",
                f"Estimated spend ${self.meter.usd:.4f} passed the ${self.config.max_usd:g} limit "
                f"during {self.step.workflow}.",
            )

    def _human_assist(self, kind: str, detail: str, **extra: Any) -> JourneyStop:
        step = self.steps[-1] if self.steps else None
        agent = extra.get("agent")
        record = {
            "kind": kind,
            "at": _now(),
            "workflow": step.workflow if step else None,
            "chat_id": step.chat_id if step else None,
            "detail": detail,
            "last_agent_message": step.last_agent_message.get(agent) if step and agent else None,
            **extra,
        }
        self.human_assists.append(record)
        return JourneyStop(EXIT_HUMAN_ASSIST, "human_assist_needed", detail, human_assist=record)

    def _next_free_text(self, agent: str) -> tuple[str, str]:
        """The text for a free-text request from ``agent``, or stop for a human."""
        workflow = self.step.workflow
        if (workflow, agent) in NO_FREE_TEXT_AGENTS:
            raise self._human_assist(
                "validation_or_review_agent",
                f"{workflow}/{agent} asked for a reply; the driver never sends free text to "
                "validation or review agents.",
                agent=agent,
            )
        if self.prompt_sent_to is None:
            self.prompt_sent_to = {"workflow": workflow, "agent": agent, "chat_id": self.step.chat_id}
            return self.script.prompt, "prompt"
        queue = self.script.replies.get((workflow, agent))
        if not queue:
            raise self._human_assist(
                "unscripted_pause",
                f"{workflow}/{agent or '(unnamed agent)'} asked for a reply and the prompt file has none left for it.",
                agent=agent,
            )
        return queue.popleft(), "reply"

    async def _on_tool_call(self, data: Mapping[str, Any]) -> None:
        step = self.step
        component = str(data.get("component_type") or data.get("tool_name") or "")
        tool_call_id = str(data.get("tool_call_id") or "")
        raw_payload = data.get("payload")
        payload: dict[str, Any] = raw_payload if isinstance(raw_payload, dict) else {}
        awaiting = bool(data.get("awaiting_response"))
        agent = str(data.get("agent") or data.get("agent_name") or "")
        # The declared actions are what a prompt-file response can choose from.
        actions = [action.get("id") for action in payload.get("actions") or [] if isinstance(action, dict)]
        entry: dict[str, Any] = {
            "component": component, "tool_call_id": tool_call_id, "awaiting_response": awaiting,
            "agent": agent or None, "actions": actions, "at": _now(),
        }
        step.ui_tools.append(entry)
        if component == REVIEW_SUMMARY_COMPONENT:
            step.review = {key: payload.get(key) for key in REVIEW_FIELDS}
        if not awaiting:
            return
        if not tool_call_id:
            raise JourneyStop(EXIT_FAILED, "protocol_error", f"UI tool {component} awaits a response but has no tool_call_id.")
        logged: dict[str, Any] | None = None
        interaction = str(data.get("interaction_type") or payload.get("interaction_type") or "")
        if component == WORKBENCH_COMPONENT:
            step.workbench.append({key: payload.get(key) for key in WORKBENCH_FIELDS})
            response: dict[str, Any] = dict(WORKBENCH_RESPONSE)
            entry["answered_with"] = "workbench_continue"
        elif interaction == "input_request" or component == INPUT_REQUEST_COMPONENT:
            text, answered_with = self._next_free_text(agent)
            response = {"status": "submitted", "text": text, "user_input": text, "user_response": text}
            entry["answered_with"] = answered_with
        else:
            queue = self.script.ui_responses.get((step.workflow, component))
            if not queue:
                raise self._human_assist(
                    "unscripted_ui_tool",
                    f"{step.workflow} asked for a {component or '(unnamed)'} response and the prompt "
                    "file has none left for it.",
                    component=component,
                    agent=agent or None,
                    actions=actions,
                    agent_message=_short(payload.get("agent_message") or "", 2000) or None,
                )
            scripted = queue.popleft()
            response = resolve_env_references(scripted)
            logged = {"type": "tool_call_response", "tool_call_id": tool_call_id, "response": scripted}
            entry["answered_with"] = "ui_response"
        await self._send({"type": "tool_call_response", "tool_call_id": tool_call_id, "response": response}, logged=logged)

    async def _on_run_complete(self, data: Mapping[str, Any], frame: dict[str, Any]) -> Any:
        chat_id = str(data.get("chat_id") or self.step.chat_id)
        step = self._step_for(chat_id)
        status = _run_status(data)
        record = {
            "status": status,
            "raw_status": data.get("status"),
            "reason": data.get("reason"),
            "agent": data.get("agent"),
            "error": data.get("error"),
            "close_reason": data.get("close_reason"),
            "at": _now(),
        }
        step.run_completes.append(record)
        if status == "failed":
            step.outcome = "failed"
            step.ended_at = _now()
            self.terminal_event = frame
            if FRESH_SESSION_ERROR in {data.get("error"), data.get("close_reason")}:
                raise JourneyStop(
                    EXIT_FAILED,
                    "fresh_session_resumed_as_interrupted_run",
                    f"The host treated {step.workflow}'s session, which had not run yet, as an "
                    f"interrupted run and tried to resume it ({FRESH_SESSION_ERROR}); no model was "
                    "called. This is the known #642 defect that PR #816 fixes; the driver does not "
                    "work around it.",
                    run_complete=record,
                    driver_messages_sent_to_chat=step.messages_sent,
                    agent_output_seen=bool(step.agents),
                    chat_meta=step.chat_meta,
                )
            raise JourneyStop(
                EXIT_FAILED,
                "run_failed",
                f"{step.workflow} failed: {data.get('error') or data.get('close_reason') or 'no reason given'}",
                run_complete=record,
            )
        if status == "paused":
            if chat_id != self.step.chat_id:
                raise JourneyStop(EXIT_FAILED, "protocol_error", f"Chat {chat_id} paused while the driver followed {self.step.chat_id}.")
            agent = str(data.get("agent") or self.pause_agent or "")
            await self._answer_pause(agent)
            return None
        if status == "completed":
            if chat_id in self.completed_chats:
                return None
            self.completed_chats.add(chat_id)
            step.outcome = "completed"
            step.ended_at = _now()
            if chat_id == self.step.chat_id:
                self.awaiting_handoff_since = time.monotonic()
                self.waiting_for = f"the journey to move on from {step.workflow}"
            self.write_transitions()
            return None
        self.terminal_event = frame
        raise JourneyStop(
            EXIT_FAILED, "unexpected_run_status",
            f"{step.workflow} reported run status {data.get('status')!r}, which the driver does not know.",
            run_complete=record,
        )

    async def _answer_pause(self, agent: str) -> None:
        step = self.step
        pause: dict[str, Any] = {"agent": agent or None, "at": _now(), "answered_with": None}
        step.pauses.append(pause)
        self.pause_agent = None
        self.waiting_for = f"{step.workflow}/{agent or '(unnamed agent)'} to continue"
        if step.workflow == REVIEW_WORKFLOW and agent == REVIEW_AGENT:
            await self._promote_and_signal(pause)
            return
        text, answered_with = self._next_free_text(agent)
        pause["answered_with"] = answered_with
        await self._send_text(text)

    async def _promote_and_signal(self, pause: dict[str, Any]) -> None:
        review = self.step.review
        if self.promotion is not None:
            raise self._human_assist(
                "review_after_promotion",
                "ReviewAgent asked for input again after the promotion signal; the driver sends it once.",
                agent=REVIEW_AGENT,
            )
        if review is None:
            raise self._human_assist(
                "review_without_summary",
                "ReviewAgent asked for input before presenting AppReviewWorkspace, so there is no "
                "reviewed build to promote.",
                agent=REVIEW_AGENT,
            )
        artifact_version_id = str(review.get("artifact_version_id") or "")
        build_registry_id = str(review.get("build_registry_id") or "")
        if build_registry_id and build_registry_id != self.config.build_registry_id:
            raise JourneyStop(
                EXIT_FAILED,
                "review_target_mismatch",
                f"The review presents build target {build_registry_id}, not the driver's "
                f"{self.config.build_registry_id}; the driver promotes only its own build.",
                review=review,
            )
        if review.get("can_promote") is False or not artifact_version_id or not build_registry_id:
            raise JourneyStop(
                EXIT_FAILED,
                "review_not_promotable",
                f"The build summary does not allow promotion: {_short(review)}",
                review=review,
            )
        status, body = await self._request(
            "POST",
            f"/api/studio/build/artifacts/{quote(artifact_version_id, safe='')}/promote",
            params={"build_registry_id": build_registry_id, "app_id": self.config.app_id},
            body={},
        )
        promoted = isinstance(body, dict) and body.get("promoted") is True
        registry = body.get("app_registry") if isinstance(body, dict) else None
        self.promotion = {
            "artifact_version_id": artifact_version_id,
            "build_registry_id": build_registry_id,
            "status_code": status,
            "promoted": promoted,
            "app_registry": registry,
            "detail": None if promoted else body,
            "signal_sent": False,
        }
        if status != 200 or not promoted:
            raise JourneyStop(
                EXIT_FAILED,
                "promotion_failed",
                f"The promote endpoint answered {status}: {_short(body)}",
                promotion=self.promotion,
            )
        pause["answered_with"] = "promotion_signal"
        await self._send_text(PROMOTION_SIGNAL.format(
            artifact_version_id=artifact_version_id, build_registry_id=build_registry_id,
        ))
        self.promotion["signal_sent"] = True

    def _on_context_switched(self, data: Mapping[str, Any]) -> None:
        to_chat_id = str(data.get("to_chat_id") or "")
        workflow = str(data.get("workflow_name") or "")
        if not to_chat_id or not workflow:
            raise JourneyStop(EXIT_FAILED, "protocol_error", f"A context switch named no chat or workflow: {_short(dict(data))}")
        from_chat_id = data.get("from_chat_id")
        self.transitions.append({
            "kind": "context_switched",
            "at": _now(),
            "from_chat_id": from_chat_id,
            "to_chat_id": to_chat_id,
            "workflow": workflow,
            "journey_id": data.get("journey_id"),
            "journey_key": data.get("journey_key"),
        })
        self._begin_step(Launch(to_chat_id, workflow, None, {"kind": "context_switched", "from_chat_id": from_chat_id}))

    async def _on_transition_requested(self, data: Mapping[str, Any]) -> Launch:
        transition_id = str(data.get("transition_id") or "")
        if not transition_id:
            raise JourneyStop(EXIT_FAILED, "protocol_error", "The host requested a transition without an id.")
        from_chat_id = str(data.get("from_chat_id") or self.step.chat_id)
        self.awaiting_handoff_since = None
        self.transitions.append({
            "kind": "transition_requested",
            "at": _now(),
            "transition_id": transition_id,
            "from_chat_id": from_chat_id,
            "journey_id": data.get("journey_id"),
            "journey_key": data.get("journey_key"),
            "journey_position": data.get("journey_position"),
        })
        raw_context = data.get("context_variables")
        context: dict[str, Any] = dict(raw_context) if isinstance(raw_context, dict) else {}
        return await self._resolve_transition(transition_id, source_chat_id=from_chat_id, context=context)

    def _poll_due(self) -> bool:
        return time.monotonic() - self.last_poll >= SESSION_POLL_SECONDS

    async def _poll_handoff(self, *, final: bool = False) -> Any:
        """After a step completed: the journey is done, still moving, or stalled."""
        step = self.step
        self.last_poll = time.monotonic()
        state = await self._session_state()
        if (
            state.get("sequence_status") == "completed"
            and state.get("journey_key") == self.script.journey_id
            and state.get("current_chat_id") == step.chat_id
        ):
            self.transitions.append({"kind": "journey_completed", "at": _now(), "chat_id": step.chat_id})
            return _JOURNEY_DONE
        assert self.awaiting_handoff_since is not None
        if not final and time.monotonic() - self.awaiting_handoff_since > HANDOFF_WINDOW_SECONDS:
            raise JourneyStop(
                EXIT_FAILED,
                "journey_stalled",
                f"{step.workflow} completed, but within {HANDOFF_WINDOW_SECONDS:g} seconds the host "
                "neither started the next step nor reported the journey complete.",
                session_state=state,
            )
        return None


# --------------------------------------------------------------------------- report


def _server_run_state(outcome: Outcome) -> str:
    if outcome.exit_code == EXIT_COMPLETED:
        return "ended"
    if outcome.exit_code == EXIT_HUMAN_ASSIST:
        return "paused_waiting_for_input"
    if outcome.exit_code == EXIT_LIMIT or outcome.reason == "spend_unpriceable":
        return "may_still_be_running"
    if outcome.reason in {
        "run_failed", "fresh_session_resumed_as_interrupted_run", "chat_failed",
        "workflow_execution_failed", "workflow_session_terminal", "journey_advance_failed",
        "workflow_prerequisites_not_met",
    }:
        return "ended"
    return "unknown"


def _exit_report(
    outcome: Outcome,
    *,
    config: DriverConfig,
    script: JourneyScript | None,
    pricing_file: Path,
    started_at: str,
    elapsed: float,
    driver: JourneyDriver | None,
) -> dict[str, Any]:
    steps = driver.steps if driver else []
    report: dict[str, Any] = {
        "schema_version": EXIT_SCHEMA_VERSION,
        "exit_code": outcome.exit_code,
        "reason": outcome.reason,
        "detail": outcome.detail,
        "started_at": started_at,
        "ended_at": _now(),
        "elapsed_seconds": round(elapsed, 3),
        "limits": {"max_seconds": config.max_seconds, "max_usd": config.max_usd},
        "run": {
            "base_url": config.base_url,
            "app_id": config.app_id,
            "user_id": config.user_id,
            "build_registry_id": config.build_registry_id,
            "journey_id": script.journey_id if script else None,
            "entry": (
                {"transition": script.entry_transition} if script and script.entry_transition
                else {"workflow": script.entry_workflow} if script else None
            ),
            "signed_in": bool(config.access_token),
        },
        "prompt_file": {"path": script.path, "sha256": script.sha256} if script else None,
        "pricing_file": {
            "path": str(pricing_file),
            "sha256": _sha256(pricing_file) if pricing_file.is_file() else None,
        },
        "server_run_state": _server_run_state(outcome),
        "steps_completed": sum(1 for step in steps if step.outcome == "completed"),
        "last_step": steps[-1].as_dict() if steps else None,
        "waiting_for": driver.waiting_for if driver else None,
        "human_assist_needed": len(driver.human_assists) if driver else 0,
        "human_assists": driver.human_assists if driver else [],
        "prompt_sent_to": driver.prompt_sent_to if driver else None,
        "promotion": driver.promotion if driver else None,
        "session_state": driver.session_state if driver else None,
        "terminal_event": driver.terminal_event if driver else None,
        "spend": driver.meter.as_dict() if driver else None,
        "unused_script": script.unused() if script else None,
        "evidence": outcome.evidence,
    }
    if outcome.reason == "fresh_session_resumed_as_interrupted_run":
        report["known_defect"] = {
            "issue": "#642",
            "fixed_by": "PR #816",
            "summary": "A session created before its first run is treated as an interrupted run.",
        }
    return report


# --------------------------------------------------------------------------- CLI


def _positive(value: str) -> float:
    number = float(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return number


def _non_negative(value: str) -> float:
    number = float(value)
    if number < 0:
        raise argparse.ArgumentTypeError("must not be negative")
    return number


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Drive one Mozaiks build journey as a plain HTTP/WebSocket client.",
        epilog=f"With sign-in enabled, set {ACCESS_TOKEN_ENV} to a bearer token issued to --user-id.",
    )
    parser.add_argument("--base-url", required=True, help="Host base URL, for example http://127.0.0.1:8123.")
    parser.add_argument("--app-id", required=True, help="The host's execution app id.")
    parser.add_argument("--user-id", required=True, help="The user the journey runs as.")
    parser.add_argument("--build-registry-id", required=True, help="The registered build target to build.")
    parser.add_argument("--prompt-file", required=True, type=Path, help="Prompt plus scripted replies (JSON).")
    parser.add_argument("--evidence-dir", required=True, type=Path, help="Directory for the evidence files.")
    parser.add_argument("--max-seconds", required=True, type=_positive, help="Wall-clock limit for the journey.")
    parser.add_argument("--max-usd", required=True, type=_non_negative, help="Estimated spend limit in US dollars.")
    parser.add_argument("--pricing-file", required=True, type=Path, help="Usage-pricing catalog used to price usage.")
    return parser


async def run_async(argv: list[str] | None = None) -> int:
    """Run the driver and return its exit code (``main`` without ``asyncio.run``)."""
    args = build_parser().parse_args(argv)
    base = urlsplit(args.base_url)
    if base.scheme not in {"http", "https"} or not base.netloc:
        print("--base-url must be an http:// or https:// URL", file=sys.stderr)
        return EXIT_INVALID
    evidence_dir: Path = args.evidence_dir
    evidence_dir.mkdir(parents=True, exist_ok=True)
    if any((evidence_dir / name).exists() for name in (WEBSOCKET_FILE, EXIT_REASON_FILE)):
        print(f"{evidence_dir} already holds a journey's evidence; use a new directory", file=sys.stderr)
        return EXIT_INVALID

    config = DriverConfig(
        base_url=args.base_url.rstrip("/"),
        app_id=args.app_id,
        user_id=args.user_id,
        build_registry_id=args.build_registry_id,
        max_seconds=args.max_seconds,
        max_usd=args.max_usd,
        access_token=os.environ.get(ACCESS_TOKEN_ENV) or None,
    )
    started_at = _now()
    started = time.monotonic()
    evidence = Evidence(evidence_dir)
    script: JourneyScript | None = None
    driver: JourneyDriver | None = None
    interrupted = False
    try:
        try:
            script = load_script(args.prompt_file)
            prices = load_pricing(args.pricing_file)
        except InvalidInput as exc:
            outcome = Outcome(EXIT_INVALID, "invalid_input", str(exc))
        else:
            driver = JourneyDriver(config, script, SpendMeter(prices, config.max_usd), evidence)
            try:
                outcome = await driver.run()
            except asyncio.CancelledError:
                interrupted = True
                outcome = Outcome(EXIT_FAILED, "interrupted", "The driver was interrupted before the journey ended.")
            driver.write_transitions()
        report = _exit_report(
            outcome,
            config=config,
            script=script,
            pricing_file=args.pricing_file,
            started_at=started_at,
            elapsed=time.monotonic() - started,
            driver=driver,
        )
        evidence.write_json(EXIT_REASON_FILE, report)
    finally:
        evidence.close()
    print(json.dumps({
        "exit_code": outcome.exit_code,
        "reason": outcome.reason,
        "detail": outcome.detail,
        "evidence_dir": str(evidence_dir),
    }), flush=True)
    if interrupted:
        raise asyncio.CancelledError
    return outcome.exit_code


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(run_async(argv))


if __name__ == "__main__":
    raise SystemExit(main())
