"""Tests for ``mozaiks studio``: status output, JSON output, and exit codes.

Nothing here launches processes: ``launch_studio`` is always replaced, and the
agent-guidance sync is either replaced or confined to ``tmp_path``.
"""

import json
from argparse import Namespace

import pytest

from mozaiks_cli.commands import studio as studio_command
from mozaiks_cli.commands.init import create_scaffold
from mozaiks_cli.main import main

_OVERVIEW_LABELS = (
    "App:",
    "Provider / Model:",
    "Refinement Engine:",
    "Pages:",
    "Workflows:",
    "Runtime Readiness:",
)


def _summary(*, refinement_policy=None, provider="openai", model="gpt-4.1") -> dict:
    return {
        "studio": {
            "surface": "cli-home",
            "local_only": True,
            "workspace_root": "/workspace/app",
            "route": "/apps/atlas/overview",
            "environment": "local",
        },
        "app": {"id": "atlas", "name": "Atlas", "preset": "chat"},
        "ai": {
            "provider": provider,
            "model": model,
            "api_billed": True,
            "refinement_policy": refinement_policy,
        },
        "theme": {"primary": "cyan", "tagline": "AI-Powered Workflows", "logo_alt": None},
        "admin": {"enabled": True, "admins": ["ada@example.com"]},
        "workspace": {
            "page_count": 2,
            "custom_page_count": 1,
            "schema_page_count": 1,
            "workflow_count": 3,
            "workflow_names": ["A", "B", "ValueEngine"],
            "entry_point": "ValueEngine",
            "runtime_readiness": "entry_point_configured",
        },
        "home": {"next_step": "Start a build in Studio."},
    }


def _args(tmp_path, **overrides) -> Namespace:
    values = {
        "directory": str(tmp_path),
        "json_output": False,
        "open_studio": False,
        "backend_port": 8000,
        "frontend_port": 3000,
        "no_browser": False,
    }
    values.update(overrides)
    return Namespace(**values)


@pytest.fixture
def no_launch(monkeypatch):
    def fail_launch(**kwargs):
        raise AssertionError(f"launch_studio must not be called: {kwargs}")

    monkeypatch.setattr(studio_command, "launch_studio", fail_launch)


@pytest.fixture
def guidance_sync_calls(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "mozaiks_cli.commands.sync_agent_guidance.auto_sync_agent_guidance",
        lambda workspace_root: calls.append(workspace_root),
    )
    return calls


@pytest.fixture
def stub_summary(monkeypatch):
    """Report a complete scaffold and return a canned overview summary."""
    state = {"summary": _summary(), "calls": []}

    def fake_build(app_root, *, surface, local_only):
        state["calls"].append({"app_root": app_root, "surface": surface, "local_only": local_only})
        return state["summary"]

    monkeypatch.setattr("mozaiksai.core.runtime.app.get_missing_studio_surfaces", lambda app_root: [])
    monkeypatch.setattr("mozaiksai.core.runtime.app.build_app_overview_summary", fake_build)
    return state


# --- missing scaffold returns 1 (#305, #311) --------------------------------


def test_missing_scaffold_lists_each_file_and_points_at_onboard(
    monkeypatch, tmp_path, capsys, no_launch, guidance_sync_calls
) -> None:
    missing = ["app/app.json", "app/config/ai.json", "app/ui/route_manifest.json"]
    monkeypatch.setattr(
        "mozaiksai.core.runtime.app.get_missing_studio_surfaces", lambda app_root: list(missing)
    )

    def fail_build(*args, **kwargs):
        raise AssertionError("summary must not be built for a missing scaffold")

    monkeypatch.setattr("mozaiksai.core.runtime.app.build_app_overview_summary", fail_build)

    result = studio_command.run(_args(tmp_path))

    assert result == 1
    output = capsys.readouterr().out
    assert f"Error: no valid Mozaiks scaffold found in {tmp_path.resolve()}" in output
    assert "Missing required files:" in output
    for rel_path in missing:
        assert f"  - {rel_path}\n" in output
    assert "Run 'mozaiks onboard --dir <workspace>' to create/configure a scaffold first." in output
    assert guidance_sync_calls == []


def test_cli_studio_in_empty_workspace_exits_one(monkeypatch, tmp_path, capsys, no_launch) -> None:
    monkeypatch.setattr("sys.argv", ["mozaiks", "studio", "--dir", str(tmp_path)])

    with pytest.raises(SystemExit) as exc_info:
        main()

    assert exc_info.value.code == 1
    output = capsys.readouterr().out
    assert "Missing required files:" in output
    assert "  - app/app.json" in output
    assert list(tmp_path.iterdir()) == []


