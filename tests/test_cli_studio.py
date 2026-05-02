import json
from argparse import Namespace

from mozaiks_cli.commands import init_command, onboard_command, studio_command
from mozaiks_cli.main import create_parser


def test_studio_parser_accepts_json_output_flag() -> None:
    args = create_parser().parse_args(
        [
            "studio",
            "--dir",
            "sample-app",
            "--json",
        ]
    )

    assert args.command == "studio"
    assert args.directory == "sample-app"
    assert args.json_output is True


def test_studio_command_recommends_onboarding_for_blank_scaffold(tmp_path, capsys) -> None:
    target_dir = tmp_path / "blank-studio"
    init_command.run(Namespace(preset="chat", name="atlas", directory=str(target_dir), starter=False))

    studio_command.run(Namespace(directory=str(target_dir), json_output=False))
    captured = capsys.readouterr()

    assert "Studio Home" in captured.out
    assert "Run 'mozaiks onboard'" in captured.out
    assert "not configured" in captured.out


def test_studio_command_outputs_json_summary_for_onboarded_workspace(tmp_path, capsys) -> None:
    target_dir = tmp_path / "atlas-studio"
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

    capsys.readouterr()
    studio_command.run(Namespace(directory=str(target_dir), json_output=True))
    captured = capsys.readouterr()
    summary = json.loads(captured.out)

    assert summary["studio"]["surface"] == "cli-home"
    assert summary["studio"]["local_only"] is True
    assert summary["app"]["name"] == "Atlas CRM"
    assert summary["app"]["journey"] == "brownfield_app"
    assert summary["ai"]["provider"] == "anthropic"
    assert summary["ai"]["model"] == "claude-sonnet-4-5"
    assert summary["theme"]["primary"] == "blue"
    assert summary["admin"]["admin_emails"] == ["founder@example.com"]
    assert summary["workspace"]["workflow_count"] == 0
    assert summary["workspace"]["runtime_readiness"] == "no_workflows"
    assert "Connect the first host-owned surface" in summary["home"]["next_step"]