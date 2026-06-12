from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SubscriptionStatus:
    success: bool
    found: bool = False
    plan_id: str | None = None
    plan_name: str | None = None
    status: str | None = None
    starts_at: str | None = None
    expires_at: str | None = None
    on_free_plan: bool = True

    @classmethod
    def from_hosted(cls, raw: dict[str, Any]) -> "SubscriptionStatus":
        return cls(
            success=bool(raw.get("success", True)),
            found=bool(raw.get("found", False)),
            plan_id=raw.get("plan_id"),
            plan_name=raw.get("plan_name"),
            status=raw.get("status"),
            starts_at=raw.get("starts_at"),
            expires_at=raw.get("expires_at"),
            on_free_plan=bool(raw.get("on_free_plan", True)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "found": self.found,
            "plan_id": self.plan_id,
            "plan_name": self.plan_name,
            "status": self.status,
            "starts_at": self.starts_at,
            "expires_at": self.expires_at,
            "on_free_plan": self.on_free_plan,
        }


def safe_portal_response(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        "success": bool(raw.get("success", False)),
        "portal_url": raw.get("portal_url"),
        "return_url": raw.get("return_url"),
        "error": raw.get("error") or raw.get("error_code"),
    }
