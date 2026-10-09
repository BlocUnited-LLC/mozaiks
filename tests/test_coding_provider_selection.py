"""Tests for bounded coding-provider selection without silent fallback.

Dispatch is pure policy: the model never chooses its own execution provider,
and an ACP attempt that fails for operational reasons remains a failed attempt.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from mozaiksai.control_plane import (
    ACPCodingProvider,
    CodingWorkerRequest,
    ControlPlaneCodingCapabilityConfig,
    ControlPlaneConfig,
    ProposedFileChange,
    ScopedRefinementCodingWorker,
    StagedPatchProposal,
    select_coding_provider,
)
from mozaiksai.core.artifacts.models import BuildRecord

_FILE_A = "ui/pages/custom/Dashboard.jsx"
_FILE_B = "ui/pages/custom/Sidebar.jsx"


def _config(acp_enabled: bool = True, max_files: int = 3) -> ControlPlaneConfig:
    return ControlPlaneConfig(
        enabled=True,
        llm_profiles={"codegen": {"llm_config": {"model": "gpt-5.2-codex"}}},
        coding=ControlPlaneCodingCapabilityConfig.model_validate(
            {
                "enabled": True,
                "llm_profile": "codegen",
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
    ("files", "acp_enabled", "worker_ready", "validation_ready", "kind", "expected", "reason_prefix"),
    [
        ({_FILE_A: "a", _FILE_B: "b"}, True, True, True, "app_bundle", "acp", "bounded_scope_within_budget"),
        ({_FILE_A: "a", _FILE_B: "b"}, True, True, True, "theme_capture", "acp", "bounded_scope_within_budget"),
        ({_FILE_A: "a"}, True, True, True, "app_bundle", "acp", "bounded_scope_within_budget"),
        ({_FILE_A: "a", _FILE_B: "b"}, False, True, True, "app_bundle", "structured_output", "acp_disabled"),
        ({_FILE_A: "a", _FILE_B: "b"}, True, False, True, "app_bundle", None, "isolated_acp_worker_unavailable"),
        ({_FILE_A: "a", _FILE_B: "b"}, True, True, False, "app_bundle", None, "isolated_candidate_validation_unavailable"),
        ({_FILE_A: "a", _FILE_B: "b"}, True, True, True, "workflow_bundle", None, "artifact_kind_not_acp_eligible"),
        ({}, True, True, True, "app_bundle", None, "empty_file_scope"),
    ],
)
def test_selection_matrix(
    files: dict[str, str],
    acp_enabled: bool,
    worker_ready: bool,
    validation_ready: bool,
    kind: str,
    expected: str | None,
    reason_prefix: str,
) -> None:
    selection = select_coding_provider(
        _request(files, artifact_kind=kind),
        _config(acp_enabled=acp_enabled),
        acp_worker_ready=worker_ready,
        acp_validation_ready=validation_ready,
    )
    assert selection.provider == expected
    assert selection.reason.startswith(reason_prefix)


def test_scope_over_acp_budget_blocks_dispatch() -> None:
    files = {f"app/f{i}.py": "x" for i in range(5)}
    selection = select_coding_provider(
        _request(files), _config(max_files=3), acp_worker_ready=True, acp_validation_ready=True,
    )

    assert selection.provider is None
    assert selection.reason.startswith("scope_exceeds_acp_max_files")


# ---------------------------------------------------------------------------
# Worker dispatch and fail-closed results
# ---------------------------------------------------------------------------


class _StubProvider:
    def __init__(self, provider_id: str, proposal: StagedPatchProposal, *, ready: bool = True) -> None:
        self.provider_id = provider_id
        self.isolated_runtime_ready = ready
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
    async def get_build_record(self, *, app_id, build_record_id):
        return BuildRecord(
            id=build_record_id, app_id=app_id, build_family="app_bundle", build_key="app_bundle",
            version_number=1, lineage_root_id=build_record_id,
        )

    async def create_build_record(self, **kwargs):  # noqa: ANN003
        return BuildRecord(id="av_child_1", version_number=1, lineage_root_id="parent", **kwargs)


async def _passing_validation(**kwargs):  # noqa: ANN003
    return {
        "validation_status": "passed",
        "app_bundle_acceptance_result": {"status": "passed", "passed": True},
        "app_validation_result": {"validation_status": "passed", "validation_strategy": "local"},
    }


class _IsolatedValidationStub:
    def __init__(self, *, ready: bool = True) -> None:
        self.isolated_validation_ready = ready

    async def __call__(self, **kwargs: Any) -> dict[str, Any]:
        return await _passing_validation(**kwargs)


def _worker(
    tmp_path: Path,
    *,
    acp: _StubProvider,
    structured: _StubProvider,
    acp_enabled: bool = True,
    validation_ready: bool = True,
) -> ScopedRefinementCodingWorker:
    return ScopedRefinementCodingWorker(
        config_loader=lambda: _config(acp_enabled=acp_enabled),
        candidate_validation_runner=_IsolatedValidationStub(ready=validation_ready),
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
async def test_single_file_scope_dispatches_to_acp(tmp_path: Path) -> None:
    acp = _StubProvider(
        "acp_claude_code",
        _proposal(
            "acp_claude_code",
            changed_files=[ProposedFileChange(path=_FILE_A, content="patched-a")],
            owned_paths=[_FILE_A],
        ),
    )
    single = _proposal(
        "control_plane_coding",
        changed_files=[ProposedFileChange(path=_FILE_A, content="patched-a")],
        owned_paths=[_FILE_A],
    )
    structured = _StubProvider("control_plane_coding", single)

    result = await _worker(tmp_path, acp=acp, structured=structured).execute(
        _request(files={_FILE_A: "a"}, validation_strategy="local")
    )

    assert acp.calls == 1
    assert structured.calls == 0
    assert result.status == "validated"
    assert result.provider == "acp_claude_code"


@pytest.mark.asyncio
@pytest.mark.parametrize("acp_status", ["unavailable", "failed", "empty", "timeout", "budget_exceeded"])
async def test_operational_acp_failure_never_falls_back(tmp_path: Path, acp_status: str) -> None:
    acp = _StubProvider("acp_claude_code", _proposal("acp_claude_code", status=acp_status, error="boom"))
    structured = _StubProvider("control_plane_coding", _proposal("control_plane_coding"))

    result = await _worker(tmp_path, acp=acp, structured=structured).execute(
        _request(validation_strategy="local")
    )

    assert acp.calls == 1
    assert structured.calls == 0
    assert result.status == "failed"
    assert result.provider == "acp_claude_code"
    assert result.error == "boom"
    attempts = result.metadata["coding_provider_attempts"]
    assert [(a["provider"], a["status"]) for a in attempts] == [
        ("acp_claude_code", acp_status),
    ]


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
@pytest.mark.parametrize(
    ("files", "kind", "ready", "validation_ready", "reason"),
    [
        ({_FILE_A: "a"}, "app_bundle", False, True, "isolated_acp_worker_unavailable"),
        ({_FILE_A: "a"}, "app_bundle", True, False, "isolated_candidate_validation_unavailable"),
        ({f"app/f{i}.py": "x" for i in range(5)}, "app_bundle", True, True, "scope_exceeds_acp_max_files"),
        ({_FILE_A: "a"}, "workflow_bundle", True, True, "artifact_kind_not_acp_eligible"),
    ],
)
async def test_enabled_acp_blocks_without_dispatch(
    tmp_path: Path, files: dict[str, str], kind: str, ready: bool, validation_ready: bool, reason: str,
) -> None:
    acp = _StubProvider("acp_claude_code", _proposal("acp_claude_code"), ready=ready)
    structured = _StubProvider("control_plane_coding", _proposal("control_plane_coding"))

    result = await _worker(
        tmp_path, acp=acp, structured=structured, validation_ready=validation_ready,
    ).execute(
        _request(files=files, artifact_kind=kind, validation_strategy="local")
    )

    assert result.status == "ineligible"
    assert result.blocked_reason.startswith(reason)
    assert acp.calls == 0
    assert structured.calls == 0


def test_shipped_local_acp_provider_does_not_claim_isolated_readiness() -> None:
    assert ACPCodingProvider(config_loader=_config).isolated_runtime_ready is False


@pytest.mark.asyncio
async def test_default_candidate_validator_blocks_ready_acp_provider(tmp_path: Path) -> None:
    acp = _StubProvider("acp_claude_code", _proposal("acp_claude_code"))
    structured = _StubProvider("control_plane_coding", _proposal("control_plane_coding"))
    worker = ScopedRefinementCodingWorker(
        config_loader=lambda: _config(),
        output_root=tmp_path,
        provider=structured,
        acp_provider=acp,
    )

    result = await worker.execute(_request(validation_strategy="docker"))

    assert result.status == "ineligible"
    assert result.blocked_reason == "isolated_candidate_validation_unavailable"
    assert acp.calls == 0
    assert structured.calls == 0


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
