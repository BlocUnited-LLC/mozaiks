from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from mozaiksai.resources import resolve_chat_ui_src_root

_EXPORT_RE = re.compile(
    r"export\s*\{\s*([^}]+)\s*\}\s*from\s*['\"](?P<source>\./[^'\"]+)['\"]"
)
_IMPORT_RE = re.compile(
    r"import\s*\{\s*([^}]+)\s*\}\s*from\s*['\"]\.\./primitives/(?P<source>[^'\"]+)['\"]"
)
_IDENTIFIER_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")


@dataclass(frozen=True)
class UIPrimitiveExport:
    name: str
    source_file: str


def _chat_ui_src_root() -> Path:
    root = resolve_chat_ui_src_root()
    if root is None:
        raise FileNotFoundError("Unable to locate packaged chat-ui source bundle")
    return root


def _split_identifiers(raw_specifiers: str) -> list[str]:
    identifiers: list[str] = []
    for part in raw_specifiers.split(","):
        token = part.strip()
        if not token:
            continue
        if " as " in token:
            token = token.split(" as ", 1)[0].strip()
        if _IDENTIFIER_RE.fullmatch(token):
            identifiers.append(token)
    return identifiers


def _parse_exports(file_path: Path, pattern: re.Pattern[str]) -> tuple[UIPrimitiveExport, ...]:
    text = file_path.read_text(encoding="utf-8")
    entries: list[UIPrimitiveExport] = []
    for match in pattern.finditer(text):
        source_file = str(match.group("source")).replace("./", "")
        for name in _split_identifiers(match.group(1)):
            entries.append(UIPrimitiveExport(name=name, source_file=source_file))
    return tuple(entries)


@lru_cache(maxsize=1)
def get_component_ui_primitive_exports() -> tuple[UIPrimitiveExport, ...]:
    exports_path = _chat_ui_src_root() / "ui" / "primitives" / "index.js"
    return _parse_exports(exports_path, _EXPORT_RE)


@lru_cache(maxsize=1)
def get_page_ui_primitive_exports() -> tuple[UIPrimitiveExport, ...]:
    registry_path = _chat_ui_src_root() / "ui" / "page-renderer" / "PrimitiveRegistry.js"
    return _parse_exports(registry_path, _IMPORT_RE)


def _names(exports: tuple[UIPrimitiveExport, ...]) -> tuple[str, ...]:
    return tuple(sorted({entry.name for entry in exports}))


def get_component_ui_primitive_names() -> tuple[str, ...]:
    return _names(get_component_ui_primitive_exports())


def get_page_ui_primitive_names() -> tuple[str, ...]:
    return _names(get_page_ui_primitive_exports())


def _validate_primitive_names(
    values: Iterable[str] | None,
    *,
    allowed_names: tuple[str, ...],
    context: str,
) -> list[str]:
    allowed = set(allowed_names)
    normalized: list[str] = []
    invalid: list[str] = []

    if values is None:
        return normalized

    for value in values:
        if not isinstance(value, str):
            invalid.append(repr(value))
            continue
        name = value.strip()
        if not name:
            continue
        if name not in allowed:
            invalid.append(name)
            continue
        if name not in normalized:
            normalized.append(name)

    if invalid:
        allowed_text = ", ".join(allowed_names)
        invalid_text = ", ".join(invalid)
        raise ValueError(
            f"{context} references unsupported primitives: {invalid_text}. "
            f"Allowed primitives: {allowed_text}"
        )

    return normalized


def validate_component_ui_primitives(
    values: Iterable[str] | None,
    *,
    context: str,
) -> list[str]:
    return _validate_primitive_names(
        values,
        allowed_names=get_component_ui_primitive_names(),
        context=context,
    )


def validate_page_ui_primitives(
    values: Iterable[str] | None,
    *,
    context: str,
) -> list[str]:
    return _validate_primitive_names(
        values,
        allowed_names=get_page_ui_primitive_names(),
        context=context,
    )


def format_component_ui_primitive_guidance() -> str:
    names = ", ".join(get_component_ui_primitive_names())
    return (
        "Live shipped agent/UI primitives from the public `@mozaiks/chat-ui/ui` entrypoint:\n"
        f"- {names}\n\n"
        "Use ONLY these names in `primitives_hint` and generated component imports.\n"
        "Import primitives from the public UI barrel, never from deep primitive files:\n"
        "```js\n"
        f"import {{ {names} }} from '@mozaiks/chat-ui/ui'\n"
        "```"
    )


def format_generated_component_ui_primitive_guidance() -> str:
    """Guidance wrapper for generated workflow/custom React components."""
    return format_component_ui_primitive_guidance()


@lru_cache(maxsize=1)
def _load_primitive_schemas() -> dict[str, dict] | None:
    """Load primitive_schemas.json from the page-renderer directory."""
    try:
        schema_path = (
            _chat_ui_src_root() / "ui" / "page-renderer" / "primitive_schemas.json"
        )
        if not schema_path.exists():
            return None
        raw = json.loads(schema_path.read_text(encoding="utf-8"))
        # Strip the _comment key if present
        return {k: v for k, v in raw.items() if not k.startswith("_")}
    except Exception:
        return None


def load_primitive_schemas() -> dict[str, dict] | None:
    return _load_primitive_schemas()


def _clear_primitive_caches() -> None:
    """Clear process-local primitive discovery caches for test isolation."""

    get_component_ui_primitive_exports.cache_clear()
    get_page_ui_primitive_exports.cache_clear()
    _load_primitive_schemas.cache_clear()


def format_page_ui_primitive_guidance() -> str:
    names = ", ".join(get_page_ui_primitive_names())
    schemas = _load_primitive_schemas()
    schema_block = ""
    if schemas:
        lines: list[str] = []
        for name, defn in schemas.items():
            required = defn.get("required", [])
            props = defn.get("properties", {})
            req_str = f"required=[{', '.join(required)}]" if required else "no required fields"
            prop_lines = [f"      {k}: {v}" for k, v in props.items()]
            lines.append(f"  {name} — {req_str}")
            lines.extend(prop_lines)
        schema_block = "\n\nPer-primitive config contract (required fields + all props):\n" + "\n".join(lines)
    return (
        "Live shipped page primitives from `@mozaiks/chat-ui/ui/page-renderer/PrimitiveRegistry.js`:\n"
        f"- {names}\n\n"
        "Emit ONLY these names in `sections[*].primitive` and nested Grid child primitives."
        f"{schema_block}"
    )


def format_generated_page_ui_primitive_guidance() -> str:
    """Guidance wrapper for generated declarative page schemas."""
    return format_page_ui_primitive_guidance()


__all__ = [
    "UIPrimitiveExport",
    "format_component_ui_primitive_guidance",
    "format_generated_component_ui_primitive_guidance",
    "format_generated_page_ui_primitive_guidance",
    "format_page_ui_primitive_guidance",
    "get_component_ui_primitive_exports",
    "get_component_ui_primitive_names",
    "get_page_ui_primitive_exports",
    "get_page_ui_primitive_names",
    "_clear_primitive_caches",
    "validate_component_ui_primitives",
    "validate_page_ui_primitives",
    "load_primitive_schemas",
]
