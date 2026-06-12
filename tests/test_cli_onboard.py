import json
from argparse import Namespace

from mozaiks_cli.commands import init_command, onboard_command
from mozaiks_cli.main import create_parser
from mozaiks_cli.workspace import resolve_theme_config_path, resolve_ui_route_manifest_path


def _load_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_onboard_parser_accepts_flags() -> None:
    args = create_parser().parse_args(
        [
            "onboard",
            "--dir",
            "sample-app",
            "--provider",
            "anthropic",
            "--non-interactive",
        ]
    )

    assert args.command == "onboard"
    assert args.directory == "sample-app"
    assert args.provider == "anthropic"
    assert args.non_interactive is True


def test_onboard_command_updates_scaffold_surfaces_non_interactively(tmp_path) -> None:
    target_dir = tmp_path / "atlas-app"
    init_command.run(Namespace(preset="chat", name="atlas", directory=str(target_dir), starter=False))

    onboard_command.run(
        Namespace(
            directory=str(target_dir),
            name="Atlas CRM",
            provider="anthropic",
            model="claude-sonnet-4-5",
            non_interactive=True,
            open_studio=False,
        )
    )

    app_json = _load_json(target_dir / "app" / "app.json")
    ai_json = _load_json(target_dir / "app" / "config" / "ai.json")
    control_plane_runtime = (target_dir / "control_plane" / "config" / "runtime.yaml").read_text(encoding="utf-8")
    shell_json = _load_json(target_dir / "app" / "config" / "shell.json")

    assert app_json["appName"] == "Atlas CRM"
    assert "onboarding" not in app_json

    assert ai_json["llm"] == {
        "provider": "anthropic",
        "model": "claude-sonnet-4-5",
    }
    assert ai_json["chat"]["chat_startup_mode"] == "ask"
    assert ai_json["workflows"]["entry_point"] == "ValueEngine"
    assert "control_plane" not in ai_json
    assert "enabled: true" in control_plane_runtime
    assert "llm_profile: classifier" in control_plane_runtime
    assert "llm_profile: codegen" in control_plane_runtime
    assert "app_context" not in ai_json
    assert shell_json["header"]["actions"]
    assert shell_json["notifications"]["show"] is True
    # onboard writes the canonical Studio ask prompt, not journey-specific onboarding content
    if "ask" in ai_json:
        assert "journey" not in str(ai_json["ask"])


def test_onboard_command_refreshes_blank_shell_placeholder(tmp_path) -> None:
    target_dir = tmp_path / "blank-shell-app"
    init_command.run(Namespace(preset="chat", name="blank-shell-app", directory=str(target_dir), starter=False))

    shell_path = target_dir / "app" / "config" / "shell.json"
    shell_path.write_text(
        json.dumps(
            {
                "header": {
                    "logo": {
                        "src": None,
                        "wordmark": None,
                        "alt": "blank-shell-app logo",
                        "href": "/",
                    },
                    "pages": [],
                    "actions": [],
                },
                "shortcuts": {
                    "profile": ["profile", "signout"],
                    "mobile": ["profile"],
                    "footer": [],
                    "footerHideOnMobile": True,
                },
                "navigation": {
                    "policy": {
                        "desktop": {
                            "global": "header",
                            "local": "sidebar",
                            "footer": "visible",
                        },
                        "mobile": {
                            "global": "bottomBar",
                            "local": "sheet",
                            "footer": "hidden",
                        },
                        "maxMobileItems": 5,
                        "autoFromPages": False,
                    }
                },
                "chrome": {
                    "defaultMode": "standard",
                    "modes": {
                        "standard": {
                            "desktop": {"header": True, "footer": True, "bottomBar": False, "localNav": True},
                            "mobile": {"header": True, "footer": False, "bottomBar": True, "localNav": "sheet"},
                        },
                        "workspace": {
                            "desktop": {"header": True, "footer": False, "bottomBar": False, "localNav": True},
                            "mobile": {"header": True, "footer": False, "bottomBar": True, "localNav": "sheet"},
                        },
                        "conversation": {
                            "desktop": {"header": True, "footer": False, "bottomBar": False, "localNav": False},
                            "mobile": {"header": True, "footer": False, "bottomBar": False, "localNav": False},
                        },
                        "focused": {
                            "desktop": {"header": True, "footer": False, "bottomBar": False, "localNav": False},
                            "mobile": {"header": True, "footer": False, "bottomBar": False, "localNav": False},
                        },
                        "immersive": {
                            "desktop": {"header": False, "footer": False, "bottomBar": False, "localNav": False},
                            "mobile": {"header": False, "footer": False, "bottomBar": False, "localNav": False},
                        },
                        "public": {
                            "desktop": {"header": True, "footer": True, "bottomBar": False, "localNav": False},
                            "mobile": {"header": True, "footer": False, "bottomBar": False, "localNav": False},
                        },
                    },
                },
                "notifications": {
                    "show": False,
                    "path": "/notifications",
                    "emptyText": "No notifications yet",
                },
                "footer": {
                    "links": [],
                    "visible": True,
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )

    onboard_command.run(
        Namespace(
            directory=str(target_dir),
            name="Blank Shell App",
            provider="anthropic",
            model="claude-sonnet-4-5",
            non_interactive=True,
            open_studio=False,
        )
    )

    refreshed_shell = _load_json(shell_path)
    assert refreshed_shell["header"]["actions"]
    assert refreshed_shell["notifications"]["show"] is True


def test_onboard_command_defaults_provider_when_not_provided(tmp_path) -> None:
    target_dir = tmp_path / "default-app"
    init_command.run(Namespace(preset="chat", name="myapp", directory=str(target_dir), starter=False))

    onboard_command.run(
        Namespace(
            directory=str(target_dir),
            name="My App",
            provider=None,
            model=None,
            non_interactive=True,
            open_studio=False,
        )
    )

    ai_json = _load_json(target_dir / "app" / "config" / "ai.json")
    shell_json = _load_json(target_dir / "app" / "config" / "shell.json")

    # Default provider is anthropic
    assert ai_json["llm"]["provider"] == "anthropic"
    assert ai_json["llm"]["model"] == "claude-sonnet-4-5"
    assert "control_plane" not in ai_json
    assert shell_json["header"]["actions"]


def test_onboard_command_prompts_when_values_are_missing(monkeypatch, tmp_path) -> None:
    target_dir = tmp_path / "prompt-onboard"
    init_command.run(Namespace(preset="chat", name="atlas", directory=str(target_dir), starter=False))

    # Responses: app name, provider choice (2=openai), model, decline open studio
    responses = iter(["Atlas Prime", "2", "gpt-4.1", "n"])
    monkeypatch.setattr("builtins.input", lambda _: next(responses))

    onboard_command.run(
        Namespace(
            directory=str(target_dir),
            name=None,
            provider=None,
            model=None,
            non_interactive=False,
            open_studio=False,
        )
    )

    app_json = _load_json(target_dir / "app" / "app.json")
    ai_json = _load_json(target_dir / "app" / "config" / "ai.json")

    assert app_json["appName"] == "Atlas Prime"
    assert "onboarding" not in app_json
    assert ai_json["llm"]["provider"] == "openai"
    assert ai_json["llm"]["model"] == "gpt-4.1"
    assert "control_plane" not in ai_json


def test_workspace_helpers_keep_brand_and_ui_inside_active_app_root(tmp_path) -> None:
    app_root = tmp_path / "workspace" / "app"
    assert resolve_theme_config_path(app_root) == app_root / "brand" / "theme_config.json"
    assert resolve_ui_route_manifest_path(app_root) == app_root / "ui" / "route_manifest.json"

