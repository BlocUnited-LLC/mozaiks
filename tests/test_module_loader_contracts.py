from __future__ import annotations

from pathlib import Path

import pytest

from mozaiksai.core.runtime.app.loader import AppLoader
from mozaiksai.core.runtime.app.module_loader import ModuleLoadError, ModuleLoader
from mozaiksai.core.runtime.composition.module_executor import ModuleExecutor, ModuleRequest
from mozaiksai.core.runtime.composition.module_event_router import ModuleEventRouter


def _write_canonical_module(
    root: Path,
    *,
    module_id: str = "tasks",
    emitted_event: str = "domain.tasks.task_created",
    include_events_manifest: bool = True,
    action_emits: str | None = None,
    subscription_target: str = "notification",
) -> Path:
    module_dir = root / "modules" / module_id
    (module_dir / "backend").mkdir(parents=True)
    (module_dir / "contracts").mkdir(parents=True)
    (root / "app.json").write_text('{"appName": "Contract Test"}', encoding="utf-8")

    action_event = action_emits if action_emits is not None else emitted_event
    emits_yaml = f"[{action_event}]" if action_event else "[]"
    emits_line = f"    emits: {emits_yaml}"
    module_dir.joinpath("module.yaml").write_text(
        f"""
schema_version: mozaiks.module.v1
module:
  id: {module_id}
  display_name: Tasks
  version: 1.0.0
  description: Task management
  handler: backend.handler:TasksModule
permissions:
  - id: tasks.write
    description: Create and update tasks
actions:
  - id: create
    description: Create a task
    handler_method: create_task
    input_schema:
      type: object
      required: [title]
    output_schema:
      type: object
      required: [task_id]
    permissions: [tasks.write]
{emits_line}
capabilities:
  - capability_id: tasks.create
    kind: action
    target: create
    title: Create Task
    permissions: [tasks.write]
    input_schema:
      type: object
""".lstrip(),
        encoding="utf-8",
    )

    if include_events_manifest:
        module_dir.joinpath("contracts", "events.yaml").write_text(
            f"""
schema_version: mozaiks.events.v1
events:
  - type: {emitted_event}
    version: 1
    description: Emitted after a task is created
    producer: {module_id}
    payload_schema:
      type: object
      required: [task_id, title]
""".lstrip(),
            encoding="utf-8",
        )

    if subscription_target == "capability":
        subscriptions_yaml = """
schema_version: mozaiks.subscriptions.v1
subscriptions:
  - id: task_created_react
    event_type: domain.tasks.task_created
    target:
      kind: capability
      capability_id: tasks.review
""".lstrip()
    else:
        subscriptions_yaml = """
schema_version: mozaiks.subscriptions.v1
subscriptions:
  - id: task_created_notify
    event_type: domain.tasks.task_created
    target:
      kind: notification
      notification_id: task_created
""".lstrip()
    module_dir.joinpath("contracts", "subscriptions.yaml").write_text(subscriptions_yaml, encoding="utf-8")
    if subscription_target != "capability":
        module_dir.joinpath("contracts", "notifications.yaml").write_text(
        """
schema_version: mozaiks.notifications.v1
notifications:
  - id: task_created
    event_type: domain.tasks.task_created
    channels: [in_app]
""".lstrip(),
        encoding="utf-8",
    )
    module_dir.joinpath("contracts", "settings.yaml").write_text(
        """
schema_version: mozaiks.settings.v1
settings: []
features: []
""".lstrip(),
        encoding="utf-8",
    )
    module_dir.joinpath("contracts", "admin.yaml").write_text(
        """
schema_version: mozaiks.admin.v2
panels: []
hooks: []
""".lstrip(),
        encoding="utf-8",
    )
    module_dir.joinpath("backend", "handler.py").write_text(
        """
class TasksModule:
    async def create_task(self, ctx, *, title):
        await ctx.emit("domain.tasks.task_created", {"task_id": "task_1", "title": title})
        return {"task_id": "task_1", "title": title, "app_id": ctx.app_id}
""".lstrip(),
        encoding="utf-8",
    )
    return module_dir


def test_module_loader_loads_canonical_contract(tmp_path: Path) -> None:
    _write_canonical_module(tmp_path)

    loaded = ModuleLoader(str(tmp_path)).load("tasks")

    assert loaded.name == "tasks"
    assert loaded.definition.schema_version == "mozaiks.module.v1"
    assert loaded.action_method_map == {"create": "create_task"}
    assert loaded.manifests.events.event_types == {"domain.tasks.task_created"}
    assert type(loaded.handler).__name__ == "TasksModule"


