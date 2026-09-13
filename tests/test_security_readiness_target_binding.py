from __future__ import annotations

import hashlib
import stat
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import yaml

from factory_app.workflows.SecurityReadiness.tools import build_artifact
from factory_app.workflows.SecurityReadiness.tools.inspect_generated_app_security import (
    inspect_generated_app_security,
)
from factory_app.workflows.SecurityReadiness.tools.record_security_findings import (
    record_security_findings,
)
from mozaiksai.core.artifacts.models import BuildRecord, BuildRecordStatus
from mozaiksai.core.workflow.agents.factory import ContextVariablesBridge, _wrap_tool_with_context
from mozaiksai.core.workflow.context.authority import (
    CALLER_INPUT_WRITER,
    ContextAuthorityError,
    build_context_authority_policy,
)
from mozaiksai.core.workflow.context.schema import load_context_variables_config

ROOT = Path(__file__).resolve().parents[1]
RUN = ("SecurityReadiness", "factory-host", "chat_1")


def security_bridge(project="project_a", *, user_id="owner_1", initial=None):
    config = yaml.safe_load((ROOT / "factory_app/workflows/SecurityReadiness/context_variables.yaml").read_text())
    definitions = load_context_variables_config(config).definitions
    policy = build_context_authority_policy(workflow_name=RUN[0], definitions=definitions)
    bridge = ContextVariablesBridge({
        "app_id": RUN[1], "user_id": user_id,
        "run_build_binding": {
            "target_app_id": f"target_{project}", "build_registry_id": project,
            "build_id": "build_1", "phase": "genesis",
        },
        "artifact_version_id": f"artifact_{project}", "security_readiness_mode": "advisory",
        **(initial or {}),
    }, authority_policy=policy)
    bridge._bind_run(RUN, policy)
    return bridge


async def invoke(tool, bridge, **kwargs):
    return await _wrap_tool_with_context(tool, bridge)(**kwargs)


@pytest.fixture(name="security_build")
def security_build_fixture(monkeypatch, tmp_path):
    records, artifacts = {}, {}

    async def get_record(*, owner_user_id, build_registry_id):
        record = records.get(build_registry_id)
        return {"app": record if record and record["owner_user_id"] == owner_user_id else None}

    async def get_artifact(*, app_id, build_record_id):
        artifact = artifacts.get(build_record_id)
        return artifact if artifact and artifact.app_id == app_id else None

    registry = SimpleNamespace(get_app_record=AsyncMock(side_effect=get_record))
    store = SimpleNamespace(get_build_record=AsyncMock(side_effect=get_artifact))
    monkeypatch.setattr(build_artifact, "AppRegistryService", lambda: registry)
    monkeypatch.setattr(build_artifact, "get_artifact_store", lambda: store)

    def add(files=None, *, project="project_a", entries=None):
        bridge = security_bridge(project)
        binding = dict(bridge.get("run_build_binding"))
        artifact_id = f"artifact_{project}"
        archive_path = tmp_path / f"{project}.zip"
        with zipfile.ZipFile(archive_path, "w") as archive:
            for path, content in (files if files is not None else {"app.json": '{"authRequired": true}'}).items():
                archive.writestr(f"bundle/{path}", content)
            for info, content in entries or []:
                archive.writestr(info, content)
        raw = archive_path.read_bytes()
        artifact = BuildRecord(
            id=artifact_id, app_id=binding["target_app_id"], build_family="app_bundle",
            build_key="app_bundle", version_number=1, lineage_root_id=artifact_id,
            lifecycle_status="current",
            files_manifest=[{"path": "bundle/bundle.zip", "sha256": hashlib.sha256(raw).hexdigest(), "content_type": "application/zip"}],
            commit_metadata={"author_user_id": "owner_1", "metadata": {
                **binding, "bundle_name": "bundle", "artifact_path": str(archive_path),
            }},
        )
        record = {
            "app_id": binding["target_app_id"], "chat_app_id": RUN[1], "owner_user_id": "owner_1",
            "build_registry_id": project,
            "current_build_run": {"build_id": "build_1", "phase": "genesis", "artifact_version_id": artifact_id},
        }
        records[project], artifacts[artifact_id] = record, artifact
        return SimpleNamespace(bridge=bridge, record=record, artifact=artifact, archive=archive_path)

    return SimpleNamespace(add=add, records=records, artifacts=artifacts, registry=registry, store=store)


@pytest.mark.asyncio
async def test_owned_current_artifact_supplies_files_not_context(security_build):
    build = security_build.add({"app.json": '{"authRequired": true}'})
    build.bridge = security_bridge(initial={
        "generated_files": {"app.json": '{"authRequired": false}'},
        "bundle_path": "untrusted-directory",
    })
    result = await invoke(inspect_generated_app_security, build.bridge)
    assert result["status"] == "attention_required"
    assert result["target_app_id"] == "target_project_a"
    assert result["artifact_version_id"] == build.artifact.id
    security_build.registry.get_app_record.assert_awaited_once_with(owner_user_id="owner_1", build_registry_id="project_a")
    security_build.store.get_build_record.assert_awaited_once_with(app_id="target_project_a", build_record_id=build.artifact.id)
    assert build.bridge.get("app_id") == RUN[1]


