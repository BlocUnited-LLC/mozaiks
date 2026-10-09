"""Exact provider runtime references accepted for sealed candidate previews."""

from __future__ import annotations

import re

_RUNTIME_REFS = {
    "docker": re.compile(r"sha256:[0-9a-f]{64}"),
    "e2b": re.compile(
        r"(?:[A-Za-z0-9][A-Za-z0-9._-]*/)?[A-Za-z0-9][A-Za-z0-9._-]*:"
        r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
    ),
}


def is_sealed_runtime_ref(provider: str, runtime_ref: str | None) -> bool:
    pattern = _RUNTIME_REFS.get(provider)
    return isinstance(runtime_ref, str) and pattern is not None and pattern.fullmatch(runtime_ref) is not None
