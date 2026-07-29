from __future__ import annotations

from pathlib import Path

import pytest

from tests.import_utils import import_module_directly

_lifecycle = import_module_directly("mozaiksai.core.workflow.execution.lifecycle")
LifecycleToolManager = _lifecycle.LifecycleToolManager


class _Context:
    def __init__(self) -> None:
        self.data = {"order": []}


def _write_orchestrator(workflow_dir: Path) -> None:
    workflow_dir.mkdir(parents=True, exist_ok=True)
    (workflow_dir / "orchestrator.yaml").write_text(
        f"workflow_name: {workflow_dir.name}\nworkflow_startup_mode: BackendOnly\n",
        encoding="utf-8",
    )


@pytest.mark.asyncio
async def test_lifecycle_tools_execute_in_declared_order(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # Redirect _workflows_root() to tmp_path via the env override.
    monkeypatch.setenv("MOZAIKS_WORKFLOWS_PATH", str(tmp_path))

    wf_dir = tmp_path / "FlowA"
    _write_orchestrator(wf_dir)
    tools_dir = wf_dir / "tools"
    tools_dir.mkdir(parents=True)

    (tools_dir / "steps.py").write_text(
        "\n".join(
            [
                "def one(context_variables=None):",
                "    if context_variables is not None:",
                "        context_variables.data.setdefault('order', []).append('one')",
                "",
                "def two(context_variables=None):",
                "    if context_variables is not None:",
                "        context_variables.data.setdefault('order', []).append('two')",
                "",
            ]
        ),
        encoding="utf-8",
    )

    (wf_dir / "tools.yaml").write_text(
        "lifecycle_tools:\n"
        "  - trigger: before_chat\n"
        "    agent: null\n"
        "    file: steps.py\n"
        "    function: one\n"
        "  - trigger: before_chat\n"
        "    agent: null\n"
        "    file: steps.py\n"
        "    function: two\n",
        encoding="utf-8",
    )

    manager = LifecycleToolManager("FlowA")
    manager.load_lifecycle_tools()

    ctx = _Context()
    await manager.trigger_before_chat(context_variables=ctx)
    assert ctx.data["order"] == ["one", "two"]


@pytest.mark.asyncio
async def test_lifecycle_tool_receives_empty_context_container(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MOZAIKS_WORKFLOWS_PATH", str(tmp_path))

    workflow_dir = tmp_path / "EmptyContext"
    _write_orchestrator(workflow_dir)
    tools_dir = workflow_dir / "tools"
    tools_dir.mkdir(parents=True)
    (tools_dir / "preload.py").write_text(
        "def preload(context_variables=None):\n"
        "    context_variables['preloaded'] = True\n",
        encoding="utf-8",
    )
    (workflow_dir / "tools.yaml").write_text(
        "lifecycle_tools:\n"
        "  - trigger: before_chat\n"
        "    agent: null\n"
        "    file: preload.py\n"
        "    function: preload\n",
        encoding="utf-8",
    )

    manager = LifecycleToolManager("EmptyContext")
    manager.load_lifecycle_tools()
    context: dict[str, object] = {}

    await manager.trigger_before_chat(context_variables=context)

    assert context["preloaded"] is True


def test_lifecycle_manager_skips_missing_tool_files(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """When tools.yaml lists a tool file that doesn't exist, no bindings are registered."""
    monkeypatch.setenv("MOZAIKS_WORKFLOWS_PATH", str(tmp_path))

    wf_dir = tmp_path / "FlowB"
    _write_orchestrator(wf_dir)
    (wf_dir / "tools.yaml").write_text(
        "lifecycle_tools:\n"
        "  - trigger: before_chat\n"
        "    agent: null\n"
        "    file: missing.py\n"
        "    function: one\n",
        encoding="utf-8",
    )

    manager = LifecycleToolManager("FlowB")
    manager.load_lifecycle_tools()
    assert isinstance(manager.tools, dict)
    assert sum(len(v) for v in manager.tools.values()) == 0


@pytest.mark.asyncio
async def test_lifecycle_manager_executes_run_level_hooks(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MOZAIKS_WORKFLOWS_PATH", str(tmp_path))

    wf_dir = tmp_path / "FlowRun"
    _write_orchestrator(wf_dir)
    tools_dir = wf_dir / "tools"
    tools_dir.mkdir(parents=True)

    (tools_dir / "run_hooks.py").write_text(
        "\n".join(
            [
                "calls = []",
                "",
                "async def started(*, app_id, workflow_name, **kwargs):",
                "    calls.append((app_id, workflow_name, kwargs.get('chat_id')))",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (wf_dir / "tools.yaml").write_text(
        "lifecycle_tools:\n"
        "  - trigger: on_start\n"
        "    agent: null\n"
        "    file: run_hooks.py\n"
        "    function: started\n",
        encoding="utf-8",
    )

    manager = LifecycleToolManager("FlowRun")
    manager.load_lifecycle_tools()
    await manager.execute_trigger(
        _lifecycle.LifecycleTrigger.ON_START,
        app_id="app_1",
        workflow_name="FlowRun",
        chat_id="chat_1",
    )

    tool = manager.tools[_lifecycle.LifecycleTrigger.ON_START][0]
    assert tool.callable.__globals__["calls"] == [("app_1", "FlowRun", "chat_1")]


def test_lifecycle_manager_resolves_shared_context_graph_loader(monkeypatch: pytest.MonkeyPatch) -> None:
    workspace = Path(__file__).resolve().parents[1]
    workflows_root = workspace / "factory_app" / "workflows"
    monkeypatch.setenv("MOZAIKS_WORKFLOWS_PATH", str(workflows_root))

    manager = LifecycleToolManager("AppGenerator")
    manager.load_lifecycle_tools()

    before_chat = manager.tools[_lifecycle.LifecycleTrigger.BEFORE_CHAT]
    assert any(
        tool.file == "../_shared/context_graph/load_context_graph_context.py"
        and tool.function == "load_context_graph_context"
        and callable(tool.callable)
        for tool in before_chat
    )

