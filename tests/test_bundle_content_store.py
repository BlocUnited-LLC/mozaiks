"""
Tests for the pluggable BundleContentStore.

Coverage:
- ContentNotFoundError is a proper exception subclass
- LocalBundleContentStore: put/get/exists/delete/verify_checksum (7 tests)
- LocalBundleContentStore: checksum edge cases (3 tests)
- read_bundle_zip_bytes produces same result as read_bundle_zip (1 test)
- get_bundle_content_store factory: default, env var, unknown value (3 tests)
- get_bundle_content_store singleton behaviour (1 test)
- load_bundle_workspace third branch: content_ref used when workspace gone (1 test)
- load_bundle_workspace: content_store_unavailable when no store passed (1 test)
- load_bundle_workspace: content_ref_not_found on ContentNotFoundError (1 test)
- load_bundle_workspace: workspace_dir takes priority over content_ref (1 test)
- load_bundle_workspace: artifact_path takes priority over content_ref (1 test)
- load_bundle_workspace: content_store_error on unexpected exception (1 test)
- load_bundle_workspace: workspace_unavailable when nothing present (1 test)

Total: 22 tests — no real MongoDB required.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import zipfile
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mozaiksai.core.artifacts.content_store import (
    ContentIntegrityError,
    ContentNotFoundError,
    GridFSBundleContentStore,
    LocalBundleContentStore,
    get_bundle_content_store,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_zip_bytes(entries: dict[str, str]) -> bytes:
    """Build an in-memory zip from a filename→content dict."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in entries.items():
            zf.writestr(name, content)
    return buf.getvalue()


def _make_build_record(metadata: dict[str, Any]):
    """Return a MagicMock shaped like BuildRecord with given commit_metadata.metadata."""
    commit_meta = MagicMock()
    commit_meta.metadata = metadata
    record = MagicMock()
    record.id = "br_test_001"
    record.commit_metadata = commit_meta
    return record


def _run_async(awaitable):
    return asyncio.run(awaitable)


# ---------------------------------------------------------------------------
# TestContentNotFoundError
# ---------------------------------------------------------------------------


class TestContentNotFoundError:
    def test_is_exception_subclass(self):
        err = ContentNotFoundError("ref not found")
        assert isinstance(err, Exception)
        assert "ref not found" in str(err)


# ---------------------------------------------------------------------------
# TestLocalBundleContentStore
# ---------------------------------------------------------------------------


class TestLocalBundleContentStore:
    def test_put_returns_absolute_path_string(self, tmp_path):
        store = LocalBundleContentStore(root=tmp_path)
        data = _make_zip_bytes({"file.txt": "hello"})
        ref = _run_async(store.put_bundle(data, app_id="app1", build_record_id="br1"))
        assert isinstance(ref, str)
        assert Path(ref).is_absolute()
        assert Path(ref).exists()

    def test_get_returns_stored_bytes(self, tmp_path):
        store = LocalBundleContentStore(root=tmp_path)
        data = _make_zip_bytes({"a.txt": "content"})
        ref = _run_async(store.put_bundle(data, app_id="app1", build_record_id="br2"))
        retrieved = _run_async(store.get_bundle(ref))
        assert retrieved == data

    def test_exists_true_after_put(self, tmp_path):
        store = LocalBundleContentStore(root=tmp_path)
        data = _make_zip_bytes({"x.py": "pass"})
        ref = _run_async(store.put_bundle(data, app_id="app1", build_record_id="br3"))
        assert _run_async(store.exists(ref)) is True

    def test_exists_false_for_missing(self, tmp_path):
        store = LocalBundleContentStore(root=tmp_path)
        assert _run_async(store.exists(str(tmp_path / "does_not_exist.zip"))) is False

    def test_get_raises_content_not_found_for_missing(self, tmp_path):
        store = LocalBundleContentStore(root=tmp_path)
        with pytest.raises(ContentNotFoundError):
            _run_async(store.get_bundle(str(tmp_path / "ghost.zip")))

    def test_delete_removes_file_returns_true(self, tmp_path):
        store = LocalBundleContentStore(root=tmp_path)
        data = _make_zip_bytes({"d.txt": "bye"})
        ref = _run_async(store.put_bundle(data, app_id="app1", build_record_id="br4"))
        assert _run_async(store.delete(ref)) is True
        assert not Path(ref).exists()

    def test_delete_missing_returns_false(self, tmp_path):
        store = LocalBundleContentStore(root=tmp_path)
        assert _run_async(store.delete(str(tmp_path / "absent.zip"))) is False


