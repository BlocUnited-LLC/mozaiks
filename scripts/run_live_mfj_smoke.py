from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import socket
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import uvicorn
import websockets
from dotenv import load_dotenv
from websockets.exceptions import ConnectionClosed


REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS_ROOT = REPO_ROOT / "platform" / "workflows"


@dataclass
class SmokeResult:
    success: bool
    app_id: str
    chat_id: str
    prompt: str
    presenter_message: Optional[str]
    summary: Optional[str]
    worker_name: Optional[str]
    merged_payload: Dict[str, Any]
    event_count: int
    observed_event_types: List[str]

    def as_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "app_id": self.app_id,
            "chat_id": self.chat_id,
            "prompt": self.prompt,
            "presenter_message": self.presenter_message,
            "summary": self.summary,
            "worker_name": self.worker_name,
            "merged_payload": self.merged_payload,
            "event_count": self.event_count,
            "observed_event_types": self.observed_event_types,
        }


def _summarize_chat_doc(doc: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not isinstance(doc, dict):
        return {}

    messages = doc.get("messages")
    tail: List[Dict[str, Any]] = []
    if isinstance(messages, list):
        for item in messages[-5:]:
            if not isinstance(item, dict):
                continue
            tail.append(
                {
                    "role": item.get("role"),
                    "name": item.get("name"),
                    "content": item.get("content"),
                    "sequence": item.get("sequence"),
                }
            )

    return {
        "chat_id": doc.get("_id"),
        "app_id": doc.get("app_id"),
        "workflow_name": doc.get("workflow_name"),
        "status": doc.get("status"),
        "last_sequence": doc.get("last_sequence"),
        "mfj_smoke_results": doc.get("mfj_smoke_results"),
        "smoke_presented_summary": doc.get("smoke_presented_summary"),
        "smoke_presented_worker": doc.get("smoke_presented_worker"),
        "_mfj_resume_pending": doc.get("_mfj_resume_pending"),
        "_mfj_resume_target_agent": doc.get("_mfj_resume_target_agent"),
        "_mfj_resume_entry_agent": doc.get("_mfj_resume_entry_agent"),
        "_mfj_resume_nonce": doc.get("_mfj_resume_nonce"),
        "_mfj_resume_consumed_nonce": doc.get("_mfj_resume_consumed_nonce"),
        "_mfj_resume_succeeded_count": doc.get("_mfj_resume_succeeded_count"),
        "_mfj_resume_failed_count": doc.get("_mfj_resume_failed_count"),
        "message_tail": tail,
    }


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _require_env() -> None:
    missing = [
        name
        for name in ("OPENAI_API_KEY", "MONGO_URI")
        if not str(os.getenv(name) or "").strip()
    ]
    if missing:
        raise RuntimeError(f"Missing required environment variables: {', '.join(missing)}")


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


async def _wait_for_success_document(
    coll: Any,
    chat_id: str,
    app_id: str,
    timeout_seconds: float,
) -> Dict[str, Any]:
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    while True:
        doc = await coll.find_one({"_id": chat_id, "app_id": app_id})
        if isinstance(doc, dict):
            merged = doc.get("mfj_smoke_results")
            if (
                isinstance(merged, dict)
                and isinstance(merged.get("result"), str)
                and isinstance(doc.get("smoke_presented_summary"), str)
                and doc.get("_mfj_resume_pending") is False
                and doc.get("_mfj_resume_nonce") == doc.get("_mfj_resume_consumed_nonce")
            ):
                return doc
        if asyncio.get_running_loop().time() >= deadline:
            raise TimeoutError("Timed out waiting for the MFJ smoke workflow to complete")
        await asyncio.sleep(1.0)


async def _collect_events(
    websocket: Any,
    prompt: str,
    chat_id: str,
    timeout_seconds: float,
) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    await websocket.send(json.dumps({"type": "user.input.submit", "chat_id": chat_id, "text": prompt}))
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    saw_resume = False
    saw_presenter_output = False

    while True:
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            if events:
                return events
            raise TimeoutError("Timed out waiting for websocket smoke events")
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
        if event_type == "chat.workflow_resumed":
            saw_resume = True
        if event_type in {"chat.text", "chat.stream_end"}:
            data = event.get("data") or {}
            agent = str(data.get("agent") or data.get("sender") or "")
            content = data.get("content") or data.get("full_content")
            if agent == "PresenterAgent" and isinstance(content, str) and content.strip():
                saw_presenter_output = True
        if saw_resume and saw_presenter_output:
            return events


def _extract_presenter_message(events: List[Dict[str, Any]]) -> Optional[str]:
    for event in reversed(events):
        if str(event.get("type") or "") not in {"chat.text", "chat.stream_end"}:
            continue
        data = event.get("data") or {}
        agent = str(data.get("agent") or data.get("sender") or "")
        if agent == "PresenterAgent":
            content = data.get("content") or data.get("full_content")
            if isinstance(content, str) and content.strip():
                return content.strip()
    return None


async def run_live_mfj_smoke(
    prompt: str = "Summarize the runtime smoke path in one sentence.",
    *,
    timeout_seconds: float = 180.0,
) -> SmokeResult:
    load_dotenv(REPO_ROOT / ".env")
    _require_env()

    os.environ.setdefault("MOZAIKS_WORKFLOWS_PATH", str(WORKFLOWS_ROOT))
    os.environ.setdefault("WORKFLOW_DIR", str(WORKFLOWS_ROOT))

    await _verify_mongo_available()

    from mozaiksai.factory import create_mozaiks_app
    from mozaiksai.core.data.persistence.persistence_manager import AG2PersistenceManager
    from mozaiksai.core.transport.simple_transport import SimpleTransport
    from mozaiksai.core.workflow.workflow_manager import UnifiedWorkflowManager

    UnifiedWorkflowManager._instance = None
    SimpleTransport._instance = None

    manager = UnifiedWorkflowManager(workflows_base_path=str(WORKFLOWS_ROOT))
    for workflow_name in ("SmokeParent", "SmokeChild"):
        info = manager.get_workflow_info(workflow_name) or {}
        if info.get("status") != "loaded":
            raise RuntimeError(f"Workflow failed to load: {workflow_name} -> {info.get('error')}")

    app = create_mozaiks_app(workflow_dir=str(WORKFLOWS_ROOT), debug=False)
    port = _find_free_port()
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", access_log=False)
    )
    serve_task = asyncio.create_task(server.serve())

    pm = AG2PersistenceManager()
    app_id = f"live-smoke-{uuid.uuid4().hex[:8]}"
    user_id = "smoke-user"
    chat_id = f"chat_smoke_parent_{uuid.uuid4().hex[:8]}"
    events: List[Dict[str, Any]] = []
    completed_successfully = False

    try:
        await _wait_for_server(server)
        await pm.create_chat_session(
            chat_id=chat_id,
            app_id=app_id,
            workflow_name="SmokeParent",
            user_id=user_id,
        )

        ws_url = f"ws://127.0.0.1:{port}/ws/SmokeParent/{app_id}/{chat_id}/{user_id}"
        async with websockets.connect(ws_url, open_timeout=20, close_timeout=5, max_size=2**20) as websocket:
            events = await _collect_events(websocket, prompt, chat_id, timeout_seconds)

        coll = await pm._coll()
        try:
            doc = await _wait_for_success_document(coll, chat_id, app_id, timeout_seconds)
        except TimeoutError as exc:
            current_doc = await coll.find_one({"_id": chat_id, "app_id": app_id})
            snapshot = _summarize_chat_doc(current_doc)
            raise TimeoutError(
                "Timed out waiting for the MFJ smoke workflow to complete. "
                f"Last parent snapshot: {json.dumps(snapshot, default=str)}"
            ) from exc
        merged_payload = doc.get("mfj_smoke_results") if isinstance(doc.get("mfj_smoke_results"), dict) else {}
        observed_event_types = [str(event.get("type") or "") for event in events]
        completed_successfully = True
        return SmokeResult(
            success=True,
            app_id=app_id,
            chat_id=chat_id,
            prompt=prompt,
            presenter_message=_extract_presenter_message(events),
            summary=str(doc.get("smoke_presented_summary") or "") or None,
            worker_name=str(merged_payload.get("worker_name") or "") or None,
            merged_payload=merged_payload,
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
    parser = argparse.ArgumentParser(description="Run the live MFJ smoke workflow against real AG2 + OpenAI")
    parser.add_argument(
        "--prompt",
        default="Summarize the runtime smoke path in one sentence.",
        help="Prompt to send into the SmokeParent workflow.",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=180.0,
        help="Maximum time to wait for completion.",
    )
    args = parser.parse_args()

    result = asyncio.run(run_live_mfj_smoke(prompt=args.prompt, timeout_seconds=args.timeout_seconds))
    print(json.dumps(result.as_dict(), indent=2))
    return 0 if result.success else 1


if __name__ == "__main__":
    raise SystemExit(main())