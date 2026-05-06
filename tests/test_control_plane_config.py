from __future__ import annotations

import json
from pathlib import Path

from mozaiksai.core.control_plane import (
    ControlPlaneConfig,
    load_control_plane_config,
)


def test_factory_app_ai_config_enables_control_plane() -> None:
    app_root = Path(__file__).resolve().parents[1] / "factory_app" / "app"
    config = load_control_plane_config(app_root)

    assert config.enabled is True
    assert config.classifier.enabled is True
    assert config.classifier.llm_config == {
        "model": "gpt-4o-mini",
        "temperature": 0.0,
    }
    assert config.coding.enabled is False
    assert config.coding.llm_config == {
        "model": "gpt-5.2-codex",
        "temperature": 0.1,
    }


def test_missing_control_plane_config_defaults_disabled(tmp_path: Path) -> None:
    app_root = tmp_path / "app"
    (app_root / "config").mkdir(parents=True)
    (app_root / "config" / "ai.json").write_text(
        json.dumps({"chat": {"chat_startup_mode": "ask"}}),
        encoding="utf-8",
    )

    config = load_control_plane_config(app_root)

    assert config == ControlPlaneConfig()
