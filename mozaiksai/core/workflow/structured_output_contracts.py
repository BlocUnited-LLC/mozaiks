"""Content-pinned references to canonical workflow structured outputs."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict, field_validator


class _FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, tuple | list):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {
            str(key): _jsonable(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    return value


def stable_digest(data: Any) -> str:
    """Return the SHA-256 digest of one canonical JSON value."""

    canonical = json.dumps(
        _jsonable(data), sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )
    return hashlib.sha256(canonical.encode("ascii")).hexdigest()


_STRUCTURED_OUTPUT_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class StructuredOutputContractRef(_FrozenModel):
    """Content-pinned locator for a workflow-owned structured-output model."""

    ref_schema_version: Literal["mozaiks.structured_output_contract_ref.v1"] = (
        "mozaiks.structured_output_contract_ref.v1"
    )
    workflow_name: str
    model_id: str
    schema_digest: str

    @field_validator("workflow_name", "model_id")
    @classmethod
    def _closed_name(cls, value: str, info: Any) -> str:
        text = str(value or "").strip()
        if _STRUCTURED_OUTPUT_NAME.fullmatch(text) is None:
            raise ValueError(
                f"{info.field_name} must be a canonical workflow/model identifier"
            )
        return text

    @field_validator("schema_digest")
    @classmethod
    def _schema_digest(cls, value: str) -> str:
        text = str(value or "").strip()
        if _SHA256.fullmatch(text) is None:
            raise ValueError("schema_digest must be a lowercase SHA-256 digest")
        return text


def structured_output_schema_digest(model: type[BaseModel]) -> str:
    """Digest the normalized schema emitted by the canonical model compiler."""

    return stable_digest(model.model_json_schema())


def _default_exact_model_ids() -> frozenset[str]:
    """The canonical registry's exact-model ids — an explicit default only.

    Canonical plan derivation never uses this default: it passes the exact
    model ids resolved from its supplied assignment-descriptor authority, so
    ambient registry state cannot influence a schema digest on the canonical
    path. Production assignment execution keeps the canonical registry as its
    natural default.
    """
    from .assignment_kinds import ASSIGNMENT_CONTRACT_DESCRIPTORS

    return frozenset(
        descriptor.structured_output_model_id
        for descriptor in ASSIGNMENT_CONTRACT_DESCRIPTORS.values()
    )


def _compiled_models(
    config: Any, *, exact_model_ids: frozenset[str] | None = None
) -> dict[str, type[BaseModel]]:
    from mozaiksai.core.workflow.declarative.contracts import StructuredOutputsConfig
    from mozaiksai.core.workflow.outputs.structured import build_models_from_config

    verified = StructuredOutputsConfig.model_validate(config)
    dumped = verified.model_dump(by_alias=True, exclude_unset=True)
    models = cast(
        dict[str, type[BaseModel]],
        build_models_from_config(dumped.get("models", {})),
    )
    resolved_exact_ids = (
        exact_model_ids if exact_model_ids is not None else _default_exact_model_ids()
    )
    for model_id in resolved_exact_ids & models.keys():
        model = models[model_id]
        model.model_config = ConfigDict(**model.model_config, extra="forbid")
        model.model_rebuild(force=True)
    return models


def build_structured_output_contract_ref(
    *,
    workflow_name: str,
    model_id: str,
    configs: Mapping[str, Any],
    exact_model_ids: frozenset[str] | None = None,
) -> StructuredOutputContractRef:
    """Resolve a model through the existing canonical workflow compiler."""

    config = configs.get(workflow_name)
    if config is None:
        raise ValueError(f"unknown structured-output workflow {workflow_name!r}")
    model = _compiled_models(config, exact_model_ids=exact_model_ids).get(model_id)
    if model is None:
        raise ValueError(
            f"unknown structured-output model {workflow_name!r}/{model_id!r}"
        )
    return StructuredOutputContractRef(
        workflow_name=workflow_name,
        model_id=model_id,
        schema_digest=structured_output_schema_digest(model),
    )


def resolve_structured_output_contract_ref(
    ref: StructuredOutputContractRef, *, configs: Mapping[str, Any]
) -> type[BaseModel]:
    """Cold-resolve a ref and reject unknown or schema-stale contracts."""

    verified_ref = StructuredOutputContractRef.model_validate(
        ref.model_dump(mode="json")
    )
    config = configs.get(verified_ref.workflow_name)
    if config is None:
        raise ValueError(
            f"unknown structured-output workflow {verified_ref.workflow_name!r}"
        )
    model = _compiled_models(config).get(verified_ref.model_id)
    if model is None:
        raise ValueError(
            f"unknown structured-output model {verified_ref.workflow_name!r}/"
            f"{verified_ref.model_id!r}"
        )
    if structured_output_schema_digest(model) != verified_ref.schema_digest:
        raise ValueError("structured-output schema digest mismatch")
    return model


__all__ = [
    "StructuredOutputContractRef",
    "build_structured_output_contract_ref",
    "resolve_structured_output_contract_ref",
    "stable_digest",
    "structured_output_schema_digest",
]
