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
from mozaiksai.core.workflow.context.frozen import detach

from .code_files import (
    _unwrap_output_envelope,
    extract_code_file_map_from_payload,
    extract_deleted_file_paths_from_payload,
    safe_relpath,
)

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


def module_action_index(file_map: dict[str, str]) -> dict[str, set[str]]:
    """Map module id to declared HTTP actions from generated module.yaml files."""
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
            and action.get("api_surface") not in {"internal", "admin_internal"}
        }
    return index


def module_action_index_from_context(context: Any) -> dict[str, set[str]]:
    """Read the closed inventory from admitted files and declared dependencies."""
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
    return module_action_index(files)


def compile_page_data_sources(
    document: Any,
    modules: dict[str, set[str]],
    *,
    reject_api_endpoints: bool = False,
) -> int:
    """Compile explicit module/action pairs; never resolve identities from URLs.

    Authoring boundaries reject runtime endpoint fields. Assembly can also pass
    already compiled documents through this compiler without changing bytes.
    """
    count = 0

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
            raise ValueError(f"{location}: model-authored endpoint URLs are forbidden; choose data_source")
        if "data_source" in node:
            count += 1
        source = node.pop("data_source", None)
        if source is not None:
            if not isinstance(source, dict) or set(source) != {"module_id", "action_id"}:
                raise ValueError(f"{location}.data_source requires exactly module_id and action_id")
            module_id, action_id = source["module_id"], source["action_id"]
            if any(
                not isinstance(value, str)
                or not re.fullmatch(r"[A-Za-z0-9_.-]+", value)
                or ".." in value
                for value in (module_id, action_id)
            ):
                raise ValueError(f"{location}.data_source requires canonical identifier strings")
            if action_id not in modules.get(module_id, set()):
                raise ValueError(
                    f"{location}.data_source references unknown module/action '{module_id}/{action_id}'"
                )
            if endpoint_key in node:
                raise ValueError(f"{location}: data_source cannot be combined with {endpoint_key}")
            node[endpoint_key] = f"/api/modules/{module_id}/{action_id}"
        elif mutation and reject_api_endpoints:
            raise ValueError(f"{location}: {node['action_type']} actions require data_source")
        for key, child in node.items():
            walk(child, f"{location}.{key}")

    walk(document, str(document.get("name") or "page") if isinstance(document, dict) else "page")
    return count


def compile_authored_page_files(
    files: dict[str, str], *, payload: Any, context: Any,
) -> dict[str, str]:
    """Close raw page/admin candidates at authoring, preserving admitted readback."""
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
    for path in extract_deleted_file_paths_from_payload(payload):
        match = re.fullmatch(r"modules/([^/]+)/module\.yaml", path)
        if match:
            modules.pop(match[1], None)
    compiled = dict(files)
    for path, content in files.items():
        admin = re.fullmatch(r"modules/([^/]+)/contracts/admin\.yaml", path)
        if not _page_stem_from_path(path) and not admin:
            continue
        # Typed admin sections already crossed the same strict compiler during
        # extraction. Identical admitted bytes are readback, not new authoring.
        if path == typed_admin or admitted.get(path) == content:
            continue
        try:
            document = yaml.safe_load(content)
        except yaml.YAMLError as exc:
            raise ValueError(f"{path}: authored page sections require valid YAML") from exc
        if not isinstance(document, dict):
            raise ValueError(f"{path}: authored page sections require an object")
        inventory = {admin[1]: modules.get(admin[1], set())} if admin else modules
        if compile_page_data_sources(document, inventory, reject_api_endpoints=True):
            compiled[path] = yaml.safe_dump(document, sort_keys=False, allow_unicode=True)
    return compiled


def normalize_planned_page_content(
    content: str, *, path: str = "", modules: dict[str, set[str]] | None = None,
    reject_api_endpoints: bool = False,
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
    # Before the resolver: a stated target must not be overwritten by a guess.
    materialized = materialize_modal_targets(document)
    declared = declare_the_intended_modal(document)
    if declared:
        logger.info("[pages] %s: declared the intended Modal %r", path or "page", declared)
    retargeted = resolve_modal_action_targets(document)
    compiled = compile_page_data_sources(document, modules or {}, reject_api_endpoints=reject_api_endpoints)
    renamed = align_page_name_with_file(document, path)
    if not promoted and not retargeted and renamed is None and not declared and not compiled and not materialized:
        return content
    if renamed is not None:
        logger.info("[pages] %s: name %r -> file identity", path or "page", renamed)
    if promoted:
        logger.info("[pages] %s: promoted %d DataTable -> ResourceTable", path or "page", promoted)
    if retargeted:
        logger.info("[pages] %s: pointed %d modal action(s) at a declared Modal", path or "page", retargeted)
    if materialized:
        logger.info("[pages] %s: carried %d typed modal target(s) into the payload", path or "page", materialized)
    return yaml.safe_dump(document, sort_keys=False, allow_unicode=True)


# The runtime sanitizes validation text to "Field value does not match the
# registered page-schema contract", which tells a repairing agent nothing about
# WHICH rule it broke. These five messages are written by the page contract
# itself and contain no author input, so they are safe to relay verbatim. Kept
# in one place because both validation lanes need them and a second copy would
# drift the moment the contract reworded one.
_RELAYABLE_ACTION_MESSAGES = frozenset({
    "Value error, navigate actions require href",
    "Value error, submit actions require href",
    "Value error, delete actions require href",
    "Value error, event actions require event_type",
    "Value error, workflow actions require workflow_id",
})


def relayable_action_reasons(error: Exception) -> list[str]:
    """Input-free reasons behind a page rejection, or nothing if none apply."""
    cause = getattr(error, "__cause__", None)
    if not isinstance(cause, ValidationError):
        return []
    return sorted({
        item["msg"].removeprefix("Value error, ")
        for item in cause.errors(include_input=False, include_context=False, include_url=False)
        if item["type"] == "value_error" and item["msg"] in _RELAYABLE_ACTION_MESSAGES
    })


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
        reasons = relayable_action_reasons(error)
        if reasons:
            details += " Action requirements: " + "; ".join(reasons) + "."
        if any(item.code == "page_schema.name_mismatch" for item in error.diagnostics):
            details += f" Runtime page name must match file identity {expected_name!r}; keep the display label in title."
        raise ValueError(f"{path}: {details}") from error
    except (ValueError, yaml.YAMLError) as error:
        raise ValueError(f"{path}: {error}") from error
