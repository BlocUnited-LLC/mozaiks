"""Close generated page reads against the declared module action response."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any


def schema_at_path(schema: Any, path: Any) -> dict[str, Any] | None:
    """Resolve the object paths supported by page value/data key bindings."""
    if not isinstance(path, str) or not path:
        return None
    for key in path.split("."):
        if not isinstance(schema, dict):
            return None
        properties = schema.get("properties")
        if not isinstance(properties, dict):
            return None
        schema = properties.get(key)
    return schema if isinstance(schema, dict) else None


def schema_field_paths(schema: Any, prefix: str = "") -> list[str]:
    """Enumerate declared response paths without guessing fields or resolving refs."""
    properties = schema.get("properties") if isinstance(schema, dict) else None
    if not isinstance(properties, dict):
        return []
    paths: list[str] = []
    for key, child in sorted(properties.items()):
        path = f"{prefix}.{key}" if prefix else str(key)
        paths.append(path)
        paths.extend(schema_field_paths(child, path))
    return paths


def contract_key_for_endpoint(endpoint: Any) -> str | None:
    """Return ``module/action`` for a compiled module endpoint, else None."""
    if isinstance(endpoint, str) and endpoint.startswith("/api/modules/"):
        return endpoint.removeprefix("/api/modules/")
    return None


def iter_data_bound_sections(document: Any) -> Iterator[tuple[dict[str, Any], str | None, str]]:
    """Yield every section with the data contract it reads, inherited from containers.

    A section that names its own ``api_endpoint`` binds to that contract; a
    section without one inherits its container's. A non-module endpoint binds
    to nothing. The location string names the page and section ids.
    """

    def visit(sections: Any, location: str, inherited_key: str | None) -> Iterator[tuple[dict[str, Any], str | None, str]]:
        for section in sections if isinstance(sections, list) else []:
            if not isinstance(section, dict) or not isinstance(section.get("config"), dict):
                continue
            config = section["config"]
            section_location = f"{location}/{section.get('id') or '<no-id>'}"
            contract_key = inherited_key
            if isinstance(config.get("api_endpoint"), str):
                contract_key = contract_key_for_endpoint(config["api_endpoint"])
            yield section, contract_key, section_location
            yield from visit(config.get("children"), section_location, contract_key)

    if isinstance(document, dict):
        yield from visit(document.get("sections"), str(document.get("name") or "page"), None)


def section_metrics(section: dict[str, Any]) -> list[tuple[dict[str, Any], str]]:
    """Return the metric items a section renders with their location suffixes."""
    config = section.get("config")
    if not isinstance(config, dict):
        return []
    primitive = section.get("primitive")
    if primitive == "SummaryStrip":
        return [
            (metric, f".items[{index}]")
            for index, metric in enumerate(config.get("items") or [])
            if isinstance(metric, dict)
        ]
    if primitive == "Metric":
        return [(config, "")]
    return []


def page_data_binding_errors(document: Any, contracts: dict[str, dict[str, Any]]) -> list[str]:
    """Validate compiled Metric/SummaryStrip and table fields, including inherited data."""
    failures: list[str] = []

    def fail(location: str, key: Any, contract_key: str, schema: Any, requirement: str) -> None:
        valid = ", ".join(schema_field_paths(schema)) or "(none declared)"
        failures.append(
            f"{location}: '{key}' {requirement} in '{contract_key}' output_schema. Valid fields: {valid}."
        )

    for section, contract_key, section_location in iter_data_bound_sections(document):
        action = contracts.get(contract_key or "")
        if action is None or not contract_key:
            continue
        config = section["config"]
        outputs = action.get("output_schema")
        for metric, suffix in section_metrics(section):
            metric_location = f"{section_location}{suffix}"
            if not metric.get("value_key") and metric.get("value") is None:
                fail(f"{metric_location}.value_key", metric.get("value_key"), contract_key, outputs,
                     "must select a declared response field when no static value is supplied")
            for field in ("value_key", "detail_key", "trend_key"):
                key = metric.get(field)
                if key is not None and schema_at_path(outputs, key) is None:
                    fail(f"{metric_location}.{field}", key, contract_key, outputs, "is not declared")
        if section.get("primitive") in {"DataTable", "ResourceTable"}:
            key = config.get("data_key")
            rows = schema_at_path(outputs, key) if key else outputs
            if not isinstance(rows, dict) or rows.get("type") != "array":
                fail(f"{section_location}.data_key", key, contract_key, outputs, "must select a declared array")
            else:
                item_schema = rows.get("items")
                properties = item_schema.get("properties", {}) if isinstance(item_schema, dict) else {}
                for column in config.get("columns") or []:
                    column_key = column.get("key") if isinstance(column, dict) else column
                    if column_key not in properties:
                        valid = ", ".join(sorted(properties)) or "(none declared)"
                        failures.append(
                            f"{section_location}.columns: '{column_key}' is not a declared row field in "
                            f"'{contract_key}' output_schema. Valid fields: {valid}."
                        )
            if config.get("total_key") is not None:
                total = schema_at_path(outputs, config["total_key"])
                if total is None or total.get("type") != "integer":
                    fail(f"{section_location}.total_key", config["total_key"], contract_key, outputs,
                         "must select a declared integer")
    return failures
