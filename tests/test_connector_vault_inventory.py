"""Metadata-only migration inventory never copies or reveals credentials."""

from __future__ import annotations

import asyncio
import json
from argparse import Namespace

import pytest

from mozaiksai.core.secrets.connector_vault import _secret_name
from scripts.inventory_connector_vault import _run, build_inventory


def _owner(scope: str, scope_id: str, service: str, *, provider: str = "mongo") -> dict:
    return {
        "scope": scope,
        "scope_id": scope_id,
        "service": service,
        "secret_available": True,
        "secret_storage": provider,
        "secret_name": _secret_name(scope, scope_id, service),
    }


def test_legacy_collision_fails_closed_without_projecting_values() -> None:
    owners = [_owner("app", "same", "foo_bar"), _owner("workspace", "same", "foo_bar")]
    old_record = [{
        "scope_id": "same", "service": "foo-bar", "secret_name": "old-name",
        "encrypted_value": "must-never-enter-report", "secret_value": "must-never-enter-report",
    }]
    report = build_inventory(owners, old_record, provider="mongo")
    assert report["ready"] is False
    assert report["counts"]["legacy_ambiguous"] == 1
    assert len(report["findings"][0]["owner_refs"]) == 2
    assert "must-never-enter-report" not in json.dumps(report)
    assert "same" not in json.dumps(report)


def test_single_legacy_candidate_still_requires_operator_review() -> None:
    report = build_inventory(
        [_owner("app", "same", "foo_bar")],
        [{"scope_id": "same", "service": "foo-bar", "secret_name": "old-name"}],
        provider="mongo",
    )
    assert report["ready"] is False
    assert report["counts"]["legacy_review"] == 1


def test_qualified_scopes_and_service_aliases_are_independent() -> None:
    owners = [
        _owner("app", "same", "foo_bar"),
        _owner("workspace", "same", "foo_bar"),
        _owner("app", "same", "foo-bar"),
    ]
    vault = [
        {"scope": owner["scope"], "scope_id": owner["scope_id"], "service": owner["service"],
         "secret_name": owner["secret_name"]}
        for owner in owners
    ]
    report = build_inventory(owners, vault, provider="mongo")
    assert report["ready"] is True
    assert report["counts"]["qualified_ready"] == 3
    assert build_inventory(list(reversed(owners)), list(reversed(vault)), provider="mongo")["fingerprint"] == report["fingerprint"]


def test_missing_secret_and_fingerprint_drift_fail_closed() -> None:
    owner = _owner("app", "id", "service")
    report = build_inventory([owner], [], provider="mongo")
    assert report["counts"]["missing_qualified_secret"] == 1
    updated = build_inventory([owner | {"updated_at": "later"}], [], provider="mongo")
    assert updated["fingerprint"] != report["fingerprint"]
    changed_prefix = build_inventory([owner], [], provider="mongo", prefix="different-prefix")
    assert changed_prefix["fingerprint"] != report["fingerprint"]
    inconsistent = build_inventory([owner | {"secret_available": False, "status": "active"}], [], provider="mongo")
    assert inconsistent["counts"]["missing_qualified_secret"] == 1


def test_azure_legacy_name_matches_only_for_review() -> None:
    from scripts.inventory_connector_vault import _legacy_azure_name

    owner = _owner("app", "id", "foo_bar", provider="azure_key_vault")
    report = build_inventory(
        [owner],
        [{"scope_id": "id", "service": "foo-bar", "secret_name": _legacy_azure_name("id", "foo_bar", "mozaiks-connector")}],
        provider="azure_key_vault",
    )
    assert report["counts"]["legacy_review"] == 1
    assert report["ready"] is False


