from __future__ import annotations

import json
from pathlib import Path

from mozaiksai.core.runtime.app.studio_home import build_studio_build_summary, save_studio_build_request


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{json.dumps(payload, indent=2)}\n", encoding="utf-8")


def _build_workspace(tmp_path: Path) -> Path:
    bundle_root = tmp_path / "workspace"
    platform_root = bundle_root / "platform"
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
    _write_json(bundle_root / "brand" / "theme_config.json", {"theme": {"primary": "blue"}, "identity": {"tagline": "Private revenue workflows"}})
    _write_json(bundle_root / "ui" / "extension.json", {"pages": []})
    return platform_root


def test_studio_build_summary_defaults_without_state_file(tmp_path: Path) -> None:
    platform_root = _build_workspace(tmp_path)

    summary = build_studio_build_summary(platform_root)

    assert summary["build"]["state_file"] == "platform/config/build.json"
    assert summary["build"]["plan_state"] == "not_started"
    assert summary["build"]["approval_state"] == "not_started"
    assert summary["build"]["current_request"]["text"] == ""
    assert summary["build"]["recent_requests"] == []


def test_save_studio_build_request_persists_request_history(tmp_path: Path) -> None:
    platform_root = _build_workspace(tmp_path)

    save_studio_build_request(
        platform_root,
        request_text="Build a lead intake flow for inbound sales ops.",
        request_kind="existing_app",
    )

    summary = build_studio_build_summary(platform_root)

    assert (platform_root / "config" / "build.json").exists()
    assert summary["build"]["plan_state"] == "draft_saved"
    assert summary["build"]["current_request"]["text"] == "Build a lead intake flow for inbound sales ops."
    assert summary["build"]["current_request"]["request_kind"] == "existing_app"
    assert summary["build"]["recent_requests"][0]["text"] == "Build a lead intake flow for inbound sales ops."
    assert summary["build"]["last_saved_at"]


def test_save_studio_build_request_persists_refinement_change_class(tmp_path: Path) -> None:
    platform_root = _build_workspace(tmp_path)

    save_studio_build_request(
        platform_root,
        request_text="Adjust the app shell for a premium finance brand.",
        request_kind="refinement",
        change_class="design",
    )

    summary = build_studio_build_summary(platform_root)

    assert summary["build"]["current_request"]["request_kind"] == "refinement"
    assert summary["build"]["current_request"]["change_class"] == "design"
    assert summary["build"]["recent_requests"][0]["change_class"] == "design"