# ---------------------------------------------------------------------------
# TestLocalChecksum
# ---------------------------------------------------------------------------


class TestLocalChecksum:
    def test_verify_checksum_correct(self, tmp_path):
        store = LocalBundleContentStore(root=tmp_path)
        data = _make_zip_bytes({"f.txt": "data"})
        sha = hashlib.sha256(data).hexdigest()
        ref = _run_async(store.put_bundle(data, app_id="app1", build_record_id="br5"))
        assert _run_async(store.verify_checksum(ref, sha)) is True

    def test_verify_checksum_wrong_hash_returns_false(self, tmp_path):
        store = LocalBundleContentStore(root=tmp_path)
        data = _make_zip_bytes({"f.txt": "data"})
        ref = _run_async(store.put_bundle(data, app_id="app1", build_record_id="br6"))
        assert _run_async(store.verify_checksum(ref, "a" * 64)) is False

    def test_verify_checksum_missing_ref_returns_false(self, tmp_path):
        store = LocalBundleContentStore(root=tmp_path)
        assert _run_async(store.verify_checksum(str(tmp_path / "missing.zip"), "a" * 64)) is False


class TestImmutableExactByteBlobs:
    def test_put_get_and_duplicate_are_content_addressed(self, tmp_path):
        store = LocalBundleContentStore(root=tmp_path)
        data = b"line one\r\nline two\r\n"
        digest = hashlib.sha256(data).hexdigest()
        assert _run_async(store.put_blob(data, expected_digest=digest)) == digest
        assert _run_async(store.put_blob(data, expected_digest=digest)) == digest
        assert _run_async(store.get_verified_blob(digest)) == data
        assert _run_async(store.has_verified_blob(digest)) is True

    def test_lf_and_crlf_remain_distinct_exact_content(self, tmp_path):
        store = LocalBundleContentStore(root=tmp_path)
        lf = b"value\n"
        crlf = b"value\r\n"
        lf_digest = hashlib.sha256(lf).hexdigest()
        crlf_digest = hashlib.sha256(crlf).hexdigest()
        assert lf_digest != crlf_digest
        _run_async(store.put_blob(lf, expected_digest=lf_digest))
        _run_async(store.put_blob(crlf, expected_digest=crlf_digest))
        assert _run_async(store.get_verified_blob(lf_digest)) == lf
        assert _run_async(store.get_verified_blob(crlf_digest)) == crlf

    def test_wrong_claim_and_modified_blob_fail_closed(self, tmp_path):
        store = LocalBundleContentStore(root=tmp_path)
        data = b"trusted"
        digest = hashlib.sha256(data).hexdigest()
        with pytest.raises(ContentIntegrityError, match="expected_digest"):
            _run_async(store.put_blob(data, expected_digest="f" * 64))

        _run_async(store.put_blob(data, expected_digest=digest))
        path = tmp_path / "sha256" / digest[:2] / digest
        path.write_bytes(b"modified at same immutable location")
        with pytest.raises(ContentIntegrityError, match="expected_digest"):
            _run_async(store.get_verified_blob(digest))

    def test_missing_blob_is_not_present(self, tmp_path):
        store = LocalBundleContentStore(root=tmp_path)
        digest = "a" * 64
        assert _run_async(store.has_verified_blob(digest)) is False
        with pytest.raises(ContentNotFoundError):
            _run_async(store.get_verified_blob(digest))

    @pytest.mark.parametrize("digest", ["A" * 64, "a" * 63, f" {'a' * 64}"])
    def test_malformed_digest_is_not_normalized(self, tmp_path, digest):
        store = LocalBundleContentStore(root=tmp_path)
        with pytest.raises(ContentIntegrityError, match="lowercase SHA-256"):
            _run_async(store.put_blob(b"content", expected_digest=digest))


