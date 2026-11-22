"""Generated page actions resolve to admitted workflows and reachable module calls."""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from pathlib import PurePosixPath
from typing import Any

import yaml

from mozaiksai.core.runtime.app.page_schema import _CHILD_CONFIG_MODELS, _TOP_LEVEL_CONFIG_MODELS
from mozaiksai.core.workflow.context.frozen import detach

from .page_data_bindings import iter_data_bound_sections

PAGE_ACTION_FIELDS = ("action", "submit_action", "cancel_action", "plan_action", "add_on_action")
# JavaScript arrays, including the renderer's selected_rows, use 32-bit length.
_MAX_ARRAY_LENGTH = 2**32 - 1


def generated_workflow_names(files: dict[str, str], context: Any = None) -> set[str]:
    """Read workflow files or the artifact-backed bundle projection, never a plan."""
    names: set[str] = set()
    for path, content in files.items():
        parts = path.replace("\\", "/").split("/")
        if len(parts) != 3 or parts[0] != "workflows" or parts[2] != "orchestrator.yaml":
            continue
        try:
            document = yaml.safe_load(content)
        except yaml.YAMLError:
            continue
        if isinstance(document, dict) and document.get("workflow_name") == parts[1]:
            names.add(parts[1])
    metadata = detach(context.get("workflow_integration_metadata")) if context is not None else None
    if isinstance(metadata, dict) and (
        metadata.get("source_artifact_version_id")
        or metadata.get("source") in {"workflow_bundle_files", "workflow_bundle_artifact"}
    ):
        for workflow in metadata.get("workflows") or []:
            name = workflow.get("workflow_name") if isinstance(workflow, dict) else None
            if isinstance(name, str) and name:
                names.add(name)
    return names


def _actions(config: dict[str, Any]) -> Iterator[tuple[str, dict[str, Any]]]:
    for field in PAGE_ACTION_FIELDS:
        action = config.get(field)
        if isinstance(action, dict):
            yield field, action
    for action in config.get("actions") or []:
        if isinstance(action, dict):
            yield "actions", action
    empty = config.get("empty")
    if isinstance(empty, dict):
        for field, action in _actions(empty):
            yield f"empty.{field}", action


def _design_controls(primitive: str, config: Any, *, child: bool = False) -> Iterator[dict[str, Any]]:
    """Read only action slots supported by this primitive, even in a partial design hint."""
    models = _CHILD_CONFIG_MODELS if child else _TOP_LEVEL_CONFIG_MODELS
    model = models.get(primitive)
    if model is None or not isinstance(config, dict):
        return
    slots = {key: value for key, value in config.items() if key in model.model_fields}
    if not isinstance(slots.get("actions"), list):
        slots.pop("actions", None)
    if isinstance(slots.get("empty"), dict):
        slots["empty"] = {"action": slots["empty"].get("action")}
    for _, action in _actions(slots):
        yield action
    children = slots.get("children")
    if isinstance(children, list):
        for section in children:
            if isinstance(section, dict) and isinstance(section.get("primitive"), str):
                yield from _design_controls(section["primitive"], section.get("config"), child=True)


def design_page_write_actions(page: dict[str, Any]) -> list[dict[str, str]]:
    """Project canonical write controls from partial DesignDocs section configs."""
    route = page["route"]
    pairs: set[tuple[str, str]] = set()
    for section in page.get("sections") or []:
        hint = section.get("config_hint")
        if not isinstance(hint, str):
            continue
        try:
            config = json.loads(hint)
        except ValueError:
            continue  # A prose hint supplies no canonical action reference.
        for action in _design_controls(section.get("primitive", ""), config):
            if action.get("action_type") not in ("submit", "delete") or action.get("data_source") is None:
                continue
            source = action["data_source"]
            if (not isinstance(source, dict) or set(source) != {"module_id", "action_id"}
                    or any(not isinstance(value, str) or not value.strip() for value in source.values())):
                raise ValueError(f"Reviewed write on {route!r} requires data_source with exactly module_id and action_id")
            pairs.add((source["module_id"], source["action_id"]))
    return [{"module_id": module, "action_id": action} for module, action in sorted(pairs)]


def _sections(
    sections: Any, modal_ancestors: tuple[str, ...] = (), inherited_data: bool = False,
) -> Iterator[tuple[dict[str, Any], tuple[str, ...], bool]]:
    for section in sections if isinstance(sections, list) else []:
        if not isinstance(section, dict) or not isinstance(section.get("config"), dict):
            continue
        ancestors = modal_ancestors
        if section.get("primitive") == "Modal":
            ancestors += (str(section.get("id") or ""),)
        config = section["config"]
        has_data = bool(config.get("api_endpoint")) or inherited_data
        yield section, ancestors, has_data
        yield from _sections(config.get("children"), ancestors, has_data)


