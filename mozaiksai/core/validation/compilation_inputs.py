"""Artifact-bound compiler inputs; candidate files never supply approval."""
from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from mozaiksai.core.artifacts.content_store import read_verified_artifact_bundle
from mozaiksai.core.artifacts.models import BuildRecord, resolve_canonical_bundle_entry
from mozaiksai.core.artifacts.store import get_artifact_store
from mozaiksai.core.artifacts.summary_artifacts import _summary_bytes, extract_summary_payload
from mozaiksai.core.session.build_binding import RunBuildBinding
from mozaiksai.core.workflow.context.frozen import detach
from mozaiksai.core.workflow.generator_support.design_access_policy import (
    resolve_design_access_policy,
)
from mozaiksai.core.workflow.generator_support.module_entitlement_gates import (
    resolve_subscription_contract,
)


class _InputReference(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    record_id: str
    sha256: str


class _CompilerInputs(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    binding: RunBuildBinding
    app_build_plan: dict[str, Any]
    design_docs: _InputReference
    subscription_contract: _InputReference | None = None


class _CompilationBinding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    version: Literal[1] = 1
    archive_sha256: str
    parent_record_id: str | None = None
    inputs: _CompilerInputs
    inputs_sha256: str


def _digest(value: Any) -> str:
    return hashlib.sha256(_summary_bytes(value)).hexdigest()


def _record_binding(record: BuildRecord) -> RunBuildBinding:
    metadata = record.commit_metadata.metadata
    return RunBuildBinding.model_validate({key: metadata.get(key) for key in RunBuildBinding.model_fields})


async def _summary(store: Any, *, app_id: str, owner_user_id: str, family: str, record_id: str) -> tuple[BuildRecord, dict[str, Any], str]:
    record = await store.get_build_record(app_id=app_id, build_record_id=record_id)
    if record is None or (
        record.id != record_id or record.app_id != app_id or record.build_family != family
        or record.build_key != family or record.commit_metadata.author_user_id != owner_user_id
        or record.source_workflow != {"design_docs": "DesignDocs", "subscription_contract": "SubscriptionContractDesigner"}[family]
        or record.lifecycle_status.value not in {"current", "draft"}
    ):
        raise ValueError(f"Approved compilation {family} record is unavailable or out of scope")
    payload = extract_summary_payload(record)
    if not isinstance(payload, dict):
        raise ValueError(f"Approved compilation {family} summary is missing")
    encoded = _summary_bytes(payload)
    digest = hashlib.sha256(encoded).hexdigest()
    if len(record.files_manifest) != 1 or (
        record.files_manifest[0].path != f"{family}.json"
        or record.files_manifest[0].sha256 != digest
        or record.files_manifest[0].size_bytes != len(encoded)
    ):
        raise ValueError(f"Approved compilation {family} summary identity changed")
    return record, payload, digest


async def _context_from_inputs(inputs: _CompilerInputs, *, store: Any, owner_user_id: str) -> dict[str, Any]:
    design, payload, digest = await _summary(
        store, app_id=inputs.binding.target_app_id, owner_user_id=owner_user_id,
        family="design_docs", record_id=inputs.design_docs.record_id,
    )
    if digest != inputs.design_docs.sha256 or design.source_workflow != "DesignDocs":
        raise ValueError("Approved compilation DesignDocs identity changed")
    context = {
        "run_build_binding": inputs.binding.model_dump(),
        "app_build_plan": deepcopy(inputs.app_build_plan),
        "data_contract": deepcopy(payload.get("data_contract")),
        "design_surface_map": deepcopy(payload.get("surface_map")),
        "design_docs_artifact": design.model_dump(mode="json", by_alias=True),
    }
    # The existing owner verifies policy review and exact design/data identity.
    resolve_design_access_policy(context)
    if inputs.subscription_contract is not None:
        _, contract, digest = await _summary(
            store, app_id=inputs.binding.target_app_id, owner_user_id=owner_user_id,
            family="subscription_contract", record_id=inputs.subscription_contract.record_id,
        )
        if digest != inputs.subscription_contract.sha256:
            raise ValueError("Approved compilation subscription identity changed")
        context["subscription_contract"] = contract
    return context


async def capture_app_compilation_inputs(
    context: Any, *, owner_user_id: str, archive_sha256: str, artifact_store: Any,
) -> dict[str, Any] | None:
    """Freeze actual approved input references at successful Factory registration.

    This is a trusted lifecycle operation, never an upload/model tool. Missing
    authority refuses transformed packs; there is no latest-record lookup.
    """
    if context is None:
        return None
    plan = detach(context.get("app_build_plan"))
    contract = resolve_subscription_contract(context)
    if not isinstance(plan, dict) or not plan.get("capability_packs") or (
        context.get("data_contract") is None and contract is None
    ):
        return None
    if context.get("app_plan_outcome") != "ready":
        raise ValueError("Approved compilation requires the validated ready AppBuildPlan")
    binding = RunBuildBinding.model_validate(detach(context.get("run_build_binding")))
    projected = detach(context.get("design_docs_build_artifact")) or detach(context.get("design_docs_artifact"))
    if not isinstance(projected, dict) or not projected.get("_id"):
        raise ValueError("Approved compilation requires the exact bound DesignDocs record")
    design, payload, digest = await _summary(
        artifact_store, app_id=binding.target_app_id, owner_user_id=owner_user_id,
        family="design_docs", record_id=projected["_id"],
    )
    if extract_summary_payload(projected) != payload or any(
        detach(context.get(key)) != payload.get(field)
        for key, field in (("data_contract", "data_contract"), ("design_surface_map", "surface_map"))
    ):
        raise ValueError("Approved compilation context differs from its bound DesignDocs")
    subscription = None
    if contract is not None:
        projected_contract = detach(context.get("subscription_contract_artifact")) or {}
        record_id = context.get("subscription_contract_artifact_version_id") or projected_contract.get("_id")
        if not record_id:
            raise ValueError("Approved compilation requires the exact subscription record")
        _, saved_contract, contract_digest = await _summary(
            artifact_store, app_id=binding.target_app_id, owner_user_id=owner_user_id,
            family="subscription_contract", record_id=record_id,
        )
        if contract != saved_contract:
            raise ValueError("Approved compilation subscription differs from its bound record")
        subscription = _InputReference(record_id=record_id, sha256=contract_digest)
    inputs = _CompilerInputs(
        binding=binding, app_build_plan=plan,
        design_docs=_InputReference(record_id=design.id, sha256=digest), subscription_contract=subscription,
    )
    await _context_from_inputs(inputs, store=artifact_store, owner_user_id=owner_user_id)
    return _CompilationBinding(
        archive_sha256=archive_sha256, inputs=inputs, inputs_sha256=_digest(inputs.model_dump(mode="json")),
    ).model_dump(mode="json")


_RESOLVER_KEY = object()


@dataclass(frozen=True, init=False)
class ApprovedAppCompilation:
    """Opaque immutable validation input, issued only by the scoped resolver."""

    _context_json: bytes
    _binding_json: str
    _record_id: str
    _app_id: str

    def __init__(self, *, key: object, context: dict[str, Any], binding: _CompilationBinding, record: BuildRecord):
        if key is not _RESOLVER_KEY:
            raise TypeError("ApprovedAppCompilation must be obtained through resolve_app_compilation_inputs")
        object.__setattr__(self, "_context_json", _summary_bytes(context))
        object.__setattr__(self, "_binding_json", binding.model_dump_json())
        object.__setattr__(self, "_record_id", record.id)
        object.__setattr__(self, "_app_id", record.app_id)

    def context_for(self, app_id: str) -> dict[str, Any]:
        if app_id != self._app_id:
            raise ValueError("Approved compilation belongs to a different app")
        return json.loads(self._context_json)

    def for_child(self, archive_sha256: str) -> dict[str, Any]:
        binding = _CompilationBinding.model_validate_json(self._binding_json)
        return binding.model_copy(update={
            "archive_sha256": archive_sha256, "parent_record_id": self._record_id,
        }).model_dump(mode="json")


async def resolve_app_compilation_inputs(
    *, app_id: str, build_record_id: str, owner_user_id: str, build_registry_id: str,
    artifact_store: Any = None,
) -> ApprovedAppCompilation | None:
    """Resolve saved authority with exact app/owner/registry/archive/input lineage.

    The host supplies authenticated ownership and registry scope. Returning
    None leaves old/unbound bundles on strict static-template validation.
    """
    store = artifact_store or get_artifact_store()
    record = await store.get_build_record(app_id=app_id, build_record_id=build_record_id)
    selected = record
    if selected is not None and selected.id != build_record_id:
        raise ValueError("Approved compilation returned a different build record")
    first: _CompilationBinding | None = None
    seen: set[str] = set()
    while record is not None:
        if record.id in seen or len(seen) >= 100:
            raise ValueError("Approved compilation lineage is cyclic or exceeds its bound")
        seen.add(record.id)
        raw = record.commit_metadata.metadata.get("compilation_inputs")
        if raw is None and first is None:
            return None
        binding = _record_binding(record)
        if (
            record.app_id != app_id or record.build_family != "app_bundle" or record.build_key != "app_bundle"
            or record.lineage_root_id != selected.lineage_root_id
            or record.commit_metadata.author_user_id != owner_user_id or not owner_user_id
            or binding.target_app_id != app_id or binding.build_registry_id != build_registry_id
        ):
            raise ValueError("Approved compilation bundle is outside the owned build")
        saved = _CompilationBinding.model_validate(raw)
        if (
            saved.archive_sha256 != resolve_canonical_bundle_entry(record).sha256
            or saved.inputs_sha256 != _digest(saved.inputs.model_dump(mode="json"))
            or (first is not None and saved.inputs != first.inputs)
            or record.commit_metadata.metadata.get("capability_packs") != selected.commit_metadata.metadata.get("capability_packs")
            or record.canonical_inputs_version.get("design_docs") != saved.inputs.design_docs.record_id
            or (saved.inputs.subscription_contract is not None and record.canonical_inputs_version.get("subscription_contract")
                != saved.inputs.subscription_contract.record_id)
        ):
            raise ValueError("Approved compilation archive, inputs or lineage identity changed")
        first = first or saved
        if saved.parent_record_id is None:
            if record.source_workflow != "AppGenerator" or saved.inputs.binding != binding:
                raise ValueError("Approved compilation origin is not the bound Factory save")
            context = await _context_from_inputs(saved.inputs, store=store, owner_user_id=owner_user_id)
            await read_verified_artifact_bundle(selected)
            context["capability_packs"] = deepcopy(selected.commit_metadata.metadata.get("capability_packs") or [])
            return ApprovedAppCompilation(key=_RESOLVER_KEY, context=context, binding=first, record=selected)
        if saved.parent_record_id != record.parent_build_record_id:
            raise ValueError("Approved compilation parent differs from bundle lineage")
        record = await store.get_build_record(app_id=app_id, build_record_id=saved.parent_record_id)
        if record is not None and record.id != saved.parent_record_id:
            raise ValueError("Approved compilation returned a different parent record")
    raise ValueError("Approved compilation bundle or parent is missing")
