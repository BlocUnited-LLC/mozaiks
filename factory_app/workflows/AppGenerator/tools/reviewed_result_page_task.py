"""Offer an additive editor for a single collection on a verified, retained app page."""

from __future__ import annotations

import json
from dataclasses import dataclass
from fnmatch import fnmatchcase
from typing import Any

import yaml

from factory_app.workflows._shared.revision_app_source import (
    read_revision_source,
    revision_source_artifact_id,
)
from factory_app.workflows.AppGenerator.tools.reviewed_result_module_task import (
    require_reviewed_build_task,
)
from mozaiksai.core.workflow.context.frozen import detach
from mozaiksai.core.workflow.generator_support.code_files import planned_page_path
from mozaiksai.core.workflow.generator_support.module_action_inventory import (
    pinned_result_workflows,
)
from mozaiksai.core.workflow.generator_support.module_revision import (
    retained_workflow_page_bindings,
)
from mozaiksai.core.workflow.generator_support.page_action_bindings import (
    planned_page_writes,
    reachable_page_action_keys,
    validate_required_page_writes,
)
from mozaiksai.core.workflow.generator_support.page_binding_construction import (
    canonical_write_ids,
    listed_collections,
    record_identity_field,
)
from mozaiksai.core.workflow.generator_support.page_data_bindings import (
    iter_data_bound_sections,
    schema_at_path,
)
from mozaiksai.core.workflow.generator_support.page_plan_utils import (
    module_action_index_from_context,
    normalize_planned_page_content,
    validate_planned_page,
    workflow_names_from_context,
)


@dataclass(frozen=True)
class VerifiedResultPageSource:
    candidate: dict[str, Any]
    source_files: dict[str, str]
    source_build_id: str
    source_artifact_version_id: str
    module_id: str
    page_path: str


_TABLES = {"DataTable", "ResourceTable"}


def _reviewed_controls(page: dict[str, Any], planned: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """A structured table/selected-form hint names the exact read and write this offer can implement."""
    def sections(rows: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]] | None:
        tables = [s for s in rows if s.get("primitive") in _TABLES]
        forms = [s for s in rows if s.get("primitive") == "Form"]
        return (tables[0], forms[0]) if len(rows) == 2 and len(tables) == len(forms) == 1 else None

    design, plan = sections(page.get("sections") or []), sections(planned.get("sections_hint") or [])
    if not design or not plan or design[0]["primitive"] != plan[0]["primitive"]:
        return None
    if any(d.get("data_source") is not None and d["data_source"] != p.get("data_source") for d, p in zip(design, plan, strict=True)):
        return None
    try:
        hints = [json.loads(entry.get("config_hint") or "null") for entry in (*design, *plan)]
    except (ValueError, TypeError):
        return None
    if not all(isinstance(h, dict) for h in hints) or hints[:2] != hints[2:]:
        return None
    table, form = hints[:2]
    if (set(table) - {"columns", "selection", "data_source"} or table.get("selection") != "single"
            or ("data_source" in table and table["data_source"] != plan[0].get("data_source"))
            or not isinstance(table.get("columns"), list) or not all(isinstance(c, str) for c in table["columns"])
            or form.get("data_source") != plan[1].get("data_source") or not isinstance(form.get("fields"), list)):
        return None
    return {**table, "data_source": plan[0].get("data_source")}, form


def _matches_editor(hint: dict[str, Any], collection: dict[str, Any], identity: str, inputs: dict[str, Any]) -> bool:
    # The source selector is a design hint. The runtime compiler binds it via
    # selected_row and owns the modal's title, size and field controls.
    selector = identity + "_source"
    if (set(hint) - {"data_source", "fields", "size", "title", selector}
            or hint.get("size", "medium") != "medium"
            or hint.get("title", f"Edit {collection['entity']}") != f"Edit {collection['entity']}"
            or hint.get(selector, f"selected_{identity}") != f"selected_{identity}"):
        return False
    fields = hint["fields"]
    if not fields or any(not isinstance(f, dict) or set(f) != {"name", "type"} or not isinstance(f["name"], str)
                         for f in fields):
        return False
    declared = {f["name"]: f["type"] for f in fields}
    return (len(declared) == len(fields) and set(inputs) == {identity, *declared} and identity not in declared
            and all(isinstance(inputs[k], dict) and inputs[k].get("type") == kind for k, kind in declared.items()))


