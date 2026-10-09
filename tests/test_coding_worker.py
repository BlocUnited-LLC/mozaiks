from __future__ import annotations

import hashlib
import io
import zipfile
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from factory_app.workflows._shared.artifact_bundle import read_artifact_bundle
from mozaiksai.control_plane import (
    CodingWorkerPlan,
    CodingWorkerRequest,
    ControlPlaneCheckpointManifest,
    ControlPlaneCodingCapabilityConfig,
    ControlPlaneConfig,
    ControlPlaneManifest,
    ControlPlanePromptDefinition,
    ControlPlanePromptsManifest,
    ControlPlaneToolDefinition,
    ControlPlaneToolResult,
    ControlPlaneToolsManifest,
    FileUpdate,
    LoadedControlPlanePack,
    ProposedFileChange,
    ScopedRefinementCodingWorker,
    StagedPatchProposal,
)
from mozaiksai.core.artifacts.content_store import read_verified_artifact_bundle
from mozaiksai.core.artifacts.models import (
    BuildRecord,
    canonical_bundle_archive_path,
    resolve_canonical_bundle_entry,
)
from mozaiksai.core.session.build_binding import RunBuildBinding


@pytest.mark.asyncio
async def test_refinement_preserves_unselected_files_and_bound_target(tmp_path):
    binding = RunBuildBinding(
        target_app_id="app_1", build_registry_id="registry_1", build_id="revision_1", phase="refinement",
    )
    store = _FakeArtifactStore()
    worker = ScopedRefinementCodingWorker(
        agent_factory=lambda sp, lc, *, middleware: _FakeAgent(sp, lc), config_loader=_enabled_control_plane,
        pack_loader=_pack, tool_executor=_FakeToolExecutor(),
        candidate_validation_runner=_fake_candidate_validation_runner,
        artifact_store=store, output_root=tmp_path,
    )
    original = "export default function Dashboard() {}"
    unchanged = '{"appId":"app_1"}'
    result = await worker.execute(CodingWorkerRequest(
        app_id="factory", target_app_id="app_1", user_id="user_1", run_build_binding=binding,
        build_family="app_bundle", build_record_id="av_parent", change_class="patch",
        requested_workflow_id="AppGenerator", raw_user_request="Change the dashboard",
        files={"app/ui/pages/Dashboard.jsx": original}, validation_strategy="local",
        baseline_files={"app/ui/pages/Dashboard.jsx": original, "app/app.json": unchanged},
    ))
    assert result.status == "validated", result.error
    assert store.calls[0]["app_id"] == "app_1"
    metadata = store.calls[0]["commit_metadata"]["metadata"]
    assert all(metadata[key] == value for key, value in binding.model_dump().items())
    with zipfile.ZipFile(metadata["artifact_path"]) as archive:
        prefix = f"{metadata['bundle_name']}/"
        assert archive.read(f"{prefix}app/app.json").decode() == unchanged
        assert b"patched" in archive.read(f"{prefix}app/ui/pages/Dashboard.jsx")

# ---------------------------------------------------------------------------
# Fake AG2 agent infrastructure
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["skipped", "warning", "pending"])
async def test_unvalidated_output_is_never_reported_validated(tmp_path, status):
    async def validate(**kwargs):
        return {
            "validation_status": status,
            "app_bundle_acceptance_result": {"passed": True},
            "app_validation_result": {"validation_status": status, "validation_strategy": kwargs["validation_strategy"]},
        }

    store = _FakeArtifactStore()
    worker = ScopedRefinementCodingWorker(
        agent_factory=lambda sp, lc, *, middleware: _FakeAgent(sp, lc), config_loader=_enabled_control_plane,
        pack_loader=_pack, tool_executor=_FakeToolExecutor(), candidate_validation_runner=validate,
        artifact_store=store, output_root=tmp_path,
    )
    result = await worker.execute(CodingWorkerRequest(
        app_id="app_1", user_id="user_1", build_family="app_bundle", build_record_id="parent", change_class="patch",
        files={"app/ui/pages/Dashboard.jsx": "export default function Dashboard() {}"},
    ))
    assert result.status == "planned"
    assert result.metadata["build_record_id"] == "av_child_1"
    assert store.calls[0]["validation_status"].value != "passed"

