"""Tests for the ACP-backed coding provider — no subprocess, no live model.

Every ACP interaction here runs through ``ag2.acp.testing.fake_acp_config``:
scripted in-process turns that drive the real bridge, session lifecycle, and
timeout machinery. The provider's safety properties are asserted from the
outside: workspace disposal, env allowlisting, harvest-based acceptance, and
fail-closed statuses for every non-happy path.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("acp", reason="ag2[acp] extra not installed")

from acp import schema  # noqa: E402
from ag2.acp.testing import ACPTurn, fake_acp_config  # noqa: E402

from mozaiksai.control_plane import (  # noqa: E402
    ControlPlaneCodingCapabilityConfig,
    ControlPlaneConfig,
)
from mozaiksai.control_plane.implementations.acp_coding_provider import (  # noqa: E402
    ACPCodingProvider,
    build_acp_agent_config,
    build_provider_prompt,
)

_SCOPED_PATH = "app/ui/pages/Dashboard.jsx"
_ORIGINAL = "export default function Dashboard() {}\n"
_PATCHED = "export default function Dashboard() { return 1; }\n"


def _policy(**acp_overrides: Any):
    def _load() -> ControlPlaneConfig:
        return ControlPlaneConfig(
            enabled=True,
            coding=ControlPlaneCodingCapabilityConfig.model_validate(
                {"enabled": True, "providers": {"acp": {"enabled": True, **acp_overrides}}}
            ),
        )

    return _load


def _request_files() -> dict[str, str]:
    return {_SCOPED_PATH: _ORIGINAL}


def _request(**overrides: Any):
    from mozaiksai.control_plane import CodingWorkerRequest

    payload: dict[str, Any] = {
        "app_id": "app_1",
        "artifact_kind": "app_bundle",
        "artifact_key": "app_bundle",
        "artifact_version_id": "av_123",
        "raw_user_request": "Make the dashboard return 1",
        "change_class": "patch",
        "files": _request_files(),
    }
    payload.update(overrides)
    return CodingWorkerRequest(**payload)


class _FakeConfigFactory:
    """Builds a FakeACPConfig per execution, letting turns write via the bridge."""

    def __init__(self, *turns: ACPTurn) -> None:
        self.turns = turns
        self.configs: list[Any] = []
        self.calls: list[dict[str, Any]] = []

    def __call__(self, *, adapter: str, workspace_root: Path, turn_timeout_seconds: int, env_source: dict) -> Any:
        self.calls.append(
            {
                "adapter": adapter,
                "workspace_root": workspace_root,
                "turn_timeout_seconds": turn_timeout_seconds,
            }
        )
        config = fake_acp_config(
            *self.turns,
            cwd=str(workspace_root),
            fs_root=str(workspace_root),
            permission_policy="auto",
            elicitation_policy="decline",
            expose_tools=False,
            allow_terminal=False,
            turn_timeout=float(turn_timeout_seconds),
        )
        self.configs.append(config)
        return config


def _writing_turn(factory_ref: list, path: str, content: str, *, message: str = "Patched the file.") -> ACPTurn:
    async def _write() -> None:
        config = factory_ref[0].configs[-1]
        session = next(iter(config.sessions.values()))
        await session.bridge.write_text_file(content=content, path=path, session_id="fake-session-1")

    return ACPTurn(
        on_prompt=_write,
        updates=[schema.AgentMessageChunk(content=schema.TextContentBlock(text=message, type="text"), session_update="agent_message_chunk")],
        usage=schema.Usage(input_tokens=100, output_tokens=20, total_tokens=120),
    )


def _provider(factory: _FakeConfigFactory, tmp_path: Path, **acp_overrides: Any) -> ACPCodingProvider:
    return ACPCodingProvider(
        config_loader=_policy(**acp_overrides),
        staging_root=tmp_path / "acp_staging",
        acp_config_factory=factory,
        env_source={},
    )


@pytest.mark.asyncio
async def test_happy_path_harvests_modified_file(tmp_path: Path) -> None:
    factory_ref: list = []
    factory = _FakeConfigFactory(_writing_turn(factory_ref, _SCOPED_PATH, _PATCHED))
    factory_ref.append(factory)

    provider = _provider(factory, tmp_path)
    proposal = await provider.execute(_request())

    assert proposal.status == "completed"
    assert proposal.provider_id == "acp_claude_code"
    assert [c.path for c in proposal.changed_files] == [_SCOPED_PATH]
    assert proposal.changed_files[0].op == "update"
    # the bridge's mediated write uses text mode, so Windows hosts produce
    # CRLF; harvest reports the exact on-disk bytes, which is the contract.
    assert proposal.changed_files[0].content.replace("\r\n", "\n") == _PATCHED
    assert proposal.owned_paths == [_SCOPED_PATH]
    assert proposal.validation_strategy_hint == "local"
    assert proposal.needs_human_review is True
    assert proposal.summary == "Patched the file."
    assert proposal.usage == {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120}
    # the disposable workspace must be gone (empty parent dirs may remain)
    staging = tmp_path / "acp_staging"
    leftovers = [p for p in staging.rglob("*") if p.is_file()] if staging.exists() else []
    assert leftovers == []


@pytest.mark.asyncio
async def test_out_of_scope_file_rejects_whole_proposal(tmp_path: Path) -> None:
    factory_ref: list = []
    factory = _FakeConfigFactory(_writing_turn(factory_ref, "app/rogue.py", "print('x')\n"))
    factory_ref.append(factory)

    proposal = await _provider(factory, tmp_path).execute(_request())

    assert proposal.status == "rejected_scope"
    assert proposal.changed_files == []
    assert "app/rogue.py" in str(proposal.error)
    assert "outside_allowlist" in str(proposal.error)


@pytest.mark.asyncio
async def test_no_modification_returns_empty(tmp_path: Path) -> None:
    factory = _FakeConfigFactory(
        ACPTurn(updates=[schema.AgentMessageChunk(content=schema.TextContentBlock(text="Nothing to do.", type="text"), session_update="agent_message_chunk")])
    )
    proposal = await _provider(factory, tmp_path).execute(_request())

    assert proposal.status == "empty"
    assert proposal.changed_files == []


@pytest.mark.asyncio
async def test_hanging_turn_times_out_and_accepts_nothing(tmp_path: Path) -> None:
    factory_ref: list = []
    # the agent writes a file, then hangs past the turn budget: the write must
    # NOT be accepted, because the turn did not complete inside policy.
    async def _write_then_never_finish() -> None:
        config = factory_ref[0].configs[-1]
        session = next(iter(config.sessions.values()))
        await session.bridge.write_text_file(content=_PATCHED, path=_SCOPED_PATH, session_id="fake-session-1")

    factory = _FakeConfigFactory(ACPTurn(on_prompt=_write_then_never_finish, hang=True))
    factory_ref.append(factory)

    provider = ACPCodingProvider(
        config_loader=_policy(budget={"max_wall_seconds": 30}),
        staging_root=tmp_path / "acp_staging",
        acp_config_factory=lambda **kw: factory(**{**kw, "turn_timeout_seconds": 1}),
        env_source={},
    )
    proposal = await provider.execute(_request())

    assert proposal.status == "timeout"
    assert proposal.changed_files == []


@pytest.mark.asyncio
async def test_disabled_provider_is_unavailable(tmp_path: Path) -> None:
    factory = _FakeConfigFactory()
    provider = ACPCodingProvider(
        config_loader=_policy(enabled=False),
        staging_root=tmp_path,
        acp_config_factory=factory,
        env_source={},
    )
    proposal = await provider.execute(_request())

    assert proposal.status == "unavailable"
    assert factory.calls == []  # never even built a config


@pytest.mark.asyncio
async def test_file_count_over_budget_fails_before_any_execution(tmp_path: Path) -> None:
    factory = _FakeConfigFactory()
    provider = _provider(factory, tmp_path, budget={"max_files": 1})
    files = {_SCOPED_PATH: _ORIGINAL, "app/other.py": "x = 1\n"}

    proposal = await provider.execute(_request(files=files))

    assert proposal.status == "budget_exceeded"
    assert factory.calls == []


@pytest.mark.asyncio
async def test_diff_over_budget_rejects_harvested_changes(tmp_path: Path) -> None:
    factory_ref: list = []
    factory = _FakeConfigFactory(_writing_turn(factory_ref, _SCOPED_PATH, "x" * 4096))
    factory_ref.append(factory)

    proposal = await _provider(factory, tmp_path, budget={"max_diff_bytes": 1024}).execute(_request())

    assert proposal.status == "budget_exceeded"
    assert proposal.changed_files == []


@pytest.mark.asyncio
async def test_secret_scoped_file_fails_closed(tmp_path: Path) -> None:
    factory = _FakeConfigFactory()
    proposal = await _provider(factory, tmp_path).execute(
        _request(files={"config/secrets.yaml": "key: old"})
    )

    assert proposal.status == "failed"
    assert "WORKSPACE_SECRET_PATH" in str(proposal.error)
    assert factory.calls == []


@pytest.mark.asyncio
async def test_workspace_is_cleaned_up_even_on_rejection(tmp_path: Path) -> None:
    factory_ref: list = []
    factory = _FakeConfigFactory(_writing_turn(factory_ref, "app/rogue.py", "x\n"))
    factory_ref.append(factory)
    staging = tmp_path / "acp_staging"

    await _provider(factory, tmp_path).execute(_request())

    leftovers = [p for p in staging.rglob("*") if p.is_file()] if staging.exists() else []
    assert leftovers == []


# ---------------------------------------------------------------------------
# Hardened config construction
# ---------------------------------------------------------------------------


def test_build_acp_agent_config_is_hardened(tmp_path: Path) -> None:
    env_source = {
        "ANTHROPIC_API_KEY": "sk-ant-x",
        "OPENAI_API_KEY": "sk-oai-x",
        "MONGO_URI": "mongodb://secret",
        "MOZAIKSPAY_CLIENT_SECRET": "mps_secret",
        "PATH": "/usr/bin",
    }
    config = build_acp_agent_config(
        adapter="claude_code",
        workspace_root=tmp_path,
        turn_timeout_seconds=300,
        env_source=env_source,
    )

    assert config.cwd == str(tmp_path)
    assert config.fs_root == str(tmp_path)
    assert config.permission_policy == "auto"
    assert config.elicitation_policy == "decline"
    assert config.expose_tools is False
    assert config.allow_terminal is False
    assert config.turn_timeout == 300.0
    # env allowlist: provider keys only — never platform secrets or PATH
    assert config.env == {"ANTHROPIC_API_KEY": "sk-ant-x", "OPENAI_API_KEY": "sk-oai-x"}
    assert config.command == ["claude-agent-acp"]


def test_build_acp_agent_config_rejects_unknown_adapter(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Unknown ACP adapter"):
        build_acp_agent_config(
            adapter="mystery",
            workspace_root=tmp_path,
            turn_timeout_seconds=60,
            env_source={},
        )


def test_provider_prompt_lists_only_editable_files(tmp_path: Path) -> None:
    from mozaiksai.control_plane.workspace import materialize_coding_workspace

    workspace = materialize_coding_workspace(_request_files(), workspace_root=tmp_path / "ws")
    prompt = build_provider_prompt(_request(), workspace)

    assert _SCOPED_PATH in prompt
    assert "Make the dashboard return 1" in prompt
    assert "changes anywhere else are discarded" in prompt
