"""Event-to-stream framing contract."""

from __future__ import annotations

from typing import Protocol

from ..domain.events import CanonicalEvent


class StreamAdapter(Protocol):
    """Converts canonical events to transport-neutral stream frames."""

    def to_frame(self, event: CanonicalEvent) -> dict[str, object]:
        ...