_GOOD_PLAN = CodingWorkerPlan(
    summary="Patch the dashboard file.",
    owned_paths=["app/ui/pages/Dashboard.jsx"],
    updated_files=[
        FileUpdate(
            path="app/ui/pages/Dashboard.jsx",
            content='export default function Dashboard() { return "patched"; }',
        )
    ],
    validation_strategy="local",
    validation_commands=["npm run build"],
    start_preview=False,
    needs_human_review=False,
    rationale="Single-file UI patch.",
)

_BAD_PLAN = CodingWorkerPlan(
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
        self.body = result.model_dump_json() if hasattr(result, "model_dump_json") else str(result)

    async def content(self, *, retries: int = 0) -> Any:
        return self._result


class _FakeAgent:
    """Records ask() calls and returns a preset CodingWorkerPlan."""

    def __init__(self, system_prompt: str, llm_config: dict[str, Any], plan: CodingWorkerPlan = _GOOD_PLAN) -> None:
        self.system_prompt = system_prompt
        self.llm_config = llm_config
        self.plan = plan
        self.calls: list[dict] = []

    async def ask(self, user_prompt: str, **kwargs: Any) -> _FakeReply:
        self.calls.append({"user_prompt": user_prompt, **kwargs})
        return _FakeReply(self.plan)


class _FakeToolExecutor:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def execute_tool(self, call, *, context=None):  # noqa: ANN001, ANN003
        self.calls.append({"call": call, "context": context})
        return ControlPlaneToolResult(success=True, output={"tool_id": call.tool_id, "artifact_version_id": context.artifact_version_id})


async def _fake_candidate_validation_runner(**kwargs):  # noqa: ANN003
    assert kwargs["app_id"] == "app_1"
    assert "app/ui/pages/Dashboard.jsx" in kwargs["files"]
    return {
        "success": True,
        "validation_status": "passed",
        "app_bundle_acceptance_result": {"passed": True},
        "app_validation_result": {"validation_status": "passed", "validation_strategy": kwargs["validation_strategy"]},
        "validation_strategy": kwargs["validation_strategy"],
    }


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


def _enabled_control_plane() -> ControlPlaneConfig:
    return ControlPlaneConfig(
        enabled=True,
        llm_profiles={"codegen": {"llm_config": {"model": "gpt-5.2-codex", "temperature": 0.1}}},
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
                    tool_ids=["get_revision_context", "get_artifact_summary"],
                )
            ],
        ),
        prompts=ControlPlanePromptsManifest(
            schema_version="mozaiks.refinement_harness.v1.prompts",
            prompts=[
                ControlPlanePromptDefinition(
                    id="coding_refinement_system",
                    content="coding system prompt from pack",
                )
            ],
        ),
        tools=ControlPlaneToolsManifest(
            schema_version="mozaiks.refinement_harness.tools.v1",
            tools=[
                ControlPlaneToolDefinition(
                    id="get_artifact_summary",
                    kind="context_tool",
                    description="artifact summary",
                    entrypoint="example.tools:get_artifact_summary",
                    available_to=["coding_requested"],
                ),
                ControlPlaneToolDefinition(
                    id="get_revision_context",
                    kind="context_tool",
                    description="revision context",
                    entrypoint="example.tools:get_revision_context",
                    available_to=["coding_requested"],
                ),
            ],
        ),
    )


