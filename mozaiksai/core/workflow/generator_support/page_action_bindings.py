"""Generated page actions resolve to admitted workflows and reachable module calls."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import yaml

from mozaiksai.core.workflow.context.frozen import detach

PAGE_ACTION_FIELDS = ("action", "submit_action", "cancel_action", "plan_action", "add_on_action")


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


def _sections(sections: Any, modal_ancestors: tuple[str, ...] = ()) -> Iterator[tuple[dict[str, Any], tuple[str, ...]]]:
    for section in sections if isinstance(sections, list) else []:
        if not isinstance(section, dict) or not isinstance(section.get("config"), dict):
            continue
        ancestors = modal_ancestors
        if section.get("primitive") == "Modal":
            ancestors += (str(section.get("id") or ""),)
        yield section, ancestors
        yield from _sections(section["config"].get("children"), ancestors)


def page_workflow_binding_errors(document: Any, workflow_names: set[str]) -> list[str]:
    failures: list[str] = []
    if not isinstance(document, dict):
        return failures
    for section, _ in _sections(document.get("sections")):
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


def reachable_page_action_keys(pages: list[Any]) -> set[str]:
    """Count visible reads/actions and modals opened by reachable page actions."""
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
        opened: set[str] = set()
        visited: set[int] = set()
        while True:
            changed = False
            for index, (section, ancestors) in enumerate(sections):
                if index in visited or any(modal not in opened for modal in ancestors):
                    continue
                visited.add(index)
                changed = True
                config = section["config"]
                add(config.get("api_endpoint"))
                for action_field, action in _actions(config):
                    if section.get("primitive") == "Form" and config.get("disabled") and action_field == "submit_action":
                        continue
                    if section.get("primitive") == "PricingCatalog":
                        rows = config.get("plans" if action_field == "plan_action" else "add_ons")
                        # Explicit static arrays override fetched arrays in the renderer.
                        if (isinstance(rows, list) and not rows) or (rows is None and not config.get("api_endpoint")):
                            continue
                    if (
                        section.get("primitive") in {"DataTable", "ResourceTable"}
                        and action_field == "actions"
                        and action.get("requires_selection")
                        and config.get("selection") in (None, "none")
                    ):
                        continue
                    if action.get("action_type") in {"submit", "delete"}:
                        add(action.get("href"))
                    elif action.get("action_type") == "event" and action.get("event_type") == "ui.modal.open":
                        payload = action.get("payload")
                        if isinstance(payload, list):
                            payload = {item.get("key"): item.get("value") for item in payload if isinstance(item, dict)}
                        modal = payload.get("modal_id") if isinstance(payload, dict) else None
                        if isinstance(modal, str):
                            opened.add(modal)
            if not changed:
                break
    return keys
