"""Tests for deterministic coding-provider selection and the fallback ladder.

Dispatch is pure policy: the model never chooses its own execution provider,
and an ACP attempt that fails for operational reasons falls back to the
structured provider exactly once — while an out-of-scope ACP result surfaces
as a failure with no retry.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from mozaiksai.control_plane import (
    CodingWorkerRequest,
    ControlPlaneCodingCapabilityConfig,
    ControlPlaneConfig,
    ProposedFileChange,
    ScopedRefinementCodingWorker,
    StagedPatchProposal,
    select_coding_provider,
)

_FILE_A = "app/ui/pages/Dashboard.jsx"
_FILE_B = "app/ui/pages/Sidebar.jsx"


def _config(acp_enabled: bool = True, max_files: int = 3) -> ControlPlaneConfig:
    return ControlPlaneConfig(
        enabled=True,
        coding=ControlPlaneCodingCapabilityConfig.model_validate(
            {
                "enabled": True,
                "llm_config": {"model": "gpt-5.2-codex"},
                "providers": {"acp": {"enabled": acp_enabled, "budget": {"max_files": max_files}}},
            }
        ),
    )


def _request(files: dict[str, str] | None = None, **overrides: Any) -> CodingWorkerRequest:
    payload: dict[str, Any] = {
        "app_id": "app_1",
        "artifact_kind": "app_bundle",
        "artifact_key": "app_bundle",
        "artifact_version_id": "av_123",
        "raw_user_request": "Fix the layout",
        "change_class": "patch",
        "files": files if files is not None else {_FILE_A: "a", _FILE_B: "b"},
    }
    payload.update(overrides)
    return CodingWorkerRequest(**payload)


# ---------------------------------------------------------------------------
# Selection policy (pure)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("files", "acp_enabled", "importable", "kind", "expected", "reason_prefix"),
    [
        ({_FILE_A: "a", _FILE_B: "b"}, True, True, "app_bundle", "acp", "multi_file_scope_within_budget"),
        ({_FILE_A: "a", _FILE_B: "b"}, True, True, "theme_config", "acp", "multi_file_scope_within_budget"),
        ({_FILE_A: "a"}, True, True, "app_bundle", "structured_output", "single_file_scope"),
        ({_FILE_A: "a", _FILE_B: "b"}, False, True, "app_bundle", "structured_output", "acp_disabled"),
        ({_FILE_A: "a", _FILE_B: "b"}, True, False, "app_bundle", "structured_output", "acp_extra_not_installed"),
        ({_FILE_A: "a", _FILE_B: "b"}, True, True, "workflow_bundle", "structured_output", "artifact_kind_not_acp_eligible"),
    ],
)
def test_selection_matrix(
    files: dict[str, str],
    acp_enabled: bool,
    importable: bool,
    kind: str,
    expected: str,
    reason_prefix: str,
) -> None:
    selection = select_coding_provider(
        _request(files, artifact_kind=kind),
        _config(acp_enabled=acp_enabled),
        acp_importable=importable,
    )
    assert selection.provider == expected
    assert selection.reason.startswith(reason_prefix)


def test_scope_over_acp_budget_prefers_structured() -> None:
    files = {f"app/f{i}.py": "x" for i in range(5)}
    selection = select_coding_provider(_request(files), _config(max_files=3), acp_importable=True)

    assert selection.provider == "structured_output"
    assert selection.reason.startswith("scope_exceeds_acp_max_files")


# ---------------------------------------------------------------------------
# Worker dispatch + fallback ladder
# ---------------------------------------------------------------------------


class _StubProvider:
    def __init__(self, provider_id: str, proposal: StagedPatchProposal) -> None:
        self.provider_id = provider_id
        self._proposal = proposal
        self.calls = 0

    async def execute(self, request: CodingWorkerRequest) -> StagedPatchProposal:
        self.calls += 1
        return self._proposal


def _proposal(provider_id: str, status: str = "completed", **fields: Any) -> StagedPatchProposal:
    defaults: dict[str, Any] = {
        "proposal_id": "p1",
        "provider_id": provider_id,
        "status": status,
    }
    if status == "completed":
        defaults.update(
            summary="patch",
            rationale="stub",
            changed_files=[
                ProposedFileChange(path=_FILE_A, content="patched-a"),
                ProposedFileChange(path=_FILE_B, content="patched-b"),
            ],
            owned_paths=[_FILE_A, _FILE_B],
            validation_strategy_hint="local",
        )
    defaults.update(fields)
    return StagedPatchProposal(**defaults)


class _FakeArtifactStore:
    async def create_build_record(self, **kwargs):  # noqa: ANN003
        return type("ArtifactVersion", (), {"id": "av_child_1"})()


async def _passing_validation(**kwargs):  # noqa: ANN003
    return {"success": True, "validation_status": "passed", "execution_mode": "isolated_workspace_copy"}


def _worker(
    tmp_path: Path,
    *,
    acp: _StubProvider,
    structured: _StubProvider,
    acp_enabled: bool = True,
) -> ScopedRefinementCodingWorker:
    return ScopedRefinementCodingWorker(
        config_loader=lambda: _config(acp_enabled=acp_enabled),
        source_validation_runner=_passing_validation,
        artifact_store=_FakeArtifactStore(),
        output_root=tmp_path,
        provider=structured,
        acp_provider=acp,
    )


@pytest.mark.asyncio
async def test_multi_file_scope_dispatches_to_acp(tmp_path: Path) -> None:
    acp = _StubProvider("acp_claude_code", _proposal("acp_claude_code"))
    structured = _StubProvider("control_plane_coding", _proposal("control_plane_coding"))

    result = await _worker(tmp_path, acp=acp, structured=structured).execute(
        _request(validation_strategy="local")
    )

    assert acp.calls == 1
    assert structured.calls == 0
    assert result.status == "validated"
    assert result.provider == "acp_claude_code"
    attempts = result.metadata["coding_provider_attempts"]
    assert [a["provider"] for a in attempts] == ["acp_claude_code"]


@pytest.mark.asyncio
async def test_single_file_scope_stays_on_structured(tmp_path: Path) -> None:
    acp = _StubProvider("acp_claude_code", _proposal("acp_claude_code"))
    single = _proposal(
        "control_plane_coding",
        changed_files=[ProposedFileChange(path=_FILE_A, content="patched-a")],
        owned_paths=[_FILE_A],
    )
    structured = _StubProvider("control_plane_coding", single)

    result = await _worker(tmp_path, acp=acp, structured=structured).execute(
        _request(files={_FILE_A: "a"}, validation_strategy="local")
    )

    assert acp.calls == 0
    assert structured.calls == 1
    assert result.provider == "control_plane_coding"


@pytest.mark.asyncio
@pytest.mark.parametrize("acp_status", ["unavailable", "failed", "empty", "timeout", "budget_exceeded"])
async def test_operational_acp_failure_falls_back_to_structured(tmp_path: Path, acp_status: str) -> None:
    acp = _StubProvider("acp_claude_code", _proposal("acp_claude_code", status=acp_status, error="boom"))
    structured = _StubProvider("control_plane_coding", _proposal("control_plane_coding"))

    result = await _worker(tmp_path, acp=acp, structured=structured).execute(
        _request(validation_strategy="local")
    )

    assert acp.calls == 1
    assert structured.calls == 1
    assert result.status == "validated"
    assert result.provider == "control_plane_coding"
    attempts = result.metadata["coding_provider_attempts"]
    assert [(a["provider"], a["status"]) for a in attempts] == [
        ("acp_claude_code", acp_status),
        ("control_plane_coding", "completed"),
    ]
    assert attempts[1]["reason"] == "acp_fallback"


@pytest.mark.asyncio
async def test_scope_rejection_never_falls_back(tmp_path: Path) -> None:
    acp = _StubProvider(
        "acp_claude_code",
        _proposal("acp_claude_code", status="rejected_scope", error="out-of-scope edit: app/rogue.py"),
    )
    structured = _StubProvider("control_plane_coding", _proposal("control_plane_coding"))

    result = await _worker(tmp_path, acp=acp, structured=structured).execute(
        _request(validation_strategy="local")
    )

    assert acp.calls == 1
    assert structured.calls == 0
    assert result.status == "failed"
    assert result.provider == "acp_claude_code"
    assert "out-of-scope" in str(result.error)


@pytest.mark.asyncio
async def test_acp_disabled_config_never_dispatches_to_acp(tmp_path: Path) -> None:
    acp = _StubProvider("acp_claude_code", _proposal("acp_claude_code"))
    structured = _StubProvider("control_plane_coding", _proposal("control_plane_coding"))

    result = await _worker(tmp_path, acp=acp, structured=structured, acp_enabled=False).execute(
        _request(validation_strategy="local")
    )

    assert acp.calls == 0
    assert structured.calls == 1
    assert result.metadata["coding_provider_attempts"][0]["reason"] == "acp_disabled"
