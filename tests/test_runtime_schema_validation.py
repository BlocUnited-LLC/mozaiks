"""Schema errors reject module input, output, emission, and event delivery."""

from __future__ import annotations

from typing import Any

import pytest

from mozaiksai.core.runtime.composition import schema_validation
from mozaiksai.core.runtime.composition.module_event_router import ModuleEventPayloadValidationError
from mozaiksai.core.runtime.composition.module_executor import ModuleExecutor, _validate_schema
from tests.test_module_event_router import (
    _handler_target_reaction,
    _loaded_module,
    _router,
    _with_event_schema,
)
from tests.test_module_executor_dispatch import _request

_BROKEN_SCHEMAS = (
    {"minLength": "three"},
    {"required": "field"},
    {"properties": []},
    {"type": "unknown"},
    {"properties": {"value": {"type": 3}}},
    {"$ref": "#/definitions/missing"},
    {"$ref": "urn:mozaiks:missing-schema"},
)


@pytest.mark.parametrize("schema", _BROKEN_SCHEMAS)
def test_malformed_or_unevaluable_schema_never_returns_success(schema: dict[str, Any]) -> None:
    first = schema_validation.validate_json_schema({"value": 1}, schema)
    second = schema_validation.validate_json_schema({"value": 1}, schema)
    assert first is not None
    assert first.category == "schema_invalid"
    assert first == second
    assert first.path == "$"
    assert first.schema_path.startswith("$")
    assert first.validator
    assert "0x" not in first.message
    assert "Traceback" not in first.message
    assert first.to_dict()["category"] == "schema_invalid"


@pytest.mark.parametrize("schema", [None, [], "", 0, 1])
def test_explicit_non_schema_inputs_are_invalid_in_the_strict_validator(schema: Any) -> None:
    diagnostic = schema_validation.validate_json_schema({}, schema)
    assert diagnostic is not None
    assert diagnostic.category == "schema_invalid"


@pytest.mark.parametrize("phase", ["normalization", "schema", "construction", "evaluation"])
def test_all_validator_exceptions_become_deterministic_schema_diagnostics(phase: str, monkeypatch) -> None:
    original_validator = schema_validation.jsonschema.Draft7Validator

    def fail(*_args, **_kwargs):
        raise RuntimeError("backend object at 0xDEADBEEF\nTraceback: unstable implementation detail")

    if phase == "normalization":
        monkeypatch.setattr(schema_validation, "normalize_nullable_schema", fail)
    elif phase == "schema":
        monkeypatch.setattr(original_validator, "check_schema", fail)
    else:
        class BrokenValidator:
            check_schema = staticmethod(original_validator.check_schema)

            def __init__(self, _schema):
                if phase == "construction":
                    fail()

            def iter_errors(self, _value):
                fail()

        monkeypatch.setattr(schema_validation.jsonschema, "Draft7Validator", BrokenValidator)
    first = schema_validation.validate_json_schema({}, {})
    second = schema_validation.validate_json_schema({}, {})
    assert first is not None
    assert first == second
    assert first.category == "schema_invalid"
    assert first.validator == phase
    assert first.path == first.schema_path == "$"
    assert "0x" not in first.message
    assert "Traceback" not in first.message


def test_normalization_recursion_failure_does_not_become_validation_success() -> None:
    cyclic = {"type": "object"}
    cyclic["properties"] = {"self": cyclic}
    diagnostic = schema_validation.validate_json_schema({}, cyclic)
    assert diagnostic is not None
    assert diagnostic.category == "schema_invalid"
    assert diagnostic.validator == "normalization"


def test_normalized_schema_is_checked_before_construction_and_value_evaluation(monkeypatch) -> None:
    original = schema_validation.jsonschema.Draft7Validator
    calls = []

    class RecordingValidator:
        @staticmethod
        def check_schema(schema):
            calls.append(("check", schema))
            original.check_schema(schema)

        def __init__(self, schema):
            calls.append(("construct", schema))
            self.validator = original(schema)

        def iter_errors(self, value):
            calls.append(("evaluate", value))
            return self.validator.iter_errors(value)

    monkeypatch.setattr(schema_validation.jsonschema, "Draft7Validator", RecordingValidator)
    source = {"type": "string", "nullable": True, "enum": ["ready"]}
    assert schema_validation.validate_json_schema(None, source) is None
    assert [phase for phase, _ in calls] == ["check", "construct", "evaluate"]
    assert calls[0][1]["type"] == ["string", "null"]
    assert calls[0][1]["enum"] == ["ready", None]
    assert source == {"type": "string", "nullable": True, "enum": ["ready"]}


