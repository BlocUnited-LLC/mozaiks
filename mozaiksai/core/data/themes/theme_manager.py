# ============================================================================
# FILE: core/data/theme_manager.py
# DESCRIPTION: Persistence and validation for app theme configuration (legacy: app)
# ============================================================================

from __future__ import annotations

import asyncio
import copy
import re
from datetime import datetime, UTC
from typing import Any, Dict, Optional

from logs.logging_config import get_workflow_logger
from pydantic import BaseModel, Field, ValidationInfo, field_validator, ConfigDict

from mozaiksai.core.data.persistence.persistence_manager import PersistenceManager

logger = get_workflow_logger("theme_manager")

HEX_COLOR_RE = re.compile(r"^#(?:[0-9a-fA-F]{3}){1,2}$")


class FontConfig(BaseModel):
    family: str
    fallbacks: Optional[str] = None
    googleFont: Optional[str] = None
    localFont: Optional[bool] = False
    tailwindClass: Optional[str] = None

    model_config = ConfigDict(extra="forbid")


class ThemeFonts(BaseModel):
    body: FontConfig
    heading: FontConfig
    logo: FontConfig

    model_config = ConfigDict(extra="forbid")


class ColorScale(BaseModel):
    main: str
    light: Optional[str] = None
    dark: Optional[str] = None
    name: Optional[str] = None

    @field_validator("main", "light", "dark", mode="before")
    @classmethod
    def validate_hex(cls, value: Optional[str], info: ValidationInfo) -> Optional[str]:
        if value is None:
            return value
        value = value.strip()
        if not HEX_COLOR_RE.match(value):
            field_name = info.field_name or "color"
            raise ValueError(f"{field_name} must be a hex color value (e.g., #0ea5e9)")
        return value

    model_config = ConfigDict(extra="forbid")


class BackgroundColors(BaseModel):
    base: str
    surface: str
    elevated: str
    overlay: str

    model_config = ConfigDict(extra="forbid")


class BorderColors(BaseModel):
    subtle: str
    strong: str
    accent: str

    model_config = ConfigDict(extra="forbid")


class TextColors(BaseModel):
    primary: str
    secondary: str
    muted: str
    onAccent: str

    model_config = ConfigDict(extra="forbid")


class ThemeColors(BaseModel):
    primary: ColorScale
    secondary: ColorScale
    accent: ColorScale
    success: ColorScale
    warning: ColorScale
    error: ColorScale
    background: BackgroundColors
    border: BorderColors
    text: TextColors

    model_config = ConfigDict(extra="forbid")


class ThemeShadows(BaseModel):
    primary: str
    secondary: str
    accent: str
    success: str
    warning: str
    error: str
    elevated: str
    focus: str

    model_config = ConfigDict(extra="forbid")


class ThemeBranding(BaseModel):
    name: Optional[str] = None
    logo: Optional[str] = None
    favicon: Optional[str] = None

    model_config = ConfigDict(extra="forbid")


class ThemeConfig(BaseModel):
    fonts: ThemeFonts
    colors: ThemeColors
    shadows: ThemeShadows
    branding: ThemeBranding

    model_config = ConfigDict(extra="forbid")


class ThemeUpdatePayload(BaseModel):
    fonts: Optional[Dict[str, Any]] = None
    colors: Optional[Dict[str, Any]] = None
    shadows: Optional[Dict[str, Any]] = None
    branding: Optional[Dict[str, Any]] = None

    @field_validator("fonts", "colors", "shadows", "branding", mode="before")
    @classmethod
    def ensure_dict(cls, value):
        if value is None:
            return value
        if not isinstance(value, dict):
            raise ValueError("Must be an object")
        return value

    model_config = ConfigDict(extra="forbid")


class ThemeUpdateRequest(BaseModel):
    theme: ThemeUpdatePayload
    updated_by: Optional[str] = Field(default=None, alias="updatedBy")

    model_config = ConfigDict(populate_by_name=True, extra="forbid")


class ThemeResponse(BaseModel):
    app_id: str
    theme: ThemeConfig
    source: str = Field(pattern=r"^(default|custom)$")
    updated_at: Optional[datetime] = Field(default=None, alias="updatedAt")
    updated_by: Optional[str] = Field(default=None, alias="updatedBy")

    model_config = ConfigDict(populate_by_name=True, extra="forbid")


