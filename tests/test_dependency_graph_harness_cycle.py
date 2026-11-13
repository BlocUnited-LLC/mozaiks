"""Exact-build graph evidence through Factory registration and AG2 refinement.

Only model replies and persistence are in memory. Archive registration, parsing,
tool execution, routing, scope selection, model prompt construction, candidate
staging and digest verification use their production owners. Runtime validation
is explicitly skipped: these are unaccepted drafts, never deployment evidence.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import zipfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from factory_app.refinement_harness.tools._context_graph import load_context_graph_for_tool
from factory_app.workflows.AppGenerator.tools.generate_and_download import (
    _register_app_bundle_artifact_version,
)
from mozaiksai.control_plane.config import ControlPlaneConfig
from mozaiksai.control_plane.contracts import ControlPlaneToolContext
from mozaiksai.control_plane.implementations.orchestration_control import (
    OrchestrationControlHarness,
)
from mozaiksai.control_plane.implementations.refinement_router import RefinementRequest
from mozaiksai.control_plane.loader import load_refinement_harness
from mozaiksai.control_plane.runtime import ControlPlaneCheckpointRuntime
from mozaiksai.core.adapters.ag2_agent_runner import AG2StructuredAgentRunner
from mozaiksai.core.app_context.store import get_current_app_context_version
from mozaiksai.core.artifacts.content_store import (
    LocalArtifactContentStore,
    read_verified_artifact_bundle,
)
from mozaiksai.core.artifacts.models import (
    BuildRecord,
    BuildRecordStatus,
    resolve_canonical_bundle_entry,
)
from mozaiksai.core.session.build_binding import RunBuildBinding
from mozaiksai.core.usage.context import AuxiliaryUsageContext

ROOT = Path(__file__).resolve().parents[1]
TARGET = "ui/pages/Home.tsx"
OLD = "ui/components/old_labels.ts"
NEW = "ui/components/new_labels.ts"
TEST = "ui/pages/Home.test.tsx"
SECRET = "graph-fixture-secret-DO-NOT-PROMPT"
SOURCE_A = (
    'import { oldLabel } from "../components/old_labels";\n'
    "export function removedHelper(): string { return oldLabel(); }\n"
    "export default function Home() { return <h1>{removedHelper()}</h1>; }\n"
)
SOURCE_B = (
    'import { newLabel } from "../components/new_labels";\n'
    "export default function Home() { return <h1>{newLabel()}</h1>; }\n"
)
SOURCE_C = SOURCE_B.replace("<h1>", '<h1 aria-label="Final">')


class MemoryBuildRecords:
    """The database boundary; tenant filtering and real record models are retained."""

    def __init__(self) -> None:
        self.records: dict[str, BuildRecord] = {}
        self.reads: list[tuple[str, str]] = []
        self.writes: list[BuildRecord] = []

    async def get_build_record(self, *, app_id: str, build_record_id: str) -> BuildRecord | None:
        self.reads.append((app_id, build_record_id))
        record = self.records.get(build_record_id)
        return record if record is not None and record.app_id == app_id else None

    async def create_build_record(self, **kwargs: Any) -> BuildRecord:
        number = len(self.records) + 1
        parent = self.records.get(kwargs.get("parent_build_record_id"))
        record = BuildRecord(
            id=f"record-{number}", version_number=number,
            lineage_root_id=parent.lineage_root_id if parent else f"record-{number}", **kwargs,
        )
        self.records[record.id] = record
        self.writes.append(record)
        return record

    async def list_build_records(self, *, app_id: str, limit: int = 50, **filters: Any) -> list[BuildRecord]:
        rows = [record for record in self.records.values() if record.app_id == app_id]
        for field in ("build_family", "build_key", "lifecycle_status"):
            if filters.get(field) is not None:
                rows = [record for record in rows if getattr(record, field) == filters[field]]
        return sorted(rows, key=lambda record: record.version_number, reverse=True)[:limit]

    async def accept_build_record(self, *, app_id: str, build_record_id: str, **kwargs: Any) -> BuildRecord:
        record = await self.get_build_record(app_id=app_id, build_record_id=build_record_id)
        assert record is not None
        for sibling in self.records.values():
            if (sibling.app_id, sibling.build_family, sibling.build_key) == (
                record.app_id, record.build_family, record.build_key,
            ) and sibling.lifecycle_status == BuildRecordStatus.CURRENT:
                sibling.lifecycle_status = BuildRecordStatus.SUPERSEDED
        record.lifecycle_status = BuildRecordStatus.CURRENT
        return record

    async def list_change_requests(self, **kwargs: Any) -> list[Any]:
        return []

    async def get_stale_artifact_families(self, **kwargs: Any) -> list[str]:
        return []


class Context(dict):
    def set(self, key: str, value: Any) -> None:
        self[key] = value


class ScriptedBackend:
    """Receives the exact prompts at Agent.ask, below the real AG2 runner."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.replacement = SOURCE_B
        self.target_path = TARGET
        self.test_path = TEST
        self.update_path = TARGET

    def factory(self, system_prompt: str, llm_config: dict[str, Any], *, middleware: list[Any]) -> Any:
        owner = self

        class Agent:
            async def ask(self, user_prompt: str, **kwargs: Any) -> Any:
                if user_prompt.startswith("Classify"):
                    kind, payload = "classifier", None
                    response = {"change_class": "patch", "rationale": "Local copy refinement.",
                                "confidence": 0.99, "signals": ["local_patch"]}
                elif user_prompt.startswith("Propose"):
                    kind = "scope"
                    payload = json.JSONDecoder().raw_decode(user_prompt.split("payload_json:\n", 1)[1])[0]
                    response = {"resolution": "scoped_files", "selected_paths": [owner.target_path],
                                "rationale": "The requested page owns this copy.", "confidence": 0.99,
                                "clarification_question": None, "signals": ["exact_path"]}
                elif user_prompt.startswith("Plan"):
                    kind = "coding"
                    payload = json.JSONDecoder().raw_decode(user_prompt.split("payload_json:\n", 1)[1])[0]
                    response = {"summary": "Update the page dependency.", "owned_paths": [owner.update_path],
                                "updated_files": [{"path": owner.update_path, "content": owner.replacement}],
                                "validation_strategy": "skip", "validation_commands": [], "start_preview": False,
                                "needs_human_review": False, "rationale": "Apply the requested scoped change."}
                else:
                    kind, payload = "graph", json.loads(user_prompt)
                    assert owner.target_path in payload["evidence"]["files"]
                    # Model suggestions cannot create scope or invent graph facts.
                    response = {"recommended_paths": [owner.target_path, "../escape.py", "ui/foreign-only.ts"],
                                "test_paths": [owner.test_path] if owner.test_path in payload["evidence"]["files"] else []}
                owner.calls.append({"kind": kind, "system": system_prompt, "user": user_prompt,
                                    "payload": payload, "response": response})

                class Reply:
                    async def content(self, *, retries: int = 0) -> dict[str, Any]:
                        return response

                return Reply()

        return Agent()


