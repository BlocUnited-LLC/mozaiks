# Runtime Specification

**Status:** Specification
**Created:** 2026-04-06
**Depends on:** MODULAR_ARCHITECTURE_V2.md, EVENT_DRIVEN_EXECUTION_SPEC.md

This document specifies the responsibilities and architecture of the Mozaiks runtime.

> **Critical**: The runtime is **event-first**, not output-first. See [EVENT_DRIVEN_EXECUTION_SPEC.md](./EVENT_DRIVEN_EXECUTION_SPEC.md) for the complete event-driven execution model.

---

## Overview

The runtime is the **central orchestrator** of a Mozaiks application. It:

- Loads and validates the application definition
- Initializes executors (AI, modules)
- Routes requests to appropriate handlers
- Manages context and security
- Coordinates events between subsystems
- Serves as the single entry point

**The runtime is NOT a bridge or translation layer.** It orchestrates independent subsystems without coupling them.

---

## 1. Core Responsibilities

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        RUNTIME RESPONSIBILITIES                              │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  1. APPLICATION LOADING                                                     │
│     • Parse app.yaml                                                        │
│     • Validate configuration                                                │
│     • Detect execution mode                                                 │
│     • Initialize logging                                                    │
│                                                                              │
│  2. EXECUTOR INITIALIZATION                                                 │
│     • Create ModuleExecutor (if enabled)                                    │
│     • Create WorkflowExecutor (if enabled)                                  │
│     • Register in ExecutorRegistry                                          │
│     • Connect to databases                                                  │
│                                                                              │
│  3. REQUEST ROUTING                                                         │
│     • Match routes to handlers                                              │
│     • Dispatch to appropriate executor                                      │
│     • Handle WebSocket upgrades                                             │
│     • Serve static files                                                    │
│                                                                              │
│  4. CONTEXT MANAGEMENT                                                      │
│     • Extract auth from requests                                            │
│     • Build RequestContext                                                  │
│     • Inject context into executors                                         │
│     • Propagate correlation IDs                                             │
│                                                                              │
│  5. EVENT COORDINATION                                                      │
│     • Create EventBus instance                                              │
│     • Route events between subsystems                                       │
│     • Forward platform events                                               │
│     • Handle event-driven triggers                                          │
│                                                                              │
│  6. TRIGGER RESOLUTION                                                      │
│     • Register workflow triggers                                            │
│     • Match events to triggers                                              │
│     • Execute triggered workflows                                           │
│     • Handle scheduled triggers                                             │
│                                                                              │
│  7. UI SERVING                                                              │
│     • Build navigation                                                      │
│     • Serve page definitions                                                │
│     • Handle UI data bindings                                               │
│     • Serve frontend assets                                                 │
│                                                                              │
│  8. LIFECYCLE MANAGEMENT                                                    │
│     • Startup sequence                                                      │
│     • Health checks                                                         │
│     • Graceful shutdown                                                     │
│     • Hot reload (dev mode)                                                 │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Application Loading

### Load Sequence

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        APPLICATION LOAD SEQUENCE                             │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  1. READ app.yaml                                                           │
│       │                                                                     │
│       ▼                                                                     │
│  2. PARSE and VALIDATE                                                      │
│       ┌───────────────────────────────────────────────────────────────────┐│
│       │ AppDefinition.parse_yaml(content)                                  ││
│       │   • Validate required fields (name, version)                       ││
│       │   • Validate capabilities                                          ││
│       │   • Validate module references                                     ││
│       │   • Validate workflow references                                   ││
│       │   • Resolve environment variables                                  ││
│       └───────────────────────────────────────────────────────────────────┘│
│       │                                                                     │
│       ▼                                                                     │
│  3. DETECT EXECUTION MODE                                                   │
│       ┌───────────────────────────────────────────────────────────────────┐│
│       │ if capabilities.ai and capabilities.modules:                       ││
│       │     mode = "full"                                                  ││
│       │ elif capabilities.ai:                                              ││
│       │     mode = "ai_only"                                               ││
│       │ elif capabilities.modules:                                         ││
│       │     mode = "modules_only"                                          ││
│       │ else:                                                              ││
│       │     mode = "static"                                                ││
│       └───────────────────────────────────────────────────────────────────┘│
│       │                                                                     │
│       ▼                                                                     │
│  4. LOAD DEPENDENCIES                                                       │
│       ┌───────────────────────────────────────────────────────────────────┐│
│       │ if mode in ["full", "modules_only"]:                               ││
│       │     load_modules(app_def.modules)                                  ││
│       │                                                                    ││
│       │ if mode in ["full", "ai_only"]:                                    ││
│       │     load_workflows(app_def.workflows)                              ││
│       │                                                                    ││
│       │ if capabilities.ui:                                                ││
│       │     load_pages(app_def.pages)                                      ││
│       └───────────────────────────────────────────────────────────────────┘│
│       │                                                                     │
│       ▼                                                                     │
│  5. INITIALIZE SERVICES                                                     │
│       • Connect to database                                                 │
│       • Create EventBus                                                     │
│       • Register triggers                                                   │
│       • Build navigation                                                    │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

