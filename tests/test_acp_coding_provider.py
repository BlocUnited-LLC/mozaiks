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
_NEW_PATH = "app/ui/pages/New.jsx"


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
                "env_source": dict(env_source),
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
async def test_host_credentials_are_not_an_implicit_acp_env_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "host-only-test-token")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "host-only-test-token")
    factory = _FakeConfigFactory(ACPTurn())
    provider = ACPCodingProvider(
        config_loader=_policy(), staging_root=tmp_path / "acp_staging",
        acp_config_factory=factory,
    )

    assert (await provider.execute(_request())).status == "empty"
    assert factory.calls[0]["env_source"] == {}


@pytest.mark.asyncio
async def test_exact_create_grant_accepts_only_the_named_new_file(tmp_path: Path) -> None:
    factory_ref: list = []
    factory = _FakeConfigFactory(_writing_turn(factory_ref, _NEW_PATH, "export const New = 1;\n"))
    factory_ref.append(factory)
    provider = ACPCodingProvider(
        config_loader=_policy(), staging_root=tmp_path / "create",
        acp_config_factory=factory, env_source={}, create_paths=[_NEW_PATH],
    )

    proposal = await provider.execute(_request(files={}))

    assert proposal.status == "completed"
    assert [(change.path, change.op) for change in proposal.changed_files] == [(_NEW_PATH, "create")]
    assert proposal.changed_files[0].content is not None
    assert proposal.changed_files[0].content.replace("\r\n", "\n") == "export const New = 1;\n"
    assert proposal.owned_paths == [_NEW_PATH]

    rogue_ref: list = []
    rogue = _FakeConfigFactory(_writing_turn(rogue_ref, "app/ui/pages/Rogue.jsx", "rogue\n"))
    rogue_ref.append(rogue)
    rejected = ACPCodingProvider(
        config_loader=_policy(), staging_root=tmp_path / "rogue",
        acp_config_factory=rogue, env_source={}, create_paths=[_NEW_PATH],
    )
    result = await rejected.execute(_request(files={}))
    assert result.status == "rejected_scope"
    assert result.changed_files == []


@pytest.mark.asyncio
async def test_exact_delete_grant_preserves_generated_app_update_only_default(tmp_path: Path) -> None:
    factory_ref: list = []

    async def _delete() -> None:
        workspace_root = factory_ref[0].calls[-1]["workspace_root"]
        (workspace_root / _SCOPED_PATH).unlink()

    factory = _FakeConfigFactory(ACPTurn(on_prompt=_delete))
    factory_ref.append(factory)
    granted = ACPCodingProvider(
        config_loader=_policy(), staging_root=tmp_path / "granted",
        acp_config_factory=factory, env_source={}, delete_paths=[_SCOPED_PATH],
    )
    proposal = await granted.execute(_request())
    assert proposal.status == "completed"
    assert [(change.path, change.op, change.content) for change in proposal.changed_files] == [
        (_SCOPED_PATH, "delete", None),
    ]

    default = _provider(factory, tmp_path / "default")
    rejected = await default.execute(_request())
    assert rejected.status == "rejected_scope"
    assert rejected.changed_files == []

    other_path = "app/ui/pages/Other.jsx"
    rogue_ref: list = []

    async def _delete_other() -> None:
        workspace_root = rogue_ref[0].calls[-1]["workspace_root"]
        (workspace_root / other_path).unlink()

    rogue = _FakeConfigFactory(ACPTurn(on_prompt=_delete_other))
    rogue_ref.append(rogue)
    ungranted = ACPCodingProvider(
        config_loader=_policy(), staging_root=tmp_path / "ungranted",
        acp_config_factory=rogue, env_source={}, delete_paths=[_SCOPED_PATH],
    )
    result = await ungranted.execute(_request(files={_SCOPED_PATH: _ORIGINAL, other_path: "other\n"}))
    assert result.status == "rejected_scope"
    assert result.changed_files == []


@pytest.mark.asyncio
async def test_invalid_operation_grant_fails_before_agent_starts(tmp_path: Path) -> None:
    factory = _FakeConfigFactory()
    provider = ACPCodingProvider(
        config_loader=_policy(), staging_root=tmp_path,
        acp_config_factory=factory, env_source={}, create_paths=[_SCOPED_PATH],
    )
    proposal = await provider.execute(_request())
    assert proposal.status == "failed"
    assert factory.calls == []


