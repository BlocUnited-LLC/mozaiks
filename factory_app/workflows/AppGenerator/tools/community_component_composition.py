"""Keyed, format-preserving composition of pack entries into an app's shared files.

An installed pack contributes entries (route pages, admin pages, data
surfaces, workflow registry entries) to files the app also edits by hand.
These helpers splice only the pack's own entries, identified by a key field,
into such a file and leave every other byte as it was: an app's formatting,
comments and its own entries never change. After each splice the result is
parsed again and must equal the intended structure, or nothing is returned.
"""

from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import yaml


class CompositionFormatError(ValueError):
    """Raised when a shared file cannot be spliced safely in its current form."""


def content_sha256(text: str) -> str:
    """Hex sha256 of text with CRLF line endings normalized, so checkouts on any OS verify alike."""
    return hashlib.sha256(text.replace("\r\n", "\n").encode("utf-8")).hexdigest()


def entry_sha256(entry: Any) -> str:
    """Hex sha256 of an entry's canonical JSON; independent of how the file formats it."""
    canonical = json.dumps(entry, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class KeyedRoot:
    """A shared file whose list containers hold entries identified by a key field."""

    path: str
    base: str  # "app" or "workflows"
    fmt: str  # "json" or "yaml"
    containers: tuple[tuple[str, str], ...]  # (container, key field)

    def key_field(self, container: str) -> str:
        for name, key_field in self.containers:
            if name == container:
                return key_field
        raise KeyError(container)


WORKFLOW_REGISTRY_PATH = "extended_orchestration/extension_registry.json"

# The shared files an installed pack may contribute keyed entries to. Paths are
# relative to their base: the app bundle root, or the workspace workflow root.
KEYED_ROOTS: tuple[KeyedRoot, ...] = (
    KeyedRoot("data/contract.json", "app", "json", (("surfaces", "surface_id"),)),
    KeyedRoot("ui/route_manifest.json", "app", "json", (("pages", "id"),)),
    KeyedRoot("admin/admin_registry.yaml", "app", "yaml", (("pages", "id"),)),
    KeyedRoot(
        WORKFLOW_REGISTRY_PATH, "workflows", "json",
        (("workflows", "id"), ("entrypoints", "id"), ("transitions", "id"), ("workflow_sequences", "id")),
    ),
)
KEYED_APP_ROOT_PATHS: frozenset[str] = frozenset(root.path for root in KEYED_ROOTS if root.base == "app")
KEYED_WORKFLOW_ROOT_PATHS: frozenset[str] = frozenset(root.path for root in KEYED_ROOTS if root.base == "workflows")
OWNERSHIP_CONTENT_HASH = "content_hash"


def parse_document(text: str, fmt: str) -> dict[str, Any]:
    try:
        data = json.loads(text) if fmt == "json" else yaml.safe_load(text)
    except (ValueError, yaml.YAMLError) as exc:
        raise CompositionFormatError(f"cannot be parsed: {exc}") from exc
    if data is None and fmt == "yaml":
        data = {}
    if not isinstance(data, dict):
        raise CompositionFormatError("must be a mapping at its root")
    return data


def keyed_entries(document: Mapping[str, Any], root: KeyedRoot) -> dict[tuple[str, str], Any]:
    """Every keyed entry of a parsed document, by ``(container, key)``.

    A row without a string key is app-owned and can never collide with a pack
    entry, so it is not returned.
    """
    entries: dict[tuple[str, str], Any] = {}
    for container, key_field in root.containers:
        rows = document.get(container)
        if rows is None:
            continue
        if not isinstance(rows, list):
            raise CompositionFormatError(f"{container} must be a list")
        for index, row in enumerate(rows):
            if not isinstance(row, dict):
                raise CompositionFormatError(f"{container}[{index}] must be a mapping")
            key = row.get(key_field)
            if not isinstance(key, str) or not key.strip():
                continue
            if (container, key) in entries:
                raise CompositionFormatError(f"{container} declares {key_field} {key!r} more than once")
            entries[(container, key)] = row
    return entries


def merged_document(
    document: Mapping[str, Any],
    root: KeyedRoot,
    *,
    upserts: Mapping[tuple[str, str], Any],
    removals: set[tuple[str, str]],
) -> dict[str, Any]:
    """The structure a splice must produce: replacements in place, additions appended in order."""
    merged = copy.deepcopy(dict(document))
    for container, key_field in root.containers:
        container_upserts = [(key, value) for (name, key), value in upserts.items() if name == container]
        container_removals = {key for name, key in removals if name == container}
        if not container_upserts and not container_removals:
            continue
        rows = list(merged.get(container) or [])
        positions = {
            row.get(key_field): index for index, row in enumerate(rows)
            if isinstance(row, dict) and isinstance(row.get(key_field), str)
        }
        for key, value in container_upserts:
            if key in positions:
                rows[positions[key]] = copy.deepcopy(value)
            else:
                rows.append(copy.deepcopy(value))
        merged[container] = [
            row for row in rows
            if not (isinstance(row, dict) and row.get(key_field) in container_removals)
        ]
    return merged


def splice_document(
    text: str,
    root: KeyedRoot,
    *,
    upserts: Mapping[tuple[str, str], Any],
    removals: set[tuple[str, str]],
) -> str:
    """Splice entries into an existing file, touching only those entries' bytes.

    Raises :class:`CompositionFormatError` when the file's layout cannot be
    spliced safely or the spliced text does not parse back to the intended
    structure.
    """
    document = parse_document(text, root.fmt)
    current = keyed_entries(document, root)
    # An entry that already holds the value is left byte for byte as the app formatted it.
    upserts = {address: value for address, value in upserts.items() if current.get(address) != value}
    removals = {address for address in removals if address in current}
    if not upserts and not removals:
        return text
    expected = merged_document(document, root, upserts=upserts, removals=removals)
    result = text
    for container, key_field in root.containers:
        container_upserts = {key: value for (name, key), value in upserts.items() if name == container}
        container_removals = {key for name, key in removals if name == container}
        if not container_upserts and not container_removals:
            continue
        splice = _splice_json_container if root.fmt == "json" else _splice_yaml_container
        result = splice(result, container, key_field, container_upserts, container_removals)
    if parse_document(result, root.fmt) != expected:
        raise CompositionFormatError("the spliced file does not parse back to the intended entries")
    return result


def new_document_text(document: Mapping[str, Any], fmt: str) -> str:
    """Serialize a file the pack creates because the app has none."""
    if fmt == "json":
        return json.dumps(document, indent=2, ensure_ascii=False) + "\n"
    return yaml.safe_dump(dict(document), sort_keys=False, allow_unicode=True, default_flow_style=False, width=4096)


def _apply_edits(text: str, edits: list[tuple[int, int, str]]) -> str:
    ordered = sorted(edits, key=lambda edit: (edit[0], edit[1]))
    for index in range(1, len(ordered)):
        if ordered[index][0] < ordered[index - 1][1]:
            raise CompositionFormatError("overlapping entry edits")
    for start, end, replacement in reversed(ordered):
        text = text[:start] + replacement + text[end:]
    return text


def _line_start(text: str, index: int) -> int:
    return text.rfind("\n", 0, index) + 1


def _line_indent(text: str, index: int) -> str:
    """The leading whitespace of the line holding ``index``."""
    start = _line_start(text, index)
    end = start
    while end < len(text) and text[end] in " \t":
        end += 1
    return text[start:end]


def _starts_own_line(text: str, index: int) -> bool:
    return text[_line_start(text, index) : index].strip() == ""


def _removal_runs(removed: list[bool]) -> list[tuple[int, int]]:
    runs: list[tuple[int, int]] = []
    index = 0
    while index < len(removed):
        if removed[index]:
            end = index
            while end + 1 < len(removed) and removed[end + 1]:
                end += 1
            runs.append((index, end))
            index = end + 1
        else:
            index += 1
    return runs


# --------------------------------------------------------------------------------------
# JSON: a span-recording parser locates each container element's exact bytes.
# --------------------------------------------------------------------------------------


@dataclass
class _JsonNode:
    kind: str  # object | array | scalar
    start: int
    end: int
    members: list[tuple[str, _JsonNode]] | None = None
    items: list[_JsonNode] | None = None


_JSON_WS = " \t\r\n"


def _parse_json_spans(text: str) -> _JsonNode:
    def skip_ws(index: int) -> int:
        while index < len(text) and text[index] in _JSON_WS:
            index += 1
        return index

    def expect(index: int) -> str:
        if index >= len(text):
            raise CompositionFormatError("unexpected end of document")
        return text[index]

    def parse_string(index: int) -> int:
        index += 1
        while index < len(text):
            char = text[index]
            if char == "\\":
                index += 2
                continue
            if char == '"':
                return index + 1
            index += 1
        raise CompositionFormatError("unterminated string")

    def parse_value(index: int) -> tuple[_JsonNode, int]:
        index = skip_ws(index)
        char = expect(index)
        if char == "{":
            start = index
            members: list[tuple[str, _JsonNode]] = []
            index = skip_ws(index + 1)
            if expect(index) == "}":
                return _JsonNode("object", start, index + 1, members=members), index + 1
            while True:
                index = skip_ws(index)
                if expect(index) != '"':
                    raise CompositionFormatError("object key must be a string")
                key_end = parse_string(index)
                key = json.loads(text[index:key_end])
                index = skip_ws(key_end)
                if expect(index) != ":":
                    raise CompositionFormatError("expected ':' after object key")
                value, index = parse_value(index + 1)
                members.append((key, value))
                index = skip_ws(index)
                if expect(index) == ",":
                    index += 1
                    continue
                if expect(index) == "}":
                    return _JsonNode("object", start, index + 1, members=members), index + 1
                raise CompositionFormatError("expected ',' or '}' in object")
        if char == "[":
            start = index
            items: list[_JsonNode] = []
            index = skip_ws(index + 1)
            if expect(index) == "]":
                return _JsonNode("array", start, index + 1, items=items), index + 1
            while True:
                value, index = parse_value(index)
                items.append(value)
                index = skip_ws(index)
                if expect(index) == ",":
                    index += 1
                    continue
                if expect(index) == "]":
                    return _JsonNode("array", start, index + 1, items=items), index + 1
                raise CompositionFormatError("expected ',' or ']' in array")
        if char == '"':
            end = parse_string(index)
            return _JsonNode("scalar", index, end), end
        end = index
        while end < len(text) and text[end] not in _JSON_WS + ",]}":
            end += 1
        if end == index:
            raise CompositionFormatError(f"unexpected character {char!r}")
        return _JsonNode("scalar", index, end), end

    node, position = parse_value(0)
    if skip_ws(position) != len(text):
        raise CompositionFormatError("trailing content after the document")
    return node


def _json_block(value: Any, indent: str, unit: int) -> str:
    return json.dumps(value, indent=unit, ensure_ascii=False).replace("\n", "\n" + indent)


def _json_indent_unit(text: str, root: _JsonNode) -> int:
    """Indent width of the root's members relative to the root, defaulting to 2."""
    root_width = len(_line_indent(text, root.start))
    for _key, node in root.members or []:
        width = len(_line_indent(text, node.start)) - root_width
        if width > 0:
            return width
    return 2


def _splice_json_container(
    text: str,
    container: str,
    key_field: str,
    upserts: Mapping[str, Any],
    removals: set[str],
) -> str:
    root = _parse_json_spans(text)
    if root.kind != "object":
        raise CompositionFormatError("must be an object at its root")
    unit = _json_indent_unit(text, root)
    root_indent = _line_indent(text, root.start)
    member = next((node for key, node in root.members or [] if key == container), None)
    if member is None:
        if not upserts:
            return text
        member_indent = root_indent + " " * unit
        item_indent = member_indent + " " * unit
        body = (",\n" + item_indent).join(_json_block(value, item_indent, unit) for value in upserts.values())
        addition = f"{json.dumps(container)}: [\n{item_indent}{body}\n{member_indent}]"
        members = root.members or []
        if members:
            last = members[-1][1]
            return text[: last.end] + ",\n" + member_indent + addition + text[last.end :]
        return text[: root.start] + "{\n" + member_indent + addition + "\n" + root_indent + "}" + text[root.end :]
    if member.kind != "array":
        raise CompositionFormatError(f"{container} must be a list")
    items = member.items or []
    keys: list[str | None] = []
    for item in items:
        found = None
        for key, value in (item.members or []) if item.kind == "object" else []:
            if key == key_field and value.kind == "scalar":
                parsed = json.loads(text[value.start : value.end])
                found = parsed if isinstance(parsed, str) else None
        keys.append(found)
    container_indent = _line_indent(text, member.start)
    if items:
        if not all(_starts_own_line(text, item.start) for item in items):
            raise CompositionFormatError(f"{container} lists its entries on one line")
        item_indent = _line_indent(text, items[0].start)
    else:
        item_indent = container_indent + " " * unit

    edits: list[tuple[int, int, str]] = []
    removed = [key is not None and key in removals for key in keys]
    surviving = [item for item, gone in zip(items, removed, strict=True) if not gone]
    appended = [value for key, value in upserts.items() if key not in keys]
    for first, last in _removal_runs(removed):
        if first > 0:
            edits.append((items[first - 1].end, items[last].end, ""))
        elif last < len(items) - 1:
            edits.append((items[0].start, items[last + 1].start, ""))
        elif not appended:
            edits.append((member.start + 1, member.end - 1, ""))
    for item, key in zip(items, keys, strict=True):
        if key is not None and key in upserts and key not in removals:
            edits.append((item.start, item.end, _json_block(upserts[key], _line_indent(text, item.start), unit)))
    if appended:
        blocks = (",\n" + item_indent).join(_json_block(value, item_indent, unit) for value in appended)
        if surviving:
            anchor = surviving[-1].end
            edits.append((anchor, anchor, ",\n" + item_indent + blocks))
        else:
            edits.append((member.start + 1, member.end - 1, "\n" + item_indent + blocks + "\n" + container_indent))
    return _apply_edits(text, edits)


# --------------------------------------------------------------------------------------
# YAML: node marks locate each block-sequence item's lines; comments stay where they are.
# --------------------------------------------------------------------------------------


def _next_line(text: str, index: int) -> int:
    newline = text.find("\n", index)
    return len(text) if newline == -1 else newline + 1


def _yaml_dash(text: str, item_start: int) -> tuple[int, int]:
    """(line start, dash column) of the ``- `` that introduces a block-sequence item."""
    line_start = _line_start(text, item_start)
    prefix = text[line_start:item_start]
    stripped = prefix.lstrip(" ")
    if not stripped.startswith("-") or stripped[1:].strip() != "":
        raise CompositionFormatError("sequence entries must each start with '- ' on their own line")
    return line_start, len(prefix) - len(stripped)


def _yaml_item_end(text: str, line_start: int, dash_column: int, limit: int) -> int:
    """End of an item's own lines; later blank lines and comments at or left of its dash stay outside."""
    end = _next_line(text, line_start)
    cursor = end
    while cursor < limit:
        line_end = _next_line(text, cursor)
        line = text[cursor:line_end].rstrip("\r\n")
        content = line.lstrip(" ")
        column = len(line) - len(content)
        if content == "" or (content.startswith("#") and column <= dash_column):
            cursor = line_end
            continue
        if column <= dash_column:
            break
        end = line_end
        cursor = line_end
    return end


def _yaml_item_text(value: Any, dash_column: int) -> str:
    rendered = yaml.safe_dump(value, sort_keys=False, allow_unicode=True, default_flow_style=False, width=4096)
    lines = rendered.rstrip("\n").split("\n")
    pad = " " * dash_column
    return "\n".join([pad + "- " + lines[0], *(pad + "  " + line for line in lines[1:])]) + "\n"


def _splice_yaml_container(
    text: str,
    container: str,
    key_field: str,
    upserts: Mapping[str, Any],
    removals: set[str],
) -> str:
    try:
        root = yaml.compose(text)
    except yaml.YAMLError as exc:
        raise CompositionFormatError(f"cannot be parsed: {exc}") from exc
    if root is not None and not isinstance(root, yaml.MappingNode):
        raise CompositionFormatError("must be a mapping at its root")
    if text and not text.endswith("\n"):
        text += "\n"
    pair = next(
        (
            (key_node, value_node) for key_node, value_node in (root.value if root is not None else [])
            if isinstance(key_node, yaml.ScalarNode) and key_node.value == container
        ),
        None,
    )
    if pair is None:
        if not upserts:
            return text
        return text + f"{container}:\n" + "".join(_yaml_item_text(value, 2) for value in upserts.values())
    key_node, sequence = pair
    key_column = key_node.start_mark.column
    key_line = _line_start(text, key_node.start_mark.index)
    empty_value = (
        isinstance(sequence, yaml.ScalarNode) and sequence.value in {"", "~", "null"} and not sequence.style
    ) or (isinstance(sequence, yaml.SequenceNode) and not sequence.value)
    if empty_value:
        if not upserts:
            return text
        if isinstance(sequence, yaml.SequenceNode) and not sequence.flow_style:
            raise CompositionFormatError(f"{container} has an unexpected empty block form")
        replacement = " " * key_column + f"{container}:\n" + "".join(
            _yaml_item_text(value, key_column + 2) for value in upserts.values()
        )
        # The empty value sits on its key's line (``pages:`` or ``pages: []``); rewrite that line only.
        return text[:key_line] + replacement + text[_next_line(text, key_node.start_mark.index) :]
    if not isinstance(sequence, yaml.SequenceNode) or sequence.flow_style:
        raise CompositionFormatError(f"{container} must be a block list ('- ' entries) to be spliced")
    items = sequence.value
    starts = [_yaml_dash(text, item.start_mark.index) for item in items]
    dash_column = starts[0][1]
    if any(column != dash_column for _start, column in starts):
        raise CompositionFormatError(f"{container} entries are not aligned")
    limits = [start for start, _column in starts[1:]] + [len(text)]
    extents = [
        (start, _yaml_item_end(text, start, dash_column, limit))
        for (start, _column), limit in zip(starts, limits, strict=True)
    ]
    keys: list[str | None] = []
    for item in items:
        found = None
        if isinstance(item, yaml.MappingNode):
            for item_key, item_value in item.value:
                if (
                    isinstance(item_key, yaml.ScalarNode) and item_key.value == key_field
                    and isinstance(item_value, yaml.ScalarNode)
                ):
                    found = item_value.value
        keys.append(found)
    removed = [key is not None and key in removals for key in keys]
    appended = [value for key, value in upserts.items() if key not in keys]
    if all(removed) and not appended:
        # Nothing survives: the container becomes an explicit empty list on its key line.
        return text[:key_line] + " " * key_column + f"{container}: []\n" + text[extents[-1][1] :]

    edits: list[tuple[int, int, str]] = []
    for index, key in enumerate(keys):
        start, end = extents[index]
        if removed[index]:
            edits.append((start, end, ""))
        elif key is not None and key in upserts:
            edits.append((start, end, _yaml_item_text(upserts[key], dash_column)))
    if appended:
        gap = ""
        if len(extents) > 1 and any(line.strip() == "" for line in text[extents[0][1] : starts[1][0]].splitlines()):
            gap = "\n"
        anchor = extents[-1][1]
        edits.append((anchor, anchor, "".join(gap + _yaml_item_text(value, dash_column) for value in appended)))
    return _apply_edits(text, edits)


__all__ = [
    "KEYED_APP_ROOT_PATHS",
    "KEYED_ROOTS",
    "KEYED_WORKFLOW_ROOT_PATHS",
    "OWNERSHIP_CONTENT_HASH",
    "WORKFLOW_REGISTRY_PATH",
    "CompositionFormatError",
    "KeyedRoot",
    "content_sha256",
    "entry_sha256",
    "keyed_entries",
    "merged_document",
    "new_document_text",
    "parse_document",
    "splice_document",
]
