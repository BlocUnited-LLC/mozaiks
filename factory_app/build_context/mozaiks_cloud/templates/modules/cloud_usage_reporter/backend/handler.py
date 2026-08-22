from __future__ import annotations

import os
from typing import Any


class CloudUsageReporterHandler:

    async def get_reporter_status(self, ctx, **_: Any) -> dict[str, Any]:
        from app.services.integrations.mozaiks_cloud_usage_client import (
            MozaiksCloudUsageClient,
        )

        enabled = str(
            os.environ.get("MOZAIKS_CLOUD_USAGE_REPORTING", "")
        ).strip().lower() not in {"0", "false", "no", "off"}
        configured = await MozaiksCloudUsageClient().is_configured()
        return {
            "enabled": enabled,
            "configured": configured,
            "reporting": enabled and configured,
        }
