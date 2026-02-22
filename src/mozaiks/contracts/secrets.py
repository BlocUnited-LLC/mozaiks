"""Secret reference contract."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class SecretRef(BaseModel):
    """Reference metadata for a required or optional secret."""

    model_config = ConfigDict(extra="forbid")

    key: str = Field(..., min_length=1)
    required: bool = True
    description: str | None = None


__all__ = ["SecretRef"]
