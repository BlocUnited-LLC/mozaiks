from __future__ import annotations

import asyncio
import os
from pathlib import Path
from uuid import uuid4

import pytest
from motor.motor_asyncio import AsyncIOMotorClient

from mozaiksai.core.artifacts.content_store import LocalArtifactContentStore
from mozaiksai.core.artifacts.revision_store import (
    ArtifactRevisionStore,
    PublicationConflictError,
)
from mozaiksai.core.semantics.artifact_revision import build_artifact_revision
from mozaiksai.core.semantics.binding import build_implementation_binding
from mozaiksai.core.semantics.refs import ImplementationBindingRef
from tests.slice_5c_revision_helpers import revision_fixture

pytestmark = pytest.mark.skipif(
    os.environ.get("MOZAIKS_RUN_REAL_MONGO_TESTS") != "1",
    reason="set MOZAIKS_RUN_REAL_MONGO_TESTS=1 for real Mongo authority tests",
)


class _TestDatabaseClient:
    def __init__(self, client, database_name: str) -> None:
        self._client = client
        self._database_name = database_name

    def __getitem__(self, _name: str):
        return self._client[self._database_name]


def _alternative(fixture: dict[str, object]):
    graph = fixture["graph"]
    revision = fixture["revision"]
    binding = build_implementation_binding(
        binding_id="slice-5c-real-mongo-alternative",
        version=1,
        scope=graph.scope,
        semantic_graph_ref=revision.semantic_graph_ref,
        capability_pack_selections=(),
        renderer_selections=(),
        deployment_profile_selections=(),
    )
    fixture["resolver"].register_implementation_binding(binding)
    ref = ImplementationBindingRef(
        subject_id=binding.binding_id,
        subject_version=binding.version,
        content_digest=binding.binding_digest,
        scope=binding.scope,
    )
    alternative = build_artifact_revision(
        **{
            **revision.model_dump(
                mode="python",
                exclude={
                    "revision_schema_version",
                    "revision_digest",
                    "implementation_binding_ref",
                },
            ),
            "implementation_binding_ref": ref,
        }
    )
    return binding, alternative


@pytest.mark.asyncio
async def test_real_mongo_competing_genesis_candidates_have_one_current(
    tmp_path: Path,
) -> None:
    uri = os.environ.get("MONGO_URI", "mongodb://127.0.0.1:27017")
    database_name = f"mozaiks_slice_5c_test_{uuid4().hex}"
    first_client = AsyncIOMotorClient(uri)
    second_client = AsyncIOMotorClient(uri)
    first_fixture = revision_fixture()
    second_fixture = revision_fixture()
    binding, alternative = _alternative(first_fixture)
    second_fixture["resolver"].register_implementation_binding(binding)
    store_one = ArtifactRevisionStore(
        content_store=LocalArtifactContentStore(root=tmp_path),
        semantic_resolver=first_fixture["resolver"],
        client=_TestDatabaseClient(first_client, database_name),
    )
    store_two = ArtifactRevisionStore(
        content_store=LocalArtifactContentStore(root=tmp_path),
        semantic_resolver=second_fixture["resolver"],
        client=_TestDatabaseClient(second_client, database_name),
    )
    try:
        first_ref = await store_one.persist_revision_closure(
            bundle=first_fixture["bundle"],
            assignment_results=(),
            evidence=first_fixture["evidence"],
            revision=first_fixture["revision"],
        )
        second_ref = await store_one.persist_revision_closure(
            bundle=first_fixture["bundle"],
            assignment_results=(),
            evidence=first_fixture["evidence"],
            revision=alternative,
        )

        async def promote(store, ref):
            try:
                return await store.promote_revision(
                    scope=ref.scope,
                    app_id=ref.app_id,
                    expected_current_revision_ref=None,
                    expected_generation=0,
                    new_revision_ref=ref,
                )
            except PublicationConflictError as exc:
                return exc

        outcomes = await asyncio.gather(
            promote(store_one, first_ref), promote(store_two, second_ref)
        )
        assert sum(not isinstance(item, Exception) for item in outcomes) == 1
        assert sum(isinstance(item, PublicationConflictError) for item in outcomes) == 1
        publication = await store_two.get_publication(
            scope=first_ref.scope, app_id=first_ref.app_id
        )
        assert publication is not None
        assert publication.generation == 1
        assert publication.current_revision_ref in {first_ref, second_ref}
    finally:
        await first_client.drop_database(database_name)
        first_client.close()
        second_client.close()