async def register_build(
    root: Path, store: MemoryBuildRecords, *, app_id: str = "target", marker: str = "initial",
    source: str = SOURCE_A, files: dict[str, str] | None = None, validation_result: dict[str, Any] | None = None,
) -> tuple[BuildRecord, dict[str, str]]:
    workspace = root / marker / "app"
    files = files if files is not None else {
        "app.json": json.dumps({"appId": app_id, "name": "Graph fixture", "authRequired": False}),
        TARGET: source,
        OLD: 'export function oldLabel(): string { return "Before"; }\n',
        NEW: 'export function newLabel(): string { return "After"; }\n',
        TEST: 'import Home from "./Home";\nexport function testHome() { return Home(); }\n',
        "ui/unrelated.ts": 'export const untouched = "KEEP-UNCHANGED";\n',
        ".env": f"API_KEY={SECRET}\n",
    }
    for path, source_text in files.items():
        destination = workspace / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(source_text, encoding="utf-8")
    bundle_name = f"fixture_{marker}"
    archive = workspace.parent / f"{bundle_name}.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
        for path in sorted(files):
            bundle.write(workspace / path, arcname=f"{bundle_name}/{path}")
    context = Context(run_build_binding=RunBuildBinding(
        target_app_id=app_id, build_registry_id=f"registry-{app_id}", build_id=marker, phase="genesis",
    ).model_dump())
    if validation_result is not None:
        assert validation_result["validation_status"] == "passed", validation_result
        context.update(
            app_bundle_acceptance_status="passed",
            app_bundle_acceptance_result=validation_result["app_bundle_acceptance_result"],
            app_validation_status="passed", app_validation_strategy_used="docker",
            app_validation_result=validation_result["app_validation_result"],
        )
    record = await _register_app_bundle_artifact_version(
        app_id=app_id, user_id="owner", workflow_name="AppGenerator", chat_id=f"chat-{marker}",
        bundle_name=bundle_name, zip_path=archive, app_dir=workspace, written_paths=list(files),
        context_variables=context, artifact_store=store,
    )
    assert context.get("greenfield_app_context_registered") is True, context
    assert record.lifecycle_status == BuildRecordStatus.DRAFT
    assert hashlib.sha256(await read_verified_artifact_bundle(record)).hexdigest() == resolve_canonical_bundle_entry(record).sha256
    return record, {path: text for path, text in files.items() if path != ".env"}


