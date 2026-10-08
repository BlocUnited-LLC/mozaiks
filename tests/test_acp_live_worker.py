"""Credential-free contract tests for the one-shot real-adapter worker."""

from __future__ import annotations

import base64
import importlib.util
import io
import json
import os
import sys
import tomllib
import zipfile
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("acp", reason="ag2[acp] extra not installed")

from acp import schema  # noqa: E402
from ag2.acp.testing import ACPTurn, fake_acp_config  # noqa: E402

from mozaiksai.core.semantics.archive import read_archive_manifest  # noqa: E402

_PATH = Path(__file__).resolve().parents[1] / "infra/docker/acp_live/worker.py"
_SPEC = importlib.util.spec_from_file_location("acp_live_worker", _PATH)
assert _SPEC is not None and _SPEC.loader is not None
worker = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = worker
_SPEC.loader.exec_module(worker)

_SELECTED = "app/ui/pages/Dashboard.jsx"
_BEFORE = "export default function Dashboard() {}\n"
_AFTER = "export default function Dashboard() { return 1; }\n"
_TOKEN = "a" * 48


def _payload(**overrides: Any) -> dict[str, Any]:
    payload = {
        "app_id": "demo",
        "build_family": "app_bundle",
        "build_record_id": "av_parent",
        "change_class": "patch",
        "raw_user_request": "Return one from Dashboard",
        "files": {_SELECTED: _BEFORE},
        "read_only_files": {"tests/test_dashboard.py": "def test_dashboard(): pass\n"},
        "create_paths": [],
        "delete_paths": [],
        "live": {
            "adapter": "codex",
            "model": "gpt-5.3-codex",
            "gateway_url": "http://model-gateway:8765",
            "job_token": _TOKEN,
            "max_wall_seconds": 90,
        },
    }
    payload.update(overrides)
    return payload


def _request(**overrides: Any):
    return worker._parse_request(json.dumps(_payload(**overrides)).encode("utf-8"))


@pytest.mark.parametrize("change", [
    {"gateway_url": "http://api.openai.com"},
    {"job_token": "short"},
    {"job_token": "a" * 40 + "\nAuthorization: Bearer stolen"},
    {"adapter": "opencode"},
    {"max_wall_seconds": 5},
    {"model": "model\nother = 'provider'"},
])
def test_rejects_unapproved_live_routing(change: dict[str, Any]) -> None:
    live = {**_payload()["live"], **change}
    with pytest.raises(ValueError):
        _request(live=live)


def test_rejects_duplicate_keys_and_hidden_host_fields() -> None:
    raw = json.dumps(_payload()).encode("utf-8")
    with pytest.raises(ValueError, match="duplicate JSON key"):
        worker._parse_request(raw[:-1] + b',"app_id":"other"}')
    with pytest.raises(ValueError, match="unexpected worker input"):
        _request(metadata={"secret": "host-state"})


def test_rejects_non_patch_and_file_overflow() -> None:
    with pytest.raises(ValueError, match="invalid repository coding scope"):
        _request(change_class="core")
    files = {f"app/ui/pages/Page{i}.jsx": "x" for i in range(51)}
    with pytest.raises(ValueError, match="invalid repository coding scope"):
        _request(files=files, read_only_files={})


