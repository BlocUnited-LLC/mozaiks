"""Bounded, app-scoped generation facts carried by the existing build outbox.

These are observations, not learned policy or anonymized community telemetry.
The receiver owns retention and aggregation. No source files or prompts travel
with this evidence.
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

Label = Annotated[str, Field(min_length=1, max_length=200)]
GateType = Literal["ui_quality", "module_contract_quality", "module_runtime_quality"]
_GATE_CONTEXT_PREFIXES: tuple[tuple[str, GateType], ...] = (
    ("app_ui_quality", "ui_quality"),
    ("module_contract_quality", "module_contract_quality"),
    ("module_runtime_quality", "module_runtime_quality"),
)


class GateEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    gate_type: GateType
    status: Literal["passed", "blocked"]
    issue_count: int = Field(ge=0)


class GenerationEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    schema_version: Literal["mozaiks.generation_evidence.v1"] = "mozaiks.generation_evidence.v1"
    module_ids: list[Label] = Field(default_factory=list, max_length=100)
    capability_packs: list[Label] = Field(default_factory=list, max_length=100)
    gates: list[GateEvidence] = Field(default_factory=list, max_length=3)
    archetype_ref: Label | None = None
    prompt_variant_id: Label | None = None
    model_name: Label | None = None
    policy_ref: Label | None = None
    factory_version: Label | None = None

    @model_validator(mode="after")
    def unique_gates(self) -> Self:
        gate_types = [gate.gate_type for gate in self.gates]
        if len(gate_types) != len(set(gate_types)):
            raise ValueError("generation evidence must contain each gate_type at most once")
        return self


def _label(context: Mapping[str, Any], key: str) -> str | None:
    value = context.get(key)
    return value.strip()[:200] if isinstance(value, str) and value.strip() else None


def collect_generation_evidence(context: Mapping[str, Any]) -> GenerationEvidence:
    """Project declared facts only; missing decision evidence stays unknown."""
    files = context.get("generated_files")
    modules = set()
    for path in files if isinstance(files, dict) else []:
        match = re.search(r"(?:^|/)modules/([a-zA-Z0-9_-]{1,200})/", str(path))
        if match:
            modules.add(match.group(1))
    # The launch-level capability_packs registry describes available inputs.
    # The normalized AppBuildPlan records which packs were actually selected.
    plan = context.get("app_build_plan")
    packs = plan.get("capability_packs") if isinstance(plan, dict) else None
    pack_ids = set()
    for pack in packs if isinstance(packs, list) else []:
        value = pack.get("capability_pack_id") if isinstance(pack, dict) else None
        if isinstance(value, str) and value.strip():
            pack_ids.add(value.strip()[:200])
    gates = []
    for prefix, gate_type in _GATE_CONTEXT_PREFIXES:
        status = context.get(f"{prefix}_status")
        if status in {"passed", "blocked"}:
            warnings = context.get(f"{prefix}_warnings")
            gates.append(GateEvidence(gate_type=gate_type, status=status,
                                      issue_count=len(warnings) if isinstance(warnings, list) else 0))
    return GenerationEvidence(
        module_ids=sorted(modules)[:100], capability_packs=sorted(pack_ids)[:100], gates=gates,
        archetype_ref=_label(context, "archetype_ref"),
        prompt_variant_id=_label(context, "prompt_variant_id"),
        model_name=_label(context, "model_name"), policy_ref=_label(context, "policy_ref"),
        factory_version=_label(context, "factory_version"),
    )
