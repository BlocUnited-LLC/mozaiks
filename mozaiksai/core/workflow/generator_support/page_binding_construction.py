"""Construct the page bindings the accepted contracts determine, and log each one.

A page author's choice is rejected only when the contracts leave a genuine
judgment gap. Where the accepted module contracts, the approved data contract
and the surface map determine the correct binding, code writes it:

- a metric key that is a dotted path the bound action does not return, whose
  final segment names a returned top-level field, reads that field
  (``items.total`` -> ``total``);
- a metric ``detail_key``/``trend_key`` the bound action does not return is
  cleared; a metric whose own ``id`` names a returned field reads that field;
- a create/edit ``workflow`` action that names no generated workflow is
  replaced by a modal form submitting the collection's one create/update
  action when the bundle has no workflows at all;
- the canonical update of a collection a list table shows gets an Edit row
  action and a modal form that submits the typed action with the selected
  row's identifier and the action's editable fields;
- the canonical create of that collection gets a toolbar action and a modal
  form submitting the create's fields, and the canonical delete gets a Delete
  row action opening a confirmation dialog that posts the selected row's
  identifier.

Canonical writes get these entry points whether or not a plan gates them: a
write every plan includes is still a write the user must be able to make. A
single gated update-shaped write outside the canonical set gets its own row
action and modal form beside the canonical Edit, named after the write; a gated
write named like a canonical create or delete gets that entry point. When the
page already holds a modal form (or confirmation dialog) for the write, the
entry point opens it instead of adding a second one.

Every construction, and every construction the contracts do not permit, is
returned as a note and logged, so a build can be read back to see what the
author wrote and what code filled in.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator, Mapping
from typing import Any

from mozaiksai.core.runtime.persistence.intent_loader import (
    DataContractLoadError,
    iter_data_contract_collections,
)
from mozaiksai.core.workflow.context.frozen import detach

from .module_action_inventory import (
    CANONICAL_WRITE_OPERATIONS,
    canonical_read_action_id,
    canonical_write_action_id,
    collection_has_canonical_writes,
    entity_identifier,
)
from .page_action_bindings import reachable_page_action_keys
from .page_data_bindings import iter_data_bound_sections, schema_at_path, section_metrics

logger = logging.getLogger(__name__)

_TABLE_PRIMITIVES = frozenset({"DataTable", "ResourceTable"})
_FORM_FIELD_TYPES = {"string": "text", "integer": "number", "number": "number", "boolean": "checkbox"}


def canonical_write_ids(collection: Mapping[str, Any]) -> dict[str, str]:
    """The canonical ``{operation}_{entity}`` write ids for a collection's entity."""
    try:
        return {
            operation: canonical_write_action_id(str(collection.get("entity") or ""), operation)
            for operation in CANONICAL_WRITE_OPERATIONS
        }
    except ValueError:
        return {}  # module closure already reported the entity name


def record_identity_field(
    collection: Mapping[str, Any], actions: Mapping[str, Mapping[str, Any]] | None = None,
) -> str | None:
    """Name the declared field that identifies one record of the collection.

    The canonical get read looks a record up by ``search_by`` (or a declared
    ``id``); failing both, a unique single-field index is the contract's own
    statement that the field identifies a record; failing that, the canonical
    update write addresses the record by its one required input.
    """
    declared = collection_fields(collection)
    lookup = collection.get("search_by") or ("id" if "id" in declared else None)
    if isinstance(lookup, str) and lookup in declared:
        return lookup
    unique = [
        str(keys[0].get("field"))
        for index in collection.get("indexes") or [] if isinstance(index, Mapping) and index.get("unique")
        for keys in [index.get("keys")]
        if isinstance(keys, list) and len(keys) == 1 and isinstance(keys[0], Mapping) and keys[0].get("field") in declared
    ]
    if len(unique) == 1:
        return unique[0]
    update = (actions or {}).get(canonical_write_ids(collection).get("update", ""))
    if update is not None:
        required = [name for name, spec in action_input_fields(update).items() if spec.get("required")]
        if len(required) == 1 and required[0] in declared:
            return required[0]
    return None