def page_workflow_binding_errors(document: Any, workflow_names: set[str]) -> list[str]:
    failures: list[str] = []
    if not isinstance(document, dict):
        return failures
    for section, _, _ in _sections(document.get("sections")):
        for _, action in _actions(section["config"]):
            if action.get("action_type") != "workflow":
                continue
            workflow = action.get("workflow_id")
            if workflow not in workflow_names:
                valid = ", ".join(sorted(workflow_names)) or "(none in the bundle)"
                failures.append(
                    f"{document.get('name') or 'page'}/{section.get('id') or '<no-id>'}: "
                    f"workflow_id '{workflow}' is not present in the generated workflow bundle. "
                    f"Valid workflows: {valid}. Use an existing workflow or a typed module action."
                )
    return failures


def _action_payload(action: dict[str, Any]) -> dict[str, Any]:
    payload = action.get("payload")
    if isinstance(payload, list):
        return {item["key"]: item.get("value") for item in payload if isinstance(item, dict) and "key" in item}
    return payload if isinstance(payload, dict) else {}


def _payload_selection(payload: dict[str, Any]) -> int:
    """Minimum selected row count required by the action's indexed payload references."""
    needed = 0
    for value in payload.values():
        if not isinstance(value, str):
            continue
        for token in re.findall(r"\{([^}]+)\}", value):
            parts = [part for part in token.strip().split(".") if part]
            if parts and parts[0] in {"row", "selected_row"}:
                needed = max(needed, 1)
            if len(parts) > 1 and parts[0] == "selected_rows" and parts[1].isdecimal():
                index = parts[1]
                if len(index) > 10 or not index.isascii() or (len(index) > 1 and index.startswith("0")):
                    return _MAX_ARRAY_LENGTH + 1
                needed = max(needed, int(index) + 1)
    return needed


def _control_selection(
    section: dict[str, Any], field: str, action: dict[str, Any], *, has_data: bool, inherited: int,
) -> int | None:
    """Maximum selectable row count. None means the control cannot fire."""
    primitive, config = section.get("primitive"), section["config"]
    if primitive == "Form" and config.get("disabled") and field == "submit_action":
        return None
    if primitive == "PricingCatalog":
        rows = config.get("plans" if field == "plan_action" else "add_ons")
        # A catalog button passes its own item. Static arrays override fetched arrays.
        has_items = bool(rows) if isinstance(rows, list) else has_data
        return 1 if has_items else None
    if primitive in {"DataTable", "ResourceTable"}:
        # The table passes its local selection even inside a modal. Empty-state actions pass [].
        selection = config.get("selection")
        available = 0
        if field == "actions" and has_data and selection not in (None, "none"):
            available = _MAX_ARRAY_LENGTH if selection == "multi" else 1
            if config.get("pagination"):
                # Changing page clears selection. In client mode each sortable
                # column can expose two more orderings without clearing it.
                page_size = config.get("page_size") or 20
                orderings = 1
                if config.get("pagination_mode") != "server":
                    orderings += 2 * sum(
                        1 for col in config.get("columns") or []
                        if isinstance(col, str) or (isinstance(col, dict) and col.get("sortable"))
                    )
                if type(page_size) is int:
                    available = min(available, page_size * orderings)
        if field == "actions" and action.get("requires_selection") and not available:
            return None
        return available
    return inherited


def reachable_page_action_keys(pages: list[Any], *, include_reads: bool = True) -> set[str]:
    """Collect reachable submit/delete controls and optionally section read bindings."""
    keys: set[str] = set()

    def add(endpoint: Any) -> None:
        if isinstance(endpoint, str) and endpoint.startswith("/api/modules/"):
            parts = endpoint.split("/")
            if len(parts) == 5:
                keys.add(f"{parts[3]}/{parts[4]}")

    for page in pages:
        if not isinstance(page, dict):
            continue
        sections = list(_sections(page.get("sections")))
        opened: dict[str, int] = {}
        visited: dict[int, int] = {}
        while True:
            changed = False
            for index, (section, ancestors, has_data) in enumerate(sections):
                if any(modal not in opened for modal in ancestors):
                    continue
                inherited = opened[ancestors[-1]] if ancestors else 0
                if visited.get(index, -1) >= inherited:
                    continue
                visited[index] = inherited
                changed = True
                config = section["config"]
                if include_reads:
                    add(config.get("api_endpoint"))
                for action_field, action in _actions(config):
                    selection = _control_selection(section, action_field, action, has_data=has_data, inherited=inherited)
                    if selection is None:
                        continue
                    payload = _action_payload(action)
                    if action.get("action_type") in {"submit", "delete"} and selection >= _payload_selection(payload):
                        add(action.get("href"))
                    elif action.get("action_type") == "event" and action.get("event_type") == "ui.modal.open":
                        modal = payload.get("modal_id")
                        if isinstance(modal, str):
                            opened[modal] = max(opened.get(modal, -1), selection)
            if not changed:
                break
    return keys