@pytest.mark.asyncio
async def test_coding_worker_executes_for_scoped_patch_request(tmp_path: Path) -> None:
    created: list[_FakeAgent] = []
    tool_executor = _FakeToolExecutor()
    artifact_store = _FakeArtifactStore()

    def capturing_factory(system_prompt: str, llm_config: dict, *, middleware: list) -> _FakeAgent:
        a = _FakeAgent(system_prompt, llm_config)
        created.append(a)
        return a

    worker = ScopedRefinementCodingWorker(
        agent_factory=capturing_factory,
        config_loader=_enabled_control_plane,
        pack_loader=_pack,
        tool_executor=tool_executor,
        candidate_validation_runner=_fake_candidate_validation_runner,
        artifact_store=artifact_store,
        output_root=tmp_path,
    )

    result = await worker.execute(
        CodingWorkerRequest(
            app_id="app_1", user_id="user_1",
            artifact_kind="app_bundle",
            artifact_key="app_bundle",
            artifact_version_id="av_123",
            requested_workflow_id="AppGenerator",
            raw_user_request="Fix the dashboard spacing",
            source_surface="app_build",
            change_class="patch",
            files={"app/ui/pages/Dashboard.jsx": "export default function Dashboard() {}"},
            validation_strategy="local",
            context_seed={"change_class": "patch"},
        )
    )

    assert result.eligible is True
    assert result.status == "validated"
    assert result.plan is not None
    assert result.plan.validation_strategy == "local"
    updated_dict = {fu.path: fu.content for fu in result.plan.updated_files}
    assert updated_dict["app/ui/pages/Dashboard.jsx"].endswith('"patched"; }')
    assert result.applied_files["app/ui/pages/Dashboard.jsx"].endswith('"patched"; }')
    assert result.validation_result["validation_status"] == "passed"
    assert result.validation_result["validation_strategy"] == "local"
    assert result.validation_result["app_bundle_acceptance_result"]["passed"] is True
    assert result.metadata["build_record_id"] == "av_child_1"
    assert result.metadata["bundle_mode"] == "staged_refinement_bundle"
    assert result.metadata["app_validation_status"] == "passed"

    assert len(created) == 1
    assert created[0].system_prompt == "coding system prompt from pack"
    assert created[0].llm_config == {"model": "gpt-5.2-codex", "temperature": 0.1}
    assert '"input_files"' in created[0].calls[0]["user_prompt"]
    assert '"refinement_context"' in created[0].calls[0]["user_prompt"]

    assert len(tool_executor.calls) == 2
    assert artifact_store.calls[0]["parent_build_record_id"] == "av_123"
    assert artifact_store.calls[0]["build_family"] == "app_bundle"
    assert artifact_store.calls[0]["lifecycle_status"].value == "draft"
    assert artifact_store.calls[0]["validation_status"].value == "passed"
    assert artifact_store.calls[0]["commit_metadata"]["metadata"]["applied_paths"] == [
        "app/ui/pages/Dashboard.jsx"
    ]
    assert artifact_store.calls[0]["commit_metadata"]["metadata"]["app_validation_result"]["validation_status"] == "passed"


@pytest.mark.asyncio
async def test_coding_worker_rejects_non_patch_requests() -> None:
    worker = ScopedRefinementCodingWorker(
        agent_factory=lambda sp, lc, *, middleware: _FakeAgent(sp, lc),
        config_loader=_enabled_control_plane,
        pack_loader=_pack,
        tool_executor=_FakeToolExecutor(),
        candidate_validation_runner=_fake_candidate_validation_runner,
    )

    result = await worker.execute(
        CodingWorkerRequest(
            app_id="app_1", user_id="user_1",
            artifact_kind="app_bundle",
            artifact_key="app_bundle",
            artifact_version_id="av_123",
            requested_workflow_id="AppGenerator",
            raw_user_request="Add a brand new approvals capability",
            source_surface="app_build",
            change_class="feature",
            files={"app/ui/pages/Dashboard.jsx": "export default function Dashboard() {}"},
        )
    )

    assert result.eligible is False
    assert result.status == "ineligible"
    assert "patch refinements" in str(result.blocked_reason)


@pytest.mark.asyncio
async def test_coding_worker_fails_when_model_edits_outside_scoped_files(tmp_path: Path) -> None:
    worker = ScopedRefinementCodingWorker(
        agent_factory=lambda sp, lc, *, middleware: _FakeAgent(sp, lc, plan=_BAD_PLAN),
        config_loader=_enabled_control_plane,
        pack_loader=_pack,
        tool_executor=_FakeToolExecutor(),
        candidate_validation_runner=_fake_candidate_validation_runner,
        artifact_store=_FakeArtifactStore(),
        output_root=tmp_path,
    )

    result = await worker.execute(
        CodingWorkerRequest(
            app_id="app_1", user_id="user_1",
            artifact_kind="app_bundle",
            artifact_key="app_bundle",
            artifact_version_id="av_123",
            requested_workflow_id="AppGenerator",
            raw_user_request="Fix the dashboard spacing",
            source_surface="app_build",
            change_class="patch",
            files={"app/ui/pages/Dashboard.jsx": "export default function Dashboard() {}"},
            validation_strategy="skip",
        )
    )

    assert result.eligible is True
    assert result.status == "failed"
    assert "outside the explicit scoped files" in str(result.error)