def _extend_columns(table: dict[str, Any], wanted: list[str], allowed: set[str], output: dict[str, Any]) -> bool:
    """Only add reviewed scalar fields already returned on each row of the same public list action."""
    config = table["config"]
    items = schema_at_path(output, config.get("data_key"))
    if not items or items.get("type") != "array" or not isinstance(items.get("items"), dict):
        return False
    columns = config.get("columns")
    if not isinstance(columns, list) or not all(isinstance(c, dict) and isinstance(c.get("key"), str) for c in columns):
        return False
    existing = {c["key"] for c in columns}
    if len(set(wanted)) != len(wanted) or set(wanted) - existing - allowed:
        return False
    added = []
    for key in wanted:
        if key in existing:
            continue
        field = schema_at_path(items["items"], key) or {}
        field_type = field.get("type")
        types = {field_type} if isinstance(field_type, str) else set(field_type or []) - {"null"}
        if len(types) != 1 or not types <= {"number", "integer", "string", "boolean"}:
            return False
        added.append({"key": key, "label": key.replace("_", " ").title()})
    columns.extend(added)
    return True


async def reviewed_result_page_task_output(
    task: dict[str, Any], base_context: Any, task_context: Any,
) -> VerifiedResultPageSource | None:
    retained = detach(base_context.get("retained_workflow_page_bindings")) or {}
    owned = set(task.get("owned_paths") or [])
    matches = owned & retained.keys()
    if (task.get("task_type") != "page_bundle" or task.get("initial_agent") != "AppSchemaAgent"
            or len(matches) != 1 or owned - matches - {"app.json"} or task.get("integration_needs")):
        return None
    path, = matches
    require_reviewed_build_task(task, base_context)
    source = revision_source_artifact_id(base_context)
    if not source or source != base_context.get("revision_source_artifact_version_id"):
        raise ValueError("Retained collection page requires the exact accepted revision source")
    record, files = await read_revision_source(base_context, source, require_accepted=True)
    if retained_workflow_page_bindings(base_context, files) != retained:
        raise ValueError("Retained workflow page scope changed since source verification")
    selected = ((base_context.get("refinement_request_meta") or {}).get("extra") or {}).get("selected_file_paths") or []
    impact = (base_context.get("impact_set") or {}).get("affected_bundle_paths") or []
    if (retained[path].get("partial_ask_context") or any(not isinstance(files.get(k), str) for k in owned)
            or any(k in (base_context.get("deleted_files") or []) for k in owned)
            or any((base_context.get("generated_files") or {}).get(k, files[k]) != files[k] for k in owned)
            or any(fnmatchcase("app.json", pattern) for pattern in (*impact, *selected))):
        return None
    build_id = record.commit_metadata.metadata.get("build_id")
    if not isinstance(build_id, str) or not build_id:
        return None
    old = {k: v for k, v in files.items() if k in owned and isinstance(v, str)}
    planned = [p for p in detach(base_context.get("app_build_plan"))["pages"] if planned_page_path(p) == path]
    designs = [p for p in (detach(base_context.get("experience_spec")) or {}).get("pages", []) if planned_page_path(p) == path]
    if (len(planned) != 1 or len(designs) != 1 or planned[0].get("ui_surface") != "declarative_page"
            or designs[0].get("route") != planned[0].get("route")):
        return None
    controls = _reviewed_controls(designs[0], planned[0])
    if not controls:
        return None
    table_hint, form_hint = controls
    document = yaml.safe_load(old[path])
    tables = [(section, key) for section, key, _ in iter_data_bound_sections(document) if section.get("primitive") in _TABLES]
    if len(tables) != 1 or document.get("route") != planned[0]["route"]:
        return None
    table, key = tables[0]
    if not key:
        return None
    module, _, list_action = key.partition("/")
    ref = {"module_id": module, "action_id": list_action}
    collection = listed_collections(base_context.get("data_contract")).get((module, list_action))
    if not collection or table_hint["data_source"] != ref:
        return None
    actions = canonical_write_ids(collection)
    if set(actions) != {"create", "update"} or form_hint.get("data_source") != {**ref, "action_id": actions["update"]}:
        return None
    writes = planned_page_writes(planned, base_context)
    create, edit = (f"{module}/{actions[op]}" for op in ("create", "update"))
    if writes.get(planned[0]["route"]) != {create, edit} or reachable_page_action_keys([document], include_reads=False) != {create}:
        return None
    grants = [g["result_write"] for w in pinned_result_workflows(base_context) for g in w["result_write_plans"]
              if g["module_id"] == module and g["result_write"]["collection"] == collection["name"]]
    if len(grants) != 1 or grants[0]["source_action"] != actions["create"]:
        return None
    modules = module_action_index_from_context(task_context)
    public = modules.get(module, {})
    identity = record_identity_field(collection, public)
    if identity != grants[0]["record_key"] or not identity or not {list_action, *actions.values()} <= public.keys():
        return None
    inputs = public[actions["update"]].get("input_schema", {}).get("properties", {})
    result_fields = set(grants[0]["fields"])
    if (not _matches_editor(form_hint, collection, identity, inputs) or result_fields & set(inputs)
            or not result_fields <= set(table_hint["columns"])
            or not _extend_columns(table, table_hint["columns"], {identity, *result_fields}, public[list_action]["output_schema"])):
        return None
    try:
        content = normalize_planned_page_content(
            yaml.safe_dump(document, sort_keys=False, allow_unicode=True), path=path, modules=modules,
            data_contract=detach(base_context.get("data_contract")), design_surface_map=detach(base_context.get("design_surface_map")),
            workflow_names=workflow_names_from_context(task_context),
        )
        # The normal compiler can also construct controls for unrelated eligible
        # mutations. This offer only covers the two owner-reviewed public actions.
        if reachable_page_action_keys([yaml.safe_load(content)], include_reads=False) != {create, edit}:
            return None
        validate_planned_page(content, planned[0], path)
        validate_required_page_writes(content, planned[0]["route"], writes, path, identity_validated=True)
    except ValueError:
        return None
    updated = {**old, path: content}
    return VerifiedResultPageSource(
        candidate={"code_files": [{"filename": k, "content": v} for k, v in sorted(updated.items())], "deleted_files": []},
        source_files=old, source_build_id=build_id,
        source_artifact_version_id=record.id, module_id=module, page_path=path,
    )