def test_codex_uses_private_user_level_provider_and_synthetic_token_only(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    home = tmp_path / "home" / ".codex"
    monkeypatch.setattr(worker, "_CODEX_HOME", home)
    request = _request()
    config = worker._live_config(request.live)(
        adapter="codex", workspace_root=tmp_path / "workspace",
        turn_timeout_seconds=90, env_source={},
    )
    content = tomllib.loads((home / "config.toml").read_text(encoding="utf-8"))
    assert content["model"] == "gpt-5.3-codex"
    assert content["model_provider"] == "mozaiks_gateway"
    assert content["model_providers"]["mozaiks_gateway"] == {
        "name": "Mozaiks job gateway",
        "base_url": "http://model-gateway:8765/v1",
        "env_key": "MOZAIKS_JOB_TOKEN",
        "wire_api": "responses",
        "requires_openai_auth": False,
        "supports_websockets": False,
    }
    assert _TOKEN not in (home / "config.toml").read_text(encoding="utf-8")
    assert config.command == ["codex-acp"]
    assert config.model == "gpt-5.3-codex"
    assert config.env["MOZAIKS_JOB_TOKEN"] == _TOKEN
    assert "OPENAI_API_KEY" not in config.env
    assert "CODEX_API_KEY" not in config.env
    assert config.expose_tools is False and config.allow_terminal is False
    with pytest.raises(FileExistsError):
        worker._live_config(request.live)(
            adapter="codex", workspace_root=tmp_path / "workspace",
            turn_timeout_seconds=90, env_source={},
        )


def test_claude_uses_gateway_auth_token_without_model_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setattr(worker, "_CLAUDE_HOME", tmp_path / "home" / ".claude")
    live = {**_payload()["live"], "adapter": "claude_code", "model": "claude-sonnet-4-5"}
    request = _request(live=live)
    config = worker._live_config(request.live)(
        adapter="claude_code", workspace_root=tmp_path / "workspace",
        turn_timeout_seconds=90, env_source={},
    )
    assert config.command == ["claude-agent-acp"]
    assert config.env["ANTHROPIC_BASE_URL"] == "http://model-gateway:8765"
    assert config.env["ANTHROPIC_AUTH_TOKEN"] == _TOKEN
    assert config.env["ANTHROPIC_MODEL"] == "claude-sonnet-4-5"
    assert "ANTHROPIC_API_KEY" not in config.env
    assert config.expose_tools is False and config.allow_terminal is False


def test_rejects_accidental_real_model_credential(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", "/home/sandbox")
    monkeypatch.setenv("OPENAI_API_KEY", "host-secret")
    with pytest.raises(RuntimeError, match="model credential reached"):
        worker._check_process_environment()


@pytest.mark.asyncio
async def test_fake_acp_turn_exports_exact_stopped_workspace(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """Exercise the real worker/provider/archive path without a model call."""
    configs: list[Any] = []

    async def write_selected_file() -> None:
        session = next(iter(configs[-1].sessions.values()))
        await session.bridge.write_text_file(
            content=_AFTER, path=_SELECTED, session_id="fake-session-1",
        )

    turn = ACPTurn(
        on_prompt=write_selected_file,
        updates=[schema.AgentMessageChunk(
            content=schema.TextContentBlock(text="Done.", type="text"),
            session_update="agent_message_chunk",
        )],
        usage=schema.Usage(input_tokens=10, output_tokens=5, total_tokens=15),
    )

    def fake_factory(_live):
        def configure(*, workspace_root: Path, turn_timeout_seconds: int, **_kwargs: Any):
            config = fake_acp_config(
                turn, cwd=str(workspace_root), fs_root=str(workspace_root),
                permission_policy="auto", elicitation_policy="decline",
                expose_tools=False, allow_terminal=False,
                turn_timeout=float(turn_timeout_seconds),
            )
            configs.append(config)
            return config

        return configure

    monkeypatch.setattr(worker, "_live_config", fake_factory)
    monkeypatch.setattr(worker, "_STAGING_ROOT", tmp_path / "staging")
    result = json.loads(await worker._execute(_request()))
    assert set(result) == {"proposal", "workspace_archive_base64"}
    assert result["proposal"]["status"] == "completed"
    expected_after = _AFTER.replace("\n", os.linesep)
    assert [(change["path"], change["op"], change["content"]) for change in result["proposal"]["changed_files"]] == [
        (_SELECTED, "update", expected_after),
    ]
    archive = base64.b64decode(result["workspace_archive_base64"], validate=True)
    manifest = read_archive_manifest(archive)
    assert [entry.path for entry in manifest.entries] == [_SELECTED]
    with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
        assert bundle.read(_SELECTED).decode("utf-8") == expected_after
    assert _TOKEN not in json.dumps(result)
    assert not list((tmp_path / "staging").glob("**/Dashboard.jsx"))
