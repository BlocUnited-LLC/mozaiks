"""Validate generated pages against planned identity without rewriting their UI."""

from __future__ import annotations

import json
import logging
import re
from functools import lru_cache
from pathlib import PurePosixPath
from typing import Any, cast

import yaml

from mozaiksai.core.runtime.app.auth_contract import (
    app_auth_route_entries,
    validate_app_auth_contract,
)
from mozaiksai.core.runtime.app.page_schema import PageSchemaValidationError, validate_page_schema
from mozaiksai.core.workflow.context.frozen import detach

from .code_files import (
    _unwrap_output_envelope,
    extract_code_file_map_from_payload,
    extract_deleted_file_paths_from_payload,
    planned_page_path,
    safe_relpath,
)
from .module_action_inventory import all_module_actions, pack_template_module_contracts
from .page_action_bindings import generated_workflow_names, page_workflow_binding_errors
from .page_binding_construction import construct_page_bindings
from .page_data_bindings import page_data_binding_errors, schema_at_path, schema_field_paths

logger = logging.getLogger(__name__)


def _to_plain(value: Any) -> Any:
    """Detach structured output and immutable runtime views for serialization."""
    return detach(value)


def _strip_none(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _strip_none(item)
            for key, item in value.items()
            if item is not None
        }
    if isinstance(value, list):
        return [_strip_none(item) for item in value if item is not None]
    return value


def _key_value_entries_to_dict(value: Any) -> Any:
    """Normalize strict key/value lists into runtime object payloads."""
    value = _to_plain(value)
    if value is None or isinstance(value, dict):
        return _strip_none(value)
    if isinstance(value, list):
        normalized: dict[str, Any] = {}
        for entry in value:
            if not isinstance(entry, dict):
                continue
            key = entry.get("key")
            if not isinstance(key, str) or not key.strip():
                continue
            normalized[str(key)] = _strip_none(entry.get("value"))
        return normalized
    return value


def _normalize_action_data(action: Any) -> Any:
    action = _strip_none(_to_plain(action))
    if not isinstance(action, dict):
        return action
    for field in ("context_variables", "payload"):
        if field in action:
            action[field] = _key_value_entries_to_dict(action.get(field))
    return _strip_none(action)


def _normalize_config_actions(config: dict[str, Any]) -> dict[str, Any]:
    for field in ("action", "submit_action", "cancel_action"):
        if field in config:
            config[field] = _normalize_action_data(config.get(field))
    if isinstance(config.get("actions"), list):
        config["actions"] = [_normalize_action_data(action) for action in config["actions"]]
    empty = config.get("empty")
    if isinstance(empty, dict) and "action" in empty:
        empty["action"] = _normalize_action_data(empty.get("action"))
    return config


_OPTIONAL_STRING_KEYS = {
    "api_endpoint",
    "cancel_label",
    "color",
    "description",
    "event_type",
    "height",
    "href",
    "icon",
    "id",
    "message",
    "placeholder",
    "size",
    "subtitle",
    "submit_label",
    "title",
    "url",
    "variant",
    "width",
    "workflow_id",
}


def _normalize_blank_optional_strings(value: Any) -> Any:
    if isinstance(value, dict):
        normalized: dict[str, Any] = {}
        for key, item in value.items():
            if key in _OPTIONAL_STRING_KEYS and isinstance(item, str) and not item.strip():
                normalized[key] = None
            else:
                normalized[key] = _normalize_blank_optional_strings(item)
        return _strip_none(normalized)
    if isinstance(value, list):
        return [_normalize_blank_optional_strings(item) for item in value]
    return value


def _normalize_page_section(section: Any) -> Any:
    section = _strip_none(_to_plain(section))
    if not isinstance(section, dict):
        return section
    config = section.get("config")
    if isinstance(config, dict):
        config = _normalize_blank_optional_strings(config)
        config = _normalize_config_actions(config)
        children = config.get("children")
        if isinstance(children, list):
            config["children"] = [_normalize_page_section(child) for child in children]
        section["config"] = _strip_none(config)
    if promote_table_primitive(section):
        logger.info(
            "[pages] page section %r promoted DataTable -> ResourceTable for %s",
            section.get("id"),
            sorted(resource_table_only_fields() & set(section.get("config") or {})),
        )
    return _strip_none(section)


def normalize_page_schema(page: Any) -> Any:
    """Normalize typed page serialization before canonical runtime validation."""
    page = _strip_none(_to_plain(page))
    if not isinstance(page, dict):
        return page
    meta = page.get("meta")
    if isinstance(meta, dict) and "routeAuth" in meta:
        meta["routeAuth"] = _normalize_route_auth(meta.get("routeAuth"))
    sections = page.get("sections")
    if isinstance(sections, list):
        page["sections"] = [_normalize_page_section(section) for section in sections]
    return _strip_none(page)


