"""Tests for ``mozaiks add``: parser contract, app.json edits, and exit codes."""

import json
import os
import stat
from argparse import Namespace

import pytest

from mozaiks_cli.commands import add as add_command
from mozaiks_cli.main import create_parser, main


def _write_app_json(workspace, config: dict):
    app_root = workspace / "app"
    app_root.mkdir(parents=True, exist_ok=True)
    app_json = app_root / "app.json"
    app_json.write_text(json.dumps(config), encoding="utf-8")
    return app_json


def _parse_error(capsys, argv: list[str]) -> str:
    with pytest.raises(SystemExit) as exc_info:
        create_parser().parse_args(argv)
    assert exc_info.value.code == 2
    return capsys.readouterr().err


# --- parser contract (#304) -------------------------------------------------


def test_parser_accepts_preset_without_feature() -> None:
    args = create_parser().parse_args(["add", "--preset", "full"])

    assert args.command == "add"
    assert args.preset == "full"
    assert args.feature is None


def test_parser_accepts_feature_without_preset() -> None:
    args = create_parser().parse_args(["add", "auth"])

    assert args.feature == "auth"
    assert args.preset is None


def test_parser_requires_feature_or_preset(capsys) -> None:
    err = _parse_error(capsys, ["add"])

    assert "one of the arguments feature --preset is required" in err


def test_parser_rejects_feature_combined_with_preset(capsys) -> None:
    err = _parse_error(capsys, ["add", "admin", "--preset", "integrated"])

    assert "not allowed with argument feature" in err


def test_parser_rejects_unknown_preset_and_lists_presets(capsys) -> None:
    err = _parse_error(capsys, ["add", "--preset", "enterprise"])

    assert "invalid choice" in err
    for preset in ("engine", "chat", "integrated", "full"):
        assert preset in err


def test_parser_preset_choices_match_command_presets() -> None:
    parser = create_parser()
    subparsers = next(
        action for action in parser._actions if action.dest == "command"
    )
    add_parser = subparsers.choices["add"]
    preset_action = next(action for action in add_parser._actions if action.dest == "preset")

    assert list(preset_action.choices) == list(add_command.TIER_PRESETS)


# --- app.json edits (#310) ---------------------------------------------------


def test_add_feature_sets_flag_and_keeps_other_keys(monkeypatch, tmp_path, capsys) -> None:
    app_json = _write_app_json(
        tmp_path,
        {"appName": "Atlas", "preset": "chat", "authRequired": False, "features": {"admin": False}},
    )
    monkeypatch.chdir(tmp_path)

    result = add_command.run(Namespace(feature="auth", preset=None))

    assert result == 0
    assert json.loads(app_json.read_text(encoding="utf-8")) == {
        "appName": "Atlas",
        "preset": "chat",
        "authRequired": False,
        "features": {"admin": False, "auth": True},
    }
    assert "Enabled feature: auth" in capsys.readouterr().out


def test_add_preset_sets_preset_and_clears_feature_overrides(monkeypatch, tmp_path, capsys) -> None:
    app_json = _write_app_json(
        tmp_path,
        {"appName": "Atlas", "preset": "chat", "features": {"auth": True, "admin": True}},
    )
    monkeypatch.chdir(tmp_path)

    result = add_command.run(Namespace(feature=None, preset="integrated"))

    assert result == 0
    assert json.loads(app_json.read_text(encoding="utf-8")) == {
        "appName": "Atlas",
        "preset": "integrated",
    }
    assert "Upgraded to preset: integrated" in capsys.readouterr().out


def test_add_rewrites_app_json_with_two_space_indent_and_trailing_newline(
    monkeypatch, tmp_path
) -> None:
    app_json = _write_app_json(tmp_path, {"appName": "Atlas", "preset": "chat"})
    monkeypatch.chdir(tmp_path)

    assert add_command.run(Namespace(feature="modules", preset=None)) == 0

    text = app_json.read_text(encoding="utf-8")
    expected = {"appName": "Atlas", "preset": "chat", "features": {"modules": True}}
    assert json.loads(text) == expected
    assert text == json.dumps(expected, indent=2, ensure_ascii=False) + "\n"


# --- failure paths return 1 and leave app.json alone (#305, #310) ------------


def test_unknown_preset_returns_1_lists_presets_and_leaves_app_json(
    monkeypatch, tmp_path, capsys
) -> None:
    app_json = _write_app_json(tmp_path, {"appName": "Atlas", "preset": "chat"})
    before = app_json.read_bytes()
    monkeypatch.chdir(tmp_path)

    result = add_command.run(Namespace(feature=None, preset="enterprise"))

    assert result == 1
    output = capsys.readouterr().out
    assert "Error: Unknown preset 'enterprise'" in output
    assert "Available: engine, chat, integrated, full" in output
    assert app_json.read_bytes() == before


def test_missing_app_json_returns_1_with_init_hint_and_writes_nothing(
    monkeypatch, tmp_path, capsys
) -> None:
    monkeypatch.chdir(tmp_path)

    result = add_command.run(Namespace(feature="auth", preset=None))

    assert result == 1
    output = capsys.readouterr().out
    assert "Error: No app/app.json found." in output
    assert "Run 'mozaiks init <preset>' to create a new project first." in output
    assert list(tmp_path.iterdir()) == []


def test_malformed_app_json_returns_1_and_leaves_file(monkeypatch, tmp_path, capsys) -> None:
    app_root = tmp_path / "app"
    app_root.mkdir()
    app_json = app_root / "app.json"
    app_json.write_text("{not-json", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    result = add_command.run(Namespace(feature="auth", preset=None))

    assert result == 1
    assert "Error reading" in capsys.readouterr().out
    assert app_json.read_text(encoding="utf-8") == "{not-json"


def test_unwritable_app_json_returns_1(monkeypatch, tmp_path, capsys) -> None:
    app_json = _write_app_json(tmp_path, {"appName": "Atlas", "preset": "chat"})
    before = app_json.read_bytes()
    os.chmod(app_json, stat.S_IREAD)
    try:
        if os.access(app_json, os.W_OK):
            pytest.skip("read-only file is still writable for this user (running as root)")
        monkeypatch.chdir(tmp_path)

        result = add_command.run(Namespace(feature="auth", preset=None))
    finally:
        os.chmod(app_json, stat.S_IREAD | stat.S_IWRITE)

    assert result == 1
    assert "Error writing" in capsys.readouterr().out
    assert app_json.read_bytes() == before


# --- process exit code through the CLI entry point (#304, #305) -------------


def test_cli_add_preset_upgrades_and_exits_zero(monkeypatch, tmp_path, capsys) -> None:
    app_json = _write_app_json(
        tmp_path, {"appName": "Atlas", "preset": "chat", "features": {"auth": True}}
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sys.argv", ["mozaiks", "add", "--preset", "full"])

    main()

    assert json.loads(app_json.read_text(encoding="utf-8")) == {"appName": "Atlas", "preset": "full"}
    assert "Upgraded to preset: full" in capsys.readouterr().out


def test_cli_add_without_app_json_exits_one(monkeypatch, tmp_path, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sys.argv", ["mozaiks", "add", "auth"])

    with pytest.raises(SystemExit) as exc_info:
        main()

    assert exc_info.value.code == 1
    assert "Error: No app/app.json found." in capsys.readouterr().out
