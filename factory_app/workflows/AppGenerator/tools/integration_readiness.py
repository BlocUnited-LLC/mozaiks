"""AppGenerator integration readiness checkpoint tool."""

from __future__ import annotations

from typing import Any

from mozaiksai.core.workflow.generator_support.connector_request import (
    collect_missing_connector_needs,
)
from mozaiksai.core.workflow.generator_support.connector_request import (
    record_integration_need as shared_record_integration_need,
)


async def record_integration_need(
    service: str,
    display_name: str | None = None,
    kind: str = "api_key",
    provider: str | None = None,
    purpose: str | None = None,
    required_at: str = "runtime",
    required_fields: list[dict[str, Any]] | None = None,
    required_by: dict[str, Any] | None = None,
    optional: bool = False,
    preferred_setup_lane: str | None = None,
    allowed_setup_lanes: list[str] | None = None,
    managed_default: dict[str, Any] | str | None = None,
    context_variables: Any = None,
) -> dict[str, Any]:
    """Record an integration need discovered by an AppGenerator task agent."""

    return await shared_record_integration_need(
        service=service,
        display_name=display_name,
        kind=kind,
        provider=provider,
        purpose=purpose,
        required_at=required_at,
        required_fields=required_fields,
        required_by=required_by,
        optional=optional,
        preferred_setup_lane=preferred_setup_lane,
        allowed_setup_lanes=allowed_setup_lanes,
        managed_default=managed_default,
        context_variables=context_variables,
    )


async def check_integration_readiness(
    required_at: list[str] | None = None,
    prompt: bool = True,
    context_variables: Any = None,
) -> dict[str, Any]:
    """Aggregate AppGenerator integration needs and collect missing setup inline."""

    return await collect_missing_connector_needs(
        context_variables=context_variables,
        required_at=required_at,
        prompt=prompt,
    )


__all__ = ["check_integration_readiness", "record_integration_need"]

