from __future__ import annotations

import pytest

from mozaiksai.core.contract_identity import (
    ContractIdentityError,
    build_contract_identity,
    manifest_digest,
    verify_contract_identity,
)


def test_identity_is_stable_and_normalizes_line_endings(tmp_path) -> None:
    (tmp_path / "schema.yaml").write_bytes(b"name: example\r\n")
    identity = build_contract_identity(
        tmp_path, ["schema.yaml"], source_repo="example/core", source_commit="abc123"
    )
    (tmp_path / "schema.yaml").write_bytes(b"name: example\n")
    assert verify_contract_identity(identity, tmp_path, expected_source_commit="abc123") == identity


def test_identity_refuses_contract_drift(tmp_path) -> None:
    path = tmp_path / "schema.yaml"
    path.write_text("name: example\n", encoding="utf-8")
    identity = build_contract_identity(tmp_path, ["schema.yaml"], source_repo="example/core", source_commit="abc123")
    path.write_text("name: changed\n", encoding="utf-8")
    with pytest.raises(ContractIdentityError, match="do not match"):
        verify_contract_identity(identity, tmp_path)


def test_identity_refuses_source_commit_drift(tmp_path) -> None:
    (tmp_path / "schema.yaml").write_text("name: example\n", encoding="utf-8")
    identity = build_contract_identity(tmp_path, ["schema.yaml"], source_repo="example/core", source_commit="abc123")
    with pytest.raises(ContractIdentityError, match="source commit mismatch"):
        verify_contract_identity(identity, tmp_path, expected_source_commit="def456")


def test_manifest_digest_is_order_independent() -> None:
    first = {"schema_version": "mozaiks.contract_manifest.v1", "files": [{"path": "a", "sha256": "1"}, {"path": "b", "sha256": "2"}]}
    second = {"files": first["files"], "schema_version": first["schema_version"]}
    assert manifest_digest(first) == manifest_digest(second)
