"""Render a capability pack's ``.j2`` template as untrusted text.

A pack is rendered to plan an install, before anyone has approved it, so a
template may only turn plain data into text. It gets no Python object it could
walk to the interpreter, no function beyond a few text filters, and no other
template.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from decimal import Decimal
from pathlib import PurePath
from typing import Any
from uuid import UUID

from jinja2 import StrictUndefined, meta
from jinja2.runtime import LoopContext
from jinja2.sandbox import ImmutableSandboxedEnvironment, SecurityError

MAX_TEMPLATE_CHARS = 1_000_000
MAX_RENDERED_CHARS = 4_000_000
MAX_REPEATED_ITEMS = 100_000
MAX_INTEGER_BITS = 4_096
_MAX_DATA_DEPTH = 64

# Left out: attr and format reach attributes by name, and the padding and batching filters
# allocate whatever width a template asks for.
_FILTERS = frozenset({
    "abs", "capitalize", "count", "d", "default", "dictsort", "e", "escape", "first", "float", "groupby", "int", "items", "join",
    "last", "length", "list", "lower", "map", "max", "min", "reject", "rejectattr", "replace", "reverse", "round", "safe", "select",
    "selectattr", "sort", "string", "striptags", "sum", "title", "tojson", "trim", "truncate", "unique", "upper", "urlencode",
    "wordcount",
})
_METHODS: dict[type, frozenset[str]] = {
    str: frozenset({
        "capitalize", "casefold", "count", "endswith", "find", "isalnum", "isalpha", "isdigit", "islower", "isspace", "isupper",
        "join", "lower", "lstrip", "partition", "removeprefix", "removesuffix", "replace", "rpartition", "rsplit", "rstrip",
        "split", "splitlines", "startswith", "strip", "swapcase", "title", "upper",
    }),
    dict: frozenset({"get", "items", "keys", "values"}),
    list: frozenset({"count", "index"}),
    tuple: frozenset({"count", "index"}),
}
_MISSING = object()


class _PackTemplateEnvironment(ImmutableSandboxedEnvironment):
    intercepted_binops = frozenset({"*", "**", "%"})

    def __init__(self) -> None:
        super().__init__(undefined=StrictUndefined)
        # cycler, joiner, namespace, lipsum, dict and range: none is needed to turn data into text.
        self.globals.clear()
        self.filters = {name: function for name, function in self.filters.items() if name in _FILTERS}

    def is_safe_attribute(self, obj: Any, attr: str, value: Any) -> bool:
        if not super().is_safe_attribute(obj, attr, value):
            return False
        return isinstance(obj, LoopContext) or attr in _METHODS.get(type(obj), frozenset())

    def wrap_str_format(self, value: Any) -> Any:
        # The sandbox would allow a guarded str.format, which still pads to any width.
        if super().wrap_str_format(value) is not None:
            raise SecurityError("string formatting is not available to a pack template")
        return None

    def call_binop(self, context: Any, operator: str, left: Any, right: Any) -> Any:
        sequences = [operand for operand in (left, right) if isinstance(operand, str | list | tuple)]
        if operator == "%" and isinstance(left, str):
            raise SecurityError("string formatting is not available to a pack template")
        if operator == "*" and sequences:
            count = next((operand for operand in (left, right) if isinstance(operand, int)), 0)
            if len(sequences[0]) * max(count, 0) > MAX_REPEATED_ITEMS:
                raise SecurityError("a pack template repeats a value too many times")
        if operator in {"*", "**"} and isinstance(left, int) and isinstance(right, int):
            bits = left.bit_length() * max(right, 0) if operator == "**" else left.bit_length() + right.bit_length()
            if bits > MAX_INTEGER_BITS:
                raise SecurityError("a pack template computes too large a number")
        return super().call_binop(context, operator, left, right)


_ENVIRONMENT = _PackTemplateEnvironment()


def _plain(value: Any, depth: int = 0) -> Any:
    """``value`` as data with no behaviour of its own, or ``_MISSING`` where it is anything else."""
    if depth > _MAX_DATA_DEPTH:
        raise SecurityError("template data is nested too deeply")
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, str):
        return str.__str__(value)
    if isinstance(value, int):
        return int(value)
    if isinstance(value, float):
        return float(value)
    if isinstance(value, date | Decimal | PurePath | UUID):
        return str(value)
    if isinstance(value, Mapping):
        pairs = ((_plain(key, depth + 1), _plain(item, depth + 1)) for key, item in value.items())
        return {key: item for key, item in pairs if key is not _MISSING and item is not _MISSING}
    if isinstance(value, list | tuple | set | frozenset):
        items = [item for item in (_plain(item, depth + 1) for item in value) if item is not _MISSING]
        return tuple(items) if isinstance(value, tuple) else set(items) if isinstance(value, set | frozenset) else items
    return _MISSING


def render_pack_template(source: str, variables: Mapping[str, Any]) -> str:
    """Render one template with the plain data among the variables it names."""
    if len(source) > MAX_TEMPLATE_CHARS:
        raise SecurityError(f"a pack template is longer than {MAX_TEMPLATE_CHARS} characters")
    template = _ENVIRONMENT.from_string(source)
    data: dict[str, Any] = {}
    for name in meta.find_undeclared_variables(_ENVIRONMENT.parse(source)):
        value = _plain(variables[name]) if name in variables else _MISSING
        if value is not _MISSING:
            data[name] = value
    parts: list[str] = []
    size = 0
    for part in template.generate(data):
        size += len(part)
        if size > MAX_RENDERED_CHARS:
            raise SecurityError(f"a pack template renders more than {MAX_RENDERED_CHARS} characters")
        parts.append(part)
    return "".join(parts)
