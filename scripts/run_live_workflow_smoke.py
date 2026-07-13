from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import re
import socket
import sys
import uuid
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

import uvicorn
import websockets
from dotenv import load_dotenv
from websockets.exceptions import ConnectionClosed

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    # Ensure this script always imports local repository modules.
    sys.path.insert(0, str(REPO_ROOT))

DEFAULT_ACTIVE_WORKFLOW = "RuntimeSmoke"
DEFAULT_FACTORY_WORKFLOWS_ROOT = REPO_ROOT / "factory_app" / "workflows"


def _configure_event_loop_policy() -> None:
    if os.name != "nt":
        return
    selector_policy = getattr(asyncio, "WindowsSelectorEventLoopPolicy", None)
    if selector_policy is None:
        return
    asyncio.set_event_loop_policy(selector_policy())


def _has_workflow_definitions(workflows_root: Path) -> bool:
    if not workflows_root.exists():
        return False
    for child in workflows_root.iterdir():
        if not child.is_dir() or child.name == "extended_orchestration":
            continue
        if (child / "orchestrator.yaml").exists():
            return True
    return False


def _resolve_default_workflows_root() -> Path:
    from mozaiksai.core.workflow.paths import resolve_workflows_root
    from mozaiksai.hosts.bootstrap import configure_repo_host_defaults

    configure_repo_host_defaults("studio")

    candidate = resolve_workflows_root()
    if _has_workflow_definitions(candidate):
        return candidate

    if _has_workflow_definitions(DEFAULT_FACTORY_WORKFLOWS_ROOT):
        return DEFAULT_FACTORY_WORKFLOWS_ROOT.resolve()

    return DEFAULT_FACTORY_WORKFLOWS_ROOT.resolve()


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


@dataclass
class SmokeResult:
    success: bool
    app_id: str
    chat_id: str
    workflow_name: str
    prompt: str
    assistant_message: str | None
    structured_output: dict[str, Any]
    final_context: dict[str, Any]
    app_connectors: list[dict[str, Any]]
    event_count: int
    observed_event_types: list[str]

    def as_dict(self) -> dict[str, Any]:
        return _json_safe(
            {
                "success": self.success,
                "app_id": self.app_id,
                "chat_id": self.chat_id,
                "workflow_name": self.workflow_name,
                "prompt": self.prompt,
                "assistant_message": self.assistant_message,
                "structured_output": self.structured_output,
                "final_context": self.final_context,
                "app_connectors": self.app_connectors,
                "event_count": self.event_count,
                "observed_event_types": self.observed_event_types,
            }
        )


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _build_uvicorn_config(app: Any, port: int, *, lifespan: str = "off") -> uvicorn.Config:
    return uvicorn.Config(
        app,
        host="127.0.0.1",
        port=port,
        log_level="warning",
        access_log=False,
        lifespan=lifespan,
    )


def _require_env() -> None:
    missing = [name for name in ("OPENAI_API_KEY", "MONGO_URI") if not str(os.getenv(name) or "").strip()]
    if missing:
        raise RuntimeError(f"Missing required environment variables: {', '.join(missing)}")


def _ensure_workflow_exists(workflows_root: Path, workflow_name: str) -> None:
    wf_dir = workflows_root / workflow_name
    if not wf_dir.exists():
        raise RuntimeError(f"Workflow '{workflow_name}' does not exist under '{workflows_root}'")
    if not (wf_dir / "orchestrator.yaml").exists():
        raise RuntimeError(f"Workflow '{workflow_name}' is missing orchestrator.yaml in '{wf_dir}'")


async def _verify_mongo_available() -> None:
    from mozaiksai.core.core_config import get_mongo_client

    client = get_mongo_client()
    try:
        await client.admin.command("ping")
    except Exception as exc:
        raise RuntimeError(
            f"MongoDB is configured but unreachable via MONGO_URI={os.getenv('MONGO_URI')!r}: {exc}"
        ) from exc


