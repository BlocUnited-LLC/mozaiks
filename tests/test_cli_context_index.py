from __future__ import annotations

import json
from argparse import Namespace
from types import SimpleNamespace

from mozaiks_cli.commands import context_command
from mozaiks_cli.main import create_parser


def test_context_index_parser_accepts_required_inputs() -> None:
    args = create_parser().parse_args(
        ["context", "index", "--app-id", "app_1", "--workspace", ".", "--json"]
    )

    assert args.command == "context"
    assert args.context_action == "index"
    assert args.app_id == "app_1"
    assert args.workspace == "."
    assert args.json_output is True


def test_context_index_command_registers_workspace(monkeypatch, tmp_path, capsys) -> None:
    captured = {}

    async def fake_index_workspace_app_intelligence(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            app_id=kwargs["app_id"],
            app_bundle_build_record_id="av_bundle",
            source_context_build_record_id="av_source",
            app_intelligence_build_record_id="av_intelligence",
            app_context_version_id="ctx_app",
            app_context_build_record_id="av_context",
            graph_build_record_id="av_graph",
            artifact_path="generated/app_intelligence/app_1/artifact.zip",
            indexed_file_count=12,
            health_report={"status": "healthy", "warnings": [], "blockers": [], "coverage": {"core_surface_file_count": 4}},
            warnings=[],
        )

    monkeypatch.setattr(context_command, "index_workspace_app_intelligence", fake_index_workspace_app_intelligence)

    result = context_command.run(
        Namespace(
            context_action="index",
            app_id="app_1",
            workspace=str(tmp_path),
            build_key="app_intelligence_workspace",
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
    assert payload["app_bundle_build_record_id"] == "av_bundle"
    assert payload["health_report"]["status"] == "healthy"


