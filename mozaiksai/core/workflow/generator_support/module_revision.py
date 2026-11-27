"""Recognize reviewed backend-only revisions of an admitted app bundle."""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from fnmatch import fnmatchcase
from typing import Any

import yaml

from mozaiksai.core.app_context.source_corpus import redact_source_text
from mozaiksai.core.runtime.app.page_schema import AppPageMeta
from mozaiksai.core.runtime.persistence.intent_loader import iter_data_contract_collections
from mozaiksai.core.workflow.context.frozen import detach
from mozaiksai.core.workflow.generator_support.code_files import (
    _materialize_schema_contract,
    planned_page_path,
    safe_relpath,
)
from mozaiksai.core.workflow.generator_support.data_contract_fields import (
    DATE_FIELD_TYPES,
    record_id_field,
)
from mozaiksai.core.workflow.generator_support.module_action_inventory import (
    canonical_read_action_id,
    canonical_read_actions_for_surface,
    canonical_write_actions_for_surface,
    module_action_index,
    pinned_result_workflows,
)
from mozaiksai.core.workflow.generator_support.module_authored_code import prune_repository
from mozaiksai.core.workflow.generator_support.page_action_bindings import (
    reachable_page_action_keys,
)
from mozaiksai.core.workflow.generator_support.page_data_bindings import schema_at_path

_SERVICE_PATH = re.compile(r"modules/([^/]+)/backend/(?:repo|service)\.py")
_PAGE_SCHEMA_PATH = re.compile(r"ui/pages/(?:[^/]+/page|[^/]+)\.ya?ml")


def _collection_read_identity(module: str, collection: dict[str, Any]) -> tuple[Any, ...]:
    fields = [field["name"] for field in collection["fields"] if isinstance(field, dict) and isinstance(field.get("name"), str)]
    return (module, collection["name"], collection["entity"], collection["tenancy"],
            collection.get("owner_field"), record_id_field(collection, fields))


def legacy_optional_updated_at_reads(files: Mapping[str, str | bytes], data_contract: Any) -> dict[str, list[str]]:
    """Project prior canonical record reads whose accepted schema permits a missing timestamp."""
    current = {
        _collection_read_identity(module, collection)
        for module, kind, collection in iter_data_contract_collections(detach(data_contract))
        if kind == "module" and collection.get("scope") == "app"
        and any(isinstance(f, dict) and f.get("name") == "updated_at" and f.get("type") in DATE_FIELD_TYPES and f.get("required") is True
                for f in collection.get("fields") or [])
    }
    if not current or "data/contract.json" not in files:
        return {}
    source_contract = json.loads(files["data/contract.json"])
    result: dict[str, list[str]] = {}
    for module, kind, collection in iter_data_contract_collections(source_contract):
        if kind != "module" or collection.get("scope") != "app":
            continue
        name = collection["name"]
        key = _collection_read_identity(module, collection)
        if key not in current:
            continue
        path = f"modules/{module}/module.yaml"
        source = files.get(path)
        if not isinstance(source, str):
            continue
        manifest = yaml.safe_load(source)
        identity = manifest.get("module") if isinstance(manifest, dict) else None
        if not isinstance(identity, dict) or identity.get("id") != module:
            continue
        record_key = key[-1]
        # Compound JSON schemas can impose guarantees outside the local required list.
        object_keys = {"type", "properties", "required", "additionalProperties", "title", "description"}
        for operation in ("get", "list"):
            action_id = canonical_read_action_id(name, operation)
            rows = [row for row in manifest.get("actions") or [] if isinstance(row, dict) and row.get("id") == action_id]
            if len(rows) != 1 or rows[0].get("handler_method") != action_id:
                continue
            schema = rows[0].get("output_schema")
            if not isinstance(schema, dict) or schema.get("type") != "object" or not schema.keys() <= object_keys:
                continue
            record = schema_at_path(schema, "item" if operation == "get" else "items")
            if operation == "list":
                record = record.get("items") if record and record.get("type") == "array" and (
                    record.keys() <= {"type", "items", "title", "description"}
                ) else None
            if not isinstance(record, dict) or record.get("type") != "object" or not record.keys() <= object_keys:
                continue
            required = record.get("required", [])
            if (schema_at_path(record, record_key) is not None and isinstance(required, list)
                    and all(isinstance(field, str) for field in required) and "updated_at" not in required):
                result.setdefault(module, []).append(action_id)
    return {module: sorted(reads) for module, reads in result.items()}


