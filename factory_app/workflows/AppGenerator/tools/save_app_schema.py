import json
import logging
import os
import re
import shutil
from pathlib import Path
from typing import Annotated, Any, Dict, List, Optional

import yaml
from autogen.tools.dependency_injection import Field
from mozaiksai.core.workflow.ui_primitives import (
    get_page_ui_primitive_names,
    validate_page_ui_primitives,
)

_logger = logging.getLogger("tools.save_app_schema")

PROMOTABLE_APP_ENTRIES = ("app.json", "ui", "brand", "config")


def _repo_root() -> Path:
    here = Path(__file__).resolve()
    for parent in [here] + list(here.parents):
        if (parent / "mozaiksai").is_dir():
            return parent
    return here.parents[-1]


def _resolve_generated_artifacts_root() -> Path:
    raw = os.getenv("MOZAIKS_GENERATED_ARTIFACTS_PATH", "generated").strip()
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = _repo_root() / candidate
    return candidate.resolve()


def _context_get(context_variables: Optional[Any], key: str) -> Optional[Any]:
    if context_variables is None:
        return None
    if hasattr(context_variables, "get"):
        try:
            value = context_variables.get(key)
            if value is not None:
                return value
        except Exception:
            pass
    data = getattr(context_variables, "data", None)
    if isinstance(data, dict):
        return data.get(key)
    if isinstance(context_variables, dict):
        return context_variables.get(key)
    return None


def _safe_path_segment(value: Any, *, fallback: str) -> str:
    text = str(value or "").strip()
    if not text:
        text = fallback
    text = re.sub(r"[^A-Za-z0-9_.-]+", "-", text).strip(".-")
    return text or fallback


def _resolve_artifact_ids(
    *,
    context_variables: Optional[Any],
    manifest_dict: Optional[Dict[str, Any]] = None,
) -> tuple[str, str]:
    manifest_dict = manifest_dict or {}
    app_id = (
        _context_get(context_variables, "app_id")
        or os.getenv("MOZAIKS_APP_ID")
        or manifest_dict.get("app_id")
        or manifest_dict.get("app_name")
        or "local-app"
    )
    build_id = (
        _context_get(context_variables, "build_id")
        or _context_get(context_variables, "chat_id")
        or os.getenv("MOZAIKS_BUILD_ID")
        or "local-build"
    )
    return (
        _safe_path_segment(app_id, fallback="local-app"),
        _safe_path_segment(build_id, fallback="local-build"),
    )


def _normalize_list(value: Any) -> List[Any]:
    if not isinstance(value, list):
        return []
    return list(value)


def _require_dict(value: Any, field: str) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"AppSchemaOutput.{field} must be a dict, got {type(value).__name__}")
    return value


def _deep_merge_dicts(base: Dict[str, Any], overlay: Dict[str, Any]) -> Dict[str, Any]:
    merged: Dict[str, Any] = dict(base)
    for key, value in overlay.items():
        if (
            isinstance(value, dict)
            and isinstance(base.get(key), dict)
        ):
            merged[key] = _deep_merge_dicts(base[key], value)
        else:
            merged[key] = value
    return merged


def _resolve_output_dir(
    *,
    context_variables: Optional[Any] = None,
    manifest_dict: Optional[Dict[str, Any]] = None,
) -> Path:
    """Resolve the generated app artifact directory.

    Generator output is staged under:
      $MOZAIKS_GENERATED_ARTIFACTS_PATH/apps/{app_id}/{build_id}/app
    """
    app_id, build_id = _resolve_artifact_ids(
        context_variables=context_variables,
        manifest_dict=manifest_dict,
    )
    return _resolve_generated_artifacts_root() / "apps" / app_id / build_id / "app"


