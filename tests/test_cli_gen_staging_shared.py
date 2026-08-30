"""mozaiks gen staging must carry the _shared helper tree.

Workflow middleware references helpers as ../_shared/... relative to the
workflow directory; staging only the workflow folder silently breaks every
such import and generation degrades to an empty output (dogfood finding,
2026-08-23).
"""
from __future__ import annotations

from pathlib import Path

from mozaiks_cli.commands.gen import _stage_workflow


def _make_source(tmp_path: Path) -> Path:
    source = tmp_path / "workflows"
    wf = source / "AgentGenerator"
    (wf / "tools").mkdir(parents=True)
    (wf / "orchestrator.yaml").write_text(
        "workflow_name: AgentGenerator\n", encoding="utf-8"
    )
    (wf / "tools" / "noop.py").write_text("def noop():\n    return 1\n", encoding="utf-8")
    shared = source / "_shared"
    (shared / "context_graph").mkdir(parents=True)
    (shared / "subscription_contract_context.py").write_text("X = 1\n", encoding="utf-8")
    (shared / "context_graph" / "hook_context_graph.py").write_text("Y = 2\n", encoding="utf-8")
    return source


def test_stage_workflow_stages_shared_helpers(tmp_path: Path) -> None:
    source = _make_source(tmp_path)
    staging = tmp_path / "staging"
    staging.mkdir()

    _stage_workflow(source, staging, "AgentGenerator")

    assert (staging / "AgentGenerator" / "orchestrator.yaml").is_file()
    assert (staging / "_shared" / "subscription_contract_context.py").is_file()
    assert (staging / "_shared" / "context_graph" / "hook_context_graph.py").is_file()


def test_stage_workflow_without_shared_dir_still_works(tmp_path: Path) -> None:
    source = _make_source(tmp_path)
    import shutil

    shutil.rmtree(source / "_shared")
    staging = tmp_path / "staging"
    staging.mkdir()

    _stage_workflow(source, staging, "AgentGenerator")
    assert (staging / "AgentGenerator" / "orchestrator.yaml").is_file()
    assert not (staging / "_shared").exists()
