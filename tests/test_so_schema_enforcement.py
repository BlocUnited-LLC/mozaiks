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


def test_factory_retry_middleware_is_not_gated_on_a_response_schema() -> None:
    """Every agent gets provider retry, with or without a response schema.

    Behaviour is proven in tests/test_workflow_provider_retry.py; this guards
    against the append moving back under a schema condition.
    """
    source = _read_factory_source()
    tree = ast.parse(source)

    def _appends_retry(node: ast.AST) -> bool:
        return (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "append"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "middleware"
            and bool(node.args)
            and isinstance(node.args[0], ast.Call)
            and isinstance(node.args[0].func, ast.Name)
            and node.args[0].func.id == "RetryMiddleware"
        )

    retry_appends = [node for node in ast.walk(tree) if _appends_retry(node)]
    assert len(retry_appends) == 1
    schema_gates = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.If) and "beta_response_schema" in ast.unparse(node.test)
    ]
    for gate in schema_gates:
        assert not any(_appends_retry(node) for node in ast.walk(gate)), (
            "RetryMiddleware must not depend on beta_response_schema"
        )


def test_factory_so_schema_error_is_logged_not_silently_swallowed() -> None:
    """Required structured-output agents must fail instead of disabling schemas."""
    source = _read_factory_source()
    assert "response_schema disabled" not in source
    assert "structured output registry " in source
    assert "could not load" in source
    assert "has no structured output registry entry" in source


def test_factory_retry_middleware_comment_does_not_claim_schema_correction() -> None:
    """RetryMiddleware is provider retry only; schema correction is AgentReply.content."""
    source = _read_factory_source()
    assert "RetryMiddleware covers provider/network exceptions" in source
    assert "Schema correction is owned by AG2 AgentReply.content()" in source
    assert "mirrors AG2StructuredAgentRunner" not in source


def test_network_runner_documents_post_hoc_validation_limitation() -> None:
    """Network runner should not claim malformed-output correction without AgentReply."""
    network_runner = (
        _REPO_ROOT / "mozaiksai" / "core" / "adapters" / "ag2_network_runner.py"
    ).read_text(encoding="utf-8")
    assert "post-hoc structured-output validation" in network_runner
    assert "AgentReply.content(retries=...)" in network_runner


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
