"""Shared setup contract helpers for generated app integration needs."""

from __future__ import annotations

import json
from typing import Any

SECRET_FIELD_TYPES = {"secret", "password", "api_key", "token"}
SETUP_LANES = {"managed", "connect_account", "bring_your_own_key", "not_required"}
DEFAULT_SETUP_LANE = "bring_your_own_key"
SENSITIVE_METADATA_KEY_PARTS = {
    "api_key",
    "apikey",
    "auth_token",
    "client_secret",
    "connection_string",
    "password",
    "private_key",
    "secret",
    "token",
}


def normalize_setup_lane(value: Any) -> str:
    lane = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "byok": "bring_your_own_key",
        "api_key": "bring_your_own_key",
        "credential": "bring_your_own_key",
        "credentials": "bring_your_own_key",
        "oauth": "connect_account",
        "oauth_connect": "connect_account",
        "hosted": "managed",
        "mozaiks_managed": "managed",
        "none": "not_required",
        "skip": "not_required",
    }
    lane = aliases.get(lane, lane)
    return lane if lane in SETUP_LANES else ""


def setup_lane_ids(value: Any) -> list[str]:
    lanes: list[str] = []
    if isinstance(value, list):
        for item in value:
            lane = normalize_setup_lane(item.get("id") if isinstance(item, dict) else item)
            if lane and lane not in lanes:
                lanes.append(lane)
    else:
        lane = normalize_setup_lane(value)
        if lane:
            lanes.append(lane)
    return lanes


def normalize_required_fields(raw: dict[str, Any]) -> list[dict[str, Any]]:
    fields = raw.get("required_fields")
    normalized: list[dict[str, Any]] = []
    if isinstance(fields, list):
        for field in fields:
            if not isinstance(field, dict):
                continue
            name = str(field.get("name") or "").strip()
            if not name:
                continue
            field_type = str(field.get("type") or "text").strip().lower()
            is_secret = bool(field.get("secret")) or field_type in SECRET_FIELD_TYPES
            normalized.append(
                {
                    "name": name,
                    "label": str(field.get("label") or name.replace("_", " ").title()),
                    "type": "secret" if is_secret else field_type,
                    "required": bool(field.get("required", True)),
                    "frontend_safe": False if is_secret else bool(field.get("frontend_safe", True)),
                    **({"options": field.get("options")} if isinstance(field.get("options"), list) else {}),
                }
            )
    if normalized:
        return normalized
    kind = str(raw.get("kind") or "api_key").strip().lower()
    secret_name = "api_key" if kind in {"api_key", "key", "secret"} else f"{kind}_secret"
    return [
        {
            "name": secret_name,
            "label": "API Key" if secret_name == "api_key" else secret_name.replace("_", " ").title(),
            "type": "secret",
            "required": True,
            "frontend_safe": False,
        }
    ]


def safe_managed_default(value: Any) -> dict[str, Any]:
    if isinstance(value, str) and value.strip():
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return {}
    if not isinstance(value, dict):
        return {}
    safe: dict[str, Any] = {}
    for key, item in value.items():
        normalized_key = str(key).strip().lower()
        if any(part in normalized_key for part in SENSITIVE_METADATA_KEY_PARTS):
            continue
        if isinstance(item, (str, int, float, bool)) or item is None:
            safe[str(key)] = item
    return safe


def normalize_setup_lanes(raw: dict[str, Any]) -> dict[str, Any]:
    """Return the app-agnostic setup-lane contract for an integration need."""

    explicit_lanes = (
        setup_lane_ids(raw.get("allowed_setup_lanes"))
        or setup_lane_ids(raw.get("setup_lanes"))
        or setup_lane_ids(raw.get("setup_lane"))
    )
    lanes = list(explicit_lanes)
    kind = str(raw.get("kind") or "api_key").strip().lower()
    has_fields = bool(normalize_required_fields(raw))

    if safe_managed_default(raw.get("managed_default")) and "managed" not in lanes:
        lanes.insert(0, "managed")
    if kind in {"oauth", "oidc"} and "connect_account" not in lanes:
        lanes.append("connect_account")
    if has_fields and DEFAULT_SETUP_LANE not in lanes:
        lanes.append(DEFAULT_SETUP_LANE)
    if not lanes:
        lanes = ["not_required"] if bool(raw.get("optional", False)) else [DEFAULT_SETUP_LANE]

    preferred = normalize_setup_lane(raw.get("preferred_setup_lane"))
    if preferred not in lanes:
        preferred = lanes[0]

    return {
        "preferred_setup_lane": preferred,
        "allowed_setup_lanes": lanes,
    }


__all__ = [
    "DEFAULT_SETUP_LANE",
    "SECRET_FIELD_TYPES",
    "SENSITIVE_METADATA_KEY_PARTS",
    "SETUP_LANES",
    "normalize_required_fields",
    "normalize_setup_lane",
    "normalize_setup_lanes",
    "safe_managed_default",
    "setup_lane_ids",
]
