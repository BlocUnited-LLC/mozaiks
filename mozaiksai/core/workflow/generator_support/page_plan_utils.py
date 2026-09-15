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


def normalize_planned_page_content(content: str, *, path: str = "") -> str:
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
    if not promote_page_table_primitives(document):
        return content
    logger.info("[pages] %s: promoted DataTable -> ResourceTable", path or "page")
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