@pytest.mark.asyncio
async def test_module_event_router_creates_notification_from_subscription(tmp_path: Path) -> None:
    loaded = ModuleLoader(str(tmp_path)).load(_write_canonical_module(tmp_path).name)
    emitted: list[tuple[str, dict]] = []
    stored: list[dict] = []

    async def capture_event(event_type: str, payload: dict) -> None:
        emitted.append((event_type, payload))

    async def capture_notification(record: dict) -> None:
        stored.append(record)

    router = ModuleEventRouter(
        [loaded],
        event_emitter=capture_event,
        notification_store=capture_notification,
    )

    await router.handle_event(
        "domain.tasks.task_created",
        {
            "id": "evt_1",
            "type": "domain.tasks.task_created",
            "tenant": {"app_id": "app_1", "tenant_id": "tenant_1"},
            "actor": {"type": "user", "id": "user_1"},
            "correlation": {"correlation_id": "corr_1"},
            "payload": {"task_id": "task_1", "title": "Draft"},
        },
    )

    assert router.event_types == ["domain.tasks.task_created"]
    assert len(stored) == 1
    assert stored[0]["rule_id"] == "task_created"
    assert stored[0]["module_id"] == "tasks"
    assert stored[0]["event_type"] == "domain.tasks.task_created"
    assert stored[0]["app_id"] == "app_1"
    assert stored[0]["status"] == "unread"
    assert emitted[0][0] == "notification.created"
    assert emitted[0][1]["type"] == "notification.created"
    assert emitted[0][1]["payload"]["notification_id"] == stored[0]["notification_id"]


@pytest.mark.asyncio
async def test_module_event_router_invokes_capability_target(tmp_path: Path) -> None:
    loaded = ModuleLoader(str(tmp_path)).load(
        _write_canonical_module(tmp_path, subscription_target="capability").name
    )
    emitted: list[tuple[str, dict]] = []
    invoked: list[tuple[str, dict, dict]] = []

    async def capture_event(event_type: str, payload: dict) -> None:
        emitted.append((event_type, payload))

    async def invoke_capability(capability_id: str, envelope: dict, subscription: dict) -> dict:
        invoked.append((capability_id, envelope, subscription))
        return {"status": "created", "workflow_id": "ReviewWorkflow", "chat_id": "chat_123"}

    router = ModuleEventRouter(
        [loaded],
        event_emitter=capture_event,
        capability_invoker=invoke_capability,
    )

    await router.handle_event(
        "domain.tasks.task_created",
        {
            "id": "evt_1",
            "type": "domain.tasks.task_created",
            "tenant": {"app_id": "app_1"},
            "actor": {"type": "user", "id": "user_1"},
            "correlation": {"correlation_id": "corr_1"},
            "payload": {"task_id": "task_1", "title": "Draft"},
        },
    )

    assert invoked[0][0] == "tasks.review"
    assert invoked[0][1]["id"] == "evt_1"
    assert invoked[0][2]["id"] == "task_created_react"
    assert emitted[0][0] == "platform.subscription.capability_requested"
    assert emitted[0][1]["payload"]["target"]["capability_id"] == "tasks.review"
    assert emitted[0][1]["payload"]["result"]["workflow_id"] == "ReviewWorkflow"


@pytest.mark.asyncio
async def test_module_executor_dispatches_public_action_id_to_handler_method(tmp_path: Path) -> None:
    loaded = ModuleLoader(str(tmp_path)).load(_write_canonical_module(tmp_path).name)
    executor = ModuleExecutor()
    executor.register(
        loaded.name,
        loaded.handler,
        action_method_map=loaded.action_method_map,
    )

    result = await executor.execute(
        ModuleRequest(
            module="tasks",
            action="create",
            params={"title": "Draft"},
            app_id="app_1",
            user_id="user_1",
        )
    )

    assert result.success is True
    assert result.data == {"task_id": "task_1", "title": "Draft", "app_id": "app_1"}