# ---------------------------------------------------------------------------
# TestReadBundleZipBytes
# ---------------------------------------------------------------------------


class TestReadBundleZipBytes:
    def test_read_bytes_matches_read_path(self, tmp_path):
        from factory_app.refinement_harness.tools._bundle_workspace import (
            read_bundle_zip,
            read_bundle_zip_bytes,
        )

        entries = {"app/module.yaml": "id: mymod\n", "app/handler.py": "pass"}
        data = _make_zip_bytes(entries)
        zip_path = tmp_path / "bundle.zip"
        zip_path.write_bytes(data)

        from_path = read_bundle_zip(zip_path)
        from_bytes = read_bundle_zip_bytes(data)
        assert from_path == from_bytes

    def test_bundle_workspace_readers_skip_sensitive_and_dependency_paths(self, tmp_path):
        from factory_app.refinement_harness.tools._bundle_workspace import (
            read_bundle_zip,
            read_workspace_dir,
        )

        workspace = tmp_path / "workspace"
        (workspace / "app").mkdir(parents=True)
        (workspace / "node_modules" / "pkg").mkdir(parents=True)
        (workspace / "app" / "main.py").write_text(
            "def main():\n    return True\n", encoding="utf-8"
        )
        (workspace / ".env").write_text("API_KEY=raw", encoding="utf-8")
        (workspace / "node_modules" / "pkg" / "index.js").write_text(
            "module.exports = {}", encoding="utf-8"
        )

        file_map = read_workspace_dir(workspace)
        assert file_map == {"app/main.py": "def main():\n    return True\n"}

        zip_path = tmp_path / "bundle.zip"
        zip_path.write_bytes(
            _make_zip_bytes(
                {
                    "app/main.py": "def main():\n    return True\n",
                    ".env": "API_KEY=raw",
                    "node_modules/pkg/index.js": "module.exports = {}",
                }
            )
        )
        assert read_bundle_zip(zip_path) == {"main.py": "def main():\n    return True\n"}


# ---------------------------------------------------------------------------
# TestGetBundleContentStore
# ---------------------------------------------------------------------------


class TestGetBundleContentStore:
    def _reset_singleton(self):
        import mozaiksai.core.artifacts.content_store as cs_mod

        cs_mod._bundle_content_store = None

    def test_default_is_local_instance(self):
        self._reset_singleton()
        with patch.dict("os.environ", {}, clear=False):
            import os

            os.environ.pop("MOZAIKS_BUNDLE_CONTENT_BACKEND", None)
            store = get_bundle_content_store()
        assert isinstance(store, LocalBundleContentStore)
        assert store.backend_name == "local"
        self._reset_singleton()

    def test_gridfs_env_returns_gridfs_instance(self):
        self._reset_singleton()
        with patch.dict("os.environ", {"MOZAIKS_BUNDLE_CONTENT_BACKEND": "gridfs"}):
            store = get_bundle_content_store()
        assert isinstance(store, GridFSBundleContentStore)
        assert store.backend_name == "gridfs"
        self._reset_singleton()

    def test_unknown_backend_falls_back_to_local(self):
        self._reset_singleton()
        with patch.dict("os.environ", {"MOZAIKS_BUNDLE_CONTENT_BACKEND": "s3"}):
            store = get_bundle_content_store()
        assert isinstance(store, LocalBundleContentStore)
        self._reset_singleton()

    def test_singleton_returns_same_instance(self):
        self._reset_singleton()
        store1 = get_bundle_content_store()
        store2 = get_bundle_content_store()
        assert store1 is store2
        self._reset_singleton()