@pytest.mark.asyncio
async def test_completed_workspace_observer_runs_before_cleanup_only_for_accepted_turn(tmp_path: Path) -> None:
    factory_ref: list = []
    factory = _FakeConfigFactory(_writing_turn(factory_ref, _SCOPED_PATH, _PATCHED))
    factory_ref.append(factory)
    observed: list[bytes] = []
    provider = ACPCodingProvider(
        config_loader=_policy(), staging_root=tmp_path / "accepted",
        acp_config_factory=factory, env_source={},
        on_completed_workspace=lambda workspace: observed.append(
            (workspace.workspace_root / _SCOPED_PATH).read_bytes()
        ),
    )

    proposal = await provider.execute(_request())

    assert proposal.status == "completed"
    assert observed == [proposal.changed_files[0].content.encode("utf-8")]
    assert not factory.calls[0]["workspace_root"].exists()

    rejected_ref: list = []
    rejected_factory = _FakeConfigFactory(_writing_turn(rejected_ref, "app/rogue.py", "bad\n"))
    rejected_ref.append(rejected_factory)
    rejected = ACPCodingProvider(
        config_loader=_policy(), staging_root=tmp_path / "rejected",
        acp_config_factory=rejected_factory, env_source={},
        on_completed_workspace=lambda workspace: observed.append(b"should not run"),
    )
    assert (await rejected.execute(_request())).status == "rejected_scope"
    assert len(observed) == 1


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
async def test_default_local_acp_is_unavailable_until_isolated(tmp_path: Path) -> None:
    staging_root = tmp_path / "acp_staging"
    provider = ACPCodingProvider(
        config_loader=_policy(),
        staging_root=staging_root,
        env_source={"ANTHROPIC_API_KEY": "test-only"},
    )

    proposal = await provider.execute(_request())

    assert proposal.status == "unavailable"
    assert "verified OS isolation" in str(proposal.error)
    assert not staging_root.exists()


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


