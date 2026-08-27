"""Tests for the CodingExecutionProvider boundary.

The coding worker owns eligibility, validation, artifact persistence, and the
checkpoint result shape; a provider owns only the production of staged file
changes as a provider-neutral ``StagedPatchProposal``. These tests pin:

- the structured-output provider produces a completed proposal contained to the
  scoped paths, with the same failure messages the worker previously raised;
- the worker consumes any conforming provider (boundary is real and swappable);
- proposal contracts reject unknown fields (structured-output-first rule).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from mozaiksai.control_plane import (
    CodingWorkerPlan,
    CodingWorkerRequest,
    ControlPlaneCheckpointManifest,
    ControlPlaneCodingCapabilityConfig,
    ControlPlaneConfig,
    ControlPlaneManifest,
    ControlPlanePromptDefinition,
    ControlPlanePromptsManifest,
    ControlPlaneToolResult,
    ControlPlaneToolsManifest,
    FileUpdate,
    LoadedControlPlanePack,
    ProposedFileChange,
    ScopedRefinementCodingWorker,
    StagedPatchProposal,
    StructuredOutputCodingProvider,
    safe_artifact_relpath,
)

_SCOPED_PATH = "app/ui/pages/Dashboard.jsx"

_GOOD_PLAN = CodingWorkerPlan(
    summary="Patch the dashboard file.",
    owned_paths=[_SCOPED_PATH],
    updated_files=[
        FileUpdate(path=_SCOPED_PATH, content='export default function Dashboard() { return "patched"; }')
    ],
    validation_strategy="local",
    validation_commands=["npm run build"],
    start_preview=False,
    needs_human_review=False,
    rationale="Single-file UI patch.",
)

_OUT_OF_SCOPE_PLAN = CodingWorkerPlan(
    summary="Bad edit",
    owned_paths=["app/ui/pages/Other.jsx"],
    updated_files=[FileUpdate(path="app/ui/pages/Other.jsx", content="x")],
    validation_strategy="skip",
    validation_commands=[],
    start_preview=False,
    needs_human_review=False,
    rationale="bad",
)


class _FakeReply:
    def __init__(self, result: Any) -> None:
        self._result = result

    async def content(self, *, retries: int = 0) -> Any:
        return self._result


class _FakeAgent:
    def __init__(self, system_prompt: str, llm_config: dict[str, Any], plan: CodingWorkerPlan) -> None:
        self.system_prompt = system_prompt
        self.llm_config = llm_config
        self.plan = plan

    async def ask(self, user_prompt: str, **kwargs: Any) -> _FakeReply:
        return _FakeReply(self.plan)


class _FakeToolExecutor:
    def __init__(self) -> None:
        self.calls: list[Any] = []

    async def execute_tool(self, call, *, context=None):  # noqa: ANN001, ANN003
        self.calls.append(call)
        return ControlPlaneToolResult(success=True, output={"tool_id": call.tool_id})


def _enabled_control_plane() -> ControlPlaneConfig:
    return ControlPlaneConfig(
        enabled=True,
        coding=ControlPlaneCodingCapabilityConfig(enabled=True, llm_config={"model": "gpt-5.2-codex"}),
    )


def _pack() -> LoadedControlPlanePack:
    return LoadedControlPlanePack(
        path=Path("factory_app/refinement_harness"),
        manifest=ControlPlaneManifest(
            schema_version="mozaiks.refinement_harness.v1",
            checkpoints=[
                ControlPlaneCheckpointManifest(
                    event="coding_requested",
                    prompt_id="coding_refinement_system",
                    tool_ids=[],
                )
            ],
        ),
        prompts=ControlPlanePromptsManifest(
            schema_version="mozaiks.refinement_harness.v1.prompts",
            prompts=[
                ControlPlanePromptDefinition(id="coding_refinement_system", content="coding system prompt")
            ],
        ),
        tools=ControlPlaneToolsManifest(
            schema_version="mozaiks.refinement_harness.tools.v1",
            tools=[],
        ),
    )


def _request(**overrides: Any) -> CodingWorkerRequest:
    payload: dict[str, Any] = {
        "app_id": "app_1",
        "artifact_kind": "app_bundle",
        "artifact_key": "app_bundle",
        "artifact_version_id": "av_123",
        "requested_workflow_id": "AppGenerator",
        "raw_user_request": "Fix the dashboard spacing",
        "source_surface": "app_build",
        "change_class": "patch",
        "files": {_SCOPED_PATH: "export default function Dashboard() {}"},
    }
    payload.update(overrides)
    return CodingWorkerRequest(**payload)


def _provider(plan: CodingWorkerPlan) -> StructuredOutputCodingProvider:
    return StructuredOutputCodingProvider(
        agent_factory=lambda sp, lc: _FakeAgent(sp, lc, plan),
        config_loader=_enabled_control_plane,
        pack_loader=_pack,
        tool_executor=_FakeToolExecutor(),
    )


# ---------------------------------------------------------------------------
# Structured provider
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_structured_provider_returns_completed_scoped_proposal() -> None:
    proposal = await _provider(_GOOD_PLAN).execute(_request())

    assert proposal.status == "completed"
    assert proposal.provider_id == "control_plane_coding"
    assert proposal.error is None
    assert [change.path for change in proposal.changed_files] == [_SCOPED_PATH]
    assert proposal.changed_files[0].op == "update"
    assert proposal.changed_files[0].content.endswith('"patched"; }')
    assert proposal.owned_paths == [_SCOPED_PATH]
    assert proposal.validation_strategy_hint == "local"
    assert proposal.validation_commands == ["npm run build"]
    assert proposal.summary == _GOOD_PLAN.summary
    assert proposal.rationale == _GOOD_PLAN.rationale


@pytest.mark.asyncio
async def test_structured_provider_fails_closed_on_out_of_scope_edit() -> None:
    proposal = await _provider(_OUT_OF_SCOPE_PLAN).execute(_request())

    assert proposal.status == "failed"
    assert proposal.changed_files == []
    assert "outside the explicit scoped files" in str(proposal.error)


@pytest.mark.asyncio
async def test_structured_provider_fails_closed_on_model_error() -> None:
    class _ExplodingAgent:
        def __init__(self, system_prompt: str, llm_config: dict[str, Any]) -> None:
            pass

        async def ask(self, user_prompt: str, **kwargs: Any) -> _FakeReply:
            raise RuntimeError("model unavailable")

    provider = StructuredOutputCodingProvider(
        agent_factory=_ExplodingAgent,
        config_loader=_enabled_control_plane,
        pack_loader=_pack,
        tool_executor=_FakeToolExecutor(),
    )
    proposal = await provider.execute(_request())

    assert proposal.status == "failed"
    assert "model unavailable" in str(proposal.error)


# ---------------------------------------------------------------------------
# Worker consumes the boundary
# ---------------------------------------------------------------------------


class _StubProvider:
    """A minimal non-LLM provider proving the boundary is swappable."""

    provider_id = "stub_provider"

    def __init__(self, proposal: StagedPatchProposal) -> None:
        self._proposal = proposal
        self.requests: list[CodingWorkerRequest] = []

    async def execute(self, request: CodingWorkerRequest) -> StagedPatchProposal:
        self.requests.append(request)
        return self._proposal


class _FakeArtifactStore:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def create_build_record(self, **kwargs):  # noqa: ANN003
        self.calls.append(dict(kwargs))
        return type("ArtifactVersion", (), {"id": "av_child_1"})()


async def _fake_source_validation_runner(**kwargs):  # noqa: ANN003
    return {
        "success": True,
        "validation_status": "passed",
        "execution_mode": "isolated_workspace_copy",
        "command_results": [],
        "fallback_checks": [],
        "warnings": [],
    }


def _completed_proposal(**overrides: Any) -> StagedPatchProposal:
    payload: dict[str, Any] = {
        "proposal_id": "prop_1",
        "provider_id": "stub_provider",
        "status": "completed",
        "summary": "Stub patch",
        "rationale": "Produced by an injected provider.",
        "changed_files": [
            ProposedFileChange(path=_SCOPED_PATH, op="update", content="stub content")
        ],
        "owned_paths": [_SCOPED_PATH],
        "validation_strategy_hint": "local",
    }
    payload.update(overrides)
    return StagedPatchProposal(**payload)


@pytest.mark.asyncio
async def test_worker_runs_injected_provider_through_full_lifecycle(tmp_path: Path) -> None:
    proposal = _completed_proposal()
    provider = _StubProvider(proposal)
    artifact_store = _FakeArtifactStore()

    worker = ScopedRefinementCodingWorker(
        config_loader=_enabled_control_plane,
        pack_loader=_pack,
        source_validation_runner=_fake_source_validation_runner,
        artifact_store=artifact_store,
        output_root=tmp_path,
        provider=provider,
    )
    result = await worker.execute(_request(validation_strategy="local"))

    assert provider.requests and provider.requests[0].app_id == "app_1"
    assert result.eligible is True
    assert result.status == "validated"
    assert result.provider == "stub_provider"
    assert result.applied_files == {_SCOPED_PATH: "stub content"}
    assert result.plan is not None
    assert result.plan.summary == "Stub patch"
    assert result.plan.validation_strategy == "local"
    assert result.metadata["applied_paths"] == [_SCOPED_PATH]
    assert artifact_store.calls[0]["parent_build_record_id"] == "av_123"
    assert artifact_store.calls[0]["lifecycle_status"].value == "draft"


@pytest.mark.asyncio
async def test_worker_fails_closed_when_provider_fails(tmp_path: Path) -> None:
    proposal = StagedPatchProposal(
        proposal_id="prop_2",
        provider_id="stub_provider",
        status="failed",
        error="provider exploded",
    )
    worker = ScopedRefinementCodingWorker(
        config_loader=_enabled_control_plane,
        pack_loader=_pack,
        source_validation_runner=_fake_source_validation_runner,
        artifact_store=_FakeArtifactStore(),
        output_root=tmp_path,
        provider=_StubProvider(proposal),
    )
    result = await worker.execute(_request())

    assert result.eligible is True
    assert result.status == "failed"
    assert result.provider == "stub_provider"
    assert result.error == "provider exploded"
    assert result.applied_files == {}
    assert result.validation_result is None


@pytest.mark.asyncio
async def test_worker_fails_closed_on_malformed_completed_proposal(tmp_path: Path) -> None:
    # A completed proposal with an empty summary cannot become a valid
    # CodingWorkerPlan; the worker must degrade to a failed result, not raise.
    proposal = _completed_proposal(summary="")
    worker = ScopedRefinementCodingWorker(
        config_loader=_enabled_control_plane,
        pack_loader=_pack,
        source_validation_runner=_fake_source_validation_runner,
        artifact_store=_FakeArtifactStore(),
        output_root=tmp_path,
        provider=_StubProvider(proposal),
    )
    result = await worker.execute(_request())

    assert result.status == "failed"
    assert result.provider == "stub_provider"


@pytest.mark.asyncio
async def test_worker_checks_eligibility_before_calling_provider(tmp_path: Path) -> None:
    provider = _StubProvider(_completed_proposal())
    worker = ScopedRefinementCodingWorker(
        config_loader=_enabled_control_plane,
        pack_loader=_pack,
        source_validation_runner=_fake_source_validation_runner,
        artifact_store=_FakeArtifactStore(),
        output_root=tmp_path,
        provider=provider,
    )
    result = await worker.execute(_request(change_class="feature"))

    assert result.status == "ineligible"
    assert provider.requests == []


# ---------------------------------------------------------------------------
# Contract hygiene
# ---------------------------------------------------------------------------


def test_staged_patch_proposal_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        StagedPatchProposal(
            proposal_id="p",
            provider_id="x",
            status="completed",
            surprise_field="nope",  # type: ignore[call-arg]
        )


def test_proposed_file_change_rejects_unknown_ops() -> None:
    with pytest.raises(ValidationError):
        ProposedFileChange(path="a.py", op="delete", content="")  # type: ignore[arg-type]


def test_safe_artifact_relpath_normalization() -> None:
    assert safe_artifact_relpath("app\\ui\\x.jsx") == "app/ui/x.jsx"
    assert safe_artifact_relpath("app/ui/x.jsx") == "app/ui/x.jsx"
    assert safe_artifact_relpath("/etc/passwd") is None
    assert safe_artifact_relpath("C:/win/system32") is None
    assert safe_artifact_relpath("../outside.py") is None
    assert safe_artifact_relpath("app/../../outside.py") is None
    assert safe_artifact_relpath("") is None
    assert safe_artifact_relpath(None) is None


# ---------------------------------------------------------------------------
# Workspace-backed persistence (PR-2)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_persistence_records_staged_file_hashes(tmp_path: Path) -> None:
    artifact_store = _FakeArtifactStore()
    worker = ScopedRefinementCodingWorker(
        config_loader=_enabled_control_plane,
        pack_loader=_pack,
        source_validation_runner=_fake_source_validation_runner,
        artifact_store=artifact_store,
        output_root=tmp_path,
        provider=_StubProvider(_completed_proposal()),
    )
    result = await worker.execute(_request(validation_strategy="local"))

    assert result.status == "validated"
    staged_hashes = artifact_store.calls[0]["commit_metadata"]["metadata"]["staged_file_sha256"]
    assert set(staged_hashes) == {_SCOPED_PATH}
    assert all(len(digest) == 64 for digest in staged_hashes.values())


@pytest.mark.asyncio
async def test_secret_scoped_file_fails_persistence_loudly(tmp_path: Path) -> None:
    secret_path = "config/secrets.yaml"
    proposal = _completed_proposal(
        changed_files=[ProposedFileChange(path=secret_path, op="update", content="key: value")],
        owned_paths=[secret_path],
    )
    worker = ScopedRefinementCodingWorker(
        config_loader=_enabled_control_plane,
        pack_loader=_pack,
        source_validation_runner=_fake_source_validation_runner,
        artifact_store=_FakeArtifactStore(),
        output_root=tmp_path,
        provider=_StubProvider(proposal),
    )
    result = await worker.execute(
        _request(files={secret_path: "key: old"}, validation_strategy="local")
    )

    assert result.status == "failed"
    assert "ARTIFACT_PERSISTENCE_FAILED" in str(result.error)
    assert "WORKSPACE_SECRET_PATH" in str(result.error)


@pytest.mark.asyncio
async def test_provider_execution_metadata_is_persisted(tmp_path: Path) -> None:
    artifact_store = _FakeArtifactStore()
    worker = ScopedRefinementCodingWorker(
        config_loader=_enabled_control_plane,
        pack_loader=_pack,
        source_validation_runner=_fake_source_validation_runner,
        artifact_store=artifact_store,
        output_root=tmp_path,
        provider=_StubProvider(_completed_proposal()),
    )
    result = await worker.execute(_request(validation_strategy="local"))

    assert result.status == "validated"
    execution = result.metadata["coding_provider"]
    assert execution["provider_id"] == "stub_provider"
    assert execution["attempts"] == result.metadata["coding_provider_attempts"]
    assert execution["events"] == []
    persisted = artifact_store.calls[0]["commit_metadata"]["metadata"]["coding_provider"]
    assert persisted["provider_id"] == "stub_provider"
