"""Bounded semantic roles in app-owned brand/theme_config.json."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError


class PageTitleStyle(BaseModel):
    """Page titles select existing app font slots and finite typography tokens."""

    model_config = ConfigDict(extra="forbid")

    font: Literal["body", "heading"] = "heading"
    weight: Literal["regular", "medium", "semibold", "bold"] = "semibold"
    size: Literal["sm", "md", "lg", "xl"] = "lg"
    lineHeight: Literal["tight", "normal", "relaxed"] = "tight"
    letterSpacing: Literal["tight", "normal", "wide"] = "normal"
    textTransform: Literal["none", "uppercase", "lowercase", "capitalize"] = "none"


def validate_page_title_theme(config: dict[str, Any]) -> None:
    """Validate the optional title role without rewriting other theme tokens."""
    if not isinstance(config, dict):
        raise ValueError("theme_config.json must be an object")
    ui = config.get("ui")
    if ui is None:
        return
    if not isinstance(ui, dict):
        raise ValueError("ui must be an object")
    page = ui.get("page")
    if page is None:
        return
    if not isinstance(page, dict):
        raise ValueError("ui.page must be an object")
    if "title" not in page:
        return
    try:
        PageTitleStyle.model_validate(page["title"])
    except ValidationError as exc:
        messages = [
            f"ui.page.title{'.' if error['loc'] else ''}"
            f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
            for error in exc.errors()
        ]
        raise ValueError("; ".join(messages)) from exc