@pytest.mark.asyncio
async def test_coding_worker_surfaces_artifact_persistence_errors(tmp_path: Path) -> None:
    class _BrokenArtifactStore(_FakeArtifactStore):
        async def create_build_record(self, **kwargs):  # noqa: ANN003
            raise RuntimeError('artifact store unavailable')

    worker = ScopedRefinementCodingWorker(
        agent_factory=lambda sp, lc, *, middleware: _FakeAgent(sp, lc),
        config_loader=_enabled_control_plane,
        pack_loader=_pack,
        tool_executor=_FakeToolExecutor(),
        candidate_validation_runner=_fake_candidate_validation_runner,
        artifact_store=_BrokenArtifactStore(),
        output_root=tmp_path,
    )

    result = await worker.execute(
        CodingWorkerRequest(
            app_id="app_1", user_id="user_1",
            artifact_kind="app_bundle",
            artifact_key="app_bundle",
            artifact_version_id="av_123",
            requested_workflow_id="AppGenerator",
            raw_user_request="Fix the dashboard spacing",
            source_surface="app_build",
            change_class="patch",
            files={"app/ui/pages/Dashboard.jsx": "export default function Dashboard() {}"},
            validation_strategy="local",
            context_seed={"change_class": "patch"},
        )
    )

    assert result.status == "failed"
    assert result.error is not None and "ARTIFACT_PERSISTENCE_FAILED" in result.error
    assert "ARTIFACT_PERSISTENCE_FAILED" in result.metadata["artifact_persistence_error"]


def _candidate_request(**updates):
    return CodingWorkerRequest(
        app_id="studio", target_app_id="app_1", user_id="alice",
        build_family="app_bundle", build_record_id="parent", change_class="patch",
        files={"brand/theme_config.json": '{"accent":"blue"}'},
        baseline_files={"app.json": '{"appId":"app_1"}', "brand/theme_config.json": '{"accent":"blue"}'},
        run_build_binding=RunBuildBinding(
            target_app_id="app_1", build_registry_id="registry", build_id="revision", phase="refinement",
        ),
        **updates,
    )


def _candidate_proposal(**updates):
    return StagedPatchProposal(
        proposal_id="proposal", provider_id="offline", status="completed",
        summary="Change accent", rationale="Requested accent", owned_paths=["brand/theme_config.json"],
        changed_files=[ProposedFileChange(path="brand/theme_config.json", content='{"accent":"coral"}')],
        **updates,
    )


@pytest.mark.parametrize("op,content", [("create", "new file"), ("delete", None)])
def test_generated_app_worker_rejects_repository_operations(op, content):
    proposal = StagedPatchProposal(
        proposal_id="proposal", provider_id="offline", status="completed",
        summary="Change accent", rationale="Requested accent", owned_paths=["brand/theme_config.json"],
        changed_files=[ProposedFileChange(path="brand/theme_config.json", op=op, content=content)],
    )
    with pytest.raises(ValueError, match="only supports updates"):
        ScopedRefinementCodingWorker._plan_from_proposal(
            request=_candidate_request(), proposal=proposal, resolved_strategy="local"
        )


def _candidate_evidence(strategy="docker"):
    return {
        "validation_status": "passed", "validation_strategy": strategy,
        "app_bundle_acceptance_result": {
            "passed": True, "status": "passed",
            "validation_evidence": {"completed": ["app_runtime_smoke"], "failed": [], "skipped": []},
        },
        "app_validation_result": {
            "validation_status": "passed", "validation_strategy": strategy,
            "sandbox_session_id": "owned-validation", "sandbox_provider": strategy,
            "sandbox_terminated": True,
        },
        "errors": [],
    }


def _selected_pack_contracts():
    return [{
        "id": "operator_readiness", "capability_source": "config_file",
        "pack_source_path": str(Path(__file__).resolve().parents[1] / "factory_app/build_context/operator_readiness"),
    }]