@pytest.fixture
def cycle(monkeypatch, tmp_path):
    from mozaiksai.control_plane import refinement_tracking
    from mozaiksai.core.artifacts import content_store
    from mozaiksai.core.artifacts import store as records_module
    from mozaiksai.core.session.persistence import SessionStateStore

    store = MemoryBuildRecords()
    monkeypatch.setattr(records_module, "_build_record_store", store)
    monkeypatch.setattr(records_module, "ArtifactStore", lambda: store)
    monkeypatch.setattr(content_store, "get_artifact_content_store", lambda: LocalArtifactContentStore())
    monkeypatch.setattr(SessionStateStore, "load", AsyncMock(return_value=None))
    monkeypatch.setattr(refinement_tracking, "_get_collection", lambda: None)
    monkeypatch.setenv("MOZAIKS_WORKFLOWS_PATH", str(ROOT / "factory_app/workflows"))
    monkeypatch.setenv("MOZAIKS_APP_PATH", str(ROOT / "factory_app/app"))
    config = ControlPlaneConfig(
        enabled=True, llm_profiles={"classifier": {"llm_config": {"model": "offline-graph-fixture"}}},
        **{name: {"enabled": True, "llm_profile": "classifier"}
           for name in ("classifier", "scope", "coding", "dependency_graph")},
    )
    pack = load_refinement_harness(app_root=ROOT / "factory_app/app")
    backend = ScriptedBackend()
    runner = AG2StructuredAgentRunner(agent_factory=backend.factory)
    runtime = ControlPlaneCheckpointRuntime(pack=pack, pack_loader=lambda: pack)
    for event in ("request_submitted", "scope_requested", "dependency_graph_requested", "coding_requested"):
        runtime.bind_checkpoint(
            event, agent_runner=runner, config_loader=lambda: config, artifact_store=store,
            output_root=tmp_path / "staged",
        )
    harness = OrchestrationControlHarness(checkpoint_runtime=runtime, config_loader=lambda: config)
    return store, backend, harness, runtime


def tool_context(record: BuildRecord) -> ControlPlaneToolContext:
    return ControlPlaneToolContext(
        checkpoint="dependency_graph_requested", app_id="factory", target_app_id=record.app_id,
        user_id="owner", build_family="app_bundle", build_key="app_bundle", build_record_id=record.id,
        raw_user_request=f"Update the copy in {TARGET}.",
    )


async def refine(harness, record: BuildRecord, baseline: dict[str, str], *, target_path=TARGET, validation_strategy="skip"):
    request = RefinementRequest(
        app_id="factory", target_app_id=record.app_id, user_id="owner", build_family="app_bundle",
        build_key="app_bundle", build_record_id=record.id, raw_user_request=f"Update the copy in {target_path}.",
        usage_context=AuxiliaryUsageContext(app_id="factory", user_id="owner"),
    )
    route = await harness.route_refinement_request(request)
    assert route.change_class == "patch"
    coding = harness.build_coding_request(refinement_request=request, routing_decision=route,
                                          payload={"validation_strategy": validation_strategy})
    assert coding is not None
    coding, decision = await harness.prepare_coding_request(coding, confirmation_required_paths=())
    assert coding is not None, decision
    assert set(coding.files) == {target_path}
    assert coding.files[target_path] == baseline[target_path]
    coding = coding.model_copy(update={"baseline_files": baseline, "run_build_binding": RunBuildBinding(
        target_app_id=record.app_id, build_registry_id=f"registry-{record.app_id}",
        build_id=f"refine-{record.id}", phase="refinement",
    )})
    return await harness.execute_coding_request(coding)