### AppLoader Implementation

```python
# packages/runtime/src/mozaiks_runtime/app/loader.py

class AppLoader:
    """Loads and validates application definitions."""

    @classmethod
    async def load(cls, path: str = ".") -> AppDefinition:
        """Load app from directory."""
        app_yaml = Path(path) / "app.yaml"

        if not app_yaml.exists():
            raise AppLoadError(f"app.yaml not found in {path}")

        with open(app_yaml) as f:
            content = yaml.safe_load(f)

        # Resolve environment variables
        content = cls._resolve_env_vars(content)

        # Parse and validate
        try:
            app_def = AppDefinition.model_validate(content)
        except ValidationError as e:
            raise AppLoadError(f"Invalid app.yaml: {e}")

        # Validate references exist
        await cls._validate_references(path, app_def)

        return app_def

    @classmethod
    def _resolve_env_vars(cls, content: Dict) -> Dict:
        """Resolve ${VAR} references in config."""
        def resolve(value):
            if isinstance(value, str):
                pattern = r'\$\{([^}]+)\}'
                matches = re.findall(pattern, value)
                for match in matches:
                    env_value = os.environ.get(match, "")
                    value = value.replace(f"${{{match}}}", env_value)
                return value
            elif isinstance(value, dict):
                return {k: resolve(v) for k, v in value.items()}
            elif isinstance(value, list):
                return [resolve(v) for v in value]
            return value

        return resolve(content)

    @classmethod
    async def _validate_references(cls, path: str, app_def: AppDefinition):
        """Ensure referenced modules and workflows exist."""
        base = Path(path)

        for module in app_def.modules:
            module_path = base / "modules" / module.name
            if not module_path.exists():
                raise AppLoadError(f"Module not found: {module.name}")

        for workflow in app_def.workflows:
            workflow_path = base / "workflows" / workflow.name
            if not workflow_path.exists():
                raise AppLoadError(f"Workflow not found: {workflow.name}")
```

---

## 3. Executor Registry

### Registry Design

```python
# packages/runtime/src/mozaiks_runtime/composition/executor_registry.py

class ExecutorRegistry:
    """Central registry for all executors."""

    def __init__(self):
        self._executors: Dict[ExecutorType, Executor] = {}
        self._initialized = False

    def register(self, executor: Executor):
        """Register an executor."""
        self._executors[executor.executor_type] = executor

    def get(self, executor_type: ExecutorType) -> Optional[Executor]:
        """Get executor by type."""
        return self._executors.get(executor_type)

    def has(self, executor_type: ExecutorType) -> bool:
        """Check if executor is registered."""
        return executor_type in self._executors

    @property
    def module_executor(self) -> Optional['ModuleExecutor']:
        """Shortcut for module executor."""
        return self._executors.get(ExecutorType.MODULE)

    @property
    def workflow_executor(self) -> Optional['WorkflowExecutor']:
        """Shortcut for workflow executor."""
        return self._executors.get(ExecutorType.AI)

    async def initialize_all(self):
        """Initialize all registered executors."""
        for executor in self._executors.values():
            await executor.initialize()
        self._initialized = True

    async def shutdown(self):
        """Shutdown all executors."""
        for executor in self._executors.values():
            await executor.shutdown()
        self._initialized = False

    def get_context_executors(self) -> Dict[str, Executor]:
        """Get executors for injection into context."""
        return {
            "modules": self.module_executor,
            "ai": self.workflow_executor,
        }
```

