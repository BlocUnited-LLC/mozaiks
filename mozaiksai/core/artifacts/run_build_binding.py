"""Server-owned run-to-build binding.

A build-specific lifecycle claim requires the exact server-owned relation
``workflow_run_id -> build_id`` established by the same run. This module
defines that relation as one closed, digest-sealed object: the binding is
created only at the moment the current run establishes its build (the
terminal build tool persisting the BuildRecord), and it names the exact
persisted BuildRecord identity it was established for.

Chat/session "latest build" fields (``build_registry_id``,
``journey_instance_id``, latest ``build_id``, most-recent CURRENT record)
are presentation and workflow context. They are never run-to-build
authority and can never produce a binding.

The binding is persisted through the existing server-owned session seam
(:data:`~mozaiksai.core.data.persistence.persistence_manager.SERVER_OWNED_SESSION_FIELDS`)
— the same session document and authority as the terminal receipt. There is
no second build store: the authoritative relation also lives on the
BuildRecord itself (``workflow_run_id``/``build_id`` stamped at creation),
and cold verification resolves the persisted record and requires exact
agreement on every dimension.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

RUN_BUILD_BINDING_CONTEXT_KEY = "run_build_binding"

BINDING_SCHEMA_VERSION = "mozaiks.run_build_binding.v1"

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class BindingValidationError(ValueError):
    """A run/build binding is malformed, mistyped, or fails digest verification."""


def _digest_payload(data: dict[str, Any]) -> str:
    canonical = json.dumps(
        {key: value for key, value in data.items() if key != "binding_digest"},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class RunBuildBinding(BaseModel):
    """The exact server-owned relation between one run and the build it established.

    Every identity field is required: the binding names the run, the build,
    and the exact persisted BuildRecord identity (record id, family, key,
    version). ``bundle_digest`` carries the bundle content digest when the
    build established one. The binding proves body integrity through
    ``binding_digest`` only — authorization always requires cold
    verification against the persisted BuildRecord.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["mozaiks.run_build_binding.v1"]
    scope: Literal["server"]
    app_id: str
    workflow_name: str
    workflow_run_id: str
    build_id: str
    build_record_id: str
    build_family: str
    build_key: str
    version_number: int = Field(ge=1)
    bundle_digest: str | None = None
    binding_digest: str

    @field_validator(
        "app_id",
        "workflow_name",
        "workflow_run_id",
        "build_id",
        "build_record_id",
        "build_family",
        "build_key",
    )
    @classmethod
    def _non_empty(cls, value: str) -> str:
        value = str(value or "").strip()
        if not value:
            raise ValueError("run/build binding identity fields must be non-empty")
        return value

    @field_validator("bundle_digest")
    @classmethod
    def _digest_shape(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = str(value or "").strip()
        if not _SHA256_RE.match(value):
            raise ValueError("bundle_digest must be a lowercase 64-hex sha256 digest")
        return value

    def verify_digest(self) -> None:
        expected = _digest_payload(self.model_dump(mode="json"))
        if expected != self.binding_digest:
            raise BindingValidationError(
                "run/build binding digest mismatch: binding was altered after issue"
            )


def issue_run_build_binding(
    *,
    app_id: str,
    workflow_name: str,
    workflow_run_id: str,
    build_id: str,
    build_record_id: str,
    build_family: str,
    build_key: str,
    version_number: int,
    bundle_digest: str | None = None,
) -> RunBuildBinding:
    """Issue the sealed binding for a build the current run just established.

    Callable only from the server-owned write point that persisted the
    BuildRecord for this run. Never derive the inputs from chat identity,
    latest-session build fields, journey state, timestamps, or a
    most-recent CURRENT record.
    """
    payload: dict[str, Any] = {
        "schema_version": BINDING_SCHEMA_VERSION,
        "scope": "server",
        "app_id": app_id,
        "workflow_name": workflow_name,
        "workflow_run_id": workflow_run_id,
        "build_id": build_id,
        "build_record_id": build_record_id,
        "build_family": build_family,
        "build_key": build_key,
        "version_number": version_number,
        "bundle_digest": bundle_digest,
        "binding_digest": "",
    }
    model = RunBuildBinding.model_validate(
        {**payload, "binding_digest": _digest_payload({**payload, "binding_digest": ""})}
    )
    # Recompute over the validated/normalized dump so verification is stable.
    dumped = model.model_dump(mode="json")
    return RunBuildBinding.model_validate(
        {**dumped, "binding_digest": _digest_payload(dumped)}
    )


def parse_run_build_binding(raw: Any) -> RunBuildBinding:
    """Parse and digest-verify a persisted run/build binding.

    Raises :class:`BindingValidationError` for anything that is not a valid,
    unaltered binding. Callers treat that as "no binding" and fail closed —
    a malformed binding never authorizes a build-specific claim.
    """
    if not isinstance(raw, dict):
        raise BindingValidationError("run/build binding must be a mapping")
    try:
        binding = RunBuildBinding.model_validate(raw)
    except BindingValidationError:
        raise
    except Exception as exc:
        raise BindingValidationError(
            f"invalid run/build binding: {type(exc).__name__}"
        ) from exc
    binding.verify_digest()
    return binding


__all__ = [
    "BINDING_SCHEMA_VERSION",
    "RUN_BUILD_BINDING_CONTEXT_KEY",
    "BindingValidationError",
    "RunBuildBinding",
    "issue_run_build_binding",
    "parse_run_build_binding",
]
