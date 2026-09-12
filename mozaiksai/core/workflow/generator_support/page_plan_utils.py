"""Validate generated pages against planned identity without rewriting their UI."""

from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Any

import yaml

from mozaiksai.core.runtime.app.page_schema import PageSchemaValidationError, validate_page_schema

from .code_files import safe_relpath


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
        if any(item.code == "page_schema.name_mismatch" for item in error.diagnostics):
            details += f" Runtime page name must match file identity {expected_name!r}; keep the display label in title."
        raise ValueError(f"{path}: {details}") from error
    except (ValueError, yaml.YAMLError) as error:
        raise ValueError(f"{path}: {error}") from error
