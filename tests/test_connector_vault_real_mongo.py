"""Opt-in connector vault isolation against a lane-owned Mongo instance."""

from __future__ import annotations

import asyncio
import os

import pytest
from motor.motor_asyncio import AsyncIOMotorClient

from mozaiksai.core.data.persistence.namespaces import SYSTEM_DATABASE, PlatformCollections
from mozaiksai.core.secrets.connector_vault import MongoConnectorVaultBackend
from scripts.inventory_connector_vault import _mongo_metadata, build_inventory


def _lane_uri() -> str:
    uri = os.getenv("MONGO_URI", "")
    if os.getenv("MOZAIKS_RUN_REAL_MONGO_TESTS") != "1" or not uri.startswith("mongodb://127.0.0.1:27189"):
        pytest.skip("requires lane-owned Mongo on 127.0.0.1:27189")
    return uri


def test_real_mongo_scopes_and_service_aliases_never_share_a_secret() -> None:
    async def exercise():
        client = AsyncIOMotorClient(_lane_uri(), serverSelectionTimeoutMS=5_000)
        database = client[SYSTEM_DATABASE]
        collection = database[PlatformCollections.CONNECTOR_SECRETS]
        await collection.delete_many({})
        backend = MongoConnectorVaultBackend()

        async def own_collection():
            return collection

        backend._collection = own_collection  # type: ignore[method-assign]
        try:
            for scope, service, value in (
                ("app", "foo_bar", "app-value"),
                ("workspace", "foo_bar", "workspace-value"),
                ("app", "foo-bar", "alias-value"),
            ):
                assert (await backend.store_secret(
                    scope=scope, scope_id="same", service=service, secret_value=value
                ))["success"]
            assert await collection.count_documents({}) == 3
            indexes = await collection.index_information()
            assert indexes["connector_secret_identity_unique"]["unique"]
            assert (await backend.get_secret(scope="app", scope_id="same", service="foo_bar"))["secret_value"] == "app-value"
            assert (await backend.get_secret(scope="workspace", scope_id="same", service="foo_bar"))["secret_value"] == "workspace-value"
            assert (await backend.get_secret(scope="app", scope_id="same", service="foo-bar"))["secret_value"] == "alias-value"
            app_filter = {"scope": "app", "scope_id": "same", "service": "foo_bar"}
            app_name = (await collection.find_one(app_filter))["secret_name"]
            await collection.update_one(app_filter, {"$set": {"secret_name": "wrong-owner-name"}})
            mismatched = await backend.get_secret(scope="app", scope_id="same", service="foo_bar")
            assert mismatched["status"] == "error"
            assert mismatched["secret_value"] is None
            await collection.update_one(app_filter, {"$set": {"secret_name": app_name}})
            raced = await asyncio.gather(*(
                backend.store_secret(scope="app", scope_id="same", service="foo_bar", secret_value=f"race-{number}")
                for number in range(6)
            ))
            assert all(result["success"] for result in raced)
            assert await collection.count_documents({"scope": "app", "scope_id": "same", "service": "foo_bar"}) == 1
            assert (await backend.store_secret(
                scope="app", scope_id="same", service="foo_bar", secret_value="updated-app-value"
            ))["success"]
            assert (await backend.delete_secret(scope="app", scope_id="same", service="foo_bar"))["success"]
            assert (await backend.get_secret(scope="app", scope_id="same", service="foo_bar"))["success"] is False
            assert (await backend.get_secret(scope="workspace", scope_id="same", service="foo_bar"))["secret_value"] == "workspace-value"
            assert (await backend.get_secret(scope="app", scope_id="same", service="foo-bar"))["secret_value"] == "alias-value"
        finally:
            await collection.delete_many({})
            client.close()

    asyncio.run(exercise())


def test_real_mongo_legacy_record_is_unreadable_and_inventory_is_metadata_only() -> None:
    async def exercise():
        client = AsyncIOMotorClient(_lane_uri(), serverSelectionTimeoutMS=5_000)
        database = client[SYSTEM_DATABASE]
        metadata = database[PlatformCollections.CONNECTORS]
        collection = database[PlatformCollections.CONNECTOR_SECRETS]
        await metadata.delete_many({})
        await collection.delete_many({})
        backend = MongoConnectorVaultBackend()

        async def own_collection():
            return collection

        backend._collection = own_collection  # type: ignore[method-assign]
        try:
            await metadata.insert_many([
                {"scope": "app", "scope_id": "same", "service": "foo_bar", "secret_available": True, "secret_storage": "mongo", "secret_name": "old"},
                {"scope": "workspace", "scope_id": "same", "service": "foo_bar", "secret_available": True, "secret_storage": "mongo", "secret_name": "old"},
            ])
            await collection.insert_one({
                "scope_id": "same", "service": "foo-bar", "secret_name": "old",
                "encrypted_value": backend._encrypt("credential-value"),  # noqa: SLF001
            })
            assert (await backend.get_secret(scope="app", scope_id="same", service="foo_bar"))["success"] is False
            assert (await backend.get_secret(scope="workspace", scope_id="same", service="foo_bar"))["success"] is False
            assert (await backend.delete_secret(scope="app", scope_id="same", service="foo_bar"))["success"] is False
            before = await collection.count_documents({})
            connector_rows, secret_rows = await _mongo_metadata(_lane_uri())
            assert all("encrypted_value" not in row for row in secret_rows)
            report = build_inventory(connector_rows, secret_rows, provider="mongo")
            assert report["counts"]["legacy_ambiguous"] == 1
            assert report["ready"] is False
            assert await collection.count_documents({}) == before
        finally:
            await metadata.delete_many({})
            await collection.delete_many({})
            client.close()

    asyncio.run(exercise())