class _LineageArtifactStore(_FakeArtifactStore):
    def __init__(self, metadata):
        super().__init__()
        self.reads = []
        self.records = {"parent": BuildRecord(
            id="parent", app_id="app_1", build_family="app_bundle", build_key="app_bundle",
            version_number=1, lineage_root_id="parent", commit_metadata={"metadata": deepcopy(metadata)},
        )}

    async def get_build_record(self, *, app_id, build_record_id):
        self.reads.append((app_id, build_record_id))
        record = self.records.get(build_record_id)
        return record if record and record.app_id == app_id else None

    async def create_build_record(self, **kwargs):
        self.calls.append(kwargs)
        record = BuildRecord(
            id=f"child_{len(self.calls)}", version_number=len(self.calls) + 1, lineage_root_id="parent", **kwargs,
        )
        self.records[record.id] = record
        return record


async def _validate_pack_support_outputs(**kwargs):
    # Exercise the existing declaration gate; no build, provider or Mongo access.
    from factory_app.workflows.AppGenerator.tools.generated_bundle_scanner import (
        _scan_declared_pack_repo_support_outputs,
    )

    errors = _scan_declared_pack_repo_support_outputs(
        kwargs["files"], capability_packs=kwargs.get("capability_packs"),
    )
    result = _candidate_evidence()
    if errors:
        result.update(validation_status="failed", errors=errors)
        result["app_bundle_acceptance_result"] = {"passed": False, "status": "failed"}
        result["app_validation_result"]["validation_status"] = "pending"
    return result


def _pack_candidate_request():
    request = _candidate_request()
    request.baseline_files["docs/operations/operator-readiness.md"] = "Original declared pack output"
    return request


@pytest.mark.asyncio
async def test_selected_pack_contracts_survive_two_refinements_without_request_authority(tmp_path):
    selected = _selected_pack_contracts()
    store = _LineageArtifactStore({"capability_packs": selected})
    seen = []

    async def validate(**kwargs):
        seen.append(deepcopy(kwargs.get("capability_packs")))
        result = await _validate_pack_support_outputs(**kwargs)
        # Validation helper writes cannot become authority on the persisted child.
        if kwargs.get("capability_packs"):
            kwargs["capability_packs"][0]["id"] = "untrusted_helper_write"
        return result

    worker = ScopedRefinementCodingWorker(
        candidate_validation_runner=validate, artifact_store=store, output_root=tmp_path,
    )
    request = _pack_candidate_request()
    first = await worker.finalize_proposal(request, _candidate_proposal())
    assert first.status == "validated", first.error
    first_id = first.metadata["build_record_id"]
    assert store.records[first_id].commit_metadata.metadata["capability_packs"] == selected
    request = request.model_copy(update={
        "build_record_id": first_id,
        "files": dict(first.applied_files),
        "baseline_files": {**request.baseline_files, **first.applied_files},
    })
    second = await worker.finalize_proposal(request, _candidate_proposal().model_copy(update={
        "changed_files": [ProposedFileChange(path="brand/theme_config.json", content='{"accent":"teal"}')],
    }))
    assert second.status == "validated", second.error
    assert store.records[second.metadata["build_record_id"]].commit_metadata.metadata["capability_packs"] == selected
    assert store.records["parent"].commit_metadata.metadata["capability_packs"] == selected
    assert seen == [selected, selected]
    assert store.reads == [("app_1", "parent"), ("app_1", first_id)]
    assert store.calls[1]["parent_build_record_id"] == first_id


@pytest.mark.asyncio
@pytest.mark.parametrize("parent_metadata", [{}, {"capability_packs": []}])
async def test_request_and_generated_provenance_cannot_grant_missing_pack_contracts(tmp_path, parent_metadata):
    store = _LineageArtifactStore(parent_metadata)
    request = _pack_candidate_request()
    request.metadata["capability_packs"] = _selected_pack_contracts()
    request.context_seed["app_build_plan"] = {"capability_packs": _selected_pack_contracts()}
    request.baseline_files[".mozaiks/pack_provenance.json"] = '{"packs":[{"id":"operator_readiness"}]}'
    worker = ScopedRefinementCodingWorker(
        candidate_validation_runner=_validate_pack_support_outputs, artifact_store=store, output_root=tmp_path,
    )
    result = await worker.finalize_proposal(request, _candidate_proposal())
    assert result.status == "failed"
    assert "undeclared repository-support pack outputs" in result.error
    assert store.calls[0]["validation_status"].value == "failed"
    assert store.calls[0]["commit_metadata"]["metadata"]["capability_packs"] == []