# ---------------------------------------------------------------------------
# TestBundleWorkspaceContentRef
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestBundleWorkspaceContentRef:
    """load_bundle_workspace third-branch: content_ref via BundleContentStore."""

    async def _call(self, metadata: dict, content_store=None, *, record_store=None):
        from factory_app.refinement_harness.tools._bundle_workspace import load_bundle_workspace

        if record_store is None:
            record = _make_build_record(metadata)
            mock_store = AsyncMock()
            mock_store.get_build_record = AsyncMock(return_value=record)
            record_store = mock_store

        return await load_bundle_workspace(
            record_store=record_store,
            app_id="app1",
            build_record_id="br_001",
            content_store=content_store,
        )

    async def test_content_ref_branch_loads_zip_when_workspace_unavailable(self, tmp_path):
        zip_data = _make_zip_bytes({"module.yaml": "id: test\n"})
        mock_cs = AsyncMock()
        mock_cs.get_bundle = AsyncMock(return_value=zip_data)

        result = await self._call(
            {"content_ref": "ref_abc", "content_backend": "gridfs"},
            content_store=mock_cs,
        )
        assert result["present"] is True
        assert result["source"] == "content_store:gridfs"
        assert "module.yaml" in result["file_map"]
        mock_cs.get_bundle.assert_awaited_once_with("ref_abc")

    async def test_content_ref_with_no_store_returns_content_store_unavailable(self):
        result = await self._call(
            {"content_ref": "ref_abc", "content_backend": "gridfs"},
            content_store=None,
        )
        assert result["present"] is False
        assert result["reason"] == "content_store_unavailable"
        assert result["content_ref"] == "ref_abc"

    async def test_content_ref_not_found_in_store_returns_not_present(self):
        mock_cs = AsyncMock()
        mock_cs.get_bundle = AsyncMock(side_effect=ContentNotFoundError("not found"))

        result = await self._call(
            {"content_ref": "ref_missing", "content_backend": "gridfs"},
            content_store=mock_cs,
        )
        assert result["present"] is False
        assert result["reason"] == "content_ref_not_found"

    async def test_workspace_dir_takes_priority_over_content_ref(self, tmp_path):
        ws = tmp_path / "ws"
        ws.mkdir()
        (ws / "hello.txt").write_text("world")

        mock_cs = AsyncMock()

        result = await self._call(
            {"workspace_dir": str(ws), "content_ref": "should_not_be_used"},
            content_store=mock_cs,
        )
        assert result["present"] is True
        assert result["source"] == "workspace_dir"
        mock_cs.get_bundle.assert_not_called()

    async def test_artifact_path_takes_priority_over_content_ref(self, tmp_path):
        zip_data = _make_zip_bytes({"main.py": "print('hi')"})
        zip_path = tmp_path / "bundle.zip"
        zip_path.write_bytes(zip_data)

        mock_cs = AsyncMock()

        result = await self._call(
            {"artifact_path": str(zip_path), "content_ref": "should_not_be_used"},
            content_store=mock_cs,
        )
        assert result["present"] is True
        assert result["source"] == "artifact_zip"
        mock_cs.get_bundle.assert_not_called()

    async def test_content_store_error_returns_not_present(self):
        mock_cs = AsyncMock()
        mock_cs.get_bundle = AsyncMock(side_effect=RuntimeError("connection refused"))

        result = await self._call(
            {"content_ref": "ref_broken", "content_backend": "gridfs"},
            content_store=mock_cs,
        )
        assert result["present"] is False
        assert result["reason"] == "content_store_error"
        assert "connection refused" in result["error"]

    async def test_all_paths_missing_returns_workspace_unavailable(self):
        result = await self._call({})
        assert result["present"] is False
        assert result["reason"] == "workspace_unavailable"