### Initialization Sequence

```python
# packages/runtime/src/mozaiks_runtime/server.py

async def initialize_executors(app_def: AppDefinition) -> ExecutorRegistry:
    """Initialize executors based on app definition."""
    registry = ExecutorRegistry()

    # Module executor
    if app_def.capabilities.modules:
        from mozaiks_modules import ModuleExecutor

        executor = ModuleExecutor(
            modules_path="./modules",
            database_config=app_def.database,
        )
        await executor.load_modules(app_def.modules)
        registry.register(executor)

    # Workflow executor
    if app_def.capabilities.ai:
        from mozaiks_ai import WorkflowExecutor

        executor = WorkflowExecutor(
            workflows_path="./workflows",
            database_config=app_def.database,
        )
        await executor.load_workflows(app_def.workflows)
        registry.register(executor)

    await registry.initialize_all()
    return registry
```

---

## 4. Request Routing

### Route Resolution

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        REQUEST ROUTING LOGIC                                 │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  REQUEST: POST /api/contacts                                                 │
│       │                                                                     │
│       ▼                                                                     │
│  1. MIDDLEWARE CHAIN                                                        │
│       CORS → Auth → Context                                                 │
│       │                                                                     │
│       ▼                                                                     │
│  2. ROUTE MATCHING (Priority Order)                                         │
│       │                                                                     │
│       ├─► (1) Workflow route triggers                                       │
│       │       Check: trigger_resolver.has_route("/api/contacts", "POST")    │
│       │       If match → Workflow execution                                 │
│       │                                                                     │
│       ├─► (2) Explicit routes in app.yaml                                   │
│       │       Check: app_def.routes for path match                          │
│       │       If match → Parse handler, dispatch                            │
│       │                                                                     │
│       ├─► (3) Module auto-routes                                            │
│       │       Pattern: /api/{module}/{action}                               │
│       │       If match → Module execution                                   │
│       │                                                                     │
│       ├─► (4) Page routes                                                   │
│       │       Check: app_def.pages for path match                           │
│       │       If match → Render page                                        │
│       │                                                                     │
│       └─► (5) Static files                                                  │
│               Serve from /static or frontend build                          │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

### RequestDispatcher Implementation

