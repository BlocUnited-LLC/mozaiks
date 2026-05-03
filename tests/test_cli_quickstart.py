import json
from argparse import Namespace

from mozaiks_cli.commands import quickstart as quickstart_command
from mozaiks_cli.main import create_parser


def _load_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_quickstart_parser_accepts_builder_flags() -> None:
    args = create_parser().parse_args(
        [
            "quickstart",
            "--dir",
            "sample-app",
            "--name",
            "Atlas",
            "--provider",
            "openai",
            "--model",
            "gpt-4.1",
            "--no-browser",
        ]
    )

    assert args.command == "quickstart"
    assert args.directory == "sample-app"
    assert args.name == "Atlas"
    assert args.provider == "openai"
    assert args.model == "gpt-4.1"
    assert args.no_browser is True


def test_quickstart_bootstraps_workspace_and_launches_studio(monkeypatch, tmp_path, capsys) -> None:
    target_dir = tmp_path / "quickstart-app"
    launched = {}

    def fake_launch_studio(*, workspace_root, backend_port, frontend_port, open_browser):
        launched["workspace_root"] = workspace_root
        launched["backend_port"] = backend_port
        launched["frontend_port"] = frontend_port
        launched["open_browser"] = open_browser
        return {
            "backend_url": f"http://localhost:{backend_port}",
            "frontend_url": f"http://localhost:{frontend_port}",
            "studio_url": f"http://localhost:{frontend_port}/studio/create",
            "frontend_available": True,
        }

    monkeypatch.setattr("mozaiks_cli.commands.onboard.launch_studio", fake_launch_studio)

    quickstart_command.run(
        Namespace(
            directory=str(target_dir),
            preset="chat",
            name="Quickstart Atlas",
            journey="greenfield_app",
            goal="Define the first live operations workflow",
            provider="openai",
            model="gpt-4.1",
            backend_port=8010,
            frontend_port=3010,
            no_browser=True,
        )
    )

    captured = capsys.readouterr()
    app_json = _load_json(target_dir / "app" / "app.json")
    ai_json = _load_json(target_dir / "app" / "config" / "ai.json")

    assert "Mode: builder" in captured.out
    assert app_json["appName"] == "Quickstart Atlas"
    assert app_json["onboarding"]["journey"] == "greenfield_app"
    assert ai_json["llm"]["provider"] == "openai"
    assert ai_json["llm"]["model"] == "gpt-4.1"
    assert launched["workspace_root"] == target_dir
    assert launched["backend_port"] == 8010
    assert launched["frontend_port"] == 3010
    assert launched["open_browser"] is False
