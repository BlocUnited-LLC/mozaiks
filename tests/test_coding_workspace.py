"""Tests for the coding workspace materializer and diff harvester.

These pin the enforcement layer the coding lane relies on: materialization
refuses unsafe and secret-sensitive paths loudly, and harvest reports every
post-run change from the real tree — symlinks, out-of-manifest files, and
deletions become scope violations instead of accepted changes.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from mozaiksai.control_plane.workspace import (
    harvest_coding_workspace,
    materialize_coding_workspace,
)

_FILES = {
    "app/ui/pages/Dashboard.jsx": "export default function Dashboard() {}\n",
    "app/modules/demo/backend/handler.py": "class Handler:\n    pass\n",
}


def _workspace(tmp_path: Path):
    return materialize_coding_workspace(dict(_FILES), workspace_root=tmp_path / "ws")


# ---------------------------------------------------------------------------
# Materialization
# ---------------------------------------------------------------------------


def test_materialize_writes_files_and_records_hashes(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)

    assert sorted(workspace.editable_manifest) == sorted(_FILES)
    for rel, content in _FILES.items():
        on_disk = (workspace.workspace_root / rel).read_text(encoding="utf-8")
        assert on_disk == content
    # hashes are content hashes: same content, same hash
    duplicate = materialize_coding_workspace(dict(_FILES), workspace_root=tmp_path / "ws2")
    assert duplicate.editable_manifest == workspace.editable_manifest


@pytest.mark.parametrize(
    "bad_path",
    ["../outside.py", "/etc/passwd", "C:/windows/system32/x", "app/../../up.py", ""],
)
def test_materialize_rejects_unsafe_paths(tmp_path: Path, bad_path: str) -> None:
    with pytest.raises(ValueError, match="WORKSPACE_UNSAFE_PATH"):
        materialize_coding_workspace({bad_path: "x"}, workspace_root=tmp_path / "ws")


@pytest.mark.parametrize(
    "secret_path",
    [".env", "config/secrets.yaml", "keys/id_rsa", "certs/server.pem"],
)
def test_materialize_rejects_secret_paths(tmp_path: Path, secret_path: str) -> None:
    with pytest.raises(ValueError, match="WORKSPACE_SECRET_PATH"):
        materialize_coding_workspace({secret_path: "x"}, workspace_root=tmp_path / "ws")


def test_cleanup_removes_tree_and_is_idempotent(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    assert workspace.workspace_root.exists()
    workspace.cleanup()
    assert not workspace.workspace_root.exists()
    workspace.cleanup()  # second call must not raise


# ---------------------------------------------------------------------------
# Harvest
# ---------------------------------------------------------------------------


def test_harvest_reports_modified_and_unmodified_files(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    target = workspace.workspace_root / "app/ui/pages/Dashboard.jsx"
    target.write_text("export default function Dashboard() { return 1; }\n", encoding="utf-8")

    harvest = harvest_coding_workspace(workspace)

    assert harvest.clean
    by_path = {f.path: f for f in harvest.files}
    assert by_path["app/ui/pages/Dashboard.jsx"].modified is True
    assert by_path["app/ui/pages/Dashboard.jsx"].op == "update"
    assert by_path["app/ui/pages/Dashboard.jsx"].previous_sha256 != by_path["app/ui/pages/Dashboard.jsx"].new_sha256
    assert by_path["app/modules/demo/backend/handler.py"].modified is False
    assert by_path["app/ui/pages/Dashboard.jsx"].content is not None
    assert "return 1" in by_path["app/ui/pages/Dashboard.jsx"].content


def test_harvest_flags_new_file_as_violation_by_default(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    rogue = workspace.workspace_root / "app/rogue.py"
    rogue.parent.mkdir(parents=True, exist_ok=True)
    rogue.write_text("print('x')\n", encoding="utf-8")

    harvest = harvest_coding_workspace(workspace)

    assert not harvest.clean
    assert [v.kind for v in harvest.violations] == ["outside_allowlist"]
    assert harvest.violations[0].path == "app/rogue.py"
    assert all(f.path != "app/rogue.py" for f in harvest.files)


def test_harvest_accepts_new_file_when_allowed(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    (workspace.workspace_root / "app/new_module.py").write_text("x = 1\n", encoding="utf-8")

    harvest = harvest_coding_workspace(workspace, allow_new_files=True)

    assert harvest.clean
    created = [f for f in harvest.files if f.op == "create"]
    assert [f.path for f in created] == ["app/new_module.py"]
    assert created[0].previous_sha256 is None
    assert created[0].modified is True


def test_harvest_flags_deletion_as_violation_by_default(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    (workspace.workspace_root / "app/ui/pages/Dashboard.jsx").unlink()

    harvest = harvest_coding_workspace(workspace)

    assert not harvest.clean
    assert [(v.path, v.kind) for v in harvest.violations] == [
        ("app/ui/pages/Dashboard.jsx", "delete_denied")
    ]


def test_harvest_reports_deletion_when_allowed(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    (workspace.workspace_root / "app/ui/pages/Dashboard.jsx").unlink()

    harvest = harvest_coding_workspace(workspace, allow_deletes=True)

    assert harvest.clean
    deleted = [f for f in harvest.files if f.op == "delete"]
    assert [f.path for f in deleted] == ["app/ui/pages/Dashboard.jsx"]
    assert deleted[0].new_sha256 is None
    assert deleted[0].content is None


def test_harvest_flags_symlink_and_never_follows_it(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    outside = tmp_path / "outside-secret.txt"
    outside.write_text("host secret", encoding="utf-8")
    link = workspace.workspace_root / "app/link.txt"
    try:
        os.symlink(outside, link)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation not permitted on this host")

    harvest = harvest_coding_workspace(workspace)

    assert not harvest.clean
    assert [(v.path, v.kind) for v in harvest.violations] == [("app/link.txt", "symlink")]
    # the linked content must never be read into the harvest
    assert all(f.content is None or "host secret" not in f.content for f in harvest.files)


def test_harvest_of_untouched_workspace_is_clean_and_unmodified(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)

    harvest = harvest_coding_workspace(workspace)

    assert harvest.clean
    assert len(harvest.files) == len(_FILES)
    assert all(f.op == "update" and f.modified is False for f in harvest.files)
    assert harvest.total_content_bytes == sum(len(c.encode("utf-8")) for c in _FILES.values())
