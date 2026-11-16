"""Bounded model inputs and advisory output for a verified build graph."""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from mozaiksai.core.app_context.models import GraphEdgeType

MAX_GRAPH_PROMPT_BYTES = 16_384
GraphPath = Annotated[str, StringConstraints(min_length=1, max_length=384)]
Identity = Annotated[str, StringConstraints(min_length=1, max_length=256)]
Digest = Annotated[str, StringConstraints(pattern=r"^sha256:[a-f0-9]{64}$")]


class DependencyGraphAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    recommended_paths: list[GraphPath] = Field(max_length=12)
    test_paths: list[GraphPath] = Field(max_length=8)


class GraphDependency(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_path: GraphPath
    target_path: GraphPath
    kind: GraphEdgeType


class DependencyGraphContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    app_id: Identity
    build_record_id: Identity
    build_family: Identity
    build_key: Identity
    bundle_digest: Digest
    graph_hash: Digest
    files: tuple[GraphPath, ...] = Field(max_length=24)
    dependencies: tuple[GraphDependency, ...] = Field(max_length=40)
    coverage_incomplete: bool
    recommended_paths: tuple[GraphPath, ...] = Field(default=(), max_length=12)
    test_paths: tuple[GraphPath, ...] = Field(default=(), max_length=8)
