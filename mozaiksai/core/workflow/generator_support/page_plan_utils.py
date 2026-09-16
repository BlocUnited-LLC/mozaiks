"""Validate generated pages against planned identity without rewriting their UI."""

from __future__ import annotations

import logging
import re
from functools import lru_cache
from pathlib import PurePosixPath
from typing import Any

import yaml
from pydantic import ValidationError

from mozaiksai.core.runtime.app.page_schema import PageSchemaValidationError, validate_page_schema

from .code_files import safe_relpath

logger = logging.getLogger(__name__)


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
            if isinstance(payload, dict):
                current = payload.get("modal_id")
                if not isinstance(current, str) or current not in modal_ids:
                    target = enclosing or sole_modal
                    if target:
                        payload["modal_id"] = target
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


_MODULE_ACTION_PATH = re.compile(r"(/api/modules/)([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)")


def module_action_index(file_map: dict[str, str]) -> dict[str, set[str]]:
    """Map module id -> declared action ids, read from generated module.yaml files."""
    index: dict[str, set[str]] = {}
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
            str(action.get("id")).strip()
            for action in (actions if isinstance(actions, list) else [])
            if isinstance(action, dict) and str(action.get("id") or "").strip()
        }
    return index


def _retarget_one(match: re.Match[str], modules: dict[str, set[str]], seen: list[str]) -> str:
    prefix, module_id, action_id = match.group(1), match.group(2), match.group(3)
    if module_id in modules:
        return match.group(0)
    owners = sorted(owner for owner, actions in modules.items() if action_id in actions)
    # Exactly one owner, or the rewrite would be a guess. Two modules declaring
    # the same action id is a real ambiguity, and picking one silently would
    # bind the page to a module the planner may not have meant - the same reason
    # a doubly-claimed surface is left rejected rather than resolved.
    if len(owners) != 1:
        return match.group(0)
    seen.append(f"{module_id}/{action_id} -> {owners[0]}/{action_id}")
    return f"{prefix}{owners[0]}/{action_id}"


def retarget_page_module_ids(document: Any, modules: dict[str, set[str]]) -> list[str]:
    """Point canonical endpoints at the module that actually declares the action.

    A live habit tracker emitted /api/modules/habits/create_habit against a
    module whose id is habits_registry. The canonical FORM was right - the page
    agent had the rule and followed it - but the identity was invented, so every
    call 404s and acceptance reports the actions as orphaned.

    Only an unknown module id is rewritten, and only when exactly one generated
    module declares that action. A path already naming a real module is left
    alone even if the action is missing: that is a different defect, and the
    module.yaml is the contract, not this.
    """
    rewrites: list[str] = []
    if not modules:
        return rewrites

    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            return {key: walk(value) for key, value in node.items()}
        if isinstance(node, list):
            return [walk(item) for item in node]
        if isinstance(node, str) and "/api/modules/" in node:
            return _MODULE_ACTION_PATH.sub(lambda m: _retarget_one(m, modules, rewrites), node)
        return node

    replaced = walk(document)
    if rewrites and isinstance(document, dict) and isinstance(replaced, dict):
        document.clear()
        document.update(replaced)
    return rewrites


def normalize_planned_page_content(
    content: str, *, path: str = "", modules: dict[str, set[str]] | None = None
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
    promoted = promote_page_table_primitives(document)
    declared = declare_the_intended_modal(document)
    if declared:
        logger.info("[pages] %s: declared the intended Modal %r", path or "page", declared)
    retargeted = resolve_modal_action_targets(document)
    remoduled = retarget_page_module_ids(document, modules or {})
    renamed = align_page_name_with_file(document, path)
    if not promoted and not retargeted and renamed is None and not declared and not remoduled:
        return content
    if renamed is not None:
        logger.info("[pages] %s: name %r -> file identity", path or "page", renamed)
    if promoted:
        logger.info("[pages] %s: promoted %d DataTable -> ResourceTable", path or "page", promoted)
    if retargeted:
        logger.info("[pages] %s: pointed %d modal action(s) at a declared Modal", path or "page", retargeted)
    for rewrite in remoduled:
        logger.info("[pages] %s: endpoint named no such module: %s", path or "page", rewrite)
    return yaml.safe_dump(document, sort_keys=False, allow_unicode=True)


def validate_planned_page(content: str, planned: dict[str, Any], path: str) -> None:
    try:
        document = yaml.safe_load(content)
        if not isinstance(document, dict):
            raise ValueError("page must be a YAML object")
        expected_name = PurePosixPath(path).stem
        page = validate_page_schema(document, expected_name=expected_name)
        if page.route != planned["route"]:
            raise ValueError(f"route must preserve approved {planned['route']!r}")
    except PageSchemaValidationError as error:
        details = "; ".join(f"{item.location}: {item.code}: {item.message}" for item in error.diagnostics)
        if isinstance(error.__cause__, ValidationError):
            # Only relay known input-free runtime messages, never rejected values or arbitrary validator text.
            action_messages = {
                "Value error, navigate actions require href",
                "Value error, submit actions require href",
                "Value error, delete actions require href",
                "Value error, event actions require event_type",
                "Value error, workflow actions require workflow_id",
            }
            reasons = sorted({
                item["msg"].removeprefix("Value error, ")
                for item in error.__cause__.errors(include_input=False, include_context=False, include_url=False)
                if item["type"] == "value_error" and item["msg"] in action_messages
            })
            if reasons:
                details += " Action requirements: " + "; ".join(reasons) + "."
        if any(item.code == "page_schema.name_mismatch" for item in error.diagnostics):
            details += f" Runtime page name must match file identity {expected_name!r}; keep the display label in title."
        raise ValueError(f"{path}: {details}") from error
    except (ValueError, yaml.YAMLError) as error:
        raise ValueError(f"{path}: {error}") from error
