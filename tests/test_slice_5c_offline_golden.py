from __future__ import annotations

from pathlib import Path

import pytest

from mozaiksai.core.artifacts.content_store import LocalArtifactContentStore
from mozaiksai.core.artifacts.revision_store import ArtifactRevisionStore
from mozaiksai.core.runtime.app.page_schema import load_app_page_schemas
from mozaiksai.core.semantics.artifact_revision import PublicationOutcome
from tests.slice_5c_revision_helpers import executable_revision_fixture
from tests.test_artifact_revision_store import _MemoryClient


@pytest.mark.asyncio
async def test_offline_revision_publication_restore_and_loader_golden(
    tmp_path: Path,
) -> None:
    fixture = executable_revision_fixture()
    store = ArtifactRevisionStore(
        content_store=LocalArtifactContentStore(root=tmp_path / "blobs"),
        semantic_resolver=fixture["resolver"],
        client=_MemoryClient(),
    )
    ref = await store.persist_revision_closure(
        bundle=fixture["bundle"],
        assignment_results=fixture["assignment_results"],
        evidence=fixture["evidence"],
        revision=fixture["revision"],
    )
    assert await store.resolve_revision(ref, requesting_scope=ref.scope) == fixture["revision"]
    restored = await store.restore_revision(ref, requesting_scope=ref.scope)
    publication = await store.promote_revision(
        scope=ref.scope,
        app_id=ref.app_id,
        expected_current_revision_ref=None,
        expected_generation=0,
        new_revision_ref=ref,
    )
    assert publication.outcome is PublicationOutcome.PROMOTED
    assert (
        await store.resolve_current_revision(scope=ref.scope, app_id=ref.app_id)
        == fixture["revision"]
    )

    app_root = tmp_path / "restored-app"
    for relative_path, content in restored.files().items():
        destination = app_root / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)
    assert set(load_app_page_schemas(app_root)) == {"home"}
    assert (
        restored.files()["modules/reports/backend/report_hook.py"]
        == b"def report_hook():\n    return None\n"
    )
