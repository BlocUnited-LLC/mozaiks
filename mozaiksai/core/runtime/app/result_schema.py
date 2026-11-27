"""Project a result producer or receiver onto the finite, closed action-input profile."""

from __future__ import annotations

from typing import Any

from mozaiksai.core.semantics.closed_contract_schema import import_closed_contract_schema
from mozaiksai.core.semantics.closed_contracts import (
    MAX_CONTRACT_DEPTH,
    MAX_CONTRACT_NODES,
    ObjectContract,
)


def result_object_schema(schema: dict[str, Any], *, compiler_output: bool = False) -> dict[str, Any]:
    """Remove documentation and expand only the workflow compiler's local model definitions."""
    definitions = schema.get("$defs", {}) if compiler_output else {}
    count = 0

    def project(node: Any, depth: int) -> dict[str, Any]:
        nonlocal count
        count += 1
        if count > MAX_CONTRACT_NODES or depth > MAX_CONTRACT_DEPTH or not isinstance(node, dict):
            raise ValueError("Workflow result schema exceeds the finite closed-contract profile")
        out = {k: v for k, v in node.items() if k not in {"title", "description"}}
        if compiler_output:
            out.pop("default", None)
            ref = out.get("$ref")
            if ref is not None:
                if (set(out) != {"$ref"} or not isinstance(ref, str) or not ref.startswith("#/$defs/")
                        or ref.removeprefix("#/$defs/") not in definitions):
                    raise ValueError("Invalid local model reference in workflow result")
                return project(definitions[ref.removeprefix("#/$defs/")], depth + 1)
            if "const" in out and "enum" not in out:
                out["enum"] = [out.pop("const")]
        if isinstance(out.get("properties"), dict):
            out["properties"] = {name: project(field, depth + 1) for name, field in out["properties"].items()}
        if "items" in out:
            out["items"] = project(out["items"], depth + 1)
        return out

    root = {key: value for key, value in schema.items() if not (compiler_output and key == "$defs")}
    projected = project(root, 1)
    contract = import_closed_contract_schema(projected)
    if not isinstance(contract, ObjectContract):
        raise ValueError("Workflow result must be a closed object")
    return projected
