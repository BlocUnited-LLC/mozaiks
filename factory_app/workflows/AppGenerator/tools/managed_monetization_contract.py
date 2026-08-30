"""Canonical AppGenerator monetization-provider selection contract."""

from __future__ import annotations

from typing import Any, Literal

ManagedMonetizationProvider = Literal["mozaiks_pay", "entitlement_dispatch"]

MOZAIKS_PAY_PROVIDER_ID = "mozaiks_pay"
SELF_MANAGED_PROVIDER_ID = "entitlement_dispatch"
MOZAIKSPAY_PACK_ID = "mozaikspay"
SUBSCRIPTION_WRITE_PATH_CAPABILITY = "subscription_write_path"
MANAGED_MONETIZATION_PROVIDER_IDS: frozenset[str] = frozenset(
    {MOZAIKS_PAY_PROVIDER_ID, SELF_MANAGED_PROVIDER_ID}
)


def pack_id_from_descriptor(pack: dict[str, Any]) -> str:
    return str(pack.get("capability_pack_id") or pack.get("id") or pack.get("pack_id") or "").strip()


def selected_pack_ids(capability_packs: list[dict[str, Any]] | None) -> frozenset[str]:
    return frozenset(
        pack_id_from_descriptor(pack)
        for pack in capability_packs or []
        if isinstance(pack, dict) and pack_id_from_descriptor(pack)
    )


def is_mozaikspay_pack(pack: dict[str, Any]) -> bool:
    return pack_id_from_descriptor(pack) == MOZAIKSPAY_PACK_ID


def is_managed_pack(pack: dict[str, Any]) -> bool:
    return str(pack.get("capability_source") or "").strip() == "managed_capability"


def normalize_monetization_provider(value: Any) -> str | None:
    provider = str(value or "").strip()
    if provider in {"", "none", "free", "null"}:
        return None
    if provider not in MANAGED_MONETIZATION_PROVIDER_IDS:
        allowed = ", ".join(sorted(MANAGED_MONETIZATION_PROVIDER_IDS))
        raise ValueError(
            f"AppBuildPlan.monetization_provider must be one of [{allowed}] when set; got {provider!r}."
        )
    return provider


__all__ = [
    "MANAGED_MONETIZATION_PROVIDER_IDS",
    "MOZAIKS_PAY_PROVIDER_ID",
    "MOZAIKSPAY_PACK_ID",
    "SELF_MANAGED_PROVIDER_ID",
    "SUBSCRIPTION_WRITE_PATH_CAPABILITY",
    "ManagedMonetizationProvider",
    "is_managed_pack",
    "is_mozaikspay_pack",
    "normalize_monetization_provider",
    "pack_id_from_descriptor",
    "selected_pack_ids",
]