```python
# packages/runtime/src/mozaiks_runtime/router/dispatcher.py

class RequestDispatcher:
    """Routes requests to appropriate handlers."""

    def __init__(
        self,
        app_definition: AppDefinition,
        executor_registry: ExecutorRegistry,
        trigger_resolver: TriggerResolver,
        event_bus: EventBus,
    ):
        self._app_def = app_definition
        self._executors = executor_registry
        self._triggers = trigger_resolver
        self._event_bus = event_bus

        # Build route table
        self._routes = self._build_route_table()

    async def dispatch(
        self,
        request: Request,
        context: RequestContext,
    ) -> Response:
        """Dispatch a request to the appropriate handler."""
        path = request.url.path
        method = request.method

        # 1. Check workflow route triggers
        if trigger_result := await self._triggers.handle_route(path, method, request):
            return JSONResponse(trigger_result.data)

        # 2. Check explicit routes
        if route := self._match_route(path, method):
            return await self._dispatch_explicit_route(route, request, context)

        # 3. Check module auto-routes
        if self._is_module_route(path) and self._executors.has(ExecutorType.MODULE):
            return await self._dispatch_module_route(path, method, request, context)

        # 4. Check page routes
        if page := self._match_page(path):
            return await self._dispatch_page(page, context)

        # 5. 404
        raise HTTPException(404, "Not found")

    async def dispatch_module(
        self,
        module_name: str,
        action: str,
        params: Dict[str, Any],
        context: RequestContext,
    ) -> ExecutionResult:
        """Dispatch to module executor."""
        executor = self._executors.module_executor

        if not executor:
            return ExecutionResult(
                success=False,
                error="Modules not available in this execution mode",
            )

        return await executor.execute(
            ExecutionRequest(
                executor_type=ExecutorType.MODULE,
                target=module_name,
                action=action,
                params=params,
                app_id=context.app_id,
                user_id=context.user_id,
                correlation_id=context.correlation_id,
            )
        )

    async def dispatch_ai(
        self,
        workflow_name: str,
        params: Dict[str, Any],
        context: RequestContext,
    ) -> ExecutionResult:
        """Dispatch to AI executor."""
        executor = self._executors.workflow_executor

        if not executor:
            return ExecutionResult(
                success=False,
                error="AI not available in this execution mode",
            )

        return await executor.execute(
            ExecutionRequest(
                executor_type=ExecutorType.AI,
                target=workflow_name,
                params=params,
                app_id=context.app_id,
                user_id=context.user_id,
                correlation_id=context.correlation_id,
            )
        )

    async def dispatch_ai_stream(
        self,
        workflow_name: str,
        message: str,
        session_id: str,
        context: RequestContext,
    ) -> AsyncIterator[Dict]:
        """Dispatch streaming AI request."""
        executor = self._executors.workflow_executor

        if not executor:
            yield {"error": "AI not available"}
            return

        async for event in executor.execute_stream(
            ExecutionRequest(
                executor_type=ExecutorType.AI,
                target=workflow_name,
                params={"message": message, "session_id": session_id},
                app_id=context.app_id,
                user_id=context.user_id,
                session_id=session_id,
                correlation_id=context.correlation_id,
            )
        ):
            yield event
```

---

## 5. Context Management

### Context Flow

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         CONTEXT FLOW                                         │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  REQUEST                                                                    │
│       │                                                                     │
│       ▼                                                                     │
│  AUTH MIDDLEWARE                                                            │
│  ┌───────────────────────────────────────────────────────────────────────┐ │
│  │ 1. Extract token from:                                                 │ │
│  │    - Authorization: Bearer <token>                                     │ │
│  │    - Cookie: session=<token>                                           │ │
│  │    - Sec-WebSocket-Protocol: access_token, <token>                     │ │
│  │                                                                        │ │
│  │ 2. Validate token:                                                     │ │
│  │    - Verify signature                                                  │ │
│  │    - Check expiration                                                  │ │
│  │    - Extract claims                                                    │ │
│  │                                                                        │ │
│  │ 3. Create UserPrincipal:                                               │ │
│  │    UserPrincipal(                                                      │ │
│  │        user_id="user_123",                                             │ │
│  │        email="user@example.com",                                       │ │
│  │        roles=["user", "admin"],                                        │ │
│  │    )                                                                   │ │
│  └───────────────────────────────────────────────────────────────────────┘ │
│       │                                                                     │
│       ▼                                                                     │
│  CONTEXT MIDDLEWARE                                                         │
│  ┌───────────────────────────────────────────────────────────────────────┐ │
│  │ 4. Build RequestContext:                                               │ │
│  │    RequestContext(                                                     │ │
│  │        app_id=app_def.name,          # From app definition            │ │
│  │        user=user_principal,           # From auth middleware           │ │
│  │        request_id=uuid4(),            # Generated                      │ │
│  │        correlation_id=header or uuid4(), # Propagated or new          │ │
│  │        session_id=session_id,         # From request                   │ │
│  │        execution_mode=app_def.mode,   # From app definition           │ │
│  │    )                                                                   │ │
│  │                                                                        │ │
│  │ 5. Attach to request:                                                  │ │
│  │    request.state.context = context                                     │ │
│  └───────────────────────────────────────────────────────────────────────┘ │
│       │                                                                     │
│       ▼                                                                     │
│  HANDLER                                                                    │
│  ┌───────────────────────────────────────────────────────────────────────┐ │
│  │ 6. Access context:                                                     │ │
│  │    context = request.state.context                                     │ │
│  │                                                                        │ │
│  │ 7. Pass to executors:                                                  │ │
│  │    - context.app_id                                                    │ │
│  │    - context.user_id                                                   │ │
│  │    - context.correlation_id                                            │ │
│  └───────────────────────────────────────────────────────────────────────┘ │
│       │                                                                     │
│       ▼                                                                     │
│  EXECUTOR                                                                   │
│  ┌───────────────────────────────────────────────────────────────────────┐ │
│  │ 8. Use context for:                                                    │ │
│  │    - Data scoping (filter by app_id, user_id)                          │ │
│  │    - Event attribution                                                 │ │
│  │    - Audit logging                                                     │ │
│  │    - Rate limiting                                                     │ │
│  └───────────────────────────────────────────────────────────────────────┘ │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Middleware Implementation