_COPY_FIELDS = {"PageHeader": "subtitle", "Modal": "description"}
MAX_PAGE_COPY_EDITS = 8
MAX_PAGE_COPY_LENGTH = 512


def _literal_display_text(value: str) -> bool:
    return bool(value.strip()) and len(value) <= MAX_PAGE_COPY_LENGTH and value.isprintable() and all(
        token not in value for token in "<>{}"
    )


def _retained_copy_targets(document: dict[str, Any]) -> dict[str, tuple[tuple[str | int, ...], str, str]]:
    targets: dict[str, tuple[tuple[str | int, ...], str, str]] = {}
    seen: set[str] = set()

    def visit(sections: Any, path: tuple[str | int, ...]) -> None:
        for index, section in enumerate(sections if isinstance(sections, list) else []):
            if not isinstance(section, dict):
                continue
            section_id = section.get("id")
            config = section.get("config") or {}
            location = (*path, index, "config")
            field = _COPY_FIELDS.get(section.get("primitive", ""))
            if isinstance(section_id, str) and section_id.strip():
                if section_id in seen:
                    targets.pop(section_id, None)
                elif field and isinstance(config.get(field), str) and _literal_display_text(config[field]):
                    targets[section_id] = ((*location, field), field, config[field])
                seen.add(section_id)
            visit(config.get("children"), (*location, "children"))

    visit(document.get("sections"), ("sections",))
    return targets