@pytest.mark.asyncio
async def test_factory_build_and_repeated_refinements_use_exact_graph_prompts(cycle, tmp_path):
    store, backend, harness, _runtime = cycle
    original, baseline = await register_build(tmp_path, store)
    original_bytes = await read_verified_artifact_bundle(original)
    decoy, _ = await register_build(tmp_path, store, marker="current-decoy", source="export const CURRENT_ONLY = 1;\n")
    foreign, _ = await register_build(tmp_path, store, app_id="foreign", marker="foreign", source="export const FOREIGN_ONLY = 1;\n")
    current_context = await get_current_app_context_version(app_id="target", artifact_store=store)
    assert current_context is not None
    assert any(ref.artifact_version_id == decoy.id for ref in current_context.artifact_refs)
    # The mutable export directory is not the saved build being refined.
    Path(original.commit_metadata.metadata["workspace_dir"], TARGET).write_text("export const MUTABLE_ONLY = 1;\n")
    writes_before = len(store.writes)
    reads_before = len(store.reads)

    result_b = await refine(harness, original, baseline)
    assert result_b.status == "planned", result_b.error
    child_b = store.records[result_b.metadata["build_record_id"]]
    assert child_b.parent_build_record_id == original.id
    assert child_b.lifecycle_status == BuildRecordStatus.DRAFT
    assert child_b.validation_status.value != "passed"
    backend.replacement = SOURCE_C
    result_c = await refine(harness, child_b, {**baseline, TARGET: SOURCE_B})
    assert result_c.status == "planned", result_c.error
    child_c = store.records[result_c.metadata["build_record_id"]]
    assert child_c.parent_build_record_id == child_b.id
    assert await read_verified_artifact_bundle(original) == original_bytes
    assert {record.app_id for record in store.writes[writes_before:]} == {"target"}
    assert decoy.lifecycle_status == foreign.lifecycle_status == BuildRecordStatus.DRAFT

    for kind in ("scope", "coding"):
        calls = [call for call in backend.calls if call["kind"] == kind]
        assert len(calls) == 2
        graphs = [call["payload"]["refinement_context"]["dependency_graph"] if kind == "scope"
                  else call["payload"]["dependency_graph"] for call in calls]
        assert [graph["build_record_id"] for graph in graphs] == [original.id, child_b.id]
        if kind == "scope":
            for call, record in zip(calls, (original, child_b), strict=True):
                for tool in ("get_context_graph_catalog", "search_app_source_context"):
                    provenance = call["payload"]["refinement_context"][tool]["provenance"]
                    assert provenance["build_record_id"] == record.id
                    assert provenance["app_id"] == record.app_id
                    assert provenance["verified"] is True
                    assert provenance["bundle_digest"] == f"sha256:{resolve_canonical_bundle_entry(record).sha256}"
        assert all(graph["app_id"] == "target" for graph in graphs)
        assert graphs[0]["graph_hash"] != graphs[1]["graph_hash"]
        assert (TARGET, OLD) in {(edge["source_path"], edge["target_path"]) for edge in graphs[0]["dependencies"]}
        assert (TARGET, OLD) not in {(edge["source_path"], edge["target_path"]) for edge in graphs[1]["dependencies"]}
        assert (TARGET, NEW) in {(edge["source_path"], edge["target_path"]) for edge in graphs[1]["dependencies"]}
        for graph, record in zip(graphs, (original, child_b), strict=True):
            assert graph["bundle_digest"] == f"sha256:{resolve_canonical_bundle_entry(record).sha256}"
            assert graph["recommended_paths"] == [TARGET]
    prompts = "\n".join(call["system"] + call["user"] for call in backend.calls)
    for forbidden in (SECRET, "CURRENT_ONLY", "FOREIGN_ONLY", "MUTABLE_ONLY"):
        assert forbidden not in prompts
    assert not any(app_id == "foreign" for app_id, _ in store.reads[reads_before:])
    assert not (tmp_path / "escape.py").exists()

    loaded = await load_context_graph_for_tool(context=tool_context(child_b), artifact_store=store, require_verified=True)
    assert loaded["present"]
    assert not any(node.label == "removedHelper" for node in loaded["graph"].nodes)
    for record, expected in ((child_b, SOURCE_B), (child_c, SOURCE_C)):
        raw = await read_verified_artifact_bundle(record)
        assert hashlib.sha256(raw).hexdigest() == resolve_canonical_bundle_entry(record).sha256
        with zipfile.ZipFile(record.commit_metadata.metadata["artifact_path"]) as bundle:
            prefix = record.commit_metadata.metadata["bundle_name"] + "/"
            assert bundle.read(prefix + TARGET).decode() == expected
            assert bundle.read(prefix + "ui/unrelated.ts").decode() == baseline["ui/unrelated.ts"]


