"""App-owned user journeys and deterministic selection for a build's context graph."""

from __future__ import annotations

import re
from collections.abc import Collection
from pathlib import Path
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from .health import ContextGraphHealthReport
from .impact_analysis import analyze_impact, build_graph_snapshot_identity
from .models import (
    AppContextGraph,
    AppContextStaleStatus,
    GraphEdgeType,
    ImpactDirection,
    ImpactQuery,
)

JourneyId = Annotated[
    str, StringConstraints(min_length=1, max_length=80, pattern=r"^[a-z][a-z0-9_-]*$")
]
Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2048)]
NodeId = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=512)]
_MAX_CATALOG_BYTES = 4 * 1024 * 1024


class AppJourney(BaseModel):
    """One user goal and the source-backed app features it exercises."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    journey_id: JourneyId
    task: Text
    starting_state: Text
    success_criterion: Text
    start_route: str | None = Field(default=None, max_length=1024)
    graph_node_ids: tuple[NodeId, ...] = Field(default=(), max_length=64)

    @field_validator("start_route")
    @classmethod
    def _app_relative_route(cls, path: str | None) -> str | None:
        if path is not None and (
            not re.fullmatch(r"/(?:[A-Za-z0-9._~:@-]+(?:/[A-Za-z0-9._~:@-]+)*/?)?", path)
            or any(segment in {".", ".."} for segment in path.split("/"))
        ):
            raise ValueError("start_route must be an app-relative route path")
        return path

    @field_validator("graph_node_ids")
    @classmethod
    def _unique_node_ids(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if len(values) != len(set(values)):
            raise ValueError("Duplicate journey graph_node_ids")
        return values


class AppJourneyCatalog(BaseModel):
    """Portable declaration for journeys tested against a single app."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["mozaiks.app_journeys.v1"] = "mozaiks.app_journeys.v1"
    app_id: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=256)]
    journeys: tuple[AppJourney, ...] = Field(min_length=1, max_length=512)

    @model_validator(mode="after")
    def _unique_journeys(self) -> AppJourneyCatalog:
        if len({journey.journey_id for journey in self.journeys}) != len(self.journeys):
            raise ValueError("Duplicate journey_id in app journey catalog")
        return self


class AppJourneySelection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    journey_ids: tuple[str, ...]
    reason: Literal["baseline", "graph_unavailable", "graph_incomplete", "impact"]
    graph_digest: str | None = None


def load_app_journeys(app_root: Path) -> AppJourneyCatalog | None:
    """Read the optional config/journeys.json under the supplied app directory."""
    root = app_root.resolve()
    path = (root / "config" / "journeys.json").resolve()
    if not path.is_relative_to(root):
        raise ValueError("Journey catalog must reside inside the app directory")
    try:
        with path.open("rb") as stream:
            content = stream.read(_MAX_CATALOG_BYTES + 1)
    except FileNotFoundError:
        return None
    if len(content) > _MAX_CATALOG_BYTES:
        raise ValueError("Journey catalog exceeds the 4 MiB limit")
    return AppJourneyCatalog.model_validate_json(content)


def select_affected_journeys(
    catalog: AppJourneyCatalog,
    *,
    graph: AppContextGraph | None = None,
    changed_node_ids: Collection[str] | None = None,
    graph_health: ContextGraphHealthReport | None = None,
) -> AppJourneySelection:
    """Prioritize declared journeys for trusted, build-matched graph changes.

    Unknown or incomplete coverage selects the baseline. All returned ids name
    work to execute. An empty exact selection means no declared coverage matched.
    """
    all_ids = tuple(journey.journey_id for journey in catalog.journeys)
    if graph is not None and graph.app_id != catalog.app_id:
        raise ValueError("Journey catalog and graph belong to different apps")
    if isinstance(changed_node_ids, (str, bytes)):
        raise ValueError("changed_node_ids must be a collection of graph node identifiers")
    identity = build_graph_snapshot_identity(graph) if graph is not None else None
    digest = identity.identity_digest if identity is not None else None
    if not changed_node_ids:
        return AppJourneySelection(journey_ids=all_ids, reason="baseline", graph_digest=digest)
    if graph is None or identity is None:
        return AppJourneySelection(journey_ids=all_ids, reason="graph_unavailable")

    baseline = AppJourneySelection(
        journey_ids=all_ids, reason="graph_incomplete", graph_digest=digest
    )
    if graph_health is not None and graph_health.status != "healthy":
        return baseline
    if any(node.stale_status != AppContextStaleStatus.CURRENT for node in graph.nodes):
        return baseline
    if any(edge.stale_status != AppContextStaleStatus.CURRENT for edge in graph.edges):
        return baseline
    known_ids = {node.node_id for node in graph.nodes}
    seeds = set(changed_node_ids)
    if graph.stale_status != AppContextStaleStatus.CURRENT or not seeds.issubset(known_ids):
        return baseline
    if any(not known_ids.issuperset(journey.graph_node_ids) for journey in catalog.journeys):
        return baseline

    query = ImpactQuery(
        snapshot_digest=identity.identity_digest,
        seed_node_ids=sorted(seeds),
        allowed_edge_types=list(GraphEdgeType),
        direction=ImpactDirection.INBOUND,
        max_depth=8,
        max_results=500,
    )
    try:
        report = analyze_impact(graph=graph, snapshot_identity=identity, query=query)
    except ValueError:
        # Symbol seeds require authoritative parser evidence before traversal.
        return baseline
    if (
        report.truncated
        or report.parser_degradation
        or report.unresolved_relationships
        or report.ambiguity_risks
        or any(item.distance >= query.max_depth for item in report.evidence)
    ):
        return baseline
    affected = set(report.affected_node_ids)
    return AppJourneySelection(
        journey_ids=tuple(
            journey.journey_id
            for journey in catalog.journeys
            if not journey.graph_node_ids or not affected.isdisjoint(journey.graph_node_ids)
        ),
        reason="impact",
        graph_digest=digest,
    )
