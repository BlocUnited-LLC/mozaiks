# ==============================================================================
# FILE: tests/test_so_schema_enforcement.py
# DESCRIPTION: Structured-output schema enforcement tests.
#   1. AgentGenerator payload_schema fields are typed (no bare `dict` allowed).
#   2. PayloadSchemaSpec model exists in AgentGenerator structured_outputs.yaml.
#   3. factory.py adds RetryMiddleware for SO agents.
# ==============================================================================
from __future__ import annotations

import ast
from pathlib import Path

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[1]
_AG_SO_YAML = (
    _REPO_ROOT
    / "factory_app"
    / "workflows"
    / "AgentGenerator"
    / "structured_outputs.yaml"
)


# ---------------------------------------------------------------------------
# Change 1 tests — payload_schema fields must not be bare `dict`
# ---------------------------------------------------------------------------


def _load_ag_so() -> dict:
    with open(_AG_SO_YAML, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def test_agentgenerator_payload_schema_spec_is_strict_compatible() -> None:
    """UIToolAction and UIToolContract must not declare payload_schema: type: dict.

    A bare `dict` field causes supports_provider_response_format() to return
    False, silently disabling strict structured outputs for PackBuildCoordinator
    and WorkflowBundleBuilderAgent.
    """
    data = _load_ag_so()
    models = data.get("models", {})

    for model_name in ("UIToolAction", "UIToolContract"):
        assert model_name in models, f"Model {model_name!r} missing from AgentGenerator structured_outputs.yaml"
        fields = models[model_name].get("fields", {})
        ps_field = fields.get("payload_schema", {})
        field_type = ps_field.get("type")
        assert field_type != "dict", (
            f"{model_name}.payload_schema must not use type: dict — "
            "bare dict blocks OpenAI strict mode. Use a typed model instead."
        )


def test_agentgenerator_payload_schema_spec_model_exists() -> None:
    """PayloadSchemaSpec model must be declared in AgentGenerator structured_outputs.yaml."""
    data = _load_ag_so()
    models = data.get("models", {})
    assert "PayloadSchemaSpec" in models, (
        "PayloadSchemaSpec model is missing from AgentGenerator structured_outputs.yaml. "
        "It is required to replace the bare dict fields on UIToolAction and UIToolContract."
    )
    spec = models["PayloadSchemaSpec"]
    fields = spec.get("fields", {})
    assert "type" in fields, "PayloadSchemaSpec must have a 'type' field (JSON schema type string)"


def test_agentgenerator_uitoolaction_payload_schema_references_spec() -> None:
    """UIToolAction.payload_schema must reference PayloadSchemaSpec (not dict)."""
    data = _load_ag_so()
    models = data.get("models", {})
    ps_field = models["UIToolAction"]["fields"]["payload_schema"]
    assert ps_field.get("type") == "PayloadSchemaSpec", (
        "UIToolAction.payload_schema must be type: PayloadSchemaSpec"
    )


def test_agentgenerator_uitoolcontract_payload_schema_references_spec() -> None:
    """UIToolContract.payload_schema must reference PayloadSchemaSpec (not dict)."""
    data = _load_ag_so()
    models = data.get("models", {})
    ps_field = models["UIToolContract"]["fields"]["payload_schema"]
    assert ps_field.get("type") == "PayloadSchemaSpec", (
        "UIToolContract.payload_schema must be type: PayloadSchemaSpec"
    )


# ---------------------------------------------------------------------------
# Change 2 tests — RetryMiddleware in factory.py for SO agents
# ---------------------------------------------------------------------------


def _read_factory_source() -> str:
    factory_path = _REPO_ROOT / "mozaiksai" / "core" / "workflow" / "agents" / "factory.py"
    return factory_path.read_text(encoding="utf-8")


def test_factory_retry_middleware_imported() -> None:
    """factory.py must import RetryMiddleware from ag2.middleware.builtin."""
    source = _read_factory_source()
    assert "from ag2.middleware.builtin import RetryMiddleware" in source, (
        "factory.py must import RetryMiddleware from ag2.middleware.builtin"
    )


def test_factory_structured_output_agent_gets_retry_middleware() -> None:
    """factory.py must append RetryMiddleware when beta_response_schema is not None.

    We verify this by inspecting the AST for the conditional append that mirrors
    the AG2StructuredAgentRunner pattern.
    """
    source = _read_factory_source()
    tree = ast.parse(source)

    retry_conditional_found = False
    for node in ast.walk(tree):
        # Look for: if beta_response_schema is not None: middleware.append(RetryMiddleware(...))
        if not isinstance(node, ast.If):
            continue
        # Check condition: beta_response_schema is not None
        test = node.test
        if not (
            isinstance(test, ast.Compare)
            and isinstance(test.left, ast.Name)
            and test.left.id == "beta_response_schema"
            and len(test.ops) == 1
            and isinstance(test.ops[0], ast.IsNot)
            and len(test.comparators) == 1
            and isinstance(test.comparators[0], ast.Constant)
            and test.comparators[0].value is None
        ):
            continue
        # Check body contains middleware.append(RetryMiddleware(...))
        for stmt in ast.walk(node):
            if not isinstance(stmt, ast.Expr):
                continue
            call = stmt.value
            if not isinstance(call, ast.Call):
                continue
            func = call.func
            if (
                isinstance(func, ast.Attribute)
                and func.attr == "append"
                and isinstance(func.value, ast.Name)
                and func.value.id == "middleware"
            ):
                # Check arg is RetryMiddleware(...)
                if call.args and isinstance(call.args[0], ast.Call):
                    inner = call.args[0].func
                    if isinstance(inner, ast.Name) and inner.id == "RetryMiddleware":
                        retry_conditional_found = True
                        break

    assert retry_conditional_found, (
        "factory.py must add RetryMiddleware to middleware when beta_response_schema is not None. "
        "Pattern: `if beta_response_schema is not None: middleware.append(RetryMiddleware(...))`"
    )


def test_factory_so_schema_error_is_logged_not_silently_swallowed() -> None:
    """factory.py must log a warning when structured-output resolution fails."""
    source = _read_factory_source()
    # Verify the warning log call is present in the exception handler
    assert "logger.warning" in source, "factory.py must call logger.warning somewhere"
    assert "response_schema disabled" in source or "Structured output schema" in source, (
        "factory.py warning message should mention structured output schema resolution failure"
    )


# ---------------------------------------------------------------------------
# Change 3 tests — response_format is NOT stored in the llm_config dict
# ---------------------------------------------------------------------------


def test_llm_config_response_format_not_stored_in_dict() -> None:
    """get_llm_config() must not store response_format in the returned dict.

    llm_config_to_ag2_config() never reads response_format; it's dead weight.
    Enforcement is via Agent(response_schema=...) in factory.py.
    """
    llm_config_path = _REPO_ROOT / "mozaiksai" / "core" / "workflow" / "llm_config.py"
    source = llm_config_path.read_text(encoding="utf-8")
    # The removed line was: llm_config["response_format"] = response_format
    assert 'llm_config["response_format"] = response_format' not in source, (
        "get_llm_config() must not store response_format in the returned dict — "
        "llm_config_to_ag2_config() never reads it. Use Agent(response_schema=...) instead."
    )
