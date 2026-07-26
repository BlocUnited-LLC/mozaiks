from __future__ import annotations

from typing import Any

from mozaiksai.control_plane.contracts import ControlPlaneToolContext
from mozaiksai.control_plane.loader import load_selected_refinement_harness
from mozaiksai.control_plane.revision_context import assemble_revision_context
from mozaiksai.core.artifacts import ArtifactStore
from mozaiksai.core.session.persistence import SessionStateStore


async def get_revision_context(
    *,
    context: ControlPlaneToolContext | dict[str, Any] | None = None,
    session_store: SessionStateStore | None = None,
    artifact_store: ArtifactStore | None = None,
    pack_loader: Any = load_selected_refinement_harness,
) -> dict[str, Any]:
    return await assemble_revision_context(
        context=context,
        session_store=session_store,
        artifact_store=artifact_store,
        pack_loader=pack_loader,
    )


__all__ = ["get_revision_context"]
