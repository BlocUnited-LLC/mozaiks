from __future__ import annotations

# ruff: noqa: E402
import argparse
import asyncio
import json
import os
import re
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from factory_app.workflows.AppGenerator.tools.app_validation import run_app_bundle_acceptance_gate
from factory_app.workflows.AppGenerator.tools.export_app_code import resolve_export_gate
from mozaiksai.core.runtime.app.loader import AppLoader
from scripts.smoke_agentgenerator_live_pack import run_live_agentgenerator_pack_smoke

DEFAULT_APP_ID = "support-operations-live-acceptance"
DEFAULT_WORKFLOW_NAME = "TicketBatchTriageWorkflow"
DEFAULT_WORKFLOW_CAPABILITY_ID = "ticket-batch-triage-workflow"
DEFAULT_TRIGGER_EVENT_TYPE = "domain.support_ticket.batch_requested"


class SmokeContext:
    def __init__(self, initial: dict[str, Any]) -> None:
        self.data = dict(initial)

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self.data[key] = value

    def to_dict(self) -> dict[str, Any]:
        return dict(self.data)


def _configure_event_loop_policy() -> None:
    if os.name != "nt":
        return
    selector_policy = getattr(asyncio, "WindowsSelectorEventLoopPolicy", None)
    if selector_policy is not None:
        asyncio.set_event_loop_policy(selector_policy())


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    return str(value)


def _kebab_from_pascal(value: str) -> str:
    text = re.sub(r"(.)([A-Z][a-z]+)", r"\1-\2", str(value or "").strip())
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1-\2", text)
    text = re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-").lower()
    return text or DEFAULT_WORKFLOW_CAPABILITY_ID


def _reaction_id(capability_id: str, event_type: str) -> str:
    token = re.sub(r"[^A-Za-z0-9]+", "_", f"{capability_id}_{event_type}").strip("_").lower()
    return token[:96] or "workflow_reaction"


def default_workflow_integration() -> dict[str, Any]:
    return {
        "workflow_name": DEFAULT_WORKFLOW_NAME,
        "capability_id": DEFAULT_WORKFLOW_CAPABILITY_ID,
        "startup_mode": "BackendOnly",
        "trigger_events": [
            {
                "event_type": DEFAULT_TRIGGER_EVENT_TYPE,
                "capability_id": DEFAULT_WORKFLOW_CAPABILITY_ID,
                "description": "Requests parallel support-ticket triage.",
            }
        ],
        "source": "default_smoke_contract",
    }


def _trigger_event_type(trigger: dict[str, Any]) -> str | None:
    for key in ("event", "event_type", "type"):
        raw = trigger.get(key)
        value = str(raw or "").strip()
        if value.startswith(("domain.", "platform.", "hosted.")):
            return value
    return None


def _load_generated_orchestrator(live_result: dict[str, Any]) -> dict[str, Any]:
    bundle_root = Path(str(live_result.get("bundle_root") or ""))
    workflow_dir = bundle_root / DEFAULT_WORKFLOW_NAME
    orchestrator_path = workflow_dir / "orchestrator.yaml"
    if not orchestrator_path.is_file():
        raise RuntimeError(f"Generated workflow orchestrator is missing: {orchestrator_path}")
    loaded = yaml.safe_load(orchestrator_path.read_text(encoding="utf-8-sig")) or {}
    if not isinstance(loaded, dict):
        raise RuntimeError(f"Generated workflow orchestrator is not a mapping: {orchestrator_path}")
    return loaded


