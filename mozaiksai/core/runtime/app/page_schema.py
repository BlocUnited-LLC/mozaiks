from __future__ import annotations

"""Canonical runtime contract for declarative app page schemas."""

import re
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError, field_validator, model_validator

from mozaiksai.core.runtime.app.module_loader import LoadedModule
from mozaiksai.core.workflow.ui_primitives import get_page_ui_primitive_names

PAGE_SCHEMA_VERSION = "mozaiks.app_page.v1"

VALID_PAGE_TYPES = frozenset(
    {
        "record_list",
        "record_detail",
        "analytics_dashboard",
        "workflow_board",
        "activity_feed",
        "gallery",
        "wizard",
        "split_view",
        "settings",
        "landing",
        "checkout_success",
    }
)

_API_PATH_RE = re.compile(r"^/api/[A-Za-z0-9_./-]+$")
_MODULE_API_RE = re.compile(r"^/api/modules/(?P<module>[A-Za-z0-9_-]+)/(?P<action>[A-Za-z0-9_-]+)$")
_SAFE_ROUTE_RE = re.compile(r"^/[A-Za-z0-9_./:{}?-]*$")


class PageSchemaValidationError(Exception):
    """Raised when a declarative page schema violates the canonical contract."""

    def __init__(self, diagnostics: Iterable[PageSchemaDiagnostic]) -> None:
        self.diagnostics = tuple(sorted(diagnostics, key=lambda item: (item.location, item.code)))
        super().__init__("Invalid page schema")


