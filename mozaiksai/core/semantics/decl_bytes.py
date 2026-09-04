"""Named canonical byte contracts for declarative application artifacts.

Two versioned serialization contracts back every deterministic
application-family artifact:

``mozaiks.json_decl_bytes.v1``
    UTF-8, LF newlines, two-space indentation, ``ensure_ascii=False``,
    deterministic declaration order (the caller supplies an ordered
    document built from closed models — key order is meaning, never
    re-sorted here), one trailing newline, no timestamps, no host paths.

``mozaiks.yaml_decl_bytes.v1``
    UTF-8, LF newlines, ``allow_unicode=True``, ``sort_keys=False`` so the
    closed model's declaration order survives, block style, unbounded line
    width (no content-dependent wrapping), one trailing newline.

Both contracts parse their own output back and require semantic equality
with the input document, so a serializer regression can never silently
change canonical meaning. Neither reads clocks, environment, or the
filesystem.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, Literal

import yaml

JSON_DECL_BYTES_VERSION: Literal["mozaiks.json_decl_bytes.v1"] = "mozaiks.json_decl_bytes.v1"
YAML_DECL_BYTES_VERSION: Literal["mozaiks.yaml_decl_bytes.v1"] = "mozaiks.yaml_decl_bytes.v1"


class DeclBytesError(ValueError):
    """The document violates a canonical byte contract."""


def json_decl_bytes(document: Mapping[str, Any]) -> bytes:
    """Serialize one declaration document under ``mozaiks.json_decl_bytes.v1``."""
    if not isinstance(document, Mapping):
        raise DeclBytesError("json_decl_bytes requires a mapping document")
    text = json.dumps(dict(document), indent=2, ensure_ascii=False)
    text = text.replace("\r\n", "\n")
    if not text.endswith("\n"):
        text += "\n"
    data = text.encode("utf-8")
    if json.loads(data.decode("utf-8")) != json.loads(json.dumps(dict(document))):
        raise DeclBytesError("json_decl_bytes round-trip changed document meaning")
    return data


def yaml_decl_bytes(document: Mapping[str, Any]) -> bytes:
    """Serialize one declaration document under ``mozaiks.yaml_decl_bytes.v1``."""
    if not isinstance(document, Mapping):
        raise DeclBytesError("yaml_decl_bytes requires a mapping document")
    text = yaml.safe_dump(
        dict(document),
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
        width=2_000_000_000,
    )
    text = text.replace("\r\n", "\n")
    if not text.endswith("\n"):
        text += "\n"
    data = text.encode("utf-8")
    if yaml.safe_load(data.decode("utf-8")) != json.loads(json.dumps(dict(document))):
        raise DeclBytesError("yaml_decl_bytes round-trip changed document meaning")
    return data


__all__ = [
    "DeclBytesError",
    "JSON_DECL_BYTES_VERSION",
    "YAML_DECL_BYTES_VERSION",
    "json_decl_bytes",
    "yaml_decl_bytes",
]