DEFAULT_THEME: Dict[str, Any] = {
    "fonts": {
        "body": {
            "family": "Rajdhani",
            "fallbacks": "ui-sans-serif, system-ui, -apple-system, sans-serif",
            "googleFont": "https://fonts.googleapis.com/css2?family=Rajdhani:wght@300;400;500;600;700&display=swap",
            "localFont": False,
            "tailwindClass": "font-sans",
        },
        "heading": {
            "family": "Orbitron",
            "fallbacks": "Rajdhani, ui-sans-serif, system-ui, sans-serif",
            "googleFont": "https://fonts.googleapis.com/css2?family=Orbitron:wght@400;600;700;900&display=swap",
            "localFont": False,
            "tailwindClass": "font-heading",
        },
        "logo": {
            "family": "Fagrak Inline",
            "fallbacks": "Rajdhani, ui-sans-serif, system-ui, sans-serif",
            "googleFont": None,
            "localFont": True,
            "tailwindClass": "font-logo",
        },
    },
    "colors": {
        "primary": {"main": "#06b6d4", "light": "#67e8f9", "dark": "#0e7490", "name": "cyan"},
        "secondary": {"main": "#8b5cf6", "light": "#a78bfa", "dark": "#6d28d9", "name": "violet"},
        "accent": {"main": "#f59e0b", "light": "#fbbf24", "dark": "#d97706", "name": "amber"},
        "success": {"main": "#10b981", "light": "#34d399", "dark": "#059669", "name": "emerald"},
        "warning": {"main": "#f59e0b", "light": "#fbbf24", "dark": "#d97706", "name": "amber"},
        "error": {"main": "#ef4444", "light": "#f87171", "dark": "#dc2626", "name": "red"},
        "background": {
            "base": "#0b1220",
            "surface": "#0f1724",
            "elevated": "#131d33",
            "overlay": "rgba(13, 23, 42, 0.72)",
        },
        "border": {
            "subtle": "#1e293b",
            "strong": "#334155",
            "accent": "#06b6d4",
        },
        "text": {
            "primary": "#e6eef8",
            "secondary": "#94a3b8",
            "muted": "#64748b",
            "onAccent": "#f8fafc",
        },
    },
    "shadows": {
        "primary": "0 20px 45px rgba(6, 182, 212, 0.24)",
        "secondary": "0 20px 45px rgba(139, 92, 246, 0.24)",
        "accent": "0 18px 40px rgba(245, 158, 11, 0.32)",
        "success": "0 18px 40px rgba(16, 185, 129, 0.24)",
        "warning": "0 18px 45px rgba(245, 158, 11, 0.34)",
        "error": "0 18px 45px rgba(239, 68, 68, 0.3)",
        "elevated": "0 24px 60px rgba(11, 18, 32, 0.55)",
        "focus": "0 0 0 3px rgba(8, 145, 178, 0.55)",
    },
    "branding": {
        "name": "MozaiksAI",
        "logo": "/mozaik_logo.svg",
        "favicon": "/mozaik.png",
    },
}


def _deep_merge(base: Dict[str, Any], overrides: Dict[str, Any]) -> Dict[str, Any]:
    result = copy.deepcopy(base)
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


class ThemeManager:
    def __init__(self, persistence: PersistenceManager):
        self._persistence = persistence
        self._collection = None
        self._init_lock = asyncio.Lock()

    async def _get_collection(self):
        if self._collection is not None:
            return self._collection
        async with self._init_lock:
            if self._collection is not None:
                return self._collection
            await self._persistence._ensure_client()
            assert self._persistence.client is not None
#################################################################################
            db = self._persistence.client["MozaiksAI"]
            self._collection = db["Themes"]
            # _id is the canonical key; keep secondary indexes non-unique to avoid
            # unique-null collisions for legacy docs missing newer fields.
            await self._collection.create_index("app_id")
            return self._collection
################################################################################

    async def get_theme(self, app_id: str) -> ThemeResponse:
        app_id = app_id.strip() or "default"

        coll = await self._get_collection()
        doc = await coll.find_one({"_id": app_id})

        if not doc:
            logger.debug("Theme fallback to default", extra={"app_id": app_id})
            theme = ThemeConfig.parse_obj(DEFAULT_THEME)
            return ThemeResponse(
                app_id=app_id,
                theme=theme,
                source="default",
                updatedAt=None,
                updatedBy=None,
            )

        merged = _deep_merge(DEFAULT_THEME, doc.get("theme", {}))
        theme = ThemeConfig.parse_obj(merged)
        return ThemeResponse(
            app_id=app_id,
            theme=theme,
            source="custom",
            updatedAt=doc.get("updated_at"),
            updatedBy=doc.get("updated_by"),
        )

    async def upsert_theme(self, app_id: str, payload: ThemeUpdateRequest) -> ThemeResponse:
        app_id = app_id.strip() or "default"

        coll = await self._get_collection()
        existing_doc = await coll.find_one({"_id": app_id})
        base_theme = DEFAULT_THEME if not existing_doc else _deep_merge(DEFAULT_THEME, existing_doc.get("theme", {}))

        overrides = payload.theme.model_dump(exclude_none=True)
        merged = _deep_merge(base_theme, overrides)
        theme = ThemeConfig.parse_obj(merged)

        now = datetime.now(UTC)
        updated_by = payload.updated_by.strip() if payload.updated_by else None
        version = int(existing_doc.get("version", 0)) + 1 if existing_doc else 1

        doc = {
            "_id": app_id,
            "app_id": app_id,
            "theme": theme.dict(),
            "updated_at": now,
            "updated_by": updated_by,
            "version": version,
        }

        await coll.replace_one({"_id": app_id}, doc, upsert=True)
        logger.info(
            "Theme updated",
            extra={
                "app_id": app_id,
                "updated_by": updated_by,
                "version": version,
            },
        )

        return ThemeResponse(
            app_id=app_id,
            theme=theme,
            source="custom",
            updatedAt=now,
            updatedBy=updated_by,
        )


__all__ = [
    "ThemeManager",
    "ThemeResponse",
    "ThemeUpdateRequest",
    "DEFAULT_THEME",
]