def _write_yaml(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        yaml.dump(data, fh, allow_unicode=True, sort_keys=False, default_flow_style=False)


VALID_ACTION_TYPES = {"navigate", "event", "workflow", "submit", "delete"}
VALID_ALERT_VARIANTS = {"default", "info", "success", "warning", "destructive"}
VALID_FIELD_TYPES = {"text", "email", "password", "number", "textarea", "select", "checkbox"}
VALID_GRID_GAPS = {"sm", "md", "lg", "1", "2", "3", "4", "6", "8", "10", "12"}
VALID_MODAL_SIZES = {"small", "medium", "large", "full"}
VALID_SELECTION_MODES = {"none", "single", "multi"}
VALID_STAT_FORMATS = {"number", "currency", "percentage", "compact"}
VALID_TREND_DIRECTIONS = {"up_good", "up_bad", "neutral"}
VALID_ASSET_SOURCES = {"local", "remote", "uploaded", "generated", "stock"}
VALID_CUSTOM_PAGE_EXTENSIONS = {".js", ".jsx"}


def _is_non_empty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _validate_string_list(value: Any, *, field: str) -> None:
    if value is None:
        return
    if not isinstance(value, list):
        raise ValueError(f"{field} must be a list of strings")
    for index, item in enumerate(value):
        if not _is_non_empty_string(item):
            raise ValueError(f"{field}[{index}] must be a non-empty string")


def _validate_optional_string(value: Any, *, field: str) -> None:
    if value is None:
        return
    if not _is_non_empty_string(value):
        raise ValueError(f"{field} must be a non-empty string or null")


def _validate_asset_manifest(asset_manifest: Any) -> None:
    if asset_manifest is None:
        return
    if not isinstance(asset_manifest, dict):
        raise ValueError("asset_manifest must be an object")
    if not _is_non_empty_string(asset_manifest.get("version")):
        raise ValueError("asset_manifest.version is required")

    assets = asset_manifest.get("assets")
    if not isinstance(assets, list):
        raise ValueError("asset_manifest.assets must be a list")

    for index, asset in enumerate(assets):
        path = f"asset_manifest.assets[{index}]"
        if not isinstance(asset, dict):
            raise ValueError(f"{path} must be an object")
        if not _is_non_empty_string(asset.get("asset_id")):
            raise ValueError(f"{path}.asset_id is required")
        if not _is_non_empty_string(asset.get("kind")):
            raise ValueError(f"{path}.kind is required")

        source = asset.get("source")
        if source not in VALID_ASSET_SOURCES:
            raise ValueError(f"{path}.source must be one of {sorted(VALID_ASSET_SOURCES)}")

        _validate_optional_string(asset.get("path"), field=f"{path}.path")
        _validate_optional_string(asset.get("url"), field=f"{path}.url")
        _validate_optional_string(asset.get("alt"), field=f"{path}.alt")
        _validate_optional_string(asset.get("license"), field=f"{path}.license")
        _validate_string_list(asset.get("usage"), field=f"{path}.usage")
        _validate_string_list(asset.get("tags"), field=f"{path}.tags")

        has_path = _is_non_empty_string(asset.get("path"))
        has_url = _is_non_empty_string(asset.get("url"))
        if not has_path and not has_url:
            raise ValueError(f"{path} must include at least one of path or url")


def _validate_custom_route_bundle(custom_route_bundle: Any) -> None:
    if custom_route_bundle is None:
        return
    if not isinstance(custom_route_bundle, dict):
        raise ValueError("custom_route_bundle must be an object")

    route_manifest = custom_route_bundle.get("route_manifest")
    page_files = custom_route_bundle.get("page_files")
    if not isinstance(route_manifest, list) or not route_manifest:
        raise ValueError("custom_route_bundle.route_manifest must be a non-empty list")
    if not isinstance(page_files, list) or not page_files:
        raise ValueError("custom_route_bundle.page_files must be a non-empty list")

    route_ids: set[str] = set()
    route_paths: set[str] = set()
    registry_keys: set[str] = set()
    route_by_id: Dict[str, Dict[str, Any]] = {}
    for index, entry in enumerate(route_manifest):
        path = f"custom_route_bundle.route_manifest[{index}]"
        if not isinstance(entry, dict):
            raise ValueError(f"{path} must be an object")
        route_id = entry.get("id")
        route_path = entry.get("path")
        component = entry.get("component")
        if not _is_non_empty_string(route_id):
            raise ValueError(f"{path}.id is required")
        if route_id in route_ids:
            raise ValueError(f"{path}.id must be unique")
        if not _is_non_empty_string(entry.get("label")):
            raise ValueError(f"{path}.label is required")
        if not _is_non_empty_string(route_path):
            raise ValueError(f"{path}.path is required")
        if route_path in route_paths:
            raise ValueError(f"{path}.path must be unique")
        if not _is_non_empty_string(component):
            raise ValueError(f"{path}.component is required")
        if component in registry_keys:
            raise ValueError(f"{path}.component must be unique")
        if not isinstance(entry.get("requiresAuth"), bool):
            raise ValueError(f"{path}.requiresAuth must be a boolean")
        order = entry.get("order")
        if order is not None and not isinstance(order, int):
            raise ValueError(f"{path}.order must be an integer or null")
        meta = entry.get("meta")
        if meta is not None and not isinstance(meta, dict):
            raise ValueError(f"{path}.meta must be an object or null")
        if not _is_non_empty_string(entry.get("purpose")):
            raise ValueError(f"{path}.purpose is required")
        route_ids.add(route_id)
        route_paths.add(route_path)
        registry_keys.add(component)
        route_by_id[route_id] = entry

    file_paths: set[str] = set()
    for index, entry in enumerate(page_files):
        path = f"custom_route_bundle.page_files[{index}]"
        if not isinstance(entry, dict):
            raise ValueError(f"{path} must be an object")
        route_id = entry.get("route_id")
        if route_id not in route_by_id:
            raise ValueError(f"{path}.route_id must reference custom_route_bundle.route_manifest[*].id")
        file_path = entry.get("path")
        if not _is_non_empty_string(file_path):
            raise ValueError(f"{path}.path is required")
        normalized = str(file_path).replace("\\", "/")
        if not normalized.startswith("ui/pages/custom/"):
            raise ValueError(f"{path}.path must live under ui/pages/custom/")
        if Path(normalized).suffix not in VALID_CUSTOM_PAGE_EXTENSIONS:
            raise ValueError(f"{path}.path must end with one of {sorted(VALID_CUSTOM_PAGE_EXTENSIONS)}")
        if normalized in file_paths:
            raise ValueError(f"{path}.path must be unique")
        if not _is_non_empty_string(entry.get("component_name")):
            raise ValueError(f"{path}.component_name is required")
        registry_key = entry.get("registry_key")
        if not _is_non_empty_string(registry_key):
            raise ValueError(f"{path}.registry_key is required")
        if registry_key != route_by_id[route_id].get("component"):
            raise ValueError(f"{path}.registry_key must match the owning route component")
        if not _is_non_empty_string(entry.get("purpose")):
            raise ValueError(f"{path}.purpose is required")
        _validate_string_list(entry.get("contract_refs"), field=f"{path}.contract_refs")
        if not _is_non_empty_string(entry.get("content")):
            raise ValueError(f"{path}.content is required")
        file_paths.add(normalized)


def _build_custom_route_manifest_json(custom_route_bundle: Dict[str, Any]) -> Dict[str, Any]:
    return {"pages": list(custom_route_bundle.get("route_manifest") or [])}


def _build_custom_ui_index(custom_route_bundle: Dict[str, Any]) -> str:
    page_files = list(custom_route_bundle.get("page_files") or [])
    if not page_files:
        return "export function register() {}\n"

    imports: List[str] = []
    registrations: List[str] = []
    for entry in page_files:
        file_path = str(entry["path"]).replace("\\", "/")
        rel_path = file_path[len("ui/") :]
        module_path = "./" + rel_path[:-4] if rel_path.endswith(".jsx") else "./" + rel_path[:-3]
        component_name = entry["component_name"]
        registry_key = entry["registry_key"]
        purpose = str(entry.get("purpose") or "").replace("\\", "\\\\").replace("'", "\\'")
        imports.append(f"import {component_name} from '{module_path}';")
        registrations.append(
            "  registerComponent("
            f"'{registry_key}', {component_name}, "
            "{\n"
            f"    description: '{purpose}',\n"
            "  }\n"
            "  );"
        )

    lines = imports + [
        "",
        "export function register(registerComponent) {",
        "  if (typeof registerComponent !== 'function') return;",
        *registrations,
        "}",
        "",
    ]
    return "\n".join(lines)


def _validate_action(action: Any, *, path: str) -> None:
    if not isinstance(action, dict):
        raise ValueError(f"{path} must be an object")
    if not _is_non_empty_string(action.get("label")):
        raise ValueError(f"{path}.label is required")

    action_type = action.get("action_type")
    if not _is_non_empty_string(action_type):
        if _is_non_empty_string(action.get("event_type")):
            action_type = "event"
        elif _is_non_empty_string(action.get("workflow_id")):
            action_type = "workflow"
        elif _is_non_empty_string(action.get("href")):
            action_type = "navigate"
        else:
            raise ValueError(f"{path}.action_type is required")

    if action_type not in VALID_ACTION_TYPES:
        raise ValueError(f"{path}.action_type must be one of {sorted(VALID_ACTION_TYPES)}")

    _validate_optional_string(action.get("id"), field=f"{path}.id")
    _validate_optional_string(action.get("variant"), field=f"{path}.variant")
    _validate_optional_string(action.get("href"), field=f"{path}.href")
    _validate_optional_string(action.get("event_type"), field=f"{path}.event_type")
    _validate_optional_string(action.get("workflow_id"), field=f"{path}.workflow_id")

    payload = action.get("payload")
    if payload is not None and not isinstance(payload, dict):
        raise ValueError(f"{path}.payload must be an object or null")

    context_variables = action.get("context_variables")
    if context_variables is not None and not isinstance(context_variables, dict):
        raise ValueError(f"{path}.context_variables must be an object or null")

    legacy_payload = action.get("event_payload")
    if legacy_payload is not None and not isinstance(legacy_payload, dict):
        raise ValueError(f"{path}.event_payload must be an object or null")

    requires_selection = action.get("requires_selection")
    if requires_selection is not None and not isinstance(requires_selection, bool):
        raise ValueError(f"{path}.requires_selection must be a boolean")

    closes_modal = action.get("closes_modal")
    if closes_modal is not None and not isinstance(closes_modal, bool):
        raise ValueError(f"{path}.closes_modal must be a boolean")

    if action_type == "navigate" and not _is_non_empty_string(action.get("href")):
        raise ValueError(f"{path}.href is required for navigate actions")
    if action_type == "event" and not _is_non_empty_string(action.get("event_type")):
        raise ValueError(f"{path}.event_type is required for event actions")
    if action_type == "workflow" and not _is_non_empty_string(action.get("workflow_id")):
        raise ValueError(f"{path}.workflow_id is required for workflow actions")
    if action_type in {"submit", "delete"} and not _is_non_empty_string(action.get("href")):
        raise ValueError(f"{path}.href is required for {action_type} actions")


def _validate_action_list(actions: Any, *, path: str) -> None:
    if actions is None:
        return
    if not isinstance(actions, list):
        raise ValueError(f"{path} must be a list")
    for index, action in enumerate(actions):
        _validate_action(action, path=f"{path}[{index}]")


def _validate_column(column: Any, *, path: str) -> None:
    if isinstance(column, str):
        if not column.strip():
            raise ValueError(f"{path} must not be empty")
        return

    if not isinstance(column, dict):
        raise ValueError(f"{path} must be a string or object")

    key = column.get("key") or column.get("field") or column.get("name") or column.get("id")
    if not _is_non_empty_string(key):
        raise ValueError(f"{path}.key is required")

    _validate_optional_string(column.get("label"), field=f"{path}.label")

    sortable = column.get("sortable")
    if sortable is not None and not isinstance(sortable, bool):
        raise ValueError(f"{path}.sortable must be a boolean")

    _validate_optional_string(column.get("type"), field=f"{path}.type")
    _validate_optional_string(column.get("width"), field=f"{path}.width")


def _validate_empty_state(empty_state: Any, *, path: str) -> None:
    if empty_state is None:
        return
    if not isinstance(empty_state, dict):
        raise ValueError(f"{path} must be an object")

    _validate_optional_string(empty_state.get("title"), field=f"{path}.title")
    _validate_optional_string(empty_state.get("message"), field=f"{path}.message")

    action = empty_state.get("action")
    if action is not None:
        _validate_action(action, path=f"{path}.action")


def _validate_form_field(field: Any, *, path: str) -> None:
    if not isinstance(field, dict):
        raise ValueError(f"{path} must be an object")

    name = field.get("name") or field.get("id")
    if not _is_non_empty_string(name):
        raise ValueError(f"{path}.name is required")
    if not _is_non_empty_string(field.get("label")):
        raise ValueError(f"{path}.label is required")

    field_type = field.get("type") or field.get("field_type")
    if field_type not in VALID_FIELD_TYPES:
        raise ValueError(f"{path}.type must be one of {sorted(VALID_FIELD_TYPES)}")

    required = field.get("required")
    if required is not None and not isinstance(required, bool):
        raise ValueError(f"{path}.required must be a boolean")

    _validate_optional_string(field.get("placeholder"), field=f"{path}.placeholder")

    options = field.get("options")
    if options is not None:
        if not isinstance(options, list):
            raise ValueError(f"{path}.options must be a list")
        for option_index, option in enumerate(options):
            if not isinstance(option, dict):
                raise ValueError(f"{path}.options[{option_index}] must be an object")
            if option.get("value") is None:
                raise ValueError(f"{path}.options[{option_index}].value is required")
            if not _is_non_empty_string(option.get("label")):
                raise ValueError(f"{path}.options[{option_index}].label is required")


def _validate_children(children: Any, *, path: str, required: bool = False) -> None:
    if children is None:
        if required:
            raise ValueError(f"{path} is required")
        return
    if not isinstance(children, list):
        raise ValueError(f"{path} must be a list")
    if required and not children:
        raise ValueError(f"{path} must contain at least one child section")
    for child_index, child in enumerate(children):
        _validate_page_section(
            child,
            path=f"{path}[{child_index}]",
            require_id=False,
        )


def _validate_section_config(primitive: str, config: Any, *, path: str) -> None:
    if not isinstance(config, dict):
        raise ValueError(f"{path} must be an object")

    _validate_optional_string(config.get("api_endpoint"), field=f"{path}.api_endpoint")

    if primitive == "DataTable":
        columns = config.get("columns")
        if not isinstance(columns, list) or not columns:
            raise ValueError(f"{path}.columns must be a non-empty list")
        for column_index, column in enumerate(columns):
            _validate_column(column, path=f"{path}.columns[{column_index}]")

        selection = config.get("selection")
        if selection is not None and selection not in VALID_SELECTION_MODES:
            raise ValueError(f"{path}.selection must be one of {sorted(VALID_SELECTION_MODES)}")

        for field_name in ("pagination", "search"):
            value = config.get(field_name)
            if value is not None and not isinstance(value, bool):
                raise ValueError(f"{path}.{field_name} must be a boolean")

        page_size = config.get("page_size") or config.get("pageSize")
        if page_size is not None and (not isinstance(page_size, int) or page_size <= 0):
            raise ValueError(f"{path}.page_size must be a positive integer")

        _validate_action_list(config.get("actions") or config.get("toolbar_actions"), path=f"{path}.actions")
        _validate_empty_state(config.get("empty"), path=f"{path}.empty")
        return

    if primitive == "Form":
        fields = config.get("fields")
        if not isinstance(fields, list) or not fields:
            raise ValueError(f"{path}.fields must be a non-empty list")
        for field_index, field in enumerate(fields):
            _validate_form_field(field, path=f"{path}.fields[{field_index}]")

        layout = config.get("layout")
        if layout is not None and layout not in {"vertical", "horizontal", "grid"}:
            raise ValueError(f"{path}.layout must be one of ['grid', 'horizontal', 'vertical']")

        columns = config.get("columns")
        if columns is not None and (not isinstance(columns, int) or columns <= 0):
            raise ValueError(f"{path}.columns must be a positive integer")

        _validate_optional_string(config.get("submit_label"), field=f"{path}.submit_label")
        _validate_optional_string(config.get("cancel_label"), field=f"{path}.cancel_label")

        disabled = config.get("disabled")
        if disabled is not None and not isinstance(disabled, bool):
            raise ValueError(f"{path}.disabled must be a boolean")

        submit_action = config.get("submit_action")
        if submit_action is not None:
            _validate_action(submit_action, path=f"{path}.submit_action")

        submit_endpoint = config.get("submit_endpoint")
        if submit_action is None and submit_endpoint is not None and not _is_non_empty_string(submit_endpoint):
            raise ValueError(f"{path}.submit_endpoint must be a non-empty string")

        cancel_action = config.get("cancel_action")
        if cancel_action is not None:
            _validate_action(cancel_action, path=f"{path}.cancel_action")
        return

    if primitive == "Stat":
        if not _is_non_empty_string(config.get("label")):
            raise ValueError(f"{path}.label is required")
        if config.get("value") is None and not _is_non_empty_string(config.get("value_key")):
            raise ValueError(f"{path} must define either value or value_key")

        stat_format = config.get("format")
        if stat_format is not None and stat_format not in VALID_STAT_FORMATS:
            raise ValueError(f"{path}.format must be one of {sorted(VALID_STAT_FORMATS)}")

        trend_direction = config.get("trend_direction")
        if trend_direction is not None and trend_direction not in VALID_TREND_DIRECTIONS:
            raise ValueError(
                f"{path}.trend_direction must be one of {sorted(VALID_TREND_DIRECTIONS)}"
            )

        _validate_optional_string(config.get("color"), field=f"{path}.color")
        return

    if primitive == "Grid":
        columns = config.get("columns", config.get("cols"))
        if not isinstance(columns, int) or not 1 <= columns <= 6:
            raise ValueError(f"{path}.columns must be an integer between 1 and 6")

        gap = config.get("gap")
        if gap is not None and str(gap) not in VALID_GRID_GAPS:
            raise ValueError(f"{path}.gap must be one of {sorted(VALID_GRID_GAPS)}")

        _validate_children(config.get("children"), path=f"{path}.children", required=True)
        return

    if primitive == "Card":
        _validate_optional_string(config.get("title"), field=f"{path}.title")
        _validate_optional_string(config.get("subtitle"), field=f"{path}.subtitle")
        _validate_action_list(config.get("actions"), path=f"{path}.actions")
        _validate_children(config.get("children"), path=f"{path}.children")
        return

    if primitive == "Button":
        if not _is_non_empty_string(config.get("label")):
            raise ValueError(f"{path}.label is required")
        _validate_optional_string(config.get("variant"), field=f"{path}.variant")
        _validate_optional_string(config.get("size"), field=f"{path}.size")

        action = config.get("action")
        if action is not None:
            _validate_action(action, path=f"{path}.action")
            return

        if any(config.get(field) is not None for field in ("action_type", "event_type", "workflow_id", "href")):
            _validate_action(
                {
                    "label": config.get("label"),
                    "action_type": config.get("action_type"),
                    "event_type": config.get("event_type"),
                    "workflow_id": config.get("workflow_id"),
                    "context_variables": config.get("context_variables"),
                    "href": config.get("href"),
                    "event_payload": config.get("event_payload"),
                },
                path=f"{path}.action",
            )
        return

    if primitive == "Modal":
        _validate_optional_string(config.get("title"), field=f"{path}.title")
        _validate_optional_string(config.get("description"), field=f"{path}.description")
        _validate_optional_string(config.get("modal_id"), field=f"{path}.modal_id")

        size = config.get("size")
        if size is not None and size not in VALID_MODAL_SIZES:
            raise ValueError(f"{path}.size must be one of {sorted(VALID_MODAL_SIZES)}")

        _validate_action_list(config.get("actions"), path=f"{path}.actions")

        open_value = config.get("open")
        if open_value is not None and not isinstance(open_value, bool):
            raise ValueError(f"{path}.open must be a boolean")

        _validate_children(config.get("children"), path=f"{path}.children")
        return

    if primitive == "Alert":
        if not _is_non_empty_string(config.get("message")):
            raise ValueError(f"{path}.message is required")

        _validate_optional_string(config.get("title"), field=f"{path}.title")
        variant = config.get("variant")
        if variant is not None and variant not in VALID_ALERT_VARIANTS:
            raise ValueError(f"{path}.variant must be one of {sorted(VALID_ALERT_VARIANTS)}")

        dismissible = config.get("dismissible")
        if dismissible is not None and not isinstance(dismissible, bool):
            raise ValueError(f"{path}.dismissible must be a boolean")
        return

    if primitive == "Badge":
        if not _is_non_empty_string(config.get("label")):
            raise ValueError(f"{path}.label is required")
        _validate_optional_string(config.get("variant"), field=f"{path}.variant")
        return

    if primitive == "Skeleton":
        rows = config.get("rows")
        if rows is not None and (not isinstance(rows, int) or rows <= 0):
            raise ValueError(f"{path}.rows must be a positive integer")
        _validate_optional_string(config.get("height"), field=f"{path}.height")
        return

    if primitive == "Empty":
        _validate_optional_string(config.get("title"), field=f"{path}.title")
        _validate_optional_string(config.get("message"), field=f"{path}.message")

        action = config.get("action")
        if action is not None:
            _validate_action(action, path=f"{path}.action")
            return

        if config.get("action_label") is not None:
            _validate_action(
                {
                    "label": config.get("action_label"),
                    "action_type": config.get("action_type") or "event",
                    "event_type": config.get("action_event") or config.get("event_type"),
                    "workflow_id": config.get("workflow_id"),
                    "context_variables": config.get("context_variables"),
                    "href": config.get("href"),
                    "event_payload": config.get("action_payload") or config.get("event_payload"),
                },
                path=f"{path}.action",
            )
        return


def _validate_page_section(section: Dict[str, Any], *, path: str, require_id: bool = True) -> None:
    if not isinstance(section, dict):
        raise ValueError(f"{path} must be an object")

    section_id = section.get("id")
    if require_id and not _is_non_empty_string(section_id):
        raise ValueError(f"{path}.id is required")
    if section_id is not None and not _is_non_empty_string(section_id):
        raise ValueError(f"{path}.id must be a non-empty string")

    primitive = section.get("primitive")
    validated = validate_page_ui_primitives([primitive], context=f"{path}.primitive")
    if not validated:
        raise ValueError(f"{path}.primitive is required")

    if section.get("title") is not None and not _is_non_empty_string(section.get("title")):
        raise ValueError(f"{path}.title must be a non-empty string or null")

    _validate_string_list(section.get("roles"), field=f"{path}.roles")
    _validate_string_list(section.get("event_triggers"), field=f"{path}.event_triggers")

    _validate_section_config(validated[0], section.get("config"), path=f"{path}.config")


def _validate_manifest_against_pages(
    manifest_dict: Dict[str, Any],
    page_list: List[Dict[str, Any]],
    custom_route_bundle: Optional[Dict[str, Any]],
) -> None:
    page_names = [page["name"] for page in page_list]
    if len(page_names) != len(set(page_names)):
        raise ValueError("Each AppPageSchema.name must be unique")

    page_routes = [page["route"] for page in page_list]
    if len(page_routes) != len(set(page_routes)):
        raise ValueError("Each AppPageSchema.route must be unique")

    manifest_pages = manifest_dict.get("pages")
    if manifest_pages != page_names:
        raise ValueError("manifest.pages must exactly match the generated page names")

    custom_route_entries = list((custom_route_bundle or {}).get("route_manifest") or [])
    custom_route_ids = [route["id"] for route in custom_route_entries]
    custom_route_paths = [route["path"] for route in custom_route_entries]
    if len(custom_route_paths) != len(set(custom_route_paths)):
        raise ValueError("Each custom route path must be unique")
    if set(page_routes).intersection(custom_route_paths):
        raise ValueError("Declarative page routes and custom route paths must not overlap")

    manifest_custom_routes = manifest_dict.get("custom_routes")
    expected_custom_routes = custom_route_ids or []
    if manifest_custom_routes is None:
        manifest_custom_routes = []
    if manifest_custom_routes != expected_custom_routes:
        raise ValueError("manifest.custom_routes must exactly match the generated custom route ids")

    default_route = manifest_dict.get("default_route")
    if default_route not in page_routes + custom_route_paths:
        raise ValueError("manifest.default_route must match one of the generated declarative or custom page routes")

def _persist_to_filesystem(
    output_dir: Path,
    manifest_dict: Dict[str, Any],
    page_list: List[Dict[str, Any]],
    theme_config_patch: Optional[Dict[str, Any]],
    shell_config: Optional[Dict[str, Any]],
    asset_manifest: Optional[Dict[str, Any]],
    custom_route_bundle: Optional[Dict[str, Any]],
) -> List[str]:
    """Write app.json, ui/pages/*.yaml, optional custom route artifacts, and optional config artifacts.

    Returns a list of written file paths (relative to output_dir).
    Tools are dumb — no reasoning here, just serialize what AppSchemaAgent produced.
    """
    written: List[str] = []

    # app.json — app-level startup and product intent.
    default_route = manifest_dict.get("default_route") or "/"
    auth_strategy = manifest_dict.get("auth_strategy")
    app_json = {
        "appName": manifest_dict["app_name"],
        "startup": {"landing_spot": default_route},
        "targets": {"web": True, "mobile": False},
        "authRequired": bool(auth_strategy and auth_strategy != "public"),
        "admins": [],
    }
    app_json_path = output_dir / "app.json"
    app_json_path.parent.mkdir(parents=True, exist_ok=True)
    app_json_path.write_text(json.dumps(app_json, indent=2, ensure_ascii=False), encoding="utf-8")
    written.append("app.json")

    # ui/pages/{name}.yaml — one file per page
    pages_dir = output_dir / "ui" / "pages"
    for page in page_list:
        name = page["name"]
        page_path = pages_dir / f"{name}.yaml"
        _write_yaml(page_path, page)
        written.append(f"ui/pages/{name}.yaml")

    if custom_route_bundle and isinstance(custom_route_bundle, dict):
        route_manifest_path = output_dir / "ui" / "route_manifest.json"
        route_manifest_path.parent.mkdir(parents=True, exist_ok=True)
        route_manifest_path.write_text(
            json.dumps(_build_custom_route_manifest_json(custom_route_bundle), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        written.append("ui/route_manifest.json")

        for entry in custom_route_bundle.get("page_files") or []:
            file_path = output_dir / Path(str(entry["path"]).replace("\\", "/"))
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(str(entry["content"]), encoding="utf-8")
            written.append(str(Path(str(entry["path"]).replace("\\", "/"))).replace("\\", "/"))

        ui_index_path = output_dir / "ui" / "index.js"
        ui_index_path.parent.mkdir(parents=True, exist_ok=True)
        ui_index_path.write_text(_build_custom_ui_index(custom_route_bundle), encoding="utf-8")
        written.append("ui/index.js")

    # brand/theme_config.json — deep-merge theme_config_patch when provided
    if theme_config_patch and isinstance(theme_config_patch, dict):
        theme_path = output_dir / "brand" / "theme_config.json"
        existing: Dict[str, Any] = {}
        if theme_path.exists():
            try:
                existing = json.loads(theme_path.read_text(encoding="utf-8"))
            except Exception:
                pass
        existing = _deep_merge_dicts(existing, theme_config_patch)
        theme_path.parent.mkdir(parents=True, exist_ok=True)
        theme_path.write_text(json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8")
        written.append("brand/theme_config.json")

    # config/shell.json — deep-merge shell_config when provided
    if shell_config and isinstance(shell_config, dict):
        shell_path = output_dir / "config" / "shell.json"
        existing_shell: Dict[str, Any] = {}
        if shell_path.exists():
            try:
                existing_shell = json.loads(shell_path.read_text(encoding="utf-8"))
            except Exception:
                pass
        existing_shell = _deep_merge_dicts(existing_shell, shell_config)
        shell_path.parent.mkdir(parents=True, exist_ok=True)
        shell_path.write_text(json.dumps(existing_shell, indent=2, ensure_ascii=False), encoding="utf-8")
        written.append("config/shell.json")

    # config/asset_manifest.json — deep-merge asset_manifest when provided
    if asset_manifest and isinstance(asset_manifest, dict):
        manifest_path = output_dir / "config" / "asset_manifest.json"
        existing_manifest: Dict[str, Any] = {}
        if manifest_path.exists():
            try:
                existing_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except Exception:
                pass
        existing_manifest = _deep_merge_dicts(existing_manifest, asset_manifest)
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(json.dumps(existing_manifest, indent=2, ensure_ascii=False), encoding="utf-8")
        written.append("config/asset_manifest.json")

    return written


def _copy_entry(source: Path, target: Path) -> None:
    if source.is_dir():
        shutil.copytree(source, target, dirs_exist_ok=True)
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def promote_generated_app(source_dir: str | Path, target_root: str | Path) -> Dict[str, Any]:
    """Explicitly promote a generated app bundle into an active app root."""
    source = Path(source_dir).resolve()
    target = Path(target_root).resolve()

    if not source.is_dir():
        raise ValueError(f"Generated app source_dir does not exist: {source}")
    if source == target or source in target.parents:
        raise ValueError("target_root must not be the generated source_dir or inside it")

    copied: List[str] = []
    target.mkdir(parents=True, exist_ok=True)
    for entry in PROMOTABLE_APP_ENTRIES:
        src = source / entry
        if not src.exists():
            continue
        _copy_entry(src, target / entry)
        copied.append(entry)

    if not copied:
        raise ValueError(f"No promotable app artifacts found in {source}")

    return {
        "status": "success",
        "source_dir": str(source),
        "target_root": str(target),
        "copied": copied,
    }


def save_app_schema(
    *,
    agent_message: Annotated[
        Optional[str],
        Field(description="Short summary of the schema produced by AppSchemaAgent."),
    ] = None,
    manifest: Annotated[
        Optional[Dict[str, Any]],
        Field(description="AppManifest object used to persist app.json startup and validate page routes."),
    ] = None,
    pages: Annotated[
        Optional[List[Dict[str, Any]]],
        Field(description="List of AppPageSchema objects, each persisted as ui/pages/{name}.yaml."),
    ] = None,
    theme_config_patch: Annotated[
        Optional[Dict[str, Any]],
        Field(description="Partial theme_config.json patch to merge into brand/theme_config.json. None to skip."),
    ] = None,
    shell_config: Annotated[
        Optional[Dict[str, Any]],
        Field(description="Optional shell config to merge into config/shell.json. None to skip."),
    ] = None,
    asset_manifest: Annotated[
        Optional[Dict[str, Any]],
        Field(description="Optional asset manifest to merge into config/asset_manifest.json. None to skip."),
    ] = None,
    custom_route_bundle: Annotated[
        Optional[Dict[str, Any]],
        Field(description="Optional bounded custom full-page React route bundle persisted to ui/route_manifest.json and ui/pages/custom/*.jsx."),
    ] = None,
    context_variables: Annotated[
        Optional[Any],
        Field(description="AG2-injected workflow context variables."),
    ] = None,
) -> str:
    """
    Persist AppSchemaOutput: write files to disk + store in context_variables.

    Writes:
      - app.json                         → app-level startup + product intent
      - ui/pages/{name}.yaml per page    → AppPageSchema
      - ui/route_manifest.json           → custom_route_bundle.route_manifest when set
      - ui/pages/custom/*.jsx            → custom_route_bundle.page_files when set
      - ui/index.js                      → deterministic registry barrel for custom_route_bundle.page_files
      - brand/theme_config.json (merge)  → theme_config_patch when set
      - config/shell.json (merge)        → shell_config when set
      - config/asset_manifest.json       → asset_manifest when set

    Stores in context_variables:
      - app_manifest, app_pages, app_theme_config_patch, app_shell_config,
        app_asset_manifest, app_custom_route_bundle, app_schema_ready

    Tools are dumb — no reasoning, no transformation. AppSchemaAgent already
    produced correct typed output; this tool just persists it.
    """
    if manifest is None:
        raise ValueError("save_app_schema: manifest is required")

    manifest_dict = _require_dict(manifest, "manifest")
    if not manifest_dict.get("app_name"):
        raise ValueError("manifest.app_name is required")

    page_list = _normalize_list(pages)
    if page_list and not isinstance(page_list, list):
        raise ValueError("save_app_schema: pages must be a list")

    for page in page_list:
        if not isinstance(page, dict):
            raise ValueError("Each entry in pages must be a dict (AppPageSchema)")
        if not page.get("name"):
            raise ValueError("Each AppPageSchema must have a 'name' field")
        if not _is_non_empty_string(page.get("route")):
            raise ValueError(f"Page '{page.get('name')}' must have a valid route")
        if not _is_non_empty_string(page.get("title")):
            raise ValueError(f"Page '{page.get('name')}' must have a valid title")
        if not page.get("sections"):
            raise ValueError(f"Page '{page.get('name')}' must have at least one section")
        if not isinstance(page.get("sections"), list):
            raise ValueError(f"Page '{page.get('name')}' sections must be a list")

        seen_section_ids = set()
        for section_index, section in enumerate(page["sections"]):
            if not isinstance(section, dict):
                raise ValueError(
                    f"Page '{page.get('name')}' sections[{section_index}] must be an object, "
                    f"got {type(section).__name__}"
                )
            section_id = section.get("id")
            if section_id in seen_section_ids:
                raise ValueError(f"Page '{page.get('name')}' has duplicate section id '{section_id}'")
            seen_section_ids.add(section_id)
            _validate_page_section(
                section,
                path=f"pages[{page.get('name')}].sections[{section_index}]",
            )

    if not page_list and custom_route_bundle is None:
        raise ValueError("save_app_schema: at least one declarative page or custom route bundle is required")

    _validate_custom_route_bundle(custom_route_bundle)
    _validate_manifest_against_pages(manifest_dict, page_list, custom_route_bundle)
    _validate_asset_manifest(asset_manifest)

    # Persist to context_variables for downstream agents
    if context_variables and hasattr(context_variables, "set"):
        try:
            context_variables.set("app_manifest", manifest_dict)
            context_variables.set("app_pages", page_list)
            context_variables.set("app_theme_config_patch", theme_config_patch)
            context_variables.set("app_shell_config", shell_config)
            context_variables.set("app_asset_manifest", asset_manifest)
            context_variables.set("app_custom_route_bundle", custom_route_bundle)
            context_variables.set("app_schema_ready", True)
            context_variables.set("available_page_primitives", list(get_page_ui_primitive_names()))
        except Exception as exc:
            _logger.error("Failed to store app schema in context_variables: %s", exc)
            return f"Error persisting app schema to context: {exc}"
    else:
        _logger.warning("context_variables not available or missing 'set' method")

    # Persist to generated artifacts; promotion is explicit and separate.
    written: List[str] = []
    try:
        output_dir = _resolve_output_dir(
            context_variables=context_variables,
            manifest_dict=manifest_dict,
        )
        written = _persist_to_filesystem(
            output_dir,
            manifest_dict,
            page_list,
            theme_config_patch,
            shell_config,
            asset_manifest,
            custom_route_bundle,
        )
        _logger.info(
            "Wrote app schema to %s: %s",
            output_dir,
            ", ".join(written),
        )
        if context_variables and hasattr(context_variables, "set"):
            context_variables.set("generated_app_dir", str(output_dir))
    except Exception as exc:
        # Log but don't fail — context_variables are already set; filesystem is best-effort
        _logger.warning("Could not write schema files to disk: %s", exc)

    msg = (agent_message or "App schema persisted.").strip()
    files_written = f"\nFiles written: {', '.join(written)}" if written else ""
    return (
        f"{msg}\n\n"
        f"App: {manifest_dict.get('app_name')}\n"
        f"Pages: {len(page_list)}\n"
        f"Auth strategy: {manifest_dict.get('auth_strategy') or 'none'}\n"
        f"Theme config patch: {'yes' if theme_config_patch else 'no'}\n"
        f"Shell config: {'yes' if shell_config else 'no'}\n"
        f"Asset manifest: {'yes' if asset_manifest else 'no'}"
        f"{files_written}"
    )


__all__ = ["save_app_schema", "promote_generated_app"]