def bind_legacy_read_timestamps(plan: dict[str, Any], context: Any) -> None:
    """Only the baseline loader can set this projection, including on a preloaded plan."""
    plan.pop("legacy_optional_updated_at_reads", None)
    if context is not None and context.get("build_mode") == "revision" and context.get("revision_source_artifact_version_id"):
        if reads := detach(context.get("legacy_optional_updated_at_reads")):
            plan["legacy_optional_updated_at_reads"] = reads


def retained_workflow_page_bindings(context: Any, files: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """Keep an existing page's workflow trigger and reviewed Ask reads on an indirect, additive edit."""
    from .design_access_policy import (
        resolve_authored_module_access_policy,
        resolve_reviewed_page_writes,
    )
    from .module_role_grants import approved_module_role_permissions

    if (context.get("revision_scope") != "feature" or context.get("workflow_sequence") != "design_revision"
            or (context.get("change_intent") or {}).get("requires_workflow_revision") is not True
            or not context.get("design_docs_build_artifact")):
        return {}
    affected = (context.get("impact_set") or {}).get("affected_bundle_paths") or []
    events = {
        (plan["module_id"], plan["result_write"]["source_action"])
        for workflow in pinned_result_workflows(context) for plan in workflow["result_write_plans"]
    }
    if not events or not affected:
        return {}
    writes = resolve_reviewed_page_writes(context)
    if not writes:
        return {}
    surfaces = (context.get("design_surface_map") or {}).get("surfaces") or []
    permissions = approved_module_role_permissions(
        resolve_authored_module_access_policy(context), data_contract=context.get("data_contract"),
        design_surface_map=context.get("design_surface_map"),
    )
    accepted_actions = module_action_index({p: v for p, v in files.items() if isinstance(v, str) and p.endswith("/module.yaml")})
    result = {}
    for page in (context.get("experience_spec") or {}).get("pages") or []:
        route, path = page["route"], planned_page_path(page)
        if route not in writes or not any(fnmatchcase(path, pattern) for pattern in affected):
            continue
        source = files.get(path)
        document = yaml.safe_load(source) if isinstance(source, str) else None
        if not isinstance(document, dict) or document.get("route") != route:
            continue
        reachable = reachable_page_action_keys([document], include_reads=False)
        if all(f"{ref['module_id']}/{ref['action_id']}" in reachable for ref in writes[route]):
            continue
        owners = {s["surface_id"]: s for s in surfaces if s.get("surface_kind") == "module"
                  and page["name"] in (s.get("owned_pages") or [])}
        triggers = [{"module_id": module, "action_id": action} for module, action in sorted(events)
                    if action in (owners.get(module, {}).get("owned_mutations") or [])
                    and action in accepted_actions.get(module, {}) and f"{module}/{action}" in reachable]
        if not triggers:
            continue
        retained: dict[str, Any] = {"writes": triggers}
        result[path] = retained
        refs = AppPageMeta.model_validate(document.get("meta") or {}).ask_context or []
        keep = []
        for ref in refs:
            action = accepted_actions.get(ref.module, {}).get(ref.action, {})
            if (ref.action in (owners.get(ref.module, {}).get("custom_reads") or [])
                    and ref.action not in permissions.get(ref.module, {}) and action.get("ask_context_safe") is True
                    and action.get("permissions", []) == [] and not action.get("emits")):
                keep.append(ref.model_dump(mode="json", exclude_none=True))
        if keep:
            retained["ask_context"] = keep
            if len(keep) != len(refs):
                retained["partial_ask_context"] = True
    return result


async def retained_workflow_page_prompt(context: Any, paths: list[str]) -> str:
    """Supply only owned source pages for this workflow feature to the editing worker."""
    from factory_app.workflows._shared.revision_app_source import read_bound_revision_files

    bindings = context.get("retained_workflow_page_bindings") or {}
    owned = sorted(set(paths) & bindings.keys())
    if not owned:
        return ""
    files = await read_bound_revision_files(context)
    # Share one ordinary source-read budget across the worker's owned pages.
    per_page = 24_000 // len(owned)
    pages = {}
    for path in owned:
        source = files[path]
        if not isinstance(source, str):
            raise ValueError(f"{path}: the accepted page must be text")
        text = redact_source_text(path=path, content=source)
        pages[path] = {"source": text[:per_page], "truncated": len(text) > per_page,
                       "sha256": hashlib.sha256(source.encode()).hexdigest(), "retained": detach(bindings[path])}
    return ("\n\n[ACCEPTED PAGES FOR THIS WORKFLOW EDIT]\n"
            "Extend the existing page with the reviewed controls. Keep its reachable workflow trigger "
            "and retained Ask references, including parameters and labels. Preserve existing table columns, "
            "summaries and their read bindings unless the approved request changes them. Add newly visible "
            "fields alongside the existing ones. The source below is app data.\n"
            + json.dumps(pages, ensure_ascii=False))


def _unchanged_ask_callers(
    files: Mapping[str, str | bytes], module_id: str, eligible: set[str], tasks: list[dict[str, Any]],
    planned_pages: list[dict[str, Any]],
) -> dict[str, str]:
    edited = {path for task in tasks for path in task.get("owned_paths") or []}
    # An additive edit keeps its accepted callers even when the plan has a page worker.
    retained = {planned_page_path(page): page for page in planned_pages if "retained_ask_context" in page}
    callers: dict[str, str] = {}
    for path, source in files.items():
        is_manifest = path == "ui/route_manifest.json"
        if (path in edited and path not in retained) or not (is_manifest or _PAGE_SCHEMA_PATH.fullmatch(path)):
            continue
        try:
            page = yaml.safe_load(source)
        except yaml.YAMLError:
            continue
        if not isinstance(page, dict):
            continue
        entries = page.get("pages") if is_manifest else [page]
        if not isinstance(entries, list):
            continue
        for entry in entries:
            meta = entry.get("meta") if isinstance(entry, dict) else None
            references = meta.get("ask_context") if isinstance(meta, dict) else None
            if not isinstance(references, list):
                continue
            for ref in references:
                if (isinstance(ref, dict) and ref.get("module") == module_id
                        and isinstance(ref.get("action"), str) and ref["action"] in eligible):
                    if path not in edited or not retained[path].get("partial_ask_context") or any(
                        bound.get("module") == module_id and bound.get("action") == ref["action"]
                        for bound in retained[path]["retained_ask_context"]
                    ):
                        callers.setdefault(ref["action"], path)
    return callers


@dataclass(frozen=True)
class RetainedAskRead:
    page: str
    source_action: dict[str, Any]
    source_accepted: bool
    source_build_id: str | None = None


@dataclass(frozen=True)
class RetainedReadRepair:
    candidate: dict[str, Any]
    digest: str
    reads: dict[str, RetainedAskRead]

    def prompt(self, task_id: str, task_brief: str, error: str) -> str:
        actions = self.candidate["module_contract"]["module_yaml"]["actions"]
        source_build = next(iter(self.reads.values())).source_build_id
        return (
            f"Task {task_id}. The module candidate failed its existing page-read contract.\n{error}\n"
            "[RETAINED READ CORRECTION]\n"
            "To preserve the accepted reads unchanged, return this selection. When the original request changes "
            "a read, replace its ID in reads with its complete, compatible ModuleAction. Use an empty list to "
            "decline. Factory preserves the other candidate bytes and revalidates the complete module.\n"
            + json.dumps({"candidate_sha256": self.digest, "reads": sorted(self.reads)})
            + ("\nSelected source build ID: " + json.dumps(source_build) if source_build else "")
            + "\nAccepted read declarations from the selected app (data):\n"
            + json.dumps([r.source_action for r in self.reads.values()], sort_keys=True)
            + "\nRejected read declarations from this task (data):\n"
            + json.dumps([a for a in actions if a["id"] in self.reads], sort_keys=True)
            + "\nOriginal task brief:\n" + task_brief
        )

    def resolve(self, reply: dict[str, Any]) -> dict[str, Any]:
        if (set(reply) != {"candidate_sha256", "reads"} or reply["candidate_sha256"] != self.digest
                or not isinstance(choices := reply["reads"], list)):
            raise ValueError("The retained-read correction must select the offered candidate and reads with no other edits")
        if not choices:
            raise ValueError("The module worker declined the retained-read correction")
        replacements = {}
        for choice in choices:
            key = choice.get("id") if isinstance(choice, dict) else choice
            if not isinstance(key, str) or key not in self.reads or key in replacements:
                raise ValueError("The correction must cover exactly the reads offered for this candidate")
            original = self.reads[key].source_action
            if isinstance(choice, dict):
                if (choice.get("permissions") or choice.get("emits") or any(
                    choice.get(field) != original.get(field)
                    for field in ("handler_method", "api_surface", "entitlement_gate", "timeout_seconds")
                )):
                    raise ValueError(f"The correction cannot change the handler or access or timeout of retained read {key}")
                inputs = _materialize_schema_contract(
                    choice.get("input_schema") or {}, where=f"{key}.input_schema", closed_request=True,
                )
                if inputs != original.get("input_schema", {}):
                    raise ValueError(f"The correction must keep the accepted inputs of retained read {key}")
                output = _materialize_schema_contract(choice.get("output_schema") or {}, where=f"{key}.output_schema")
                accepted = original.get("output_schema") or {}
                if not _retains_read_response(accepted, output):
                    raise ValueError(f"The correction must keep the accepted response of retained read {key}")
                replacements[key] = choice
            else:
                replacements[key] = original
        if replacements.keys() != self.reads.keys():
            raise ValueError("The correction must cover exactly the reads offered for this candidate")
        # Only same-task bytes are reconstructed; all ordinary module gates follow.
        result = deepcopy(self.candidate)
        actions = result["module_contract"]["module_yaml"]["actions"]
        for index, action in enumerate(actions):
            if action["id"] in replacements:
                actions[index] = deepcopy(replacements[action["id"]])
        return result


def _retains_read_response(accepted: dict[str, Any], output: dict[str, Any]) -> bool:
    """Allow optional new response fields only on an open object; keep all existing guarantees."""
    if accepted == output:
        return True
    if (accepted.get("type") != "object" or accepted.get("additionalProperties", True) is not True
            or not isinstance(accepted.get("properties"), dict) or not isinstance(output.get("properties"), dict)):
        return False
    previous = accepted["properties"]
    fields = output["properties"]
    if not previous.keys() <= fields.keys():
        return False
    # JSON Schema required membership is unordered. Compare every other assertion
    # verbatim; more complex contract changes belong to the coordinated revision.
    if set(accepted.get("required", [])) != set(output.get("required", [])):
        return False
    restored = {**output, "properties": {k: fields[k] for k in previous}}
    return {k: v for k, v in accepted.items() if k != "required"} == {
        k: v for k, v in restored.items() if k != "required"
    }


def retained_read_repair(
    rejected_output: str | None, module_id: str, reads: dict[str, RetainedAskRead], error: str | None,
) -> RetainedReadRepair | None:
    if not reads or not rejected_output or not error:
        return None
    candidate = json.loads(rejected_output)
    if (not isinstance(candidate, dict) or candidate.get("mode") != "module_contract_bundle"
            or candidate.get("retain_ask_reads") is not None):
        return None
    contract = candidate.get("module_contract")
    if not isinstance(contract, dict) or contract.get("module_id") != module_id:
        return None
    manifest = contract.get("module_yaml")
    actions = manifest.get("actions") if isinstance(manifest, dict) else None
    if not isinstance(actions, list) or not all(
        isinstance(a, dict) and isinstance(a.get("id"), str) and a["id"].strip() for a in actions
    ):
        return None
    eligible = {}
    diagnostics: set[str] = set()
    for key, read in reads.items():
        if not read.source_accepted:
            continue
        matches = [a for a in actions if a.get("id") == key]
        if len(matches) != 1:
            continue
        action = matches[0]
        if (action.get("permissions") or action.get("emits")
                or any(action.get(k) != read.source_action.get(k) for k in
                       ("handler_method", "api_surface", "entitlement_gate"))):
            continue
        try:
            validate_retained_ask_callers(contract, {key: read})
        except ValueError as exc:
            eligible[key] = read
            diagnostics.update(str(exc).splitlines())
    if not eligible or not set(error.splitlines()).issubset(diagnostics):
        return None
    digest = hashlib.sha256(json.dumps(candidate, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return RetainedReadRepair(candidate, digest, eligible)


def _required_response_types(schema: Any, path: str, *, required: bool = False) -> dict[str, frozenset[str]]:
    """Record required members and array element types at their unambiguous schema paths."""
    if not isinstance(schema, dict):
        return {path: frozenset()} if required else {}
    members: dict[str, frozenset[str]] = {}
    if required:
        types = schema.get("type")
        if isinstance(types, str):
            types = [types]
        members[path] = frozenset(t for t in types if isinstance(t, str)) if isinstance(types, list) else frozenset()
    properties = schema.get("properties", {})
    if isinstance(properties, dict):
        required_keys = {name for name in schema.get("required") or [] if isinstance(name, str)}
        # Open JSON Schemas can require a key without declaring a type for its value.
        for name in [*properties, *sorted(required_keys - properties.keys())]:
            if not isinstance(name, str):
                continue
            member = f"{path}.{name}" if name.isidentifier() else f"{path}[{json.dumps(name)}]"
            members.update(_required_response_types(properties.get(name), member, required=name in required_keys))
    if "items" in schema:
        item = schema["items"]
        typed_items = required and isinstance(item, dict) and bool(item.get("type"))
        members.update(_required_response_types(item, f"{path}[]", required=typed_items))
    return members


def _required_nullable_paths(schema: Any, path: str) -> list[str]:
    """Name guaranteed response keys for which a null value is allowed."""
    return [p for p, types in _required_response_types(schema, path).items() if not p.endswith("[]") and "null" in types]


async def retained_custom_action_context(
    context: Any, module_id: str, tasks: list[dict[str, Any]],
) -> tuple[str, dict[str, RetainedAskRead]]:
    """Show retained action contracts to the worker and check callers outside its revision."""
    from factory_app.workflows._shared.revision_app_source import (
        is_accepted_revision_source,
        read_revision_source,
        revision_baseline_required,
        revision_source_artifact_id,
    )

    if not revision_baseline_required(context):
        return "", {}
    surfaces = (detach(context.get("design_surface_map")) or {}).get("surfaces", [])
    surface = next((s for s in surfaces if s.get("surface_id") == module_id
                    and s.get("owner") == "app" and s.get("surface_kind") == "module"), None)
    if surface is None:
        return "", {}
    contract = context.get("data_contract")
    custom = set(surface.get("custom_reads") or []) | set(surface.get("owned_mutations") or [])
    custom.difference_update(canonical_read_actions_for_surface(surface, contract), canonical_write_actions_for_surface(surface, contract))
    if not custom:
        return "", {}
    artifact, baseline = await read_revision_source(context, revision_source_artifact_id(context))
    path = f"modules/{module_id}/module.yaml"
    source = baseline.get(path)
    if not isinstance(source, str):
        return "", {}  # The approved design can add modules and actions on a revision.
    actions = module_action_index({path: source}, http_only=False).get(module_id, {})
    retained = [actions[name] for name in sorted(custom.intersection(actions))]
    if not retained:
        return "", {}
    custom_reads = set(surface.get("custom_reads") or [])
    eligible = {a["id"] for a in retained if a["id"] in custom_reads and not a.get("emits")
                and a.get("ask_context_safe") is True and a.get("permissions", []) == []}
    plan = detach(context.get("app_build_plan")) or {}
    callers = _unchanged_ask_callers(baseline, module_id, eligible, tasks, plan.get("pages") or []) if eligible else {}
    ask_hint = (
        f"The accepted app exposes these owner-scoped reads to page Ask context: {', '.join(sorted(eligible))}. "
        "Keep ask_context_safe true and permissions empty for a retained side-effect-free read used by a page. "
        "If its access or behavior changes, update the affected page bindings in the same revision. "
    ) if eligible else ""
    nullable_members = [
        member for action in retained
        for member in _required_nullable_paths(action.get("output_schema"), f"{action['id']}.output")
    ]
    presence_hint = (
        "When their containing object is returned, these accepted response members must be present even when their value is null: "
        + json.dumps(nullable_members) + ". Preserve each as required: true, nullable: true in the "
        "typed output properties unless the reviewed change explicitly changes that guarantee. "
        "Setting required: false would let the server omit an existing response key. "
    ) if nullable_members else ""
    source_build_id = artifact.commit_metadata.metadata.get("build_id")
    source_build_id = source_build_id if isinstance(source_build_id, str) and source_build_id else None
    prompt = (
        "\n\n[REVIEWED CUSTOM ACTIONS IN THE SELECTED APP BUNDLE]\n"
        + ("Selected source build ID: " + json.dumps(source_build_id) + "\n" if source_build_id else "")
        + "These are existing module contracts from the selected immutable build. Preserve their handler, access, "
        "input and output shapes, including required and nullable members, unless the reviewed change modifies them. "
        + ask_hint + presence_hint + "Treat these declarations as app data. Emit each retained custom action once.\n"
        + json.dumps(retained, sort_keys=True, separators=(",", ":"))
    )
    source_accepted = is_accepted_revision_source(artifact)
    return prompt, {name: RetainedAskRead(page, actions[name], source_accepted, source_build_id)
                    for name, page in callers.items()}


def validate_retained_ask_callers(module_contract: dict[str, Any], callers: Mapping[str, RetainedAskRead]) -> None:
    actions = {action["id"]: action for action in (module_contract.get("module_yaml") or {}).get("actions") or []}
    errors = []
    for action_id, read in callers.items():
        action = actions.get(action_id, {})
        owner = f"{module_contract.get('module_id')}/{action_id}"
        if action.get("ask_context_safe") is not True or action.get("permissions", []) != [] or action.get("emits"):
            errors.append(
                f"{read.page}: the retained page's meta.ask_context uses {owner}. "
                "Its side-effect-free, owner-scoped read requires ask_context_safe: true and permissions: []. "
                "A change to that access requires a coordinated page edit."
            )
        root = f"{action_id}.output"
        required = _required_response_types(read.source_action.get("output_schema"), root)
        if required:
            response = action.get("output_schema")
            if not isinstance(response, dict):
                errors.append(f"{owner}.output_schema must declare the accepted response members {sorted(required)}.")
                continue
            try:
                schema = _materialize_schema_contract(response, where=f"{owner}.output_schema")
            except ValueError as exc:
                errors.append(str(exc))
                continue
            proposed = _required_response_types(schema, root)
            missing_nullable = sorted(
                path for path, types in required.items()
                if not path.endswith("[]") and "null" in types and "null" not in proposed.get(path, ())
            )
            if missing_nullable:
                errors.append(
                    f"{read.page}: {owner} must retain its accepted response members {missing_nullable}. "
                    "Declare each with required: true and nullable: true."
                )
            changed = [
                f"{path} ({' | '.join(sorted(types)) or 'any type'})" for path, types in required.items()
                if path not in missing_nullable and (path not in proposed or (types and types != proposed[path]))
            ]
            if changed:
                errors.append(
                    f"{read.page}: {owner} must keep the accepted required response members and their JSON types: "
                    + ", ".join(sorted(changed)) + "."
                )
    if errors:
        raise ValueError("\n".join(errors))


def scoped_service_revision_paths(
    context: Any, baseline_files: Mapping[str, str],
) -> frozenset[str]:
    """Use the approved task paths only for a bound implementation-only revision."""
    if (
        context is None
        or context.get("build_mode") != "revision"
        or context.get("workflow_sequence") != "app_revision"
        or not (context.get("revision_source_artifact_version_id") or context.get("artifact_version_id"))
    ):
        return frozenset()
    binding = detach(context.get("run_build_binding"))
    plan = detach(context.get("app_build_plan"))
    if not isinstance(binding, dict) or binding.get("phase") != "refinement" or not isinstance(plan, dict):
        return frozenset()
    tasks = plan.get("build_tasks")
    if not isinstance(tasks, list):
        return frozenset()
    paths: set[str] = set()
    for task in tasks:
        if not isinstance(task, dict) or task.get("task_type") != "business_services":
            return frozenset()
        owned = task.get("owned_paths")
        if not isinstance(owned, list) or not owned:
            return frozenset()
        for raw in owned:
            path = safe_relpath(raw) if isinstance(raw, str) else None
            if not path:
                return frozenset()
            match = _SERVICE_PATH.fullmatch(path)
            if not match or path not in baseline_files or f"modules/{match[1]}/module.yaml" not in baseline_files:
                return frozenset()
            paths.add(path)
    return frozenset(paths)


def validate_scoped_service_repositories(
    files: Mapping[str, str], owned_paths: frozenset[str],
) -> None:
    """Keep repository API checks for preserved authored code without rewriting it."""
    for path in sorted(owned_paths.intersection(files)):
        if not path.endswith("/repo.py"):
            continue
        prefix = path.removesuffix("repo.py")
        module_id = path.split("/")[1]
        prune_repository(
            path, files[path], module_id=module_id, code_owned=set(), prune_unused=False,
            business_sources={
                name: source for name, source in files.items()
                if name.startswith(prefix) and name.endswith(".py") and name != path
            },
        )
