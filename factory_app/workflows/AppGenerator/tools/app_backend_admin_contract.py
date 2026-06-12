from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

APP_BACKEND_ADMIN_SCHEMA_VERSION = "mozaiks.admin.app_backend.v1"
APP_BACKEND_ADMIN_SECTION_IDS = (
    "overview",
    "users",
    "billing",
    "usage",
    "operations",
    "settings",
    "integrations",
    "support",
)
APP_BACKEND_ADMIN_LAYOUTS = ("grid", "sidebar", "full-width", "split")
APP_BACKEND_ADMIN_BUILTIN_PANELS = ("stats", "users", "subscriptions")


class AppBackendAdminContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _required_text(value: Any, *, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field_name} must be non-empty")
    return text


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _string_list(values: Any) -> list[str]:
    if values is None:
        return []
    if not isinstance(values, list):
        raise ValueError("value must be a list")
    result: list[str] = []
    seen: set[str] = set()
    for raw in values:
        text = str(raw or "").strip()
        if text and text not in seen:
            result.append(text)
            seen.add(text)
    return result


class AppBackendAdminPanel(AppBackendAdminContractModel):
    id: str
    label: str
    description: str | None = None
    section: Literal[
        "overview",
        "users",
        "billing",
        "usage",
        "operations",
        "settings",
        "integrations",
        "support",
    ]
    order: int = 0
    renderer: Literal["builtin", "schema", "custom_component"]
    builtin_panel: Literal["stats", "users", "subscriptions"] | None = None
    layout: Literal["grid", "sidebar", "full-width", "split"] | None = None
    sections: list[dict[str, Any]] = Field(default_factory=list)
    component: str | None = None
    permissions: list[str] = Field(default_factory=list)

    @field_validator("id", "label", mode="before")
    @classmethod
    def _required(cls, value: Any, info):  # type: ignore[no-untyped-def]
        return _required_text(value, field_name=info.field_name)

    @field_validator("description", "component", mode="before")
    @classmethod
    def _optional(cls, value: Any) -> str | None:
        return _optional_text(value)

    @field_validator("permissions", mode="before")
    @classmethod
    def _permissions(cls, value: Any) -> list[str]:
        return _string_list(value)

    @field_validator("sections", mode="before")
    @classmethod
    def _sections(cls, value: Any) -> list[dict[str, Any]]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError("sections must be a list")
        result: list[dict[str, Any]] = []
        for item in value:
            if not isinstance(item, dict):
                raise ValueError("sections entries must be objects")
            result.append(item)
        return result

    @model_validator(mode="after")
    def _validate_renderer_contract(self) -> AppBackendAdminPanel:
        if self.renderer == "builtin":
            if self.builtin_panel is None:
                raise ValueError("builtin panels must declare builtin_panel")
            if self.layout is not None:
                raise ValueError("builtin panels must not declare layout")
            if self.sections:
                raise ValueError("builtin panels must not declare sections")
            if self.component is not None:
                raise ValueError("builtin panels must not declare component")
            return self

        if self.renderer == "schema":
            if self.builtin_panel is not None:
                raise ValueError("schema panels must not declare builtin_panel")
            if self.component is not None:
                raise ValueError("schema panels must not declare component")
            if not self.sections:
                raise ValueError("schema panels must declare sections")
            if self.layout is None:
                self.layout = "full-width"
            return self

        if self.builtin_panel is not None:
            raise ValueError("custom_component panels must not declare builtin_panel")
        if not self.component:
            raise ValueError("custom_component panels must declare component")
        if self.sections:
            raise ValueError("custom_component panels must not declare sections")
        if self.layout is not None:
            raise ValueError("custom_component panels must not declare layout")
        return self


class AppBackendAdminConfig(AppBackendAdminContractModel):
    schema_version: Literal["mozaiks.admin.app_backend.v1"]
    panels: list[AppBackendAdminPanel] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique_panel_ids(self) -> AppBackendAdminConfig:
        panel_ids = [panel.id for panel in self.panels]
        if len(panel_ids) != len(set(panel_ids)):
            raise ValueError("app backend admin panels must have unique id values")
        return self


def validate_app_backend_admin_config(raw: Any) -> AppBackendAdminConfig:
    return AppBackendAdminConfig.model_validate(raw)


__all__ = [
    "APP_BACKEND_ADMIN_SCHEMA_VERSION",
    "APP_BACKEND_ADMIN_SECTION_IDS",
    "APP_BACKEND_ADMIN_LAYOUTS",
    "APP_BACKEND_ADMIN_BUILTIN_PANELS",
    "AppBackendAdminConfig",
    "AppBackendAdminPanel",
    "validate_app_backend_admin_config",
]