@pytest.mark.asyncio
async def test_tampered_build_has_no_graph_or_mutable_workspace_fallback(cycle, tmp_path):
    store, backend, _harness, runtime = cycle
    original, _ = await register_build(tmp_path, store)
    await register_build(tmp_path, store, marker="valid-current-decoy")
    Path(original.commit_metadata.metadata["artifact_path"]).write_bytes(b"replaced archive")
    assessment = await runtime.get_checkpoint("dependency_graph_requested").analyze(
        context=tool_context(original),
        usage_context=AuxiliaryUsageContext(app_id="factory", user_id="owner"),
        selected_paths=[TARGET],
    )
    assert assessment is None
    assert backend.calls == []
    files = await runtime.get_checkpoint("scope_requested").materialize_files(
        refinement_request=RefinementRequest(
            app_id="factory", target_app_id="target", user_id="owner", build_family="app_bundle",
            build_record_id=original.id, raw_user_request=f"Update {TARGET}",
        ), selected_paths=[TARGET],
    )
    assert files == {}


@pytest.mark.asyncio
async def test_graph_recommendations_cannot_expand_coding_write_scope(cycle, tmp_path):
    store, backend, harness, _runtime = cycle
    original, baseline = await register_build(tmp_path, store)
    count = len(store.writes)
    backend.update_path = NEW
    result = await refine(harness, original, baseline)
    assert result.status == "failed"
    assert len(store.writes) == count
    assert Path(original.commit_metadata.metadata["workspace_dir"], NEW).read_text() == baseline[NEW]


@pytest.mark.asyncio
async def test_workflow_hook_consumes_exact_registered_build_and_real_specialist(cycle, tmp_path, monkeypatch):
    from factory_app.workflows._shared.context_graph import load_context_graph_context as loader
    from factory_app.workflows._shared.context_graph.hook_context_graph import (
        inject_context_graph_context,
    )
    from mozaiksai.core.workflow.agents.factory import ContextVariablesBridge
    from mozaiksai.core.workflow.context.adapter import _RuntimeContextVariables

    store, backend, _harness, runtime = cycle
    original, _ = await register_build(tmp_path, store)
    await register_build(tmp_path, store, marker="newer-current", source="export const CURRENT_ONLY = 1;\n")
    specialist = runtime.get_checkpoint("dependency_graph_requested")
    monkeypatch.setattr(loader, "DependencyGraphAgent", lambda: specialist)
    context = _RuntimeContextVariables({
        "app_id": "factory", "user_id": "owner", "chat_id": "revision-chat", "workflow_name": "AppGenerator",
        "run_build_binding": RunBuildBinding(
            target_app_id="target", build_registry_id="registry-target", build_id="workflow-refinement", phase="refinement",
        ).model_dump(),
        "artifact_version_id": "design-input-not-the-source", "artifact_kind": "design_docs",
        "revision_source_artifact_version_id": original.id, "build_mode": "revision", "workflow_sequence": "design_revision",
        "refinement_request": f"Update the copy in {TARGET}.",
        "dependency_graph": {"recommended_paths": ["caller-owned-escape.py"]},
    })
    loaded = await loader.load_context_graph_context(context)
    assert loaded["present"]
    evidence = context.get("context_graph_pack")["dependency_graph"]
    assert evidence["build_record_id"] == original.id
    assert evidence["bundle_digest"] == f"sha256:{resolve_canonical_bundle_entry(original).sha256}"
    graph_call, = backend.calls
    assert graph_call["kind"] == "graph"
    assert graph_call["payload"]["evidence"]["build_record_id"] == original.id
    agent = SimpleNamespace(
        name="ServiceAgent", system_message="Workflow implementation prompt", context_variables=ContextVariablesBridge(context.snapshot()),
    )
    agent.update_system_message = lambda value: setattr(agent, "system_message", value)
    inject_context_graph_context(agent, [])
    assert "Specialist assessment (advisory JSON)" in agent.system_message
    assert original.id in agent.system_message
    assert OLD in agent.system_message
    for forbidden in (SECRET, "CURRENT_ONLY", "caller-owned-escape.py"):
        assert forbidden not in agent.system_message

    first_generation = _RuntimeContextVariables({
        **context.snapshot(), "revision_source_artifact_version_id": None, "revision_first_generation": True,
    })
    result = await loader.load_context_graph_context(first_generation)
    assert result["reason"] == "no_revision_baseline"
    assert first_generation.get("source_context_catalog") is None
    assert len(backend.calls) == 1


