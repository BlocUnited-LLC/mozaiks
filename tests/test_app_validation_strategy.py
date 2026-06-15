from __future__ import annotations

import asyncio
import importlib
import importlib.util
import sys
from pathlib import Path

import pytest


def _workspace() -> Path:
    return Path(__file__).resolve().parents[1]


_FACTORY_APP_PATH = str(_workspace() / "factory_app")


@pytest.fixture(autouse=True)
def _clean_factory_app_syspath():
    """Ensure factory_app/ is on sys.path during the test and clean up imported workflow modules after."""
    added = _FACTORY_APP_PATH not in sys.path
    if added:
        sys.path.insert(0, _FACTORY_APP_PATH)

    # Track modules added during the test
    before = set(sys.modules.keys())

    yield

    # Remove only workflow-namespace modules added during the test to avoid cross-test pollution.
    # Do NOT remove mozaiksai.* modules — other tests in the suite depend on them staying cached.
    added_keys = [
        k for k in sys.modules
        if k not in before and (k.startswith("workflows.") or k.startswith("factory_app."))
    ]
    for k in added_keys:
        del sys.modules[k]

    if added:
        try:
            sys.path.remove(_FACTORY_APP_PATH)
        except ValueError:
            pass


def _import_workflow_module(module_name: str):
    """Import a workflow tool file directly from this repo's workflow pack."""
    parts = module_name.split(".")
    if len(parts) < 4 or parts[0] != "workflows":
        return importlib.import_module(module_name)

    workflow_name = parts[1]
    relative_parts = parts[3:]
    file_path = _workspace() / "factory_app" / "workflows" / workflow_name / "tools" / f"{relative_parts[-1]}.py"
    module_name_direct = f"tests.{workflow_name.lower()}_{relative_parts[-1]}_direct"
    spec = importlib.util.spec_from_file_location(module_name_direct, file_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Unable to load module spec for {file_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Context:
    def __init__(self, initial: dict | None = None) -> None:
        self._data = dict(initial or {})

    def get(self, key: str, default=None):
        return self._data.get(key, default)

    def set(self, key: str, value) -> None:
        self._data[key] = value


@pytest.mark.parametrize(
    ("status", "acceptance_status", "integration_passed", "allow_export"),
    [
        ("passed", "passed", True, True),
        ("skipped", "passed", True, True),
        ("passed", "failed", True, False),
        ("passed", None, True, False),
        ("failed", "passed", True, False),
        ("pending", "passed", True, False),
        (None, "passed", True, False),
        ("passed", "passed", False, False),
    ],
)
def test_resolve_export_gate_uses_acceptance_validation_and_integration(
    status,
    acceptance_status,
    integration_passed,
    allow_export,
) -> None:
    module = _import_workflow_module("workflows.AppGenerator.tools.export_app_code")
    context = _Context(
        {
            "app_validation_status": status,
            "app_bundle_acceptance_status": acceptance_status,
            "app_validation_strategy_used": "local",
            "integration_tests_passed": integration_passed,
        }
    )

    gate = module.resolve_export_gate(context)

    assert gate["allow_export"] is allow_export
    assert gate["app_validation_status"] == (status.lower() if isinstance(status, str) else status)
    assert gate["app_bundle_acceptance_status"] == (
        acceptance_status.lower() if isinstance(acceptance_status, str) else acceptance_status
    )
    assert gate["app_validation_strategy_used"] == "local"
    assert gate["integration_tests_passed"] is integration_passed


def test_validation_strategy_defaults_to_skip_when_e2b_local_and_docker_are_unavailable(monkeypatch) -> None:
    from mozaiksai.core.workflow.generator_support import (
        app_validation_strategy as app_validation_strategy_module,
    )

    monkeypatch.delenv("E2B_API_KEY", raising=False)
    monkeypatch.delenv("MOZAIKS_APP_VALIDATION_STRATEGY", raising=False)
    monkeypatch.setattr(app_validation_strategy_module, "local_app_validation_available", lambda: False)
    monkeypatch.setattr(app_validation_strategy_module, "docker_app_validation_available", lambda: False)

    strategy, reason = app_validation_strategy_module.resolve_app_validation_strategy(
        requested=None, context_value=None, local_available=False, docker_available=False
    )

    assert strategy == "skip"
    assert "resolved" in reason


def test_validation_strategy_summary_exposes_allowed_values() -> None:
    from mozaiksai.core.workflow.generator_support.app_validation_strategy import (
        build_app_validation_strategy_summary,
    )

    summary = build_app_validation_strategy_summary(env={}, local_available=False, docker_available=False)

    assert summary["allowed_values"] == ["e2b", "docker", "local", "skip"]
    assert summary["default_value"] == "skip"
    assert summary["options"][0]["value"] == "e2b"
    assert summary["options"][1]["value"] == "docker"
    assert summary["options"][2]["value"] == "local"
    assert summary["options"][3]["value"] == "skip"


def test_validation_strategy_rejects_invalid_explicit_values() -> None:
    from mozaiksai.core.workflow.generator_support.app_validation_strategy import (
        resolve_app_validation_strategy,
    )

    with pytest.raises(ValueError):
        resolve_app_validation_strategy(requested="invalid", context_value=None)


def test_validate_app_build_skip_strategy_persists_context() -> None:
    module = _import_workflow_module("workflows.AppGenerator.tools.app_validation")
    context = _Context({"workflow_name": "AppGenerator", "chat_id": "chat-1", "app_id": "app-1"})

    result = asyncio.run(
        module.validate_app_build(
            files={"package.json": '{"name":"demo","scripts":{"build":"echo build"}}'},
            validation_strategy="skip",
            context_variables=context,
        )
    )

    assert result["success"] is True
    assert result["validation_strategy"] == "skip"
    assert result["validation_status"] == "skipped"
    assert context.get("app_validation_status") == "skipped"
    assert context.get("app_validation_strategy_used") == "skip"
    assert context.get("app_validation_preview_url") is None


def test_module_implementation_contract_rejects_missing_handler_methods() -> None:
    module = _import_workflow_module("workflows.AppGenerator.tools.app_validation")

    result = module.validate_module_implementation_contract(
        {
            "modules/tickets/module.yaml": """
schema_version: mozaiks.module.v1
module:
  id: tickets
  handler: backend.handler:TicketsModule
actions:
- id: list_tickets
  handler_method: list_tickets
- id: update_status
  handler_method: update_status
""",
            "modules/tickets/backend/handler.py": """
class TicketsModule:
    async def update_status(self, ctx, **params):
        return {"ok": True}
""",
        }
    )

    assert result["passed"] is False
    assert any(
        item["test"] == "module_action_handler_method_missing"
        and "list_tickets" in item["error"]
        for item in result["failed_tests"]
    )


def test_module_implementation_contract_rejects_unresolved_class_base() -> None:
    module = _import_workflow_module("workflows.AppGenerator.tools.app_validation")

    result = module.validate_module_implementation_contract(
        {
            "modules/tickets/module.yaml": """
schema_version: mozaiks.module.v1
module:
  id: tickets
  handler: backend.handler:TicketsModule
actions:
- id: list_tickets
  handler_method: list_tickets
""",
            "modules/tickets/backend/handler.py": """
class TicketsModule:
    async def list_tickets(self, ctx, **params):
        return {"tickets": []}
""",
            "modules/tickets/backend/schemas.py": """
from typing import TypedDict

class TicketStatus(str, Enum):
    OPEN = "open"
""",
        }
    )

    assert result["passed"] is False
    assert any(
        item["test"] == "backend_python_unresolved_class_base"
        and "Enum" in item["error"]
        for item in result["failed_tests"]
    )


def test_module_implementation_contract_rejects_handler_context_last_signature() -> None:
    module = _import_workflow_module("workflows.AppGenerator.tools.app_validation")

    result = module.validate_module_implementation_contract(
        {
            "modules/tickets/module.yaml": """
schema_version: mozaiks.module.v1
module:
  id: tickets
  handler: backend.handler:TicketsModule
actions:
- id: update_status
  handler_method: update_status
  input_schema:
    required: [ticket_id, status]
""",
            "modules/tickets/backend/handler.py": """
class TicketsModule:
    async def update_status(self, ticket_id: str, status: str, context):
        return {"ok": True}
""",
        }
    )

    assert result["passed"] is False
    assert any(
        item["test"] == "module_handler_context_parameter"
        and "ticket_id" in item["error"]
        for item in result["failed_tests"]
    )


def test_module_implementation_contract_accepts_ctx_kwargs_signature() -> None:
    module = _import_workflow_module("workflows.AppGenerator.tools.app_validation")

    result = module.validate_module_implementation_contract(
        {
            "modules/tickets/module.yaml": """
schema_version: mozaiks.module.v1
module:
  id: tickets
  handler: backend.handler:TicketsModule
actions:
- id: update_status
  handler_method: update_status
  input_schema:
    required: [ticket_id, status]
""",
            "modules/tickets/backend/handler.py": """
class TicketsModule:
    async def update_status(self, ctx, **params):
        return {"ok": True}
""",
        }
    )

    assert result["passed"] is True


def test_module_implementation_contract_rejects_synthetic_payload_for_field_inputs() -> None:
    module = _import_workflow_module("workflows.AppGenerator.tools.app_validation")

    result = module.validate_module_implementation_contract(
        {
            "modules/tickets/module.yaml": """
schema_version: mozaiks.module.v1
module:
  id: tickets
  handler: backend.handler:TicketsModule
actions:
- id: update_status
  handler_method: update_status
  input_schema:
    required: [ticket_id, new_status]
""",
            "modules/tickets/backend/handler.py": """
class TicketsModule:
    async def update_status(self, ctx, **params):
        return {"ok": True}
""",
            "modules/tickets/backend/service.py": """
class TicketsService:
    async def update_status(self, ctx, **params):
        payload = params.get("payload")
        return payload
""",
        }
    )

    assert result["passed"] is False
    assert any(
        item["test"] == "module_service_synthetic_payload_wrapper"
        for item in result["failed_tests"]
    )


def test_module_implementation_contract_rejects_undeclared_service_params_key() -> None:
    module = _import_workflow_module("workflows.AppGenerator.tools.app_validation")

    result = module.validate_module_implementation_contract(
        {
            "modules/tickets/module.yaml": """
schema_version: mozaiks.module.v1
module:
  id: tickets
  handler: backend.handler:TicketsModule
actions:
- id: save_settings
  handler_method: save_settings
  input_schema:
    required: [default_queue, sla_hours]
    properties:
    - name: default_queue
    - name: sla_hours
""",
            "modules/tickets/backend/handler.py": """
class TicketsModule:
    async def save_settings(self, ctx, **params):
        return {"ok": True}
""",
            "modules/tickets/backend/service.py": """
class TicketsService:
    async def save_settings(self, ctx, **params):
        settings = params["settings"]
        return settings
""",
        }
    )

    assert result["passed"] is False
    assert any(
        item["test"] == "module_service_undeclared_params_key"
        and "settings" in item["error"]
        for item in result["failed_tests"]
    )


def test_module_implementation_contract_rejects_pass_backed_runtime_logic() -> None:
    module = _import_workflow_module("workflows.AppGenerator.tools.app_validation")

    result = module.validate_module_implementation_contract(
        {
            "modules/tickets/module.yaml": """
schema_version: mozaiks.module.v1
module:
  id: tickets
  handler: backend.handler:TicketsModule
actions:
- id: save_settings
  handler_method: save_settings
""",
            "modules/tickets/backend/handler.py": """
class TicketsModule:
    async def save_settings(self, ctx, **params):
        return {"ok": True}
""",
            "modules/tickets/backend/repo.py": """
class TicketsRepo:
    async def save_settings(self, ctx, settings):
        pass
""",
        }
    )

    assert result["passed"] is False
    assert any(
        item["test"] == "backend_python_pass_statement"
        for item in result["failed_tests"]
    )


@pytest.mark.asyncio
async def test_validate_app_bundle_from_request_blocks_module_implementation_failure(monkeypatch) -> None:
    module = importlib.import_module("factory_app.workflows.AppGenerator.tools.app_validation")

    async def fake_validate_app_build(**kwargs):
        return {"validation_status": "skipped", "validation_strategy": "skip"}

    monkeypatch.setattr(module, "validate_app_build", fake_validate_app_build)
    context = _Context(
        {
            "generated_files": {
                "modules/tickets/module.yaml": """
schema_version: mozaiks.module.v1
module:
  id: tickets
  handler: backend.handler:TicketsModule
actions:
- id: list_tickets
  handler_method: list_tickets
""",
                "modules/tickets/backend/handler.py": """
class TicketsModule:
    pass
""",
            }
        }
    )

    result = await module.validate_app_bundle_from_request(
        {"validation_strategy": "skip", "start_dev_server": False},
        context_variables=context,
    )

    assert result["status"] == "failed"
    assert result["integration_tests_passed"] is False
    assert context.get("integration_tests_passed") is False
    assert context.get("module_implementation_validation_passed") is False
    assert context.get("integration_test_result")["module_implementation"]["failed_tests"]


@pytest.mark.asyncio
async def test_validate_app_bundle_from_request_blocks_runtime_quality_warnings(monkeypatch) -> None:
    module = importlib.import_module("factory_app.workflows.AppGenerator.tools.app_validation")

    async def fake_validate_app_build(**kwargs):
        return {"validation_status": "skipped", "validation_strategy": "skip"}

    monkeypatch.setattr(module, "validate_app_build", fake_validate_app_build)
    context = _Context(
        {
            "generated_files": {
                "modules/tickets/module.yaml": """
schema_version: mozaiks.module.v1
module:
  id: tickets
  handler: backend.handler:TicketsModule
actions:
- id: save_settings
  handler_method: save_settings
""",
                "modules/tickets/backend/handler.py": """
class TicketsModule:
    async def save_settings(self, ctx, **params):
        return {"ok": True}
""",
                "modules/tickets/backend/policy.py": """
class TicketsPolicy:
    async def require_save_settings(self, ctx):
        # Placeholder for auth logic
        return True
""",
            }
        }
    )

    result = await module.validate_app_bundle_from_request(
        {"validation_strategy": "skip", "start_dev_server": False},
        context_variables=context,
    )

    assert result["status"] == "failed"
    assert context.get("integration_tests_passed") is False
    assert context.get("module_runtime_quality_status") == "blocked"
    assert context.get("integration_test_result")["module_runtime_quality"]["warnings"]


@pytest.mark.asyncio
async def test_validate_app_bundle_from_request_blocks_workflow_integration_failure(monkeypatch) -> None:
    module = importlib.import_module("factory_app.workflows.AppGenerator.tools.app_validation")
    from scripts.smoke_appgenerator_live_acceptance import (
        build_appgenerator_acceptance_files,
        default_workflow_integration,
    )

    async def fake_validate_app_build(**kwargs):
        return {"validation_status": "skipped", "validation_strategy": "skip"}

    monkeypatch.setattr(module, "validate_app_build", fake_validate_app_build)
    integration = default_workflow_integration()
    files = build_appgenerator_acceptance_files(integration)
    del files["modules/support_tickets/contracts/reactions.yaml"]
    context = _Context(
        {
            "generated_files": files,
            "generated_workflow_name": integration["workflow_name"],
            "generated_workflow_capability_id": integration["capability_id"],
            "generated_workflow_startup_mode": integration["startup_mode"],
            "generated_workflow_trigger_events": integration["trigger_events"],
        }
    )

    result = await module.validate_app_bundle_from_request(
        {"validation_strategy": "skip", "start_dev_server": False},
        context_variables=context,
    )

    assert result["status"] == "failed"
    assert result["integration_tests_passed"] is False
    assert result["workflow_integration_validation_result"]["passed"] is False
    assert result["workflow_integration_repair"]["status"] == "needs_revision"
    assert context.get("workflow_integration_validation_passed") is False
    assert context.get("workflow_integration_repair_status") == "needs_revision"
    assert context.get("integration_test_result")["workflow_integration"]["failed_tests"]
    assert context.get("integration_test_result")["workflow_integration_repair"]["status"] == "needs_revision"


def test_validate_wiring_tool_annotations_are_runtime_resolved() -> None:
    from factory_app.workflows.AppGenerator.tools.validate_wiring import validate_wiring

    assert validate_wiring.__annotations__["context_variables"] != "Optional[Dict[str, Any]]"

