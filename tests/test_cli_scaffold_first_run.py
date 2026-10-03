"""A brand-new scaffold must boot and render Studio without hand edits.

A release audit installed the wheel, ran ``mozaiks init`` and ``mozaiks
serve``, and the host died on the scaffold's own ``secrets.yaml``; with that
patched, the shell had no ``appId`` and the anonymous local user had no role
that opens Studio. These tests pin each scaffold writer (init, onboard,
quickstart) to the contracts the runtime enforces.
"""

from __future__ import annotations

import json
from argparse import Namespace

import pytest

from mozaiks_cli.commands import init_command, onboard_command
from mozaiks_cli.commands import quickstart as quickstart_command
from mozaiks_cli.main import main
from mozaiksai.core.secrets.contract import validate_secret_contract_text


def _load_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _env_example_values(path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key] = value
    return values


def _assert_first_run_ready(workspace, *, app_id: str) -> None:
    contract = validate_secret_contract_text(
        (workspace / "app" / "security" / "secrets.yaml").read_text(encoding="utf-8")
    )
    assert contract.version == 1
    assert contract.kind == "app_secret_contract"
    assert contract.provider is not None and contract.provider.type == "env"
    assert contract.secrets == []

    assert _load_json(workspace / "app" / "app.json")["appId"] == app_id

    env_example = _env_example_values(workspace / ".env.example")
    assert env_example["AUTH_ENABLED"] == "false"
    assert env_example["ENV"] == "development"
    assert env_example["AUTH_ANON_ROLES"] == "admin,user"


@pytest.mark.parametrize("preset", ["engine", "chat", "integrated", "full"])
def test_every_init_preset_writes_a_bootable_scaffold(tmp_path, preset) -> None:
    workspace = tmp_path / preset
    init_command.create_scaffold(
        target_dir=workspace, preset=preset, app_name="Stock", admin_email="operator@example.invalid",
    )

    _assert_first_run_ready(workspace, app_id="stock")


def test_init_command_derives_the_app_id_from_the_app_name(tmp_path) -> None:
    workspace = tmp_path / "ws"

    assert init_command.run(Namespace(preset="chat", name="Acme CRM 2", directory=str(workspace), starter=False)) == 0

    _assert_first_run_ready(workspace, app_id="acme-crm-2")


def test_secret_contract_example_uses_the_current_entry_shape() -> None:
    """The commented example, uncommented, is itself a valid contract body."""
    lines = init_command._secrets_yaml_placeholder().splitlines()
    example: list[str] = []
    for line in lines[lines.index("# Example entries:") + 1:]:
        if not line.startswith("#"):
            break
        example.append(line[2:])

    assert example[0] == "secrets:"
    contract = validate_secret_contract_text("version: 1\n" + "\n".join(example) + "\n")
    assert [entry.env for entry in contract.secrets] == ["OPENAI_API_KEY", "MONGO_URI"]


def test_scaffold_scripts_bind_loopback_by_default(tmp_path) -> None:
    workspace = tmp_path / "ws"
    init_command.create_scaffold(target_dir=workspace, preset="chat", app_name="Stock")

    for script in ("run-backend.ps1", "run-frontend.ps1"):
        text = (workspace / "scripts" / script).read_text(encoding="utf-8")
        assert '[string]$BindHost = "127.0.0.1"' in text
        assert '"0.0.0.0",' not in text


def test_onboard_keeps_the_app_id_when_the_app_is_renamed(tmp_path) -> None:
    workspace = tmp_path / "atlas-app"
    init_command.run(Namespace(preset="chat", name="atlas", directory=str(workspace), starter=False))

    result = onboard_command.run(
        Namespace(directory=str(workspace), name="Atlas CRM", provider=None, model=None,
                  non_interactive=True, open_studio=False)
    )

    assert result == 0
    app_json = _load_json(workspace / "app" / "app.json")
    assert app_json["appName"] == "Atlas CRM"
    assert app_json["appId"] == "atlas"


def test_onboard_gives_an_older_scaffold_without_an_app_id_one(tmp_path) -> None:
    workspace = tmp_path / "earlier"
    init_command.create_scaffold(target_dir=workspace, preset="chat", app_name="Earlier")
    app_json_path = workspace / "app" / "app.json"
    earlier = _load_json(app_json_path)
    earlier.pop("appId")
    app_json_path.write_text(json.dumps(earlier), encoding="utf-8")

    onboard_command.run(
        Namespace(directory=str(workspace), name="Earlier Notes", provider=None, model=None,
                  non_interactive=True, open_studio=False)
    )

    assert _load_json(app_json_path)["appId"] == "earlier-notes"


def test_quickstart_bootstraps_a_bootable_scaffold(monkeypatch, tmp_path) -> None:
    workspace = tmp_path / "qs"
    monkeypatch.setattr(
        "mozaiks_cli.commands.onboard.launch_studio",
        lambda **kwargs: {
            "backend_url": "http://localhost:8000",
            "frontend_url": None,
            "studio_url": None,
            "frontend_available": False,
        },
    )

    result = quickstart_command.run(
        Namespace(directory=str(workspace), preset="chat", name="Quick Start", provider=None,
                  model=None, backend_port=8000, frontend_port=3000, no_browser=True)
    )

    assert result == 0
    _assert_first_run_ready(workspace, app_id="quick-start")


def test_init_exits_nonzero_when_the_scaffold_already_exists(monkeypatch, tmp_path, capsys) -> None:
    workspace = tmp_path / "ws"
    init_command.create_scaffold(target_dir=workspace, preset="chat", app_name="Stock")
    monkeypatch.setattr(
        "sys.argv", ["mozaiks", "init", "chat", "--name", "Stock", "--dir", str(workspace)]
    )

    with pytest.raises(SystemExit) as exc:
        main()

    assert exc.value.code == 1
    assert "scaffold already exists" in capsys.readouterr().out
