from __future__ import annotations

import os
from pathlib import Path
from typing import Dict


_TRUTHY = {"1", "true", "yes", "on"}


def _logs_root() -> Path:
    return Path(__file__).resolve().parent


def _resolve_dir(env_name: str, default_relative: str) -> Path:
    raw = os.getenv(env_name, "").strip()
    if raw:
        candidate = Path(raw).expanduser()
        if not candidate.is_absolute():
            candidate = Path.cwd() / candidate
    else:
        candidate = _logs_root() / default_relative
    return candidate.resolve()


def get_agent_outputs_dir() -> Path:
    return _resolve_dir("MOZAIKS_AGENT_OUTPUTS_DIR", "agent_outputs")


def get_workflow_converter_logs_dir() -> Path:
    return _resolve_dir("MOZAIKS_WORKFLOW_CONVERTER_LOG_DIR", "workflow_converter")


def should_clear_runtime_artifacts_on_start() -> bool:
    return os.getenv("CLEAR_RUNTIME_ARTIFACTS_ON_START", "0").strip().lower() in _TRUTHY


def clear_runtime_artifacts(
    *,
    clear_agent_outputs: bool = True,
    clear_workflow_converter: bool = True,
) -> Dict[str, int]:
    cleared: Dict[str, int] = {
        "agent_outputs": 0,
        "workflow_converter": 0,
    }

    targets = []
    if clear_agent_outputs:
        targets.append(("agent_outputs", get_agent_outputs_dir()))
    if clear_workflow_converter:
        targets.append(("workflow_converter", get_workflow_converter_logs_dir()))

    for label, directory in targets:
        if not directory.exists():
            continue
        for path in directory.rglob("*"):
            if not path.is_file():
                continue
            try:
                path.unlink()
                cleared[label] += 1
            except Exception:
                # Best effort only; callers can inspect counts.
                pass

    return cleared
