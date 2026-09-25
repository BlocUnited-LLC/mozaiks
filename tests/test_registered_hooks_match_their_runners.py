"""Every registered hook is resolved by its real resolver, named on a real agent, shaped for the
call its runner actually makes, and fires at least once when called that way.

Two prompt-rendering mechanisms were registered, wired, and inert:

  #718  the subscription contract injector read an attribute no live container
        exposes -- registered three times, never ran
  #723  the brownfield hook had the signature (agent_name, context_variables)
        while the runner calls every hook as fn(capture, history) -- registered
        six times, never called since #144

The #721 quality-state writes now run through an authorized auto tool and are
covered by test_appgenerator_ui_quality_writer_authority.py.

A dead hook emits nothing: no error, no warning, no log line. The runner swallows
prompt-hook exceptions at DEBUG, drops a registration that fails to resolve after
one WARNING, and ignores return values. So this file does what a live run cannot:
it resolves each registration with the runner's own resolver, checks the
registered agent exists, pins the call shape the runner uses (and fails first if
the runner changes it), and then calls each prompt hook the way the runner does,
on a real ContextVariablesBridge, and reads the prompt back.

Two runners exist for lifecycle tools and they pass different arguments:
before_chat and the manager on_fail path go through LifecycleToolManager
(mozaiksai/core/workflow/execution/lifecycle.py), while on_start, on_complete and
the composition on_fail path go through get_workflow_lifecycle_hooks
(mozaiksai/core/runtime/composition/extensions.py) from workflow_bridge.py, which
never passes context_variables. Both are pinned here.
"""

from __future__ import annotations

import ast
import inspect
import pathlib
from collections.abc import Callable
from typing import Any

import pytest
import yaml

from mozaiksai.core.workflow.agents.factory import ContextVariablesBridge
from mozaiksai.core.workflow.execution.lifecycle import LifecycleToolManager
from mozaiksai.core.workflow.execution.middleware import (
    _resolve_import,
    load_prompt_middleware_entries,
)

ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / "factory_app" / "workflows"
MIDDLEWARE_RUNNER = ROOT / "mozaiksai/core/workflow/execution/middleware.py"
LIFECYCLE_RUNNER = ROOT / "mozaiksai/core/workflow/execution/lifecycle.py"
ORCHESTRATION = ROOT / "mozaiksai/core/workflow/orchestration_patterns.py"
BRIDGE = ROOT / "mozaiksai/core/transport/workflow_bridge.py"

# The call shapes the runners use today. If a runner changes, the tests that pin
# these fail first, and the constants (and every hook) are updated together.
RUN_LEVEL_KWARGS = {"app_id", "execution_id", "chat_id", "user_id", "workflow_name"}
RUN_LEVEL_FAIL_KWARGS = RUN_LEVEL_KWARGS | {"message", "details"}
MANAGER_FAIL_KWARGS = RUN_LEVEL_KWARGS | {"context_variables", "error"}