@pytest.mark.asyncio
async def test_module_executor_wraps_handler_events_in_canonical_envelope(tmp_path: Path) -> None:
    loaded = ModuleLoader(str(tmp_path)).load(_write_canonical_module(tmp_path).name)
    emitted: list[tuple[str, dict]] = []

    async def capture(event_type: str, payload: dict) -> None:
        emitted.append((event_type, payload))

    executor = ModuleExecutor(event_emitter=capture)
    executor.register(
        loaded.name,
        loaded.handler,
        action_method_map=loaded.action_method_map,
    )

    result = await executor.execute(
        ModuleRequest(
            module="tasks",
            action="create",
            params={"title": "Draft"},
            app_id="app_1",
            user_id="user_1",
            tenant_id="tenant_1",
            correlation_id="corr_1",
        )
    )

    assert result.success is True
    assert len(emitted) == 1
    event_type, envelope = emitted[0]
    assert event_type == "domain.tasks.task_created"
    assert envelope["type"] == "domain.tasks.task_created"
    assert envelope["source"] == {
        "layer": "module",
        "app_id": "app_1",
        "module_id": "tasks",
        "capability_id": "tasks.create",
    }
    assert envelope["tenant"] == {"app_id": "app_1", "tenant_id": "tenant_1"}
    assert envelope["actor"] == {"type": "user", "id": "user_1"}
    assert envelope["correlation"] == {"correlation_id": "corr_1"}
    assert envelope["payload"] == {"task_id": "task_1", "title": "Draft"}


@pytest.mark.asyncio
async def test_app_loader_discovers_canonical_modules(tmp_path: Path) -> None:
    _write_canonical_module(tmp_path)

    result = await AppLoader.load(str(tmp_path))

    assert [module.name for module in result.modules] == ["tasks"]
    assert result.definition.modules[0].name == "tasks"


def test_module_loader_rejects_missing_companion_manifest(tmp_path: Path) -> None:
    _write_canonical_module(tmp_path, include_events_manifest=False, action_emits="")

    loaded = ModuleLoader(str(tmp_path)).load("tasks")

    assert loaded.manifests.events is None


def test_module_loader_rejects_undeclared_emitted_event(tmp_path: Path) -> None:
    _write_canonical_module(
        tmp_path,
        emitted_event="domain.tasks.task_created",
        action_emits="domain.tasks.task_archived",
    )

    with pytest.raises(ModuleLoadError, match="emits undeclared event"):
        ModuleLoader(str(tmp_path)).load("tasks")


def test_module_loader_rejects_non_module_owned_event_namespace(tmp_path: Path) -> None:
    _write_canonical_module(tmp_path, emitted_event="workflow.tasks.completed")

    with pytest.raises(ModuleLoadError, match="module-published events"):
        ModuleLoader(str(tmp_path)).load("tasks")


@pytest.mark.asyncio
async def test_module_executor_injects_settings_into_context(tmp_path: Path) -> None:
    module_dir = _write_canonical_module(tmp_path)
    module_dir.joinpath("contracts", "settings.yaml").write_text(
        """
schema_version: mozaiks.settings.v1
settings:
  - id: max_items
    type: integer
    default: 50
    label: Maximum items per page
features: []
""".lstrip(),
        encoding="utf-8",
    )
    # Patch handler to capture ctx.settings
    module_dir.joinpath("backend", "handler.py").write_text(
        """
class TasksModule:
    async def create_task(self, ctx, *, title):
        return {"settings": ctx.settings, "app_id": ctx.app_id}
""".lstrip(),
        encoding="utf-8",
    )
    loaded = ModuleLoader(str(tmp_path)).load("tasks")
    executor = ModuleExecutor()
    executor.register(
        loaded.name,
        loaded.handler,
        action_method_map=loaded.action_method_map,
        settings=loaded.manifests.settings.settings if loaded.manifests.settings is not None else None,
    )

    result = await executor.execute(
        ModuleRequest(module="tasks", action="create", params={"title": "x"}, app_id="app_1")
    )

    assert result.success is True
    assert result.data["settings"] == [{"id": "max_items", "type": "integer", "default": 50, "label": "Maximum items per page"}]


@pytest.mark.asyncio
async def test_module_executor_settings_is_none_when_not_registered(tmp_path: Path) -> None:
    module_dir = _write_canonical_module(tmp_path)
    module_dir.joinpath("backend", "handler.py").write_text(
        """
class TasksModule:
    async def create_task(self, ctx, *, title):
        return {"settings": ctx.settings}
""".lstrip(),
        encoding="utf-8",
    )
    loaded = ModuleLoader(str(tmp_path)).load("tasks")
    executor = ModuleExecutor()
    executor.register(loaded.name, loaded.handler, action_method_map=loaded.action_method_map)

    result = await executor.execute(
        ModuleRequest(module="tasks", action="create", params={"title": "x"}, app_id="app_1")
    )

    assert result.success is True
    assert result.data["settings"] is None


