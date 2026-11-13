"""Exact BuildRecord authority across graph, corpus and intelligence retrieval."""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from factory_app.refinement_harness.tools import app_intelligence, source_context
from factory_app.refinement_harness.tools._context_graph import load_context_graph_for_tool
from factory_app.refinement_harness.tools.app_intelligence import get_app_intelligence_context
from factory_app.refinement_harness.tools.get_context_graph_catalog import get_context_graph_catalog
from factory_app.refinement_harness.tools.get_context_graph_scope import get_context_graph_scope
from factory_app.refinement_harness.tools.get_contract_surface_context import (
    get_contract_surface_context,
)
from factory_app.refinement_harness.tools.source_context import (
    get_related_app_source_files,
    read_app_source_file,
    search_app_source_context,
)
from mozaiksai.control_plane import app_context
from mozaiksai.control_plane.app_context import (
    AppIntelligenceSnapshotLookupResult,
    SourceContextBundleLookupResult,
)
from mozaiksai.core.app_context.indexer import index_file_map
from mozaiksai.core.app_context.scan_policy import default_context_graph_scan_policy
from mozaiksai.core.artifacts import content_store
from mozaiksai.core.artifacts.models import BuildRecord


def _context(**changes):
    return {
        "app_id": "studio_host",
        "target_app_id": "target_app",
        "build_record_id": "build_a",
        "build_family": "app_bundle",
        "build_key": "app_bundle",
        "raw_user_request": "report rendering",
        "extra": {"source_path": "src/report.py", "selected_file_paths": ["src/report.py"]},
        **changes,
    }


def _record(tmp_path: Path, *, files=None, verified=True):
    files = files if files is not None else {
        "src/report.py": "from .helpers import render\ndef report():\n    return render()\n",
        "src/helpers.py": "def render():\n    return 'requested build'\n",
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for path, content in files.items():
            archive.writestr(f"Bundle/{path}", content)
    archive_bytes = buffer.getvalue()
    archive_path = tmp_path / "bundle.zip"
    archive_path.write_bytes(archive_bytes)
    digest = hashlib.sha256(archive_bytes).hexdigest()
    return BuildRecord(
        id="build_a", app_id="target_app", build_family="app_bundle", build_key="app_bundle",
        version_number=1, lineage_root_id="build_a",
        files_manifest=[{
            "path": "Bundle/Bundle.zip", "content_type": "application/zip", "sha256": digest,
        }] if verified else [],
        commit_metadata={"metadata": {
            "artifact_path": str(archive_path), **({"bundle_name": "Bundle"} if verified else {}),
        }},
    )


def _store(record):
    store = AsyncMock()
    store.get_build_record.return_value = record
    return store


@pytest.fixture
def no_current(monkeypatch):
    forbidden = AsyncMock(side_effect=AssertionError("An explicit build must not read current context"))
    monkeypatch.setattr(app_context, "get_current_app_context_graph", forbidden)
    monkeypatch.setattr(source_context, "get_current_source_context_bundle", forbidden)
    monkeypatch.setattr(app_intelligence, "get_current_app_intelligence_snapshot", forbidden)
    return forbidden


@pytest.mark.asyncio
async def test_exact_graph_corpus_and_intelligence_share_verified_build(tmp_path, no_current):
    record = _record(tmp_path)
    store = _store(record)
    loaded = await load_context_graph_for_tool(context=_context(), artifact_store=store, require_verified=True)
    assert loaded["present"] is True
    assert set(loaded["file_map"]) == {"src/report.py", "src/helpers.py"}
    assert loaded["graph"].app_id == "target_app"
    assert loaded["provenance"] == {
        "app_id": "target_app", "build_record_id": "build_a", "build_family": "app_bundle",
        "build_key": "app_bundle", "verified": True,
        "bundle_digest": f"sha256:{record.files_manifest[0].sha256}",
    }
    assert set(loaded["workspace"]) == {"source", "provenance", "scan_health", "context_graph_health_report"}
    assert "artifact_path" not in json.dumps(loaded["provenance"])
    store.get_build_record.assert_awaited_once_with(app_id="target_app", build_record_id="build_a")
    store.list_build_records.assert_not_awaited()

    for tool in (get_context_graph_catalog, get_context_graph_scope, get_contract_surface_context,
                 search_app_source_context, read_app_source_file, get_related_app_source_files,
                 get_app_intelligence_context):
        result = await tool(context=_context(), artifact_store=store)
        assert result["present"] is True
        assert result["provenance"] == loaded["provenance"]
    read = await read_app_source_file(path="src/helpers.py", context=_context(), artifact_store=store)
    assert "requested build" in read["content"]
    related = await get_related_app_source_files(context=_context(), artifact_store=store)
    assert any(item["path"] == "src/helpers.py" for item in related["files"])
    no_current.assert_not_awaited()
    store.list_build_records.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation,reason", [
    ("missing", "artifact_not_found"), ("app_id", "artifact_identity_mismatch"),
    ("id", "artifact_identity_mismatch"), ("build_family", "artifact_identity_mismatch"),
    ("build_key", "artifact_identity_mismatch"), ("corrupt", "content_digest_unavailable_or_invalid"),
    ("unavailable", "content_digest_unavailable_or_invalid"),
])
async def test_bad_requested_build_never_falls_back(tmp_path, no_current, mutation, reason):
    record = _record(tmp_path)
    if mutation == "missing":
        record = None
    elif mutation == "corrupt":
        Path(record.commit_metadata.metadata["artifact_path"]).write_bytes(b"corrupt")
    elif mutation == "unavailable":
        record.commit_metadata.metadata["artifact_path"] = str(tmp_path / "absent.zip")
    else:
        setattr(record, mutation, "foreign")
    store = _store(record)
    for tool in (load_context_graph_for_tool, search_app_source_context, read_app_source_file,
                 get_related_app_source_files, get_app_intelligence_context):
        result = await tool(context=_context(), artifact_store=store)
        assert result["present"] is False
        assert result["reason"] == reason
        assert "foreign" not in json.dumps(result)
        assert str(tmp_path) not in json.dumps(result)
    no_current.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("files", [{}, {"image.svg": "<svg/>"}, {".env": "TOKEN=private"}])
