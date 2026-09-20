"""Sequence normalisation helpers shared across control-plane and app-context code."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

__all__ = ["dedupe_strings"]


def dedupe_strings(values: Iterable[Any] | None) -> list[str]:
    """Strip each value, drop blanks, and keep the first occurrence of each.

    Order-preserving and case-sensitive: "A" and "a" are distinct entries.
    """
    deduped: list[str] = []
    seen: set[str] = set()
    for value in values or []:
        normalized = str(value or "").strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(normalized)
    return deduped
