from __future__ import annotations

import json
from argparse import Namespace
from types import SimpleNamespace

from mozaiks_cli.commands import context_command
from mozaiks_cli.main import create_parser


def test_context_snapshot_parser_accepts_required_inputs() -> None:
    args = create_parser().parse_args(
        ["context", "snapshot", "--app-id", "app_1", "--workspace", ".", "--json"]
    )

    assert args.command == "context"
    assert args.context_action == "snapshot"
    assert args.app_id == "app_1"
    assert args.workspace == "."
    assert args.json_output is True


def test_context_snapshot_command_registers_workspace(monkeypatch, tmp_path, capsys) -> None:
    captured = {}

    async def fake_register_workspace_snapshot(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            app_id=kwargs["app_id"],
            app_bundle_artifact_version_id="av_bundle",
            app_context_version_id="ctx_app",
            app_context_artifact_version_id="av_context",
            graph_artifact_version_id="av_graph",
            artifact_path="generated/workspace_snapshots/app_1/artifact.zip",
            indexed_file_count=12,
            health_report={"status": "healthy", "warnings": [], "blockers": [], "coverage": {"core_surface_file_count": 4}},
            warnings=[],
        )

    monkeypatch.setattr(context_command, "register_workspace_snapshot", fake_register_workspace_snapshot)

    result = context_command.run(
        Namespace(
            context_action="snapshot",
            app_id="app_1",
            workspace=str(tmp_path),
            artifact_key="workspace_snapshot",
            draft=False,
            generated_artifacts_root=None,
            json_output=True,
        )
    )

    assert result == 0
    assert captured["app_id"] == "app_1"
    assert captured["workspace_root"] == tmp_path.resolve()
    assert captured["make_current"] is True
    payload = json.loads(capsys.readouterr().out)
    assert payload["app_bundle_artifact_version_id"] == "av_bundle"
    assert payload["health_report"]["status"] == "healthy"

