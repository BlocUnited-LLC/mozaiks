from __future__ import annotations

import json
from pathlib import Path

from mozaiksai.core.runtime.app.studio_home import build_studio_create_summary


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{json.dumps(payload, indent=2)}\n", encoding="utf-8")


def _build_workspace(tmp_path: Path) -> Path:
    bundle_root = tmp_path / "workspace"
    platform_root = bundle_root / "app"
    _write_json(
        platform_root / "app.json",
        {
            "appName": "Atlas CRM",
            "preset": "chat",
            "onboarding": {
                "journey": "existing_app",
                "first_goal": "Bridge lead intake first",
            },
        },
    )
    _write_json(
        platform_root / "config" / "ai.json",
        {
            "llm": {"provider": "anthropic", "model": "claude-sonnet-4-5"},
            "workflows": {"entry_point": None},
        },
    )
    _write_json(platform_root / "config" / "shell.json", {"header": {"pages": [], "actions": []}})
    _write_json(platform_root / "brand" / "theme_config.json", {"theme": {"primary": "blue"}, "identity": {"tagline": "Private revenue workflows"}})
    _write_json(platform_root / "ui" / "route_manifest.json", {"pages": []})
    return platform_root


def test_studio_create_summary_defaults_without_persisted_state(tmp_path: Path) -> None:
    platform_root = _build_workspace(tmp_path)

    summary = build_studio_create_summary(platform_root)

    assert summary["studio"]["route"] == "/studio/create"
    assert summary["create"]["plan_state"] == "not_started"
    assert summary["create"]["approval_state"] == "not_started"
    assert summary["create"]["current_request"]["text"] == ""
    assert summary["create"]["recent_requests"] == []