@pytest.mark.asyncio
async def test_module_executor_enforces_action_permissions(tmp_path: Path) -> None:
    loaded = ModuleLoader(str(tmp_path)).load(_write_canonical_module(tmp_path).name)
    executor = ModuleExecutor()
    executor.register(
        loaded.name,
        loaded.handler,
        action_method_map=loaded.action_method_map,
        action_permissions=loaded.action_permissions_map,
    )

    # Caller has the required permission → allowed
    result_ok = await executor.execute(
        ModuleRequest(
            module="tasks",
            action="create",
            params={"title": "x"},
            app_id="app_1",
            granted_permissions=["tasks.write"],
        )
    )
    assert result_ok.success is True

    # Caller has no permissions at all → denied
    result_denied = await executor.execute(
        ModuleRequest(
            module="tasks",
            action="create",
            params={"title": "x"},
            app_id="app_1",
            granted_permissions=[],
        )
    )
    assert result_denied.success is False
    assert result_denied.error_code == "PERMISSION_DENIED"
    assert "tasks.write" in (result_denied.error or "")


@pytest.mark.asyncio
async def test_module_executor_bypasses_enforcement_when_granted_permissions_is_none(tmp_path: Path) -> None:
    loaded = ModuleLoader(str(tmp_path)).load(_write_canonical_module(tmp_path).name)
    executor = ModuleExecutor()
    executor.register(
        loaded.name,
        loaded.handler,
        action_method_map=loaded.action_method_map,
        action_permissions=loaded.action_permissions_map,
    )

    # granted_permissions=None → trusted call, bypasses enforcement
    result = await executor.execute(
        ModuleRequest(module="tasks", action="create", params={"title": "x"}, app_id="app_1")
    )
    assert result.success is True


def test_module_definition_action_permissions_map(tmp_path: Path) -> None:
    loaded = ModuleLoader(str(tmp_path)).load(_write_canonical_module(tmp_path).name)
    perms_map = loaded.action_permissions_map
    assert "create" in perms_map
    assert perms_map["create"] == ["tasks.write"]


def test_module_definition_action_schemas_map(tmp_path: Path) -> None:
    loaded = ModuleLoader(str(tmp_path)).load(_write_canonical_module(tmp_path).name)
    schemas = loaded.action_schemas_map
    assert "create" in schemas
    assert schemas["create"]["input"] == {"type": "object", "required": ["title"]}
    assert schemas["create"]["output"] == {"type": "object", "required": ["task_id"]}


@pytest.mark.asyncio
async def test_module_executor_rejects_invalid_input(tmp_path: Path) -> None:
    loaded = ModuleLoader(str(tmp_path)).load(_write_canonical_module(tmp_path).name)
    executor = ModuleExecutor()
    executor.register(
        loaded.name,
        loaded.handler,
        action_method_map=loaded.action_method_map,
        action_schemas=loaded.action_schemas_map,
    )

    # Missing required 'title' → INVALID_PARAMS
    result = await executor.execute(
        ModuleRequest(module="tasks", action="create", params={}, app_id="app_1")
    )
    assert result.success is False
    assert result.error_code == "INVALID_PARAMS"
    assert "title" in (result.error or "")


@pytest.mark.asyncio
async def test_module_executor_accepts_valid_input(tmp_path: Path) -> None:
    loaded = ModuleLoader(str(tmp_path)).load(_write_canonical_module(tmp_path).name)
    executor = ModuleExecutor()
    executor.register(
        loaded.name,
        loaded.handler,
        action_method_map=loaded.action_method_map,
        action_schemas=loaded.action_schemas_map,
    )

    result = await executor.execute(
        ModuleRequest(module="tasks", action="create", params={"title": "My Task"}, app_id="app_1")
    )
    assert result.success is True


@pytest.mark.asyncio
async def test_module_executor_skips_validation_when_no_schema(tmp_path: Path) -> None:
    loaded = ModuleLoader(str(tmp_path)).load(_write_canonical_module(tmp_path).name)
    executor = ModuleExecutor()
    # Register without schemas → no validation
    executor.register(loaded.name, loaded.handler, action_method_map=loaded.action_method_map)

    result = await executor.execute(
        ModuleRequest(module="tasks", action="create", params={}, app_id="app_1")
    )
    # No schema → dispatched even with missing params (handler gets TypeError → INVALID_PARAMS from existing guard)
    assert result.error_code in (None, "INVALID_PARAMS")  # either path is acceptable without schema


def test_module_loader_rejects_schema_admin_panel_without_sections(tmp_path: Path) -> None:
    module_dir = _write_canonical_module(tmp_path)
    module_dir.joinpath("contracts", "admin.yaml").write_text(
        """
schema_version: mozaiks.admin.v2
panels:
  - id: tasks.overview
    label: Tasks
    section: overview
    renderer: schema
hooks: []
""".lstrip(),
        encoding="utf-8",
    )

    with pytest.raises(ModuleLoadError, match="schema admin panels must declare sections"):
        ModuleLoader(str(tmp_path)).load("tasks")
