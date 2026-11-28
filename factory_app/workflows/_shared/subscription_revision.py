"""Exact, server-admitted inputs for a selected app's subscription revision."""

from __future__ import annotations

import hashlib
import json
import zipfile
from copy import deepcopy
from typing import Annotated, Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, StringConstraints

from mozaiksai.core.artifacts.content_store import ContentNotFoundError
from mozaiksai.core.artifacts.models import BuildRecord, resolve_canonical_bundle_entry
from mozaiksai.core.artifacts.summary_artifacts import _summary_bytes, verified_summary_payload
from mozaiksai.core.runtime.app.subscriptions_loader import SubscriptionsConfig
from mozaiksai.core.session.build_binding import BuildIdentity, RunBuildBinding
from mozaiksai.core.workflow.context.context_utils import context_to_dict

_MAX_BASELINE_BYTES = 8 * 1024 * 1024
_Digest = Annotated[str, StringConstraints(strict=True, pattern=r"^[a-f0-9]{64}$")]


class SubscriptionRevisionBaseline(BaseModel):
    """A frozen input snapshot, admitted only by the server's transition writer."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["mozaiks.subscription_revision_baseline.v1"] = "mozaiks.subscription_revision_baseline.v1"
    owner_user_id: str
    app_bundle_id: BuildIdentity
    app_binding: RunBuildBinding
    app_inputs: dict[str, str]
    archive_sha256: _Digest
    input_binding: RunBuildBinding
    design_record: BuildRecord
    subscription_record: BuildRecord
    catalog: dict[str, Any]
    context: dict[str, Any]
    payload_sha256: _Digest


def _payload_digest(payload: dict[str, Any]) -> str:
    encoded = _summary_bytes({key: value for key, value in payload.items() if key != "payload_sha256"})
    if len(encoded) > _MAX_BASELINE_BYTES:
        raise ValueError("Selected subscription inputs exceed the 8 MiB snapshot limit")
    return hashlib.sha256(encoded).hexdigest()


def _binding(record: BuildRecord) -> RunBuildBinding:
    metadata = record.commit_metadata.metadata
    return RunBuildBinding.model_validate({key: metadata.get(key) for key in RunBuildBinding.model_fields})


def _design_projection(design: BuildRecord) -> dict[str, Any]:
    from factory_app.workflows.DesignDocs.tools.save_design_doc import _experience_spec_to_yaml

    summary = verified_summary_payload(design)
    return {
        "design_docs_artifact": design.model_dump(mode="json", by_alias=True),
        "design_docs_build_artifact": None,
        "backend_design_document": summary.get("backend_markdown"),
        "frontend_design_document": summary.get("frontend_markdown"),
        "database_design_document": summary.get("database_markdown"),
        "design_surface_map": summary.get("surface_map"),
        "data_contract": summary.get("data_contract"),
        "experience_spec": summary.get("experience_spec"),
        "experience_spec_document": _experience_spec_to_yaml(summary["experience_spec"], summary["surface_map"], summary.get("ownership_normalizations")),
    }


def _validate_baseline(raw: Any) -> SubscriptionRevisionBaseline:
    baseline = SubscriptionRevisionBaseline.model_validate(raw)
    if baseline.payload_sha256 != _payload_digest(baseline.model_dump(mode="json", by_alias=True)):
        raise ValueError("Selected subscription input snapshot changed")
    target = baseline.app_binding.target_app_id
    if (baseline.input_binding.target_app_id != target
            or baseline.input_binding.build_registry_id != baseline.app_binding.build_registry_id):
        raise ValueError("Selected subscription input binding changed")
    for family, record in (("design_docs", baseline.design_record), ("subscription_contract", baseline.subscription_record)):
        if (record.app_id != target or record.build_family != family or record.build_key != family
                or record.commit_metadata.author_user_id != baseline.owner_user_id):
            raise ValueError("Selected subscription input is outside its app or owner")
        verified_summary_payload(record)
    if baseline.design_record.lifecycle_status.value != "current":
        raise ValueError("Selected subscription design is still a draft; review the app's design in Project first")
    if baseline.subscription_record.canonical_inputs_version.get("design_docs") != baseline.design_record.id:
        raise ValueError("Selected subscription contract has a different design input")
    if (baseline.app_inputs.get("design_docs") != baseline.design_record.id
            or baseline.app_inputs.get("subscription_contract") != baseline.subscription_record.id):
        raise ValueError("Selected app has different subscription inputs")
    if baseline.context != _design_projection(baseline.design_record):
        raise ValueError("Selected subscription design approval changed")
    contract = verified_summary_payload(baseline.subscription_record)
    catalog = SubscriptionsConfig.model_validate(baseline.catalog)
    if catalog.model_dump(mode="json") != SubscriptionsConfig.model_validate(contract.get("subscription_config_file")).model_dump(mode="json"):
        raise ValueError("Selected build catalog differs from its subscription contract")
    return baseline


async def capture_subscription_revision_baseline(
    *, app_record: BuildRecord, owner_user_id: str, build_registry_id: str, artifact_store: Any,
) -> dict[str, Any]:
    """Capture the selected archive and its approved inputs without a latest query."""
    from factory_app.workflows._shared.artifact_bundle import read_artifact_bundle
    from factory_app.workflows.DesignDocs.tools.save_design_doc import verified_design_summary
    from mozaiksai.core.validation.compilation_inputs import resolve_app_compilation_inputs

    binding = _binding(app_record)
    if (app_record.build_family != "app_bundle" or app_record.build_key != "app_bundle"
            or app_record.commit_metadata.author_user_id != owner_user_id or not owner_user_id
            or binding.target_app_id != app_record.app_id or binding.build_registry_id != build_registry_id
            or app_record.lifecycle_status.value not in {"draft", "current"}
            or app_record.validation_status.value != "passed" or app_record.app_validation_status != "passed"):
        raise ValueError("Selected subscription build is unavailable or outside its owner")
    try:
        approved = await resolve_app_compilation_inputs(
            app_id=app_record.app_id, build_record_id=app_record.id, owner_user_id=owner_user_id,
            build_registry_id=build_registry_id, artifact_store=artifact_store,
        )
    except (ContentNotFoundError, OSError, zipfile.BadZipFile) as exc:
        raise ValueError("Selected subscription compilation inputs could not be verified") from exc
    if approved is None:
        raise ValueError("Selected subscription build has no exact approved compilation inputs")
    inputs = approved.context_for(app_record.app_id)
    input_binding = RunBuildBinding.model_validate(inputs.get("run_build_binding"))
    design = BuildRecord.model_validate(inputs.get("design_docs_artifact"))
    if design.lifecycle_status.value != "current":
        raise ValueError("Selected subscription design is still a draft; review the app's design in Project first")
    contract_id = app_record.canonical_inputs_version.get("subscription_contract")
    contract = await artifact_store.get_build_record(app_id=app_record.app_id, build_record_id=contract_id) if contract_id else None
    if (contract is None or contract.id != contract_id or contract.lifecycle_status.value not in {"draft", "current"}
            or app_record.canonical_inputs_version.get("design_docs") != design.id):
        raise ValueError("Selected subscription build has no usable pinned contract and design")
    verified_design_summary(design, user_id=owner_user_id, build_id=input_binding.build_id)
    try:
        files, diagnostics = await read_artifact_bundle(app_record)
    except (ContentNotFoundError, OSError, zipfile.BadZipFile) as exc:
        raise ValueError("Selected subscription archive could not be verified") from exc
    if any(item.get("blocking") for item in diagnostics):
        raise ValueError("Selected subscription archive could not be verified")
    matches = [path for path in ("config/subscriptions.yaml", "app/config/subscriptions.yaml") if path in files]
    if len(matches) != 1:
        raise ValueError("Selected subscription archive has no single catalog")
    try:
        catalog = yaml.safe_load(files[matches[0]])
    except yaml.YAMLError as exc:
        raise ValueError("Selected subscription catalog is invalid") from exc
    if inputs.get("subscription_contract") != verified_summary_payload(contract):
        raise ValueError("Selected subscription contract differs from approved compilation")
    payload = SubscriptionRevisionBaseline(
        owner_user_id=owner_user_id, app_bundle_id=app_record.id, app_binding=binding,
        app_inputs=app_record.canonical_inputs_version,
        archive_sha256=resolve_canonical_bundle_entry(app_record).sha256,
        input_binding=input_binding, design_record=design, subscription_record=contract,
        catalog=catalog, context=_design_projection(design), payload_sha256="0" * 64,
    ).model_dump(mode="json", by_alias=True)
    payload["payload_sha256"] = _payload_digest(payload)
    return _validate_baseline(payload).model_dump(mode="json", by_alias=True)


def subscription_revision_baseline(context: Any) -> SubscriptionRevisionBaseline | None:
    """Read only the admitted snapshot; invalidated source records need no reread."""
    data = context_to_dict(context)
    raw = data.get("subscription_revision_baseline")
    if raw is None:
        return None
    baseline = _validate_baseline(raw)
    current = RunBuildBinding.model_validate(data.get("run_build_binding"))
    if (current.phase != "refinement" or current.target_app_id != baseline.app_binding.target_app_id
            or current.build_registry_id != baseline.app_binding.build_registry_id
            or data.get("user_id") != baseline.owner_user_id
            or data.get("workflow_sequence") not in {"subscription_patch", "subscription_revision"}):
        raise ValueError("Selected subscription snapshot is outside this revision")
    request = data.get("refinement_request_meta")
    if isinstance(request, dict):
        selected = (request.get("extra") or {}).get("settings_selection")
        if (request.get("build_family") != "subscription_contract"
                or request.get("build_record_id") != baseline.subscription_record.id
                or isinstance(selected, dict) and (
                    selected.get("app_bundle_id") != baseline.app_bundle_id
                    or selected.get("build_id") != baseline.app_binding.build_id
                    or selected.get("artifact_version_id") != baseline.subscription_record.id
                )):
            raise ValueError("Selected subscription snapshot belongs to a different request")
    return baseline


def subscription_revision_design_context(context: Any) -> dict[str, Any] | None:
    """Project inherited design for validation using its unchanged reviewed binding."""
    from factory_app.workflows.DesignDocs.tools.save_design_doc import verified_design_summary
    from mozaiksai.core.workflow.generator_support.design_access_policy import (
        resolve_design_access_policy,
    )

    baseline = subscription_revision_baseline(context)
    if baseline is None:
        return None
    verified_design_summary(
        baseline.design_record, user_id=baseline.owner_user_id, build_id=baseline.input_binding.build_id,
    )
    data = context_to_dict(context)
    data.update(deepcopy(baseline.context))
    data["design_docs_artifact_version_id"] = baseline.design_record.id
    resolve_design_access_policy(data)
    return data


def subscription_revision_prompt(context: Any) -> str | None:
    baseline = subscription_revision_baseline(context)
    if baseline is None:
        return None
    return "\n".join([
        "Revise the selected saved contract below using refinement_request. Preserve every unrequested plan, price, quota, gate, and provider-neutral setting.",
        "The catalog is the actual subscriptions.yaml from the selected app bundle. Its reviewed design inventory is the only source of allowed features.",
        "Do not redesign plans from current global documents or treat unspecified prices as permission to invent amounts.",
        json.dumps({"contract": verified_summary_payload(baseline.subscription_record), "selected_catalog": baseline.catalog}, ensure_ascii=False, sort_keys=True),
    ])