def _normalize_route_auth(route_auth: Any) -> Any:
    route_auth = _strip_none(_to_plain(route_auth))
    if not isinstance(route_auth, dict):
        return route_auth
    if "params" in route_auth:
        route_auth["params"] = _key_value_entries_to_dict(route_auth.get("params"))
    return _strip_none(route_auth)


def _normalize_custom_route_bundle(bundle: Any) -> Any:
    bundle = _strip_none(_to_plain(bundle))
    if not isinstance(bundle, dict):
        return bundle
    route_manifest = bundle.get("route_manifest")
    if isinstance(route_manifest, list):
        normalized_routes: list[Any] = []
        for entry in route_manifest:
            entry = _strip_none(_to_plain(entry))
            if isinstance(entry, dict):
                meta = entry.get("meta")
                if isinstance(meta, dict) and "routeAuth" in meta:
                    meta["routeAuth"] = _normalize_route_auth(meta.get("routeAuth"))
            normalized_routes.append(entry)
        bundle["route_manifest"] = normalized_routes
    page_files = bundle.get("page_files")
    if isinstance(page_files, list):
        bundle["page_files"] = [_strip_none(_to_plain(entry)) for entry in page_files]
    return _strip_none(bundle)


def _slug(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", str(value or "").strip()).strip("_").lower()


def _page_stem_from_path(path: str) -> str | None:
    safe = safe_relpath(path)
    if not safe:
        return None
    pure = PurePosixPath(safe)
    if len(pure.parts) == 3 and pure.parts[:2] == ("ui", "pages") and pure.suffix in {".yaml", ".yml"}:
        return _slug(pure.stem)
    return None


def _page_stems(page: dict[str, Any]) -> set[str]:
    stems = {_slug(str(page.get(key) or "")) for key in ("name", "id", "surface_id")}
    route = str(page.get("route") or "").strip()
    if route and route != "/":
        stems.add(_slug(route.strip("/").split("/")[-1]))
    return stems - {""}


# ResourceTable extends DataTable. A section asking for a field only
# ResourceTable declares is rejected as "Unknown runtime-affecting field is not
# allowed", which failed live builds at ui/pages/habits.yaml and
# ui/pages/dashboard.yaml. The fields are real; only the primitive was wrong.
#
# Derived from the models rather than listed, because a hand-written list got it
# wrong: `search` is declared on AppDataTableConfig too, so promoting on it
# rewrote valid DataTables. Deriving the difference cannot drift from the schema.
#
# Server pagination is left alone: AppResourceTableConfig accepts client
# pagination only, so promoting there trades one rejection for another.
def _resource_table_only_fields() -> frozenset[str]:
    from mozaiksai.core.runtime.app.page_schema import (
        AppDataTableConfig,
        AppResourceTableConfig,
    )

    return frozenset(set(AppResourceTableConfig.model_fields) - set(AppDataTableConfig.model_fields))


@lru_cache(maxsize=1)
def resource_table_only_fields() -> frozenset[str]:
    return _resource_table_only_fields()


def promote_table_primitive(section: Any) -> bool:
    """Give a section the table primitive that supports the fields it uses."""
    if not isinstance(section, dict) or section.get("primitive") != "DataTable":
        return False
    config = section.get("config")
    if not isinstance(config, dict):
        return False
    if not (resource_table_only_fields() & set(config)):
        return False
    if config.get("pagination_mode") == "server":
        return False
    section["primitive"] = "ResourceTable"
    return True


def promote_page_table_primitives(document: Any) -> int:
    """Promote every eligible section in a page document, children included."""
    if not isinstance(document, dict):
        return 0
    promoted = 0

    def walk(sections: Any) -> None:
        nonlocal promoted
        if not isinstance(sections, list):
            return
        for section in sections:
            if promote_table_primitive(section):
                promoted += 1
            if isinstance(section, dict):
                config = section.get("config")
                if isinstance(config, dict):
                    walk(config.get("children"))

    walk(document.get("sections"))
    return promoted


_MODAL_EVENT_TYPES = frozenset({"ui.modal.open", "ui.modal.close"})


def _iter_action_nodes(node: Any):
    """Yield every dict in a section tree, so actions nested anywhere are seen."""
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _iter_action_nodes(value)
    elif isinstance(node, list):
        for item in node:
            yield from _iter_action_nodes(item)


def _collect_modal_ids(document: Any) -> set[str]:
    return {
        str(node.get("id"))
        for node in _iter_action_nodes(document)
        if isinstance(node, dict) and node.get("primitive") == "Modal" and node.get("id")
    }


def materialize_modal_targets(document: Any) -> int:
    """Fold a typed AppEventAction.modal_id into the payload the page serves.

    The action contract names the modal target as its own typed field, because
    a target buried in a free-form payload is a field no schema can require.
    Everything downstream -- the runtime check, the event bus, the Modal
    component -- addresses it as ``payload.modal_id``, so exactly one place
    projects the typed field onto the transport shape, and it is this one.

    Shape-aware on purpose. The save lane folds key/value lists into dicts
    before it gets here; the task-batch lane writes the structured output
    verbatim, so the payload is still a list of {key, value} entries. A
    dict-only fold would silently skip every page the batch lane produces.

    The typed field wins over whatever the payload already carried: it is the
    author's stated target, while a payload entry may be a leftover the
    resolver guessed. Run this before resolve_modal_action_targets so the
    resolver only fills in targets nobody stated.
    """
    if not isinstance(document, dict):
        return 0
    folded = 0

    def walk(node: Any) -> None:
        nonlocal folded
        if isinstance(node, list):
            for item in node:
                walk(item)
            return
        if not isinstance(node, dict):
            return
        if node.get("action_type") == "event":
            # Always remove it, whatever it held. modal_id is a field of the
            # action contract, not of the page the runtime loads -- and the
            # provider's strict schema makes every event action carry the key,
            # so a non-modal event would otherwise arrive at a runtime model
            # that forbids unknown fields and be rejected for a field it was
            # required to send.
            target = node.pop("modal_id", None)
            if node.get("event_type") in _MODAL_EVENT_TYPES and isinstance(target, str) and target.strip():
                _set_payload_entry(node, "modal_id", target.strip())
                folded += 1
        for value in node.values():
            walk(value)

    walk(document.get("sections"))
    return folded


def _set_payload_entry(action: dict[str, Any], key: str, value: str) -> None:
    """Write one payload entry in whichever shape the action already uses."""
    payload = action.get("payload")
    if isinstance(payload, list):
        for entry in payload:
            if isinstance(entry, dict) and entry.get("key") == key:
                entry["value"] = value
                return
        payload.append({"key": key, "value": value})
        return
    if not isinstance(payload, dict):
        payload = {}
        action["payload"] = payload
    payload[key] = value


def resolve_modal_action_targets(document: Any) -> int:
    """Point modal open/close actions at a Modal that exists on the page.

    A live build generated a form whose cancel action closed a modal id nothing
    declared:

        $.sections[1].config.children[0].config.cancel_action.payload.modal_id:
        page_schema.unknown_modal: Modal actions require modal_id referencing a
        Modal on this page.

    Two readings are unambiguous and both are applied: an action inside a Modal
    means that Modal, and on a page with exactly one Modal there is only one
    candidate. Anything else is left for the validator - guessing between
    several modals would silently wire the wrong one.
    """
    if not isinstance(document, dict):
        return 0
    modal_ids = _collect_modal_ids(document)
    if not modal_ids:
        return 0
    sole_modal = next(iter(modal_ids)) if len(modal_ids) == 1 else None
    fixed = 0

    def walk(node: Any, enclosing: str | None) -> None:
        nonlocal fixed
        if isinstance(node, list):
            for item in node:
                walk(item, enclosing)
            return
        if not isinstance(node, dict):
            return
        if node.get("primitive") == "Modal" and node.get("id"):
            enclosing = str(node["id"])
        if node.get("action_type") == "event" and node.get("event_type") in _MODAL_EVENT_TYPES:
            payload = node.get("payload")
            # An action that names no payload at all is the commonest shape of
            # this defect, and it used to be the one shape this skipped: the
            # enclosing Modal is just as unambiguous whether the payload is
            # empty or absent. A live build lost its whole repair budget to four
            # such actions, each inside a Modal this function could see.
            if payload is None:
                payload = {}
            if isinstance(payload, dict):
                current = payload.get("modal_id")
                if not isinstance(current, str) or current not in modal_ids:
                    target = enclosing or sole_modal
                    if target:
                        payload["modal_id"] = target
                        # Attach only once there is something to carry, so an
                        # unresolvable action is left exactly as it was found.
                        node["payload"] = payload
                        fixed += 1
        for value in node.values():
            walk(value, enclosing)

    walk(document.get("sections"), None)
    return fixed


def align_page_name_with_file(document: Any, path: str) -> str | None:
    """Make a page's runtime name match the file it is written to.

    A live build failed with:

        $.name: page_schema.name_mismatch: Page schema name must match the
        requested page. Runtime page name must match file identity 'habits';
        keep the display label in title.

    The name is the page's runtime identity and is fixed by the filename the
    plan assigned. The agent had put the human label there instead. Both the
    correct value and where the label belongs are stated by the validator, so
    apply them: the stem becomes the name, and a label that would otherwise be
    lost moves to title when title is empty.
    """
    if not isinstance(document, dict):
        return None
    stem = PurePosixPath(path).stem if path else ""
    if not stem:
        return None
    current = document.get("name")
    if current == stem:
        return None
    if current and not document.get("title"):
        document["title"] = current
    document["name"] = stem
    return str(current) if current else ""
def _modal_config_fields() -> frozenset[str]:
    from mozaiksai.core.runtime.app.page_schema import _TOP_LEVEL_CONFIG_MODELS

    model = _TOP_LEVEL_CONFIG_MODELS.get("Modal")
    return frozenset(model.model_fields) if model is not None else frozenset()


def _undeclared_modal_refs(document: Any, modal_ids: set[str]) -> list[str]:
    refs: list[str] = []
    for node in _iter_action_nodes(document):
        if not isinstance(node, dict):
            continue
        if node.get("action_type") == "event" and node.get("event_type") in _MODAL_EVENT_TYPES:
            payload = node.get("payload")
            if isinstance(payload, dict):
                value = payload.get("modal_id")
                if isinstance(value, str) and value and value not in modal_ids:
                    refs.append(value)
    return refs


def declare_the_intended_modal(document: Any) -> str | None:
    """Turn the container that was clearly meant to be a dialog into one.

    A page that wires modal open/close actions but declares no Modal fails, and
    it cannot be repaired by pointing the action somewhere - there is nowhere to
    point. Telling the agent the rule did not take, and four retries carrying
    the validator message did not either.

    There is one reading that is not a guess. If every undeclared reference on
    the page names the SAME id, and exactly one container section holds one of
    those references, that container is the dialog the agent was building: it
    holds the form whose cancel closes the modal, and the trigger elsewhere
    opens it. Making it a Modal is what the author already described.

    Anything less determined is refused: several ids, several candidate
    containers, or no container at all. Guessing there would move a section into
    a dialog nobody asked to be hidden.

    Config keys Modal does not accept cannot survive the change - the schema
    forbids unknown runtime-affecting fields - so they are dropped and reported.
    """
    if not isinstance(document, dict):
        return None
    modal_ids = _collect_modal_ids(document)
    if modal_ids:
        return None
    refs = set(_undeclared_modal_refs(document, modal_ids))
    if len(refs) != 1:
        return None
    modal_id = next(iter(refs))

    sections = document.get("sections")
    if not isinstance(sections, list):
        return None
    candidates = [
        section
        for section in sections
        if isinstance(section, dict)
        and isinstance(section.get("config"), dict)
        and isinstance(section["config"].get("children"), list)
        and _undeclared_modal_refs(section, set())
    ]
    if len(candidates) != 1:
        return None

    section = candidates[0]
    allowed = _modal_config_fields()
    config = section.get("config") or {}
    dropped = sorted(set(config) - allowed)
    section["config"] = {key: value for key, value in config.items() if key in allowed}
    if section.get("title") and not section["config"].get("title"):
        section["config"]["title"] = section["title"]
    section["primitive"] = "Modal"
    section["id"] = modal_id
    if dropped:
        logger.info("[pages] modal %r dropped config keys Modal does not accept: %s", modal_id, dropped)
    return modal_id


ModuleActionIndex = dict[str, dict[str, dict[str, Any]]]


def module_action_index(file_map: dict[str, str]) -> ModuleActionIndex:
    """Index declared HTTP action contracts, including their exact output fields."""
    index: ModuleActionIndex = {}
    for path, content in (file_map or {}).items():
        if not str(path).replace("\\", "/").endswith("/module.yaml"):
            continue
        try:
            document = yaml.safe_load(content)
        except yaml.YAMLError:
            continue
        if not isinstance(document, dict):
            continue
        module = document.get("module")
        module_id = str((module or {}).get("id") or "").strip() if isinstance(module, dict) else ""
        if not module_id:
            continue
        actions = document.get("actions")
        index[module_id] = {
            str(action.get("id")).strip(): action
            for action in (actions if isinstance(actions, list) else [])
            if isinstance(action, dict) and str(action.get("id") or "").strip()
            and action.get("api_surface") not in {"internal", "admin_internal"}
        }
    return index


def _page_reference_files(context: Any) -> dict[str, str]:
    """Read admitted files, declared dependencies and pack template module contracts.

    No task authors a module a selected pack's templates provide, so its
    contract is read from the template assembly will apply.
    """
    if context is None:
        return {}
    files = detach(context.get("generated_files")) or {}
    files.update(extract_code_file_map_from_payload({
        "code_files": detach(context.get("code_files")),
    }))
    dependencies = detach(context.get("dependency_task_outputs")) or {}
    for output in dependencies.values():
        files.update(extract_code_file_map_from_payload(output))
    for path in extract_deleted_file_paths_from_payload({"deleted_files": detach(context.get("deleted_files"))}):
        files.pop(path, None)
    files.update(pack_template_module_contracts(context))
    return files


def module_action_index_from_context(context: Any) -> ModuleActionIndex:
    """Read the closed inventory from admitted files and declared dependencies."""
    return _approved_page_actions(context, module_action_index(_page_reference_files(context)))


def workflow_names_from_context(context: Any) -> set[str]:
    return generated_workflow_names(_page_reference_files(context), context)


def _approved_page_actions(context: Any, modules: ModuleActionIndex) -> ModuleActionIndex:
    if context is None or not context.get("design_surface_map"):
        return modules
    approved = all_module_actions(context)
    return {
        module: {action_id: contract for action_id, contract in actions.items() if action_id in approved.get(module, [])}
        for module, actions in modules.items()
    }


def _materialize_table_response_keys(document: Any, contracts: dict[str, dict[str, Any]]) -> int:
    """Fill the canonical list envelope when its declared shape is unambiguous."""
    changed = 0
    for section in _iter_action_nodes(document):
        if section.get("primitive") not in {"DataTable", "ResourceTable"}:
            continue
        config = section.get("config")
        if not isinstance(config, dict):
            continue
        endpoint = config.get("api_endpoint")
        if not isinstance(endpoint, str) or not endpoint.startswith("/api/modules/"):
            continue
        output = contracts.get(endpoint.removeprefix("/api/modules/"), {}).get("output_schema")
        properties = output.get("properties") if isinstance(output, dict) else None
        if not isinstance(properties, dict):
            continue
        arrays = [path for path in schema_field_paths(output) if (schema_at_path(output, path) or {}).get("type") == "array"]
        total = properties.get("total")
        if arrays != ["items"] or not isinstance(total, dict) or total.get("type") != "integer":
            continue
        if config.get("data_key") != "items":
            config["data_key"] = "items"
            changed += 1
        current_total = schema_at_path(output, config.get("total_key"))
        if config.get("pagination_mode") == "server" and (current_total or {}).get("type") != "integer":
            config["total_key"] = "total"
            changed += 1
    return changed


def compile_page_data_sources(
    document: Any,
    modules: ModuleActionIndex,
    *,
    reject_api_endpoints: bool = False,
    workflow_names: set[str] | None = None,
    data_contract: Any = None,
    design_surface_map: Any = None,
    path: str | None = None,
) -> int:
    """Compile explicit module/action pairs; never resolve identities from URLs.

    Authoring boundaries reject runtime endpoint fields. Assembly can also pass
    already compiled documents through this compiler without changing bytes.

    With the approved ``data_contract`` and ``design_surface_map`` the compiler
    also writes the bindings those contracts determine (see
    ``page_binding_construction``) before checking what remains.
    """
    count = 0
    label = path or (str(document.get("name") or "page") if isinstance(document, dict) else "page")
    # Every unresolved reference on the page is collected, so one rejection
    # names them all; the binding checks then run over whatever did resolve.
    reference_errors: list[str] = []

    def walk(node: Any, location: str) -> None:
        nonlocal count
        if isinstance(node, list):
            for index, child in enumerate(node):
                walk(child, f"{location}[{index}]")
            return
        if not isinstance(node, dict):
            return
        mutation = node.get("action_type") in {"submit", "delete"}
        endpoint_key = "href" if mutation else "api_endpoint"
        if reject_api_endpoints and (
            "api_endpoint" in node or (mutation and "href" in node)
        ):
            reference_errors.append(f"{location}: model-authored endpoint URLs are forbidden; choose data_source")
        if "data_source" in node:
            count += 1
        source = node.pop("data_source", None)
        if source is not None:
            if not isinstance(source, dict) or set(source) != {"module_id", "action_id"}:
                reference_errors.append(f"{location}.data_source requires exactly module_id and action_id")
            else:
                module_id, action_id = source["module_id"], source["action_id"]
                if any(
                    not isinstance(value, str)
                    or not re.fullmatch(r"[A-Za-z0-9_.-]+", value)
                    or ".." in value
                    for value in (module_id, action_id)
                ):
                    reference_errors.append(f"{location}.data_source requires canonical identifier strings")
                elif action_id not in modules.get(module_id, {}):
                    # Name the valid choices: one bounded correction cannot
                    # find a real action from a message that lists none.
                    valid = (
                        f"valid action ids for module '{module_id}': {sorted(modules[module_id]) or 'none'}"
                        if module_id in modules
                        else f"module '{module_id}' is unknown; valid module ids: {sorted(modules) or 'none'}"
                    )
                    reference_errors.append(
                        f"{location}.data_source references unknown module/action '{module_id}/{action_id}'; {valid}"
                    )
                elif endpoint_key in node:
                    reference_errors.append(f"{location}: data_source cannot be combined with {endpoint_key}")
                else:
                    node[endpoint_key] = f"/api/modules/{module_id}/{action_id}"
        elif mutation and reject_api_endpoints:
            reference_errors.append(f"{location}: {node['action_type']} actions require data_source")
        for key, child in node.items():
            walk(child, f"{location}.{key}")

    walk(document, str(document.get("name") or "page") if isinstance(document, dict) else "page")
    contracts = {f"{module_id}/{action_id}": action for module_id, actions in modules.items() for action_id, action in actions.items()}
    normalized = _materialize_table_response_keys(document, contracts)
    constructed = construct_page_bindings(
        document, contracts, data_contract=data_contract, design_surface_map=design_surface_map,
        workflow_names=workflow_names, page=label,
    )
    errors = page_data_binding_errors(document, contracts)
    errors.extend(page_workflow_binding_errors(document, workflow_names or set()))
    messages = list(reference_errors)
    if errors:
        messages.append("Page bindings do not match declared contracts: " + "; ".join(errors))
    if messages:
        raise ValueError("; ".join(messages))
    return count or normalized or len(constructed)


def compile_authored_page_files(
    files: dict[str, str], *, payload: Any, context: Any, failures: list[str] | None = None,
) -> dict[str, str]:
    """Close raw page/admin candidates at authoring, preserving admitted readback.

    Every page is closed before any rejection is raised, so a worker's one
    bounded correction sees every page's errors at once. With ``failures``
    supplied, the per-page messages are appended there instead of raised and a
    failed page keeps what compilation made of it (data sources resolved to
    endpoints where they could be), so the caller can run its structural
    checks on it and raise everything together.
    """
    payload = _unwrap_output_envelope(detach(payload))
    bundle = payload.get("module_contract") if isinstance(payload, dict) else None
    typed_admin = (
        f"modules/{bundle['module_id']}/contracts/admin.yaml"
        if isinstance(bundle, dict) and bundle.get("module_id") and bundle.get("admin_yaml") is not None else None
    )
    admitted = detach(context.get("generated_files")) or {}
    admitted.update(extract_code_file_map_from_payload({"code_files": detach(context.get("code_files"))}))
    modules = module_action_index_from_context(context)
    modules.update(module_action_index(files))
    modules = _approved_page_actions(context, modules)
    for path in extract_deleted_file_paths_from_payload(payload):
        match = re.fullmatch(r"modules/([^/]+)/module\.yaml", path)
        if match:
            modules.pop(match[1], None)
    workflows = generated_workflow_names({**_page_reference_files(context), **files}, context)
    data_contract = detach(context.get("data_contract"))
    surface_map = detach(context.get("design_surface_map"))
    compiled = dict(files)
    raise_failures = failures is None
    if failures is None:
        failures = []
    for path, content in files.items():
        admin = re.fullmatch(r"modules/([^/]+)/contracts/admin\.yaml", path)
        if not _page_stem_from_path(path) and not admin:
            continue
        # Typed admin sections already crossed the same strict compiler during
        # extraction. Identical admitted bytes are readback, not new authoring.
        if path == typed_admin or admitted.get(path) == content:
            continue
        document: Any = None
        try:
            try:
                document = yaml.safe_load(content)
            except yaml.YAMLError as exc:
                raise ValueError(f"{path}: authored page sections require valid YAML") from exc
            if not isinstance(document, dict):
                raise ValueError(f"{path}: authored page sections require an object")
            inventory = {admin[1]: modules.get(admin[1], {})} if admin else modules
            if compile_page_data_sources(
                document, inventory, reject_api_endpoints=True, workflow_names=workflows,
                data_contract=None if admin else data_contract, design_surface_map=None if admin else surface_map,
                path=path,
            ):
                compiled[path] = yaml.safe_dump(document, sort_keys=False, allow_unicode=True)
        except ValueError as exc:
            failures.append(_prefixed_failure(path, exc))
            if isinstance(document, dict):
                compiled[path] = yaml.safe_dump(document, sort_keys=False, allow_unicode=True)
    if failures and raise_failures:
        raise ValueError("\n".join(failures))
    return compiled


def _prefixed_failure(path: str, error: BaseException) -> str:
    message = str(error)
    return message if message.startswith(f"{path}:") else f"{path}: {message}"


def normalize_planned_page_content(
    content: str, *, path: str = "", modules: ModuleActionIndex | None = None,
    reject_api_endpoints: bool = False,
    workflow_names: set[str] | None = None,
    data_contract: Any = None,
    design_surface_map: Any = None,
) -> str:
    """Return page YAML with table primitives corrected, or the original.

    Materialized page content is written from the same map it is validated
    from, so the correction has to reach the content rather than only the
    validation - otherwise the file on disk keeps the rejected primitive.
    """
    try:
        document = yaml.safe_load(content)
    except yaml.YAMLError:
        return content
    if not isinstance(document, dict):
        return content
    restructured = _normalize_page_structure(document, path)
    compiled = compile_page_data_sources(
        document, modules or {}, reject_api_endpoints=reject_api_endpoints, workflow_names=workflow_names,
        data_contract=data_contract, design_surface_map=design_surface_map, path=path or None,
    )
    renamed = _align_page_name(document, path)
    if not restructured and not compiled and not renamed:
        return content
    return yaml.safe_dump(document, sort_keys=False, allow_unicode=True)


def normalize_page_structure(content: str, *, path: str = "") -> str:
    """Apply the structural corrections of :func:`normalize_planned_page_content` only.

    A page whose bindings were already rejected still gets these corrections,
    so its schema errors join the same rejection instead of the next attempt.
    """
    try:
        document = yaml.safe_load(content)
    except yaml.YAMLError:
        return content
    if not isinstance(document, dict):
        return content
    restructured = _normalize_page_structure(document, path)
    renamed = _align_page_name(document, path)
    if not restructured and not renamed:
        return content
    return yaml.safe_dump(document, sort_keys=False, allow_unicode=True)


def _normalize_page_structure(document: dict[str, Any], path: str) -> bool:
    promoted = promote_page_table_primitives(document)
    # Before the resolver: a stated target must not be overwritten by a guess.
    materialized = materialize_modal_targets(document)
    declared = declare_the_intended_modal(document)
    retargeted = resolve_modal_action_targets(document)
    if declared:
        logger.info("[pages] %s: declared the intended Modal %r", path or "page", declared)
    if promoted:
        logger.info("[pages] %s: promoted %d DataTable -> ResourceTable", path or "page", promoted)
    if retargeted:
        logger.info("[pages] %s: pointed %d modal action(s) at a declared Modal", path or "page", retargeted)
    if materialized:
        logger.info("[pages] %s: carried %d typed modal target(s) into the payload", path or "page", materialized)
    return bool(promoted or materialized or declared or retargeted)


def _align_page_name(document: dict[str, Any], path: str) -> bool:
    renamed = align_page_name_with_file(document, path)
    if renamed is not None:
        logger.info("[pages] %s: name %r -> file identity", path or "page", renamed)
    return renamed is not None


def page_schema_error_details(error: PageSchemaValidationError, *, expected_name: str | None = None) -> str:
    """Every page-schema diagnostic as ``location: code: message``, joined for one rejection."""
    details = "; ".join(f"{item.location}: {item.code}: {item.message}" for item in error.diagnostics)
    if expected_name and any(item.code == "page_schema.name_mismatch" for item in error.diagnostics):
        details += f" Runtime page name must match file identity {expected_name!r}; keep the display label in title."
    return details


def validate_planned_page(content: str, planned: dict[str, Any], path: str) -> None:
    expected_name = PurePosixPath(path).stem
    try:
        document = yaml.safe_load(content)
        if not isinstance(document, dict):
            raise ValueError("page must be a YAML object")
        page = validate_page_schema(document, expected_name=expected_name)
        if page.route != planned["route"]:
            raise ValueError(f"route must preserve approved {planned['route']!r}")
    except PageSchemaValidationError as error:
        raise ValueError(f"{path}: {page_schema_error_details(error, expected_name=expected_name)}") from error
    except (ValueError, yaml.YAMLError) as error:
        raise ValueError(f"{path}: {error}") from error


def validate_planned_custom_routes(
    files: dict[str, str],
    *,
    pages: list[dict[str, Any]],
    owned_paths: set[str],
    baseline_files: dict[str, str] | None = None,
) -> None:
    """Bind this task's materialized custom routes to its approved page identities.

    The typed bundle materializer owns the registry syntax. This checks its
    route-to-import binding, without interpreting or rewriting authored React.
    A scoped revision may retain other routes only as unchanged baseline entries.
    """
    custom_paths = {path for path in owned_paths if path.startswith("ui/pages/custom/")}
    if not custom_paths:
        return
    planned = {
        planned_page_path(page): page
        for page in pages if page.get("ui_surface") == "custom_react_page"
    }
    unknown = custom_paths - planned.keys()
    if unknown:
        raise ValueError(f"Custom pages have no approved plan identity: {sorted(unknown)}")
    baseline = baseline_files or {}
    combined = {**baseline, **files}
    manifest_path = "ui/route_manifest.json"

    def routes_from(source: dict[str, str]) -> list[dict[str, Any]]:
        try:
            document = json.loads(source.get(manifest_path, "{}"))
            routes = document.get("pages") if isinstance(document, dict) else None
            if not isinstance(routes, list) or any(not isinstance(route, dict) for route in routes):
                raise ValueError("pages must be a list of route objects")
            # Compare preserved entries in the same canonical runtime shape;
            # older archives can still contain typed nulls or key/value lists.
            return cast(list[dict[str, Any]], _normalize_custom_route_bundle({"route_manifest": routes})["route_manifest"])
        except (ValueError, TypeError) as exc:
            raise ValueError(f"{manifest_path}: {exc}") from exc

    routes = routes_from(combined)
    preserved = routes_from(baseline) if manifest_path in baseline else []
    approved_routes = {planned[path]["route"] for path in custom_paths}
    # Assembly adds shared sign-in/callback routes from the app auth contract.
    # Only that exact projection belongs to auth; matching a URL or a component
    # name alone must not authorize an undeclared custom page.
    auth_routes: list[dict[str, Any]] = []
    if "config/auth.yaml" in combined:
        app_manifest = json.loads(combined.get("app.json", "{}"))
        if isinstance(app_manifest, dict) and app_manifest.get("authRequired") is True:
            contract = validate_app_auth_contract(yaml.safe_load(combined["config/auth.yaml"]))
            auth_routes = app_auth_route_entries(contract)
    if manifest_path in owned_paths:
        for route in routes:
            canonical_auth = route in auth_routes and sum(
                entry.get("path") == route.get("path") for entry in routes
            ) == 1
            if route.get("path") not in approved_routes and route not in preserved and not canonical_auth:
                raise ValueError(
                    f"{manifest_path}: unapproved custom route {route.get('path')!r}. "
                    f"Preserve the approved custom routes {sorted(approved_routes)} and their registered page files. "
                    "Sign-in and callback routes must match the canonical auth projection from config/auth.yaml; "
                    "other custom routes require an approved page identity. Do not remove required app behavior."
                )
    registry = combined.get("ui/index.js", "")
    for path in sorted(custom_paths):
        approved = planned[path]["route"]
        matches = [route for route in routes if route.get("path") == approved]
        if len(matches) != 1:
            raise ValueError(f"{path}: custom route must preserve approved {approved!r} exactly once")
        if path not in files:
            raise ValueError(f"{path}: page worker did not materialize its owned custom page")
        component = matches[0].get("component")
        if not isinstance(component, str) or not component:
            raise ValueError(f"{path}: approved route {approved!r} must declare its registry component")
        # These two statements are generated by _build_custom_ui_index, not by
        # the model. Require the actual registered binding to import this file.
        bindings = re.findall(
            rf"\bregisterComponent\s*\(\s*['\"]{re.escape(component)}['\"]\s*,\s*([A-Za-z_$][\w$]*)\s*,",
            registry,
        )
        module_path = "./" + path.removeprefix("ui/").removesuffix(".jsx")
        if len(bindings) != 1 or not re.search(
            rf"(?m)^import\s+{re.escape(bindings[0])}\s+from\s+['\"]{re.escape(module_path)}(?:\.jsx)?['\"]\s*;",
            registry,
        ):
            raise ValueError(f"{path}: approved route {approved!r} must register its canonical page file")


def pack_template_page_errors(
    content: str,
    *,
    path: str,
    modules: ModuleActionIndex,
    workflow_names: set[str] | None = None,
    planned: dict[str, Any] | None = None,
) -> list[str]:
    """Every assembly-time page check, run on a pack template page without rewriting it.

    A selected pack's template is the page that ships, so assembly checks the
    template bytes themselves: the page schema and its module action closure,
    the approved route, each metric and table binding against the declared
    module contracts, and every workflow reference. CI runs each shipped pack
    template through this same function, so a template that would fail here
    cannot merge.
    """
    try:
        document = yaml.safe_load(content)
    except yaml.YAMLError:
        return [f"{path}: template page is not valid YAML"]
    if not isinstance(document, dict):
        return [f"{path}: template page must be a YAML object"]
    errors: list[str] = []
    expected_name = PurePosixPath(path).stem
    action_index = {module_id: frozenset(actions) for module_id, actions in modules.items()}
    try:
        page = validate_page_schema(document, expected_name=expected_name, action_index=action_index)
        if planned is not None and page.route != planned.get("route"):
            errors.append(f"{path}: route {page.route!r} does not match the approved route {planned.get('route')!r}")
    except PageSchemaValidationError as error:
        errors.append(f"{path}: {page_schema_error_details(error, expected_name=expected_name)}")
    contracts = {f"{module_id}/{action_id}": action for module_id, actions in modules.items() for action_id, action in actions.items()}
    bindings = page_data_binding_errors(document, contracts)
    bindings.extend(page_workflow_binding_errors(document, workflow_names or set()))
    if bindings:
        errors.append(f"{path}: Page bindings do not match declared contracts: " + "; ".join(bindings))
    return errors