@pytest.mark.asyncio
@pytest.mark.parametrize("fault,code", [
    ("foreign_owner", "target_unavailable"), ("foreign_host", "target_unavailable"),
    ("foreign_target", "target_unavailable"), ("new_build", "build_superseded"),
    ("missing_build", "build_superseded"), ("missing_artifact", "artifact_missing"),
    ("foreign_selector", "artifact_not_current"), ("old_selector", "artifact_not_current"),
    ("wrong_artifact_target", "artifact_unavailable"), ("wrong_author", "artifact_binding_mismatch"),
    ("wrong_build_metadata", "artifact_binding_mismatch"), ("wrong_registry_metadata", "artifact_binding_mismatch"),
    ("wrong_family", "artifact_manifest_invalid"), ("missing_binding", "binding_invalid"),
])
async def test_foreign_or_stale_source_fails_before_reading_files(security_build, monkeypatch, fault, code):
    build = security_build.add()
    from factory_app.workflows.SecurityReadiness.tools import (
        inspect_generated_app_security as scanner,
    )
    reader = AsyncMock()
    monkeypatch.setattr(scanner, "read_artifact_bundle", reader)
    if fault == "foreign_owner":
        build.record["owner_user_id"] = "someone_else"
    elif fault == "foreign_host":
        build.record["chat_app_id"] = "another_host"
    elif fault == "foreign_target":
        build.record["app_id"] = "another_target"
    elif fault == "new_build":
        build.record["current_build_run"]["build_id"] = "build_2"
    elif fault == "missing_build":
        build.record["current_build_run"] = {}
    elif fault == "missing_artifact":
        build.record["current_build_run"].pop("artifact_version_id")
    elif fault in {"foreign_selector", "old_selector"}:
        build.bridge.set("artifact_version_id", fault)
    elif fault == "wrong_artifact_target":
        build.artifact.app_id = "other_target"
    elif fault == "wrong_author":
        build.artifact.commit_metadata.author_user_id = "other_owner"
    elif fault == "wrong_build_metadata":
        build.artifact.commit_metadata.metadata["build_id"] = "build_old"
    elif fault == "wrong_registry_metadata":
        build.artifact.commit_metadata.metadata["build_registry_id"] = "other_registry"
    elif fault == "wrong_family":
        build.artifact.build_family = "theme_capture"
    else:
        build.bridge = security_bridge(initial={"run_build_binding": None})
    result = await invoke(inspect_generated_app_security, build.bridge)
    assert result["source_error"] == f"security_source_{code}"
    assert result["status"] == "not_assessed"
    assert result["checked_file_count"] == 0
    assert build.bridge.get("security_readiness_recorded") is False
    reader.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["stale", "superseded", "archived", "deleted"])
async def test_retired_artifact_cannot_be_assessed(security_build, status):
    build = security_build.add()
    build.artifact.lifecycle_status = BuildRecordStatus(status)
    result = await invoke(inspect_generated_app_security, build.bridge)
    assert result["source_error"] == "security_source_artifact_stale"


@pytest.mark.asyncio
async def test_digest_mismatch_is_not_a_clean_assessment(security_build):
    build = security_build.add()
    build.artifact.files_manifest[0].sha256 = "0" * 64
    result = await invoke(inspect_generated_app_security, build.bridge)
    assert result["source_error"] == "artifact_bundle_digest_mismatch"
    assert result["status"] == "not_assessed"


@pytest.mark.asyncio
@pytest.mark.parametrize("path,content,code", [
    ("../outside.py", "", "unsafe_path"), ("/absolute.py", "", "unsafe_path"),
    ("C:/outside.py", "", "unsafe_path"), ("bundle/broken.py", b"\xff", "non_text_source"),
    ("bundle/app.json", "{}", "duplicate_path"),
])
async def test_source_diagnostics_block_partial_assessment(security_build, path, content, code):
    build = security_build.add(entries=[(path, content)])
    result = await invoke(inspect_generated_app_security, build.bridge)
    assert result["source_error"] == "security_source_incomplete"
    assert any(item["code"] == code and item["blocking"] for item in result["source_diagnostics"])
    recorded = await invoke(record_security_findings, build.bridge)
    assert recorded["source_diagnostics"] == result["source_diagnostics"]
    assert build.bridge.get("security_readiness_recorded") is False


@pytest.mark.asyncio
async def test_symlink_is_reported_without_following_it(security_build):
    info = zipfile.ZipInfo("bundle/link.py")
    info.external_attr = (stat.S_IFLNK | 0o777) << 16
    build = security_build.add(entries=[(info, "outside.py")])
    result = await invoke(inspect_generated_app_security, build.bridge)
    assert result["source_error"] == "security_source_incomplete"
    assert result["source_diagnostics"][0]["code"] == "symlink"