class PageSchemaDiagnostic(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    code: str
    location: str
    message: str


class PageContractModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


PrimitiveValue = str | int | float | bool | None
PrimitiveMap = dict[str, PrimitiveValue]


class AppPrimitiveKeyValue(PageContractModel):
    key: str
    value: PrimitiveValue


class AppPageAction(PageContractModel):
    id: str | None = None
    label: str
    variant: str | None = None
    action_type: Literal["navigate", "event", "workflow", "submit", "delete"]
    href: str | None = None
    event_type: str | None = None
    workflow_id: str | None = None
    context_variables: list[AppPrimitiveKeyValue] | PrimitiveMap | None = None
    payload: list[AppPrimitiveKeyValue] | PrimitiveMap | None = None
    requires_selection: bool = False
    closes_modal: bool = True

    @model_validator(mode="after")
    def _validate_action_shape(self) -> AppPageAction:
        if self.action_type in {"navigate", "submit", "delete"} and not self.href:
            raise ValueError(f"{self.action_type} actions require href")
        if self.action_type == "event" and not self.event_type:
            raise ValueError("event actions require event_type")
        if self.action_type == "workflow" and not self.workflow_id:
            raise ValueError("workflow actions require workflow_id")
        if self.href is not None:
            _validate_href(self.href, self.action_type)
        return self


class AppTableColumn(PageContractModel):
    key: str
    label: str | None = None
    sortable: bool = False
    type: str | None = None
    width: str | None = None


class AppSelectOption(PageContractModel):
    value: PrimitiveValue
    label: str


class AppFormField(PageContractModel):
    name: str
    label: str
    type: Literal["text", "email", "password", "number", "textarea", "select", "checkbox"]
    required: bool = False
    placeholder: str | None = None
    options: list[AppSelectOption] | None = None


class AppEmptyStateConfig(PageContractModel):
    title: str | None = None
    message: str | None = None
    action: AppPageAction | None = None
    error_title: str | None = None
    error_message: str | None = None
    retry_label: str | None = None


class AppTimelineItem(PageContractModel):
    label: str
    status: str
    description: str | None = None
    timestamp: str | None = None


class AppProgressStage(PageContractModel):
    label: str
    status: str
    description: str | None = None


class AppFileListItem(PageContractModel):
    name: str
    type: str | None = None
    size: str | None = None
    url: str | None = None
    status: str | None = None


class AppTableFilter(PageContractModel):
    label: str
    value: str
    field: str | None = None
    match_values: list[str] | None = None


class AppTableSort(PageContractModel):
    label: str
    value: str
    key: str | None = None
    direction: str | None = None
    type: str | None = None


class DataBackedConfig(PageContractModel):
    api_endpoint: str | None = None

    @field_validator("api_endpoint")
    @classmethod
    def _api_endpoint_is_safe(cls, value: str | None) -> str | None:
        if value is not None:
            _validate_api_endpoint(value)
        return value


class AppDataTableConfig(DataBackedConfig):
    columns: list[AppTableColumn | str]
    data_key: str | None = None
    selection: str | None = None
    pagination: bool = False
    page_size: int | None = None
    search: bool = False
    actions: list[AppPageAction] | None = None
    empty: AppEmptyStateConfig | None = None


class AppResourceTableConfig(AppDataTableConfig):
    search: bool = True
    search_placeholder: str | None = None
    search_keys: list[str] | None = None
    filters: list[AppTableFilter] | None = None
    default_filter: str | None = None
    sorts: list[AppTableSort] | None = None
    default_sort: str | None = None


class AppFormConfig(PageContractModel):
    fields: list[AppFormField]
    layout: str | None = None
    columns: int | None = None
    submit_label: str | None = None
    submit_action: AppPageAction | None = None
    cancel_label: str | None = None
    cancel_action: AppPageAction | None = None
    disabled: bool = False


class AppButtonConfig(PageContractModel):
    label: str
    variant: str | None = None
    size: str | None = None
    action: AppPageAction | None = None


class AppModalLeafConfig(PageContractModel):
    title: str | None = None
    description: str | None = None
    size: str | None = None
    actions: list[AppPageAction] | None = None


class AppAlertConfig(PageContractModel):
    title: str | None = None
    message: str
    variant: str | None = None
    dismissible: bool = False


class AppSkeletonConfig(PageContractModel):
    rows: int | None = None
    height: str | None = None


class AppEmptyConfig(PageContractModel):
    title: str | None = None
    message: str | None = None
    action: AppPageAction | None = None
    icon: str | None = None


class AppTimelineConfig(PageContractModel):
    title: str | None = None
    items: list[AppTimelineItem]


class AppCodeBlockConfig(PageContractModel):
    title: str | None = None
    code: str
    language: str | None = None
    filename: str | None = None


class AppProgressTrackerConfig(PageContractModel):
    title: str | None = None
    stages: list[AppProgressStage]


class AppAlertBannerConfig(PageContractModel):
    title: str | None = None
    message: str
    variant: str | None = None
    dismissible: bool = False
    actions: list[AppPageAction] | None = None


class AppActionButtonConfig(PageContractModel):
    title: str | None = None
    layout: str | None = None
    actions: list[AppPageAction]


class AppFileListConfig(PageContractModel):
    title: str | None = None
    files: list[AppFileListItem]


class AppPageHeaderConfig(PageContractModel):
    title: str
    subtitle: str | None = None
    actions: list[AppPageAction] | None = None
    title_font: str | None = None


class AppSummaryItem(PageContractModel):
    id: str | None = None
    label: str
    value: PrimitiveValue = None
    value_key: str | None = None
    detail: str | None = None
    detail_key: str | None = None
    trend_key: str | None = None
    format: str | None = None
    trend_format: str | None = None
    trend_label: str | None = None


class AppSummaryStripConfig(PageContractModel):
    items: list[AppSummaryItem]


class AppInlineEmptyStateConfig(PageContractModel):
    title: str
    description: str | None = None
    action: AppPageAction | None = None


class AppLoadingStateConfig(PageContractModel):
    label: str | None = None
    message: str | None = None


class AppErrorStateConfig(PageContractModel):
    title: str | None = None
    message: str
    action: AppPageAction | None = None


class AppPanelConfig(PageContractModel):
    title: str | None = None
    eyebrow: str | None = None
    subtitle: str | None = None


class AppSurfaceCardConfig(AppPanelConfig):
    accent: bool = False


class AppStatusPillConfig(PageContractModel):
    label: str
    tone: str | None = None
    size: str | None = None
    dot: bool | None = None


class AppMetricConfig(AppSummaryItem):
    pass


class AppSegment(PageContractModel):
    id: str | None = None
    label: str
    value: float
    tone: str | None = None


class AppSegmentedBarConfig(PageContractModel):
    segments: list[AppSegment]


class AppPricingCatalogManagedAI(PageContractModel):
    display: str | None = None


class AppPricingCatalogUsageLimit(PageContractModel):
    label: str | None = None
    monthly_limit_display: str | None = None


class AppPricingCatalogPrice(PageContractModel):
    display: str | None = None
    interval: str | None = None


class AppPricingCatalogPlan(PageContractModel):
    plan_id: str
    label: str
    description: str | None = None
    price_display: str | None = None
    cta_label: str | None = None
    highlights: list[str] | None = None
    managed_ai: AppPricingCatalogManagedAI | None = None
    usage_limits: list[AppPricingCatalogUsageLimit] | None = None
    pricing: AppPricingCatalogPrice | None = None
    is_default: bool | None = None


class AppPricingCatalogGroup(PageContractModel):
    group_id: str
    label: str
    description: str | None = None
    kind: Literal["subscription", "service", "add_on", "mixed"] | None = None
    plan_ids: list[str] | None = None
    capability_groups: list[str] | None = None
    add_on_ids: list[str] | None = None


class AppPricingCatalogAddOn(PageContractModel):
    id: str | None = None
    add_on_id: str | None = None
    label: str
    description: str | None = None
    price_display: str | None = None
    price: AppPricingCatalogPrice | None = None
    cta_label: str | None = None
    highlights: list[str] | None = None


class AppPricingCatalogConfig(DataBackedConfig):
    title: str | None = None
    subtitle: str | None = None
    plans_key: str | None = None
    groups_key: str | None = None
    add_ons_key: str | None = None
    default_group_id: str | None = None
    default_group_key: str | None = None
    current_plan_key: str | None = None
    highlighted_plan_id: str | None = None
    plan_action_label: str | None = None
    add_on_action_label: str | None = None
    plans: list[AppPricingCatalogPlan] | None = None
    groups: list[AppPricingCatalogGroup] | None = None
    add_ons: list[AppPricingCatalogAddOn] | None = None
    plan_action: AppPageAction | None = None
    add_on_action: AppPageAction | None = None


class AppPageChildSection(PageContractModel):
    id: str | None = None
    primitive: str
    title: str | None = None
    config: dict[str, Any]
    event_triggers: list[str] | None = None
    roles: list[str] | None = None

    @model_validator(mode="after")
    def _validate_child_section(self) -> AppPageChildSection:
        _validate_primitive(self.primitive, allow_grid=False)
        object.__setattr__(self, "config", _validate_config(self.primitive, self.config, child=True))
        return self


class AppGridConfig(PageContractModel):
    columns: int
    gap: str | None = None
    children: list[AppPageChildSection]

    @field_validator("columns")
    @classmethod
    def _columns_in_bounds(cls, value: int) -> int:
        if value < 1 or value > 6:
            raise ValueError("grid columns must be between 1 and 6")
        return value


class AppModalConfig(AppModalLeafConfig):
    children: list[AppPageChildSection] | None = None


_TOP_LEVEL_CONFIG_MODELS: dict[str, type[PageContractModel]] = {
    "DataTable": AppDataTableConfig,
    "Form": AppFormConfig,
    "Grid": AppGridConfig,
    "Button": AppButtonConfig,
    "Modal": AppModalConfig,
    "Alert": AppAlertConfig,
    "Skeleton": AppSkeletonConfig,
    "Empty": AppEmptyConfig,
    "PageHeader": AppPageHeaderConfig,
    "ResourceTable": AppResourceTableConfig,
    "SummaryStrip": AppSummaryStripConfig,
    "InlineEmptyState": AppInlineEmptyStateConfig,
    "LoadingState": AppLoadingStateConfig,
    "ErrorState": AppErrorStateConfig,
    "Panel": AppPanelConfig,
    "SurfaceCard": AppSurfaceCardConfig,
    "StatusPill": AppStatusPillConfig,
    "Metric": AppMetricConfig,
    "SegmentedBar": AppSegmentedBarConfig,
    "Timeline": AppTimelineConfig,
    "CodeBlock": AppCodeBlockConfig,
    "ProgressTracker": AppProgressTrackerConfig,
    "AlertBanner": AppAlertBannerConfig,
    "ActionButton": AppActionButtonConfig,
    "FileList": AppFileListConfig,
    "PricingCatalog": AppPricingCatalogConfig,
}
_CHILD_CONFIG_MODELS = dict(_TOP_LEVEL_CONFIG_MODELS)
_CHILD_CONFIG_MODELS["Modal"] = AppModalLeafConfig
_CHILD_CONFIG_MODELS.pop("Grid", None)


class AppPageSection(PageContractModel):
    id: str
    primitive: str
    title: str | None = None
    config: dict[str, Any]
    event_triggers: list[str] | None = None
    roles: list[str] | None = None

    @model_validator(mode="after")
    def _validate_section(self) -> AppPageSection:
        _validate_primitive(self.primitive, allow_grid=True)
        object.__setattr__(self, "config", _validate_config(self.primitive, self.config, child=False))
        return self


class AppShellNavigationPlacement(PageContractModel):
    desktop: str | None = None
    mobile: str | None = None


class AppPageNavigation(PageContractModel):
    id: str | None = None
    label: str | None = None
    icon: str | None = None
    scope: Literal["global", "local", "profile", "footer"]
    order: int | None = None
    visible: bool = True
    placement: AppShellNavigationPlacement | None = None


class AppRouteAuthParam(PageContractModel):
    key: str
    value: PrimitiveValue


class AppRouteAuth(PageContractModel):
    module: str
    action: str
    params: list[AppRouteAuthParam] | PrimitiveMap | None = None


class AppPageMeta(PageContractModel):
    requiresAuth: bool | None = None
    requiresRole: str | None = None
    authRedirect: str | None = None
    shellMode: Literal["standard", "workspace", "conversation", "focused", "immersive", "public"] | None = None
    routeAuth: AppRouteAuth | None = None


class AppPageSchema(PageContractModel):
    schema_version: Literal["mozaiks.app_page.v1"]
    name: str
    route: str
    title: str
    page_type: Literal[
        "record_list",
        "record_detail",
        "analytics_dashboard",
        "workflow_board",
        "activity_feed",
        "gallery",
        "wizard",
        "split_view",
        "settings",
        "landing",
        "checkout_success",
    ]
    layout: Literal["grid", "sidebar", "full-width", "split"]
    shell_mode: Literal["standard", "workspace", "conversation", "focused", "immersive", "public"] | None = None
    roles: list[str] | None = None
    navigation: AppPageNavigation | None = None
    meta: AppPageMeta | None = None
    sections: list[AppPageSection]

    @field_validator("route")
    @classmethod
    def _route_is_safe(cls, value: str) -> str:
        if not value.startswith("/") or ".." in value or not _SAFE_ROUTE_RE.fullmatch(value):
            raise ValueError("route must be a safe absolute app route")
        return value

    @model_validator(mode="after")
    def _page_is_consistent(self) -> AppPageSchema:
        if self.page_type == "checkout_success":
            raise ValueError("checkout_success must use a custom route bundle")
        if not self.sections:
            raise ValueError("sections must contain at least one section")
        seen = set()
        for section in self.sections:
            if section.id in seen:
                raise ValueError(f"duplicate section id {section.id!r}")
            seen.add(section.id)
        return self


def build_page_action_index(modules: Iterable[LoadedModule]) -> dict[str, frozenset[str]]:
    return {
        module.name: frozenset(module.action_method_map)
        for module in sorted(modules, key=lambda item: item.name)
    }


def build_page_action_index_from_module_contracts(base_path: Path) -> dict[str, frozenset[str]]:
    """Build page action closure authority from declared module contracts."""
    index: dict[str, frozenset[str]] = {}
    modules_dir = base_path / "modules"
    if not modules_dir.exists():
        return index
    for module_yaml in sorted(modules_dir.glob("*/module.yaml"), key=lambda item: item.as_posix()):
        try:
            data = yaml.safe_load(module_yaml.read_text(encoding="utf-8")) or {}
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        module_block = data.get("module")
        module_id = (
            str(module_block.get("id") or "").strip()
            if isinstance(module_block, dict)
            else module_yaml.parent.name
        )
        if not module_id:
            module_id = module_yaml.parent.name
        action_ids = [
            str(action.get("id") or "").strip()
            for action in data.get("actions") or []
            if isinstance(action, dict) and str(action.get("id") or "").strip()
        ]
        index[module_id] = frozenset(sorted(action_ids))
    return index


def load_and_validate_page_schema(
    page_path: Path,
    *,
    expected_name: str | None = None,
    action_index: Mapping[str, frozenset[str]] | None = None,
) -> AppPageSchema:
    try:
        raw = yaml.safe_load(page_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise PageSchemaValidationError(
            [
                PageSchemaDiagnostic(
                    code="page_schema.invalid_yaml",
                    location="$",
                    message="Page schema YAML could not be parsed.",
                )
            ]
        ) from exc
    if not isinstance(raw, dict):
        raise PageSchemaValidationError(
            [
                PageSchemaDiagnostic(
                    code="page_schema.invalid_document",
                    location="$",
                    message="Page schema must be a mapping.",
                )
            ]
        )
    return validate_page_schema(raw, expected_name=expected_name, action_index=action_index)


def validate_page_schema(
    schema: Mapping[str, Any],
    *,
    expected_name: str | None = None,
    action_index: Mapping[str, frozenset[str]] | None = None,
) -> AppPageSchema:
    try:
        page = AppPageSchema.model_validate(dict(schema))
    except ValidationError as exc:
        raise PageSchemaValidationError(_diagnostics_from_validation_error(exc)) from exc
    except ValueError as exc:
        raise PageSchemaValidationError(
            [
                PageSchemaDiagnostic(
                    code="page_schema.invalid_value",
                    location="$",
                    message=str(exc),
                )
            ]
        ) from exc

    diagnostics: list[PageSchemaDiagnostic] = []
    if expected_name is not None and _identity_key(page.name) != _identity_key(expected_name):
        diagnostics.append(
            PageSchemaDiagnostic(
                code="page_schema.name_mismatch",
                location="$.name",
                message="Page schema name must match the requested page.",
            )
        )
    diagnostics.extend(_validate_action_closure(page, action_index))
    if diagnostics:
        raise PageSchemaValidationError(diagnostics)
    return page


def _identity_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def discover_page_schema_paths(base_path: Path) -> dict[str, Path]:
    pages_dir = base_path / "ui" / "pages"
    if not pages_dir.exists():
        return {}
    paths: dict[str, Path] = {}
    for child in sorted(pages_dir.iterdir(), key=lambda item: item.name.lower()):
        if child.is_file() and child.suffix.lower() in {".yaml", ".yml"}:
            paths[child.stem] = child
        elif child.is_dir():
            page_yaml = child / "page.yaml"
            page_yml = child / "page.yml"
            if page_yaml.exists():
                paths[child.name] = page_yaml
            elif page_yml.exists():
                paths[child.name] = page_yml
    return dict(sorted(paths.items()))


def load_app_page_schemas(
    base_path: Path,
    *,
    action_index: Mapping[str, frozenset[str]] | None = None,
) -> dict[str, AppPageSchema]:
    pages = {
        name: load_and_validate_page_schema(path, expected_name=name, action_index=action_index)
        for name, path in discover_page_schema_paths(base_path).items()
    }
    return dict(sorted(pages.items()))


def safe_page_schema_error_detail(error: PageSchemaValidationError) -> dict[str, Any]:
    return {
        "error": "Invalid page schema",
        "diagnostics": [diagnostic.model_dump() for diagnostic in error.diagnostics],
    }


def _validate_config(primitive: str, config: Mapping[str, Any], *, child: bool) -> dict[str, Any]:
    model_map = _CHILD_CONFIG_MODELS if child else _TOP_LEVEL_CONFIG_MODELS
    model_cls = model_map.get(primitive)
    if model_cls is None:
        raise ValueError(f"primitive {primitive!r} is not valid in this position")
    parsed = model_cls.model_validate(dict(config))
    return parsed.model_dump(exclude_none=True)


def _validate_primitive(primitive: str, *, allow_grid: bool) -> None:
    allowed = set(get_page_ui_primitive_names())
    if primitive not in allowed:
        raise ValueError(f"unknown page primitive {primitive!r}")
    if not allow_grid and primitive == "Grid":
        raise ValueError("nested Grid sections are not supported")


def _validate_href(value: str, action_type: str) -> None:
    if action_type in {"submit", "delete"}:
        _validate_api_endpoint(value)
        return
    if value.startswith("/api/"):
        _validate_api_endpoint(value)
        return
    if ".." in value or not _SAFE_ROUTE_RE.fullmatch(value):
        raise ValueError("href must be a safe route or API path")


def _validate_api_endpoint(value: str) -> None:
    if "?" in value or "#" in value or ".." in value or "//" in value[1:]:
        raise ValueError("api_endpoint must not contain traversal, query strings, or fragments")
    if not _API_PATH_RE.fullmatch(value):
        raise ValueError("api_endpoint must be a safe absolute API path")


def _validate_action_closure(
    page: AppPageSchema,
    action_index: Mapping[str, frozenset[str]] | None,
) -> list[PageSchemaDiagnostic]:
    if action_index is None:
        return []
    diagnostics: list[PageSchemaDiagnostic] = []
    route_auth = page.meta.routeAuth if page.meta is not None else None
    if route_auth is not None:
        diagnostics.extend(_validate_module_action(route_auth.module, route_auth.action, "$.meta.routeAuth", action_index))
    for location, endpoint in _walk_api_endpoints(page.model_dump(mode="json", exclude_none=True)):
        match = _MODULE_API_RE.fullmatch(endpoint)
        if match:
            diagnostics.extend(
                _validate_module_action(
                    match.group("module"),
                    match.group("action"),
                    location,
                    action_index,
                )
            )
    return diagnostics


def _validate_module_action(
    module: str,
    action: str,
    location: str,
    action_index: Mapping[str, frozenset[str]],
) -> list[PageSchemaDiagnostic]:
    if module not in action_index:
        return [
            PageSchemaDiagnostic(
                code="page_schema.unknown_module",
                location=location,
                message="Page schema references an unknown module.",
            )
        ]
    if action not in action_index[module]:
        return [
            PageSchemaDiagnostic(
                code="page_schema.unknown_action",
                location=location,
                message="Page schema references an unknown module action.",
            )
        ]
    return []


def _walk_api_endpoints(value: Any, location: str = "$") -> Iterable[tuple[str, str]]:
    if isinstance(value, Mapping):
        for key, item in value.items():
            child_location = f"{location}.{key}"
            if key in {"api_endpoint", "href"} and isinstance(item, str) and item.startswith("/api/"):
                yield child_location, item
            yield from _walk_api_endpoints(item, child_location)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _walk_api_endpoints(item, f"{location}[{index}]")


def _diagnostics_from_validation_error(exc: ValidationError) -> tuple[PageSchemaDiagnostic, ...]:
    diagnostics: list[PageSchemaDiagnostic] = []
    for error in exc.errors(include_input=False):
        loc = _format_location(error.get("loc", ()))
        diagnostics.append(
            PageSchemaDiagnostic(
                code=f"page_schema.{error.get('type', 'invalid')}",
                location=loc,
                message=_safe_validation_message(error),
            )
        )
    return tuple(diagnostics)


def _format_location(raw_location: Any) -> str:
    if not raw_location:
        return "$"
    location = "$"
    for part in raw_location:
        if isinstance(part, int):
            location += f"[{part}]"
        else:
            location += f".{part}"
    return location


def _safe_validation_message(error: Mapping[str, Any]) -> str:
    error_type = str(error.get("type") or "invalid")
    if error_type == "extra_forbidden":
        return "Unknown runtime-affecting field is not allowed."
    if error_type == "missing":
        return "Required field is missing."
    if error_type == "literal_error":
        return "Field value is outside the registered page-schema contract."
    return "Field value does not match the registered page-schema contract."


__all__ = [
    "AppPageSchema",
    "PAGE_SCHEMA_VERSION",
    "PageSchemaDiagnostic",
    "PageSchemaValidationError",
    "VALID_PAGE_TYPES",
    "build_page_action_index",
    "build_page_action_index_from_module_contracts",
    "discover_page_schema_paths",
    "load_and_validate_page_schema",
    "load_app_page_schemas",
    "safe_page_schema_error_detail",
    "validate_page_schema",
]
