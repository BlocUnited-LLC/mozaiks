from __future__ import annotations

from typing import Any

# Subscription assignment document stored in the billing.subscriptions collection.
#
# Fields read by ConfiguredEntitlementAdapter (via assignment_store config):
#   app_id        — used to scope queries per app (app_id_field default: "app_id")
#   user_id       — used to find the user's assignment (user_id_field default: "user_id")
#   status        — checked against active_statuses (default: ["active", "pending", "trialing"])
#   plan_id       — used to look up capabilities in config/subscriptions.yaml plan catalog
#
# Optional fields read by ConfiguredEntitlementAdapter:
#   granted_capabilities — if present, used directly instead of plan catalog lookup
#   activated_at  — informational; not used for grant logic
#   deactivated_at — informational; not used for grant logic
#   metadata      — not read by the adapter; available for app-owned billing context
SubscriptionAssignmentDoc = dict[str, Any]