@pytest.mark.asyncio
async def test_current_build_is_revalidated_before_recording(security_build, monkeypatch):
    build = security_build.add()
    await invoke(inspect_generated_app_security, build.bridge)
    build.record["current_build_run"]["build_id"] = "build_2"
    from factory_app.workflows.SecurityReadiness.tools import record_security_findings as recorder
    dispatch = AsyncMock()
    monkeypatch.setattr(recorder, "dispatch_workflow_module_action", dispatch)
    result = await invoke(record_security_findings, build.bridge)
    assert result["source_error"] == "security_source_build_superseded"
    assert build.bridge.get("security_readiness_recorded") is False
    dispatch.assert_not_awaited()


def test_runtime_binding_and_scan_evidence_are_not_caller_writable():
    bridge = security_bridge()
    for key in ("run_build_binding", "security_readiness_summary", "security_readiness_findings", "security_readiness_recorded"):
        with pytest.raises(ContextAuthorityError):
            bridge._authority_policy.require_can_write(key, writer_id=CALLER_INPUT_WRITER)


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["missing", "unreadable", "invalid_zip"])
async def test_unavailable_content_keeps_explicit_source_diagnostics(security_build, monkeypatch, fault):
    from factory_app.workflows._shared.artifact_bundle import LocalArtifactContentStore
    build = security_build.add()
    if fault == "missing":
        build.artifact.commit_metadata.metadata["artifact_path"] = str(build.archive.parent / "missing.zip")
    elif fault == "unreadable":
        monkeypatch.setattr(LocalArtifactContentStore, "get_bundle", AsyncMock(side_effect=OSError("private backend detail")))
    else:
        build.archive.write_bytes(b"not a zip")
        build.artifact.files_manifest[0].sha256 = hashlib.sha256(b"not a zip").hexdigest()
    result = await invoke(inspect_generated_app_security, build.bridge)
    assert result["source_error"] == "security_source_unavailable"
    assert result["source_diagnostics"][0]["blocking"] is True
    assert "private backend detail" not in str(result)
    assert result["checked_file_count"] == 0


@pytest.mark.asyncio
async def test_expected_binary_assets_are_disclosed_without_hiding_text_failures(security_build):
    build = security_build.add({"app.json": '{"authRequired": false}', "brand/logo.png": b"\x89PNG\x00"})
    scanned = await invoke(inspect_generated_app_security, build.bridge)
    assert scanned["status"] == "passed"
    assert scanned["checked_file_count"] == 1
    assert scanned["source_diagnostics"] == [{"path": "brand/logo.png", "code": "binary_asset", "blocking": False}]
    await invoke(record_security_findings, build.bridge)
    assert list(build.bridge.get("security_readiness_summary")["source_diagnostics"])


@pytest.mark.asyncio
async def test_app_relative_modules_and_auth_contract_are_scanned(security_build):
    build = security_build.add({
        "app.json": '{"authRequired": true}', "config/auth.yaml": "version: 1\n",
        "modules/orders/module.yaml": "module: {id: orders}\nactions:\n- {id: update, api_surface: private, permissions: []}\n",
    })
    scanned = await invoke(inspect_generated_app_security, build.bridge)
    ids = {item["finding_id"] for item in scanned["findings"]}
    assert "module_permissions:missing:orders:update" in ids
    assert "auth_contract:missing_auth_yaml" not in ids


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["new_artifact", "changed_selector", "changed_findings", "not_inspected"])
async def test_recording_cannot_relabel_or_invent_an_assessment(security_build, monkeypatch, fault):
    from factory_app.workflows.SecurityReadiness.tools import record_security_findings as recorder
    build = security_build.add()
    build.bridge.set("artifact_version_id", None)
    if fault != "not_inspected":
        await invoke(inspect_generated_app_security, build.bridge)
    kwargs = {}
    if fault in {"new_artifact", "changed_selector"}:
        artifact = build.artifact.model_copy(update={"id": "new_artifact", "version_number": 2})
        security_build.artifacts[artifact.id] = artifact
        build.record["current_build_run"]["artifact_version_id"] = artifact.id
        if fault == "changed_selector":
            build.bridge.set("artifact_version_id", artifact.id)
    elif fault == "changed_findings":
        kwargs["findings"] = []
    dispatch = AsyncMock()
    monkeypatch.setattr(recorder, "dispatch_workflow_module_action", dispatch)
    result = await invoke(record_security_findings, build.bridge, **kwargs)
    expected = {
        "new_artifact": "artifact_not_current", "changed_selector": "assessment_stale",
        "changed_findings": "findings_mismatch", "not_inspected": "not_inspected",
    }
    assert result["source_error"] == f"security_source_{expected[fault]}"
    assert build.bridge.get("security_readiness_recorded") is False
    dispatch.assert_not_awaited()