def reviewed_page_copy_fields(source: VerifiedResultPageSource, proposed_files: dict[str, str]) -> list[dict[str, str]]:
    """List existing visible descriptions that survived the accepted-page compiler unchanged."""
    if set(proposed_files) != source.source_files.keys() or any(
        proposed_files[k] != v for k, v in source.source_files.items() if k != source.page_path
    ):
        raise ValueError("Retained page offer has a different file inventory or app manifest")
    old = _retained_copy_targets(yaml.safe_load(source.source_files[source.page_path]))
    new = _retained_copy_targets(yaml.safe_load(proposed_files[source.page_path]))
    return [{"section_id": key, "field": spec[1], "expected": spec[2]} for key, spec in old.items()
            if key in new and new[key][1:] == spec[1:]]


def _yaml_text_span(document: yaml.Node, path: tuple[str | int, ...]) -> tuple[int, int]:
    node = document
    for key in path:
        if isinstance(key, int) and isinstance(node, yaml.SequenceNode):
            node = node.value[key]
        elif isinstance(key, str) and isinstance(node, yaml.MappingNode):
            values = [v for k, v in node.value if isinstance(k, yaml.ScalarNode) and k.value == key]
            if len(values) != 1:
                raise ValueError("Ambiguous copy target in retained YAML")
            node = values[0]
        else:
            raise ValueError("Invalid copy target in retained YAML")
    if not isinstance(node, yaml.ScalarNode) or node.tag != "tag:yaml.org,2002:str":
        raise ValueError("Copy target must be a literal string")
    return node.start_mark.index, node.end_mark.index


def apply_reviewed_page_copy(
    source: VerifiedResultPageSource, proposed_files: dict[str, str], edits: list[dict[str, str]],
) -> dict[str, str]:
    """Change only the selected literal scalar spans, retaining every other byte."""
    fields = {(r["section_id"], r["field"]): r["expected"] for r in reviewed_page_copy_fields(source, proposed_files)}
    if not isinstance(edits, list) or len(edits) > min(MAX_PAGE_COPY_EDITS, len(fields)):
        raise ValueError("Invalid number of retained page copy edits")
    if not edits:
        return dict(proposed_files)
    text = proposed_files[source.page_path]
    if any(isinstance(token, yaml.AliasToken) for token in yaml.scan(text)):
        raise ValueError("Retained copy edits require independent literal values")
    root = yaml.compose(text, Loader=yaml.SafeLoader)
    if root is None:
        raise ValueError("Missing compiled page")
    decoded = yaml.safe_load(text)
    targets = _retained_copy_targets(decoded)
    replacements = []
    for edit in edits:
        if not isinstance(edit, dict) or set(edit) != {"section_id", "field", "expected", "replacement"} or not all(
            isinstance(v, str) for v in edit.values()
        ):
            raise ValueError("Invalid retained page copy target")
        key = (edit["section_id"], edit["field"])
        replacement = edit["replacement"]
        if (key not in fields or fields.pop(key) != edit["expected"] or replacement == edit["expected"]
                or not _literal_display_text(replacement)):
            raise ValueError("Unknown, duplicate, stale or invalid retained page copy edit")
        path = targets[key[0]][0]
        start, end = _yaml_text_span(root, path)
        # A folded/block scalar can include its final line break in the YAML
        # span. Preserve it so the following key or section stays on its line.
        original_span = text[start:end]
        end -= len(original_span) - len(original_span.rstrip("\r\n"))
        replacements.append((start, end, json.dumps(replacement, ensure_ascii=False)))
        parent = decoded
        for part in path[:-1]:
            parent = parent[part]
        parent[path[-1]] = replacement
    for start, end, literal in sorted(replacements, reverse=True):
        text = text[:start] + literal + text[end:]
    if yaml.safe_load(text) != decoded:
        raise ValueError("Copy edit changed another YAML value")
    return {**proposed_files, source.page_path: text}