def test_azure_version_identity_conflict_blocks_qualified_record() -> None:
    owner = _owner("app", "id", "service", provider="azure_key_vault")
    report = build_inventory(
        [owner],
        [{"scope": "app", "scope_id": "id", "service": "service", "managed_by": "mozaiks", "secret_name": owner["secret_name"],
          "version_identity_conflict": True, "version_count": 2}],
        provider="azure_key_vault",
    )
    assert report["ready"] is False
    assert report["counts"]["version_identity_conflict"] == 1


def test_azure_wrong_owner_tag_blocks_inventory_readiness() -> None:
    owner = _owner("app", "id", "service", provider="azure_key_vault")
    report = build_inventory(
        [owner],
        [{"scope": "app", "scope_id": "id", "service": "service", "managed_by": "other",
          "secret_name": owner["secret_name"]}],
        provider="azure_key_vault",
    )
    assert report["ready"] is False
    assert report["counts"]["qualified_identity_mismatch"] == 1


def test_dry_run_requires_new_private_report_and_matching_fingerprint(monkeypatch, tmp_path) -> None:
    from scripts import inventory_connector_vault as inventory

    async def metadata(uri):
        assert uri == "mongodb://127.0.0.1:27189"
        return [_owner("app", "id", "service")], []

    monkeypatch.setenv("MONGO_URI", "mongodb://127.0.0.1:27189")
    monkeypatch.setattr(inventory, "_mongo_metadata", metadata)
    target = tmp_path / "report.json"
    args = Namespace(provider="mongo", report=str(target), prefix="mozaiks-connector", expect_fingerprint=None)
    assert asyncio.run(_run(args)) == 1
    report = json.loads(target.read_text(encoding="utf-8"))
    with pytest.raises(RuntimeError, match="already exists"):
        asyncio.run(_run(args))
    args.report = str(tmp_path / "next.json")
    args.expect_fingerprint = "stale-fingerprint"
    with pytest.raises(RuntimeError, match="changed"):
        asyncio.run(_run(args))
    assert not (tmp_path / "next.json").exists()
    assert report["counts"]["missing_qualified_secret"] == 1


def test_azure_inventory_only_lists_properties(monkeypatch) -> None:
    import sys
    from types import ModuleType, SimpleNamespace

    from scripts.inventory_connector_vault import _azure_metadata

    class Client:
        def list_properties_of_secrets(self):
            return [
                SimpleNamespace(name="other-secret", tags={}, created_on=None, expires_on=None),
                SimpleNamespace(
                    name="mozaiks-connector-app-id-digest",
                    tags={"managed_by": "mozaiks", "scope": "app", "scope_id": "id", "service": "service"},
                    created_on=None,
                    expires_on=None,
                ),
            ]

        def get_secret(self, name):
            raise AssertionError("inventory must not retrieve a secret")

        def list_properties_of_secret_versions(self, name):
            assert name == "mozaiks-connector-app-id-digest"
            return [SimpleNamespace(
                version="v1", created_on=None, expires_on=None,
                tags={"managed_by": "mozaiks", "scope": "app", "scope_id": "id", "service": "service"},
            )]

    azure = ModuleType("azure")
    azure.__path__ = []  # type: ignore[attr-defined]
    identity = ModuleType("azure.identity")
    identity.DefaultAzureCredential = lambda: object()  # type: ignore[attr-defined]
    keyvault = ModuleType("azure.keyvault")
    keyvault.__path__ = []  # type: ignore[attr-defined]
    secrets = ModuleType("azure.keyvault.secrets")
    secrets.SecretClient = lambda **kwargs: Client()  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "azure", azure)
    monkeypatch.setitem(sys.modules, "azure.identity", identity)
    monkeypatch.setitem(sys.modules, "azure.keyvault", keyvault)
    monkeypatch.setitem(sys.modules, "azure.keyvault.secrets", secrets)
    rows = _azure_metadata("test-vault", "mozaiks-connector")
    assert len(rows) == 1
    assert rows[0]["scope"] == "app"
    assert rows[0]["version_count"] == 1
    assert rows[0]["version_identity_conflict"] is False
    assert rows[0]["version_fingerprint"]