async def test_no_supported_sources_is_unavailable(tmp_path, no_current, files):
    result = await load_context_graph_for_tool(
        context=_context(), artifact_store=_store(_record(tmp_path, files=files)), require_verified=True,
    )
    assert result["present"] is False
    assert result["reason"] == "artifact_source_unavailable"
    no_current.assert_not_awaited()


@pytest.mark.asyncio
async def test_verified_manifest_wins_over_mutable_workspace_and_detects_changed_archive(tmp_path, no_current):
    record = _record(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "decoy.py").write_text("def other_build(): pass", encoding="utf-8")
    record.commit_metadata.metadata["workspace_dir"] = str(workspace)
    loaded = await load_context_graph_for_tool(context=_context(), artifact_store=_store(record), require_verified=True)
    assert loaded["present"] is True
    assert "decoy.py" not in loaded["file_map"]
    Path(record.commit_metadata.metadata["artifact_path"]).write_bytes(b"different bytes")
    failed = await load_context_graph_for_tool(context=_context(), artifact_store=_store(record))
    assert failed["present"] is False
    assert failed["reason"] == "content_digest_unavailable_or_invalid"


@pytest.mark.asyncio
async def test_verified_content_reference_uses_canonical_reader_bytes(tmp_path, monkeypatch):
    record = _record(tmp_path)
    raw = Path(record.commit_metadata.metadata.pop("artifact_path")).read_bytes()
    record.commit_metadata.metadata.update(content_ref="private-storage-reference", content_backend="local")
    backend = AsyncMock()
    backend.backend_name = "local"
    backend.get_bundle.return_value = raw
    monkeypatch.setattr(content_store, "get_artifact_content_store", lambda: backend)
    loaded = await load_context_graph_for_tool(context=_context(), artifact_store=_store(record), require_verified=True)
    assert loaded["present"] is True
    assert loaded["provenance"]["verified"] is True
    assert "private-storage-reference" not in json.dumps(loaded["workspace"])
    backend.get_bundle.assert_awaited_once_with("private-storage-reference")


@pytest.mark.asyncio
async def test_legacy_zip_is_readable_but_cannot_supply_verified_authority(tmp_path, no_current):
    record = _record(tmp_path, verified=False)
    unverified = await load_context_graph_for_tool(context=_context(), artifact_store=_store(record))
    assert unverified["present"] is True
    assert unverified["provenance"]["verified"] is False
    assert unverified["provenance"]["bundle_digest"] is None
    strict = await load_context_graph_for_tool(context=_context(), artifact_store=_store(record), require_verified=True)
    assert strict["present"] is False
    assert strict["reason"] == "content_digest_unavailable_or_invalid"


