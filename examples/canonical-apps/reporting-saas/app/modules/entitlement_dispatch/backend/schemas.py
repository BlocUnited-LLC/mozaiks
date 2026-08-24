from typing import TypedDict


class SubscriptionAssignment(TypedDict):
    app_id: str
    user_id: str
    plan_id: str
    status: str
    granted_capabilities: list[str]