LIVE_TARGET = "ui/pages/custom/graph_cycle.jsx"
LIVE_OLD = "ui/pages/custom/graph_old.jsx"
LIVE_NEW = "ui/pages/custom/graph_new.jsx"
LIVE_SOURCE_A = SOURCE_A.replace(": string", "").replace("../components/old_labels", "./graph_old.jsx")
LIVE_SOURCE_B = SOURCE_B.replace("../components/new_labels", "./graph_new.jsx")
LIVE_SOURCE_C = LIVE_SOURCE_B.replace("<h1>", '<h1 aria-label="Final">')


def contained_cycle_files() -> dict[str, str]:
    """Extend the established CRUD smoke fixture within its generated UI contract."""
    files = json.loads((Path(__file__).parent / "fixtures/runtime_smoke_good_bundle_fdfa818e.json").read_text())[
        "files"
    ]
    # The saved fixture predates the self-hosted entitlement_dispatch module.
    files.pop("config/subscriptions.yaml")
    files.update({
        LIVE_TARGET: LIVE_SOURCE_A,
        LIVE_OLD: 'export function oldLabel() { return "Before"; }\n',
        LIVE_NEW: 'export function newLabel() { return "After"; }\n',
        "ui/index.js": (
            'import GraphCycle from "./pages/custom/graph_cycle.jsx";\n'
            'export function register(registerComponent) { registerComponent("GraphCycle", GraphCycle); }\n'
        ),
    })
    manifest = json.loads(files["ui/route_manifest.json"])
    manifest["pages"].append({
        "path": "/graph-cycle", "component": "GraphCycle", "label": "Graph cycle",
        "meta": {"title": "Graph cycle", "requiresAuth": False},
    })
    files["ui/route_manifest.json"] = json.dumps(manifest)
    return files


def test_contained_cycle_fixture_preserves_canonical_runtime_contracts():
    from mozaiksai.core.validation import (
        GeneratedAppValidationRequest,
        validate_generated_app_bundle,
    )

    files = contained_cycle_files()
    result = validate_generated_app_bundle(GeneratedAppValidationRequest(files=files))
    assert result.passed, result.diagnostics
    assert "ui/pages/tasks.yaml" in files
    assert json.loads(files["ui/route_manifest.json"])["pages"][-1]["component"] == "GraphCycle"


def assert_contained_validation(result: dict[str, Any], files: dict[str, str], image_id: str) -> None:
    from factory_app.workflows.AppGenerator.tools.task_integrity import artifact_snapshot_digest
    from tests.test_app_acceptance_isolation import _source_digest

    assert result["validation_status"] == "passed", json.dumps(result, indent=2)
    acceptance = result["app_bundle_acceptance_result"]
    assert acceptance["status"] == "passed" and acceptance["passed"] is True
    assert acceptance["snapshot_digest"] == artifact_snapshot_digest({}, files)
    assert acceptance["app_runtime_load_worker"]["passed"] is True
    for evidence in (acceptance["app_runtime_load"], acceptance["app_runtime_smoke"]):
        assert evidence["validator_image_id"] == image_id
        assert evidence["source_content_sha256"] == _source_digest(files)
    smoke = acceptance["app_runtime_smoke"]
    assert smoke["observer_origin"] == "trusted_external_probe_v1"
    assert smoke["observer_completion_verified"] is True
    assert smoke["observer_cleanup_verified"] is True
    build = result["app_validation_result"]
    assert build["validation_status"] == "passed"
    assert build["sandbox_provider"] == "docker"
    assert build["sandbox_image_id"] == image_id


