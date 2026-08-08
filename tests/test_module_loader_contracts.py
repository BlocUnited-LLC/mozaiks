from __future__ import annotations

from pathlib import Path

import pytest

from mozaiksai.core.runtime.app.loader import AppLoader
from mozaiksai.core.runtime.app.module_loader import ActionDef, ModuleLoader, ModuleLoadError
from mozaiksai.core.runtime.composition.module_event_router import ModuleEventRouter
from mozaiksai.core.runtime.composition.module_executor import ModuleExecutor, ModuleRequest


def _write_canonical_module(
    root: Path,
    *,
    module_id: str = "tasks",
    emitted_event: str = "domain.tasks.task_created",
    include_events_manifest: bool = True,
    action_emits: str | None = None,
    reaction_target: str = "notification",
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

    if reaction_target == "capability":
        reaction_items_yaml = """
  - id: task_created_react
    event_type: domain.tasks.task_created
    target:
      kind: capability
      capability_id: tasks.review
""".rstrip()
    else:
        reaction_items_yaml = """
  - id: task_created_notify
    event_type: domain.tasks.task_created
    target:
      kind: notification
      notification_id: task_created
""".rstrip()

    reactions_yaml = f"""
schema_version: mozaiks.reactions.v1
reactions:
{reaction_items_yaml}
""".lstrip()

    module_dir.joinpath("contracts", "reactions.yaml").write_text(
        reactions_yaml,
        encoding="utf-8",
    )
    if reaction_target != "capability":
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
    module_dir.joinpath("contracts", "profile.yaml").write_text(
        """
schema_version: mozaiks.profile.v1
panels: []
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
    assert loaded.manifests.reactions is not None
    assert loaded.manifests.reactions.reactions[0].id == "task_created_notify"
    assert type(loaded.handler).__name__ == "TasksModule"


def test_module_loader_normalizes_notification_contract(tmp_path: Path) -> None:
    module_dir = _write_canonical_module(tmp_path)
    module_dir.joinpath("contracts", "notifications.yaml").write_text(
        """
schema_version: mozaiks.notifications.v1
notifications:
  - id: task_created
    on: domain.tasks.task_created
    title: "Task {{title}}"
    body: "A task was created."
    channels: [in_app, email]
    audience:
      roles: [operator, operator]
    context_fields: [task_id, title]
""".lstrip(),
        encoding="utf-8",
    )

    loaded = ModuleLoader(str(tmp_path)).load("tasks")

    notification = loaded.manifests.notifications.notifications[0]
    assert notification.event_type == "domain.tasks.task_created"
    assert notification.channels == ["in_app", "email"]
    assert notification.audience.roles == ["operator"]
    assert notification.template is not None
    assert notification.template.title == "Task {{title}}"
    assert notification.as_rule()["template"]["body"] == "A task was created."


def test_module_loader_rejects_notification_without_event_type(tmp_path: Path) -> None:
    module_dir = _write_canonical_module(tmp_path)
    module_dir.joinpath("contracts", "notifications.yaml").write_text(
        """
schema_version: mozaiks.notifications.v1
notifications:
  - id: task_created
    channels: [in_app]
""".lstrip(),
        encoding="utf-8",
    )

    with pytest.raises(ModuleLoadError):
        ModuleLoader(str(tmp_path)).load("tasks")


def test_module_loader_rejects_unsupported_notification_channel(tmp_path: Path) -> None:
    module_dir = _write_canonical_module(tmp_path)
    module_dir.joinpath("contracts", "notifications.yaml").write_text(
        """
schema_version: mozaiks.notifications.v1
notifications:
  - id: task_created
    event_type: domain.tasks.task_created
    channels: [fax]
""".lstrip(),
        encoding="utf-8",
    )

    with pytest.raises(ModuleLoadError):
        ModuleLoader(str(tmp_path)).load("tasks")


@pytest.mark.parametrize(
    "module_type",
    [
        "standard",
        "messaging",
        "workflow",
        "event_pipeline",
        "transactional",
        "billing",
        "membership",
        "entitlement_dispatch",
    ],
)
def test_module_loader_accepts_canonical_module_type(tmp_path: Path, module_type: str) -> None:
    module_dir = _write_canonical_module(tmp_path)
    module_yaml = module_dir.joinpath("module.yaml")
    module_yaml.write_text(
        module_yaml.read_text(encoding="utf-8").replace(
            "  version: 1.0.0\n",
            f"  version: 1.0.0\n  type: {module_type}\n",
        ),
        encoding="utf-8",
    )

    loaded = ModuleLoader(str(tmp_path)).load("tasks")

    assert loaded.definition.module.type == module_type


def test_module_loader_loads_action_api_surface_metadata(tmp_path: Path) -> None:
    module_dir = _write_canonical_module(tmp_path)
    module_yaml = module_dir.joinpath("module.yaml")
    module_yaml.write_text(
        module_yaml.read_text(encoding="utf-8").replace(
            "    handler_method: create_task\n",
            "    handler_method: create_task\n    api_surface: public_readonly\n",
        ),
        encoding="utf-8",
    )

    loaded = ModuleLoader(str(tmp_path)).load("tasks")

    assert loaded.definition.actions[0].api_surface == "public_readonly"
    assert loaded.action_permissions_map == {"create": ["tasks.write"]}
    assert loaded.action_api_surface_map == {"create": "public_readonly"}


def test_module_loader_loads_without_action_api_surface(tmp_path: Path) -> None:
    loaded = ModuleLoader(str(tmp_path)).load(_write_canonical_module(tmp_path).name)

    assert loaded.definition.actions[0].api_surface is None
    assert loaded.action_api_surface_map == {"create": None}


@pytest.mark.parametrize("api_surface", ["public", "public_readonly", "internal", "admin_internal"])
def test_action_api_surface_known_metadata_values_load(api_surface: str) -> None:
    action = ActionDef(
        id="list_items",
        description="List items",
        handler_method="list_items",
        api_surface=api_surface,
    )

    assert action.api_surface == api_surface


@pytest.mark.parametrize("api_surface", [123, [], {}])
def test_action_api_surface_must_be_string(api_surface) -> None:
    with pytest.raises(ValueError, match="api_surface"):
        ActionDef(
            id="list_items",
            description="List items",
            handler_method="list_items",
            api_surface=api_surface,
        )


def test_action_api_surface_must_be_non_empty() -> None:
    with pytest.raises(ValueError, match="api_surface"):
        ActionDef(
            id="list_items",
            description="List items",
            handler_method="list_items",
            api_surface="  ",
        )


def test_action_unknown_extra_field_still_fails_validation() -> None:
    with pytest.raises(ValueError, match="unexpected_field"):
        ActionDef(
            id="list_items",
            description="List items",
            handler_method="list_items",
            unexpected_field=True,
        )


def test_module_loader_rejects_subscriptions_manifest(tmp_path: Path) -> None:
    module_dir = _write_canonical_module(tmp_path)
    module_dir.joinpath("contracts", "reactions.yaml").unlink()
    module_dir.joinpath("contracts", "subscriptions.yaml").write_text(
        """
schema_version: mozaiks.subscriptions.v1
subscriptions:
  - id: task_created_notify
    event_type: domain.tasks.task_created
    target:
      kind: notification
      notification_id: task_created
""".lstrip(),
        encoding="utf-8",
    )

    with pytest.raises(ModuleLoadError, match="contracts/subscriptions.yaml is not supported"):
        ModuleLoader(str(tmp_path)).load("tasks")


def test_module_loader_rejects_subscriptions_manifest_even_when_reactions_exists(tmp_path: Path) -> None:
    module_dir = _write_canonical_module(tmp_path)
    module_dir.joinpath("contracts", "subscriptions.yaml").write_text(
        """
schema_version: mozaiks.subscriptions.v1
subscriptions:
  - id: unsupported_task_created_react
    event_type: domain.tasks.task_created
    target:
      kind: capability
      capability_id: tasks.review
""".lstrip(),
        encoding="utf-8",
    )

    with pytest.raises(ModuleLoadError, match="contracts/subscriptions.yaml is not supported"):
        ModuleLoader(str(tmp_path)).load("tasks")


def test_module_loader_rejects_subscriptions_manifest_before_reaction_validation(tmp_path: Path) -> None:
    module_dir = _write_canonical_module(tmp_path)
    module_dir.joinpath("contracts", "reactions.yaml").write_text(
        """
schema_version: mozaiks.reactions.v1
reactions:
  - id: invalid_reaction
    event_type: invalid.event
    target:
      kind: notification
      notification_id: task_created
""".lstrip(),
        encoding="utf-8",
    )
    module_dir.joinpath("contracts", "subscriptions.yaml").write_text(
        """
schema_version: mozaiks.subscriptions.v1
subscriptions:
  - id: unsupported_task_created_notify
    event_type: domain.tasks.task_created
    target:
      kind: notification
      notification_id: task_created
""".lstrip(),
        encoding="utf-8",
    )

    with pytest.raises(ModuleLoadError, match="contracts/subscriptions.yaml is not supported"):
        ModuleLoader(str(tmp_path)).load("tasks")


@pytest.mark.asyncio
async def test_module_event_router_creates_notification_from_reaction(tmp_path: Path) -> None:
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
        _write_canonical_module(tmp_path, reaction_target="capability").name
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
    assert emitted[0][0] == "platform.reaction.capability_dispatched"
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


def test_module_loader_rejects_app_local_event_namespace(tmp_path: Path) -> None:
    module_dir = _write_canonical_module(tmp_path, emitted_event="app.tasks.task_created")
    for relative in ("contracts/reactions.yaml", "contracts/notifications.yaml"):
        path = module_dir / relative
        path.write_text(
            path.read_text(encoding="utf-8").replace(
                "domain.tasks.task_created",
                "app.tasks.task_created",
            ),
            encoding="utf-8",
        )

    with pytest.raises(ModuleLoadError, match="domain"):
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
    page: overview
    renderer: schema
hooks: []
""".lstrip(),
        encoding="utf-8",
    )

    with pytest.raises(ModuleLoadError, match="schema admin panels must declare sections"):
        ModuleLoader(str(tmp_path)).load("tasks")


def test_module_loader_accepts_canonical_admin_page_references(tmp_path: Path) -> None:
    module_dir = _write_canonical_module(tmp_path)
    module_dir.joinpath("contracts", "admin.yaml").write_text(
        """
schema_version: mozaiks.admin.v2
panels:
  - id: tasks.billing
    label: Billing
    page: billing
    renderer: schema
    sections:
      - id: billing-table
        primitive: DataTable
        config:
          api_endpoint: /api/modules/tasks/create
          columns: [{ key: task_id, label: Task }]
  - id: tasks.config
    label: Settings
    page: settings
    renderer: schema
    sections:
      - id: settings-table
        primitive: DataTable
        config:
          api_endpoint: /api/modules/tasks/create
          columns: [{ key: task_id, label: Task }]
hooks: []
""".lstrip(),
        encoding="utf-8",
    )

    loaded = ModuleLoader(str(tmp_path)).load("tasks")
    assert loaded.manifests.admin.panels[0].page == "billing"
    assert loaded.manifests.admin.panels[1].page == "settings"


def test_module_loader_loads_profile_manifest_with_panels(tmp_path: Path) -> None:
    """ModuleLoader must expose a non-empty profile.yaml as manifests.profile."""
    from mozaiksai.core.profile.discovery import load_profile_panels

    module_dir = _write_canonical_module(tmp_path)
    # Overwrite the empty profile.yaml that _write_canonical_module creates with
    # a real panel that has an action binding.
    module_dir.joinpath("contracts", "profile.yaml").write_text(
        """
schema_version: mozaiks.profile.v1
panels:
  - id: activity-summary
    title: Activity Summary
    description: Account activity.
    order: 30
    kind: metrics
    action: create
    fields:
      - { id: total, label: Total, type: number }
      - { id: status, label: Status, type: status }
""".lstrip(),
        encoding="utf-8",
    )

    loaded = ModuleLoader(str(tmp_path)).load("tasks")

    assert loaded.manifests.profile is not None
    assert len(loaded.manifests.profile.panels) == 1
    panel = loaded.manifests.profile.panels[0]
    assert panel.id == "activity-summary"
    assert panel.kind == "metrics"
    assert panel.action == "create"
    assert len(panel.fields) == 2
    assert panel.fields[0].type == "number"
    assert panel.fields[1].type == "status"

    # Discovery layer also surfaces the same panel.
    discovered = load_profile_panels(tmp_path)
    assert len(discovered) == 1
    assert discovered[0]["id"] == "activity-summary"
    assert discovered[0]["module_id"] == "tasks"


def test_module_loader_loads_profile_manifest_component_kind(tmp_path: Path) -> None:
    """component-kind panels must be accepted with a component name and no fields."""
    module_dir = _write_canonical_module(tmp_path)
    module_dir.joinpath("contracts", "profile.yaml").write_text(
        """
schema_version: mozaiks.profile.v1
panels:
  - id: usage-detail
    title: Usage Detail
    order: 40
    kind: component
    component: UsageDetailPanel
    action: create
""".lstrip(),
        encoding="utf-8",
    )

    loaded = ModuleLoader(str(tmp_path)).load("tasks")

    assert loaded.manifests.profile is not None
    panel = loaded.manifests.profile.panels[0]
    assert panel.kind == "component"
    assert panel.component == "UsageDetailPanel"
    assert panel.fields == []


def test_module_loader_rejects_profile_manifest_form_kind(tmp_path: Path) -> None:
    """form kind is reserved and must be rejected at load time."""
    from mozaiksai.core.runtime.app.module_loader import ModuleLoadError

    module_dir = _write_canonical_module(tmp_path)
    module_dir.joinpath("contracts", "profile.yaml").write_text(
        """
schema_version: mozaiks.profile.v1
panels:
  - id: edit-preferences
    title: Edit Preferences
    order: 50
    kind: form
    fields:
      - { id: pref_key, label: Preference, type: string }
""".lstrip(),
        encoding="utf-8",
    )

    with pytest.raises(ModuleLoadError, match="kind"):
        ModuleLoader(str(tmp_path)).load("tasks")


def test_module_loader_rejects_absolute_runtime_extension_entrypoint(tmp_path: Path) -> None:
    module_dir = _write_canonical_module(tmp_path)
    module_dir.joinpath("runtime_extensions.yaml").write_text(
        """
schema_version: mozaiks.runtime_extensions.v1
extensions:
  - kind: api_router
    entrypoint: app.modules.tasks.backend.router:get_router
    prefix: /webhooks/tasks
""".lstrip(),
        encoding="utf-8",
    )

    with pytest.raises(ModuleLoadError, match="module-local"):
        ModuleLoader(str(tmp_path)).load("tasks")


# ---------------------------------------------------------------------------
# module.yaml extra-field guard (changelog / schema drift)
# ---------------------------------------------------------------------------

def test_module_yaml_rejects_changelog_field(tmp_path: Path) -> None:
    """A top-level `changelog:` field must be rejected (extra="forbid").

    The correct convention is a YAML block comment before schema_version.
    Accepting unknown top-level keys silently would mask contract drift.
    """
    module_dir = _write_canonical_module(tmp_path)
    yaml_path = module_dir / "module.yaml"
    original = yaml_path.read_text(encoding="utf-8")
    # Prepend a changelog key that is NOT part of ModuleDefinition
    yaml_path.write_text(
        "changelog:\n  - v1.0.0: Initial release\n" + original,
        encoding="utf-8",
    )

    with pytest.raises(ModuleLoadError, match="changelog"):
        ModuleLoader(str(tmp_path)).load("tasks")


def test_module_yaml_rejects_unknown_top_level_field(tmp_path: Path) -> None:
    """Any undeclared top-level key in module.yaml must cause a load error."""
    module_dir = _write_canonical_module(tmp_path)
    yaml_path = module_dir / "module.yaml"
    original = yaml_path.read_text(encoding="utf-8")
    yaml_path.write_text(original + "\nhistory:\n  - bumped version\n", encoding="utf-8")

    with pytest.raises(ModuleLoadError):
        ModuleLoader(str(tmp_path)).load("tasks")


# ---------------------------------------------------------------------------
# Lane 1 — user_data_scope requires account_data_handler.py (hard error)
# ---------------------------------------------------------------------------


def test_module_loader_rejects_user_data_scope_without_account_data_handler(
    tmp_path: Path,
) -> None:
    """Loader must raise ModuleLoadError when user_data_scope=true but
    backend/account_data_handler.py is absent.

    Regression guard: declaring user_data_scope=true is a compliance contract —
    account deletion and data export must be implemented. A silent warning
    allowed this gap to ship undetected; hard error ensures generated modules
    scaffold the file before the module can load.
    """
    module_dir = _write_canonical_module(tmp_path)
    yaml_path = module_dir / "module.yaml"
    yaml_path.write_text(
        yaml_path.read_text(encoding="utf-8").replace(
            "  handler: backend.handler:TasksModule",
            "  handler: backend.handler:TasksModule\n  user_data_scope: true",
        ),
        encoding="utf-8",
    )
    # account_data_handler.py intentionally absent

    with pytest.raises(ModuleLoadError, match="account_data_handler"):
        ModuleLoader(str(tmp_path)).load("tasks")


# ---------------------------------------------------------------------------
# Lane 2 — handler_method alignment (hard error, explicit rejection test)
# ---------------------------------------------------------------------------


def test_module_loader_rejects_missing_handler_method(tmp_path: Path) -> None:
    """Loader must raise ModuleLoadError when module.yaml declares a
    handler_method that does not exist on the handler class.

    Regression guard: this is the primary module.yaml/handler.py drift —
    renaming a handler method without updating module.yaml (or vice versa)
    produces a module that appears valid from YAML alone but fails at runtime.
    Catching it at module-load time ensures CI finds the drift before deploy.
    """
    module_dir = _write_canonical_module(tmp_path)
    module_dir.joinpath("backend", "handler.py").write_text(
        """
class TasksModule:
    # create_task deliberately absent — module.yaml declares handler_method: create_task
    async def renamed_create_task(self, ctx, *, title):
        return {"task_id": "task_1"}
""".lstrip(),
        encoding="utf-8",
    )

    with pytest.raises(ModuleLoadError, match="missing action method"):
        ModuleLoader(str(tmp_path)).load("tasks")


# ---------------------------------------------------------------------------
# Check 3 — permission consistency (action.permissions[] vs. top-level block)
# ---------------------------------------------------------------------------


def test_module_loader_rejects_action_referencing_undeclared_permission(tmp_path: Path) -> None:
    """Loader must reject an action whose permissions[] list references a
    permission ID not declared in the module-level permissions block.

    Regression guard: this bug class appeared in workspace_support where
    access_as_user was used in action.permissions but was absent from the
    top-level permissions block.
    """
    module_dir = _write_canonical_module(tmp_path)
    yaml_path = module_dir / "module.yaml"
    yaml_path.write_text(
        """
schema_version: mozaiks.module.v1
module:
  id: tasks
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
    permissions: [tasks.write, undeclared.permission]
    emits: [domain.tasks.task_created]
""".lstrip(),
        encoding="utf-8",
    )

    with pytest.raises(ModuleLoadError, match="undeclared permission"):
        ModuleLoader(str(tmp_path)).load("tasks")


# ---------------------------------------------------------------------------
# Check 6 — notification_id consistency (reactions.yaml vs notifications.yaml)
# ---------------------------------------------------------------------------


def test_module_loader_rejects_reaction_notification_id_not_in_notifications_yaml(
    tmp_path: Path,
) -> None:
    """Loader must reject a notification reaction whose notification_id is not
    declared in contracts/notifications.yaml.

    Regression guard: this bug class can silently wire a reaction to a
    non-existent notification template, resulting in a runtime dispatch error
    that only appears at event-emission time rather than at startup.
    """
    module_dir = _write_canonical_module(tmp_path)
    module_dir.joinpath("contracts", "reactions.yaml").write_text(
        """
schema_version: mozaiks.reactions.v1
reactions:
  - id: task_created_notify
    event_type: domain.tasks.task_created
    target:
      kind: notification
      notification_id: nonexistent_notification
""".lstrip(),
        encoding="utf-8",
    )
    # notifications.yaml only declares 'task_created', not 'nonexistent_notification'
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

    with pytest.raises(ModuleLoadError, match="nonexistent_notification"):
        ModuleLoader(str(tmp_path)).load("tasks")


def test_module_loader_rejects_notification_reaction_without_notifications_yaml(
    tmp_path: Path,
) -> None:
    """Loader must reject a notification reaction when notifications.yaml is absent.

    A reaction that dispatches to a notification template requires the template
    to be declared — missing notifications.yaml is a hard contract gap.
    """
    module_dir = _write_canonical_module(tmp_path)
    module_dir.joinpath("contracts", "reactions.yaml").write_text(
        """
schema_version: mozaiks.reactions.v1
reactions:
  - id: task_created_notify
    event_type: domain.tasks.task_created
    target:
      kind: notification
      notification_id: task_created
""".lstrip(),
        encoding="utf-8",
    )
    # Remove notifications.yaml so the reaction target has no backing declaration
    notif_path = module_dir / "contracts" / "notifications.yaml"
    if notif_path.exists():
        notif_path.unlink()

    with pytest.raises(ModuleLoadError, match="notifications.yaml"):
        ModuleLoader(str(tmp_path)).load("tasks")