@pytest.mark.asyncio
async def test_non_application_legacy_archive_remains_unverified(tmp_path):
    record = _record(tmp_path)
    record.build_family = record.build_key = "workflow_bundle"
    context = _context(build_family="workflow_bundle", build_key="workflow_bundle")
    loaded = await load_context_graph_for_tool(context=context, artifact_store=_store(record))
    assert loaded["present"] is True
    assert loaded["provenance"]["verified"] is False
    strict = await load_context_graph_for_tool(context=context, artifact_store=_store(record), require_verified=True)
    assert strict["present"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("old,new", [
    ("artifact_version_id", "build_record_id"), ("artifact_kind", "build_family"), ("artifact_key", "build_key"),
])
async def test_conflicting_context_aliases_rejected_before_storage(old, new):
    store = _store(None)
    for tool in (load_context_graph_for_tool, get_context_graph_catalog, get_context_graph_scope,
                 get_contract_surface_context, search_app_source_context, read_app_source_file,
                 get_related_app_source_files, get_app_intelligence_context):
        with pytest.raises(ValueError, match=f"conflicting_context_selectors:{new}"):
            await tool(context=_context(**{old: "conflict"}), artifact_store=store)
    store.get_build_record.assert_not_awaited()


@pytest.mark.asyncio
async def test_matching_legacy_alias_does_not_mutate_callers_context(tmp_path):
    context = _context(artifact_version_id="build_a")
    before = dict(context)
    result = await load_context_graph_for_tool(context=context, artifact_store=_store(_record(tmp_path)))
    assert result["present"] is True
    assert context == before


@pytest.mark.asyncio
@pytest.mark.parametrize("second_path", ["src/./report.py", "src\\report.py"])
async def test_archive_path_aliases_cannot_overwrite_source_authority(tmp_path, second_path):
    record = _record(tmp_path, files={"src/report.py": "def real(): pass", second_path: "def forged(): pass"})
    result = await load_context_graph_for_tool(context=_context(), artifact_store=_store(record), require_verified=True)
    assert result["present"] is False
    assert result["reason"] == "content_digest_unavailable_or_invalid"


@pytest.mark.asyncio
async def test_indexing_redacts_source_without_leaking_raw_workspace(tmp_path):
    secret = "never-send-this-secret"
    record = _record(tmp_path, files={
        "src/report.py": f'API_TOKEN = "{secret}"\ndef report():\n    return API_TOKEN\n',
        ".env": f"API_TOKEN={secret}",
    })
    loaded = await load_context_graph_for_tool(context=_context(), artifact_store=_store(record), require_verified=True)
    assert loaded["present"] is True
    assert ".env" not in loaded["file_map"]
    assert secret not in loaded["file_map"]["src/report.py"]
    assert "file_map" not in loaded["workspace"]
    for key in ("source_context_catalog", "app_intelligence_catalog", "workspace"):
        assert secret not in json.dumps(loaded[key], default=str)
    read = await read_app_source_file(context=_context(), artifact_store=_store(record))
    assert secret not in read["content"]


@pytest.mark.asyncio
async def test_current_corpus_remains_available_only_without_build_selector(monkeypatch):
    index = index_file_map(
        app_id="target_app", artifact_version_id="discovery", file_map={"src/report.py": "def report(): pass"},
    )
    current_source = AsyncMock(return_value=SourceContextBundleLookupResult(bundle=index.source_corpus))
    current_intelligence = AsyncMock(return_value=AppIntelligenceSnapshotLookupResult(snapshot=index.app_intelligence_snapshot))
    monkeypatch.setattr(source_context, "get_current_source_context_bundle", current_source)
    monkeypatch.setattr(app_intelligence, "get_current_app_intelligence_snapshot", current_intelligence)
    store = _store(None)
    for tool in (search_app_source_context, read_app_source_file, get_related_app_source_files, get_app_intelligence_context):
        result = await tool(context=_context(build_record_id=None), artifact_store=store)
        assert result["present"] is True
        assert result["source"] == "current_app_context"
        assert result["provenance"] is None
    store.get_build_record.assert_not_awaited()
    current_source.reset_mock()
    current_intelligence.reset_mock()
    for tool in (search_app_source_context, get_app_intelligence_context):
        result = await tool(context=_context(build_record_id=" "), artifact_store=store)
        assert result["present"] is False
    current_source.assert_not_awaited()
    current_intelligence.assert_not_awaited()


@pytest.mark.asyncio
async def test_standalone_discovery_rejects_foreign_app_payload(monkeypatch):
    index = index_file_map(
        app_id="foreign_app", artifact_version_id="discovery", file_map={"src/report.py": "def report(): pass"},
    )
    monkeypatch.setattr(source_context, "get_current_source_context_bundle", AsyncMock(
        return_value=SourceContextBundleLookupResult(bundle=index.source_corpus),
    ))
    monkeypatch.setattr(app_intelligence, "get_current_app_intelligence_snapshot", AsyncMock(
        return_value=AppIntelligenceSnapshotLookupResult(snapshot=index.app_intelligence_snapshot),
    ))
    for tool in (search_app_source_context, read_app_source_file, get_related_app_source_files, get_app_intelligence_context):
        result = await tool(context=_context(build_record_id=None), artifact_store=_store(None))
        assert result["present"] is False
        assert result["reason"].endswith("identity_mismatch")
        assert "foreign_app" not in json.dumps(result)


@pytest.mark.asyncio
@pytest.mark.parametrize("context", [None, {}, {"build_record_id": "build_a"}, {"app_id": "target_app"}])
async def test_verified_context_requires_app_and_build_identity(context):
    store = _store(None)
    result = await load_context_graph_for_tool(context=context, artifact_store=store, require_verified=True)
    assert result["present"] is False
    assert result["reason"] == "missing_app_id_or_build_record"
    store.get_build_record.assert_not_awaited()


@pytest.mark.asyncio
async def test_verified_archive_keeps_source_above_legacy_80kb_limit(tmp_path):
    record = _record(tmp_path, files={
        "app/src/report.py": "from .helpers import render\ndef report():\n    return render()\n",
        "app/src/helpers.py": "def render():\n    return 1\n#" + "x" * 90_000 + "\n",
    })
    loaded = await load_context_graph_for_tool(context=_context(), artifact_store=_store(record), require_verified=True)
    assert loaded["present"] is True
    assert set(loaded["file_map"]) == {"app/src/report.py", "app/src/helpers.py"}
    assert len(loaded["file_map"]["app/src/helpers.py"]) > 80_000
    assert loaded["workspace"]["scan_health"]["artifact_source_incomplete"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("content", [b"\xff\xfe", "#" + "x" * (default_context_graph_scan_policy().max_file_bytes + 1)])
async def test_omitted_supported_source_is_reported_to_specialist_and_graph_tools(tmp_path, content):
    record = _record(tmp_path, files={
        "src/report.py": "from .helpers import render\ndef report():\n    return render()\n",
        "src/helpers.py": content,
    })
    loaded = await load_context_graph_for_tool(context=_context(), artifact_store=_store(record), require_verified=True)
    assert loaded["present"] is True
    assert "src/helpers.py" not in loaded["file_map"]
    assert loaded["workspace"]["scan_health"]["artifact_source_incomplete"] is True
    catalog = await get_context_graph_catalog(context=_context(), artifact_store=_store(record))
    assert catalog["scan_health"]["artifact_source_incomplete"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy_kind", ["archive", "workspace"])
async def test_legacy_source_read_limits_are_unchanged_but_report_incomplete(tmp_path, legacy_kind):
    record = _record(tmp_path, verified=False, files={
        "src/report.py": "def report(): pass",
        "src/helpers.py": "#" + "x" * 90_000,
    })
    if legacy_kind == "workspace":
        workspace = tmp_path / "workspace"
        (workspace / "src").mkdir(parents=True)
        (workspace / "src/report.py").write_text("def report(): pass", encoding="utf-8")
        (workspace / "src/helpers.py").write_text("#" + "x" * 90_000, encoding="utf-8")
        record.commit_metadata.metadata["workspace_dir"] = str(workspace)
    loaded = await load_context_graph_for_tool(context=_context(), artifact_store=_store(record))
    assert loaded["present"] is True
    assert "src/helpers.py" not in loaded["file_map"]
    assert loaded["workspace"]["scan_health"]["artifact_source_incomplete"] is True
    assert loaded["provenance"]["verified"] is False


@pytest.mark.asyncio
async def test_verified_archive_rejects_members_outside_persisted_bundle_prefix(tmp_path):
    record = _record(tmp_path)
    archive_path = Path(record.commit_metadata.metadata["artifact_path"])
    with zipfile.ZipFile(archive_path, "a") as archive:
        archive.writestr("outside.py", "def foreign(): pass")
    record.files_manifest[0].sha256 = hashlib.sha256(archive_path.read_bytes()).hexdigest()
    loaded = await load_context_graph_for_tool(context=_context(), artifact_store=_store(record), require_verified=True)
    assert loaded["present"] is False
    assert loaded["reason"] == "content_digest_unavailable_or_invalid"
