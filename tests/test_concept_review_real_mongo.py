"""Concept review compare-and-set behavior against a disposable Mongo database."""

from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import pytest_asyncio
from motor.motor_asyncio import AsyncIOMotorClient

from mozaiksai.core.data.persistence.artifact_store import BuilderArtifactStore


@pytest_asyncio.fixture
async def concepts(monkeypatch):
    database_name = f"concept_review_{uuid4().hex}"
    mongo = AsyncIOMotorClient(os.getenv("MONGO_URI", "mongodb://127.0.0.1:27017"), serverSelectionTimeoutMS=1500)
    try:
        await mongo.admin.command("ping")
    except Exception:
        mongo.close()
        if os.getenv("MOZAIKS_REQUIRE_REAL_MONGO"):
            pytest.fail("Real Mongo is required for concept review acceptance")
        pytest.skip("Mongo is unavailable")
    collection = mongo[database_name]["Concepts"]
    store = BuilderArtifactStore()
    monkeypatch.setattr(store, "_collection", AsyncMock(return_value=collection))
    try:
        yield SimpleNamespace(store=store, collection=collection)
    finally:
        assert database_name.startswith("concept_review_")
        await mongo.drop_database(database_name)
        mongo.close()


async def _draft(concepts, review_id="review-one"):
    await concepts.store.save_concept(app_id="target-app", concept_record={
        "app_id": "target-app", "review_id": review_id,
        "review_owner_user_id": "owner", "status": "draft",
    })


async def _finish(concepts, **overrides):
    return await concepts.store.finish_concept_review(**{
        "app_id": "target-app", "review_id": "review-one", "reviewed_by": "owner",
        "status": "approved", "feedback": "Approved as displayed", **overrides,
    })


@pytest.mark.parametrize("overrides", [
    {"reviewed_by": "other-user"}, {"app_id": "other-app"}, {"review_id": "other-review"},
])
async def test_review_cannot_cross_identity_boundaries(concepts, overrides):
    await _draft(concepts)
    assert await _finish(concepts, **overrides) is False
    assert (await concepts.store.get_concept(app_id="target-app"))["status"] == "draft"


async def test_replacement_draft_rejects_stale_review(concepts):
    await _draft(concepts)
    await _draft(concepts, "review-two")
    assert await _finish(concepts) is False
    assert await _finish(concepts, review_id="review-two") is True
    assert await _finish(concepts, review_id="review-two") is False


async def test_competing_reviews_can_only_finish_once(concepts):
    await _draft(concepts)
    results = await asyncio.gather(*(_finish(concepts) for _ in range(12)))
    assert results.count(True) == 1
    record = await concepts.store.get_concept(app_id="target-app")
    assert record["status"] == "approved"
    assert record["reviewed_by"] == "owner"


async def test_concurrent_proposals_have_only_one_current_draft(concepts):
    await asyncio.gather(*(_draft(concepts, f"review-{index}") for index in range(12)))
    assert await concepts.collection.count_documents({"app_id": "target-app"}) == 1
    current = await concepts.store.get_concept(app_id="target-app")
    for index in range(12):
        review_id = f"review-{index}"
        assert await _finish(concepts, review_id=review_id) is (review_id == current["review_id"])


@pytest.mark.parametrize("status", ["changes_requested", "cancelled"])
async def test_negative_decision_cannot_be_replayed_as_approval(concepts, status):
    await _draft(concepts)
    assert await _finish(concepts, status=status) is True
    assert await _finish(concepts) is False
    assert (await concepts.store.get_concept(app_id="target-app"))["status"] == status