```python
# packages/runtime/src/mozaiks_runtime/router/middleware.py

class ContextMiddleware:
    """Builds and injects RequestContext."""

    def __init__(self, app: FastAPI):
        self.app = app

    async def __call__(
        self,
        request: Request,
        call_next: Callable,
    ) -> Response:
        # Get app definition
        app_def: AppDefinition = request.app.state.app_definition

        # Get user from auth (may be None for public routes)
        user: Optional[UserPrincipal] = getattr(request.state, "user", None)

        # Build context
        context = RequestContext(
            app_id=app_def.name,
            user=user,
            request_id=str(uuid.uuid4()),
            correlation_id=request.headers.get("X-Correlation-ID", str(uuid.uuid4())),
            session_id=request.headers.get("X-Session-ID"),
            execution_mode=detect_mode(app_def),
        )

        # Attach to request
        request.state.context = context

        # Add correlation ID to response
        response = await call_next(request)
        response.headers["X-Correlation-ID"] = context.correlation_id
        response.headers["X-Request-ID"] = context.request_id

        return response
```

---

## 6. Event Coordination

> **See also:** [EVENT_DRIVEN_EXECUTION_SPEC.md](./EVENT_DRIVEN_EXECUTION_SPEC.md) for the complete event-first orchestration model, normalized event vocabulary, and event layer separation (domain, runtime, control-plane).

The runtime coordinates events between all subsystems. This section covers the **runtime's responsibilities** for event handling. For the **full event vocabulary and patterns**, see the EVENT_DRIVEN_EXECUTION_SPEC.

### Event Bus Integration

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                       EVENT COORDINATION                                     │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│                          ┌─────────────────┐                                │
│                          │    RUNTIME      │                                │
│                          │                 │                                │
│                          │  ┌───────────┐  │                                │
│                          │  │ EventBus  │  │                                │
│                          │  └─────┬─────┘  │                                │
│                          └───────┬┬───────┘                                │
│                                  ││                                         │
│           ┌──────────────────────┼┼──────────────────────┐                 │
│           │                      ││                      │                 │
│           ▼                      ▼▼                      ▼                 │
│   ┌───────────────┐      ┌───────────────┐      ┌───────────────┐         │
│   │    MODULES    │      │   TRIGGERS    │      │      AI       │         │
│   │               │      │               │      │               │         │
│   │ emit events   │      │ listen for    │      │ emit events   │         │
│   │ on CRUD       │      │ events to     │      │ on workflow   │         │
│   │               │      │ trigger       │      │ execution     │         │
│   │               │      │ workflows     │      │               │         │
│   └───────────────┘      └───────────────┘      └───────────────┘         │
│           │                      │                      │                  │
│           │                      │                      │                  │
│           │              ┌───────────────┐              │                  │
│           └─────────────►│   PLATFORM    │◄─────────────┘                  │
│                          │   FORWARDER   │                                 │
│                          │               │                                 │
│                          │ Forward       │                                 │
│                          │ Commerce.*    │                                 │
│                          │ Observability.*                                 │
│                          │ Learning.*    │                                 │
│                          └───────┬───────┘                                 │
│                                  │                                          │
│                                  ▼                                          │
│                          ┌───────────────┐                                 │
│                          │   PLATFORM    │                                 │
│                          │   (external)  │                                 │
│                          └───────────────┘                                 │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Event Coordinator Implementation