def test_valid_schema_invalid_value_has_bounded_value_diagnostic() -> None:
    schema = {"type": "object", "properties": {"count": {"type": "integer"}}}
    diagnostic = schema_validation.validate_json_schema({"count": "not an integer"}, schema)
    assert diagnostic is not None
    assert diagnostic.category == "value_invalid"
    assert diagnostic.path == "$.count"
    assert diagnostic.schema_path == "$.properties.count.type"
    assert diagnostic.validator == "type"
    assert schema_validation.validate_json_schema({"count": 1}, schema) is None


def test_value_diagnostics_never_include_runtime_object_addresses() -> None:
    first = schema_validation.validate_json_schema(object(), {"type": "string"})
    second = schema_validation.validate_json_schema(object(), {"type": "string"})
    assert first is not None
    assert first == second
    assert first.category == "value_invalid"
    assert "0x" not in first.message


@pytest.mark.parametrize("boundary", ["value", "schema"])
def test_non_json_dictionary_keys_have_stable_diagnostic_paths(boundary: str) -> None:
    def diagnostic_for_fresh_key():
        if boundary == "value":
            return schema_validation.validate_json_schema(
                {object(): 1}, {"additionalProperties": {"type": "string"}},
            )
        return schema_validation.validate_json_schema(
            {}, {"properties": {object(): {"type": "unknown"}}},
        )

    first = diagnostic_for_fresh_key()
    second = diagnostic_for_fresh_key()
    assert first is not None
    assert first == second
    assert first.category == ("value_invalid" if boundary == "value" else "schema_invalid")
    expected_path = '$["<non-json-key>"]' if boundary == "value" else '$.properties["<non-json-key>"].type'
    assert (first.path if boundary == "value" else first.schema_path) == expected_path
    assert "0x" not in str(first.to_dict())


def test_malformed_schema_error_metadata_still_returns_stable_failure(monkeypatch) -> None:
    class BrokenPath:
        def __iter__(self):
            raise RuntimeError("path at 0xDEADBEEF")

    def malformed_schema_error(_schema):
        error = schema_validation.jsonschema.exceptions.SchemaError("unstable diagnostic")
        error.path = BrokenPath()
        error.validator = object()
        raise error

    monkeypatch.setattr(schema_validation.jsonschema.Draft7Validator, "check_schema", malformed_schema_error)
    first = schema_validation.validate_json_schema({}, {})
    assert first is not None
    assert first == schema_validation.validate_json_schema({}, {})
    assert first.category == "schema_invalid"
    assert first.schema_path == '$["<invalid-path>"]'
    assert first.validator == "schema"
    assert "0x" not in str(first.to_dict())


@pytest.mark.parametrize("value", [None, {}, [], False, 0, "anything"])
def test_explicit_empty_schema_is_valid_universal_draft7_contract(value: Any) -> None:
    assert schema_validation.validate_json_schema(value, {}) is None
    assert schema_validation.validate_json_schema(value, True) is None
    diagnostic = schema_validation.validate_json_schema(value, False)
    assert diagnostic is not None
    assert diagnostic.category == "value_invalid"


def test_absence_is_skipped_only_at_the_explicit_caller_boundary(monkeypatch) -> None:
    import mozaiksai.core.runtime.composition.module_executor as executor_module

    calls = []
    original = executor_module.validate_json_schema

    def record(value, schema):
        calls.append(schema)
        return original(value, schema)

    monkeypatch.setattr(executor_module, "validate_json_schema", record)
    assert _validate_schema({"anything": True}, None) is None
    assert calls == []
    assert _validate_schema({"anything": True}, {}) is None
    assert calls == [{}]
    assert _validate_schema({}, []) is not None


class _Handler:
    def __init__(self, result: Any = None):
        self.result = result
        self.calls = 0

    def run(self, _ctx, **_params):
        self.calls += 1
        return self.result


@pytest.mark.asyncio
@pytest.mark.parametrize("schema", (*_BROKEN_SCHEMAS, [], "", 0))
async def test_executor_rejects_bad_input_schema_before_handler(schema: Any) -> None:
    handler = _Handler({"value": 1})
    executor = ModuleExecutor()
    executor.register("probe", handler, action_schemas={"run": {"input": schema}})
    result = await executor.execute(_request(module="probe", action="run"))
    assert result.success is False
    assert result.error_code == "INVALID_PARAMS"
    assert handler.calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("schema", _BROKEN_SCHEMAS)
@pytest.mark.parametrize("value", [None, {"value": 1}])
async def test_executor_rejects_bad_output_schema_including_null_results(schema: Any, value: Any) -> None:
    handler = _Handler(value)
    executor = ModuleExecutor()
    executor.register("probe", handler, action_schemas={"run": {"output": schema}})
    result = await executor.execute(_request(module="probe", action="run"))
    assert handler.calls == 1
    assert result.success is False
    assert result.error_code == "INVALID_OUTPUT_SCHEMA"
    assert result.data is None