@pytest.mark.asyncio
async def test_saved_pack_still_requires_its_resolvable_output_contract(tmp_path):
    selected = _selected_pack_contracts()
    selected[0]["pack_source_path"] = str(tmp_path / "missing_installed_pack")
    store = _LineageArtifactStore({"capability_packs": selected})
    worker = ScopedRefinementCodingWorker(
        candidate_validation_runner=_validate_pack_support_outputs, artifact_store=store, output_root=tmp_path,
    )
    result = await worker.finalize_proposal(_pack_candidate_request(), _candidate_proposal())
    assert result.status == "failed"
    assert "Selected CapabilityPack output contract is invalid" in result.error
    assert store.calls[0]["validation_status"].value == "failed"


@pytest.mark.asyncio
@pytest.mark.parametrize("packs", [{"id": "operator_readiness"}, ["operator_readiness"], None])
async def test_malformed_saved_pack_contracts_fail_before_validation_or_persistence(tmp_path, packs):
    store = _LineageArtifactStore({"capability_packs": packs})
    validate = AsyncMock(return_value=_candidate_evidence())
    worker = ScopedRefinementCodingWorker(
        candidate_validation_runner=validate, artifact_store=store, output_root=tmp_path,
    )
    result = await worker.finalize_proposal(_candidate_request(), _candidate_proposal())
    assert result.status == "failed"
    assert "capability_packs" in result.error
    validate.assert_not_awaited()
    assert store.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("parent_id", [None, "", "   "])
async def test_finalization_requires_parent_id_before_store_read(tmp_path, parent_id):
    store = _LineageArtifactStore({"capability_packs": _selected_pack_contracts()})
    validate = AsyncMock(return_value=_candidate_evidence())
    worker = ScopedRefinementCodingWorker(
        candidate_validation_runner=validate, artifact_store=store, output_root=tmp_path,
    )
    request = _candidate_request().model_copy(update={"build_record_id": parent_id})
    result = await worker.finalize_proposal(request, _candidate_proposal())
    assert result.status == "failed"
    assert "parent build record ID is required" in result.error
    assert store.reads == store.calls == []
    validate.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("parent_app", [None, "foreign_app"])
async def test_refinement_cannot_resolve_parent_outside_target_app(tmp_path, parent_app):
    store = _LineageArtifactStore({"capability_packs": _selected_pack_contracts()})
    if parent_app is None:
        store.records.clear()
    else:
        store.records["parent"].app_id = parent_app
    validate = AsyncMock(return_value=_candidate_evidence())
    worker = ScopedRefinementCodingWorker(
        candidate_validation_runner=validate, artifact_store=store, output_root=tmp_path,
    )
    result = await worker.finalize_proposal(_candidate_request(), _candidate_proposal())
    assert result.status == "failed"
    assert "parent build record" in result.error
    validate.assert_not_awaited()
    assert store.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("requested,operator,hint,expected", [
    ("docker", None, "skip", "docker"),
    ("e2b", None, "local", "e2b"),
    ("skip", None, "local", "skip"),
    ("local", "docker", "skip", "docker"),
    (None, "e2b", "skip", "e2b"),
])
async def test_candidate_execution_uses_operator_policy_not_provider_hint(
    monkeypatch, tmp_path, requested, operator, hint, expected,
):
    if operator is None:
        monkeypatch.delenv("MOZAIKS_APP_VALIDATION_STRATEGY", raising=False)
    else:
        monkeypatch.setenv("MOZAIKS_APP_VALIDATION_STRATEGY", operator)
    evidence = _candidate_evidence(expected)
    if expected == "skip":
        evidence["validation_status"] = "skipped"
        evidence["app_validation_result"]["validation_status"] = "skipped"
    validate = AsyncMock(return_value=evidence)
    provider = SimpleNamespace(execute=AsyncMock(return_value=_candidate_proposal(
        validation_strategy_hint=hint, validation_commands=["echo must-not-execute"], start_preview=True,
    )))
    store = _FakeArtifactStore()
    worker = ScopedRefinementCodingWorker(
        provider=provider, candidate_validation_runner=validate, config_loader=_enabled_control_plane,
        artifact_store=store, output_root=tmp_path,
    )
    result = await worker.execute(_candidate_request(validation_strategy=requested))

    assert result.status == ("planned" if expected == "skip" else "validated"), result.error
    assert result.plan.validation_strategy == expected
    validate.assert_awaited_once_with(
        files={"app.json": '{"appId":"app_1"}', "brand/theme_config.json": '{"accent":"coral"}'},
        app_id="app_1", validation_strategy=expected, timeout_seconds=120,
        capability_packs=[],
    )
    assert store.calls[0]["app_validation_strategy"] == expected
    assert store.calls[0]["app_validation_status"] == ("skipped" if expected == "skip" else "passed")


