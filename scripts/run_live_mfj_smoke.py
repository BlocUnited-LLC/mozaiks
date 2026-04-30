from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import socket
import sys
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import uvicorn
import websockets
from dotenv import load_dotenv
from websockets.exceptions import ConnectionClosed


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    # Ensure this script always imports local repository modules.
    sys.path.insert(0, str(REPO_ROOT))

DEFAULT_WORKFLOWS_ROOT = REPO_ROOT / "platform" / "workflows"
_app_workspace = os.environ.get("MOZAIKS_APP_WORKSPACE_PATH", "")
APP_ZERO_WORKFLOWS_ROOT = (Path(_app_workspace) / "app" / "workflows") if _app_workspace else Path("/dev/null")
DEFAULT_ACTIVE_WORKFLOW = "RuntimeSmoke"


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
    override = str(os.getenv("MOZAIKS_WORKFLOWS_PATH") or "").strip()
    if override:
        candidate = Path(override)
        if not candidate.is_absolute():
            candidate = (REPO_ROOT / candidate).resolve()
        return candidate
    if _has_workflow_definitions(APP_ZERO_WORKFLOWS_ROOT):
        return APP_ZERO_WORKFLOWS_ROOT
    return DEFAULT_WORKFLOWS_ROOT


DEFAULT_ACTIVE_WORKFLOWS_ROOT = _resolve_default_workflows_root()


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
    assistant_message: Optional[str]
    structured_output: Dict[str, Any]
    event_count: int
    observed_event_types: List[str]

    def as_dict(self) -> Dict[str, Any]:
        return _json_safe(
            {
                "success": self.success,
                "app_id": self.app_id,
                "chat_id": self.chat_id,
                "workflow_name": self.workflow_name,
                "prompt": self.prompt,
                "assistant_message": self.assistant_message,
                "structured_output": self.structured_output,
                "event_count": self.event_count,
                "observed_event_types": self.observed_event_types,
            }
        )


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


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


def _extract_latest_structured_output(doc: Optional[Dict[str, Any]]) -> Dict[str, Any]:
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
    return {}


def _extract_assistant_message(events: List[Dict[str, Any]]) -> Optional[str]:
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


def _resolve_assistant_message(events: List[Dict[str, Any]], structured_output: Dict[str, Any]) -> Optional[str]:
    message = _extract_assistant_message(events)
    if isinstance(message, str) and message.strip():
        return message.strip()

    fallback = structured_output.get("agent_message")
    if isinstance(fallback, str) and fallback.strip():
        return fallback.strip()
    return None


async def _wait_for_completed_document(
    coll: Any,
    *,
    chat_id: str,
    app_id: str,
    timeout_seconds: float,
) -> Dict[str, Any]:
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
    timeout_seconds: float,
) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []

    deadline = asyncio.get_running_loop().time() + timeout_seconds
    completion_grace_deadline: Optional[float] = None
    saw_assistant_message = False

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

        try:
            raw = await asyncio.wait_for(websocket.recv(), timeout=remaining)
        except TimeoutError:
            if events:
                return events
            raise
        except ConnectionClosed:
            if events:
                return events
            raise

        event = json.loads(raw)
        events.append(event)
        event_type = str(event.get("type") or "")
        data = event.get("data") or {}

        if event_type in {"chat.text", "chat.stream_end"}:
            sender = str(data.get("agent") or data.get("sender") or data.get("name") or "").strip().lower()
            content = data.get("content") or data.get("full_content")
            if sender not in {"", "user", "userproxy", "chat_manager", "manager"} and isinstance(content, str) and content.strip():
                saw_assistant_message = True
                completion_grace_deadline = now + 2.0

        if event_type in {"chat.workflow_complete", "chat.workflow_completed", "chat.completed"}:
            completion_grace_deadline = now + 1.0
        elif saw_assistant_message and completion_grace_deadline is None:
            completion_grace_deadline = now + 2.0


async def run_live_mfj_smoke(
    prompt: str = "Write a one-line joke about release engineering.",
    *,
    timeout_seconds: float = 180.0,
    workflow_name: str = DEFAULT_ACTIVE_WORKFLOW,
    workflows_root: Optional[Path] = None,
) -> SmokeResult:
    load_dotenv(REPO_ROOT / ".env")
    _require_env()

    effective_root = (workflows_root or DEFAULT_ACTIVE_WORKFLOWS_ROOT).resolve()
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
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", access_log=False)
    )
    serve_task = asyncio.create_task(server.serve())

    pm = AG2PersistenceManager()
    app_id = f"live-smoke-{uuid.uuid4().hex[:8]}"
    user_id = "smoke-user"
    chat_id = f"chat_{workflow_name.lower()}_{uuid.uuid4().hex[:8]}"
    events: List[Dict[str, Any]] = []
    completed_successfully = False

    try:
        await _wait_for_server(server)
        await pm.create_chat_session(
            chat_id=chat_id,
            app_id=app_id,
            workflow_name=workflow_name,
            user_id=user_id,
        )

        ws_url = f"ws://127.0.0.1:{port}/ws/{workflow_name}/{app_id}/{chat_id}/{user_id}"
        run_task: Optional[asyncio.Task] = None
        async with websockets.connect(ws_url, open_timeout=20, close_timeout=5, max_size=2**20) as websocket:
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
                )
            )
            events = await _collect_events(
                websocket,
                timeout_seconds=timeout_seconds,
            )
            try:
                await asyncio.wait_for(run_task, timeout=timeout_seconds)
            except TimeoutError as task_timeout:
                raise TimeoutError(
                    f"Background workflow task did not finish within {timeout_seconds}s"
                ) from task_timeout

        coll = await pm._coll()
        doc = await _wait_for_completed_document(
            coll,
            chat_id=chat_id,
            app_id=app_id,
            timeout_seconds=timeout_seconds,
        )

        structured_output = _extract_latest_structured_output(doc)
        observed_event_types = [str(event.get("type") or "") for event in events]
        completed_successfully = True
        return SmokeResult(
            success=True,
            app_id=app_id,
            chat_id=chat_id,
            workflow_name=workflow_name,
            prompt=prompt,
            assistant_message=_resolve_assistant_message(events, structured_output),
            structured_output=structured_output,
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
        try:
            await asyncio.wait_for(serve_task, timeout=15)
        except BaseException:
            serve_task.cancel()
            with contextlib.suppress(BaseException):
                await serve_task


def main() -> int:
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
        "--workflows-root",
        default=str(DEFAULT_ACTIVE_WORKFLOWS_ROOT),
        help="Root directory containing workflow folders.",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=180.0,
        help="Maximum time to wait for completion.",
    )
    args = parser.parse_args()

    result = asyncio.run(
        run_live_mfj_smoke(
            prompt=args.prompt,
            timeout_seconds=args.timeout_seconds,
            workflow_name=args.workflow,
            workflows_root=Path(args.workflows_root),
        )
    )
    print(json.dumps(result.as_dict(), indent=2))
    return 0 if result.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