async def _wait_for_server(server: uvicorn.Server, timeout_seconds: float = 20.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    while not getattr(server, "started", False):
        if asyncio.get_running_loop().time() >= deadline:
            raise TimeoutError("Timed out waiting for the smoke server to start")
        await asyncio.sleep(0.1)


def _extract_json_object_from_text(value: Any) -> dict[str, Any]:
    if not isinstance(value, str) or not value.strip():
        return {}
    stripped = value.strip()
    if "{" not in stripped or "}" not in stripped:
        return {}
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start < 0 or end < start:
        return {}
    candidate = stripped[start : end + 1]
    try:
        parsed = json.loads(candidate)
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _extract_latest_structured_output(doc: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(doc, dict):
        return {}
    messages = doc.get("messages")
    if not isinstance(messages, list):
        return {}
    for message in reversed(messages):
        if not isinstance(message, dict):
            continue
        structured = message.get("structured_output")
        if isinstance(structured, dict) and structured:
            return structured
        parsed = _extract_json_object_from_text(message.get("content"))
        if parsed:
            return parsed
    return {}


def _extract_assistant_message(events: list[dict[str, Any]]) -> str | None:
    for event in reversed(events):
        event_type = str(event.get("type") or "")
        if event_type not in {"chat.text", "chat.stream_end"}:
            continue
        data = event.get("data") or {}
        sender = str(data.get("agent") or data.get("sender") or data.get("name") or "").strip()
        if sender.lower() in {"user", "userproxy", "chat_manager", "manager"}:
            continue
        content = data.get("content") or data.get("full_content")
        if isinstance(content, str) and content.strip():
            return content.strip()
    return None


def _resolve_assistant_message(events: list[dict[str, Any]], structured_output: dict[str, Any]) -> str | None:
    message = _extract_assistant_message(events)
    if isinstance(message, str) and message.strip():
        return message.strip()

    fallback = structured_output.get("agent_message")
    if isinstance(fallback, str) and fallback.strip():
        return fallback.strip()
    return None


def _build_tool_call_response_payload(response_text: str) -> dict[str, Any]:
    normalized = str(response_text or "")
    return {
        "status": "submitted",
        "text": normalized,
        "user_input": normalized,
        "user_response": normalized,
    }


def _build_workflow_user_reply_message(chat_id: str, response_text: str) -> dict[str, Any]:
    normalized = str(response_text or "")
    return {
        "type": "user.input.submit",
        "chat_id": chat_id,
        "text": normalized,
        "context": {
            "source": "live_workflow_smoke",
            "conversation_mode": "workflow",
        },
    }


def _is_terminal_completion_event(event: dict[str, Any]) -> bool:
    event_type = str(event.get("type") or "")
    if event_type not in {"chat.run_complete", "chat.workflow_complete", "chat.workflow_completed", "chat.completed"}:
        return False
    data = event.get("data") or {}
    status = data.get("status")
    normalized_status = str(status).strip().lower()
    if status == 1 or normalized_status == "1":
        return True
    return normalized_status in {"completed", "complete", "success", "succeeded", "done", "ok"}


def _normalize_tool_response_payload(raw_payload: Any) -> dict[str, Any]:
    if isinstance(raw_payload, dict):
        normalized = dict(raw_payload)
        normalized.setdefault("status", "submitted")
        return normalized
    return _build_tool_call_response_payload(str(raw_payload or ""))


def _load_tool_response_file(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("tool response file must contain a top-level JSON object")

    input_replies = payload.get("input_replies") or []
    if not isinstance(input_replies, list) or any(not isinstance(item, str) for item in input_replies):
        raise ValueError("tool response file input_replies must be a list of strings")

    tool_responses = payload.get("tool_responses") or {}
    if not isinstance(tool_responses, dict):
        raise ValueError("tool response file tool_responses must be an object keyed by tool/component name")

    default_input_reply = payload.get("default_input_reply")
    if default_input_reply is not None and not isinstance(default_input_reply, str):
        raise ValueError("tool response file default_input_reply must be a string")

    assistant_reply_rules = payload.get("assistant_reply_rules") or []
    if not isinstance(assistant_reply_rules, list):
        raise ValueError("tool response file assistant_reply_rules must be a list")
    normalized_assistant_reply_rules: list[dict[str, str]] = []
    for raw_rule in assistant_reply_rules:
        if not isinstance(raw_rule, dict):
            raise ValueError("assistant_reply_rules entries must be objects")
        reply = str(raw_rule.get("reply") or "").strip()
        equals = str(raw_rule.get("equals") or "").strip()
        contains = str(raw_rule.get("contains") or "").strip()
        regex = str(raw_rule.get("regex") or "").strip()
        if not reply:
            raise ValueError("assistant_reply_rules entries must declare a non-empty reply")
        if not (equals or contains or regex):
            raise ValueError("assistant_reply_rules entries must declare equals, contains, or regex")
        normalized_rule: dict[str, str] = {"reply": reply}
        if equals:
            normalized_rule["equals"] = equals
        if contains:
            normalized_rule["contains"] = contains
        if regex:
            normalized_rule["regex"] = regex
        normalized_assistant_reply_rules.append(normalized_rule)

    return {
        "input_replies": [str(item).strip() for item in input_replies if str(item).strip()],
        "tool_responses": tool_responses,
        "default_input_reply": str(default_input_reply).strip() if isinstance(default_input_reply, str) else None,
        "assistant_reply_rules": normalized_assistant_reply_rules,
    }


def _load_prompt_file(path: Path) -> str:
    prompt = path.read_text(encoding="utf-8").strip()
    if not prompt:
        raise ValueError("prompt file must contain non-empty text")
    return prompt


def _load_context_file(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("context file must contain a top-level JSON object")
    return payload


def _extract_final_context(doc: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(doc, dict):
        return {}
    protected = {
        "_id",
        "chat_id",
        "app_id",
        "workflow_name",
        "user_id",
        "status",
        "created_at",
        "last_updated_at",
        "last_sequence",
        "messages",
        "last_artifact",
    }
    return {
        str(key): value
        for key, value in doc.items()
        if isinstance(key, str) and key not in protected
    }


def _has_terminal_context_output(final_context: dict[str, Any]) -> bool:
    """Return true when a workflow completed through persisted context state."""

    if not isinstance(final_context, dict):
        return False
    if final_context.get("app_download_ready") is True:
        return True
    if str(final_context.get("download_status") or "").strip().lower() == "ready":
        return True
    if isinstance(final_context.get("download_result"), dict) and final_context["download_result"]:
        return True
    if isinstance(final_context.get("generated_app_dir"), str) and final_context["generated_app_dir"].strip():
        return True
    return False


def _build_tool_response_queues(tool_responses: dict[str, Any] | None) -> dict[str, deque[Any]]:
    queues: dict[str, deque[Any]] = {}
    if not isinstance(tool_responses, dict):
        return queues

    for raw_key, raw_value in tool_responses.items():
        key = str(raw_key or "").strip().lower()
        if not key:
            continue
        values = raw_value if isinstance(raw_value, list) else [raw_value]
        queue: deque[Any] = deque()
        for value in values:
            queue.append(value)
        if queue:
            queues[key] = queue
    return queues


def _tool_response_candidates(data: dict[str, Any]) -> list[str]:
    payload = data.get("payload") or {}
    candidates = [
        data.get("tool_name"),
        data.get("component_type"),
        data.get("tool_call_id"),
    ]
    if isinstance(payload, dict):
        candidates.extend(
            [
                payload.get("component_type"),
                payload.get("workflow_primitive"),
            ]
        )
    normalized: list[str] = []
    for candidate in candidates:
        text = str(candidate or "").strip().lower()
        if text and text not in normalized:
            normalized.append(text)
    normalized.append("*")
    return normalized


def _pop_tool_response_payload(
    response_queues: dict[str, deque[Any]],
    data: dict[str, Any],
) -> dict[str, Any] | None:
    for candidate in _tool_response_candidates(data):
        queue = response_queues.get(candidate)
        if not queue:
            continue
        return _normalize_tool_response_payload(queue.popleft())
    return None


def _is_input_request_tool_call(data: dict[str, Any]) -> bool:
    interaction_type = str(data.get("interaction_type") or "").strip().lower()
    component_type = str(data.get("component_type") or "").strip().lower()
    tool_name = str(data.get("tool_name") or "").strip().lower()
    payload = data.get("payload") or {}
    payload_interaction_type = ""
    payload_component_type = ""
    if isinstance(payload, dict):
        payload_interaction_type = str(payload.get("interaction_type") or "").strip().lower()
        payload_component_type = str(payload.get("component_type") or "").strip().lower()
    return (
        interaction_type == "input_request"
        or payload_interaction_type == "input_request"
        or component_type == "userinputrequest"
        or payload_component_type == "userinputrequest"
        or tool_name == "userinputrequest"
    )


def _is_generic_feedback_pending_input(pending_input: dict[str, Any] | None) -> bool:
    if not isinstance(pending_input, dict):
        return False
    raw_payload = pending_input.get("raw_payload")
    raw_prompt = ""
    if isinstance(raw_payload, dict):
        raw_prompt = str(raw_payload.get("prompt") or "").strip()
    prompt = str(pending_input.get("prompt") or "").strip()
    candidate = raw_prompt or prompt
    return candidate.lower().startswith("please give feedback to ")


def _assistant_reply_matches(rule: dict[str, str], message: str) -> bool:
    candidate = str(message or "").strip()
    if not candidate:
        return False
    equals = str(rule.get("equals") or "").strip()
    contains = str(rule.get("contains") or "").strip()
    regex = str(rule.get("regex") or "").strip()
    if equals and candidate.lower() == equals.lower():
        return True
    if contains and contains.lower() in candidate.lower():
        return True
    if regex:
        try:
            return re.search(regex, candidate, re.IGNORECASE) is not None
        except re.error:
            return False
    return False


def _pop_assistant_reply(events: list[dict[str, Any]], reply_rules: list[dict[str, str]]) -> str | None:
    if not reply_rules:
        return None
    assistant_message = _extract_assistant_message(events)
    if not assistant_message:
        return None
    for index, rule in enumerate(reply_rules):
        if _assistant_reply_matches(rule, assistant_message):
            matched = reply_rules.pop(index)
            return matched["reply"]
    return None


def _pop_reply_for_assistant_message(
    assistant_message: str | None,
    reply_rules: list[dict[str, str]],
) -> str | None:
    candidate = str(assistant_message or "").strip()
    if not candidate or not reply_rules:
        return None
    for index, rule in enumerate(reply_rules):
        if _assistant_reply_matches(rule, candidate):
            matched = reply_rules.pop(index)
            return matched["reply"]
    return None


def _peek_input_reply(
    *,
    events: list[dict[str, Any]],
    reply_rules: list[dict[str, str]],
    reply_queue: deque[str],
    default_input_reply: str | None,
    assistant_message: str | None = None,
) -> tuple[str | None, bool]:
    reply_text = _pop_reply_for_assistant_message(assistant_message, reply_rules)
    if reply_text is None:
        reply_text = _pop_assistant_reply(events, reply_rules)
    if reply_text is not None:
        return reply_text, False
    if reply_queue:
        return reply_queue[0], True
    if default_input_reply:
        return default_input_reply, False
    return None, False


async def _wait_for_completed_document(
    coll: Any,
    *,
    chat_id: str,
    app_id: str,
    timeout_seconds: float,
) -> dict[str, Any]:
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    while True:
        doc = await coll.find_one({"_id": chat_id, "app_id": app_id})
        if isinstance(doc, dict):
            if int(doc.get("status", 0)) == 1:
                return doc
        if asyncio.get_running_loop().time() >= deadline:
            raise TimeoutError("Timed out waiting for workflow completion")
        await asyncio.sleep(1.0)


async def _collect_events(
    websocket: Any,
    *,
    chat_id: str,
    timeout_seconds: float,
    tool_response_text: str | None = None,
    user_replies: list[str] | None = None,
    tool_response_payloads: dict[str, Any] | None = None,
    default_input_reply: str | None = None,
    assistant_reply_rules: list[dict[str, str]] | None = None,
    pending_input_provider: Callable[[], Awaitable[dict[str, Any] | None]] | None = None,
    reply_state: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    responded_tool_calls: set[str] = set()
    responded_pending_requests: set[str] = set()
    reply_queue = deque(str(reply).strip() for reply in (user_replies or []) if str(reply).strip())
    response_queues = _build_tool_response_queues(tool_response_payloads)
    contextual_reply_rules = [dict(rule) for rule in (assistant_reply_rules or []) if isinstance(rule, dict)]
    if reply_state is not None:
        responded_tool_calls = reply_state.setdefault("responded_tool_calls", set())
        responded_pending_requests = reply_state.setdefault("responded_pending_requests", set())
        reply_state["reply_queue"] = reply_queue
        reply_state["response_queues"] = response_queues
        reply_state["assistant_reply_rules"] = contextual_reply_rules

    deadline = asyncio.get_running_loop().time() + timeout_seconds
    completion_grace_deadline: float | None = None

    while True:
        now = asyncio.get_running_loop().time()
        if now >= deadline:
            if events:
                return events
            raise TimeoutError("Timed out waiting for websocket smoke events")

        remaining = deadline - now
        if completion_grace_deadline is not None:
            grace_remaining = completion_grace_deadline - now
            if grace_remaining <= 0:
                return events
            remaining = min(remaining, grace_remaining)
        if pending_input_provider is not None and events:
            remaining = min(remaining, 1.0)

        try:
            raw = await asyncio.wait_for(websocket.recv(), timeout=remaining)
        except TimeoutError:
            if pending_input_provider is not None and events:
                pending_input = await pending_input_provider()
                if isinstance(pending_input, dict):
                    request_id = str(pending_input.get("request_id") or "").strip()
                    if request_id and request_id not in responded_pending_requests:
                        response_payload: dict[str, Any] | None = None
                        reply_text, used_queue = _peek_input_reply(
                            events=events,
                            reply_rules=contextual_reply_rules,
                            reply_queue=reply_queue,
                            default_input_reply=default_input_reply,
                            assistant_message=str(pending_input.get("assistant_message") or ""),
                        )
                        if reply_text is not None:
                            response_payload = _build_tool_call_response_payload(reply_text)
                        if response_payload is not None:
                            responded_pending_requests.add(request_id)
                            responded_tool_calls.add(request_id)
                            if used_queue and reply_queue:
                                reply_queue.popleft()
                            await websocket.send(
                                json.dumps(
                                    {
                                        "type": "tool_call_response",
                                        "tool_call_id": request_id,
                                        "response": response_payload,
                                    }
                                )
                            )
                            completion_grace_deadline = None
                            continue
            if events:
                continue
            raise
        except ConnectionClosed:
            if events:
                return events
            raise

        event = json.loads(raw)
        events.append(event)
        event_type = str(event.get("type") or "")
        data = event.get("data") or {}

        if event_type in {"error", "chat.error"}:
            error_code = data.get("code") if isinstance(data, dict) else None
            error_message = data.get("message") if isinstance(data, dict) else None
            raise RuntimeError(
                f"Workflow websocket reported {event_type}"
                f"{f' [{error_code}]' if error_code else ''}: {error_message or event}"
            )

        if event_type == "chat.tool_call" and isinstance(data, dict):
            tool_call_id = str(data.get("tool_call_id") or "").strip()
            awaiting_response = bool(data.get("awaiting_response"))
            response_payload: dict[str, Any] | None = None
            used_queue = False
            if _is_input_request_tool_call(data):
                reply_text, used_queue = _peek_input_reply(
                    events=events,
                    reply_rules=contextual_reply_rules,
                    reply_queue=reply_queue,
                    default_input_reply=default_input_reply,
                )
                if reply_text is not None:
                    response_payload = _build_tool_call_response_payload(reply_text)
            if response_payload is None:
                response_payload = _pop_tool_response_payload(response_queues, data)
            if response_payload is None and tool_response_text is not None:
                response_payload = _build_tool_call_response_payload(tool_response_text)
            if tool_call_id and awaiting_response and response_payload is not None and tool_call_id not in responded_tool_calls:
                responded_tool_calls.add(tool_call_id)
                if used_queue and reply_queue:
                    reply_queue.popleft()
                await websocket.send(
                    json.dumps(
                        {
                            "type": "tool_call_response",
                            "tool_call_id": tool_call_id,
                            "response": response_payload,
                        }
                    )
                )
                if not reply_queue:
                    completion_grace_deadline = None
            continue

        if event_type == "chat.awaiting_reply":
            reply_text, used_queue = _peek_input_reply(
                events=events,
                reply_rules=contextual_reply_rules,
                reply_queue=reply_queue,
                default_input_reply=default_input_reply,
            )
            if reply_text is not None:
                if used_queue and reply_queue:
                    reply_queue.popleft()
                await websocket.send(
                    json.dumps(_build_workflow_user_reply_message(chat_id, reply_text))
                )
                completion_grace_deadline = None
                continue

        if event_type in {"chat.run_complete", "chat.workflow_complete", "chat.workflow_completed", "chat.completed"}:
            status_value = data.get("status") if isinstance(data, dict) else None
            reason_value = str(data.get("reason") or "").strip().lower() if isinstance(data, dict) else ""
            awaiting_user_input = bool(data.get("awaiting_user_input")) if isinstance(data, dict) else False
            is_paused = (
                awaiting_user_input
                or str(status_value).strip() == "0"
                or reason_value in {"awaiting_user_input", "paused"}
            )
            if is_paused:
                reply_text, used_queue = _peek_input_reply(
                    events=events,
                    reply_rules=contextual_reply_rules,
                    reply_queue=reply_queue,
                    default_input_reply=default_input_reply,
                )
                if reply_text is not None:
                    if used_queue and reply_queue:
                        reply_queue.popleft()
                    await websocket.send(
                        json.dumps(_build_workflow_user_reply_message(chat_id, reply_text))
                    )
                    completion_grace_deadline = None
                    continue
            completion_grace_deadline = now + 1.0
            continue

        if event_type == "chat.input_ack" and not reply_queue:
            # Use a generous grace window so multi-turn LLM workflows have time
            # to start a new run and emit their next events (tool calls, stream
            # chunks, etc.) before the collector decides there is nothing left.
            completion_grace_deadline = max(
                completion_grace_deadline or 0.0,
                now + 60.0,
            )


async def _await_workflow_with_pending_input_fallback(
    *,
    workflow_wait_task: asyncio.Task[dict[str, Any]],
    transport: Any,
    pending_input_provider: Callable[[], Awaitable[dict[str, Any] | None]] | None,
    events: list[dict[str, Any]],
    reply_state: dict[str, Any],
    default_input_reply: str | None,
) -> dict[str, Any]:
    responded_pending_requests = reply_state.setdefault("responded_pending_requests", set())
    responded_tool_calls = reply_state.setdefault("responded_tool_calls", set())

    while True:
        try:
            return await asyncio.wait_for(asyncio.shield(workflow_wait_task), timeout=1.0)
        except TimeoutError:
            if pending_input_provider is None:
                continue
            pending_input = await pending_input_provider()
            if not isinstance(pending_input, dict):
                continue

            request_id = str(pending_input.get("request_id") or "").strip()
            if not request_id or request_id in responded_pending_requests or request_id in responded_tool_calls:
                continue

            reply_queue = reply_state.get("reply_queue")
            if not isinstance(reply_queue, deque):
                reply_queue = deque()
                reply_state["reply_queue"] = reply_queue
            reply_rules = reply_state.get("assistant_reply_rules")
            if not isinstance(reply_rules, list):
                reply_rules = []
                reply_state["assistant_reply_rules"] = reply_rules

            reply_text, used_queue = _peek_input_reply(
                events=events,
                reply_rules=reply_rules,
                reply_queue=reply_queue,
                default_input_reply=default_input_reply,
                assistant_message=str(pending_input.get("assistant_message") or ""),
            )
            if reply_text is None:
                continue

            submitted = await transport.submit_tool_call_response(
                request_id,
                _build_tool_call_response_payload(reply_text),
            )
            if submitted:
                responded_pending_requests.add(request_id)
                responded_tool_calls.add(request_id)
                if used_queue and reply_queue:
                    reply_queue.popleft()


async def run_live_workflow_smoke(
    prompt: str = "Write a one-line joke about release engineering.",
    *,
    timeout_seconds: float = 180.0,
    workflow_name: str = DEFAULT_ACTIVE_WORKFLOW,
    workflows_root: Path | None = None,
    initial_context: dict[str, Any] | None = None,
    initial_agent: str | None = None,
    tool_response_text: str | None = None,
    user_replies: list[str] | None = None,
    tool_response_payloads: dict[str, Any] | None = None,
    default_input_reply: str | None = None,
    assistant_reply_rules: list[dict[str, str]] | None = None,
) -> SmokeResult:
    load_dotenv(REPO_ROOT / ".env")
    _require_env()

    effective_root = (workflows_root or _resolve_default_workflows_root()).resolve()
    _ensure_workflow_exists(effective_root, workflow_name)

    # Force the smoke run to target the intended workflows root regardless of caller env.
    os.environ["MOZAIKS_WORKFLOWS_PATH"] = str(effective_root)
    os.environ["WORKFLOW_DIR"] = str(effective_root)

    await _verify_mongo_available()

    from mozaiksai.core.data.persistence.persistence_manager import AG2PersistenceManager
    from mozaiksai.core.transport.simple_transport import SimpleTransport
    from mozaiksai.core.workflow.workflow_manager import get_workflow_manager, initialize_workflows
    from mozaiksai.factory import create_mozaiks_app

    SimpleTransport._instance = None
    initialize_workflows(base_path=str(effective_root))
    manager = get_workflow_manager()

    info = manager.get_workflow_info(workflow_name) or {}
    if info.get("status") != "loaded":
        raise RuntimeError(f"Workflow failed to load: {workflow_name} -> {info.get('error')}")

    app = create_mozaiks_app(workflow_dir=str(effective_root), debug=False)
    port = _find_free_port()
    server = uvicorn.Server(_build_uvicorn_config(app, port))
    serve_task = asyncio.create_task(server.serve())

    pm = AG2PersistenceManager()
    app_id = f"live-smoke-{uuid.uuid4().hex[:8]}"
    user_id = "smoke-user"
    chat_id = f"chat_{workflow_name.lower()}_{uuid.uuid4().hex[:8]}"
    events: list[dict[str, Any]] = []
    completed_successfully = False
    workflow_result: dict[str, Any] | None = None

    try:
        await _wait_for_server(server)
        await pm.create_chat_session(
            chat_id=chat_id,
            app_id=app_id,
            workflow_name=workflow_name,
            user_id=user_id,
            extra_fields=initial_context if isinstance(initial_context, dict) else None,
        )

        ws_url = f"ws://127.0.0.1:{port}/ws/{workflow_name}/{app_id}/{chat_id}/{user_id}"
        run_task: asyncio.Task | None = None
        async with websockets.connect(
            ws_url,
            open_timeout=20,
            close_timeout=5,
            max_size=2**20,
            ping_interval=None,
        ) as websocket:
            reply_state: dict[str, Any] = {}

            async def _pending_input_provider() -> dict[str, Any] | None:
                coll = await pm._coll()
                doc = await coll.find_one(
                    {"_id": chat_id, "app_id": app_id},
                    {"pending_input_request": 1, "messages": 1},
                )
                pending_input = (doc or {}).get("pending_input_request")
                if not isinstance(pending_input, dict):
                    return None

                assistant_message = None
                for message in reversed((doc or {}).get("messages") or []):
                    if not isinstance(message, dict):
                        continue
                    role = str(message.get("role") or "").strip().lower()
                    if role == "user":
                        continue
                    content = message.get("content")
                    if isinstance(content, str) and content.strip():
                        assistant_message = content.strip()
                        break

                enriched = dict(pending_input)
                if assistant_message:
                    enriched["assistant_message"] = assistant_message
                return enriched

            ws_conn = app.state.transport.connections.get(chat_id) or {}
            ws_id = ws_conn.get("ws_id")
            if ws_id is None:
                raise RuntimeError(f"WebSocket connection metadata missing ws_id for chat {chat_id}")

            run_task = asyncio.create_task(
                app.state.transport._run_workflow_background(
                    chat_id=chat_id,
                    workflow_name=workflow_name,
                    app_id=app_id,
                    user_id=user_id,
                    ws_id=ws_id,
                    initial_message=prompt,
                    initial_agent_name_override=initial_agent,
                )
            )
            collect_task = asyncio.create_task(
                _collect_events(
                    websocket,
                    chat_id=chat_id,
                    timeout_seconds=timeout_seconds,
                    tool_response_text=tool_response_text,
                    user_replies=user_replies,
                    tool_response_payloads=tool_response_payloads,
                    default_input_reply=default_input_reply,
                    assistant_reply_rules=assistant_reply_rules,
                    pending_input_provider=_pending_input_provider,
                    reply_state=reply_state,
                )
            )
            workflow_wait_task = asyncio.create_task(asyncio.wait_for(run_task, timeout=timeout_seconds))
            done, _ = await asyncio.wait(
                {collect_task, workflow_wait_task},
                return_when=asyncio.FIRST_COMPLETED,
            )

            if workflow_wait_task in done:
                workflow_result = await workflow_wait_task
                slice_status = str((workflow_result or {}).get("run_status") or "").strip().lower()
                if slice_status == "completed":
                    if not collect_task.done():
                        with contextlib.suppress(Exception):
                            await websocket.close()
                        events = await asyncio.wait_for(collect_task, timeout=15.0)
                    else:
                        events = collect_task.result()
                else:
                    events = await collect_task
            else:
                events = collect_task.result()
                workflow_result = await _await_workflow_with_pending_input_fallback(
                    workflow_wait_task=workflow_wait_task,
                    transport=app.state.transport,
                    pending_input_provider=_pending_input_provider,
                    events=events,
                    reply_state=reply_state,
                    default_input_reply=default_input_reply,
                )

        final_doc: dict[str, Any] | None = None
        structured_output: dict[str, Any] = {}
        final_context: dict[str, Any] = {}
        app_connectors: list[dict[str, Any]] = []
        try:
            coll = await pm._coll()
            run_status_value = str((workflow_result or {}).get("run_status") or "").strip().lower()
            if run_status_value == "paused":
                pending_input = await _pending_input_provider()
                if _is_generic_feedback_pending_input(pending_input):
                    try:
                        workflow_result = await asyncio.wait_for(
                            app.state.transport.handle_user_input_from_api(
                                chat_id=chat_id,
                                user_id=user_id,
                                workflow_name=workflow_name,
                                message="",
                                app_id=app_id,
                            ),
                            timeout=60.0,
                        )
                    except Exception:
                        pass

            final_doc = await coll.find_one({"_id": chat_id, "app_id": app_id})
            saw_terminal_completion = any(_is_terminal_completion_event(event) for event in events)
            workflow_completed = str((workflow_result or {}).get("run_status") or "").strip().lower() == "completed"
            if (not isinstance(final_doc, dict) or int(final_doc.get("status", 0)) != 1) and (
                saw_terminal_completion or workflow_completed
            ):
                final_doc = await _wait_for_completed_document(
                    coll,
                    chat_id=chat_id,
                    app_id=app_id,
                    timeout_seconds=15.0,
                )
            structured_output = _extract_latest_structured_output(final_doc)
            final_context = _extract_final_context(final_doc)
            try:
                from mozaiksai.core.data.persistence.connector_store import ConnectorStore
                from mozaiksai.core.workflow.generator_support.connector_service import (
                    list_connectors,
                )

                app_connectors = await list_connectors(scope=ConnectorStore.SCOPE_APP, scope_id=app_id)
            except Exception:
                app_connectors = []
        except Exception:
            structured_output = {}
            final_context = {}
            app_connectors = []

        observed_event_types = [str(event.get("type") or "") for event in events]
        assistant_message = _resolve_assistant_message(events, structured_output)
        if not structured_output:
            structured_output = _extract_json_object_from_text(assistant_message)
        final_status = int((final_doc or {}).get("status", 0)) if isinstance(final_doc, dict) else 0
        completed_successfully = final_status == 1 and (
            bool(structured_output)
            or bool(assistant_message)
            or _has_terminal_context_output(final_context)
        )
        if not completed_successfully:
            import sys
            print(f"[DEBUG] workflow_result={workflow_result}", file=sys.stderr)
            print(f"[DEBUG] final_status={final_status}", file=sys.stderr)
            print(f"[DEBUG] observed_events={observed_event_types}", file=sys.stderr)
            run_status = str((workflow_result or {}).get("run_status") or "paused").strip().lower() or "paused"
            raise RuntimeError(f"Workflow ended without terminal completion (run_status={run_status})")
        return SmokeResult(
            success=completed_successfully,
            app_id=app_id,
            chat_id=chat_id,
            workflow_name=workflow_name,
            prompt=prompt,
            assistant_message=assistant_message,
            structured_output=structured_output,
            final_context=final_context,
            app_connectors=app_connectors,
            event_count=len(events),
            observed_event_types=observed_event_types,
        )
    finally:
        if completed_successfully:
            try:
                coll = await pm._coll()
                await coll.delete_many({"app_id": app_id})
            except Exception:
                pass
        server.should_exit = True
        if hasattr(server, "force_exit"):
            server.force_exit = True
        try:
            await asyncio.wait_for(serve_task, timeout=15)
        except BaseException:
            serve_task.cancel()
            with contextlib.suppress(BaseException):
                await serve_task
        try:
            from mozaiksai.core.core_config import close_mongo_client

            close_mongo_client()
        except Exception:
            pass
        current_task = asyncio.current_task()
        pending_tasks = [
            task
            for task in asyncio.all_tasks()
            if task is not current_task and not task.done()
        ]
        for task in pending_tasks:
            task.cancel()
        if pending_tasks:
            with contextlib.suppress(BaseException):
                await asyncio.gather(*pending_tasks, return_exceptions=True)


def main() -> int:
    _configure_event_loop_policy()
    parser = argparse.ArgumentParser(description="Run live AG2 runtime smoke against a real workflow + LLM")
    parser.add_argument(
        "--workflow",
        default=DEFAULT_ACTIVE_WORKFLOW,
        help=f"Workflow to execute for smoke validation (default: {DEFAULT_ACTIVE_WORKFLOW}).",
    )
    parser.add_argument(
        "--prompt",
        default="Write a one-line joke about release engineering.",
        help="Prompt to send into the workflow.",
    )
    parser.add_argument(
        "--prompt-file",
        default=None,
        help="Optional text file containing the prompt to send into the workflow.",
    )
    parser.add_argument(
        "--context-file",
        default=None,
        help="Optional JSON file used as initial workflow context variables.",
    )
    parser.add_argument(
        "--initial-agent",
        default=None,
        help="Optional agent name override used to start the workflow at a specific agent.",
    )
    parser.add_argument(
        "--workflows-root",
        default=str(_resolve_default_workflows_root()),
        help="Root directory containing workflow folders.",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=180.0,
        help="Maximum time to wait for completion.",
    )
    parser.add_argument(
        "--tool-response-text",
        default=None,
        help="If set, automatically answer every chat.tool_call with this text.",
    )
    parser.add_argument(
        "--user-reply",
        action="append",
        default=[],
        help="Optional scripted user reply for after-work user handoffs. Repeat for multi-turn workflows.",
    )
    parser.add_argument(
        "--tool-response-file",
        default=None,
        help="Optional JSON file with scripted input_replies and structured tool_responses.",
    )
    parser.add_argument(
        "--expect-output-contains",
        action="append",
        default=[],
        help="Fail if the final smoke payload does not contain this text. Repeatable.",
    )
    parser.add_argument(
        "--expect-connector-service",
        action="append",
        default=[],
        help="Fail if the app connector inventory does not contain this normalized service id. Repeatable.",
    )
    parser.add_argument(
        "--fail-on-needs-revision",
        action="store_true",
        help="Fail if the final UI quality status is needs_revision or blocked.",
    )
    args = parser.parse_args()

    scripted_responses = None
    scripted_replies: list[str] = list(args.user_reply or [])
    default_input_reply = None
    assistant_reply_rules = None
    initial_context = None
    prompt = str(args.prompt)
    if args.prompt_file:
        prompt = _load_prompt_file(Path(args.prompt_file))
    if args.context_file:
        initial_context = _load_context_file(Path(args.context_file))
    if args.tool_response_file:
        scripted_responses = _load_tool_response_file(Path(args.tool_response_file))
        scripted_replies = list(scripted_responses.get("input_replies") or []) + scripted_replies
        default_input_reply = scripted_responses.get("default_input_reply")
        assistant_reply_rules = scripted_responses.get("assistant_reply_rules")

    result = asyncio.run(
        run_live_workflow_smoke(
            prompt=prompt,
            timeout_seconds=args.timeout_seconds,
            workflow_name=args.workflow,
            workflows_root=Path(args.workflows_root),
            initial_context=initial_context,
            initial_agent=str(args.initial_agent).strip() if args.initial_agent else None,
            tool_response_text=args.tool_response_text,
            user_replies=scripted_replies or None,
            tool_response_payloads=(scripted_responses or {}).get("tool_responses"),
            default_input_reply=default_input_reply,
            assistant_reply_rules=assistant_reply_rules,
        )
    )
    result_payload = result.as_dict()
    validation_errors: list[str] = []
    output_text = json.dumps(result_payload, ensure_ascii=False)
    for expected in args.expect_output_contains or []:
        expected_text = str(expected or "")
        if expected_text and expected_text not in output_text:
            validation_errors.append(f"missing expected output text: {expected_text}")
    connector_services = {
        str(connector.get("service") or "").strip().lower()
        for connector in result.app_connectors or []
        if isinstance(connector, dict)
    }
    for expected in args.expect_connector_service or []:
        expected_service = str(expected or "").strip().lower().replace(" ", "_")
        if expected_service and expected_service not in connector_services:
            validation_errors.append(f"missing expected connector service: {expected_service}")
    if args.fail_on_needs_revision:
        quality_status = str(
            (result.final_context or {}).get("app_ui_quality_status")
            or (result.structured_output or {}).get("status")
            or ""
        ).strip()
        if quality_status != "passed":
            validation_errors.append(
                f"UI quality gate did not pass; status={quality_status or 'missing'}"
            )

    exit_code = 0 if result.success and not validation_errors else 1
    if validation_errors:
        result_payload["validation_errors"] = validation_errors
    print(json.dumps(result_payload, indent=2), flush=True)
    try:
        from mozaiksai.core.core_config import close_mongo_client

        close_mongo_client()
    except Exception:
        pass
    try:
        sys.stdout.flush()
        sys.stderr.flush()
    finally:
        # AG2 + Mongo can leave non-daemon background threads behind in local smoke runs.
        os._exit(exit_code)


if __name__ == "__main__":
    raise SystemExit(main())
