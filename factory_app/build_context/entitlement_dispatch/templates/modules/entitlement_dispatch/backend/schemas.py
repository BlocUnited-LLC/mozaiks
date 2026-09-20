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


def metadata_entries_to_map(entries: list[dict[str, Any]] | None) -> dict[str, str]:
    """Fold closed {key, value} request entries into the stored metadata map.

    The module request contract carries metadata as typed entries; assignment
    records keep the {key: value} map shape. Later entries win on duplicate keys.
    """
    metadata: dict[str, str] = {}
    for entry in entries or []:
        metadata[str(entry["key"])] = str(entry["value"])
    return metadata
