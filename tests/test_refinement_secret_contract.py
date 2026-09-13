from __future__ import annotations

import zipfile

import pytest
from fastapi import HTTPException

from mozaiksai.control_plane.artifact_promotion import _bundle_workspace
from mozaiksai.control_plane.contracts import is_secret_sensitive_path
from mozaiksai.control_plane.workspace import materialize_coding_workspace
from mozaiksai.core.secrets.contract import SecretContractError

_CONTRACT = "version: 1\nprovider:\n  type: env\nsecrets:\n  - env: SERVICE_API_KEY\n    required: true\n"


@pytest.mark.parametrize("path", ["security/secrets.yaml", "app/security/secrets.yaml"])
def test_names_only_contract_survives_refinement_packaging_and_restore(tmp_path, path):
    from mozaiksai.hosts.studio import _restore_bundle_to_target

    baseline = tmp_path / "baseline"
    staged = tmp_path / "staged"
    snapshot = tmp_path / "snapshot"
    materialize_coding_workspace({path: _CONTRACT, "app.json": "{}"}, workspace_root=baseline)
    materialize_coding_workspace({"ui/title.txt": "Updated"}, workspace_root=staged)
    assert not is_secret_sensitive_path(path)
    (baseline / ".env").write_text("SERVICE_API_KEY=not-for-export", encoding="utf-8")
    archive_path = tmp_path / "bundle.zip"
    _bundle_workspace(workspace_root=staged, baseline_root=baseline, snapshot_root=snapshot, bundle_path=archive_path)
    with zipfile.ZipFile(archive_path) as archive:
        assert archive.read(path).decode() == _CONTRACT
        assert ".env" not in archive.namelist()
    (baseline / path).write_text("changed later", encoding="utf-8")
    assert (snapshot / path).read_text(encoding="utf-8") == _CONTRACT
    target = tmp_path / "target"
    _restore_bundle_to_target(zip_path=archive_path, target_dir=target, workspace_layout=True)
    assert (target / "app/security/secrets.yaml").read_text(encoding="utf-8") == _CONTRACT


def test_credential_values_in_contract_fail_all_write_boundaries(tmp_path):
    from mozaiksai.hosts.studio import _restore_bundle_to_target

    content = "version: 1\nsecrets:\n  - env: SERVICE_API_KEY\n    value: not-for-export\n"
    path = "security/secrets.yaml"
    with pytest.raises(SecretContractError):
        materialize_coding_workspace({path: content}, workspace_root=tmp_path / "scoped")
    staged = tmp_path / "staged"
    (staged / "security").mkdir(parents=True)
    (staged / path).write_text(content, encoding="utf-8")
    with pytest.raises(SecretContractError):
        _bundle_workspace(workspace_root=staged, bundle_path=tmp_path / "blocked.zip")
    archive_path = tmp_path / "untrusted.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("app.json", "{}")
        archive.writestr(path, content)
    with pytest.raises(HTTPException) as caught:
        _restore_bundle_to_target(zip_path=archive_path, target_dir=tmp_path / "target", workspace_layout=True)
    assert caught.value.status_code == 400
    assert not list((tmp_path / "target").iterdir())
    assert "not-for-export" not in caught.value.detail