```python
# packages/runtime/src/mozaiks_runtime/composition/event_coordinator.py

class EventCoordinator:
    """Coordinates events between subsystems."""

    def __init__(
        self,
        event_bus: EventBus,
        trigger_resolver: TriggerResolver,
        platform_forwarder: Optional[PlatformEventForwarder] = None,
    ):
        self._bus = event_bus
        self._triggers = trigger_resolver
        self._forwarder = platform_forwarder

        # Subscribe to all events
        self._bus.subscribe("*", self._handle_event)

    async def _handle_event(self, event: Event):
        """Handle an event."""

        # 1. Check for trigger matches
        await self._triggers.handle_event(event)

        # 2. Forward platform events
        if self._should_forward(event):
            await self._forward_to_platform(event)

        # 3. Log for observability
        await self._log_event(event)

    def _should_forward(self, event: Event) -> bool:
        """Check if event should be forwarded to platform."""
        forward_prefixes = [
            "Commerce.",
            "Observability.",
            "Learning.",
            "Evaluation.",
            "Orchestration.Run",
            "Entitlements.",
        ]
        return any(event.type.startswith(p) for p in forward_prefixes)

    async def _forward_to_platform(self, event: Event):
        """Forward event to platform."""
        if self._forwarder:
            await self._forwarder.forward(event)

    async def _log_event(self, event: Event):
        """Log event for debugging/observability."""
        logger.debug(
            "Event",
            event_type=event.type,
            event_id=event.id,
            app_id=event.app_id,
            user_id=event.user_id,
        )
```

---

## 7. Lifecycle Management

### Startup Sequence

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        STARTUP SEQUENCE                                      │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  1. LOAD CONFIGURATION                                                      │
│       ├── Read app.yaml                                                     │
│       ├── Load .env                                                         │
│       └── Resolve environment variables                                     │
│                                                                              │
│  2. CONNECT TO DATABASES                                                    │
│       ├── MongoDB connection                                                │
│       └── Verify connectivity                                               │
│                                                                              │
│  3. CREATE EVENT BUS                                                        │
│       └── In-memory or external (Redis, etc.)                               │
│                                                                              │
│  4. INITIALIZE EXECUTORS                                                    │
│       ├── ModuleExecutor (if enabled)                                       │
│       │   └── Load all modules                                              │
│       └── WorkflowExecutor (if enabled)                                     │
│           ├── Load all workflows                                            │
│           └── Register tools                                                │
│                                                                              │
│  5. REGISTER TRIGGERS                                                       │
│       ├── Event triggers                                                    │
│       ├── Route triggers                                                    │
│       ├── Schedule triggers                                                 │
│       └── Action triggers                                                   │
│                                                                              │
│  6. BUILD UI                                                                │
│       ├── Build navigation                                                  │
│       └── Compile page definitions                                          │
│                                                                              │
│  7. START SERVER                                                            │
│       ├── Mount routes                                                      │
│       ├── Start middleware                                                  │
│       └── Begin accepting requests                                          │
│                                                                              │
│  8. EMIT STARTUP EVENT                                                      │
│       └── Runtime.AppStarted                                                │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Shutdown Sequence

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        SHUTDOWN SEQUENCE                                     │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  1. STOP ACCEPTING NEW REQUESTS                                             │
│       └── Return 503 for new connections                                    │
│                                                                              │
│  2. DRAIN IN-FLIGHT REQUESTS                                                │
│       ├── Wait for active requests (timeout: 30s)                           │
│       └── Close WebSocket connections gracefully                            │
│                                                                              │
│  3. STOP SCHEDULED TRIGGERS                                                 │
│       └── Cancel pending scheduled tasks                                    │
│                                                                              │
│  4. SHUTDOWN EXECUTORS                                                      │
│       ├── WorkflowExecutor.shutdown()                                       │
│       │   └── Wait for running workflows                                    │
│       └── ModuleExecutor.shutdown()                                         │
│                                                                              │
│  5. FLUSH EVENT BUS                                                         │
│       └── Send remaining events to platform                                 │
│                                                                              │
│  6. CLOSE DATABASE CONNECTIONS                                              │
│       └── Close MongoDB client                                              │
│                                                                              │
│  7. EMIT SHUTDOWN EVENT                                                     │
│       └── Runtime.AppStopped                                                │
│                                                                              │
│  8. EXIT                                                                    │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Lifespan Implementation

