import json
from pathlib import Path

from mozaiks_cli import agent_guidance
from mozaiks_cli.commands import init_command, sync_agent_guidance_command
from mozaiks_cli.main import create_parser


def test_sync_agent_guidance_parser_accepts_safe_modes() -> None:
    args = create_parser().parse_args(
        ["sync-agent-guidance", "--dir", ".", "--write-missing"]
    )

    assert args.command == "sync-agent-guidance"
    assert args.directory == "."
    assert args.write_missing is True


def test_sync_agent_guidance_write_missing_creates_guidance(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    app_root = workspace / "app"
    app_root.mkdir(parents=True)
    (app_root / "app.json").write_text(
        json.dumps({"appName": "Sync App", "preset": "integrated"}) + "\n",
        encoding="utf-8",
    )

    statuses = sync_agent_guidance_command.sync_agent_guidance(
        workspace_root=workspace,
        app_name="Sync App",
        preset="integrated",
        mode="write-missing",
    )

    assert {status.status for status in statuses} == {"created"}
    assert (workspace / "AGENTS.md").exists()
    assert (workspace / "CLAUDE.md").exists()
    assert (workspace / ".claude" / "rules" / "modules.md").exists()
    assert (workspace / ".claude" / "skills" / "add-branding" / "SKILL.md").exists()
    assert (workspace / ".claude" / "skills" / "setup" / "SKILL.md").exists()

    current_statuses = sync_agent_guidance_command.sync_agent_guidance(
        workspace_root=workspace,
        app_name="Sync App",
        preset="integrated",
        mode="check",
    )
    assert {status.status for status in current_statuses} == {"current"}


def test_generated_agent_guidance_declares_app_backend_support_lane() -> None:
    assert init_command.build_agent_guidance_files is agent_guidance.build_agent_guidance_files

    files = init_command.build_agent_guidance_files("Backend Lane App", "integrated")

    agents = files[Path("AGENTS.md")]
    claude = files[Path("CLAUDE.md")]

    assert "`app/backend/` - optional app-owned support code" in agents
    assert "app/backend/integrations/" in agents
    assert "app/backend/adapters/" in agents
    assert "app/backend/security/" in agents
    assert "app/backend/routes/" in agents
    assert "app/config/secrets.yaml" in agents
    assert "Never store raw API keys" in agents
    assert "app/config/shared_persistence.json" in agents
    assert "workflows/" in agents
    assert "business actions, lifecycle state, emitted events, or persistence authority" in agents
    assert "auth/" in agents and "dns/" in agents and "registrar/" in agents and "secrets/" in agents
    assert "backend/  # optional integrations/adapters/security/routes support code" in claude
    assert "app/backend/integrations/<service>_client.py" in claude
    assert "app/backend/adapters/<area>/<provider>.py" in claude
    assert "app/backend/adapters/auth/<provider>.py" in claude
    assert "app/backend/security/" in claude
    assert "Secret management contract, names only" in claude
    assert "app/config/secrets.yaml" in claude
    assert "workflows/<WorkflowName>/" in claude
    assert Path(".claude/skills/add-branding/SKILL.md") in files
    assert "Studio/factory-generated modules and workflows" in claude
    assert "refreshed automatically by workspace commands" in agents


def test_package_manifest_includes_agent_guidance_templates() -> None:
    manifest = Path("MANIFEST.in").read_text(encoding="utf-8")

    assert "recursive-include mozaiks_cli/agent_guidance *.md" in manifest


def test_sync_agent_guidance_write_missing_preserves_unmanaged_files(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "AGENTS.md").write_text("custom agent guidance\n", encoding="utf-8")

    statuses = sync_agent_guidance_command.sync_agent_guidance(
        workspace_root=workspace,
        app_name="Custom App",
        preset="chat",
        mode="write-missing",
    )

    status_by_path = {str(status.relative_path): status.status for status in statuses}
    assert status_by_path["AGENTS.md"] == "unmanaged"
    assert status_by_path["CLAUDE.md"] == "created"
    assert (workspace / "AGENTS.md").read_text(encoding="utf-8") == "custom agent guidance\n"


def test_sync_agent_guidance_update_managed_block_preserves_custom_notes(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    init_command.create_scaffold(
        target_dir=workspace,
        preset="chat",
        app_name="Managed App",
        starter=False,
    )

    agents_path = workspace / "AGENTS.md"
    original = agents_path.read_text(encoding="utf-8")
    changed = original.replace(
        "Do not assume a sibling checkout",
        "STALE MANAGED TEXT",
    )
    agents_path.write_text(changed.rstrip() + "\n\n## Custom Notes\nKeep me.\n", encoding="utf-8")

    statuses = sync_agent_guidance_command.sync_agent_guidance(
        workspace_root=workspace,
        app_name="Managed App",
        preset="chat",
        mode="update",
    )

    status_by_path = {str(status.relative_path): status.status for status in statuses}
    assert status_by_path["AGENTS.md"] == "updated"
    updated = agents_path.read_text(encoding="utf-8")
    assert "Do not assume a sibling checkout" in updated
    assert "STALE MANAGED TEXT" not in updated
    assert "## Custom Notes\nKeep me." in updated


def test_sync_agent_guidance_force_overwrites_unmanaged_files(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    agents_path = workspace / "AGENTS.md"
    agents_path.write_text("custom only\n", encoding="utf-8")

    statuses = sync_agent_guidance_command.sync_agent_guidance(
        workspace_root=workspace,
        app_name="Forced App",
        preset="chat",
        mode="force",
    )

    status_by_path = {str(status.relative_path): status.status for status in statuses}
    assert status_by_path["AGENTS.md"] == "overwritten"
    agents = agents_path.read_text(encoding="utf-8")
    assert "custom only" not in agents
    assert "Forced App" in agents
    assert agent_guidance.AGENT_GUIDANCE_BEGIN in agents


def test_auto_sync_agent_guidance_updates_managed_blocks(tmp_path, capsys) -> None:
    workspace = tmp_path / "workspace"
    init_command.create_scaffold(
        target_dir=workspace,
        preset="chat",
        app_name="Managed App",
        starter=False,
    )
    agents_path = workspace / "AGENTS.md"
    stale = agents_path.read_text(encoding="utf-8").replace(
        "Do not assume a sibling checkout",
        "STALE PACKAGE GUIDANCE",
    )
    agents_path.write_text(stale, encoding="utf-8")

    sync_agent_guidance_command.auto_sync_agent_guidance(workspace)

    output = capsys.readouterr().out
    updated = agents_path.read_text(encoding="utf-8")
    assert "Agent guidance refreshed" in output
    assert "Do not assume a sibling checkout" in updated
    assert "STALE PACKAGE GUIDANCE" not in updated


def test_auto_sync_agent_guidance_skips_non_app_workspace(tmp_path) -> None:
    sync_agent_guidance_command.auto_sync_agent_guidance(tmp_path)

    assert not (tmp_path / "AGENTS.md").exists()
