"""Vendor event normalization contract."""

from __future__ import annotations

from typing import Protocol

from ..domain.events import CanonicalEvent


class VendorEventAdapter(Protocol):
    """Maps vendor-native events to canonical events."""

    def normalize(
        self,
        vendor_event: object,
        *,
        run_id: str,
        task_id: str | None,
    ) -> list[CanonicalEvent]:
        """Normalize a vendor event to one or more canonical events."""
        ...