def workflow_integration_from_live_agentgenerator(live_result: dict[str, Any]) -> dict[str, Any]:
    orchestrator = _load_generated_orchestrator(live_result)
    workflow_name = str(orchestrator.get("workflow_name") or DEFAULT_WORKFLOW_NAME).strip()
    startup_mode = str(orchestrator.get("workflow_startup_mode") or "").strip()
    triggers = orchestrator.get("triggers") if isinstance(orchestrator.get("triggers"), list) else []

    trigger_events: list[dict[str, Any]] = []
    for trigger in triggers:
        if not isinstance(trigger, dict):
            continue
        event_type = _trigger_event_type(trigger)
        if not event_type:
            continue
        capability_id = str(trigger.get("capability_id") or "").strip()
        trigger_events.append(
            {
                "event_type": event_type,
                "capability_id": capability_id or None,
                "description": str(trigger.get("description") or "").strip(),
            }
        )

    capability_id = next(
        (
            str(item.get("capability_id")).strip()
            for item in trigger_events
            if str(item.get("capability_id") or "").strip()
        ),
        _kebab_from_pascal(workflow_name),
    )

    errors: list[str] = []
    event_types = {str(item.get("event_type") or "") for item in trigger_events}
    if DEFAULT_TRIGGER_EVENT_TYPE not in event_types:
        errors.append(
            f"Generated {workflow_name} did not declare expected trigger {DEFAULT_TRIGGER_EVENT_TYPE!r}."
        )
    if startup_mode != "BackendOnly":
        errors.append(
            f"Generated {workflow_name} startup mode was {startup_mode!r}; expected 'BackendOnly'."
        )

    return {
        "workflow_name": workflow_name or DEFAULT_WORKFLOW_NAME,
        "capability_id": capability_id,
        "startup_mode": startup_mode,
        "trigger_events": trigger_events,
        "source": "live_agentgenerator_bundle",
        "errors": errors,
    }


