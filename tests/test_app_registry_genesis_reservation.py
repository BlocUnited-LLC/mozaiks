"""One Factory target can reserve one exact imported Genesis source."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from unittest.mock import AsyncMock

import pytest

from factory_app.app.modules.app_registry.backend.repo import AppRegistryRepo
from factory_app.app.modules.app_registry.backend.schemas import GenesisImportClaim
from factory_app.app.modules.app_registry.backend.service import AppRegistryService


class _Collection:
    def __init__(self) -> None:
        self.doc = {
            "_id": "appreg_1", "owner_user_id": "owner", "app_id": "existing-app",
            "chat_app_id": "existing-app", "lifecycle_state": "draft",
            "updated_at": "before",
        }
        self.lock = asyncio.Lock()

    def _matches(self, query):  # noqa: ANN001
        for key, expected in query.items():
            value = self.doc
            for part in key.split("."):
                value = value.get(part) if isinstance(value, dict) else None
            if isinstance(expected, dict) and "$exists" in expected:
                if (value is not None) != expected["$exists"]:
                    return False
            elif isinstance(expected, dict) and "$ne" in expected:
                if value == expected["$ne"]:
                    return False
            elif value != expected:
                return False
        return True

    async def find_one_and_update(self, query, update, **_kwargs):  # noqa: ANN001, ANN003
        async with self.lock:
            if not self._matches(query):
                return None
            self.doc.update(deepcopy(update["$set"]))
            return deepcopy(self.doc)

    async def find_one(self, query):  # noqa: ANN001
        return deepcopy(self.doc) if self._matches(query) else None


def _claim(*, bundle_sha256: str = "a" * 64) -> GenesisImportClaim:
    return GenesisImportClaim(
        build_record_id="av_" + "1" * 24,
        bundle_name="ExistingApp", bundle_sha256=bundle_sha256,
        manifest_sha256="b" * 64, content_backend="local",
        source_id="managed/existing", revision_id="c" * 40, tree_id="d" * 40,
    )


@pytest.fixture
def registry(monkeypatch):
    collection = _Collection()
    repo = AppRegistryRepo.__new__(AppRegistryRepo)
    monkeypatch.setattr(repo, "ensure_indexes", AsyncMock())
    monkeypatch.setattr(repo, "_collection", AsyncMock(return_value=collection))
    return AppRegistryService(repo), collection


async def _reserve(service: AppRegistryService, claim: GenesisImportClaim, *, owner: str = "owner"):
    return await service.reserve_genesis_import(
        build_registry_id="appreg_1", owner_user_id=owner,
        app_id="existing-app", chat_app_id="existing-app", claim=claim,
    )


@pytest.mark.asyncio
async def test_competing_sources_have_one_atomic_claim_and_exact_retry(registry):
    service, collection = registry
    first, changed = _claim(), _claim(bundle_sha256="f" * 64)
    outcomes = await asyncio.gather(_reserve(service, first), _reserve(service, changed))
    assert sum(outcome is not None for outcome in outcomes) == 1
    winner = first if outcomes[0] is not None else changed
    loser = changed if winner is first else first
    assert collection.doc["genesis_import"] == winner.model_dump(mode="json")
    assert (await _reserve(service, winner))["genesis_import"] == winner.model_dump(mode="json")
    assert await _reserve(service, loser) is None
    assert await _reserve(service, winner, owner="foreign") is None
    assert collection.doc["lifecycle_state"] == "draft"
    assert collection.doc.get("current_build_run") is None


@pytest.mark.asyncio
async def test_existing_build_or_chat_blocks_reservation(registry):
    service, collection = registry
    for change in ({"current_build_run": {"build_id": "build_1"}},
                   {"active_chat_id": "chat_1"}, {"lifecycle_state": "building"},
                   {"artifact_version_id": "av_current"},
                   {"bundle_path": "generated/apps/old/app"}):
        collection.doc.update(change)
        assert await _reserve(service, _claim()) is None
        for key in change:
            collection.doc.pop(key)
    assert "genesis_import" not in collection.doc


@pytest.mark.asyncio
async def test_reserved_source_blocks_generic_build_and_refinement_mutations(registry):
    service, collection = registry
    assert await _reserve(service, _claim()) is not None
    assert await service.repo.update_lifecycle_state(
        build_registry_id="appreg_1", owner_user_id="owner", lifecycle_state="building",
        expected_lifecycle_state="draft",
        current_build_run={"build_id": "build_other", "phase": "genesis"},
    ) is None
    with pytest.raises(ValueError, match="reserved Genesis import"):
        await service.resolve_build_binding(
            owner_user_id="owner", app_id="existing-app", chat_id="chat_new",
            workflow_name="ValueEngine", build_registry_id="appreg_1", allow_create=True,
        )
    with pytest.raises(ValueError, match="unaccepted Genesis import"):
        await service.begin_refinement_run(
            owner_user_id="owner", app_id="existing-app", build_registry_id="appreg_1",
            workflow_name="AppGenerator", chat_id="chat_refine",
        )
    assert collection.doc["lifecycle_state"] == "draft"
    assert collection.doc.get("current_build_run") is None
