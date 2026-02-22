"""Agent specification model."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True, kw_only=True)
class AgentSpec:
    """Vendor-neutral agent description used by adapters."""

    name: str = "default-agent"
    system_prompt: str = "You are a helpful assistant."
    model: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