def build_appgenerator_acceptance_files(
    workflow_integration: dict[str, Any] | None = None,
) -> dict[str, str]:
    integration = workflow_integration or default_workflow_integration()
    workflow_name = str(integration.get("workflow_name") or DEFAULT_WORKFLOW_NAME)
    capability_id = str(integration.get("capability_id") or DEFAULT_WORKFLOW_CAPABILITY_ID)
    trigger_events = integration.get("trigger_events") if isinstance(integration.get("trigger_events"), list) else []
    trigger_event_type = DEFAULT_TRIGGER_EVENT_TYPE
    for item in trigger_events:
        if isinstance(item, dict) and str(item.get("event_type") or "").strip():
            trigger_event_type = str(item["event_type"]).strip()
            break
    reaction_id = _reaction_id(capability_id, trigger_event_type)

    data_contract = {
        "version": "1",
        "app_id": DEFAULT_APP_ID,
        "surfaces": [
            {
                "surface_id": "support_tickets",
                "surface_kind": "module",
                "collections": [
                    {
                        "name": "tickets",
                        "ownership": {
                            "surface_id": "support_tickets",
                            "surface_kind": "module",
                        },
                    }
                ],
            }
        ],
        "shared_collections": [],
    }

    return {
        "app.json": json.dumps(
            {
                "appId": DEFAULT_APP_ID,
                "appName": "Support Operations",
                "version": "1.0.0",
                "startup": {"landing_spot": "/support-tickets"},
            }
        ),
        "config/ai.json": json.dumps(
            {
                "chat": {"chat_startup_mode": "ask"},
                "workflows": {"entry_point": None},
            }
        ),
        "config/shell.json": json.dumps(
            {
                "navigation": {"autoFromPages": True},
                "header": {"show": True},
            }
        ),
        "ui/route_manifest.json": json.dumps(
            {
                "pages": [
                    {
                        "path": "/support-tickets",
                        "component": "SchemaPage",
                        "label": "Support Tickets",
                        "order": 10,
                        "schema": "support_tickets",
                        "navigation": {"group": "main"},
                        "meta": {"title": "Support Tickets"},
                    }
                ]
            }
        ),
        "ui/pages/support_tickets.yaml": """
name: SupportTickets
route: /support-tickets
title: Support Tickets
sections:
  - id: ticket-list
    type: record_list
    config:
      api_endpoint: /api/modules/support_tickets/list_tickets
  - id: ticket-create
    type: form
    config:
      submit_action:
        api_endpoint: /api/modules/support_tickets/create_ticket
  - id: batch-triage
    type: form
    config:
      submit_action:
        api_endpoint: /api/modules/support_tickets/request_batch_triage
""",
        "data/contract.json": json.dumps(data_contract),
        "security/secrets.yaml": """
version: 1
secrets:
  - name: SUPPORT_WEBHOOK_SECRET
    env: SUPPORT_WEBHOOK_SECRET
    required: false
""",
        "modules/support_tickets/module.yaml": f"""
schema_version: mozaiks.module.v1
module:
  id: support_tickets
  display_name: Support Tickets
  version: 1.0.0
  description: Support ticket intake and batch triage requests.
  handler: backend.handler:SupportTicketsModule
actions:
  - id: list_tickets
    description: List support tickets visible to the current workspace.
    handler_method: list_tickets
    input_schema:
      type: object
      properties: {{}}
    output_schema:
      type: object
  - id: create_ticket
    description: Create a support ticket.
    handler_method: create_ticket
    input_schema:
      type: object
      required:
        - customer_name
        - issue
      properties:
        customer_name:
          type: string
        issue:
          type: string
        priority:
          type: string
    output_schema:
      type: object
    emits:
      - domain.support_ticket.created
  - id: request_batch_triage
    description: Request agentic triage for the current support ticket queue.
    handler_method: request_batch_triage
    input_schema:
      type: object
      properties:
        priority:
          type: string
    output_schema:
      type: object
    emits:
      - {trigger_event_type}
capabilities:
  - capability_id: support_tickets.list
    kind: action
    target: list_tickets
    title: List support tickets
  - capability_id: support_tickets.create
    kind: action
    target: create_ticket
    title: Create support ticket
  - capability_id: support_tickets.request_batch_triage
    kind: action
    target: request_batch_triage
    title: Request batch triage
  - capability_id: {capability_id}
    kind: workflow
    target: {workflow_name}
    title: Run ticket batch triage workflow
""",
        "modules/support_tickets/contracts/events.yaml": f"""
schema_version: mozaiks.events.v1
events:
  - type: domain.support_ticket.created
    version: 1
    description: Emitted after a support ticket is created.
    producer: support_tickets
    payload_schema:
      type: object
      required:
        - ticket_id
  - type: {trigger_event_type}
    version: 1
    description: Emitted when a support ticket queue needs agentic triage.
    producer: support_tickets
    payload_schema:
      type: object
      required:
        - priority
""",
        "modules/support_tickets/contracts/reactions.yaml": f"""
schema_version: mozaiks.reactions.v1
reactions:
  - id: {reaction_id}
    event_type: {trigger_event_type}
    target:
      kind: capability
      capability_id: {capability_id}
    description: Route the support ticket batch request to the generated workflow capability.
""",
        "modules/support_tickets/backend/__init__.py": "",
        "modules/support_tickets/backend/handler.py": """
from .service import SupportTicketsService


class SupportTicketsModule:
    async def list_tickets(self, ctx, **params):
        return await SupportTicketsService(ctx).list_tickets(**params)

    async def create_ticket(self, ctx, **params):
        return await SupportTicketsService(ctx).create_ticket(**params)

    async def request_batch_triage(self, ctx, **params):
        return await SupportTicketsService(ctx).request_batch_triage(**params)
""",
        "modules/support_tickets/backend/service.py": f'''
from .repo import SupportTicketsRepo
from .schemas import batch_request_document, ticket_document


class SupportTicketsService:
    def __init__(self, ctx):
        self.ctx = ctx
        self.repo = SupportTicketsRepo(ctx)

    async def list_tickets(self, **params):
        priority = params.get("priority")
        tickets = await self.repo.list_tickets(priority=priority)
        return {{"tickets": tickets}}

    async def create_ticket(self, **params):
        record = ticket_document(
            customer_name=params.get("customer_name"),
            issue=params.get("issue"),
            priority=params.get("priority"),
        )
        created = await self.repo.create_ticket(record)
        await self._emit("domain.support_ticket.created", created)
        return {{"ticket": created}}

    async def request_batch_triage(self, **params):
        request = batch_request_document(priority=params.get("priority"))
        await self._emit("{trigger_event_type}", request)
        return {{"batch_request": request}}

    async def _emit(self, event_type, payload):
        emit = getattr(self.ctx, "emit", None)
        if callable(emit):
            await emit(event_type, payload)
''',
        "modules/support_tickets/backend/repo.py": """
class SupportTicketsRepo:
    def __init__(self, ctx):
        self.ctx = ctx

    def _collection(self):
        persistence = getattr(self.ctx, "persistence", None)
        if persistence is None:
            return None
        return persistence.collection("support_tickets", "tickets")

    async def list_tickets(self, *, priority=None):
        collection = self._collection()
        if collection is None:
            return []
        query = {"priority": priority} if priority else {}
        return await collection.find(query).to_list(length=100)

    async def create_ticket(self, record):
        collection = self._collection()
        if collection is None:
            return record
        result = await collection.insert_one(record)
        return {**record, "ticket_id": str(result.inserted_id)}
""",
        "modules/support_tickets/backend/schemas.py": """
from uuid import uuid4


def ticket_document(*, customer_name, issue, priority=None):
    return {
        "ticket_id": uuid4().hex,
        "customer_name": str(customer_name or "").strip(),
        "issue": str(issue or "").strip(),
        "priority": str(priority or "normal").strip(),
        "status": "open",
    }


def batch_request_document(*, priority=None):
    return {
        "priority": str(priority or "all").strip(),
        "status": "requested",
    }
""",
    }


