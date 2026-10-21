from __future__ import annotations

import argparse
import zipfile

import pytest

from scripts.dogfood_context_graph_refinement import _run


@pytest.mark.asyncio
async def test_scope_dogfood_stages_patch_without_claiming_app_validation(tmp_path, monkeypatch):
    monkeypatch.setenv("MOZAIKS_APP_VALIDATION_STRATEGY", "skip")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    source = workspace / "support.py"
    original = "def summary():\n    return 'Support workspace'\n"
    source.write_text(original, encoding="utf-8")
    result = await _run(argparse.Namespace(
        workspace=str(workspace), output_root=str(tmp_path / "evidence"),
        app_id="synthetic-context-dogfood", request="Update the support summary.",
        target_path="support.py",
    ))

    assert result["app_intelligence"]["indexed_file_count"] == 1
    assert result["scope"]["proposal"]["resolution"] == "scoped_files"
    worker = result["coding_worker"]
    assert worker["status"] == "planned"
    assert worker["validation_status"] == "skipped"
    assert worker["acceptance_status"] == "skipped"
    assert worker["app_validation_status"] == "skipped"
    assert worker["applied_paths"] == ["support.py"]
    assert worker["build_record_id"]
    with zipfile.ZipFile(worker["artifact_path"]) as archive:
        [saved_path] = archive.namelist()
        assert saved_path.startswith("refinement_") and saved_path.endswith("/support.py")
        saved = archive.read(saved_path).decode("utf-8")
    assert saved.startswith(original.rstrip())
    assert "staged patch only" in saved
    assert source.read_text(encoding="utf-8") == original
    assert result["source_mutated"] is False