@pytest.mark.asyncio
@pytest.mark.parametrize("defect", ["source_only", "acceptance_failed", "build_skipped"])
async def test_candidate_cannot_be_saved_as_validated_without_both_required_results(tmp_path, defect):
    evidence = _candidate_evidence()
    if defect == "source_only":
        evidence = {"validation_status": "passed"}
    elif defect == "acceptance_failed":
        evidence["app_bundle_acceptance_result"]["passed"] = False
    else:
        evidence["app_validation_result"]["validation_status"] = "skipped"
    store = _FakeArtifactStore()
    worker = ScopedRefinementCodingWorker(
        candidate_validation_runner=AsyncMock(return_value=evidence), artifact_store=store, output_root=tmp_path,
    )
    result = await worker.finalize_proposal(_candidate_request(validation_strategy="docker"), _candidate_proposal())

    assert result.status == "failed"
    assert "CANDIDATE_VALIDATION_FAILED" in result.error
    assert store.calls == []


@pytest.mark.asyncio
async def test_candidate_archive_binds_validated_contents_lineage_and_both_gate_results(monkeypatch, tmp_path):
    monkeypatch.delenv("MOZAIKS_APP_VALIDATION_STRATEGY", raising=False)
    observed = {}

    async def validate(**kwargs):
        observed.update(kwargs["files"])
        # A validator's local context write-back cannot alter the saved candidate.
        kwargs["files"]["brand/theme_config.json"] = "unvalidated mutation"
        return _candidate_evidence()

    store = _FakeArtifactStore()
    worker = ScopedRefinementCodingWorker(candidate_validation_runner=validate, artifact_store=store, output_root=tmp_path)
    result = await worker.finalize_proposal(_candidate_request(validation_strategy="docker"), _candidate_proposal())
    assert result.status == "validated", result.error
    saved = store.calls[0]
    record = BuildRecord(id="av_child_1", version_number=1, lineage_root_id="av_parent", **saved)
    entry = resolve_canonical_bundle_entry(record)
    metadata = record.commit_metadata.metadata
    archive_path = Path(metadata["artifact_path"])
    assert entry.path == canonical_bundle_archive_path(metadata["bundle_name"])
    assert archive_path.name == f'{metadata["bundle_name"]}.zip'
    assert record.parent_build_record_id == "parent"
    assert record.app_id == "app_1"
    assert record.commit_metadata.author_user_id == "alice"
    assert metadata["build_registry_id"] == "registry"
    assert metadata["build_id"] == "revision"
    assert record.validation_status.value == record.app_validation_status == "passed"
    assert record.app_validation_strategy == record.sandbox_provider == "docker"
    assert record.sandbox_session_id == "owned-validation"
    assert metadata["app_bundle_acceptance"] == _candidate_evidence()["app_bundle_acceptance_result"]
    assert metadata["app_validation_result"] == _candidate_evidence()["app_validation_result"]
    raw = await read_verified_artifact_bundle(record)
    assert hashlib.sha256(raw).hexdigest() == entry.sha256
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        prefix = f"{metadata['bundle_name']}/"
        assert {name.removeprefix(prefix): archive.read(name).decode() for name in archive.namelist()} == observed
        assert all(name.startswith(prefix) for name in archive.namelist())
    files, diagnostics = await read_artifact_bundle(record, include_binary=True)
    assert files == observed and diagnostics == []
    assert metadata["staged_file_sha256"] == {
        name: hashlib.sha256(content.encode()).hexdigest() for name, content in observed.items()
    }
    archive_path.write_bytes(b"changed after validation")
    with pytest.raises(ValueError):
        await read_verified_artifact_bundle(record)