def _write_files(root: Path, files: dict[str, str]) -> None:
    for rel_path, content in files.items():
        target = root / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")


async def validate_appgenerator_acceptance_handoff(
    *,
    workflow_integration: dict[str, Any] | None = None,
) -> dict[str, Any]:
    integration = workflow_integration or default_workflow_integration()
    files = build_appgenerator_acceptance_files(integration)
    context = SmokeContext(
        {
            "workflow_name": "AppGenerator",
            "app_id": DEFAULT_APP_ID,
            "chat_id": "live-agentgenerator-appgenerator-handoff",
            "generated_files": files,
            "app_validation_status": "skipped",
            "app_validation_strategy_used": "skip",
            "generated_workflow_name": integration.get("workflow_name"),
            "generated_workflow_capability_id": integration.get("capability_id"),
            "generated_workflow_startup_mode": integration.get("startup_mode"),
            "generated_workflow_trigger_events": integration.get("trigger_events") or [],
            "app_build_plan": {
                "capability_packs": [
                    {
                        "module_id": "support_tickets",
                        "actions": [
                            "list_tickets",
                            "create_ticket",
                            "request_batch_triage",
                        ],
                        "workflow_capability_ids": [integration.get("capability_id")],
                    }
                ]
            },
        }
    )

    acceptance = await run_app_bundle_acceptance_gate(files=files, context_variables=context)
    export_gate = resolve_export_gate(context)
    loader_result: dict[str, Any]
    try:
        with tempfile.TemporaryDirectory(prefix="mozaiks-appgenerator-acceptance-") as temp_dir:
            app_root = Path(temp_dir) / "app"
            _write_files(app_root, files)
            loaded = await AppLoader.load(str(app_root))
            module = loaded.modules[0]
            reactions = module.manifests.reactions.reactions if module.manifests.reactions else []
            loader_result = {
                "loaded": True,
                "app_name": loaded.definition.name,
                "module_ids": [item.name for item in loaded.modules],
                "page_names": [page.name for page in loaded.definition.pages],
                "reaction_capability_ids": [
                    reaction.target.capability_id
                    for reaction in reactions
                    if reaction.target.kind == "capability"
                ],
            }
    except Exception as exc:
        loader_result = {"loaded": False, "error": str(exc)}

    errors: list[str] = []
    if not acceptance.get("passed"):
        errors.append("App bundle acceptance did not pass.")
    if not export_gate.get("allow_export"):
        errors.extend(str(reason) for reason in export_gate.get("reasons") or [])
    if not loader_result.get("loaded"):
        errors.append(f"Runtime app loader failed: {loader_result.get('error')}")
    capability_id = str(integration.get("capability_id") or "")
    if capability_id and capability_id not in loader_result.get("reaction_capability_ids", []):
        errors.append(f"Runtime app loader did not find workflow reaction capability {capability_id!r}.")

    return _json_safe(
        {
            "success": not errors,
            "validation_errors": errors,
            "workflow_integration": integration,
            "app_bundle_acceptance_status": acceptance.get("status"),
            "app_bundle_validation_evidence": acceptance.get("validation_evidence"),
            "acceptance": acceptance,
            "export_gate": export_gate,
            "runtime_loader": loader_result,
            "context": context.to_dict(),
        }
    )


