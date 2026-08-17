"""Bounded WorkAssignment executor for AppGenerator module contracts.

The executor is intentionally a thin bridge:

* WorkflowQueue owns durable delivery and fencing.
* WorkAssignment owns path authority and retry budgets.
* AG2TaskBatchRunner owns the bounded agent turn and structured-output
  validation.
* AppGenerator typed module contracts are materialized through the existing
  code-file materializer.
* Runtime module-loader models and the AppGenerator contract auditor validate
  the generated YAML before a WorkResult is returned.

No repository files are written here; integration remains a separate phase.

Lease renewal fires at three fixed checkpoints: before the runner call, after
the runner returns, and after validation completes.  It does NOT fire during
the blocking runner.run() call (which may run up to timeout_seconds).  This is
intentional: the runner owns its own timeout via asyncio.wait_for, and injecting
concurrent renewal would require unmanaged threads against the AG2 event loop.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Protocol, cast

import yaml
from pydantic import BaseModel

from factory_app.workflows.AppGenerator.tools.audit_module_contracts import (
    audit_module_contracts,
)
from mozaiksai.core.adapters.ag2_task_batch_runner import (
    AG2TaskBatchRunner,
    AG2TaskBatchRunnerRequest,
    AG2TaskBatchRunnerResult,
)
from mozaiksai.core.ports.orchestration import RunStatus
from mozaiksai.core.runtime.app.module_loader import (
    ModuleAdminManifest,
    ModuleDefinition,
    ModuleEventsManifest,
    ModuleNotificationsManifest,
    ModulePolicyHooksManifest,
    ModuleProfileManifest,
    ModuleReactionsManifest,
    ModuleRelationshipsManifest,
    ModuleSettingsManifest,
)
from mozaiksai.core.workflow.assignment_kinds import AssignmentKind
from mozaiksai.core.workflow.generator_support.code_files import (
    extract_code_file_map_from_payload,
)
from mozaiksai.core.workflow.outputs.structured import (
    get_structured_outputs_for_workflow,
)
from mozaiksai.core.workflow.path_ownership import normalize_owned_path
from mozaiksai.core.workflow.work_assignment_worker import (
    WORK_ASSIGNMENT_PAYLOAD_KEY,
    WorkAssignmentExecutionContext,
    WorkAssignmentExecutorRegistry,
    WorkAssignmentLeaseLostError,
    WorkAssignmentPermanentError,
    WorkAssignmentTransientError,
)
from mozaiksai.core.workflow.work_contracts import (
    WorkResult,
    make_work_result,
    stable_digest,
)

MODULE_CONTRACT_WORKFLOW_NAME = "AppGenerator"
MODULE_CONTRACT_AGENT_NAME = "ConfigMiddlewareAgent"
MODULE_CONTRACT_STRUCTURED_OUTPUT_ID = "ConfigMiddlewareOutput"
MODULE_CONTRACT_VALIDATOR_ID = "appgenerator.module_contract"

_CONTRACT_MODE = "module_contract_bundle"
_SUPPORTED_CONTRACT_FILES = frozenset(
    {
        "admin.yaml",
        "events.yaml",
        "notifications.yaml",
        "policy_hooks.yaml",
        "profile.yaml",
        "relationships.yaml",
        "reactions.yaml",
        "settings.yaml",
    }
)
_COMPANION_MODELS: dict[str, type[BaseModel]] = {
    "admin.yaml": ModuleAdminManifest,
    "events.yaml": ModuleEventsManifest,
    "notifications.yaml": ModuleNotificationsManifest,
    "policy_hooks.yaml": ModulePolicyHooksManifest,
    "profile.yaml": ModuleProfileManifest,
    "relationships.yaml": ModuleRelationshipsManifest,
    "reactions.yaml": ModuleReactionsManifest,
    "settings.yaml": ModuleSettingsManifest,
}


class ModuleContractRunner(Protocol):
    async def run(self, request: AG2TaskBatchRunnerRequest) -> AG2TaskBatchRunnerResult:
        """Run one bounded structured-output task."""
        ...


class ModuleContractExecutor:
    """Generate only assignment-owned module.yaml and contracts/*.yaml artifacts."""

    def __init__(
        self,
        *,
        agent: Any,
        runner: ModuleContractRunner | None = None,
        agent_name: str = MODULE_CONTRACT_AGENT_NAME,
        workflow_name: str = MODULE_CONTRACT_WORKFLOW_NAME,
        timeout_seconds: int = 120,
    ) -> None:
        if agent is None:
            raise ValueError("ModuleContractExecutor requires a pre-registered AG2 agent")
        self.agent = agent
        self.runner = runner or AG2TaskBatchRunner()
        self.agent_name = _required_text(agent_name, "agent_name")
        self.workflow_name = _required_text(workflow_name, "workflow_name")
        self.timeout_seconds = int(timeout_seconds)

    async def execute(self, context: WorkAssignmentExecutionContext) -> WorkResult:
        if context.assignment.assignment_kind is not AssignmentKind.MODULE_CONTRACT:
            raise WorkAssignmentPermanentError("ModuleContractExecutor accepts only module_contract assignments")
        module_scope = _module_scope_from_owned_paths(context.owned_paths)
        self._validate_assignment_authority(context, module_scope)

        await _renew_or_stale(context)
        request = AG2TaskBatchRunnerRequest(
            workflow_name=self.workflow_name,
            batch_id="work_assignment",
            task_id=context.assignment.assignment_id,
            chat_id=context.item.chat_id,
            app_id=context.item.app_id,
            agent_name=self.agent_name,
            agent=self.agent,
            prompt=_build_prompt(context=context, module_scope=module_scope),
            context_variables=_context_variables(context=context, module_scope=module_scope),
            structured_registry=self._structured_registry(),
            timeout_seconds=self.timeout_seconds,
        )

        try:
            runner_result = await self.runner.run(request)
        except (ConnectionError, TimeoutError) as exc:
            raise WorkAssignmentTransientError("module contract AG2 execution transient failure") from exc
        except OSError as exc:
            raise WorkAssignmentTransientError("module contract AG2 provider I/O failure") from exc
        except Exception as exc:
            raise WorkAssignmentPermanentError("module contract AG2 execution failed before typed output") from exc

        await _renew_or_stale(context)
        if runner_result.status is RunStatus.PAUSED:
            raise WorkAssignmentTransientError("module contract AG2 execution did not complete before timeout")
        if runner_result.status is not RunStatus.COMPLETED:
            if runner_result.close_reason == "structured_output_validation_failed":
                raise WorkAssignmentPermanentError("module contract structured output failed validation")
            raise WorkAssignmentPermanentError("module contract AG2 execution failed")

        file_map = _materialize_owned_module_contract(
            runner_result.output,
            module_scope=module_scope,
            owned_paths=context.owned_paths,
        )
        validation_evidence = _validate_materialized_files(
            file_map=file_map,
            module_scope=module_scope,
        )
        await _renew_or_stale(context)

        changed_artifacts = [
            {
                "path": path,
                "operation": "create",
                "content_digest": stable_digest(content),
            }
            for path, content in sorted(file_map.items())
        ]
        return make_work_result(
            assignment=context.assignment,
            status="completed",
            attempt_id=_attempt_id(context.assignment.assignment_id, context.assignment.assignment_digest),
            changed_artifacts=changed_artifacts,
            validation_evidence=validation_evidence,
            file_map=file_map,
        )

    def _validate_assignment_authority(
        self,
        context: WorkAssignmentExecutionContext,
        module_scope: _ModuleScope,
    ) -> None:
        if self.agent_name not in context.allowed_agent_ids:
            raise WorkAssignmentPermanentError(
                f"module_contract assignment does not authorize agent {self.agent_name!r}"
            )
        if context.required_structured_output_id != MODULE_CONTRACT_STRUCTURED_OUTPUT_ID:
            raise WorkAssignmentPermanentError(
                "module_contract assignment must require ConfigMiddlewareOutput"
            )
        if not module_scope.owned_file_paths:
            raise WorkAssignmentPermanentError("module_contract assignment owns no concrete YAML artifacts")

    def _structured_registry(self) -> dict[str, type]:
        registry = get_structured_outputs_for_workflow(self.workflow_name)
        model = registry.get(self.agent_name)
        if model is None:
            raise WorkAssignmentPermanentError(
                f"{self.workflow_name}.{self.agent_name} has no structured-output model"
            )
        return {self.agent_name: model}


def register_module_contract_executor(
    registry: WorkAssignmentExecutorRegistry,
    *,
    agent: Any,
    runner: ModuleContractRunner | None = None,
) -> ModuleContractExecutor:
    """Register the production module_contract executor on the closed registry."""
    executor = ModuleContractExecutor(agent=agent, runner=runner)
    registry.register(AssignmentKind.MODULE_CONTRACT, executor)
    return executor


@dataclass(frozen=True, slots=True)
class _ModuleScope:
    prefix: str
    module_id: str
    owned_file_paths: tuple[str, ...]


def _module_scope_from_owned_paths(owned_paths: tuple[str, ...]) -> _ModuleScope:
    modules: set[tuple[str, str]] = set()
    concrete_files: list[str] = []
    for raw_path in owned_paths:
        path = normalize_owned_path(raw_path)
        parts = PurePosixPath(path).parts
        prefix = ""
        module_index = 0
        if parts and parts[0] == "app":
            prefix = "app"
            module_index = 1
        if len(parts) < module_index + 3 or parts[module_index] != "modules":
            raise WorkAssignmentPermanentError(f"unsupported module_contract owned path: {path!r}")
        module_id = parts[module_index + 1]
        modules.add((prefix, module_id))
        if _is_module_contract_file(parts, module_index):
            concrete_files.append(path)
            continue
        raise WorkAssignmentPermanentError(f"module_contract cannot own non-contract artifact: {path!r}")
    if len(modules) != 1:
        raise WorkAssignmentPermanentError("module_contract assignment must target exactly one module")
    prefix, module_id = next(iter(modules))
    if _manifest_path(prefix, module_id) not in set(concrete_files):
        raise WorkAssignmentPermanentError("module_contract assignment must own module.yaml")
    return _ModuleScope(
        prefix=prefix,
        module_id=module_id,
        owned_file_paths=tuple(sorted(concrete_files)),
    )


def _is_module_contract_file(parts: tuple[str, ...], module_index: int) -> bool:
    rel = parts[module_index + 2 :]
    if rel == ("module.yaml",):
        return True
    if len(rel) == 2 and rel[0] == "contracts" and rel[1] in _SUPPORTED_CONTRACT_FILES:
        return True
    return False


def _materialize_owned_module_contract(
    output: Any,
    *,
    module_scope: _ModuleScope,
    owned_paths: tuple[str, ...],
) -> dict[str, str]:
    payload = _coerce_mapping(output)
    if payload.get("mode") != _CONTRACT_MODE:
        raise WorkAssignmentPermanentError("ConfigMiddlewareOutput mode must be module_contract_bundle")
    bundle = payload.get("module_contract")
    if not isinstance(bundle, Mapping):
        raise WorkAssignmentPermanentError("ConfigMiddlewareOutput.module_contract is required")
    if str(bundle.get("module_id") or "").strip() != module_scope.module_id:
        raise WorkAssignmentPermanentError("module_contract.module_id does not match assignment-owned module")
    if payload.get("service_foundation_bundle") is not None or payload.get("subscription_config_bundle") is not None:
        raise WorkAssignmentPermanentError("module_contract output must not include other ConfigMiddleware bundles")
    if payload.get("deleted_files"):
        raise WorkAssignmentPermanentError("module_contract executor does not accept deleted_files")

    typed_payload = {"module_contract": _prune_bundle_to_owned_paths(bundle, module_scope=module_scope, owned_paths=owned_paths)}
    raw_file_map = extract_code_file_map_from_payload(typed_payload)
    file_map = {
        _with_prefix(path, module_scope.prefix): _canonical_yaml(content)
        for path, content in raw_file_map.items()
    }
    owned = set(owned_paths)
    unexpected = sorted(set(file_map) - owned)
    if unexpected:
        raise WorkAssignmentPermanentError(f"module_contract emitted unowned artifact(s): {unexpected}")
    missing = sorted(owned - set(file_map))
    if missing:
        raise WorkAssignmentPermanentError(f"module_contract omitted owned artifact(s): {missing}")
    return {path: file_map[path] for path in sorted(file_map)}


def _validate_materialized_files(
    *,
    file_map: dict[str, str],
    module_scope: _ModuleScope,
) -> list[dict[str, Any]]:
    code_files = [{"filename": path, "content": content} for path, content in sorted(file_map.items())]
    warnings = audit_module_contracts(code_files)
    blocking = [
        warning
        for warning in warnings
        if any(term in warning for term in ("schema_version", "could not parse"))
    ]
    if blocking:
        raise WorkAssignmentPermanentError(f"module contract quality gate failed: {blocking}")

    manifest = _load_yaml(file_map[_manifest_path(module_scope.prefix, module_scope.module_id)])
    try:
        definition = ModuleDefinition.model_validate(manifest)
    except Exception as exc:
        raise WorkAssignmentPermanentError("module.yaml failed runtime module contract validation") from exc
    if definition.module.id != module_scope.module_id:
        raise WorkAssignmentPermanentError("module.yaml module.id does not match assignment-owned module")

    declared_events: set[str] = set()
    for path, content in sorted(file_map.items()):
        name = PurePosixPath(path).name
        if name == "module.yaml":
            continue
        model = _COMPANION_MODELS[name]
        try:
            parsed = model.model_validate(_load_yaml(content))
        except Exception as exc:
            raise WorkAssignmentPermanentError(f"{name} failed runtime module contract validation") from exc
        if isinstance(parsed, ModuleEventsManifest):
            declared_events = parsed.event_types
            for event in parsed.events:
                if event.producer != module_scope.module_id:
                    raise WorkAssignmentPermanentError("events.yaml producer must match module id")

    for action in definition.actions:
        for event_type in action.emits:
            if event_type not in declared_events:
                raise WorkAssignmentPermanentError(
                    f"module action {action.id!r} emits undeclared event {event_type!r}"
                )

    evidence_detail = {
        "module_id": module_scope.module_id,
        "validated_paths": sorted(file_map),
        "audit_warning_count": len(warnings),
    }
    return [
        {
            "validator_id": MODULE_CONTRACT_VALIDATOR_ID,
            "passed": True,
            "evidence_digest": stable_digest(evidence_detail),
            "detail": "module contract YAML validated by runtime models and AppGenerator quality audit",
        }
    ]


def _coerce_mapping(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    if not isinstance(value, Mapping):
        raise WorkAssignmentPermanentError("module contract runner did not return a mapping output")
    return dict(value)


def _context_variables(
    *,
    context: WorkAssignmentExecutionContext,
    module_scope: _ModuleScope,
) -> dict[str, Any]:
    extra_payload = {
        key: value
        for key, value in context.item.payload.items()
        if key != WORK_ASSIGNMENT_PAYLOAD_KEY
    }
    current_build_task = {
        "task_id": context.assignment.assignment_id,
        "task_type": AssignmentKind.MODULE_CONTRACT.value,
        "capability_pack_id": module_scope.module_id,
        "owned_paths": list(context.owned_paths),
        "initial_agent": MODULE_CONTRACT_AGENT_NAME,
    }
    return {
        "task_run_mode": True,
        "current_build_task_id": context.assignment.assignment_id,
        "current_build_task_type": AssignmentKind.MODULE_CONTRACT.value,
        "current_build_task": current_build_task,
        "allowed_agent_ids": list(context.allowed_agent_ids),
        "owned_paths": list(context.owned_paths),
        "module_id": module_scope.module_id,
        "workspace_id": context.workspace_id,
        "work_assignment": context.assignment.model_dump(mode="json"),
        "work_assignment_payload": extra_payload,
    }


def _build_prompt(
    *,
    context: WorkAssignmentExecutionContext,
    module_scope: _ModuleScope,
) -> str:
    owned = "\n".join(f"- {path}" for path in context.owned_paths)
    return (
        "Generate the typed AppGenerator module contract bundle for this single "
        "WorkAssignment.\n"
        f"Assignment id: {context.assignment.assignment_id}\n"
        f"Module id: {module_scope.module_id}\n"
        f"Required output schema: {MODULE_CONTRACT_STRUCTURED_OUTPUT_ID}\n"
        "Output mode must be module_contract_bundle.\n"
        "Emit only the typed module_contract object. The executor will ignore "
        "model-supplied file paths and materialize canonical paths from the typed "
        "artifact identities.\n"
        "Owned artifact paths:\n"
        f"{owned}\n"
        "Do not include backend Python, page schemas, deployment files, "
        "source-control workflow files, repository operations, or provider "
        "implementation code."
    )


async def _renew_or_stale(context: WorkAssignmentExecutionContext) -> None:
    renewed = await context.renew_lease(None)
    if not renewed:
        raise WorkAssignmentLeaseLostError("module_contract lease authority lost")


def _manifest_path(prefix: str, module_id: str) -> str:
    base = PurePosixPath("modules", module_id, "module.yaml")
    return str(PurePosixPath(prefix, base)) if prefix else str(base)


def _with_prefix(path: str, prefix: str) -> str:
    if not prefix:
        return normalize_owned_path(path)
    normalized = normalize_owned_path(path)
    if normalized.startswith(f"{prefix}/"):
        return normalized
    return normalize_owned_path(str(PurePosixPath(prefix, normalized)))


def _canonical_yaml(content: str) -> str:
    parsed = _load_yaml(content)
    return cast(
        str,
        yaml.safe_dump(
            parsed,
            allow_unicode=True,
            sort_keys=True,
            default_flow_style=False,
        ),
    )


def _load_yaml(content: str) -> dict[str, Any]:
    try:
        parsed = yaml.safe_load(content)
    except Exception as exc:
        raise WorkAssignmentPermanentError("module contract materialized invalid YAML") from exc
    if not isinstance(parsed, dict):
        raise WorkAssignmentPermanentError("module contract YAML must contain an object")
    return parsed


def _attempt_id(assignment_id: str, assignment_digest: str) -> str:
    return f"{assignment_id}:{stable_digest({'assignment_digest': assignment_digest})[:16]}"


def _prune_bundle_to_owned_paths(
    bundle: Mapping[str, Any],
    *,
    module_scope: _ModuleScope,
    owned_paths: tuple[str, ...],
) -> dict[str, Any]:
    owned = set(owned_paths)
    pruned = {"module_id": module_scope.module_id, "module_yaml": bundle.get("module_yaml")}
    key_paths = {
        "events_yaml": _contract_path(module_scope.prefix, module_scope.module_id, "events.yaml"),
        "reactions_yaml": _contract_path(module_scope.prefix, module_scope.module_id, "reactions.yaml"),
        "notifications_yaml": _contract_path(module_scope.prefix, module_scope.module_id, "notifications.yaml"),
        "settings_yaml": _contract_path(module_scope.prefix, module_scope.module_id, "settings.yaml"),
        "admin_yaml": _contract_path(module_scope.prefix, module_scope.module_id, "admin.yaml"),
        "profile_yaml": _contract_path(module_scope.prefix, module_scope.module_id, "profile.yaml"),
        "relationships_yaml": _contract_path(module_scope.prefix, module_scope.module_id, "relationships.yaml"),
        "policy_hooks_yaml": _contract_path(module_scope.prefix, module_scope.module_id, "policy_hooks.yaml"),
    }
    for key, path in key_paths.items():
        value = bundle.get(key)
        if path in owned:
            pruned[key] = value
        elif value is not None and not _empty_companion_manifest(key, value):
            raise WorkAssignmentPermanentError(f"module_contract emitted unowned non-empty {key}")
    if bundle.get("runtime_extensions_yaml") is not None:
        raise WorkAssignmentPermanentError("module_contract executor does not emit runtime_extensions.yaml")
    return pruned


def _contract_path(prefix: str, module_id: str, filename: str) -> str:
    base = PurePosixPath("modules", module_id, "contracts", filename)
    return str(PurePosixPath(prefix, base)) if prefix else str(base)


def _empty_companion_manifest(key: str, value: Any) -> bool:
    if not isinstance(value, Mapping):
        return False
    list_fields = {
        "events_yaml": ("events",),
        "reactions_yaml": ("reactions",),
        "notifications_yaml": ("notifications",),
        "settings_yaml": ("settings", "features"),
        "admin_yaml": ("panels", "hooks"),
        "profile_yaml": ("panels", "tabs"),
        "relationships_yaml": ("providers",),
        "policy_hooks_yaml": ("hooks",),
    }
    fields = list_fields.get(key, ())
    return all(not value.get(field) for field in fields)


def _required_text(value: str, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field_name} must be non-empty")
    return str(text)


__all__ = [
    "MODULE_CONTRACT_AGENT_NAME",
    "MODULE_CONTRACT_STRUCTURED_OUTPUT_ID",
    "MODULE_CONTRACT_VALIDATOR_ID",
    "MODULE_CONTRACT_WORKFLOW_NAME",
    "ModuleContractExecutor",
    "register_module_contract_executor",
]