```python
# packages/runtime/src/mozaiks_runtime/server.py

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application lifecycle."""

    # STARTUP
    logger.info("Starting application...")

    # 1. Load configuration
    app_def = await AppLoader.load(".")
    app.state.app_definition = app_def

    # 2. Connect to database
    db = await connect_database(app_def.database)
    app.state.db = db

    # 3. Create event bus
    event_bus = create_event_bus(app_def)
    app.state.event_bus = event_bus

    # 4. Initialize executors
    registry = await initialize_executors(app_def)
    app.state.executor_registry = registry

    # 5. Register triggers
    trigger_resolver = TriggerResolver(
        workflow_executor=registry.workflow_executor,
        event_bus=event_bus,
    )
    for workflow in app_def.workflows:
        await trigger_resolver.register_workflow_triggers(
            workflow.name,
            await load_workflow_triggers(workflow),
        )
    app.state.trigger_resolver = trigger_resolver

    # 6. Create event coordinator
    coordinator = EventCoordinator(event_bus, trigger_resolver)
    app.state.event_coordinator = coordinator

    # 7. Build navigation
    nav_builder = NavigationBuilder(app_def)
    app.state.navigation = nav_builder.build()

    # 8. Emit startup event
    await event_bus.publish(Event.create(
        event_type="Runtime.AppStarted",
        source="runtime",
        app_id=app_def.name,
        payload={"mode": detect_mode(app_def)},
    ))

    logger.info(f"Application started: {app_def.name} ({detect_mode(app_def)} mode)")

    yield

    # SHUTDOWN
    logger.info("Shutting down application...")

    # Emit shutdown event
    await event_bus.publish(Event.create(
        event_type="Runtime.AppStopped",
        source="runtime",
        app_id=app_def.name,
        payload={},
    ))

    # Shutdown executors
    await registry.shutdown()

    # Close database
    await db.close()

    logger.info("Application stopped")
```

---

## 8. Health Checks

### Health Endpoint

```python
# packages/runtime/src/mozaiks_runtime/health.py

@router.get("/health")
async def health_check(request: Request):
    """Comprehensive health check."""
    checks = {}

    # Database
    try:
        await request.app.state.db.command("ping")
        checks["database"] = {"status": "ok"}
    except Exception as e:
        checks["database"] = {"status": "error", "error": str(e)}

    # Executors
    registry = request.app.state.executor_registry

    if registry.module_executor:
        checks["modules"] = {
            "status": "ok",
            "loaded": len(registry.module_executor.list_modules()),
        }

    if registry.workflow_executor:
        checks["workflows"] = {
            "status": "ok",
            "loaded": len(registry.workflow_executor.list_workflows()),
        }

    # Overall status
    all_ok = all(c.get("status") == "ok" for c in checks.values())

    return {
        "status": "healthy" if all_ok else "degraded",
        "checks": checks,
        "uptime_seconds": time.time() - request.app.state.start_time,
    }
```

---

## 9. Event-First Orchestration (Critical)

### Core Principle

> **The runtime reacts to explicit events, not inferred transcript state or post-hoc output inspection.**

This is a non-negotiable architectural requirement. See [EVENT_DRIVEN_EXECUTION_SPEC.md](./EVENT_DRIVEN_EXECUTION_SPEC.md) for full details.