def planned_page_writes(pages: list[dict[str, Any]], context: Any = None) -> dict[str, set[str]]:
    """Read the code-owned plan projection; older plans can use their bound DesignDocs review."""
    declarative = [page for page in pages if page.get("ui_surface") != "custom_react_page" and page.get("route")]
    reviewed = {}
    if any("required_write_actions" not in page for page in declarative) and context is not None and (
        context.get("design_docs_build_artifact") or context.get("design_docs_artifact")
    ):
        from .design_access_policy import resolve_reviewed_page_writes

        reviewed = resolve_reviewed_page_writes(context)
    by_route: dict[str, set[str]] = {}
    for page in declarative:
        route = page["route"]
        actions = page.get("required_write_actions", reviewed.get(route))
        if actions:
            by_route[route] = {f"{action['module_id']}/{action['action_id']}" for action in actions}
    return by_route


def validate_required_page_writes(
    content: str, route: str, expected_by_route: dict[str, set[str]], path: str, *, identity_validated: bool = False,
) -> None:
    """Compare a materialized page with the reviewed writes for its approved route."""
    expected = expected_by_route.get(route)
    if not expected:
        return
    if not identity_validated:
        # Exact export snapshots can contain overlays applied after worker admission.
        from .page_plan_utils import validate_planned_page

        validate_planned_page(content, {"route": route}, path)
    feedback = _missing_write_feedback(yaml.safe_load(content), route, expected, path)
    if feedback:
        raise ValueError(feedback)


def _missing_write_feedback(page: dict[str, Any], route: str, expected: set[str], path: str) -> str | None:
    missing = expected - reachable_page_action_keys([page], include_reads=False)
    if missing:
        return (
            f"{path}: route {route!r} is missing reachable write controls for {', '.join(sorted(missing))}. "
            "Add a reachable submit or delete control on this page with the matching "
            "data_source {module_id, action_id} for each missing action."
        )
    return None


def rejected_page_write_feedback(
    content: str, planned: dict[str, Any], expected_by_route: dict[str, set[str]], path: str,
) -> str | None:
    """Supplement an existing schema rejection only for this identified, binding-compiled page."""
    from .code_files import planned_page_path

    route = planned["route"]
    expected = expected_by_route.get(route)
    if not expected or planned_page_path(planned) != path:
        return None
    # Reachability consumes the compiled module endpoints. A malformed shape can
    # make its controls indeterminate; it still gets its original schema error.
    try:
        document = yaml.safe_load(content)
        if not isinstance(document, dict) or document.get("route") != route:
            return None
        if document.get("name") != PurePosixPath(path).stem or not isinstance(document.get("sections"), list):
            return None
        return _missing_write_feedback(document, route, expected, path)
    except (yaml.YAMLError, TypeError, ValueError):
        return None


def page_refresh_binding_errors(document: Any) -> list[str]:
    """A table refresh names a real fetch target; its payload is not a query API."""
    if not isinstance(document, dict):
        return []
    candidates: dict[str, int] = {}
    for section, contract_key, _ in iter_data_bound_sections(document):
        if (section.get("primitive") in {"DataTable", "ResourceTable"}
                and (contract_key or section["config"].get("api_endpoint"))):
            name = section.get("id")
            if isinstance(name, str):
                candidates[name] = candidates.get(name, 0) + 1
    targets = sorted(name for name, count in candidates.items() if count == 1)
    errors: list[str] = []
    for section, _, _ in _sections(document.get("sections")):
        for field, action in _actions(section["config"]):
            if action.get("action_type") != "event" or action.get("event_type") != "ui.datatable.refresh":
                continue
            payload = action.get("payload") or {}
            if isinstance(payload, list):
                payload = {entry.get("key"): entry.get("value") for entry in payload if isinstance(entry, dict)}
            extra = sorted(str(key) for key in payload if key != "component_id") if isinstance(payload, dict) else []
            target = payload.get("component_id") if isinstance(payload, dict) else None
            if not isinstance(target, str) or target not in targets or extra or not isinstance(payload, dict):
                unsupported = f" Unsupported fields: {', '.join(extra)}." if extra else ""
                errors.append(
                    f"{document.get('name') or 'page'}/{section.get('id') or '<no-id>'}.{field}: "
                    "ui.datatable.refresh requires only payload.component_id naming a bound table on this page. "
                    f"Valid targets: {', '.join(targets) or '(none)'}.{unsupported} "
                    "Refresh reloads the existing query; form fields and filters are not query bindings. "
                    "Use supported table controls or the approved custom_react_page contract for custom query state."
                )
    return errors
