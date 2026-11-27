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

import json
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
from mozaiksai.core.artifacts.models import BuildRecord

_SCOPED_PATH = "app/ui/pages/Dashboard.jsx"

_GOOD_PLAN = CodingWorkerPlan(
    summary="Patch the dashboard file.",
    owned_paths=[_SCOPED_PATH],
    updated_files=[
        FileUpdate(path=_SCOPED_PATH, content='export default function Dashboard() { return "patched"; }')
    ],
    validation_strategy="docker",
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
    def __init__(self, system_prompt: str, llm_config: dict[str, Any], plan: CodingWorkerPlan | dict) -> None:
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
        llm_profiles={"codegen": {"llm_config": {"model": "gpt-5.2-codex"}}},
        coding=ControlPlaneCodingCapabilityConfig(enabled=True, llm_profile="codegen"),
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
        "user_id": "user_1",
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


def test_coding_prompt_presents_unicode_and_preserves_source_escapes():
    source = r"const label = 'café · 星'; const separator = '\u0000';"
    request = _request(files={_SCOPED_PATH: source})
    prompt = StructuredOutputCodingProvider._build_user_prompt(request=request, control_plane_context={})
    assert "café · 星" in prompt
    payload, _ = json.JSONDecoder().raw_decode(prompt.split("payload_json:\n", 1)[1])
    assert payload["input_files"] == {_SCOPED_PATH: ["1|" + source]}


def test_coding_prompt_excludes_runtime_state_and_keeps_edit_inputs():
    request = _request(validation_strategy="docker", start_preview=True)
    refinement_request = {"extra": {"files_manifest": ["unrelated.py"] * 1000}}
    routed = request.model_copy(update={
        "context_seed": {
            "refinement_request": refinement_request,
            "routing_decision": {"context_seed": {"refinement_request": refinement_request}},
        },
        "metadata": {"scope_proposal": {"selected_paths": [_SCOPED_PATH]}},
    })
    evidence = {"get_artifact_workspace_scope": {"selected_file_contracts": {_SCOPED_PATH: {"kind": "page"}}}}
    prompt = StructuredOutputCodingProvider._build_user_prompt(request=routed, control_plane_context=evidence)
    assert prompt == StructuredOutputCodingProvider._build_user_prompt(request=request, control_plane_context=evidence)
    payload, _ = json.JSONDecoder().raw_decode(prompt.split("payload_json:\n", 1)[1])
    assert payload["request"] == request.raw_user_request
    assert payload["build_record_id"] == request.build_record_id
    assert payload["file_paths"] == [_SCOPED_PATH]
    assert payload["input_files"] == {_SCOPED_PATH: ["1|" + request.files[_SCOPED_PATH]]}
    assert payload["refinement_context"] == evidence
    assert payload["validation_strategy"] == "docker"
    assert payload["start_preview_requested"] is True
    assert "context_seed" not in payload
    assert "metadata" not in payload


def _provider(plan: CodingWorkerPlan | dict) -> StructuredOutputCodingProvider:
    return StructuredOutputCodingProvider(
        agent_factory=lambda sp, lc, *, middleware: _FakeAgent(sp, lc, plan),
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
    assert proposal.validation_strategy_hint == "docker"
    assert proposal.validation_commands == ["npm run build"]
    assert proposal.summary == _GOOD_PLAN.summary
    assert proposal.rationale == _GOOD_PLAN.rationale


@pytest.mark.asyncio
async def test_structured_provider_uses_current_binding_and_host_owner() -> None:
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from mozaiksai.core.session.build_binding import RunBuildBinding
    from mozaiksai.core.usage.context import AuxiliaryUsageContext

    context = AuxiliaryUsageContext(app_id="factory", user_id="owner", tenant_id="tenant")
    binding = RunBuildBinding(
        target_app_id="app_1", build_registry_id="registry", build_id="revision", phase="refinement",
    )
    runner = SimpleNamespace(run=AsyncMock(return_value=_GOOD_PLAN))
    provider = StructuredOutputCodingProvider(
        agent_runner=runner, config_loader=_enabled_control_plane, pack_loader=_pack,
        tool_executor=_FakeToolExecutor(),
    )
    proposal = await provider.execute(_request(
        app_id="factory", target_app_id="app_1", user_id="owner", usage_context=context,
        run_build_binding=binding, metadata={"build_id": "forged", "user_id": "forged"},
    ))
    assert proposal.status == "completed"
    usage = runner.run.await_args.kwargs["usage_context"]
    assert (usage.app_id, usage.user_id, usage.tenant_id) == ("factory", "owner", "tenant")
    assert usage.run_build_binding == binding
    assert usage.chat_id is usage.workflow_name is None
    assert "usage_context" not in runner.run.await_args.kwargs["user_prompt"]


@pytest.mark.asyncio
async def test_structured_provider_fails_closed_on_out_of_scope_edit() -> None:
    proposal = await _provider(_OUT_OF_SCOPE_PLAN).execute(_request())

    assert proposal.status == "failed"
    assert proposal.changed_files == []
    assert "outside the explicit scoped files" in str(proposal.error)


@pytest.mark.asyncio
async def test_structured_provider_fails_closed_on_model_error() -> None:
    class _ExplodingAgent:
        def __init__(self, system_prompt: str, llm_config: dict[str, Any], *, middleware: list) -> None:
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

    async def get_build_record(self, *, app_id, build_record_id):
        return BuildRecord(
            id=build_record_id, app_id=app_id, build_family="app_bundle", build_key="app_bundle",
            version_number=1, lineage_root_id=build_record_id,
        )

    async def create_build_record(self, **kwargs):  # noqa: ANN003
        self.calls.append(dict(kwargs))
        return BuildRecord(id="av_child_1", version_number=1, lineage_root_id="av_parent", **kwargs)


async def _fake_candidate_validation_runner(**kwargs):  # noqa: ANN003
    return {
        "success": True,
        "validation_status": "passed",
        "app_bundle_acceptance_result": {"passed": True},
        "app_validation_result": {"validation_status": "passed", "validation_strategy": kwargs["validation_strategy"]},
        "validation_strategy": kwargs["validation_strategy"],
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
        "validation_strategy_hint": "docker",
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
        candidate_validation_runner=_fake_candidate_validation_runner,
        artifact_store=artifact_store,
        output_root=tmp_path,
        provider=provider,
    )
    result = await worker.execute(_request(validation_strategy="docker"))

    assert provider.requests and provider.requests[0].app_id == "app_1"
    assert result.eligible is True
    assert result.status == "validated"
    assert result.provider == "stub_provider"
    assert result.applied_files == {_SCOPED_PATH: "stub content"}
    assert result.plan is not None
    assert result.plan.summary == "Stub patch"
    assert result.plan.validation_strategy == "docker"
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
        candidate_validation_runner=_fake_candidate_validation_runner,
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
        candidate_validation_runner=_fake_candidate_validation_runner,
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
        candidate_validation_runner=_fake_candidate_validation_runner,
        artifact_store=_FakeArtifactStore(),
        output_root=tmp_path,
        provider=provider,
    )
    result = await worker.execute(_request(change_class="feature"))

    assert result.status == "ineligible"
    assert provider.requests == []


@pytest.mark.asyncio
async def test_generated_app_worker_rejects_repository_inspection_before_provider(tmp_path: Path) -> None:
    provider = _StubProvider(_completed_proposal())
    worker = ScopedRefinementCodingWorker(
        config_loader=_enabled_control_plane,
        pack_loader=_pack,
        candidate_validation_runner=_fake_candidate_validation_runner,
        artifact_store=_FakeArtifactStore(),
        output_root=tmp_path,
        provider=provider,
    )
    result = await worker.execute(_request(read_only_files={"tests/test_dashboard.py": "private context\n"}))

    assert result.status == "ineligible"
    assert "inspection files" in (result.blocked_reason or "")
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


@pytest.mark.parametrize("path", [
    "app/brand/x:payload.png",
    "app/brand/CON.png",
    "app/brand/COM¹.png",
    "app/brand/LPT².png",
    "app/brand/trailing. ",
    "app/brand/name?.png",
])
def test_safe_artifact_relpath_rejects_windows_unsafe_components(path: str) -> None:
    assert safe_artifact_relpath(path) is None


# ---------------------------------------------------------------------------
# Workspace-backed persistence (PR-2)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_persistence_records_staged_file_hashes(tmp_path: Path) -> None:
    artifact_store = _FakeArtifactStore()
    worker = ScopedRefinementCodingWorker(
        config_loader=_enabled_control_plane,
        pack_loader=_pack,
        candidate_validation_runner=_fake_candidate_validation_runner,
        artifact_store=artifact_store,
        output_root=tmp_path,
        provider=_StubProvider(_completed_proposal()),
    )
    result = await worker.execute(_request(validation_strategy="docker"))

    assert result.status == "validated"
    staged_hashes = artifact_store.calls[0]["commit_metadata"]["metadata"]["staged_file_sha256"]
    assert set(staged_hashes) == {_SCOPED_PATH}
    assert all(len(digest) == 64 for digest in staged_hashes.values())


@pytest.mark.asyncio
async def test_secret_scoped_file_fails_before_source_validation(tmp_path: Path) -> None:
    secret_path = "config/secrets.yaml"
    proposal = _completed_proposal(
        changed_files=[ProposedFileChange(path=secret_path, op="update", content="key: value")],
        owned_paths=[secret_path],
    )
    worker = ScopedRefinementCodingWorker(
        config_loader=_enabled_control_plane,
        pack_loader=_pack,
        candidate_validation_runner=_fake_candidate_validation_runner,
        artifact_store=_FakeArtifactStore(),
        output_root=tmp_path,
        provider=_StubProvider(proposal),
    )
    result = await worker.execute(
        _request(files={secret_path: "key: old"}, validation_strategy="docker")
    )

    assert result.status == "failed"
    assert "CANDIDATE_VALIDATION_FAILED" in str(result.error)
    assert "WORKSPACE_SECRET_PATH" in str(result.error)
    assert "build_record_id" not in result.metadata


@pytest.mark.asyncio
async def test_provider_execution_metadata_is_persisted(tmp_path: Path) -> None:
    artifact_store = _FakeArtifactStore()
    worker = ScopedRefinementCodingWorker(
        config_loader=_enabled_control_plane,
        pack_loader=_pack,
        candidate_validation_runner=_fake_candidate_validation_runner,
        artifact_store=artifact_store,
        output_root=tmp_path,
        provider=_StubProvider(_completed_proposal()),
    )
    result = await worker.execute(_request(validation_strategy="docker"))

    assert result.status == "validated"
    execution = result.metadata["coding_provider"]
    assert execution["provider_id"] == "stub_provider"
    assert execution["attempts"] == result.metadata["coding_provider_attempts"]
    assert execution["events"] == []
    persisted = artifact_store.calls[0]["commit_metadata"]["metadata"]["coding_provider"]
    assert persisted["provider_id"] == "stub_provider"

def _anchored_response(path, replacements, *, owned_paths=None):
    return {
        **_GOOD_PLAN.model_dump(exclude={"updated_files", "owned_paths"}),
        "owned_paths": [path] if owned_paths is None else owned_paths,
        "updated_files": [{"path": path, "replacements": [
            {"old_text": old, "new_text": new} for old, new in replacements
        ]}],
    }


@pytest.mark.asyncio
async def test_numbered_source_round_trips_and_targets_only_the_selected_form():
    path = "ui/pages/records.yaml"
    source = (
        "create:\r\n  label: Save\r\n\r\nedit:\n  label: Save\n  other: \\n|Save\r"
        "  submit: Save"
    )
    request = _request(files={path: source})
    prompt = StructuredOutputCodingProvider._build_user_prompt(request=request, control_plane_context={})
    payload, _ = json.JSONDecoder().raw_decode(prompt.split("payload_json:\n", 1)[1])
    assert payload["input_files"] == {path: [
        "1|create:\r\n", "2|  label: Save\r\n", "3|\r\n", "4|edit:\n", "5|  label: Save\n",
        "6|  other: \\n|Save\r", "7|  submit: Save",
    ]}
    assert "".join(line.split("|", 1)[1] for line in payload["input_files"][path]) == source
    wire = _anchored_response(path, [])
    wire["updated_files"][0]["replacements"] = [
        {"line_number": number, "old_text": "Save", "new_text": "Save changes"} for number in (7, 5)
    ] + [{"line_number": 6, "old_text": r"\n", "new_text": r"\t"}]
    result = await _provider(wire).execute(request)
    assert result.status == "completed", result.error
    assert result.changed_files[0].content == (
        "create:\r\n  label: Save\r\n\r\nedit:\n  label: Save changes\n  other: \\t|Save\r"
        "  submit: Save changes"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(("edits", "reason"), [
    ([{"line_number": 1, "old_text": "Save", "new_text": "Next"}], "exactly once"),
    ([{"line_number": 2, "old_text": "create", "new_text": "X"}], "exactly once"),
    ([{"line_number": 3, "old_text": "Edit", "new_text": "X"}], "outside"),
    ([{"line_number": 0, "old_text": "Edit", "new_text": "X"}], "greater than or equal"),
    ([{"line_number": "2", "old_text": "Edit", "new_text": "X"}], "valid integer"),
    ([{"line_number": 2, "old_text": "Edit\n", "new_text": "X"}], "line endings"),
    ([{"line_number": 2, "old_text": "Edit", "new_text": "X\r\nY"}], "line endings"),
    ([{"line_number": 2, "old_text": "Edit", "new_text": "Update"},
      {"old_text": "\nEdit: ", "new_text": "\nModify: "}], "overlap"),
    ([{"line_number": 2, "old_text": "Save", "new_text": "Save"}], "no change"),
])
async def test_bad_line_replacements_reject_the_complete_proposal(edits, reason):
    wire = _anchored_response(_SCOPED_PATH, [])
    wire["updated_files"][0]["replacements"] = edits
    result = await _provider(wire).execute(_request(files={_SCOPED_PATH: "create Save Save\r\nEdit: Save\n"}))
    assert result.status == "failed" and not result.changed_files
    assert reason in result.error


@pytest.mark.asyncio
async def test_anchored_edits_preserve_unselected_text_and_use_original_offsets():
    path = "ui/pages/records.yaml"
    old = "# Crème\r\ncreate: Save\r\nedit: Save\r\ncancel: Cancel\r\n"
    wire = _anchored_response(path, [("edit: Save", "edit: Save changes"), ("# Crème", "# Dossier")])
    proposal = await _provider(wire).execute(_request(files={path: old}))

    assert proposal.status == "completed", proposal.error
    assert proposal.owned_paths == [path]
    assert proposal.changed_files == [
        ProposedFileChange(path=path, op="update", content="# Dossier\r\ncreate: Save\r\nedit: Save changes\r\ncancel: Cancel\r\n")
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(("source", "replacements", "reason"), [
    ("aaaa", [("aa", "z")], "exactly once"),
    ("record: 42", [("reCord", "x")], "exactly once"),
    ("abcde", [("abc", "t"), ("cde", "r")], "overlap"),
    ("text", [("text", "text")], "no change"),
    ("xy", [("x", "xy"), ("y", "")], "no change"),
    ("abc", [("abc", "new"), ("new", "next")], "exactly once"),
    ("abc", [("", "new")], "at least 1"),
])
async def test_invalid_anchors_refuse_entire_proposal(source, replacements, reason):
    response = _anchored_response(_SCOPED_PATH, replacements)
    result = await _provider(response).execute(_request(files={_SCOPED_PATH: source}))
    assert result.status == "failed" and result.changed_files == []
    assert reason in result.error


@pytest.mark.asyncio
async def test_adjacent_anchored_edits_and_empty_replacement():
    result = await _provider(_anchored_response(_SCOPED_PATH, [("a", ""), ("bc", "c")])).execute(
        _request(files={_SCOPED_PATH: "abc"})
    )
    assert result.status == "completed", result.error
    assert result.changed_files[0].content == "c"


@pytest.mark.asyncio
@pytest.mark.parametrize("damage", ["duplicate_update", "alias_duplicate", "unowned", "out_of_scope", "malformed", "invalid_yaml", "missing_owner"])
async def test_anchored_output_keeps_scope_and_complete_file_validation(damage):
    path = "ui/pages/records.yaml"
    request = _request(files={path: "value: old\n"})
    wire = _anchored_response(path, [("old", "new")])
    item = wire["updated_files"][0]
    if damage == "duplicate_update":
        wire["updated_files"].append({"path": path, "content": "value: other\n"})
    elif damage == "alias_duplicate":
        wire["updated_files"].append({"path": "ui/./pages/records.yaml", "replacements": item["replacements"]})
    elif damage == "unowned":
        request.files["ui/pages/other.yaml"] = "value: old\n"
        item["path"] = "ui/pages/other.yaml"
    elif damage == "out_of_scope":
        item["path"] = "ui/pages/absent.yaml"
        wire["owned_paths"].append(item["path"])
    elif damage == "malformed":
        item["content"] = "value: fallback"
    elif damage == "invalid_yaml":
        item["replacements"][0]["new_text"] = "["
    else:
        wire["owned_paths"] = []
    result = await _provider(wire).execute(request)
    assert result.status == "failed" and result.changed_files == []


def test_structured_provider_wire_preserves_public_plan_fields_and_closed_file_options():
    from mozaiksai.control_plane.implementations.structured_coding_provider import (
        _ProviderCodingPlan,
    )

    public = CodingWorkerPlan.model_json_schema()
    wire = _ProviderCodingPlan.model_json_schema()
    assert wire["required"] == public["required"]
    assert wire["additionalProperties"] is False
    opts = wire["properties"]["updated_files"]["items"]["anyOf"]
    entries = [wire["$defs"][item["$ref"].rsplit("/", 1)[1]] for item in opts]
    assert {tuple(item["required"]) for item in entries} == {("path", "replacements"), ("path", "content")}
    assert all(item["additionalProperties"] is False for item in entries)
    anchor_schema = next(item for item in entries if "replacements" in item["properties"])
    edits = anchor_schema["properties"]["replacements"]["items"]["anyOf"]
    edit_options = [wire["$defs"][item["$ref"].rsplit("/", 1)[1]] for item in edits]
    assert {frozenset(item["required"]) for item in edit_options} == {
        frozenset({"line_number", "old_text", "new_text"}), frozenset({"old_text", "new_text"}),
    }
    assert all(item["additionalProperties"] is False for item in edit_options)
    assert set(public["$defs"]["FileUpdate"]["properties"]) == {"path", "content"}


@pytest.mark.asyncio
async def test_complete_file_output_adds_omitted_in_scope_ownership():
    plan = _GOOD_PLAN.model_copy(update={"owned_paths": ["shared.css"]})
    request = _request(files={_SCOPED_PATH: "previous", "shared.css": "retained"})
    proposal = await _provider(plan).execute(request)
    assert proposal.status == "completed", proposal.error
    assert proposal.owned_paths == ["shared.css", _SCOPED_PATH]
    assert [change.path for change in proposal.changed_files] == [_SCOPED_PATH]