def collection_fields(collection: Mapping[str, Any]) -> set[str]:
    return {
        str(field.get("name")) for field in collection.get("fields") or [] if isinstance(field, Mapping)
    }


def listed_collections(data_contract: Any) -> dict[tuple[str, str], dict[str, Any]]:
    """Map (module id, canonical list action id) to the approved collection it lists."""
    contract = detach(data_contract)
    if not isinstance(contract, Mapping):
        return {}
    listed: dict[tuple[str, str], dict[str, Any]] = {}
    try:
        for owner_id, owner_kind, collection in iter_data_contract_collections(dict(contract)):
            name = collection.get("name")
            if owner_kind != "module" or not isinstance(name, str):
                continue
            try:
                action_id = canonical_read_action_id(name, "list")
            except ValueError:
                continue
            listed[(str(owner_id), action_id)] = collection
    except (DataContractLoadError, TypeError, ValueError, KeyError):
        return {}
    return listed


def _scalar_type(value: Any) -> str | None:
    if isinstance(value, list):
        kinds = [kind for kind in value if kind != "null"]
        return kinds[0] if len(kinds) == 1 and isinstance(kinds[0], str) else None
    return value if isinstance(value, str) else None


def action_input_fields(action: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """Read an action's declared input fields from either contract property shape."""
    schema = action.get("input_schema")
    if not isinstance(schema, Mapping):
        return {}
    properties = schema.get("properties")
    required = set(schema.get("required") or []) if isinstance(schema.get("required"), list) else set()
    fields: dict[str, dict[str, Any]] = {}
    if isinstance(properties, Mapping):
        for name, spec in properties.items():
            spec = spec if isinstance(spec, Mapping) else {}
            fields[str(name)] = {
                "type": _scalar_type(spec.get("type")), "required": name in required, "enum": spec.get("enum"),
            }
    elif isinstance(properties, list):
        for spec in properties:
            if isinstance(spec, Mapping) and spec.get("name"):
                fields[str(spec["name"])] = {
                    "type": _scalar_type(spec.get("type")), "required": bool(spec.get("required")),
                    "enum": spec.get("enum_values") or spec.get("enum"),
                }
    return fields


def _owned_mutations(surface_map: Any, module_id: str) -> set[str] | None:
    surfaces = surface_map.get("surfaces") if isinstance(surface_map, Mapping) else None
    for surface in surfaces if isinstance(surfaces, list) else []:
        if isinstance(surface, Mapping) and surface.get("surface_id") == module_id:
            return {str(action) for action in surface.get("owned_mutations") or []}
    return None


def mutation_shapes(
    module_id: str,
    actions: Mapping[str, Mapping[str, Any]],
    surface_map: Any,
    identifier: str | None,
    declared_fields: set[str] | None = None,
    canonical_ids: Mapping[str, str] | None = None,
    canonical_writes: set[str] | None = None,
) -> dict[str, list[str]]:
    """Classify the surface's owned mutations by how they address the record.

    Only a mutation whose inputs are all declared fields of the collection is a
    record write. Among those, a create takes no identifier, an update requires
    the identifier and takes other fields, and a delete requires the identifier
    alone. Without a record identity only the canonical create write (named
    ``create_<entity>``) is a create candidate. Canonical writes the data
    contract approves are candidates whether or not the surface map repeats
    them in ``owned_mutations``. Anything else is not classified, so it never
    becomes a constructed entry point.
    """
    shapes: dict[str, list[str]] = {"create": [], "update": [], "delete": []}
    owned = (_owned_mutations(surface_map, module_id) or set()) | set(canonical_writes or ())
    if not owned:
        return shapes
    for action_id in sorted(owned):
        action = actions.get(action_id)
        if action is None or action.get("api_surface") in {"internal", "admin_internal"}:
            continue
        fields = action_input_fields(action)
        allowed = (declared_fields | {identifier}) if declared_fields is not None and identifier else declared_fields
        if not fields or (allowed is not None and not set(fields) <= allowed):
            continue
        if identifier is None:
            if action_id == (canonical_ids or {}).get("create"):
                shapes["create"].append(action_id)
        elif identifier not in fields:
            shapes["create"].append(action_id)
        elif fields[identifier]["required"] and len(fields) > 1:
            shapes["update"].append(action_id)
        elif fields[identifier]["required"]:
            shapes["delete"].append(action_id)
    return shapes


def _label(name: str) -> str:
    return " ".join(part.capitalize() for part in name.replace("-", "_").split("_") if part) or name


def _form_fields(fields: Mapping[str, Mapping[str, Any]], exclude: str | None) -> list[dict[str, Any]] | None:
    result: list[dict[str, Any]] = []
    for name, spec in fields.items():
        if name == exclude:
            continue
        enum = spec.get("enum")
        options = None
        if isinstance(enum, list) and [value for value in enum if value is not None]:
            field_type = "select"
            options = [{"value": value, "label": str(value)} for value in enum if value is not None]
        elif spec.get("type") in _FORM_FIELD_TYPES:
            field_type = _FORM_FIELD_TYPES[str(spec["type"])]
        else:
            return None  # object/array inputs have no declarative form field
        result.append({
            "name": name, "label": _label(name), "type": field_type, "required": bool(spec.get("required")),
            "default_value": None, "placeholder": None, "options": options,
        })
    return result or None


def _modal_id(action_id: str) -> str:
    return f"{action_id}-modal"


def _user_facing(action: Mapping[str, Any]) -> bool:
    return action.get("api_surface") not in {"internal", "admin_internal"}


def _kind(action: Mapping[str, Any]) -> str:
    return "gated" if action.get("entitlement_gate") else "shared"


def _subject(module_id: str, action_id: str, action: Mapping[str, Any]) -> str:
    """Name the write in a note: a gated action quotes its gate, any other is a shared action."""
    gate = action.get("entitlement_gate")
    return f"gated action {module_id}/{action_id} ({gate})" if gate else f"shared action {module_id}/{action_id}"


def _iter_sections(sections: Any) -> Iterator[dict[str, Any]]:
    for section in sections if isinstance(sections, list) else []:
        if isinstance(section, dict):
            yield section
            config = section.get("config")
            if isinstance(config, dict):
                yield from _iter_sections(config.get("children"))


def _existing_modal(sections: Any, matches: Callable[[Mapping[str, Any]], bool]) -> str | None:
    """The id of the first Modal on the page, at any depth, that ``matches``."""
    for section in _iter_sections(sections):
        if section.get("primitive") == "Modal" and isinstance(section.get("id"), str) and matches(section):
            return str(section["id"])
    return None


def _action_modal(
    *, module_id: str, action_id: str, action: Mapping[str, Any], identifier: str, entity: str, edit: bool,
    title: str | None = None,
) -> dict[str, Any] | None:
    fields = _form_fields(action_input_fields(action), identifier if edit else None)
    if fields is None:
        return None
    modal_id = _modal_id(action_id)
    heading = title or f"{'Edit' if edit else 'Create'} {entity}"
    payload = [{"key": identifier, "value": "{selected_row." + identifier + "}"}] if edit else []
    payload.extend({"key": field["name"], "value": "{form." + field["name"] + "}"} for field in fields)
    form_config: dict[str, Any] = {
        "fields": fields, "layout": "vertical", "columns": 1,
        "submit_label": "Save" if edit else "Create",
        "submit_action": {
            "id": f"submit-{action_id}", "label": "Save" if edit else "Create", "variant": "primary",
            "action_type": "submit", "href": f"/api/modules/{module_id}/{action_id}", "payload": payload,
            "requires_selection": False, "closes_modal": True,
        },
        "cancel_label": "Cancel",
        "cancel_action": {
            "id": f"cancel-{action_id}", "label": "Cancel", "action_type": "event", "event_type": "ui.modal.close",
            "payload": {"modal_id": modal_id}, "requires_selection": False, "closes_modal": True,
        },
        "disabled": False,
    }
    if edit:
        form_config = {"initial_values_key": "selected_row", **form_config}
    description = action.get("description")
    return {
        "id": modal_id, "primitive": "Modal", "title": heading,
        "config": {
            "title": heading, "description": description if isinstance(description, str) else None,
            "size": "medium",
            "children": [{
                "id": f"{action_id}-form", "primitive": "Form", "title": heading, "config": form_config,
            }],
        },
    }


def _modal_submits(section: Mapping[str, Any], endpoint: str) -> bool:
    """True when a Modal holds an enabled Form whose submit posts to the endpoint."""
    config = section.get("config")
    children = config.get("children") if isinstance(config, Mapping) else None
    for child in children if isinstance(children, list) else []:
        if not isinstance(child, Mapping) or child.get("primitive") != "Form":
            continue
        form = child.get("config")
        submit = form.get("submit_action") if isinstance(form, Mapping) else None
        if isinstance(form, Mapping) and not form.get("disabled") and isinstance(submit, Mapping):
            if submit.get("action_type") == "submit" and submit.get("href") == endpoint:
                return True
    return False


def _modal_footer_submits(section: Mapping[str, Any], endpoint: str) -> bool:
    """True when a Modal's own footer actions submit to the endpoint.

    Footer actions run without form state, so such a modal collects none of the
    action's inputs and is not a form for it.
    """
    config = section.get("config")
    actions = config.get("actions") if isinstance(config, Mapping) else None
    return isinstance(actions, list) and any(
        isinstance(action, Mapping) and action.get("action_type") == "submit" and action.get("href") == endpoint
        for action in actions
    )


def _ensure_modal(document: dict[str, Any], **modal_args: Any) -> tuple[str | None, str | None, bool]:
    """Return ``(modal id, refusal, existing)`` for a modal form submitting the action.

    A Modal already on the page whose Form submits the action is reused whatever
    its id, so the page never carries two forms for one write. Otherwise a
    section that carries the constructed id is the author's, and the
    construction is refused with a reason rather than layered on top of it.
    """
    sections = document.get("sections")
    if not isinstance(sections, list):
        return None, "the page declares no sections list", False
    action_id = str(modal_args["action_id"])
    modal_id = _modal_id(action_id)
    endpoint = f"/api/modules/{modal_args['module_id']}/{action_id}"
    existing = _existing_modal(sections, lambda section: _modal_submits(section, endpoint))
    if existing is not None:
        return existing, None, True
    if any(section.get("id") == modal_id for section in _iter_sections(sections)):
        return None, f"section '{modal_id}' exists but is not a Modal form submitting {endpoint}", False
    modal = _action_modal(**modal_args)
    if modal is None:
        return None, f"{endpoint} takes an input the Form primitive cannot render (object or array)", False
    sections.append(modal)
    return modal_id, None, False


def _modal_form_clause(document: dict[str, Any], modal_id: str, existing: bool, endpoint: str) -> str:
    """Finish a note: which modal form the new entry point opens."""
    if existing:
        return f"opening the page's existing modal form '{modal_id}' that submits it"
    formless = _existing_modal(document.get("sections"), lambda section: _modal_footer_submits(section, endpoint))
    if formless is None:
        return "and a modal form that submits it"
    return (
        f"and a modal form that submits it; modal '{formless}' submits it from footer actions without a Form, "
        "so it collects no input and is left unopened"
    )


def _confirm_modal(*, module_id: str, action_id: str, identifier: str, entity: str) -> dict[str, Any]:
    """A confirmation dialog whose Delete button posts the selected row's identifier."""
    modal_id = _modal_id(action_id)
    return {
        "id": modal_id, "primitive": "Modal", "title": f"Delete {entity}",
        "config": {
            "title": f"Delete {entity}",
            "description": f"Delete the selected {entity}? This cannot be undone.",
            "size": "small",
            "actions": [
                {
                    "id": f"confirm-{action_id}", "label": "Delete", "variant": "danger", "action_type": "delete",
                    "href": f"/api/modules/{module_id}/{action_id}",
                    "payload": {identifier: "{selected_row." + identifier + "}"},
                    "requires_selection": False, "closes_modal": True,
                },
                {
                    "id": f"cancel-{action_id}", "label": "Cancel", "variant": "secondary", "action_type": "event",
                    "event_type": "ui.modal.close", "payload": {"modal_id": modal_id},
                    "requires_selection": False, "closes_modal": True,
                },
            ],
            "children": [],
        },
    }


def _modal_deletes(section: Mapping[str, Any], endpoint: str) -> bool:
    """True when a Modal's own actions include a delete posting to the endpoint."""
    config = section.get("config")
    actions = config.get("actions") if isinstance(config, Mapping) else None
    if not isinstance(actions, list):
        return False
    return any(
        isinstance(action, Mapping) and action.get("action_type") == "delete" and action.get("href") == endpoint
        for action in actions
    )


def _ensure_confirm_modal(document: dict[str, Any], **modal_args: str) -> tuple[str | None, str | None, bool]:
    """Return ``(modal id, refusal, existing)`` for a confirmation modal deleting through the action.

    A Modal already on the page that deletes through the action is reused
    whatever its id; otherwise one is built unless the constructed id is taken.
    """
    sections = document.get("sections")
    if not isinstance(sections, list):
        return None, "the page declares no sections list", False
    modal_id = _modal_id(modal_args["action_id"])
    endpoint = f"/api/modules/{modal_args['module_id']}/{modal_args['action_id']}"
    existing = _existing_modal(sections, lambda section: _modal_deletes(section, endpoint))
    if existing is not None:
        return existing, None, True
    if any(section.get("id") == modal_id for section in _iter_sections(sections)):
        return None, f"section '{modal_id}' exists but is not a Modal confirming a delete through {endpoint}", False
    sections.append(_confirm_modal(**modal_args))
    return modal_id, None, False


def _opener(
    *, modal_id: str, action_id: str, label: str, edit: bool, base: Mapping[str, Any] | None,
    variant: str | None = None,
) -> dict[str, Any]:
    """Build the modal opener; the id is always constructed so it cannot collide with an authored action.

    ``edit`` openers are row actions (they need a selected row); the others are
    toolbar actions.
    """
    base = base or {}
    return {
        "id": f"open-{action_id}", "label": base.get("label") or label,
        "variant": base.get("variant") or variant or ("secondary" if edit else "primary"),
        "action_type": "event", "event_type": "ui.modal.open", "payload": {"modal_id": modal_id},
        "requires_selection": edit, "closes_modal": False,
    }


def _add_opener(config: dict[str, Any], opener: dict[str, Any], modal_id: str) -> bool:
    """Append a table action opening the modal unless one already does; True when added."""
    actions_list = config.get("actions")
    if not isinstance(actions_list, list):
        actions_list = []
        config["actions"] = actions_list
    if any(isinstance(item, Mapping) and _opens(item, modal_id) for item in actions_list):
        return False
    actions_list.append(opener)
    return True


def _opens(action: Mapping[str, Any], modal_id: str) -> bool:
    payload = action.get("payload")
    if isinstance(payload, list):
        payload = {item.get("key"): item.get("value") for item in payload if isinstance(item, Mapping)}
    return (
        action.get("action_type") == "event" and action.get("event_type") == "ui.modal.open"
        and isinstance(payload, Mapping) and payload.get("modal_id") == modal_id
    )


def _replace_in(container: Any, key: Any) -> Callable[[dict[str, Any]], None]:
    def replace(new: dict[str, Any]) -> None:
        container[key] = new

    return replace


def _table_actions(config: dict[str, Any]) -> Iterator[tuple[dict[str, Any], Callable[[dict[str, Any]], None], bool]]:
    """Yield (action, replacer, in_empty_state) for a table's row and empty-state actions."""
    actions = config.get("actions")
    if isinstance(actions, list):
        for index, action in enumerate(actions):
            if isinstance(action, dict):
                yield action, _replace_in(actions, index), False
    empty = config.get("empty")
    if isinstance(empty, dict) and isinstance(empty.get("action"), dict):
        yield empty["action"], _replace_in(empty, "action"), True


def _enable_selection(config: dict[str, Any], location: str, notes: list[str]) -> None:
    if config.get("selection") in (None, "none"):
        config["selection"] = "single"
        notes.append(f"{location}.selection: set to single so the row action can be used")


def _construct_create_delete(
    document: dict[str, Any], config: dict[str, Any], location: str, *, module_id: str,
    actions: Mapping[str, Mapping[str, Any]], shapes: Mapping[str, list[str]], canonical_ids: Mapping[str, str],
    canonical: set[str], identifier: str | None, entity: str, notes: list[str], refused: list[str],
) -> None:
    """Give an unreachable canonical create/delete of a listed collection its entry point.

    A create gets a toolbar action opening a modal form; a delete gets a row
    action opening a confirmation dialog that posts the selected row's identifier.
    """
    label = _label(entity_label_identifier(entity))
    for operation in ("create", "delete"):
        target = canonical_ids.get(operation)
        action = actions.get(target or "")
        if target is None or action is None or not _user_facing(action):
            continue
        if not action.get("entitlement_gate") and target not in canonical:
            continue  # an ungated write is determined only when it is the collection's canonical write
        if f"{module_id}/{target}" in reachable_page_action_keys([document]):
            continue
        kind = _kind(action)
        if target not in shapes[operation]:
            refused.append(
                f"{location}: no {operation} entry point for {kind} {module_id}/{target}: its inputs are not the "
                f"collection's declared fields{'' if identifier else ' and the collection declares no record identity'}"
            )
            continue
        endpoint = f"/api/modules/{module_id}/{target}"
        if operation == "create":
            modal_id, reason, existing = _ensure_modal(
                document, module_id=module_id, action_id=target, action=action,
                identifier=identifier or "", entity=label, edit=False,
            )
            if modal_id is None:
                refused.append(f"{location}: no create entry point for {kind} {module_id}/{target}: {reason}")
                continue
            opener = _opener(modal_id=modal_id, action_id=target, label=f"New {label}", edit=False, base=None)
            if _add_opener(config, opener, modal_id):
                notes.append(
                    f"{location}: {_subject(module_id, target, action)} has no page entry point; added a "
                    f"'New {label}' toolbar action {_modal_form_clause(document, modal_id, existing, endpoint)}"
                )
            continue
        assert identifier is not None  # a delete-shaped action is classified only against a record identity
        modal_id, reason, existing = _ensure_confirm_modal(
            document, module_id=module_id, action_id=target, identifier=identifier, entity=label,
        )
        if modal_id is None:
            refused.append(f"{location}: no delete entry point for {kind} {module_id}/{target}: {reason}")
            continue
        opener = _opener(modal_id=modal_id, action_id=target, label="Delete", edit=True, base=None, variant="danger")
        if _add_opener(config, opener, modal_id):
            dialog = (
                f"opening the page's existing confirmation dialog '{modal_id}'" if existing
                else "and a confirmation dialog"
            )
            notes.append(
                f"{location}: {_subject(module_id, target, action)} has no page entry point; added a Delete "
                f"row action {dialog} that deletes the selected {label}"
            )
        _enable_selection(config, location, notes)


def entity_label_identifier(entity: str) -> str:
    """Split a declared entity name into words for labels (``ProjectMilestone`` -> ``project_milestone``)."""
    try:
        return entity_identifier(entity)
    except ValueError:
        return entity


def construct_page_bindings(
    document: Any,
    contracts: Mapping[str, Mapping[str, Any]],
    *,
    data_contract: Any = None,
    design_surface_map: Any = None,
    workflow_names: set[str] | None = None,
    page: str = "page",
) -> list[str]:
    """Write the determined bindings into ``document`` and return one note per change."""
    if not isinstance(document, dict):
        return []
    notes: list[str] = []
    refused: list[str] = []
    for section, key, location in iter_data_bound_sections(document):
        action = contracts.get(key or "")
        if action is None or not key:
            continue
        outputs = action.get("output_schema")
        top_level = outputs.get("properties") if isinstance(outputs, Mapping) else None
        for metric, suffix in section_metrics(section):
            for field in ("value_key", "detail_key", "trend_key"):
                value = metric.get(field)
                if not isinstance(value, str) or "." not in value or schema_at_path(outputs, value) is not None:
                    continue
                final = value.rsplit(".", 1)[1]
                if isinstance(top_level, Mapping) and final in top_level:
                    metric[field] = final
                    notes.append(
                        f"{location}{suffix}.{field}: '{value}' is not returned by {key}; its final segment "
                        f"names the returned top-level field '{final}', used it"
                    )
            for field in ("detail_key", "trend_key"):
                value = metric.get(field)
                if value is not None and schema_at_path(outputs, value) is None:
                    metric[field] = None
                    notes.append(f"{location}{suffix}.{field}: '{value}' is not returned by {key}; cleared")
            value_key = metric.get("value_key")
            identity = metric.get("id")
            undeclared = (value_key is None and metric.get("value") is None) or (
                value_key is not None and schema_at_path(outputs, value_key) is None
            )
            if undeclared and isinstance(identity, str) and schema_at_path(outputs, identity) is not None:
                metric["value_key"] = identity
                notes.append(
                    f"{location}{suffix}.value_key: '{value_key}' is not returned by {key}; "
                    f"the item id '{identity}' names a returned field, used it"
                )

    listed = listed_collections(data_contract)
    surface_map = detach(design_surface_map)
    workflows = set(workflow_names or ())
    if listed and isinstance(surface_map, Mapping):
        for section, key, location in list(iter_data_bound_sections(document)):
            if section.get("primitive") not in _TABLE_PRIMITIVES or not key:
                continue
            module_id, _, action_id = key.partition("/")
            collection = listed.get((module_id, action_id))
            if collection is None:
                continue
            actions = {
                contract_key.split("/", 1)[1]: contract
                for contract_key, contract in contracts.items() if contract_key.startswith(f"{module_id}/")
            }
            identifier = record_identity_field(collection, actions)
            no_identity = (
                f"collection '{collection.get('name')}' declares no record identity "
                "(no search_by, id field, unique single-field index or canonical update write)"
            )
            canonical_ids = canonical_write_ids(collection)
            canonical = set(canonical_ids.values()) & set(actions) if collection_has_canonical_writes(collection) else set()
            shapes = mutation_shapes(
                module_id, actions, surface_map, identifier, collection_fields(collection),
                canonical_ids=canonical_ids, canonical_writes=canonical,
            )
            entity = str(collection.get("entity") or collection.get("name"))
            config = section["config"]
            if not workflows:
                for action, replace, in_empty_state in list(_table_actions(config)):
                    if action.get("action_type") != "workflow":
                        continue
                    shape = "update" if action.get("requires_selection") else "create"
                    workflow_id = action.get("workflow_id")
                    if shape == "update" and in_empty_state:
                        refused.append(
                            f"{location}: workflow_id '{workflow_id}' not replaced: an empty table has no row to edit"
                        )
                        continue
                    if shape == "update" and identifier is None:
                        refused.append(f"{location}: workflow_id '{workflow_id}' not replaced: {no_identity}")
                        continue
                    if len(shapes[shape]) != 1:
                        refused.append(
                            f"{location}: workflow_id '{workflow_id}' not replaced: {len(shapes[shape])} "
                            f"{shape} candidates {shapes[shape]} for {module_id}"
                            + ("" if identifier else f"; {no_identity}")
                        )
                        continue
                    target = shapes[shape][0]
                    modal_id, reason, _ = _ensure_modal(
                        document, module_id=module_id, action_id=target, action=actions[target],
                        identifier=identifier or "", entity=entity, edit=shape == "update",
                    )
                    if modal_id is None:
                        refused.append(f"{location}: workflow_id '{action.get('workflow_id')}' not replaced: {reason}")
                        continue
                    replace(_opener(
                        modal_id=modal_id, action_id=target, label="Edit" if shape == "update" else "Create",
                        edit=shape == "update", base=action,
                    ))
                    notes.append(
                        f"{location}: workflow_id '{action.get('workflow_id')}' names no generated workflow; "
                        f"replaced the action with a modal form that submits {module_id}/{target}"
                    )
                    if shape == "update":
                        _enable_selection(config, location, notes)
            # The canonical update gets an Edit entry point, gated or not; a single gated update-shaped
            # write beside it gets its own. Two gated update-shaped writes leave the choice to the author.
            canonical_update = canonical_ids.get("update")
            edits = [canonical_update] if canonical_update in canonical and canonical_update in shapes["update"] else []
            gated_updates = [
                candidate for candidate in shapes["update"]
                if candidate not in edits and actions[candidate].get("entitlement_gate")
            ]
            if len(gated_updates) == 1:
                edits.append(gated_updates[0])
            elif identifier:
                for candidate in gated_updates:
                    if f"{module_id}/{candidate}" not in reachable_page_action_keys([document]):
                        refused.append(
                            f"{location}: no edit entry point for gated {module_id}/{candidate}: "
                            f"{len(gated_updates)} gated update-shaped writes {gated_updates} for {module_id}"
                        )
            if identifier is None:
                unaddressable = sorted(
                    action_id for action_id, action in actions.items()
                    if (action.get("entitlement_gate") or action_id in canonical) and _user_facing(action)
                    and action_id in ((_owned_mutations(surface_map, module_id) or set()) | canonical)
                    and action_id != canonical_ids.get("create")  # a create needs no record identity
                    and f"{module_id}/{action_id}" not in reachable_page_action_keys([document])
                )
                for action_id in unaddressable:
                    refused.append(
                        f"{location}: no edit entry point for {_kind(actions[action_id])} {module_id}/{action_id}: "
                        f"{no_identity}"
                    )
            for target in edits if identifier else []:
                if f"{module_id}/{target}" in reachable_page_action_keys([document]):
                    continue
                # Beside the canonical Edit, a custom write's row action carries its own name.
                label = "Edit" if len(edits) == 1 or target == canonical_update else _label(target)
                modal_id, reason, existing = _ensure_modal(
                    document, module_id=module_id, action_id=target, action=actions[target],
                    identifier=identifier, entity=entity, edit=True, title=None if label == "Edit" else label,
                )
                if modal_id is None:
                    refused.append(f"{location}: no edit entry point for {_kind(actions[target])} {module_id}/{target}: {reason}")
                    continue
                edit = _opener(modal_id=modal_id, action_id=target, label=label, edit=True, base=None)
                if _add_opener(config, edit, modal_id):
                    clause = _modal_form_clause(document, modal_id, existing, f"/api/modules/{module_id}/{target}")
                    row_action = "an Edit row action" if label == "Edit" else f"a '{label}' row action"
                    notes.append(
                        f"{location}: {_subject(module_id, target, actions[target])} has no page entry point; "
                        f"added {row_action} {clause}"
                    )
                _enable_selection(config, location, notes)
            _construct_create_delete(
                document, config, location, module_id=module_id, actions=actions, shapes=shapes,
                canonical_ids=canonical_ids, canonical=canonical, identifier=identifier, entity=entity,
                notes=notes, refused=refused,
            )
    for note in notes:
        logger.info("[pages] %s: constructed %s", page, note)
    for note in refused:
        logger.info("[pages] %s: not constructed %s", page, note)
    return notes


__all__ = [
    "action_input_fields",
    "canonical_write_ids",
    "collection_fields",
    "construct_page_bindings",
    "listed_collections",
    "mutation_shapes",
    "record_identity_field",
]