@pytest.mark.skipif(
    not os.getenv("MOZAIKS_TEST_CONTAINED_SMOKE_IMAGE"),
    reason="set MOZAIKS_TEST_CONTAINED_SMOKE_IMAGE to a freshly built local preview image",
)
@pytest.mark.asyncio
async def test_graph_refinement_cycle_passes_real_pinned_docker_validation(cycle, tmp_path, monkeypatch):
    """Three validated immutable drafts; explicit Studio acceptance remains separate."""
    from mozaiksai.core.validation import validate_generated_app_candidate

    inspected = subprocess.run(
        ["docker", "image", "inspect", "--format", "{{.Id}}", os.environ["MOZAIKS_TEST_CONTAINED_SMOKE_IMAGE"]],
        capture_output=True, text=True, check=True, timeout=5,
    )
    image_id = inspected.stdout.strip()
    monkeypatch.setenv("MOZAIKS_APP_RUNTIME_IMAGE_ID", image_id)
    monkeypatch.setenv("MOZAIKS_APP_VALIDATION_STRATEGY", "docker")
    store, backend, harness, _runtime = cycle
    backend.target_path = backend.update_path = LIVE_TARGET
    files = contained_cycle_files()
    app_id = json.loads(files["data/contract.json"])["app_id"]
    initial_validation = await validate_generated_app_candidate(
        files=files, app_id=app_id, validation_strategy="docker", timeout_seconds=180,
    )
    assert_contained_validation(initial_validation, files, image_id)
    original, _ = await register_build(
        tmp_path, store, app_id=app_id, marker="contained-initial", files=files,
        validation_result=initial_validation,
    )
    previous = original
    original_bytes = await read_verified_artifact_bundle(original)
    records = [original]
    for replacement in (LIVE_SOURCE_B, LIVE_SOURCE_C):
        backend.replacement = replacement
        result = await refine(harness, previous, files, target_path=LIVE_TARGET, validation_strategy="docker")
        assert result.status == "validated", result.error
        files = {**files, LIVE_TARGET: replacement}
        assert_contained_validation(result.validation_result, files, image_id)
        child = store.records[result.metadata["build_record_id"]]
        assert child.parent_build_record_id == previous.id
        assert child.validation_status.value == child.app_validation_status == "passed"
        assert child.lifecycle_status == BuildRecordStatus.DRAFT
        assert "acceptance" not in child.commit_metadata.metadata
        assert child.commit_metadata.metadata["validation_result"]["app_bundle_acceptance_result"]["snapshot_digest"] == (
            result.validation_result["app_bundle_acceptance_result"]["snapshot_digest"]
        )
        raw = await read_verified_artifact_bundle(child)
        assert hashlib.sha256(raw).hexdigest() == resolve_canonical_bundle_entry(child).sha256
        with zipfile.ZipFile(child.commit_metadata.metadata["artifact_path"]) as archive:
            prefix = child.commit_metadata.metadata["bundle_name"] + "/"
            assert archive.read(prefix + LIVE_TARGET).decode() == replacement
            assert archive.read(prefix + "ui/pages/tasks.yaml").decode() == files["ui/pages/tasks.yaml"]
        records.append(child)
        previous = child
    assert await read_verified_artifact_bundle(original) == original_bytes
    calls = [call for call in backend.calls if call["kind"] == "coding"]
    assert len(calls) == 2
    for call, record in zip(calls, records[:2], strict=True):
        evidence = call["payload"]["dependency_graph"]
        assert evidence["build_record_id"] == record.id
        assert evidence["bundle_digest"] == f"sha256:{resolve_canonical_bundle_entry(record).sha256}"
        assert evidence["recommended_paths"] == [LIVE_TARGET]
    dependencies = [
        {(edge["source_path"], edge["target_path"]) for edge in call["payload"]["dependency_graph"]["dependencies"]}
        for call in calls
    ]
    assert (LIVE_TARGET, LIVE_OLD) in dependencies[0]
    assert (LIVE_TARGET, LIVE_OLD) not in dependencies[1]
    assert (LIVE_TARGET, LIVE_NEW) in dependencies[1]
