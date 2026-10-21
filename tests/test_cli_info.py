import json
from argparse import Namespace

import pytest

from mozaiks_cli.main import main
from tests.import_utils import import_module_directly

info_command = import_module_directly("mozaiks_cli.commands.info")


def _write_app_json(tmp_path, config: dict) -> None:
    (tmp_path / "app.json").write_text(json.dumps(config), encoding="utf-8")


def test_available_lists_all_presets_with_descriptions(capsys) -> None:
    assert info_command.run(Namespace(available=True)) == 0

    output = capsys.readouterr().out
    assert "engine       - AI workflow runtime only (headless API)" in output
    assert "chat         - AI workflows + chat UI (chatbot builders)" in output
    assert "integrated   - AI + chat + modules + event bus + auth (SaaS builders)" in output
    assert "full         - Everything including admin and subscriptions" in output


def test_current_config_prints_app_preset_and_resolved_features(
    monkeypatch, tmp_path, capsys
) -> None:
    _write_app_json(
        tmp_path,
        {"appName": "Atlas", "preset": "chat", "authRequired": False},
    )
    monkeypatch.chdir(tmp_path)

    assert info_command.run(Namespace(available=False)) == 0

    output = capsys.readouterr().out
    assert "App Name:      Atlas" in output
    assert "Preset:        chat" in output
    assert "Auth Required: False" in output
    assert "\u2713 ai_runtime" in output
    assert "\u2713 chat_ui" in output
    assert "\u2717 admin" in output


def test_feature_overrides_win_over_preset_defaults(monkeypatch, tmp_path, capsys) -> None:
    _write_app_json(
        tmp_path,
        {
            "appName": "Overrides",
            "preset": "engine",
            "features": {"ai_runtime": False, "admin": True},
        },
    )
    monkeypatch.chdir(tmp_path)

    assert info_command.run(Namespace(available=False)) == 0

    output = capsys.readouterr().out
    assert "\u2717 ai_runtime" in output
    assert "\u2713 admin" in output


def test_missing_app_json_prints_init_hint_and_returns_1(monkeypatch, tmp_path, capsys) -> None:
    monkeypatch.chdir(tmp_path)

    assert info_command.run(Namespace(available=False)) == 1

    output = capsys.readouterr().out
    assert "No app/app.json found." in output
    assert "Run 'mozaiks init <preset>' to create a new project." in output


def test_malformed_app_json_prints_readable_error_and_returns_1(
    monkeypatch, tmp_path, capsys
) -> None:
    (tmp_path / "app.json").write_text("{not-json", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    assert info_command.run(Namespace(available=False)) == 1

    output = capsys.readouterr().out
    assert "Error reading" in output
    assert "app.json" in output


def test_cli_info_without_app_json_exits_one(monkeypatch, tmp_path, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sys.argv", ["mozaiks", "info"])

    with pytest.raises(SystemExit) as exc_info:
        main()

    assert exc_info.value.code == 1
    assert "No app/app.json found." in capsys.readouterr().out


def test_cli_info_with_valid_app_json_exits_zero(monkeypatch, tmp_path, capsys) -> None:
    _write_app_json(tmp_path, {"appName": "Atlas", "preset": "full"})
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sys.argv", ["mozaiks", "info"])

    main()

    assert "App Name:      Atlas" in capsys.readouterr().out
