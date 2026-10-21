from __future__ import annotations

import os
from typing import Any


class CloudUsageReporterHandler:

    async def get_reporter_status(self, ctx, **_: Any) -> dict[str, Any]:
        from app.services.integrations.mozaiks_cloud_usage_client import (
            MozaiksCloudUsageClient,
        )
        from .identity import UsageReporterIdentityError, configured_app_id

        enabled = str(
            os.environ.get("MOZAIKS_CLOUD_USAGE_REPORTING", "")
        ).strip().lower() not in {"0", "false", "no", "off"}
        try:
            app_id = configured_app_id()
        except UsageReporterIdentityError as exc:
            return {
                "enabled": enabled,
                "configured": False,
                "reporting": False,
                "reason": exc.code,
            }
        if app_id != str(getattr(ctx, "app_id", None) or "").strip():
            return {
                "enabled": enabled,
                "configured": False,
                "reporting": False,
                "reason": "app_identity_mismatch",
            }
        configured = await MozaiksCloudUsageClient(app_id=app_id).is_configured()
        return {
            "enabled": enabled,
            "configured": configured,
            "reporting": enabled and configured,
        }
