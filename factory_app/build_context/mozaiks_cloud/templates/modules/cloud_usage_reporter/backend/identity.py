"""Pure configured identity boundary shared by reporter and status action."""
from __future__ import annotations

import os


class UsageReporterIdentityError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def configured_app_id() -> str:
    cloud_app_id = str(os.environ.get("MOZAIKS_CLOUD_APP_ID") or "").strip()
    runtime_app_id = str(os.environ.get("MOZAIKS_APP_ID") or "").strip()
    if cloud_app_id and runtime_app_id and cloud_app_id != runtime_app_id:
        raise UsageReporterIdentityError(
            "app_identity_mismatch",
            "MOZAIKS_CLOUD_APP_ID and MOZAIKS_APP_ID must match for usage reporting",
        )
    app_id = cloud_app_id or runtime_app_id
    if not app_id:
        raise UsageReporterIdentityError(
            "app_identity_missing",
            "MOZAIKS_CLOUD_APP_ID or MOZAIKS_APP_ID is required for usage reporting",
        )
    return app_id
