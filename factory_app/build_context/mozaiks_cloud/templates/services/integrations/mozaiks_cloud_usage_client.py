"""Mozaiks Cloud usage-report client.

Posts the app's own daily usage rollups (aggregate counts only) to the
configured Mozaiks Cloud-compatible operator endpoint:

    POST /usage/rollups   (scope: cloud:usage or cloud:deploy)

Sink-agnostic by design: where reports go is entirely determined by the
mozaiks_cloud connector / MOZAIKS_CLOUD_* configuration. When nothing is
configured the client reports is_configured() == False and sends nothing —
generated apps never phone home by default.

An operator may inject MOZAIKS_CLOUD_USAGE_API_KEY at runtime to give this
client a credential dedicated to usage reporting. Without it, the normal
Mozaiks Cloud connector or API key continues to work.
"""

from __future__ import annotations

import os
from typing import Any

from .mozaiks_cloud_client import (
    MozaiksCloudConfigurationError,
    MozaiksCloudTransport,
)


class MozaiksCloudUsageClient:
    def __init__(self, transport: MozaiksCloudTransport | None = None) -> None:
        if transport is not None:
            self._transport = transport
        else:
            usage_key = os.getenv("MOZAIKS_CLOUD_USAGE_API_KEY", "").strip()
            self._transport = MozaiksCloudTransport(api_key=usage_key or None)

    async def is_configured(self) -> bool:
        try:
            await self._transport.settings()
        except MozaiksCloudConfigurationError:
            return False
        except Exception:
            return False
        return True

    async def post_usage_rollups(
        self,
        rollups: list[dict[str, Any]],
        *,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """Send daily usage rollups. Each rollup:
        {period_start, granularity, page_views, unique_sessions,
        action_invocations, active_users}.
        """
        return await self._transport.request(
            "POST",
            "/usage/rollups",
            json={"rollups": list(rollups)},
            idempotency_key=idempotency_key,
        )
