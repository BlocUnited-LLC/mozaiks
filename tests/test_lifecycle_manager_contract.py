from __future__ import annotations

from pathlib import Path

import pytest

from tests.import_utils import import_module_directly

_lifecycle = import_module_directly("mozaiksai.core.workflow.execution.lifecycle")
LifecycleToolManager = _lifecycle.LifecycleToolManager


class _Context:
    def __init__(self) -> None:
        self.data = {"order": []}


@pytest.mark.asyncio
async def test_lifecycle_tools_execute_in_declared_order(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # Redirect _workflows_root() to tmp_path via the env override.
    monkeypatch.setenv("MOZAIKS_WORKFLOWS_PATH", str(tmp_path))

    wf_dir = tmp_path / "FlowA"
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


def test_lifecycle_manager_skips_missing_tool_files(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """When tools.yaml lists a tool file that doesn't exist, no bindings are registered."""
    monkeypatch.setenv("MOZAIKS_WORKFLOWS_PATH", str(tmp_path))

    wf_dir = tmp_path / "FlowB"
    wf_dir.mkdir(parents=True)
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

