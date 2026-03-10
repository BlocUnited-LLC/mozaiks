from __future__ import annotations

from typing import Optional

from .config import load_workflow_pack_graph
from .schema import WorkflowPackGraph


def get_workflow_pack_graph(workflow_name: str) -> Optional[WorkflowPackGraph]:
    return load_workflow_pack_graph(workflow_name)


def workflow_has_mid_flight_journeys(workflow_name: str) -> bool:
    graph = load_workflow_pack_graph(workflow_name)
    if graph is None:
        return False
    return bool(graph.mid_flight_journeys)


__all__ = ["get_workflow_pack_graph", "workflow_has_mid_flight_journeys"]

