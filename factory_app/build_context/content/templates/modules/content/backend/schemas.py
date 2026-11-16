from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Literal, TypedDict

from mozaiksai.core.runtime.composition.module_executor import ModuleInputValidationError

STATUSES = frozenset({"draft", "published", "archived"})
_ENTRY_ID = re.compile(r"ent_[0-9a-f]{32}")
_SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
_TEXT_LIMITS = {"title": 200, "slug": 100, "summary": 1000, "body": 100000}


class Entry(TypedDict):
    entry_id: str
    title: str
    slug: str
    summary: str
    body: str
    status: Literal["draft", "published", "archived"]
    created_at: str
    updated_at: str


def timestamp_now():
    return datetime.now(UTC).isoformat()


def generated_slug(title, entry_id):
    prefix = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    prefix = prefix[:_TEXT_LIMITS["slug"] - 33].rstrip("-") or "entry"
    return prefix + "-" + entry_id.removeprefix("ent_")


def validate_entry_id(value):
    if not isinstance(value, str) or _ENTRY_ID.fullmatch(value) is None:
        raise ModuleInputValidationError("entry_id must be ent_ followed by 32 lowercase hexadecimal digits")
    return value


def validate_status(value):
    if not isinstance(value, str) or value not in STATUSES:
        raise ModuleInputValidationError("status must be draft, published, or archived")
    return value


def validate_limit(value):
    if type(value) is not int or not 1 <= value <= 50:
        raise ModuleInputValidationError("limit must be an integer from 1 through 50")
    return value


def validate_fields(values, *, creating=False):
    allowed = set(_TEXT_LIMITS) | ({"status"} if not creating else set())
    if not values or set(values) - allowed:
        raise ModuleInputValidationError("Supply only title, slug, summary, body, or status changes")
    result = {}
    for name, value in values.items():
        if name == "status":
            result[name] = validate_status(value)
            continue
        if not isinstance(value, str):
            raise ModuleInputValidationError(f"{name} must be a string")
        text = value if name == "body" else value.strip()
        if len(text) > _TEXT_LIMITS[name] or (name in {"title", "slug"} and not text):
            raise ModuleInputValidationError(f"{name} must fit its declared nonempty text bounds")
        if name == "slug" and _SLUG.fullmatch(text) is None:
            raise ModuleInputValidationError("slug must contain lowercase letters, numbers, and single hyphens")
        result[name] = text
    return result


def serialize_entry(record):
    return {field: record[field] for field in Entry.__annotations__}


def serialize_summary(record):
    return {field: record[field] for field in Entry.__annotations__ if field != "body"}
