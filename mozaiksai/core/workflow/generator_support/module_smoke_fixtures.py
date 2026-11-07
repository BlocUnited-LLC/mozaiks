"""Bounded test inputs for the existing contained app observer, never app policy."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal, Self

import yaml
from jsonschema import Draft7Validator
from pydantic import BaseModel, ConfigDict, Field, model_validator

from mozaiksai.core.runtime.app.auth_contract import AppAuthContract
from mozaiksai.core.runtime.persistence.intent_loader import iter_data_contract_collections
from mozaiksai.core.semantics.closed_contract_schema import import_closed_contract_schema
from mozaiksai.core.semantics.closed_contracts import ObjectContract, ScalarContract

from .data_contract_fields import read_lookup_field, record_id_field
from .module_action_inventory import canonical_read_action_id, canonical_write_action_id

SCHEMA_VERSION = "mozaiks.module_smoke_fixtures.v1"
MAX_FIXTURE_BYTES = 131072
_Name = Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")]
_Role = Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[a-z][a-z0-9_]*$")]
_Roles = Annotated[list[_Role], Field(max_length=16)]


class _FixtureModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


def _unique(values: list[str], label: str) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"smoke fixture repeats {label}")


def _json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    _unique([key for key, _ in pairs], "literal object property")
    return dict(pairs)


def _literal(value: str) -> Any:
    def reject_constant(_value: str) -> Any:
        raise ValueError("smoke fixture literals require finite JSON values")

    try:
        result = json.loads(value, object_pairs_hook=_json_object, parse_constant=reject_constant)
    except RecursionError as exc:
        raise ValueError("smoke fixture literal exceeds the JSON nesting bound") from exc
    stack = [(result, 0)]
    nodes = 0
    while stack:
        item, depth = stack.pop()
        nodes += 1
        if nodes > 256 or depth > 8:
            raise ValueError("smoke fixture literal exceeds depth or node bounds")
        if isinstance(item, float) and not math.isfinite(item):
            raise ValueError("smoke fixture literals require finite JSON values")
        if isinstance(item, dict):
            stack.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            stack.extend((child, depth + 1) for child in item)
    return result


class SmokeFixtureParam(_FixtureModel):
    field: _Name
    source: Literal["literal", "response", "actor_subject", "relative_date", "relative_datetime", "record"]
    value_json: Annotated[str, Field(max_length=4096)] | None = None
    step_id: _Name | None = None
    response_path: Annotated[list[_Name], Field(min_length=1, max_length=8)] | None = None
    offset_days: Annotated[int, Field(ge=-3650, le=3650)] | None = None
    record_field: _Name | None = None

    @model_validator(mode="after")
    def source_fields(self) -> Self:
        fields = {"value_json", "step_id", "response_path", "offset_days", "record_field"}
        required = {
            "literal": {"value_json"}, "response": {"step_id", "response_path"},
            "actor_subject": set(), "relative_date": {"offset_days"},
            "relative_datetime": {"offset_days"}, "record": {"record_field"},
        }[self.source]
        supplied = {name for name in fields if getattr(self, name) is not None}
        if supplied != required:
            raise ValueError(f"smoke fixture source {self.source!r} requires exactly {sorted(required)}")
        if self.value_json is not None:
            _literal(self.value_json)
        return self


_Params = Annotated[list[SmokeFixtureParam], Field(max_length=64)]


class SmokeFixtureSetup(_FixtureModel):
    id: _Name
    module: _Name
    action: _Name
    roles: _Roles
    params: _Params

    @model_validator(mode="after")
    def unique_inputs(self) -> Self:
        _unique(self.roles, "setup role")
        _unique([param.field for param in self.params], "setup input field")
        if any(param.source == "record" for param in self.params):
            raise ValueError("smoke fixture setup cannot access the observer's target record")
        return self


class ModuleSmokeFixture(_FixtureModel):
    action: _Name
    roles: _Roles
    setup: Annotated[list[SmokeFixtureSetup], Field(max_length=24)]
    params: _Params

    @model_validator(mode="after")
    def unique_inputs(self) -> Self:
        _unique(self.roles, "target role")
        _unique([step.id for step in self.setup], "setup id")
        _unique([param.field for param in self.params], "target input field")
        return self


class ModuleSmokeFixtures(_FixtureModel):
    schema_version: Literal["mozaiks.module_smoke_fixtures.v1"]
    fixtures: Annotated[list[ModuleSmokeFixture], Field(min_length=1, max_length=64)]

    @model_validator(mode="after")
    def unique_targets(self) -> Self:
        _unique([fixture.action for fixture in self.fixtures], "target action")
        if sum(len(fixture.setup) for fixture in self.fixtures) > 128:
            raise ValueError("module smoke fixtures exceed the total setup bound")
        return self


def parse_module_smoke_fixtures(payload: Any) -> ModuleSmokeFixtures:
    """Validate the finite document before deterministic YAML materialization."""
    stack = [(payload, 0)]
    nodes = 0
    while stack:
        value, depth = stack.pop()
        nodes += 1
        if depth > 16 or nodes > 16384:
            raise ValueError("module smoke fixtures exceed document depth or node bounds")
        if isinstance(value, dict):
            stack.extend((item, depth + 1) for item in value.values())
        elif isinstance(value, list):
            stack.extend((item, depth + 1) for item in value)
    if len(json.dumps(payload, ensure_ascii=False).encode("utf-8")) > MAX_FIXTURE_BYTES:
        raise ValueError("module smoke fixtures exceed the byte bound")
    return ModuleSmokeFixtures.model_validate(payload)


def _schema_at(schema: Mapping[str, Any], path: list[str]) -> Mapping[str, Any]:
    current = schema
    for part in path:
        properties = current.get("properties")
        if current.get("type") != "object" or not isinstance(properties, dict) or part not in properties:
            raise ValueError(f"smoke fixture response path {path!r} is not a declared object field")
        current = properties[part]
        if not isinstance(current, dict):
            raise ValueError("smoke fixture response path has no declared schema")
    kind = current.get("type")
    if type(kind) is not str or kind not in {"string", "integer", "number", "boolean", "null", "object", "array"}:
        raise ValueError("smoke fixture response field requires one declared type")
    return current


def _compatible(source: Mapping[str, Any], destination: Mapping[str, Any]) -> None:
    source_type, target_type = source.get("type"), destination.get("type")
    if source_type != target_type and (source_type, target_type) != ("integer", "number"):
        raise ValueError("smoke fixture reference type does not match its input field")
    if target_type in {"array", "object"} and source != destination:
        raise ValueError("smoke fixture structured references require the exact input schema")
    if destination.get("enum") and (
        not source.get("enum") or not all(value in destination["enum"] for value in source["enum"])
    ):
        raise ValueError("smoke fixture reference enum does not fit its input field")


def _validate_params(
    params: list[SmokeFixtureParam], schema: Mapping[str, Any],
    responses: Mapping[str, Mapping[str, Any]], record_fields: Mapping[str, Mapping[str, Any]],
) -> None:
    import_closed_contract_schema(dict(schema))
    if schema.get("type") != "object":
        raise ValueError("smoke fixture action input must be a closed object")
    properties = schema.get("properties", {})
    supplied = {param.field for param in params}
    if not supplied <= properties.keys() or not set(schema.get("required", [])) <= supplied:
        raise ValueError("smoke fixture params must cover required and only declared input fields")
    for param in params:
        destination = properties[param.field]
        if param.source == "literal":
            if not Draft7Validator(destination).is_valid(_literal(param.value_json or "")):
                raise ValueError(f"smoke fixture literal does not satisfy input field {param.field!r}")
        elif param.source == "response":
            if param.step_id is None or param.step_id not in responses:
                raise ValueError("smoke fixture response must reference an earlier setup step; forward references and cycles are forbidden")
            _compatible(_schema_at(responses[param.step_id], param.response_path or []), destination)
        elif param.source == "record":
            if param.record_field is None or param.record_field not in record_fields:
                raise ValueError("smoke fixture record source requires a declared field of the canonical get/update/delete target")
            _compatible(record_fields[param.record_field], destination)
        else:
            _compatible({"type": "string"}, destination)


def _validate_roles(roles: list[str], action: Mapping[str, Any], grants: Mapping[str, set[str]], scopes: set[str]) -> None:
    if any(role not in grants for role in roles):
        raise ValueError("smoke fixture names an unknown role")
    required = set(action.get("permissions") or []) - scopes
    held = set().union(*(grants[role] for role in roles)) if roles else set()
    if not required <= held:
        raise ValueError("smoke fixture roles do not grant every required action permission")
    for role in roles:
        other = set().union(*(grants[item] for item in roles if item != role))
        if not (required & grants[role]) - other:
            raise ValueError("smoke fixture role is unrelated or redundant for the action")


def _action(modules: Mapping[str, Mapping[str, Any]], module: str, action: str) -> Mapping[str, Any]:
    matches = [item for item in modules.get(module, {}).get("actions", []) if item.get("id") == action]
    if len(matches) != 1:
        raise ValueError(f"smoke fixture action {module}.{action} must resolve exactly once")
    result: Mapping[str, Any] = matches[0]
    if result.get("api_surface") in {"internal", "admin_internal"}:
        raise ValueError("smoke fixtures cannot invoke internal actions")
    return result


def fixture_record_identity(
    fixture: ModuleSmokeFixture | None, *, collection: Mapping[str, Any],
    operation: Literal["get", "update", "delete"],
) -> tuple[str, str]:
    """Return the declared input and stored field that identify the observed row."""
    if operation not in {"get", "update", "delete"}:
        raise ValueError("smoke fixture record identity requires get, update, or delete")
    stored_field = (read_lookup_field(collection) if operation == "get" else
                    record_id_field(collection, [field["name"] for field in collection.get("fields") or []]))
    input_field = "id" if operation == "get" else stored_field
    if fixture is None:
        return input_field, stored_field
    if operation == "get":
        matches = [binding for binding in fixture.params
                   if binding.source == "record" and binding.record_field == stored_field]
        if len(matches) != 1:
            raise ValueError(
                f"smoke fixture {fixture.action!r} requires exactly one declared input bound with "
                f"source=record and record_field={stored_field!r}; setup results cannot replace the tested row"
            )
        return matches[0].field, stored_field
    binding = next((binding for binding in fixture.params if binding.field == input_field), None)
    if binding is None or binding.source != "record" or binding.record_field != stored_field:
        raise ValueError(
            f"smoke fixture {fixture.action!r} input {input_field!r} requires source=record and "
            f"record_field={stored_field!r}; setup results cannot replace the tested row"
        )
    return input_field, stored_field


def _record_targets(
    data_contract: Any,
) -> dict[tuple[str, str], tuple[dict[str, dict[str, Any]], Mapping[str, Any], Literal["get", "update", "delete"]]]:
    result: dict[tuple[str, str], tuple[dict[str, dict[str, Any]], Mapping[str, Any], Literal["get", "update", "delete"]]] = {}
    for module, kind, collection in iter_data_contract_collections(data_contract, require_complete_ownership=False):
        if kind != "module":
            continue
        fields = {}
        for field in collection.get("fields") or []:
            schema: dict[str, Any] = {"type": {"date": "string", "datetime": "string"}.get(field["type"], field["type"])}
            if field.get("enum"):
                schema["enum"] = field["enum"]
            fields[field["name"]] = schema
        if "_id" in {record_id_field(collection, list(fields)), read_lookup_field(collection)}:
            fields["_id"] = {"type": "string"}
        actions: list[tuple[str, Literal["get", "update", "delete"]]] = [
            (canonical_read_action_id(collection["name"], "get"), "get"),
        ]
        if collection.get("entity"):
            actions.extend([
                (canonical_write_action_id(collection["entity"], "update"), "update"),
                (canonical_write_action_id(collection["entity"], "delete"), "delete"),
            ])
        for action, operation in actions:
            key = module, action
            if key in result:
                raise ValueError("smoke fixture canonical record action has ambiguous collection ownership")
            result[key] = fields, collection, operation
    return result


def _default_read_inputs_compatible(schema: dict[str, Any], operation: str) -> bool:
    """Prove finite defaults without treating descriptive annotations as assertions."""
    projection = deepcopy(schema)
    stack = [projection]
    visited: set[int] = set()
    while stack:
        node = stack.pop()
        if not isinstance(node, dict) or id(node) in visited:
            continue
        visited.add(id(node))
        node.pop("description", None)
        properties = node.get("properties")
        if isinstance(properties, dict):
            stack.extend(properties.values())
        if "items" in node:
            stack.append(node["items"])
    try:
        contract = import_closed_contract_schema(projection)
    except (TypeError, ValueError):
        return False
    if not isinstance(contract, ObjectContract):
        return False
    required = {prop.name for prop in contract.properties if prop.required}
    if operation == "list":
        return not required
    identifier = next((prop.contract for prop in contract.properties if prop.name == "id"), None)
    return (isinstance(identifier, ScalarContract) and identifier.kind == "string"
            and identifier.enum is None and not identifier.nullable and required <= {"id"})


def validate_module_fixture_record_identities(
    module_id: str, payload: Any, *, data_contract: Any, manifest: Mapping[str, Any] | None = None,
) -> None:
    """Close observed-row identity and finite read inputs before service work."""
    fixtures = parse_module_smoke_fixtures(payload).fixtures if payload is not None else []
    records = _record_targets(data_contract)
    failures = []
    for fixture in fixtures:
        target = records.get((module_id, fixture.action))
        if target is not None:
            try:
                fixture_record_identity(fixture, collection=target[1], operation=target[2])
            except ValueError as error:
                failures.append(f"{module_id}: {error}")
    if manifest is not None:
        from .code_files import _materialize_schema_contract

        actions = {action["id"]: action for action in manifest.get("actions") or []}
        supplied = {fixture.action for fixture in fixtures}
        for (owner, _), (_, collection, operation) in records.items():
            if owner != module_id or operation != "get" or not collection.get("entity"):
                continue
            # The ordinary CRUD observer only owns records whose create action exists.
            create = canonical_write_action_id(collection["entity"], "create")
            if create not in actions:
                continue
            for read in ("get", "list"):
                action_id = canonical_read_action_id(collection["name"], read)
                action = actions.get(action_id)
                if action is None or action.get("api_surface") in {"internal", "admin_internal"}:
                    continue
                if action_id in supplied:
                    continue
                schema = _materialize_schema_contract(action["input_schema"], closed_request=True)
                if _default_read_inputs_compatible(schema, read):
                    continue
                binding = (
                    "Bind exactly one actual declared input with source=record and "
                    f"record_field={read_lookup_field(collection)!r}. " if read == "get" else ""
                )
                default = "{id: string}" if read == "get" else "{}"
                failures.append(
                    f"{module_id}: read {action_id!r} does not admit the observer default {default}; "
                    "declare its exact input bindings in module_contract.fixtures_yaml. "
                    f"{binding}Keep the admitted input schema, permissions and API surface; "
                    "do not rename inputs or invent prerequisite records to satisfy the observer."
                )
    if failures:
        raise ValueError("\n".join(failures))


def load_module_smoke_fixtures(files: Mapping[str, str | bytes]) -> dict[tuple[str, str], ModuleSmokeFixture]:
    """Close all fixture references against exact bundle contracts before any request."""
    paths = [path for path in files if path.endswith("/contracts/fixtures.yaml")]
    if not paths:
        return {}
    if any(not re.fullmatch(r"modules/[A-Za-z_][A-Za-z0-9_]*/contracts/fixtures\.yaml", path) for path in paths):
        raise ValueError("module smoke fixtures require a canonical module companion path")
    modules = {}
    for path, content in files.items():
        match = re.fullmatch(r"modules/([A-Za-z_][A-Za-z0-9_]*)/module\.yaml", path)
        if match:
            manifest = yaml.safe_load(content)
            if not isinstance(manifest, dict) or manifest.get("module", {}).get("id") != match[1]:
                raise ValueError("smoke fixture module identity must match its bundle path")
            modules[match[1]] = manifest
    grants: dict[str, set[str]] = {}
    scopes: set[str] = set()
    if "config/auth.yaml" in files:
        auth = AppAuthContract.model_validate(yaml.safe_load(files["config/auth.yaml"]))
        grants = {role.id: set(role.permissions) for role in auth.authorization.roles}
        scopes = set(auth.frontend.default_scopes)
    records = _record_targets(json.loads(files["data/contract.json"])) if "data/contract.json" in files else {}
    result = {}
    for path in sorted(paths):
        content = files[path]
        raw = content.encode("utf-8") if isinstance(content, str) else content
        if not isinstance(raw, bytes) or len(raw) > MAX_FIXTURE_BYTES:
            raise ValueError("module smoke fixtures require bounded UTF-8 text")
        content = raw.decode("utf-8", errors="strict")
        document = parse_module_smoke_fixtures(yaml.safe_load(content))
        module = path.split("/")[1]
        for fixture in document.fixtures:
            target = _action(modules, module, fixture.action)
            _validate_roles(fixture.roles, target, grants, scopes)
            responses: dict[str, Mapping[str, Any]] = {}
            for step in fixture.setup:
                action = _action(modules, step.module, step.action)
                _validate_roles(step.roles, action, grants, scopes)
                _validate_params(step.params, action.get("input_schema", {}), responses, {})
                output = action.get("output_schema", {})
                Draft7Validator.check_schema(output)
                responses[step.id] = output
            record_target = records.get((module, fixture.action))
            _validate_params(fixture.params, target.get("input_schema", {}), responses,
                             record_target[0] if record_target else {})
            if record_target is not None:
                fixture_record_identity(fixture, collection=record_target[1], operation=record_target[2])
            result[module, fixture.action] = fixture
    if len(result) > 256 or sum(len(fixture.setup) for fixture in result.values()) > 512:
        raise ValueError("app smoke fixtures exceed the total execution bound")
    return result


def resolve_fixture_params(
    bindings: list[SmokeFixtureParam], *, responses: Mapping[str, Any], actor_subject: str,
    record: Mapping[str, Any] | None, now: datetime,
) -> dict[str, Any]:
    """Resolve declared inputs only; the observer validates and executes the request."""
    if not actor_subject or now.tzinfo is None:
        raise ValueError("smoke fixture resolution requires a trusted subject and aware run clock")
    result = {}
    for binding in bindings:
        if binding.source == "literal":
            value = _literal(binding.value_json or "")
        elif binding.source == "response":
            if binding.step_id is None or binding.step_id not in responses:
                raise ValueError("smoke fixture setup response is unavailable")
            value = responses[binding.step_id]
            for part in binding.response_path or []:
                if not isinstance(value, Mapping) or part not in value:
                    raise ValueError("smoke fixture setup response field is unavailable")
                value = value[part]
        elif binding.source == "record":
            if record is None or binding.record_field is None or binding.record_field not in record:
                raise ValueError("smoke fixture target record field is unavailable")
            value = record[binding.record_field]
        elif binding.source == "actor_subject":
            value = actor_subject
        else:
            moment = now.astimezone(UTC) + timedelta(days=binding.offset_days or 0)
            value = moment.date().isoformat() if binding.source == "relative_date" else moment.isoformat()
        result[binding.field] = deepcopy(value)
    return result