# --- status output (#311) ----------------------------------------------------


def test_json_output_round_trips_summary(
    tmp_path, capsys, no_launch, guidance_sync_calls, stub_summary
) -> None:
    result = studio_command.run(_args(tmp_path, json_output=True))

    assert result == 0
    assert json.loads(capsys.readouterr().out) == stub_summary["summary"]
    assert stub_summary["calls"] == [
        {"app_root": (tmp_path / "app").resolve(), "surface": "cli-home", "local_only": True}
    ]
    assert guidance_sync_calls == [tmp_path.resolve()]


def test_overview_renders_status_labels(
    tmp_path, capsys, no_launch, guidance_sync_calls, stub_summary
) -> None:
    stub_summary["summary"] = _summary(refinement_policy={"enabled": True, "profile": "balanced"})

    result = studio_command.run(_args(tmp_path))

    assert result == 0
    output = capsys.readouterr().out
    assert output.startswith("App Overview\n")
    for label in _OVERVIEW_LABELS:
        assert label in output
    assert "App:               Atlas\n" in output
    assert "Provider / Model:  openai / gpt-4.1\n" in output
    assert "Refinement Engine: enabled (balanced)\n" in output
    assert "Admins:            ada@example.com\n" in output
    assert "Pages:             2\n" in output
    assert "Workflows:         3\n" in output
    assert "Entry Point:       ValueEngine\n" in output
    assert "Runtime Readiness: entry_point_configured\n" in output
    assert "  Start a build in Studio.\n" in output


@pytest.mark.parametrize(
    ("refinement_policy", "expected"),
    [
        ({"enabled": True, "profile": "balanced"}, "enabled (balanced)"),
        ({"enabled": True, "profile": None}, "enabled"),
        ({"enabled": False, "profile": "strict"}, "disabled (strict)"),
        ({"enabled": False}, "disabled"),
        (None, "disabled"),
    ],
)
def test_refinement_engine_label(capsys, refinement_policy, expected) -> None:
    studio_command._print_app_overview(_summary(refinement_policy=refinement_policy))

    assert f"Refinement Engine: {expected}\n" in capsys.readouterr().out


def test_unconfigured_provider_and_model_are_labelled(capsys) -> None:
    studio_command._print_app_overview(_summary(provider=None, model=None))

    assert "Provider / Model:  not configured / not configured\n" in capsys.readouterr().out


def test_open_launches_with_requested_ports(
    monkeypatch, tmp_path, capsys, guidance_sync_calls, stub_summary
) -> None:
    launched = {}

    def fake_launch(*, workspace_root, backend_port, frontend_port, open_browser):
        launched.update(
            workspace_root=workspace_root,
            backend_port=backend_port,
            frontend_port=frontend_port,
            open_browser=open_browser,
        )
        return {
            "backend_url": f"http://localhost:{backend_port}",
            "frontend_url": f"http://localhost:{frontend_port}",
            "studio_url": f"http://localhost:{frontend_port}/apps",
            "frontend_available": True,
        }

    monkeypatch.setattr(studio_command, "launch_studio", fake_launch)

    result = studio_command.run(
        _args(tmp_path, open_studio=True, backend_port=8010, frontend_port=3010, no_browser=True)
    )

    assert result == 0
    assert launched == {
        "workspace_root": tmp_path.resolve(),
        "backend_port": 8010,
        "frontend_port": 3010,
        "open_browser": False,
    }
    output = capsys.readouterr().out
    assert "Studio launched." in output
    assert "Studio: http://localhost:3010/apps" in output
    assert stub_summary["calls"] == []


# --- real scaffold, real summary builder --------------------------------------


def test_real_scaffold_status_and_json_exit_zero(monkeypatch, tmp_path, capsys, no_launch) -> None:
    create_scaffold(target_dir=tmp_path, preset="chat", app_name="Atlas")

    monkeypatch.setattr("sys.argv", ["mozaiks", "studio", "--dir", str(tmp_path)])
    main()
    output = capsys.readouterr().out
    for label in _OVERVIEW_LABELS:
        assert label in output
    assert "App:               Atlas\n" in output

    assert studio_command.run(_args(tmp_path, json_output=True)) == 0
    json_output = capsys.readouterr().out
    summary = json.loads(json_output[json_output.index("{"):])
    assert summary["app"]["name"] == "Atlas"
    assert summary["app"]["preset"] == "chat"