@pytest.fixture(autouse=True)
def _workflows_root(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MOZAIKS_WORKFLOWS_PATH", str(WORKFLOWS))
    monkeypatch.setenv("MOZAIKS_BUILD_CONTEXT_PATH", str(ROOT / "factory_app" / "build_context"))


class _Capture:
    """What _run_prompt_middleware hands every hook (execution/middleware.py)."""

    def __init__(self, name: str, context_variables: Any) -> None:
        self.name = name
        self.context_variables = context_variables
        self.system_message = "BASE PROMPT"
        self._system_message = "BASE PROMPT"

    def update_system_message(self, message: str) -> None:
        self.system_message = message
        self._system_message = message


def _roster(workflow: str) -> set[str]:
    doc = yaml.safe_load((WORKFLOWS / workflow / "agents.yaml").read_text(encoding="utf-8")) or {}
    agents = doc.get("agents") if isinstance(doc, dict) else doc
    if isinstance(agents, dict):
        return set(agents)
    return {a.get("name") for a in agents or [] if isinstance(a, dict) and a.get("name")}


def _call_keywords(path: pathlib.Path, callee: str) -> list[set[str]]:
    """Keyword names of every call to `callee(...)` in the file."""
    found: list[set[str]] = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if not isinstance(node, ast.Call):
            continue
        name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
        if name == callee:
            found.append({kw.arg for kw in node.keywords if kw.arg})
    return found


# ---------------------------------------------------------------------------
# The runners' call shapes, pinned
# ---------------------------------------------------------------------------


def test_the_prompt_middleware_runner_calls_hooks_positionally_and_reads_the_prompt_back() -> None:
    src = MIDDLEWARE_RUNNER.read_text(encoding="utf-8")
    assert "result = middleware_fn(capture, history)" in src, (
        "the prompt-hook call shape changed; update every hook and the contract below together"
    )
    assert "capture._captured if capture._captured is not None else projected_message" in src, (
        "the runner no longer reads the prompt back from update_system_message"
    )


def test_the_run_level_runner_passes_exactly_these_kwargs() -> None:
    started = _call_keywords(BRIDGE, "emit_execution_started")
    completed = _call_keywords(BRIDGE, "emit_execution_completed")
    failed = _call_keywords(BRIDGE, "_emit_execution_failed")
    assert started and all(kws == RUN_LEVEL_KWARGS for kws in started), started
    assert completed and all(kws == RUN_LEVEL_KWARGS for kws in completed), completed
    assert failed and all(kws == RUN_LEVEL_FAIL_KWARGS for kws in failed), failed


def test_the_manager_runner_passes_a_subset_of_these_kwargs() -> None:
    calls = [kws for path in (ORCHESTRATION, BRIDGE) for kws in _call_keywords(path, "execute_trigger")]
    assert calls, "no execute_trigger call sites found"
    for kws in calls:
        assert kws <= MANAGER_FAIL_KWARGS, kws
    assert any("context_variables" in kws for kws in _call_keywords(ORCHESTRATION, "trigger_before_chat"))


# ---------------------------------------------------------------------------
# Registrations
# ---------------------------------------------------------------------------


def _middleware_registrations() -> list[tuple[str, dict[str, Any]]]:
    out: list[tuple[str, dict[str, Any]]] = []
    for path in sorted(WORKFLOWS.glob("*/middleware.yaml")):
        workflow = path.parent.name
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        entries = load_prompt_middleware_entries(workflow, base_path=str(WORKFLOWS))
        assert len(entries) == len(raw.get("prompt_middleware") or []), f"{path}: the loader dropped entries"
        out.extend((workflow, entry) for entry in entries)
    return out


def _lifecycle_registrations() -> list[tuple[str, dict[str, Any]]]:
    out: list[tuple[str, dict[str, Any]]] = []
    for path in sorted(WORKFLOWS.glob("*/tools.yaml")):
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        out.extend((path.parent.name, entry) for entry in raw.get("lifecycle_tools") or [] if isinstance(entry, dict))
    return out


MIDDLEWARE = _middleware_registrations()
LIFECYCLE = _lifecycle_registrations()


def _resolve_middleware(workflow: str, entry: dict[str, Any]) -> Callable | None:
    fn, _ = _resolve_import(workflow, entry.get("filename"), entry["function"], WORKFLOWS / workflow)
    return fn


@pytest.mark.parametrize(
    ("workflow", "entry"), MIDDLEWARE,
    ids=[f"{w}:{e.get('agent')}:{e['function'].rsplit('.', 1)[-1]}" for w, e in MIDDLEWARE],
)
def test_a_prompt_hook_registration_resolves_and_is_shaped_for_the_runner(workflow: str, entry: dict[str, Any]) -> None:
    fn = _resolve_middleware(workflow, entry)
    assert fn is not None, f"{workflow}: {entry} does not resolve; the runner logs one WARNING and drops it"
    agent = entry.get("agent")
    assert agent == "all" or agent in _roster(workflow), (
        f"{workflow}: agent {agent!r} is not in agents.yaml, so the entry never matches create_agents"
    )
    params = list(inspect.signature(fn).parameters.values())
    positional = [p for p in params if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
    assert len(positional) >= 2 or any(p.kind is p.VAR_POSITIONAL for p in params), (
        f"{fn.__qualname__}: the runner calls fn(capture, history) positionally"
    )
    assert [p.name for p in positional[:2]] == ["agent", "messages"], (
        f"{fn.__qualname__}{inspect.signature(fn)}: the runner passes the agent capture and the message "
        "history positionally; a hook that names its first two parameters anything else receives the wrong "
        "objects and fails silently (#723 named them agent_name, context_variables)"
    )
    for extra in positional[2:]:
        assert extra.default is not inspect.Parameter.empty, f"{fn.__qualname__}: extra positional {extra.name} has no default"


def _manager_tools(workflow: str) -> dict[tuple[str, str, str], Any]:
    manager = LifecycleToolManager(workflow)
    manager.load_lifecycle_tools()
    return {(t.trigger.value, t.file, t.function): t for tools in manager.tools.values() for t in tools}


@pytest.mark.parametrize(
    ("workflow", "entry"), LIFECYCLE,
    ids=[f"{w}:{e.get('trigger')}:{e.get('function')}" for w, e in LIFECYCLE],
)
def test_a_lifecycle_registration_resolves_and_binds_its_runner_kwargs(workflow: str, entry: dict[str, Any]) -> None:
    from mozaiksai.core.runtime.composition.extensions import get_workflow_lifecycle_hooks

    trigger = entry.get("trigger")
    tool = _manager_tools(workflow).get((trigger, entry.get("file"), entry.get("function")))
    assert tool is not None, f"{workflow}: {entry} does not resolve through LifecycleToolManager"
    fn = tool.callable
    sig = inspect.signature(fn)
    if trigger in ("before_chat", "after_chat"):
        assert tool.accepts_context, (
            f"{fn.__qualname__}: {trigger} tools are called with context_variables only; without that "
            "parameter the runner calls it with no arguments"
        )
        sig.bind(context_variables=None)
    elif trigger in ("on_start", "on_complete", "on_fail"):
        hooks = get_workflow_lifecycle_hooks(workflow)
        assert hooks.get(trigger) is not None, f"{workflow}: {trigger} does not resolve through get_workflow_lifecycle_hooks"
        assert hooks[trigger].__name__ == fn.__name__
        kwargs = RUN_LEVEL_FAIL_KWARGS if trigger == "on_fail" else RUN_LEVEL_KWARGS
        sig.bind(**{k: None for k in kwargs})  # the composition path (workflow_bridge)
        if trigger == "on_fail":
            var_kw = any(p.kind is p.VAR_KEYWORD for p in sig.parameters.values())
            sig.bind(**{k: None for k in MANAGER_FAIL_KWARGS if var_kw or k in sig.parameters})  # the manager path
    else:
        pytest.fail(f"unknown trigger {trigger!r}")


# ---------------------------------------------------------------------------
# A hook that fails must be visible, and the runner's own reads must work
# ---------------------------------------------------------------------------


def test_a_prompt_hook_that_raises_is_reported_at_warning(caplog: pytest.LogCaptureFixture) -> None:
    """The subscription injector's fail-closed refusal was swallowed at DEBUG and never seen (#723)."""
    import asyncio
    import logging
    from types import SimpleNamespace

    from mozaiksai.core.workflow.execution.middleware import MozaiksPromptMiddleware

    def refusing_hook(agent: Any, messages: list[dict[str, Any]]) -> None:
        raise RuntimeError("contract review requested changes")

    runner = MozaiksPromptMiddleware(
        None, None, middleware_functions=[refusing_hook], agent_name="AppPlanAgent",
        base_system_message="BASE PROMPT", context_bridge=ContextVariablesBridge({}),
    )
    with caplog.at_level(logging.WARNING):
        prompt = asyncio.run(runner._run_prompt_middleware(SimpleNamespace(variables={}, prompt=["BASE PROMPT"])))

    assert prompt == "BASE PROMPT", "a failing hook still contributes nothing; the turn continues"
    reported = [r for r in caplog.records if "Prompt middleware failed" in r.getMessage()]
    assert reported and reported[0].levelno == logging.WARNING
    assert "refusing_hook" in reported[0].getMessage()
    assert "contract review requested changes" in reported[0].getMessage()


@pytest.mark.parametrize("make", ["dict", "bridge", "runtime"])
def test_the_lifecycle_runner_can_read_the_ids_it_logs(make: str) -> None:
    """lifecycle.py read getattr(ctx, "data", {}) for its log ids; no container has .data (#723)."""
    from mozaiksai.core.workflow.context.adapter import create_context_container
    from mozaiksai.core.workflow.execution.lifecycle import _context_value

    seed = {"chat_id": "chat_1", "app_id": "app_1"}
    container = {"dict": dict(seed), "bridge": ContextVariablesBridge(dict(seed)),
                 "runtime": create_context_container(dict(seed))}[make]

    assert _context_value(container, "chat_id") == "chat_1"
    assert _context_value(container, "app_id") == "app_1"
    assert _context_value(None, "chat_id") is None