### What This Means for Runtime

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    EVENT-FIRST ORCHESTRATION MODEL                           │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  ❌ WRONG (What we're moving away from):                                     │
│  ───────────────────────────────────────────────────────────────────────    │
│  1. Run workflow to completion (black box)                                  │
│  2. Wait until end                                                          │
│  3. Parse transcript or structured outputs                                  │
│  4. Infer what happened                                                     │
│  5. Then make orchestration decisions                                       │
│                                                                              │
│  ✅ CORRECT (What we're doing):                                              │
│  ───────────────────────────────────────────────────────────────────────    │
│  1. Workflow emits explicit checkpoint events                               │
│  2. Adapter captures events in REAL-TIME                                    │
│  3. Events normalized to stable runtime vocabulary                          │
│  4. Orchestration reacts to events IMMEDIATELY                              │
│  5. Decisions made DURING execution, not after                              │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Adapter Responsibility

The execution adapter MUST iterate events in real-time, not treat workflows as black boxes.

```python
# packages/runtime/src/mozaiks_runtime/adapter/execution_adapter.py

class ExecutionAdapter:
    """Adapts AG2 execution to normalized runtime events."""

    async def execute_workflow(
        self,
        workflow: Workflow,
        context: RequestContext,
    ) -> AsyncIterator[Event]:
        """Execute workflow with real-time event iteration."""

        # DO NOT run to completion then interpret
        # DO iterate events as they occur

        async for ag2_event in workflow.run_iter():
            # Map AG2 event to normalized runtime event IMMEDIATELY
            normalized = self._normalize_event(ag2_event)

            # Emit normalized event IMMEDIATELY
            yield normalized

            # Allow gating/pause/abort DURING iteration
            if await self._should_pause(normalized):
                yield Event.create("process.paused", ...)
                await self._wait_for_resume()

            if await self._should_abort(normalized):
                yield Event.create("process.failed", ...)
                return

        # Final completion event
        yield Event.create("process.completed", ...)

    def _normalize_event(self, ag2_event: AG2Event) -> Event:
        """Map AG2 events to normalized runtime events."""
        mapping = {
            "GroupChatRunChatEvent": "task.started",
            "TextEvent": "chat.message_appended",
            "ToolCallEvent": "chat.tool_call_requested",
            "ToolResponseEvent": "chat.tool_result_received",
            "RunCompletionEvent": "chat.run_complete",
            # Custom checkpoints
            "DecompositionPlannedEvent": "runtime.decomposition_planned",
            "ArtifactPublishedEvent": "artifact.ready",
        }
        return Event.create(
            event_type=mapping.get(type(ag2_event).__name__, "task.progress"),
            payload=ag2_event.to_dict(),
        )
```

### Source of Truth

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        SOURCE OF TRUTH                                       │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  ✅ SOURCE OF TRUTH (use for orchestration):                                 │
│  • Normalized runtime events                                                 │
│  • Control-plane state                                                       │
│  • Persisted session state                                                   │
│                                                                              │
│  ❌ NOT SOURCE OF TRUTH (never use for orchestration):                       │
│  • Transcript text                                                           │
│  • Structured output discovery                                               │
│  • Message patterns                                                          │
│  • Inferred workflow state                                                   │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

### MFJ (Mid-Flight Journey) Triggering

MFJ fan-out MUST be triggered by explicit runtime events, not by discovering structured outputs.

```
WRONG:  DecompositionAgent produces output → runtime parses → discovers plan → triggers MFJ
RIGHT:  DecompositionAgent → emits runtime.decomposition_planned → runtime reacts → MFJ fan-out
```

---

## Summary

### Runtime Responsibilities Checklist

| Responsibility | Description |
|----------------|-------------|
| **Load App** | Parse app.yaml, validate, detect mode |
| **Init Executors** | Create and register ModuleExecutor, WorkflowExecutor |
| **Route Requests** | Match to triggers, routes, modules, pages |
| **Manage Context** | Build and inject RequestContext |
| **Coordinate Events** | Route events, trigger workflows, forward to platform |
| **Resolve Triggers** | Match events to workflow triggers |
| **Serve UI** | Build navigation, serve pages |
| **Lifecycle** | Startup, health checks, graceful shutdown |

### Key Principles

1. **Runtime is the orchestrator** - It composes, doesn't implement
2. **Executors are independent** - Runtime coordinates, executors execute
3. **Context flows from runtime** - Security context injected everywhere
4. **Events are the nervous system** - Runtime routes events between subsystems
5. **Single entry point** - All requests go through runtime
6. **Event-first, not output-first** - React to explicit events, not transcript parsing
7. **Real-time event iteration** - Process events during execution, not after
8. **Events are source of truth** - Never infer state from transcript or outputs
