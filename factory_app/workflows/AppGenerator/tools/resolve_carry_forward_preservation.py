"""
AG2 wrapper for the Phase 7A carry-forward preservation resolver.

Thin wrapper with Pydantic tool metadata annotations that delegates to
the core implementation in
``factory_app.control_plane.tools.resolve_carry_forward_preservation``.
"""
from __future__ import annotations

from typing import Annotated, Any

from pydantic import Field

from factory_app.refinement_harness.tools.resolve_carry_forward_preservation import (
    resolve_carry_forward_preservation as _core,
)


async def resolve_carry_forward_preservation(
    *,
    context_variables: Annotated[
        Any | None,
        Field(description="AG2-injected workflow context variables."),
    ] = None,
) -> dict[str, Any]:
    """Inject allowlisted declarative module contract files from the prior app bundle.

    Runs automatically after ``assemble_app_tasks`` completes.  Reads
    ``carry_forward_decisions`` from ``app_build_plan`` in context, loads the
    prior workspace via ``load_artifact_workspace()``, and copies only Phase 7A
    allowlisted module contract files (``module.yaml`` and ``contracts/*.yaml``)
    for modules with ``decision == "reuse"`` to paths not already produced by
    generation.

    Generated output wins all conflicts.  Backend Python,
    ``runtime_extensions.yaml``, custom React, route manifests, database
    artifacts, env files, and all non-declarative files are never copied.
    No-ops gracefully when prior workspace is unavailable or
    ``carry_forward_decisions`` is absent.  Emits ``carry_forward_report`` to
    ``context_variables``.
    """
    return await _core(
        context_variables=(
            dict(context_variables)
            if isinstance(context_variables, dict)
            else context_variables
        ),
    )