@pytest.mark.parametrize(
    ("adapter", "expected_env", "expected_command"),
    [
        ("claude_code", {"ANTHROPIC_API_KEY": "sk-ant-x"}, ["claude-agent-acp"]),
        ("codex", {"CODEX_API_KEY": "sk-codex-x"}, ["codex-acp"]),
        ("opencode", None, ["opencode", "acp"]),
    ],
)
def test_build_acp_agent_config_is_hardened(
    tmp_path: Path, adapter: str, expected_env: dict[str, str] | None, expected_command: list[str]
) -> None:
    env_source = {
        "ANTHROPIC_API_KEY": "sk-ant-x",
        "CODEX_API_KEY": "sk-codex-x",
        "OPENAI_API_KEY": "sk-oai-x",
        "MONGO_URI": "mongodb://secret",
        "MOZAIKSPAY_CLIENT_SECRET": "mps_secret",
        "PATH": "/usr/bin",
    }
    config = build_acp_agent_config(
        adapter=adapter,
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
    # Never forward another adapter's credential or platform secrets.
    assert config.env == expected_env
    assert config.command == expected_command


def test_codex_uses_openai_key_only_when_codex_key_is_absent(tmp_path: Path) -> None:
    config = build_acp_agent_config(
        adapter="codex",
        workspace_root=tmp_path,
        turn_timeout_seconds=300,
        env_source={"ANTHROPIC_API_KEY": "sk-ant-x", "OPENAI_API_KEY": "sk-oai-x"},
    )

    assert config.env == {"OPENAI_API_KEY": "sk-oai-x"}


def test_build_acp_agent_config_rejects_unknown_adapter(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Unknown ACP adapter"):
        build_acp_agent_config(
            adapter="mystery",
            workspace_root=tmp_path,
            turn_timeout_seconds=60,
            env_source={},
        )


def test_provider_prompt_separates_editable_and_read_only_files(tmp_path: Path) -> None:
    from mozaiksai.control_plane.workspace import materialize_coding_workspace

    workspace = materialize_coding_workspace(
        _request_files(), workspace_root=tmp_path / "ws",
        read_only_files={"tests/test_dashboard.py": "def test_dashboard(): pass\n"},
    )
    prompt = build_provider_prompt(_request(), workspace)

    assert _SCOPED_PATH in prompt
    assert "Read-only inspection files (do not modify or delete):\n- tests/test_dashboard.py" in prompt
    assert "Make the dashboard return 1" in prompt
    assert "changes anywhere else are rejected" in prompt


@pytest.mark.asyncio
async def test_provider_can_inspect_read_only_file_without_exporting_it(tmp_path: Path) -> None:
    inspected: list[str] = []
    factory_ref: list[_FakeConfigFactory] = []

    async def _inspect_and_write() -> None:
        config = factory_ref[0].configs[-1]
        inspected.append((Path(config.cwd) / "tests/test_dashboard.py").read_text(encoding="utf-8"))
        session = next(iter(config.sessions.values()))
        await session.bridge.write_text_file(
            content=_PATCHED, path=_SCOPED_PATH, session_id="fake-session-1",
        )

    factory = _FakeConfigFactory(ACPTurn(on_prompt=_inspect_and_write))
    factory_ref.append(factory)
    proposal = await _provider(factory, tmp_path).execute(_request(
        read_only_files={"tests/test_dashboard.py": "def test_dashboard(): pass\n"},
    ))

    assert inspected == ["def test_dashboard(): pass\n"]
    assert proposal.status == "completed"
    assert [change.path for change in proposal.changed_files] == [_SCOPED_PATH]


@pytest.mark.asyncio
async def test_provider_rejects_read_only_edit_from_acp_bridge(tmp_path: Path) -> None:
    factory_ref: list = []
    factory = _FakeConfigFactory(_writing_turn(
        factory_ref, "tests/test_dashboard.py", "changed\n",
    ))
    factory_ref.append(factory)

    proposal = await _provider(factory, tmp_path).execute(_request(
        read_only_files={"tests/test_dashboard.py": "original\n"},
    ))

    assert proposal.status == "rejected_scope"
    assert "read_only_modified" in (proposal.error or "")
    assert proposal.changed_files == []


@pytest.mark.asyncio
async def test_provider_budget_counts_inspection_files(tmp_path: Path) -> None:
    factory = _FakeConfigFactory()
    proposal = await _provider(factory, tmp_path).execute(_request(read_only_files={
        f"tests/test_{index}.py": "pass\n" for index in range(3)
    }))

    assert proposal.status == "budget_exceeded"
    assert factory.calls == []


def test_request_caps_inspection_content_before_provider() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="CODING_READ_ONLY_BUDGET"):
        _request(read_only_files={"tests/test_large.py": "x" * 2_097_153})


# ---------------------------------------------------------------------------
# Provider event capture (PR-6 observability)
# ---------------------------------------------------------------------------


def test_record_provider_event_captures_operational_events_only() -> None:
    from ag2.acp.events import ACPModeChange, ACPPlan, ACPPlanEntry
    from ag2.events import ModelReasoning
    from ag2.events.tool_events import BuiltinToolCallEvent

    from mozaiksai.control_plane.implementations.acp_coding_provider import record_provider_event

    records: list = []
    record_provider_event(ACPPlan([ACPPlanEntry(content="analyze files", status="in_progress")]), records)
    record_provider_event(BuiltinToolCallEvent(name="write_file", arguments="{}"), records)
    record_provider_event(ACPModeChange("edit"), records)
    # chain of thought must never be recorded
    record_provider_event(ModelReasoning("secret internal reasoning"), records)

    assert [(r.kind, r.summary) for r in records] == [
        ("plan", "[in_progress] analyze files"),
        ("tool_call", "write_file"),
        ("mode_change", "edit"),
    ]
    assert all("secret" not in r.summary for r in records)


def test_record_provider_event_enforces_bound() -> None:
    from ag2.acp.events import ACPModeChange

    from mozaiksai.control_plane.implementations.acp_coding_provider import (
        _MAX_PROVIDER_EVENTS,
        record_provider_event,
    )

    records: list = []
    for index in range(_MAX_PROVIDER_EVENTS + 25):
        record_provider_event(ACPModeChange(f"mode-{index}"), records)

    assert len(records) == _MAX_PROVIDER_EVENTS


@pytest.mark.asyncio
async def test_happy_path_proposal_carries_event_list(tmp_path: Path) -> None:
    factory_ref: list = []
    factory = _FakeConfigFactory(_writing_turn(factory_ref, _SCOPED_PATH, _PATCHED))
    factory_ref.append(factory)

    proposal = await _provider(factory, tmp_path).execute(_request())

    assert proposal.status == "completed"
    # the fake turn emits only message chunks, which are deliberately not
    # recorded as operational events
    assert proposal.provider_events == []
