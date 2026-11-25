import hashlib
import zipfile
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import yaml

from mozaiksai.core.artifacts import BuildRecord
from tests import test_studio_app_ownership

dashboard_portal = test_studio_app_ownership.dashboard_portal


@pytest.fixture
def saved_billing_build(dashboard_portal, monkeypatch, tmp_path):
    from mozaiksai.hosts import studio

    portal = dashboard_portal
    run = {"artifact_version_id": "version-one", "build_id": "build-one", "phase": "genesis"}
    portal.record["current_build_run"] = run
    archive = tmp_path / "billing.zip"

    def save(catalog, *, app_prefix="", duplicate_catalog=False):
        with zipfile.ZipFile(archive, "w") as output:
            output.writestr(f"bundle/{app_prefix}app.json", '{"appId":"notes"}')
            if catalog is not None:
                output.writestr(f"bundle/{app_prefix}config/subscriptions.yaml", yaml.safe_dump(catalog))
                if duplicate_catalog:
                    output.writestr("bundle/app/config/subscriptions.yaml", yaml.safe_dump(catalog))
        version = BuildRecord(
            id="version-one", app_id="notes", build_family="app_bundle", build_key="app_bundle",
            version_number=1, lifecycle_status="current", lineage_root_id="version-one",
            validation_status="passed", app_validation_status="passed",
            files_manifest=[{"path": "bundle/bundle.zip", "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
                             "content_type": "application/zip"}],
            commit_metadata={"metadata": {
                "bundle_name": "bundle", "artifact_path": str(archive), "build_registry_id": "reg-notes",
                "target_app_id": "notes", "build_id": "build-one", "phase": "genesis",
            }},
        )
        store = SimpleNamespace(get_build_record=AsyncMock(return_value=version))
        monkeypatch.setattr(studio, "get_artifact_store", lambda: store)
        return version, store

    return portal, save


@pytest.mark.parametrize("multi_product", [False, True])
@pytest.mark.parametrize("app_prefix", ["", "app/"])
def test_owner_reads_only_selected_build_plan_catalog(saved_billing_build, multi_product, app_prefix):
    portal, save = saved_billing_build
    product = {
        "label": "Notes subscriptions", "default_plan_id": "starter",
        "plans": [
            {"plan_id": "starter", "label": "Starter", "capabilities": ["notes.read"]},
            {"plan_id": "pro", "label": "Pro", "price": {
                "amount_cents": 5100, "currency": "eur", "interval": "year", "display": "€51 per year",
            }},
        ],
    }
    if multi_product:
        catalog = {"schema_version": "mozaiks.subscriptions.v2", "label": "Catalog",
                   "products": [{"product_id": "notes", **product}]}
    else:
        catalog = {"schema_version": "mozaiks.subscriptions.v1", **product}
    _, store = save(catalog, app_prefix=app_prefix)
    url = "/api/studio/apps/hosted-notes/billing"
    response = portal.client.get(url)
    assert response.status_code == 200, response.text
    assert response.json() == {
        "status": "configured", "dashboard_app_id": "hosted-notes", "app_name": "Notes",
        "artifact_version_id": "version-one",
        "products": [{
            "product_id": "notes" if multi_product else None, "label": "Notes subscriptions",
            "default_plan_id": "starter", "plans": [
                {"plan_id": "starter", "label": "Starter", "description": None, "price": None},
                {"plan_id": "pro", "label": "Pro", "description": None,
                 "price": {"amount_cents": 5100, "currency": "eur", "interval": "year", "display": "€51 per year"}},
            ],
        }],
    }
    assert portal.client.get("/api/studio/apps/notes/billing").status_code == 404
    portal.state["user"] = "bob"
    assert portal.client.get(url).status_code == 404
    store.get_build_record.assert_awaited_once_with(app_id="notes", build_record_id="version-one")


def test_billing_distinguishes_missing_build_and_missing_catalog(saved_billing_build):
    portal, save = saved_billing_build
    version, _ = save(None)
    url = "/api/studio/apps/hosted-notes/billing"
    assert portal.client.get(url).json()["status"] == "no_catalog"
    version.app_validation_status = "failed"
    assert portal.client.get(url).json()["status"] == "validation_required"
    portal.record["current_build_run"] = None
    assert portal.client.get(url).json()["status"] == "no_build"


def test_invalid_catalog_and_mismatched_build_fail_closed(saved_billing_build):
    portal, save = saved_billing_build
    version, _ = save({"private_value": "never-return-this"})
    url = "/api/studio/apps/hosted-notes/billing"
    invalid = portal.client.get(url)
    assert invalid.status_code == 422
    assert "private_value" not in invalid.text and "never-return-this" not in invalid.text
    version.commit_metadata.metadata["build_id"] = "another-run"
    assert portal.client.get(url).status_code == 409


def test_billing_rejects_duplicate_catalog_paths(saved_billing_build):
    portal, save = saved_billing_build
    save({}, duplicate_catalog=True)
    response = portal.client.get("/api/studio/apps/hosted-notes/billing")
    assert response.status_code == 409
    assert "conflicting subscription catalogs" in response.json()["detail"]


@pytest.mark.parametrize("invalid_name", [None, "../invalid"])
def test_billing_requires_canonical_bundle_metadata(saved_billing_build, invalid_name):
    portal, save = saved_billing_build
    version, _ = save(None)
    version.commit_metadata.metadata["bundle_name"] = invalid_name
    response = portal.client.get("/api/studio/apps/hosted-notes/billing")
    assert response.status_code == 409
    assert response.json()["detail"] == "The selected build archive could not be verified."
