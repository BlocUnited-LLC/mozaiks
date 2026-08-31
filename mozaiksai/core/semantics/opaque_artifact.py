"""Exact preservation contract for opaque, non-rendered child artifacts.

Executable/model-authored bytes are never promoted to semantic facts merely
to make an offline renderer appear complete.  This contract couples the exact
bytes to the existing ``ChildContractRef`` identity and fails closed on digest
substitution.  It performs no filesystem access and grants no execution or
promotion authority.
"""

from __future__ import annotations

import hashlib

from pydantic import field_validator, model_validator

from mozaiksai.core.semantics.refs import ChildContractRef, SemanticsModel


class PreservedOpaqueArtifact(SemanticsModel):
    contract_ref: ChildContractRef
    content: bytes

    @field_validator("content")
    @classmethod
    def _utf8_content(cls, value: bytes) -> bytes:
        data = bytes(value)
        try:
            data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("preserved opaque artifact content must be UTF-8") from exc
        return data

    @model_validator(mode="after")
    def _verify_content_digest(self) -> PreservedOpaqueArtifact:
        digest = hashlib.sha256(self.content).hexdigest()
        if digest != self.contract_ref.content_digest:
            raise ValueError("preserved opaque artifact bytes do not match ChildContractRef")
        return self


__all__ = ["PreservedOpaqueArtifact"]