@pytest.mark.asyncio
async def test_output_value_invalid_policy_remains_warning_only() -> None:
    executor = ModuleExecutor()
    executor.register("probe", _Handler(None), action_schemas={"run": {"output": {"type": "object"}}})
    result = await executor.execute(_request(module="probe", action="run"))
    assert result.success is True
    assert result.data is None


@pytest.mark.asyncio
@pytest.mark.parametrize("schema", (*_BROKEN_SCHEMAS, [], "", 0))
async def test_executor_rejects_bad_event_schema_before_emission(schema: Any) -> None:
    emitted = []

    async def emit(event_type, envelope):
        emitted.append((event_type, envelope))

    class Handler:
        async def run(self, ctx):
            await ctx.emit("domain.probe.changed", {"value": 1})
            return {}

    executor = ModuleExecutor(event_emitter=emit)
    executor.register("probe", Handler(), event_payload_schemas={"domain.probe.changed": schema})
    result = await executor.execute(_request(module="probe", action="run"))
    assert result.success is False
    assert result.error_code == "INVALID_EVENT_PAYLOAD"
    assert emitted == []


def _event_envelope(producer: str = "producer") -> dict[str, Any]:
    return {
        "id": "schema-proof-event",
        "type": "domain.probe.changed",
        "source": {"layer": "module", "module_id": producer, "action_id": "run"},
        "tenant": {"app_id": "app-1", "tenant_id": "tenant-1"},
        "payload": {"value": 1},
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("schema", (*_BROKEN_SCHEMAS, [], "", 0))
async def test_router_rejects_bad_schema_before_any_reaction(schema: Any) -> None:
    called = []

    class Handler:
        async def on_event(self, _ctx, **payload):
            called.append(payload)

    producer = _with_event_schema(_loaded_module("producer"), event_type="domain.probe.changed", payload_schema=schema)
    consumer = _loaded_module(
        "consumer", reactions=[_handler_target_reaction("domain.probe.changed")], handler=Handler(),
    )
    router = _router([producer, consumer])
    with pytest.raises(ModuleEventPayloadValidationError) as failure:
        await router.handle_event("domain.probe.changed", _event_envelope())
    assert failure.value.diagnostic.category == "schema_invalid"
    assert called == []


@pytest.mark.asyncio
async def test_router_retains_explicit_universal_schema_for_the_exact_producer() -> None:
    called = []

    class Handler:
        async def on_event(self, _ctx, **payload):
            called.append(payload)

    universal = _with_event_schema(_loaded_module("producer"), event_type="domain.probe.changed", payload_schema={})
    restrictive = _with_event_schema(
        _loaded_module("other"), event_type="domain.probe.changed",
        payload_schema={"type": "object", "required": ["absent"]},
    )
    consumer = _loaded_module(
        "consumer", reactions=[_handler_target_reaction("domain.probe.changed")], handler=Handler(),
    )
    router = _router([universal, restrictive, consumer])
    await router.handle_event("domain.probe.changed", _event_envelope())
    assert called == [{"value": 1}]


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["input", "output", "emission", "router"])
async def test_explicit_empty_schema_never_bypasses_validation_in_any_caller(boundary: str, monkeypatch) -> None:
    def cannot_normalize(_schema):
        raise RuntimeError("injected schema normalization failure")

    monkeypatch.setattr(schema_validation, "normalize_nullable_schema", cannot_normalize)
    if boundary == "router":
        producer = _with_event_schema(
            _loaded_module("producer"), event_type="domain.probe.changed", payload_schema={},
        )
        with pytest.raises(ModuleEventPayloadValidationError) as failure:
            await _router([producer]).handle_event("domain.probe.changed", _event_envelope())
        assert failure.value.diagnostic.category == "schema_invalid"
        return
    if boundary == "emission":
        emitted = []

        async def emit(event_type, envelope):
            emitted.append((event_type, envelope))

        class Handler:
            async def run(self, ctx):
                await ctx.emit("domain.probe.changed", {})

        executor = ModuleExecutor(event_emitter=emit)
        executor.register("probe", Handler(), event_payload_schemas={"domain.probe.changed": {}})
        result = await executor.execute(_request(module="probe", action="run"))
        assert result.error_code == "INVALID_EVENT_PAYLOAD"
        assert emitted == []
    else:
        executor = ModuleExecutor()
        executor.register("probe", _Handler(), action_schemas={"run": {boundary: {}}})
        result = await executor.execute(_request(module="probe", action="run"))
        assert result.error_code == ("INVALID_PARAMS" if boundary == "input" else "INVALID_OUTPUT_SCHEMA")
    assert result.success is False