async def run_live_agentgenerator_to_appgenerator_acceptance_smoke(
    *,
    timeout_seconds: float = 600.0,
    enable_telemetry: bool = False,
) -> dict[str, Any]:
    load_dotenv(REPO_ROOT / ".env")
    live_result = await run_live_agentgenerator_pack_smoke(
        timeout_seconds=timeout_seconds,
        enable_telemetry=enable_telemetry,
    )
    if not live_result.get("success"):
        return _json_safe(
            {
                "success": False,
                "validation_errors": [
                    "Live AgentGenerator pack smoke failed before AppGenerator acceptance."
                ],
                "live_agentgenerator": live_result,
            }
        )

    integration = workflow_integration_from_live_agentgenerator(live_result)
    integration_errors = [str(item) for item in integration.get("errors") or []]
    if integration_errors:
        return _json_safe(
            {
                "success": False,
                "validation_errors": integration_errors,
                "live_agentgenerator": {
                    "success": live_result.get("success"),
                    "bundle_root": live_result.get("bundle_root"),
                    "task_batch_meta": live_result.get("task_batch_meta"),
                    "task_run_trace": live_result.get("task_run_trace"),
                    "validation": live_result.get("validation"),
                    "semantic_drift": live_result.get("semantic_drift"),
                    "promotion": live_result.get("promotion"),
                },
                "workflow_integration": integration,
            }
        )

    acceptance_result = await validate_appgenerator_acceptance_handoff(
        workflow_integration=integration,
    )
    errors = list(acceptance_result.get("validation_errors") or [])
    return _json_safe(
        {
            "success": not errors,
            "validation_errors": errors,
            "live_agentgenerator": {
                "success": live_result.get("success"),
                "app_id": live_result.get("app_id"),
                "bundle_root": live_result.get("bundle_root"),
                "active_workflows_root": live_result.get("active_workflows_root"),
                "task_batch_meta": live_result.get("task_batch_meta"),
                "task_run_trace": live_result.get("task_run_trace"),
                "validation": live_result.get("validation"),
                "semantic_drift": live_result.get("semantic_drift"),
                "promotion": live_result.get("promotion"),
            },
            "workflow_integration": integration,
            "appgenerator_acceptance": acceptance_result,
        }
    )


async def run_deterministic_appgenerator_acceptance_smoke() -> dict[str, Any]:
    return await validate_appgenerator_acceptance_handoff(
        workflow_integration=default_workflow_integration(),
    )


def main() -> int:
    _configure_event_loop_policy()
    parser = argparse.ArgumentParser(
        description=(
            "Run the live AgentGenerator to deterministic AppGenerator acceptance smoke. "
            "Use --skip-agentgenerator for the no-live AppGenerator fixture gate."
        )
    )
    parser.add_argument("--timeout-seconds", type=float, default=600.0)
    parser.add_argument(
        "--skip-agentgenerator",
        action="store_true",
        help="Run only the deterministic AppGenerator acceptance half.",
    )
    parser.add_argument(
        "--enable-telemetry",
        action="store_true",
        help="Opt into AG2 telemetry middleware during the live smoke. Disabled by default.",
    )
    args = parser.parse_args()

    if args.skip_agentgenerator:
        payload = asyncio.run(run_deterministic_appgenerator_acceptance_smoke())
    else:
        payload = asyncio.run(
            run_live_agentgenerator_to_appgenerator_acceptance_smoke(
                timeout_seconds=float(args.timeout_seconds),
                enable_telemetry=bool(args.enable_telemetry),
            )
        )
    print(json.dumps(payload, indent=2), flush=True)
    return 0 if payload.get("success") else 1


if __name__ == "__main__":
    raise SystemExit(main())
