from __future__ import annotations

import importlib.util
import json
from pathlib import Path


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{json.dumps(payload, indent=2)}\n", encoding="utf-8")


def _load_console_summary_module():
    workspace = Path(__file__).resolve().parents[1]
    file_path = workspace / "mozaiksai/core/runtime/app/console_summary.py"
    spec = importlib.util.spec_from_file_location("tests.console_summary_module", file_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def _build_workspace(tmp_path: Path) -> Path:
    bundle_root = tmp_path / "workspace"
    app_root = bundle_root / "app"
    _write_json(
        app_root / "app.json",
        {
            "appName": "Atlas CRM",
            "preset": "chat",
            "onboarding": {
                "journey": "brownfield_app",
                "first_goal": "Bridge lead intake first",
            },
        },
    )
    _write_json(
        app_root / "config" / "ai.json",
        {
            "llm": {"provider": "anthropic", "model": "claude-sonnet-4-5"},
            "workflows": {"entry_point": None},
        },
    )
    _write_json(app_root / "config" / "shell.json", {"header": {"pages": [], "actions": []}})
    _write_json(app_root / "brand" / "theme_config.json", {"theme": {"primary": "blue"}, "identity": {"tagline": "Private revenue workflows"}})
    _write_json(app_root / "ui" / "route_manifest.json", {"pages": []})
    return app_root


def test_build_summary_defaults_without_saved_state(tmp_path: Path) -> None:
    console_summary = _load_console_summary_module()
    app_root = _build_workspace(tmp_path)

    summary = console_summary.build_build_summary(app_root)

    assert summary["console"]["route"] == "/apps/app/build"
    assert summary["build"]["plan_state"] == "not_started"
    assert summary["build"]["approval_state"] == "not_started"
    assert summary["build"]["current_request"]["text"] == ""
    assert summary["build"]["recent_requests"] == []
