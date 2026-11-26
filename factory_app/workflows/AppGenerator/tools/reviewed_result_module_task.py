"""Use the accepted module as the input to Factory's existing action and result compilers."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from fnmatch import fnmatchcase
from typing import Any

import yaml

from factory_app.workflows._shared.platform.build_target import require_build_binding
from factory_app.workflows._shared.revision_app_source import (
    read_revision_source,
    revision_source_artifact_id,
)
from mozaiksai.core.runtime.persistence.collection_writes import is_managed_timestamp
from mozaiksai.core.runtime.persistence.intent_loader import iter_data_contract_collections
from mozaiksai.core.validation.compilation_inputs import resolve_app_compilation_inputs
from mozaiksai.core.workflow.context.frozen import detach
from mozaiksai.core.workflow.generator_support.design_access_policy import (
    resolve_design_access_policy,
)
from mozaiksai.core.workflow.generator_support.module_action_inventory import (
    canonical_read_actions_for_surface,
    canonical_write_actions_for_surface,
    pinned_result_workflows,
)
from mozaiksai.core.workflow.generator_support.module_entitlement_gates import (
    resolve_subscription_contract,
)
from mozaiksai.core.workflow.generator_support.module_revision import RetainedAskRead

from .revision_result_coverage import revision_result_module_coverage


@dataclass(frozen=True)
class VerifiedResultModuleSource:
    candidate: dict[str, Any]
    source_build_id: str
    source_artifact_version_id: str
    accepted_module_ids: list[str]
    retained_companions: list[dict[str, Any]]

    def compiled_evidence(self, module: str, files: dict[str, str]) -> dict[str, Any]:
        """Describe the emitted contract files and the accepted companion documents that travel unchanged."""
        return {
            "module_id": module,
            "source_build_id": self.source_build_id,
            "source_artifact_version_id": self.source_artifact_version_id,
            "compiled_files": {path: yaml.safe_load(content) for path, content in files.items()},
            "compiled_file_sha256": {path: hashlib.sha256(content.encode()).hexdigest() for path, content in files.items()},
            "retained_companions": self.retained_companions,
            "accepted_module_ids": self.accepted_module_ids,
        }


def _matches_reviewed_task(task: dict[str, Any], context: Any) -> None:
    planned = [t for t in (context.get("app_build_plan") or {}).get("build_tasks", []) if t.get("task_id") == task.get("task_id")]
    dispatched = [t for t in context.get("app_task_batch_items") or [] if t.get("task_id") == task.get("task_id")]
    if (len(planned) != 1 or len(dispatched) != 1 or detach(dispatched[0]) != detach(task)
            or detach(planned[0]) != detach(task.get("current_build_task"))
            or any(detach(task.get(k)) != detach(v) for k, v in planned[0].items())):
        raise ValueError("Result module compilation requires the reviewed task and its exact registered dispatch")


def _module_surface(context: Any, module: str) -> dict[str, Any] | None:
    rows = [s for s in (detach(context.get("design_surface_map")) or {}).get("surfaces", [])
            if s.get("surface_id") == module and s.get("surface_kind") == "module" and s.get("owner") == "app"]
    return rows[0] if len(rows) == 1 else None


def _only_result_fields(module: str, files: dict[str, Any], desired: Any, grants: list[dict[str, Any]]) -> bool:
    def collections(contract: Any) -> dict[str, dict[str, Any]]:
        return {c["name"]: c for owner, kind, c in iter_data_contract_collections(contract) if (owner, kind) == (module, "module")}

    old, new = collections(json.loads(files["data/contract.json"])), collections(desired)
    if old.keys() != new.keys():
        return False
    for name, before in old.items():
        after = new[name]
        result_fields = {field for g in grants if g["collection"] == name for field in g["fields"]}
        if not result_fields and before != after:
            return False
        if any(before.get(key) != after.get(key) for key in ("entity", "scope", "tenancy", "owner_field")):
            return False
        fields = {f["name"]: f for f in after["fields"]}
        accepted_fields = {f["name"]: f for f in before["fields"]}
        if any(fields.get(key) != field for key, field in accepted_fields.items()):
            return False
        if any(key not in result_fields and not is_managed_timestamp(fields[key]) for key in fields.keys() - accepted_fields.keys()):
            return False
    return True


async def reviewed_result_module_task_output(
    task: dict[str, Any], context: Any, *, retained_ask_callers: Mapping[str, RetainedAskRead] | None = None,
) -> VerifiedResultModuleSource | None:
    """Return an immutable input only when all work is retained or deterministically compiled."""
    module = task.get("capability_pack_id")
    coverage = detach(context.get("revision_result_module_coverage")) or {}
    if (task.get("task_type") != "module_contract" or not isinstance(module, str)
            or not coverage.get(module, {}).get("module_contract") or context.get("build_mode") != "revision"
            or context.get("workflow_sequence") != "design_revision" or context.get("revision_scope") != "feature"):
        return None
    _matches_reviewed_task(task, context)
    source = revision_source_artifact_id(context)
    if not source or source != context.get("revision_source_artifact_version_id"):
        raise ValueError("Result module compilation requires the exact accepted revision source")
    record, files = await read_revision_source(context, source, require_accepted=True)
    if revision_result_module_coverage(context, files) != coverage:
        raise ValueError("Result module coverage changed since review")
    root = f"modules/{module}/"
    manifest, events, reactions = (root + p for p in ("module.yaml", "contracts/events.yaml", "contracts/reactions.yaml"))
    owned = set(task.get("owned_paths") or [])
    required = {manifest, events, reactions}
    if (not required <= owned or task.get("surface_id") != module or task.get("surface_kind") != "module"
            or task.get("initial_agent") != "ConfigMiddlewareAgent" or task.get("integration_needs")):
        return None
    from mozaiksai.core.workflow.task_batches import optional_task_output_paths

    companions = optional_task_output_paths(task) - required
    impact = (context.get("impact_set") or {}).get("affected_bundle_paths") or []
    selected = ((context.get("refinement_request_meta") or {}).get("extra") or {}).get("selected_file_paths") or []
    if (not isinstance(selected, (list, tuple)) or not isinstance(impact, (list, tuple)) or not impact
            or not all(isinstance(path, str) for path in [*impact, *selected]) or not owned <= (companions | required)
            or any(fnmatchcase(path, pattern) for path in companions for pattern in [*impact, *selected])):
        return None
    for path in owned - required:
        # Optional compiled companions survive through the ordinary baseline overlay. A request to edit any
        # of them, or to create a new companion, needs its normal author and cannot be satisfied by copying.
        if path not in files:
            return None
    for path in owned:
        current = (context.get("generated_files") or {}).get(path)
        if (current is not None and current != files.get(path)) or path in (context.get("deleted_files") or []):
            return None
    compilation = await resolve_app_compilation_inputs(
        app_id=record.app_id, build_record_id=record.id, owner_user_id=context.get("user_id"),
        build_registry_id=require_build_binding(context).build_registry_id,
    )
    if not compilation:
        return None
    prior = compilation.context_for(record.app_id)
    before, after = _module_surface(prior, module), _module_surface(context, module)
    if not before or not after or after.get("source_capability_packs") or before.get("primary_entities") != after.get("primary_entities"):
        return None
    if before.get("integrations") or after.get("integrations") or before.get("workflow_triggers") or after.get("workflow_triggers"):
        return None
    for state in (prior, context):
        pack = [p for p in (state.get("app_build_plan") or {}).get("capability_packs", []) if p.get("surface_id") == module]
        policy = resolve_design_access_policy(state) or {}
        if (len(pack) != 1 or pack[0].get("capability_source") != "generated_module" or pack[0].get("capability_pack_id") != module
                or policy.get("roles") or policy.get("grants") or (resolve_subscription_contract(state) or {}).get("contract_required")):
            return None
    data = detach(context.get("data_contract"))
    writes = set(canonical_write_actions_for_surface(after, data))
    if set(after.get("owned_mutations") or []) - writes:
        return None
    bindings = {(p["event_type"], p["result_write"]["source_action"], w["capability_id"]): p["result_write"]
                for w in pinned_result_workflows(context) for p in w["result_write_plans"] if p["module_id"] == module}
    flows = [f for f in (context.get("app_build_plan") or {}).get("event_flows") or [] if f.get("producer_pack_id") == module]
    if any(set(f.get("subscriber_intents") or []) - {"workflow"} for f in flows):
        return None
    if {(f["event_type"], f["producing_action"], capability) for f in flows
            for capability in f.get("workflow_capability_ids") or [None]} != bindings.keys():
        return None
    grants = list(bindings.values())
    if not grants or not _only_result_fields(module, files, data, grants):
        return None
    module_yaml = yaml.safe_load(files[manifest])
    actions = {a["id"]: a for a in module_yaml.get("actions", [])}
    callers = retained_ask_callers or {}
    custom = set(after.get("custom_reads") or [])
    source_build_id = record.commit_metadata.metadata.get("build_id")
    if (not isinstance(source_build_id, str) or not source_build_id.strip()
            or custom != set(before.get("custom_reads") or []) or custom != set(callers)
            or any(not read.source_accepted or read.source_action != actions.get(name)
                   or read.source_build_id != source_build_id
                   for name, read in callers.items())):
        return None
    compiled = writes | set(canonical_read_actions_for_surface(after, data))
    if set(actions) - compiled - custom - {g["action"] for g in grants}:
        return None
    if (module_yaml.get("permissions") or any(a.get("permissions") or a.get("entitlement_gate") for a in actions.values())
            or any(c.get("permissions") for c in module_yaml.get("capabilities", []))):
        return None
    event_yaml = yaml.safe_load(files[events]) if events in files else None
    accepted_events = {e["type"] for e in (event_yaml or {}).get("events", [])}
    required_events = {event for event, _, _ in bindings}
    if set(after.get("events_emitted") or []) != accepted_events or not required_events <= accepted_events:
        return None
    # Keep the accepted Ask action and other inherited metadata verbatim. The normal write/read closure
    # and compile_result_task_bindings will introduce the reviewed CRUD, internal receiver and reaction.
    bundle = {"module_id": module, "module_yaml": module_yaml, "events_yaml": event_yaml}
    if reactions in files:
        bundle["reactions_yaml"] = yaml.safe_load(files[reactions])
    retained = []
    size = 0
    for path in sorted(companions & files.keys()):
        content = files[path]
        if not isinstance(content, str):
            return None
        raw = content.encode()
        size += len(raw)
        if size > 32_000:
            return None
        retained.append({"path": path, "sha256": hashlib.sha256(raw).hexdigest(), "document": yaml.safe_load(content)})
    modules = sorted(path.split("/")[1] for path in files
                     if path.startswith("modules/") and path.endswith("/module.yaml") and path.count("/") == 2)
    return VerifiedResultModuleSource(
        {"mode": "module_contract_bundle", "module_contract": bundle}, source_build_id, record.id, modules, retained,
    )
