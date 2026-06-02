from __future__ import annotations

from pathlib import Path

from logs.runtime_artifacts import (
    clear_runtime_artifacts,
    get_agent_outputs_dir,
    get_workflow_converter_logs_dir,
    should_clear_runtime_artifacts_on_start,
)


def _workspace() -> Path:
    return Path(__file__).resolve().parents[1]


def _read(relative_path: str) -> str:
    return (_workspace() / relative_path).read_text(encoding="utf-8")


def test_runtime_artifact_dirs_default_to_repo_logs() -> None:
    workspace = _workspace()
    assert get_agent_outputs_dir() == (workspace / "logs" / "agent_outputs").resolve()
    assert get_workflow_converter_logs_dir() == (workspace / "logs" / "workflow_converter").resolve()


def test_clear_runtime_artifacts_uses_env_overrides(monkeypatch, tmp_path: Path) -> None:
    agent_dir = tmp_path / "agent_outputs"
    converter_dir = tmp_path / "workflow_converter"
    (agent_dir / "nested").mkdir(parents=True)
    converter_dir.mkdir(parents=True)
    (agent_dir / "nested" / "run.jsonl").write_text("{}", encoding="utf-8")
    (converter_dir / "input.json").write_text("{}", encoding="utf-8")

    monkeypatch.setenv("MOZAIKS_AGENT_OUTPUTS_DIR", str(agent_dir))
    monkeypatch.setenv("MOZAIKS_WORKFLOW_CONVERTER_LOG_DIR", str(converter_dir))

    cleared = clear_runtime_artifacts()
    assert cleared["agent_outputs"] == 1
    assert cleared["workflow_converter"] == 1
    assert not any(agent_dir.rglob("*.*"))
    assert not any(converter_dir.rglob("*.*"))


def test_runtime_artifact_startup_flag_is_explicit(monkeypatch) -> None:
    monkeypatch.setenv("CLEAR_RUNTIME_ARTIFACTS_ON_START", "1")
    assert should_clear_runtime_artifacts_on_start() is True
    monkeypatch.setenv("CLEAR_RUNTIME_ARTIFACTS_ON_START", "0")
    assert should_clear_runtime_artifacts_on_start() is False


def test_runtime_artifact_writers_use_shared_helpers() -> None:
    orchestration = _read("mozaiksai/core/workflow/orchestration_patterns.py")
    workflow_download = _read("factory_app/workflows/AgentGenerator/tools/generate_and_download.py")
    workflow_converter = _read("factory_app/workflows/AgentGenerator/tools/workflow_converter.py")

    assert "get_agent_outputs_dir" in orchestration
    assert 'Path("logs/agent_outputs")' not in orchestration

    assert "MOZAIKS_GENERATED_ARTIFACTS_PATH" in workflow_download
    assert 'Path("logs/agent_outputs")' not in workflow_download

    assert 'Path("logs/workflow_converter")' not in workflow_converter
    assert "MOZAIKS_GENERATED_ARTIFACTS_PATH" in workflow_converter


def test_runtime_cleanup_scripts_are_available() -> None:
    backend = _read("scripts/run-backend.ps1")
    cleaner = _read("scripts/clean-runtime-artifacts.ps1")
    env_example = _read(".env.example")

    assert "CleanRuntimeArtifactsOnStart" in backend
    assert "CleanRuntimeArtifactsOnExit" in backend
    assert "KeepLogsBetweenRuns" in backend
    assert "KeepRuntimeArtifactsBetweenRuns" in backend
    assert "clean-runtime-artifacts.ps1" in backend
    assert '$cleanLogsOnStart = -not $KeepLogsBetweenRuns' in backend
    assert '$cleanRuntimeArtifactsNow = $CleanRuntimeArtifactsOnStart -or (-not $KeepRuntimeArtifactsBetweenRuns)' in backend

    assert "logs/agent_outputs" in cleaner
    assert "logs/workflow_converter" in cleaner

    assert "CLEAR_RUNTIME_ARTIFACTS_ON_START=0" in env_example
    assert "MOZAIKS_AGENT_OUTPUTS_DIR=logs/agent_outputs" in env_example
