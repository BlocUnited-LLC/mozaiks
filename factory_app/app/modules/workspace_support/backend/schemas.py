from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SupportRequest:
    request_id: str
    app_id: str
    user_id: str | None
    message: str
    page_url: str | None
    page_title: str | None
    severity: str  # low | medium | high
    status: str  # open | resolved
    created_at: str
    resolved_at: str | None = None
    notes: str | None = None
