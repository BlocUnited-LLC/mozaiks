"""Collision-domain-qualified artifact addresses and accounted artifacts.

Leaf contract module: the physical-address vocabulary is shared by the
composition ledger, the implementation-artifact authority, and the
implementation binding, none of which may drag the offline materializer into
the semantics package import surface.
"""

from __future__ import annotations

import re
from typing import cast

from pydantic import Field, field_validator, model_validator

from mozaiksai.core.runtime.app.layout_registry import PathScope
from mozaiksai.core.semantics.portable_path import validate_portable_path
from mozaiksai.core.semantics.refs import SemanticsModel

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_GLOBAL_PATH_SCOPES = frozenset(
    {
        PathScope.APP_BUNDLE_ROOT,
        PathScope.WORKSPACE_ROOT,
        PathScope.DEPLOYMENT_DERIVED,
        PathScope.GENERATED_STAGING,
    }
)


def _sha256(value: str, *, field_name: str) -> str:
    text = str(value or "").strip()
    if _SHA256.fullmatch(text) is None:
        raise ValueError(f"{field_name} must be a lowercase SHA-256 digest")
    return text


class ArtifactAddress(SemanticsModel):
    """One collision-domain-qualified physical artifact address."""

    path_scope: PathScope
    placeholder_values: tuple[tuple[str, str], ...] = Field(default_factory=tuple)
    path: str

    @field_validator("placeholder_values")
    @classmethod
    def _placeholders(
        cls, value: tuple[tuple[str, str], ...]
    ) -> tuple[tuple[str, str], ...]:
        ordered = tuple(sorted(value))
        keys = [key for key, _ in ordered]
        if len(keys) != len(set(keys)):
            raise ValueError("artifact address substitutions must have unique names")
        return ordered

    @field_validator("path")
    @classmethod
    def _path(cls, value: str) -> str:
        return cast(str, validate_portable_path(value).text)

    @model_validator(mode="after")
    def _scope_identity(self) -> ArtifactAddress:
        if self.path_scope in _GLOBAL_PATH_SCOPES and self.placeholder_values:
            raise ValueError("global artifact addresses cannot use instance placeholders")
        if self.path_scope not in _GLOBAL_PATH_SCOPES and not self.placeholder_values:
            raise ValueError("instance-relative artifact addresses require placeholders")
        return self


class AccountedArtifact(SemanticsModel):
    address: ArtifactAddress
    content_digest: str | None

    @field_validator("content_digest")
    @classmethod
    def _digest(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _sha256(value, field_name="content_digest")


__all__ = [
    "AccountedArtifact",
    "ArtifactAddress",
]
