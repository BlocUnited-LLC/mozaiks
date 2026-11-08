"""Call every HTTP-reachable action of a booted app bundle once, in a child process.

The runtime smoke exercises canonical CRUD for data-contract entities. A pack's
own actions (send a friend request, update preferences, open a ticket) are
reached only here. The child boots the bundle exactly as the smoke's trusted
fixture path does, then calls each action that is not internal-only as one
signed-in user with schema-valid required inputs.

An HTTP wiring probe passes when it answers below 500 within the request timeout.
A 4xx is not evidence of successful business execution. This probe rejects the 400
the executor gives when the handler raises TypeError on inputs the action's
schema accepted: that is a handler that cannot be called. Events the
runtime rejects and permissions no signed-in user holds are recorded by the
smoke's own call path with the smoke's own check ids.

Run as ``python -m tests.pack_proof_action_sweep`` with a JSON request on stdin.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any
from uuid import uuid4

from factory_app.workflows.AppGenerator.tools import app_runtime_smoke as smoke

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SWEEP_TIMEOUT_SECONDS = 300.0
# The router refuses these surfaces over HTTP; they run only from the event bus.
INTERNAL_SURFACES = frozenset({"internal", "admin_internal"})


async def run_action_sweep(app_root: Path, *, mongo_uri: str) -> list[dict[str, Any]]:
    """Every outcome the child recorded, on a disposable database dropped afterwards."""
    from motor.motor_asyncio import AsyncIOMotorClient

    database_name = f"{smoke._SMOKE_DATABASE_PREFIX}sweep_{uuid4().hex[:16]}"
    environment = smoke.child_environment()
    environment["PYTHONPATH"] = os.pathsep.join([str(REPOSITORY_ROOT), environment["PYTHONPATH"]])
    environment["USERPROFILE" if os.name == "nt" else "HOME"] = str(app_root.parent)
    request = {"app_root": str(app_root), "mongo_uri": mongo_uri, "database_name": database_name}
    client: Any = AsyncIOMotorClient(mongo_uri, serverSelectionTimeoutMS=5000)
    try:
        completed = await asyncio.to_thread(
            subprocess.run,
            [sys.executable, "-m", "tests.pack_proof_action_sweep"],
            input=json.dumps(request), capture_output=True, text=True, encoding="utf-8", errors="replace",
            env=environment, cwd=str(app_root.parent), timeout=SWEEP_TIMEOUT_SECONDS,
        )
    finally:
        await client.drop_database(database_name)
        client.close()
    events = smoke._events(completed.stdout)
    if completed.returncode != 0 or not any(event.get("event") == "done" for event in events):
        raise AssertionError(
            f"the action sweep child exited {completed.returncode} without finishing: {completed.stderr[-2000:]}"
        )
    return [
        {key: value for key, value in event.items() if key != "event"}
        for event in events if event.get("event") == "outcome"
    ]


def _bad_params(records: list[logging.LogRecord], module: str, action: str) -> str | None:
    """The TypeError the executor reports as INVALID_PARAMS for inputs the action's schema accepted.

    The sweep sends only schema-valid inputs, so this 400 is the handler or the
    code it calls failing to take them, not a business answer.
    """
    marker = f"MODULE_ACTION_BAD_PARAMS: module={module} action={action} error="
    for record in records:
        message = record.getMessage()
        if message.startswith(marker):
            return message[len(marker):]
    return None


async def _sweep(request: Mapping[str, Any], emit: Callable[..., None], initial_environment: set[str]) -> None:
    from motor.motor_asyncio import AsyncIOMotorClient

    client: Any = AsyncIOMotorClient(str(request["mongo_uri"]), serverSelectionTimeoutMS=5000)
    run = smoke._SmokeRun(Path(str(request["app_root"])), client, str(request["database_name"]), emit)
    runtime_logger = logging.getLogger(smoke._RUNTIME_LOGGER)
    runtime_logger.addHandler(run.capture)
    try:
        if await smoke._boot(run, initial_environment):
            user = run.principal("sweep user")
            for module in sorted(run.load.modules, key=lambda item: item.name):
                for action, surface in module.action_api_surface_map.items():
                    check = f"action.{module.name}.{action}"
                    if surface in INTERNAL_SURFACES:
                        run.not_run(check, f"{surface} actions are reachable only from the event bus")
                        continue
                    params = smoke._required_params(smoke._input_schema(module, action), {})
                    since = len(run.capture.records)
                    status, _body, summary = await run.call("POST", module.name, action, user, params)
                    rejected = _bad_params(run.capture.records[since:], module.name, action)
                    if rejected:
                        summary += f"; the handler could not take its declared inputs ({rejected})"
                    run.record(
                        check, 0 < status < 500 and not rejected,
                        f"{module.name}.{action} with required inputs {sorted(params)} answered {summary}.",
                        path=run.last_exception_path or (f"modules/{module.name}/backend" if rejected else None),
                        http_status=status, coverage="http_wiring",
                    )
    except Exception as exc:  # the sweep's own failure is a failed check, never a silent pass
        run.record("sweep.error", False, f"The action sweep could not complete: {type(exc).__name__}: {exc}")
    finally:
        if run.http is not None:
            await run.http.aclose()
        runtime_logger.removeHandler(run.capture)
        client.close()
    emit("done")


def _main() -> int:
    initial_environment = set(os.environ)
    request = json.loads(sys.stdin.read() or "{}")
    channel = os.fdopen(os.dup(sys.stdout.fileno()), "w", encoding="utf-8")
    sys.stdout = sys.stderr  # bundle code's prints never reach the result channel

    def emit(kind: str, **data: Any) -> None:
        channel.write(smoke._EVENT_PREFIX + json.dumps({"event": kind, **data}, default=str) + "\n")
        channel.flush()

    asyncio.run(_sweep(request, emit, initial_environment))
    channel.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
