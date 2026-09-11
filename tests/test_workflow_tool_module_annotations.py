from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

from mozaiksai.core.workflow.agents import tools
from mozaiksai.core.workflow.outputs import structured


@pytest.fixture
def tool_workflow(tmp_path: Path, monkeypatch):
    root = tmp_path / "TypedTools"
    (root / "tools").mkdir(parents=True)
    monkeypatch.setattr(tools.workflow_manager, "resolve_workflow_path", lambda _: root)
    monkeypatch.setattr(structured, "get_structured_outputs_for_workflow", lambda _: {})
    saved_path = list(sys.path)
    saved_modules = {key: value for key, value in sys.modules.items() if key == "workflows" or key.startswith("workflows.")}
    yield root
    sys.path[:] = saved_path
    for key in list(sys.modules):
        if key == "workflows" or key.startswith("workflows.TypedTools"):
            sys.modules.pop(key, None)
    sys.modules.update(saved_modules)


def declare(root, entries):
    (root / "tools.yaml").write_text(yaml.safe_dump({"tools": [
        {"agent": agent, "file": file, "function": function, "tool_type": "Agent_Tool"}
        for agent, file, function in entries
    ]}), encoding="utf-8")


def test_typed_tool_globals_survive_loading_sibling_tools(tool_workflow):
    (tool_workflow / "tools/review.py").write_text('''from __future__ import annotations
from dataclasses import dataclass
from typing import Literal
from pydantic import BaseModel, StrictBool

@dataclass
class Label:
    text: str

class Review(BaseModel):
    action: Literal["approve", "cancel"]
    approved: StrictBool
    detail: Detail

class Detail(BaseModel):
    label: str

def review(payload):
    return Review.model_validate(payload).model_dump()

def label():
    return Label("ready").text
''', encoding="utf-8")
    (tool_workflow / "tools/other.py").write_text("def other():\n    return True\n", encoding="utf-8")
    declare(tool_workflow, [("Review", "review.py", "review"), ("Other", "other.py", "other"), ("Label", "review.py", "label")])
    mapping = tools.load_agent_tool_functions("TypedTools")
    payload = {"action": "approve", "approved": True, "detail": {"label": "draft"}}
    assert mapping["Review"][0](payload) == payload
    assert mapping["Label"][0]() == "ready"
    with pytest.raises(ValueError):
        mapping["Review"][0]({**payload, "approved": "yes"})


def test_failed_import_does_not_leave_partial_module_and_reload_is_fresh(tool_workflow):
    path = tool_workflow / "tools/probe.py"
    path.write_text("raise RuntimeError('incomplete import')\n", encoding="utf-8")
    declare(tool_workflow, [("Probe", "probe.py", "probe")])
    assert tools.load_agent_tool_functions("TypedTools") == {}
    assert "workflows.TypedTools.tools.probe" not in sys.modules
    path.write_text("def probe():\n    return 'first'\n", encoding="utf-8")
    first = tools.load_agent_tool_functions("TypedTools")["Probe"][0]
    assert first() == "first"
    path.write_text("def probe():\n    return 'second-version'\n", encoding="utf-8")
    second = tools.load_agent_tool_functions("TypedTools")["Probe"][0]
    assert second() == "second-version"
    assert first() == "first"
