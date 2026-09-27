"""Close generated page reads against the declared module action response."""

from __future__ import annotations

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


def page_data_binding_errors(document: Any, contracts: dict[str, dict[str, Any]]) -> list[str]:
    """Validate compiled Metric/SummaryStrip and table fields, including inherited data."""
    failures: list[str] = []

    def fail(location: str, key: Any, contract_key: str, schema: Any, requirement: str) -> None:
        valid = ", ".join(schema_field_paths(schema)) or "(none declared)"
        failures.append(
            f"{location}: '{key}' {requirement} in '{contract_key}' output_schema. Valid fields: {valid}."
        )

    def visit(sections: Any, location: str, inherited_key: str | None = None) -> None:
        for section in sections if isinstance(sections, list) else []:
            if not isinstance(section, dict) or not isinstance(section.get("config"), dict):
                continue
            config = section["config"]
            section_location = f"{location}/{section.get('id') or '<no-id>'}"
            endpoint = config.get("api_endpoint")
            contract_key = inherited_key
            if isinstance(endpoint, str):
                contract_key = endpoint.removeprefix("/api/modules/") if endpoint.startswith("/api/modules/") else None
            action = contracts.get(contract_key or "")
            if action is not None and contract_key:
                outputs = action.get("output_schema")
                primitive = section.get("primitive")
                metrics = config.get("items", []) if primitive == "SummaryStrip" else [config] if primitive == "Metric" else []
                for index, metric in enumerate(metrics):
                    if not isinstance(metric, dict):
                        continue
                    metric_location = f"{section_location}.items[{index}]" if primitive == "SummaryStrip" else section_location
                    if not metric.get("value_key") and metric.get("value") is None:
                        fail(f"{metric_location}.value_key", metric.get("value_key"), contract_key, outputs,
                             "must select a declared response field when no static value is supplied")
                    for field in ("value_key", "detail_key", "trend_key"):
                        key = metric.get(field)
                        if key is not None and schema_at_path(outputs, key) is None:
                            fail(f"{metric_location}.{field}", key, contract_key, outputs, "is not declared")
                if primitive in {"DataTable", "ResourceTable"}:
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
            visit(config.get("children"), section_location, contract_key)

    if isinstance(document, dict):
        visit(document.get("sections"), str(document.get("name") or "page"))
    return failures
