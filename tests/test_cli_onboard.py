import json
from argparse import Namespace

from mozaiks_cli.commands import init_command, onboard_command
from mozaiks_cli.main import create_parser
from mozaiks_cli.workspace import resolve_theme_config_path, resolve_ui_route_manifest_path


def _load_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_onboard_parser_accepts_guided_setup_flags() -> None:
    args = create_parser().parse_args(
        [
            "onboard",
            "--dir",
            "sample-app",
            "--journey",
            "brownfield_app",
            "--provider",
            "anthropic",
            "--theme-primary",
            "blue",
            "--non-interactive",
        ]
    )

    assert args.command == "onboard"
    assert args.directory == "sample-app"
    assert args.journey == "brownfield_app"
    assert args.provider == "anthropic"
    assert args.theme_primary == "blue"
    assert args.non_interactive is True


def test_onboard_command_updates_scaffold_surfaces_non_interactively(tmp_path) -> None:
    target_dir = tmp_path / "atlas-app"
    init_command.run(Namespace(preset="chat", name="atlas", directory=str(target_dir), starter=False))

    onboard_command.run(
        Namespace(
            directory=str(target_dir),
            name="Atlas CRM",
            journey="brownfield_app",
            goal="Bridge lead intake before building anything else",
            provider="anthropic",
            model="claude-sonnet-4-5",
            tagline="Private revenue workflows",
            theme_primary="blue",
            admin_email="founder@example.com",
            existing_url="https://example.com",
            host_owned_summary="Keep billing and auth host-owned",
            non_interactive=True,
        )
    )

    app_json = _load_json(target_dir / "app" / "app.json")
    ai_json = _load_json(target_dir / "app" / "config" / "ai.json")
    shell_json = _load_json(target_dir / "app" / "config" / "shell.json")
    theme_json = _load_json(target_dir / "app" / "brand" / "theme_config.json")
    admin_json = _load_json(target_dir / "app" / "config" / "admin.json")

    assert app_json["appName"] == "Atlas CRM"
    assert app_json["onboarding"]["journey"] == "brownfield_app"
    assert app_json["onboarding"]["first_goal"] == "Bridge lead intake before building anything else"
    assert app_json["onboarding"]["existing_app_url"] == "https://example.com"
    assert app_json["admins"] == ["founder@example.com"]

    assert ai_json["llm"] == {
        "provider": "anthropic",
        "model": "claude-sonnet-4-5",
    }
    assert "augment an existing app safely" in ai_json["ask"]["ask_mode_prompt"]
    assert ai_json["app_context"]["journey"] == "brownfield_app"

    assert shell_json["header"]["logo"]["alt"] == "Atlas CRM logo"
    assert theme_json["theme"]["primary"] == "blue"
    assert theme_json["identity"]["name"] == "Atlas CRM"
    assert theme_json["identity"]["tagline"] == "Private revenue workflows"
    assert theme_json["colors"]["primary"]["main"] == "#1d4ed8"
    assert admin_json["admin_emails"] == ["founder@example.com"]
    assert admin_json["schema_version"] == "mozaiks.admin.host.v1"
    assert admin_json["sections"]["usage"]["enabled"] is True
    assert admin_json["sections"]["billing"]["enabled"] is True
    assert admin_json["runtime_panels"][0]["section"] == "usage"
    assert admin_json["runtime_panels"][2]["section"] == "activity"


def test_onboard_command_prompts_when_values_are_missing(monkeypatch, tmp_path) -> None:
    target_dir = tmp_path / "prompt-onboard"
    init_command.run(Namespace(preset="chat", name="atlas", directory=str(target_dir), starter=False))

    responses = iter(
        [
            "Atlas Prime",
            "1",
            "Build the first customer intake workflow",
            "openai",
            "gpt-4.1",
            "Operator workspace",
            "emerald",
            "owner@example.com",
        ]
    )
    monkeypatch.setattr("builtins.input", lambda _: next(responses))

    onboard_command.run(
        Namespace(
            directory=str(target_dir),
            name=None,
            journey=None,
            goal=None,
            provider=None,
            model=None,
            tagline=None,
            theme_primary=None,
            admin_email=None,
            existing_url=None,
            host_owned_summary=None,
            non_interactive=False,
        )
    )

    app_json = _load_json(target_dir / "app" / "app.json")
    ai_json = _load_json(target_dir / "app" / "config" / "ai.json")
    theme_json = _load_json(target_dir / "app" / "brand" / "theme_config.json")
    admin_json = _load_json(target_dir / "app" / "config" / "admin.json")

    assert app_json["appName"] == "Atlas Prime"
    assert app_json["onboarding"]["journey"] == "greenfield_app"
    assert app_json["onboarding"]["first_goal"] == "Build the first customer intake workflow"
    assert ai_json["llm"]["provider"] == "openai"
    assert ai_json["llm"]["model"] == "gpt-4.1"
    assert theme_json["theme"]["primary"] == "emerald"
    assert theme_json["identity"]["tagline"] == "Operator workspace"
    assert admin_json["admin_emails"] == ["owner@example.com"]
    assert admin_json["schema_version"] == "mozaiks.admin.host.v1"
    assert admin_json["sections"]["overview"]["enabled"] is True
    assert admin_json["sections"]["users"]["enabled"] is True


def test_workspace_helpers_keep_brand_and_ui_inside_active_app_root(tmp_path) -> None:
    app_root = tmp_path / "workspace" / "app"
    assert resolve_theme_config_path(app_root) == app_root / "brand" / "theme_config.json"
    assert resolve_ui_route_manifest_path(app_root) == app_root / "ui" / "route_manifest.json"
