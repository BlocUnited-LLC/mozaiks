from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

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


def _split_identifiers(raw_specifiers: str) -> List[str]:
    identifiers: List[str] = []
    for part in raw_specifiers.split(","):
        token = part.strip()
        if not token:
            continue
        if " as " in token:
            token = token.split(" as ", 1)[0].strip()
        if _IDENTIFIER_RE.fullmatch(token):
            identifiers.append(token)
    return identifiers


def _parse_exports(file_path: Path, pattern: re.Pattern[str]) -> Tuple[UIPrimitiveExport, ...]:
    text = file_path.read_text(encoding="utf-8")
    entries: List[UIPrimitiveExport] = []
    for match in pattern.finditer(text):
        source_file = str(match.group("source")).replace("./", "")
        for name in _split_identifiers(match.group(1)):
            entries.append(UIPrimitiveExport(name=name, source_file=source_file))
    return tuple(entries)


@lru_cache(maxsize=1)
def get_component_ui_primitive_exports() -> Tuple[UIPrimitiveExport, ...]:
    exports_path = _chat_ui_src_root() / "ui" / "primitives" / "index.js"
    return _parse_exports(exports_path, _EXPORT_RE)


@lru_cache(maxsize=1)
def get_page_ui_primitive_exports() -> Tuple[UIPrimitiveExport, ...]:
    registry_path = _chat_ui_src_root() / "ui" / "page-renderer" / "PrimitiveRegistry.js"
    return _parse_exports(registry_path, _IMPORT_RE)


def _names(exports: Tuple[UIPrimitiveExport, ...]) -> Tuple[str, ...]:
    return tuple(sorted({entry.name for entry in exports}))


def get_component_ui_primitive_names() -> Tuple[str, ...]:
    return _names(get_component_ui_primitive_exports())


def get_page_ui_primitive_names() -> Tuple[str, ...]:
    return _names(get_page_ui_primitive_exports())


def _validate_primitive_names(
    values: Iterable[str] | None,
    *,
    allowed_names: Tuple[str, ...],
    context: str,
) -> List[str]:
    allowed = set(allowed_names)
    normalized: List[str] = []
    invalid: List[str] = []

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
) -> List[str]:
    return _validate_primitive_names(
        values,
        allowed_names=get_component_ui_primitive_names(),
        context=context,
    )


def validate_page_ui_primitives(
    values: Iterable[str] | None,
    *,
    context: str,
) -> List[str]:
    return _validate_primitive_names(
        values,
        allowed_names=get_page_ui_primitive_names(),
        context=context,
    )


def _group_exports_by_source(
    exports: Tuple[UIPrimitiveExport, ...],
) -> Dict[str, List[str]]:
    grouped: Dict[str, List[str]] = {}
    for entry in exports:
        grouped.setdefault(entry.source_file, []).append(entry.name)
    return dict(sorted(grouped.items()))


def format_component_ui_primitive_guidance() -> str:
    names = ", ".join(get_component_ui_primitive_names())
    grouped = _group_exports_by_source(get_component_ui_primitive_exports())
    import_lines = [
        f"import {{ {', '.join(names_for_source)} }} from '@mozaiks/chat-ui/ui/primitives/{source_file}'"
        for source_file, names_for_source in grouped.items()
    ]
    imports_block = "\n".join(import_lines)
    return (
        "Live shipped agent/UI primitives from `@mozaiks/chat-ui/ui/primitives/index.js`:\n"
        f"- {names}\n\n"
        "Use ONLY these names in `primitives_hint` and generated component imports.\n"
        "Suggested import statements:\n"
        "```js\n"
        f"{imports_block}\n"
        "```"
    )


def format_page_ui_primitive_guidance() -> str:
    names = ", ".join(get_page_ui_primitive_names())
    return (
        "Live shipped page primitives from `@mozaiks/chat-ui/ui/page-renderer/PrimitiveRegistry.js`:\n"
        f"- {names}\n\n"
        "Emit ONLY these names in `sections[*].primitive` and nested Grid child primitives."
    )


__all__ = [
    "UIPrimitiveExport",
    "format_component_ui_primitive_guidance",
    "format_page_ui_primitive_guidance",
    "get_component_ui_primitive_exports",
    "get_component_ui_primitive_names",
    "get_page_ui_primitive_exports",
    "get_page_ui_primitive_names",
    "validate_component_ui_primitives",
    "validate_page_ui_primitives",
]
