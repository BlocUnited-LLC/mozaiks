# Mozaiks Modular Architecture v2

**Status:** Design Proposal
**Created:** 2026-04-06
**Based on:** Actual system state (two working runtimes)

---

## Design Philosophy

This architecture does NOT merge the AI runtime and module runtime. Instead, it introduces a **composition layer** that can orchestrate both while keeping them fully independent.

**Core Principle:** Each layer can run standalone. The App Runtime is an orchestrator, not a bridge.

---

## 1. Package Structure

```
mozaiks/
├── packages/
│   ├── core/                    # Shared primitives (interfaces, types, utilities)
│   ├── ai/                      # AI workflow execution (current mozaiks)
│   ├── modules/                 # Module execution (current mozaiks-core-public backend)
│   ├── runtime/                 # NEW: App composition layer
│   ├── ui/                      # UI rendering system
│   └── cli/                     # CLI tool
├── templates/                   # App templates for CLI
└── examples/                    # Example apps
```

### Package Definitions

#### `@mozaiks/core`

**Purpose:** Shared primitives that all packages can depend on.

**Responsibilities:**
- Interface definitions (protocols)
- Event envelope schema
- Context object definition
- Configuration loading
- Database connection utilities
- Auth token validation (shared logic)

**Depends on:** Nothing (leaf package)

**Depended on by:** All other packages

```
packages/core/
├── src/
│   └── mozaiks_core/
│       ├── __init__.py
│       ├── interfaces/
│       │   ├── __init__.py
│       │   ├── event_bus.py       # EventBus protocol
│       │   ├── executor.py        # Executor protocols
│       │   ├── context.py         # Context protocol
│       │   └── storage.py         # Storage protocols
│       ├── events/
│       │   ├── __init__.py
│       │   ├── envelope.py        # Event schema
│       │   └── types.py           # Event type constants
│       ├── context/
│       │   ├── __init__.py
│       │   └── request_context.py # Request context implementation
│       ├── config/
│       │   ├── __init__.py
│       │   ├── loader.py          # YAML/JSON loading
│       │   └── schema.py          # Config validation
│       ├── auth/
│       │   ├── __init__.py
│       │   ├── jwt.py             # JWT validation
│       │   └── user.py            # User principal
│       └── db/
│           ├── __init__.py
│           └── mongo.py           # MongoDB utilities
└── pyproject.toml
```

---

#### `@mozaiks/ai`

**Purpose:** Execute AI workflows using AG2. This is the current `mozaiks` runtime, repackaged.

**Responsibilities:**
- Load workflow definitions (YAML)
- Create and manage AG2 agents
- Execute multi-agent conversations
- Stream events via transport
- Persist conversation history
- Track token usage

**Depends on:** `@mozaiks/core`

**Depended on by:** `@mozaiks/runtime` (optional)

**Does NOT depend on:** `@mozaiks/modules`

```
packages/ai/
├── src/
│   └── mozaiks_ai/
│       ├── __init__.py
│       ├── workflow/
│       │   ├── __init__.py
│       │   ├── loader.py          # Load workflow YAML
│       │   ├── manager.py         # Workflow registry
│       │   ├── executor.py        # AG2 execution
│       │   └── declarative/       # YAML contracts
│       ├── agents/
│       │   ├── __init__.py
│       │   ├── factory.py         # Create agents from YAML
│       │   └── prompts.py         # Prompt assembly
│       ├── tools/
│       │   ├── __init__.py
│       │   ├── loader.py          # Load tools from Python files
│       │   ├── registry.py        # Tool registration
│       │   └── ui_tools.py        # UI Tool handling
│       ├── transport/
│       │   ├── __init__.py
│       │   ├── websocket.py       # WebSocket transport
│       │   └── protocol.py        # Message protocol
│       ├── persistence/
│       │   ├── __init__.py
│       │   └── chat_store.py      # Chat history storage
│       ├── server.py              # Standalone FastAPI server
│       └── executor.py            # WorkflowExecutor implementation
└── pyproject.toml
```

**Standalone usage:**
```bash
# Run AI runtime only
pip install mozaiks-ai
mozaiks-ai serve --workflows ./workflows --port 8001
```

---

#### `@mozaiks/modules`

**Purpose:** Execute modules (plugins). This is the current `mozaiks-core-public` backend, repackaged.

**Responsibilities:**
- Load module definitions
- Execute module logic (CRUD operations)
- Manage module lifecycle
- Publish module events

**Depends on:** `@mozaiks/core`

**Depended on by:** `@mozaiks/runtime` (optional)

**Does NOT depend on:** `@mozaiks/ai`

```
packages/modules/
├── src/
│   └── mozaiks_modules/
│       ├── __init__.py
│       ├── loader.py              # Load modules from disk
│       ├── registry.py            # Module registry
│       ├── executor.py            # ModuleExecutor implementation
│       ├── context_injection.py   # Inject app_id, user_id
│       ├── server.py              # Standalone FastAPI server
│       └── builtin/               # Built-in modules
│           ├── __init__.py
│           ├── users.py           # User management
│           └── settings.py        # Settings management
└── pyproject.toml
```

**Standalone usage:**
```bash
# Run module runtime only
pip install mozaiks-modules
mozaiks-modules serve --modules ./modules --port 8002
```

---

#### `@mozaiks/runtime` (NEW - Critical)

**Purpose:** Compose AI and modules into a unified application. This is the orchestration layer.

**Responsibilities:**
- Load app definition
- Route requests to appropriate executor (AI or modules)
- Manage shared context
- Coordinate events between systems
- Provide unified auth
- Serve as single entry point

**Depends on:** `@mozaiks/core`, `@mozaiks/ai` (optional), `@mozaiks/modules` (optional)

**Depended on by:** `@mozaiks/cli`

```
packages/runtime/
├── src/
│   └── mozaiks_runtime/
│       ├── __init__.py
│       ├── app/
│       │   ├── __init__.py
│       │   ├── definition.py      # App definition schema
│       │   ├── loader.py          # Load app from disk
│       │   └── registry.py        # App registry
│       ├── router/
│       │   ├── __init__.py
│       │   ├── dispatcher.py      # Route requests
│       │   └── middleware.py      # Auth, context injection
│       ├── composition/
│       │   ├── __init__.py
│       │   ├── ai_adapter.py      # Adapter for AI executor
│       │   ├── module_adapter.py  # Adapter for module executor
│       │   └── event_coordinator.py # Cross-system events
│       ├── server.py              # Unified FastAPI server
│       └── modes.py               # Execution mode detection
└── pyproject.toml
```

---

#### `@mozaiks/ui`

**Purpose:** UI system for rendering pages and components.

**Responsibilities:**
- Define page schemas
- Render UI (React or server-side)
- Handle navigation
- Load module frontends

**Depends on:** `@mozaiks/core`

**Depended on by:** `@mozaiks/runtime`

```
packages/ui/
├── src/
│   └── mozaiks_ui/
│       ├── __init__.py
│       ├── pages/
│       │   ├── __init__.py
│       │   ├── schema.py          # Page definition schema
│       │   └── renderer.py        # Page rendering
│       ├── navigation/
│       │   ├── __init__.py
│       │   └── builder.py         # Build nav from modules
│       └── components/
│           ├── __init__.py
│           └── registry.py        # Component registry
├── frontend/                      # React app (optional)
│   ├── src/
│   │   ├── App.tsx
│   │   ├── pages/
│   │   └── components/
│   └── package.json
└── pyproject.toml
```

---

#### `@mozaiks/cli`

**Purpose:** CLI tool for project management.

**Responsibilities:**
- Create new projects
- Add modules/workflows
- Run dev server
- Build for production

**Depends on:** `@mozaiks/core`, `@mozaiks/runtime`

```
packages/cli/
├── src/
│   └── mozaiks_cli/
│       ├── __init__.py
│       ├── main.py                # Entry point
│       └── commands/
│           ├── __init__.py
│           ├── new.py             # Create project
│           ├── add.py             # Add module/workflow
│           ├── dev.py             # Dev server
│           └── build.py           # Production build
└── pyproject.toml
```

---

## 2. Core Interfaces

These interfaces are defined in `@mozaiks/core` and implemented by other packages.

### EventBus Interface

```python
# packages/core/src/mozaiks_core/interfaces/event_bus.py

from typing import Protocol, Callable, Awaitable, Any, Dict, List, Optional
from dataclasses import dataclass
from datetime import datetime
import uuid


@dataclass
class Event:
    """Canonical event structure."""
    id: str
    type: str                      # e.g., "module.notes.created", "ai.workflow.completed"
    timestamp: datetime
    source: str                    # "ai" | "modules" | "runtime" | "external"

    # Scoping
    app_id: str
    user_id: Optional[str] = None
    session_id: Optional[str] = None

    # Payload
    payload: Dict[str, Any] = None

    # Causality
    correlation_id: Optional[str] = None
    causation_id: Optional[str] = None

    @classmethod
    def create(
        cls,
        event_type: str,
        payload: Dict[str, Any],
        tenant: "TenantContext",
        source: str = "modules",
        correlation_id: Optional[str] = None,
    ) -> "Event":
        """
        Create an event with proper tenant context.

        Usage:
            event = Event.create(
                event_type="contacts.created",
                payload={"contact_id": "123", "name": "John"},
                tenant=ctx.tenant,  # From RequestContext
            )
            await event_bus.publish(event)
        """
        return cls(
            id=str(uuid.uuid4()),
            type=event_type,
            timestamp=datetime.utcnow(),
            source=source,
            app_id=tenant.app_id,
            user_id=tenant.user_id,
            payload=payload or {},
            correlation_id=correlation_id,
        )


class EventBus(Protocol):
    """Event bus interface. Implementations may vary."""

    def subscribe(
        self,
        event_type: str,
        handler: Callable[[Event], Awaitable[None]],
        filter_fn: Optional[Callable[[Event], bool]] = None,
    ) -> str:
        """
        Subscribe to events of a given type.

        Args:
            event_type: Event type pattern (supports wildcards: "module.*")
            handler: Async function to handle events
            filter_fn: Optional filter function

        Returns:
            Subscription ID (for unsubscribing)
        """
        ...

    def unsubscribe(self, subscription_id: str) -> bool:
        """Unsubscribe from events."""
        ...

    async def publish(self, event: Event) -> None:
        """Publish an event to all matching subscribers."""
        ...

    async def publish_and_wait(
        self,
        event: Event,
        response_type: str,
        timeout: float = 30.0,
    ) -> Optional[Event]:
        """Publish event and wait for a response event."""
        ...


class EventBusFactory(Protocol):
    """Factory for creating event bus instances."""

    def create(self, app_id: str) -> EventBus:
        """Create an event bus for an app."""
        ...
```

#### Event Routing Rules

Event types support wildcard matching for subscriptions:

| Pattern | Matches | Does NOT Match |
|---------|---------|----------------|
| `contacts.created` | `contacts.created` | `contacts.updated` |
| `contacts.*` | `contacts.created`, `contacts.updated` | `projects.created` |
| `*.created` | `contacts.created`, `projects.created` | `contacts.updated` |
| `*` | All events | (none) |

#### Event Flow Example

```python
# In a module: emit domain event
async def create_contact(ctx: RequestContext, data: dict):
    contact = await storage.insert(data)

    # Emit domain event
    await ctx.event_bus.publish(Event.create(
        event_type="contacts.created",
        payload={"contact_id": contact["_id"], **data},
        tenant=ctx.tenant,
    ))

    return contact


# In runtime: subscribe to events for workflow triggers
event_bus.subscribe(
    "contacts.created",
    handler=lambda event: trigger_workflow("ContactFollowUp", event),
)


# In runtime: forward platform-routed events
event_bus.subscribe(
    "commerce.*",
    handler=lambda event: platform_client.forward_event(event),
)
```

### Executor Interfaces

```python
# packages/core/src/mozaiks_core/interfaces/executor.py

from typing import Protocol, Dict, Any, Optional, AsyncIterator
from dataclasses import dataclass
from enum import Enum


class ExecutorType(Enum):
    AI = "ai"
    MODULE = "module"


@dataclass
class ExecutionRequest:
    """Request to execute something."""
    executor_type: ExecutorType
    target: str                    # workflow name or module name
    action: Optional[str] = None   # For modules: "list", "create", etc.
    params: Dict[str, Any] = None

    # Context (injected by runtime)
    app_id: str = None
    user_id: str = None
    session_id: str = None
    correlation_id: str = None


@dataclass
class ExecutionResult:
    """Result of execution."""
    success: bool
    data: Dict[str, Any] = None
    error: Optional[str] = None
    events: list = None            # Events generated during execution


class Executor(Protocol):
    """Base executor interface."""

    @property
    def executor_type(self) -> ExecutorType:
        """Return executor type."""
        ...

    async def execute(self, request: ExecutionRequest) -> ExecutionResult:
        """Execute a request."""
        ...

    def can_handle(self, target: str) -> bool:
        """Check if this executor can handle the target."""
        ...


class StreamingExecutor(Executor, Protocol):
    """Executor that can stream results."""

    async def execute_stream(
        self,
        request: ExecutionRequest,
    ) -> AsyncIterator[Dict[str, Any]]:
        """Execute with streaming output."""
        ...


class WorkflowExecutor(StreamingExecutor, Protocol):
    """AI workflow executor interface."""

    async def list_workflows(self) -> list:
        """List available workflows."""
        ...

    async def get_workflow_config(self, name: str) -> Dict[str, Any]:
        """Get workflow configuration."""
        ...


class ModuleExecutor(Executor, Protocol):
    """Module executor interface."""

    async def list_modules(self) -> list:
        """List available modules."""
        ...

    async def get_module_config(self, name: str) -> Dict[str, Any]:
        """Get module configuration."""
        ...
```

### Context Interface

```python
# packages/core/src/mozaiks_core/interfaces/context.py

from typing import Protocol, Dict, Any, Optional
from dataclasses import dataclass, field


@dataclass
class UserPrincipal:
    """Authenticated user."""
    user_id: str
    username: Optional[str] = None
    email: Optional[str] = None
    roles: list = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class TenantContext:
    """
    Tenant context for multi-tenancy.
    Matches the 'tenant' field in EVENT_CONTRACTS.md event envelope.
    """
    app_id: str                              # Required: which app this belongs to
    user_id: Optional[str] = None            # User who initiated the action
    organization_id: Optional[str] = None    # Optional: org-level isolation
    environment: str = "production"          # "production" | "staging" | "development"

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dict for event payload."""
        return {
            "app_id": self.app_id,
            "user_id": self.user_id,
            "organization_id": self.organization_id,
            "environment": self.environment
        }


@dataclass
class RequestContext:
    """
    Context for a single request.
    Passed through the entire request lifecycle.

    Usage in tools/modules:
        @system_tool
        async def get_contacts(ctx: RequestContext) -> list:
            # Access tenant info
            app_id = ctx.tenant.app_id
            user_id = ctx.user_id

            # Use storage with tenant isolation
            return await ctx.storage.find({"tenant": ctx.tenant.app_id})
    """
    # Identity
    app_id: str
    user: Optional[UserPrincipal] = None

    # Request metadata
    request_id: str = None
    correlation_id: str = None
    session_id: str = None

    # Runtime info
    execution_mode: str = "full"   # "ai_only" | "modules_only" | "full"

    # Mutable state (for passing data between middleware)
    state: Dict[str, Any] = field(default_factory=dict)

    # Platform connection (for external API calls)
    platform_url: Optional[str] = None
    auth_token: Optional[str] = None

    @property
    def user_id(self) -> Optional[str]:
        return self.user.user_id if self.user else None

    @property
    def is_authenticated(self) -> bool:
        return self.user is not None

    @property
    def tenant(self) -> TenantContext:
        """Get tenant context for events and isolation."""
        return TenantContext(
            app_id=self.app_id,
            user_id=self.user_id
        )


class ContextProvider(Protocol):
    """Provides request context."""

    async def get_context(self, request: Any) -> RequestContext:
        """Extract context from a request."""
        ...

    def inject_context(self, data: Dict[str, Any], context: RequestContext) -> Dict[str, Any]:
        """Inject context into data dict (for module execution)."""
        ...
```

### Storage Interface

```python
# packages/core/src/mozaiks_core/interfaces/storage.py

from typing import Protocol, Dict, Any, List, Optional


class DocumentStorage(Protocol):
    """Document storage interface (MongoDB-like)."""

    async def find_one(
        self,
        collection: str,
        query: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        ...

    async def find_many(
        self,
        collection: str,
        query: Dict[str, Any],
        limit: int = 100,
        skip: int = 0,
        sort: Optional[List[tuple]] = None,
    ) -> List[Dict[str, Any]]:
        ...

    async def insert_one(
        self,
        collection: str,
        document: Dict[str, Any],
    ) -> str:
        """Returns inserted document ID."""
        ...

    async def update_one(
        self,
        collection: str,
        query: Dict[str, Any],
        update: Dict[str, Any],
        upsert: bool = False,
    ) -> bool:
        ...

    async def delete_one(
        self,
        collection: str,
        query: Dict[str, Any],
    ) -> bool:
        ...


class ScopedStorage:
    """
    Storage scoped to app_id and optionally user_id.
    Ensures data isolation.
    """

    def __init__(
        self,
        storage: DocumentStorage,
        app_id: str,
        user_id: Optional[str] = None,
    ):
        self._storage = storage
        self._app_id = app_id
        self._user_id = user_id

    def _scope_query(self, query: Dict[str, Any]) -> Dict[str, Any]:
        """Add scope to query."""
        scoped = {"app_id": self._app_id, **query}
        if self._user_id:
            scoped["user_id"] = self._user_id
        return scoped

    def _scope_document(self, doc: Dict[str, Any]) -> Dict[str, Any]:
        """Add scope to document."""
        scoped = {"app_id": self._app_id, **doc}
        if self._user_id:
            scoped["user_id"] = self._user_id
        return scoped

    async def find_one(self, collection: str, query: Dict[str, Any]):
        return await self._storage.find_one(collection, self._scope_query(query))

    async def find_many(self, collection: str, query: Dict[str, Any], **kwargs):
        return await self._storage.find_many(collection, self._scope_query(query), **kwargs)

    async def insert_one(self, collection: str, document: Dict[str, Any]):
        return await self._storage.insert_one(collection, self._scope_document(document))

    async def update_one(self, collection: str, query: Dict[str, Any], update: Dict[str, Any], **kwargs):
        return await self._storage.update_one(collection, self._scope_query(query), update, **kwargs)

    async def delete_one(self, collection: str, query: Dict[str, Any]):
        return await self._storage.delete_one(collection, self._scope_query(query))
```

---

## 3. App Runtime Design

The App Runtime is the composition layer that orchestrates AI and modules.

### App Definition Schema

```python
# packages/runtime/src/mozaiks_runtime/app/definition.py

from typing import List, Dict, Any, Optional, Literal
from pydantic import BaseModel, Field


class ModuleRef(BaseModel):
    """Reference to a module."""
    name: str
    path: str = None               # Relative path, defaults to modules/{name}
    enabled: bool = True
    config: Dict[str, Any] = {}


class WorkflowRef(BaseModel):
    """Reference to a workflow."""
    name: str
    path: str = None               # Relative path, defaults to workflows/{name}
    enabled: bool = True
    config: Dict[str, Any] = {}


class RouteDefinition(BaseModel):
    """Route definition."""
    path: str                      # URL path pattern
    handler: str                   # "module:notes:list" or "workflow:assistant"
    method: str = "GET"
    auth_required: bool = True
    roles: List[str] = []


class PageDefinition(BaseModel):
    """Page definition for UI."""
    path: str                      # URL path
    title: str
    module: Optional[str] = None   # Module that provides the page
    component: Optional[str] = None # Component name
    layout: str = "default"
    nav: Optional[Dict[str, Any]] = None  # Navigation config


class AppCapabilities(BaseModel):
    """What capabilities are enabled."""
    ai: bool = True
    modules: bool = True
    ui: bool = True


class AppDefinition(BaseModel):
    """
    Complete app definition.
    Loaded from app.yaml in app root.
    """
    # Identity
    name: str
    version: str = "1.0.0"
    description: str = ""

    # Capabilities
    capabilities: AppCapabilities = AppCapabilities()

    # Components
    modules: List[ModuleRef] = []
    workflows: List[WorkflowRef] = []

    # Routing
    routes: List[RouteDefinition] = []
    pages: List[PageDefinition] = []

    # Auth
    auth: Dict[str, Any] = {}

    # Database
    database: Dict[str, Any] = {}

    # Feature flags
    features: Dict[str, bool] = {}
```

### App Folder Structure

```
my-app/
├── app.yaml                     # App definition (REQUIRED)
├── .env                         # Environment variables
├── modules/                     # Module implementations
│   ├── notes/
│   │   ├── module.yaml          # Module config
│   │   ├── logic.py             # Backend logic
│   │   └── frontend/            # Frontend components (optional)
│   │       ├── index.js
│   │       └── components/
│   └── tasks/
│       ├── module.yaml
│       └── logic.py
├── workflows/                   # AI workflows (optional)
│   └── assistant/
│       ├── orchestrator.yaml
│       ├── agents.yaml
│       ├── tools.yaml
│       └── tools/
│           └── search.py
├── pages/                       # Custom pages (optional)
│   └── dashboard.yaml
└── frontend/                    # Custom frontend (optional)
    ├── src/
    └── package.json
```

### Example app.yaml

```yaml
# app.yaml - Complete app definition

name: my-crm
version: 1.0.0
description: A simple CRM with AI assistant

# What's enabled
capabilities:
  ai: true
  modules: true
  ui: true

# Modules to load
modules:
  - name: contacts
    config:
      max_per_user: 10000
  - name: deals
  - name: notes
  - name: settings
    # Built-in module, no path needed

# Workflows to load
workflows:
  - name: sales_assistant
    config:
      model: gpt-4
      max_turns: 50

# API routes
routes:
  # Module routes (auto-generated from modules, but can override)
  - path: /api/contacts
    handler: module:contacts:list
    method: GET
  - path: /api/contacts
    handler: module:contacts:create
    method: POST
  - path: /api/contacts/{id}
    handler: module:contacts:get
    method: GET

  # AI routes
  - path: /api/chat
    handler: workflow:sales_assistant
    method: POST

# Pages
pages:
  - path: /
    title: Dashboard
    component: DashboardPage
    nav:
      label: Home
      icon: home
      order: 0

  - path: /contacts
    title: Contacts
    module: contacts
    nav:
      label: Contacts
      icon: users
      order: 10

  - path: /deals
    title: Deals
    module: deals
    nav:
      label: Deals
      icon: briefcase
      order: 20

  - path: /assistant
    title: AI Assistant
    component: ChatPage
    nav:
      label: Assistant
      icon: message-circle
      order: 30

# Auth configuration
auth:
  mode: local                    # local | external | platform
  jwt_secret: ${JWT_SECRET}
  session_expire_minutes: 60

# Database
database:
  provider: mongodb
  uri: ${DATABASE_URI}
  name: ${DATABASE_NAME}

# Feature flags
features:
  dark_mode: true
  notifications: true
  export: false
```

### Request Lifecycle

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           REQUEST LIFECYCLE                                  │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  1. REQUEST ARRIVES                                                         │
│     GET /api/contacts?limit=10                                              │
│     Authorization: Bearer <jwt>                                             │
│                                                                              │
│  2. MIDDLEWARE CHAIN                                                        │
│     ┌─────────────────────────────────────────────────────────────────┐    │
│     │ CORSMiddleware                                                   │    │
│     │     │                                                            │    │
│     │     ▼                                                            │    │
│     │ AuthMiddleware                                                   │    │
│     │     - Validate JWT                                               │    │
│     │     - Extract user_id, roles                                     │    │
│     │     - Create UserPrincipal                                       │    │
│     │     │                                                            │    │
│     │     ▼                                                            │    │
│     │ ContextMiddleware                                                │    │
│     │     - Create RequestContext                                      │    │
│     │     - Set app_id (from config or domain)                         │    │
│     │     - Attach user to context                                     │    │
│     │     - Generate request_id, correlation_id                        │    │
│     │     │                                                            │    │
│     │     ▼                                                            │    │
│     │ Request.state.context = RequestContext(...)                      │    │
│     └─────────────────────────────────────────────────────────────────┘    │
│                                                                              │
│  3. ROUTE DISPATCH                                                          │
│     ┌─────────────────────────────────────────────────────────────────┐    │
│     │ Router.dispatch(request)                                         │    │
│     │     │                                                            │    │
│     │     ├─► Match route: /api/contacts → module:contacts:list        │    │
│     │     │                                                            │    │
│     │     ├─► Parse handler: executor=MODULE, target=contacts,         │    │
│     │     │                  action=list                               │    │
│     │     │                                                            │    │
│     │     └─► Create ExecutionRequest                                  │    │
│     └─────────────────────────────────────────────────────────────────┘    │
│                                                                              │
│  4. EXECUTOR SELECTION                                                      │
│     ┌─────────────────────────────────────────────────────────────────┐    │
│     │ ExecutorRegistry.get_executor(executor_type)                     │    │
│     │     │                                                            │    │
│     │     ├─► executor_type == MODULE → ModuleExecutor                 │    │
│     │     │                                                            │    │
│     │     └─► executor_type == AI → WorkflowExecutor                   │    │
│     └─────────────────────────────────────────────────────────────────┘    │
│                                                                              │
│  5. EXECUTION                                                               │
│     ┌─────────────────────────────────────────────────────────────────┐    │
│     │ ModuleExecutor.execute(request)                                  │    │
│     │     │                                                            │    │
│     │     ├─► Inject context into params                               │    │
│     │     │   params["app_id"] = context.app_id                        │    │
│     │     │   params["user_id"] = context.user_id                      │    │
│     │     │                                                            │    │
│     │     ├─► Load module: contacts                                    │    │
│     │     │                                                            │    │
│     │     ├─► Call module.execute({                                    │    │
│     │     │       "action": "list",                                    │    │
│     │     │       "app_id": "app_123",                                 │    │
│     │     │       "user_id": "user_456",                               │    │
│     │     │       "limit": 10                                          │    │
│     │     │   })                                                       │    │
│     │     │                                                            │    │
│     │     ├─► Module queries MongoDB (scoped)                          │    │
│     │     │                                                            │    │
│     │     ├─► Module publishes event                                   │    │
│     │     │   event_bus.publish(Event(                                 │    │
│     │     │       type="module.contacts.listed",                       │    │
│     │     │       source="modules",                                    │    │
│     │     │       payload={"count": 10}                                │    │
│     │     │   ))                                                       │    │
│     │     │                                                            │    │
│     │     └─► Return ExecutionResult                                   │    │
│     └─────────────────────────────────────────────────────────────────┘    │
│                                                                              │
│  6. RESPONSE                                                                │
│     ┌─────────────────────────────────────────────────────────────────┐    │
│     │ {                                                                │    │
│     │   "contacts": [...],                                             │    │
│     │   "count": 10,                                                   │    │
│     │   "has_more": true                                               │    │
│     │ }                                                                │    │
│     └─────────────────────────────────────────────────────────────────┘    │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

### AI Request Lifecycle (WebSocket)

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        AI REQUEST LIFECYCLE (WebSocket)                      │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  1. WEBSOCKET CONNECT                                                       │
│     WS /ws/chat/{session_id}                                                │
│     Sec-WebSocket-Protocol: access_token, <jwt>                             │
│                                                                              │
│  2. AUTH & CONTEXT                                                          │
│     ┌─────────────────────────────────────────────────────────────────┐    │
│     │ - Extract JWT from protocol header                               │    │
│     │ - Validate token                                                 │    │
│     │ - Create RequestContext                                          │    │
│     │ - Register connection in transport                               │    │
│     └─────────────────────────────────────────────────────────────────┘    │
│                                                                              │
│  3. USER SENDS MESSAGE                                                      │
│     {"type": "message", "content": "Show my top contacts"}                  │
│                                                                              │
│  4. ROUTE TO AI EXECUTOR                                                    │
│     ┌─────────────────────────────────────────────────────────────────┐    │
│     │ Router identifies this as AI request                             │    │
│     │     │                                                            │    │
│     │     └─► WorkflowExecutor.execute_stream(request)                 │    │
│     └─────────────────────────────────────────────────────────────────┘    │
│                                                                              │
│  5. WORKFLOW EXECUTION (streaming)                                          │
│     ┌─────────────────────────────────────────────────────────────────┐    │
│     │ async for event in executor.execute_stream(request):             │    │
│     │     │                                                            │    │
│     │     ├─► Event: {"kind": "chat.thinking", "agent": "Assistant"}   │    │
│     │     │   → Stream to WebSocket                                    │    │
│     │     │                                                            │    │
│     │     ├─► Event: {"kind": "chat.tool_call", "tool": "get_contacts"}│    │
│     │     │   → Stream to WebSocket                                    │    │
│     │     │                                                            │    │
│     │     │   ┌─────────────────────────────────────────────────────┐ │    │
│     │     │   │ TOOL EXECUTION (within AI executor)                  │ │    │
│     │     │   │                                                      │ │    │
│     │     │   │ Tool calls: module:contacts:list                     │ │    │
│     │     │   │                                                      │ │    │
│     │     │   │ HOW? Option A: Direct call if modules loaded         │ │    │
│     │     │   │       module_executor.execute(...)                   │ │    │
│     │     │   │                                                      │ │    │
│     │     │   │       Option B: HTTP call if separate process        │ │    │
│     │     │   │       httpx.post("http://modules:8002/execute/...")  │ │    │
│     │     │   │                                                      │ │    │
│     │     │   │       Option C: Event-based (async)                  │ │    │
│     │     │   │       event_bus.publish_and_wait(...)                │ │    │
│     │     │   └─────────────────────────────────────────────────────┘ │    │
│     │     │                                                            │    │
│     │     ├─► Event: {"kind": "chat.tool_result", "data": [...]}      │    │
│     │     │   → Stream to WebSocket                                    │    │
│     │     │                                                            │    │
│     │     ├─► Event: {"kind": "chat.text", "content": "Here are..."}  │    │
│     │     │   → Stream to WebSocket                                    │    │
│     │     │                                                            │    │
│     │     └─► Event: {"kind": "chat.complete"}                        │    │
│     │         → Stream to WebSocket                                    │    │
│     └─────────────────────────────────────────────────────────────────┘    │
│                                                                              │
│  6. PERSIST & FINALIZE                                                      │
│     ┌─────────────────────────────────────────────────────────────────┐    │
│     │ - Save conversation to chat_sessions collection                  │    │
│     │ - Track token usage                                              │    │
│     │ - Publish completion event                                       │    │
│     └─────────────────────────────────────────────────────────────────┘    │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Runtime Server Implementation

```python
# packages/runtime/src/mozaiks_runtime/server.py

from fastapi import FastAPI, Request, WebSocket, Depends
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager

from mozaiks_core.interfaces import RequestContext, EventBus
from .app.loader import AppLoader
from .router.dispatcher import RequestDispatcher
from .router.middleware import AuthMiddleware, ContextMiddleware
from .composition.executor_registry import ExecutorRegistry


@asynccontextmanager
async def lifespan(app: FastAPI):
    """App lifecycle management."""
    # Load app definition
    app_def = await AppLoader.load(".")

    # Initialize executors based on capabilities
    registry = ExecutorRegistry()

    if app_def.capabilities.modules:
        from mozaiks_modules import ModuleExecutor
        module_executor = ModuleExecutor(
            modules_path="./modules",
            config=app_def.database,
        )
        await module_executor.load_modules(app_def.modules)
        registry.register(module_executor)

    if app_def.capabilities.ai:
        from mozaiks_ai import WorkflowExecutor
        workflow_executor = WorkflowExecutor(
            workflows_path="./workflows",
            config=app_def.database,
        )
        await workflow_executor.load_workflows(app_def.workflows)
        registry.register(workflow_executor)

    # Create dispatcher
    dispatcher = RequestDispatcher(
        app_definition=app_def,
        executor_registry=registry,
    )

    # Store in app state
    app.state.app_definition = app_def
    app.state.dispatcher = dispatcher
    app.state.executor_registry = registry

    yield

    # Cleanup
    await registry.shutdown()


def create_app() -> FastAPI:
    app = FastAPI(lifespan=lifespan)

    # Middleware
    app.add_middleware(CORSMiddleware, allow_origins=["*"], ...)
    app.add_middleware(AuthMiddleware)
    app.add_middleware(ContextMiddleware)

    # Health check
    @app.get("/health")
    async def health():
        return {"status": "ok"}

    # Config endpoint
    @app.get("/api/config")
    async def get_config(request: Request):
        app_def = request.app.state.app_definition
        return {
            "name": app_def.name,
            "capabilities": app_def.capabilities.model_dump(),
            "features": app_def.features,
        }

    # Navigation endpoint
    @app.get("/api/navigation")
    async def get_navigation(request: Request):
        app_def = request.app.state.app_definition
        nav_items = [
            {
                "path": page.path,
                "label": page.nav.get("label", page.title) if page.nav else page.title,
                "icon": page.nav.get("icon") if page.nav else None,
                "order": page.nav.get("order", 99) if page.nav else 99,
            }
            for page in app_def.pages
            if page.nav
        ]
        return {"navigation": sorted(nav_items, key=lambda x: x["order"])}

    # Dynamic module routes
    @app.api_route(
        "/api/{module_name}/{action}",
        methods=["GET", "POST", "PUT", "DELETE"],
    )
    async def module_route(
        request: Request,
        module_name: str,
        action: str,
    ):
        context: RequestContext = request.state.context
        dispatcher: RequestDispatcher = request.app.state.dispatcher

        # Get request body if present
        params = {}
        if request.method in ["POST", "PUT"]:
            params = await request.json()
        params.update(dict(request.query_params))

        result = await dispatcher.dispatch_module(
            module_name=module_name,
            action=action,
            params=params,
            context=context,
        )

        if not result.success:
            raise HTTPException(400, result.error)

        return result.data

    # WebSocket for AI
    @app.websocket("/ws/chat/{session_id}")
    async def chat_websocket(
        websocket: WebSocket,
        session_id: str,
    ):
        # Auth
        context = await authenticate_websocket(websocket)
        if not context:
            await websocket.close(4001, "Unauthorized")
            return

        await websocket.accept()

        dispatcher: RequestDispatcher = websocket.app.state.dispatcher

        try:
            while True:
                data = await websocket.receive_json()

                if data.get("type") == "message":
                    async for event in dispatcher.dispatch_ai_stream(
                        workflow_name=data.get("workflow", "default"),
                        message=data.get("content"),
                        session_id=session_id,
                        context=context,
                    ):
                        await websocket.send_json(event)

        except WebSocketDisconnect:
            pass

    return app


# Entry point
app = create_app()
```

---

## 4. Execution Modes

### Mode A: AI-Only

**Use case:** Chatbot, AI assistant, workflow automation

**What runs:**
- `@mozaiks/core` (always)
- `@mozaiks/ai` (workflow execution)
- `@mozaiks/runtime` (server, auth, routing)

**What's disabled:**
- `@mozaiks/modules` (not loaded)
- Module routes return 404
- No module-based pages

**app.yaml:**
```yaml
name: my-assistant
capabilities:
  ai: true
  modules: false
  ui: true

workflows:
  - name: assistant

pages:
  - path: /
    title: Chat
    component: ChatPage
```

**Detection:**
```python
# packages/runtime/src/mozaiks_runtime/modes.py

def detect_mode(app_def: AppDefinition) -> str:
    has_ai = app_def.capabilities.ai and len(app_def.workflows) > 0
    has_modules = app_def.capabilities.modules and len(app_def.modules) > 0

    if has_ai and has_modules:
        return "full"
    elif has_ai:
        return "ai_only"
    elif has_modules:
        return "modules_only"
    else:
        return "static"  # Just serving pages
```

### Mode B: Modules-Only

**Use case:** CRUD app, admin panel, no AI

**What runs:**
- `@mozaiks/core`
- `@mozaiks/modules`
- `@mozaiks/runtime`

**What's disabled:**
- `@mozaiks/ai` (not loaded)
- No workflow routes
- No WebSocket chat

**app.yaml:**
```yaml
name: my-crm
capabilities:
  ai: false
  modules: true
  ui: true

modules:
  - name: contacts
  - name: deals
  - name: notes

pages:
  - path: /contacts
    module: contacts
```

### Mode C: Full System

**Use case:** AI-enhanced app with CRUD

**What runs:**
- Everything

**How they interact:**

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           FULL SYSTEM MODE                                   │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│                         ┌─────────────────────┐                             │
│                         │    App Runtime      │                             │
│                         │    (composition)    │                             │
│                         └──────────┬──────────┘                             │
│                                    │                                         │
│                    ┌───────────────┴───────────────┐                        │
│                    │                               │                        │
│                    ▼                               ▼                        │
│          ┌─────────────────┐             ┌─────────────────┐               │
│          │   AI Executor   │             │ Module Executor │               │
│          │                 │             │                 │               │
│          │  Workflows      │◄───────────►│  Modules        │               │
│          │  Agents         │   (tools)   │  CRUD           │               │
│          │  Tools          │             │  Storage        │               │
│          └─────────────────┘             └─────────────────┘               │
│                    │                               │                        │
│                    │         ┌─────────┐          │                        │
│                    └────────►│EventBus │◄─────────┘                        │
│                              └─────────┘                                    │
│                                                                              │
│  TOOL INVOCATION PATH:                                                      │
│                                                                              │
│  Agent wants data                                                           │
│       │                                                                     │
│       ▼                                                                     │
│  Agent calls tool: get_contacts                                             │
│       │                                                                     │
│       ▼                                                                     │
│  Tool implementation:                                                       │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │ async def get_contacts(context, limit: int = 10):                    │   │
│  │     # Option A: Direct module call (same process)                    │   │
│  │     module_executor = context.get_executor("modules")                │   │
│  │     result = await module_executor.execute(ExecutionRequest(         │   │
│  │         executor_type=ExecutorType.MODULE,                           │   │
│  │         target="contacts",                                           │   │
│  │         action="list",                                               │   │
│  │         params={"limit": limit},                                     │   │
│  │         app_id=context.app_id,                                       │   │
│  │         user_id=context.user_id,                                     │   │
│  │     ))                                                               │   │
│  │     return result.data                                               │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│       │                                                                     │
│       ▼                                                                     │
│  Module executes, returns data                                              │
│       │                                                                     │
│       ▼                                                                     │
│  Agent uses data in response                                                │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 5. Dependency Rules

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          DEPENDENCY GRAPH                                    │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│                              ┌─────────┐                                    │
│                              │   cli   │                                    │
│                              └────┬────┘                                    │
│                                   │                                         │
│                                   ▼                                         │
│                              ┌─────────┐                                    │
│                              │ runtime │                                    │
│                              └────┬────┘                                    │
│                                   │                                         │
│                    ┌──────────────┼──────────────┐                         │
│                    │              │              │                         │
│                    ▼              ▼              ▼                         │
│              ┌─────────┐    ┌─────────┐    ┌─────────┐                    │
│              │   ai    │    │   ui    │    │ modules │                    │
│              └────┬────┘    └────┬────┘    └────┬────┘                    │
│                   │              │              │                         │
│                   │              │              │                         │
│                   │    ┌─────────┴─────────┐   │                         │
│                   │    │                   │   │                         │
│                   └────┼───────────────────┼───┘                         │
│                        │                   │                              │
│                        ▼                   │                              │
│                   ┌─────────┐              │                              │
│                   │  core   │◄─────────────┘                              │
│                   └─────────┘                                             │
│                                                                              │
│  RULES:                                                                     │
│                                                                              │
│  ✅ ALLOWED:                                                                │
│     - cli → runtime                                                         │
│     - runtime → ai, modules, ui, core                                       │
│     - ai → core                                                             │
│     - modules → core                                                        │
│     - ui → core                                                             │
│                                                                              │
│  ❌ FORBIDDEN:                                                              │
│     - ai → modules                                                          │
│     - modules → ai                                                          │
│     - core → anything                                                       │
│     - ai → runtime                                                          │
│     - modules → runtime                                                     │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

### pyproject.toml Dependencies

```toml
# packages/core/pyproject.toml
[project]
name = "mozaiks-core"
dependencies = [
    "pydantic>=2.0",
    "pyyaml>=6.0",
    "motor>=3.0",      # Async MongoDB
]

# packages/ai/pyproject.toml
[project]
name = "mozaiks-ai"
dependencies = [
    "mozaiks-core",
    "ag2>=0.4",
    "openai>=1.0",
    "anthropic>=0.18",
]

# packages/modules/pyproject.toml
[project]
name = "mozaiks-modules"
dependencies = [
    "mozaiks-core",
]

# packages/ui/pyproject.toml
[project]
name = "mozaiks-ui"
dependencies = [
    "mozaiks-core",
    "jinja2>=3.0",
]

# packages/runtime/pyproject.toml
[project]
name = "mozaiks-runtime"
dependencies = [
    "mozaiks-core",
    "fastapi>=0.109",
    "uvicorn>=0.27",
]

[project.optional-dependencies]
ai = ["mozaiks-ai"]
modules = ["mozaiks-modules"]
ui = ["mozaiks-ui"]
full = ["mozaiks-ai", "mozaiks-modules", "mozaiks-ui"]

# packages/cli/pyproject.toml
[project]
name = "mozaiks-cli"
dependencies = [
    "mozaiks-runtime[full]",
    "typer>=0.9",
    "rich>=13.0",
]
```

---

## 6. Migration Plan

### Current State

```
CURRENT:
├── mozaiks/                      # AI runtime (working)
│   ├── mozaiksai/               # Main package
│   ├── mozaiks_cli/             # CLI
│   ├── platform/                # Example workflows
│   └── shared_app.py            # Server
│
└── mozaiks-core-public/          # Plugin runtime (working)
    ├── backend/                  # FastAPI server
    ├── platform/plugins/         # Example plugins
    └── src/                      # React frontend
```

### Target State

```
TARGET:
└── mozaiks/                      # Unified repo
    ├── packages/
    │   ├── core/                # NEW: extracted shared code
    │   ├── ai/                  # FROM: mozaiks/mozaiksai
    │   ├── modules/             # FROM: mozaiks-core-public/backend
    │   ├── ui/                  # FROM: mozaiks-core-public/src + new
    │   ├── runtime/             # NEW: composition layer
    │   └── cli/                 # FROM: mozaiks/mozaiks_cli
    ├── templates/               # App templates
    └── examples/
        ├── ai-only/             # Example: chatbot
        ├── modules-only/        # Example: CRUD app
        └── full/                # Example: AI + modules
```

### Migration Steps

#### Phase 1: Extract Core (Week 1)

```
Tasks:
□ Create packages/core/ structure
□ Extract from mozaiks:
  - Event envelope schema
  - JWT validation logic
  - MongoDB connection utilities
  - Config loading
□ Extract from mozaiks-core-public:
  - Request context
  - User principal
  - Storage abstractions
□ Define interfaces (EventBus, Executor, etc.)
□ Write tests
□ Verify both runtimes can use core
```

#### Phase 2: Repackage AI (Week 2)

```
Tasks:
□ Create packages/ai/ structure
□ Move mozaiks/mozaiksai → packages/ai/src/mozaiks_ai
□ Update imports to use mozaiks_core
□ Extract server into packages/ai/src/mozaiks_ai/server.py
□ Implement WorkflowExecutor interface
□ Add standalone entry point
□ Write tests
□ Verify standalone mode works
```

#### Phase 3: Repackage Modules (Week 2)

```
Tasks:
□ Create packages/modules/ structure
□ Move mozaiks-core-public/backend → packages/modules/src/mozaiks_modules
□ Update imports to use mozaiks_core
□ Extract server into standalone file
□ Implement ModuleExecutor interface
□ Add standalone entry point
□ Write tests
□ Verify standalone mode works
```

#### Phase 4: Create Runtime (Week 3)

```
Tasks:
□ Create packages/runtime/ structure
□ Implement AppDefinition schema
□ Implement AppLoader
□ Implement RequestDispatcher
□ Implement ExecutorRegistry
□ Implement middleware chain
□ Create unified server
□ Write integration tests
□ Verify all three modes work
```

#### Phase 5: Create UI Package (Week 3)

```
Tasks:
□ Create packages/ui/ structure
□ Move mozaiks-core-public/src → packages/ui/frontend
□ Create server-side rendering option
□ Implement page schema
□ Implement navigation builder
□ Write tests
```

#### Phase 6: Update CLI (Week 4)

```
Tasks:
□ Create packages/cli/ structure
□ Update mozaiks new command for new structure
□ Add mode selection (ai-only, modules-only, full)
□ Create templates/
□ Update mozaiks dev command
□ Write tests
```

#### Phase 7: Examples & Documentation (Week 4)

```
Tasks:
□ Create examples/ai-only/
□ Create examples/modules-only/
□ Create examples/full/
□ Update README.md
□ Write migration guide
□ Update AGENTS.md
```

### What Stays Unchanged

| Component | Status |
|-----------|--------|
| AG2 workflow execution logic | KEEP |
| Workflow YAML format | KEEP |
| Module execute() function signature | KEEP |
| Plugin folder structure | KEEP |
| WebSocket protocol | KEEP |
| JWT validation logic | KEEP (move to core) |
| MongoDB storage patterns | KEEP |

### What Needs Refactoring

| Component | Change |
|-----------|--------|
| Event bus | Implement shared interface |
| Auth middleware | Standardize across packages |
| Server entry points | Create standalone + composable versions |
| Config loading | Standardize in core |
| Context injection | Implement ContextProvider |

---

## 7. What NOT to Build Yet

### Delay for v2:

| Feature | Why Delay |
|---------|-----------|
| Platform event routing | Complex, needs more design |
| Admin dashboard | Can use separate tooling for now |
| Self-service billing | Integrate when monetizing |
| Multi-tenant routing | Start with single-tenant |
| Schema-driven UI | Code-based UI is simpler |
| GraphQL API | REST is sufficient |
| Distributed event bus | In-process is fine for now |

### Over-engineering to Avoid:

| Trap | Why Avoid |
|------|-----------|
| Microservice split | Monolith is fine for this scale |
| Event sourcing | Simple CRUD is sufficient |
| Complex DI framework | Simple factory pattern works |
| Plugin marketplace | Focus on core first |
| Custom query language | MongoDB queries are sufficient |

### Unnecessary for v1:

| Feature | Why Skip |
|---------|----------|
| Hot module reload | Dev server restart is fast enough |
| Distributed tracing | Logs are sufficient |
| Rate limiting | Add when needed |
| API versioning | /api/ is fine for now |
| WebSocket reconnection | Client can handle |

---

## 8. Biggest Risks

### Risk 1: Tool-to-Module Communication

**Risk:** AI tools need to call modules. How?

**Options:**
1. Direct call (same process) - simplest, but couples at runtime
2. HTTP call (separate process) - decoupled, but adds latency
3. Event-based (async) - fully decoupled, but complex

**Mitigation:** Start with Option 1 (direct call) in full mode. Runtime injects module executor into tool context. Can add Option 2 later if needed.

```python
# In tool implementation
async def get_contacts(context, limit: int = 10):
    # Runtime provides this
    module_executor = context.executors.get("modules")
    if module_executor:
        result = await module_executor.execute(...)
        return result.data
    else:
        # Fallback: HTTP call
        return await http_client.get(f"{MODULES_URL}/api/contacts")
```

### Risk 2: Auth Token Sharing

**Risk:** Different systems need same user identity.

**Mitigation:** Standardize JWT validation in core. All packages use same validation logic. Token is passed through context.

```python
# All packages use this
from mozaiks_core.auth import validate_jwt, UserPrincipal

user = validate_jwt(token)  # Same logic everywhere
```

### Risk 3: Event Schema Drift

**Risk:** AI and modules emit different event formats.

**Mitigation:** Define Event schema in core. All packages MUST use it.

```python
# WRONG - don't do this
event_bus.publish("note_created", {"note_id": "123"})

# RIGHT - use standard envelope
event_bus.publish(Event.create(
    event_type="module.notes.created",
    source="modules",
    app_id=context.app_id,
    payload={"note_id": "123"},
))
```

### Risk 4: Breaking Changes During Migration

**Risk:** Moving code breaks existing deployments.

**Mitigation:**
- Keep package names stable (mozaiks-ai, not mozaiksai)
- Add deprecation warnings before removing
- Maintain backwards compatibility for 2 versions
- Document migration path

### Risk 5: Complexity Creep

**Risk:** Architecture becomes over-engineered.

**Mitigation:**
- Follow YAGNI strictly
- Review every new abstraction
- Start simple, add complexity only when needed
- Keep packages thin
- Measure actual usage before optimizing

---

## 9. Summary

### What We're Building

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                                                                              │
│   ┌─────────────────────────────────────────────────────────────────────┐   │
│   │                         APP RUNTIME                                  │   │
│   │                                                                      │   │
│   │   Load app.yaml → Route requests → Compose executors → Serve        │   │
│   └─────────────────────────────────────────────────────────────────────┘   │
│                                    │                                         │
│              ┌─────────────────────┼─────────────────────┐                  │
│              │                     │                     │                  │
│              ▼                     ▼                     ▼                  │
│   ┌─────────────────┐   ┌─────────────────┐   ┌─────────────────┐         │
│   │       AI        │   │       UI        │   │    MODULES      │         │
│   │                 │   │                 │   │                 │         │
│   │  Workflows      │   │  Pages          │   │  CRUD           │         │
│   │  Agents         │   │  Components     │   │  Logic          │         │
│   │  Tools          │   │  Navigation     │   │  Storage        │         │
│   │                 │   │                 │   │                 │         │
│   │  CAN RUN ALONE  │   │  CAN RUN ALONE  │   │  CAN RUN ALONE  │         │
│   └────────┬────────┘   └────────┬────────┘   └────────┬────────┘         │
│            │                     │                     │                  │
│            └─────────────────────┼─────────────────────┘                  │
│                                  │                                         │
│                                  ▼                                         │
│                         ┌─────────────────┐                                │
│                         │      CORE       │                                │
│                         │                 │                                │
│                         │  Interfaces     │                                │
│                         │  Events         │                                │
│                         │  Auth           │                                │
│                         │  Config         │                                │
│                         └─────────────────┘                                │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Key Decisions

1. **NO bridge layer** - Runtime orchestrates, doesn't translate
2. **Optional dependencies** - Each package works alone
3. **Shared interfaces** - Core defines, others implement
4. **app.yaml as contract** - Single definition for entire app
5. **Three modes** - AI-only, modules-only, full
6. **Direct tool calls** - Simpler than event-based for v1

### Next Steps

1. Review this design with team
2. Approve or modify dependency rules
3. Start Phase 1 (extract core)
4. Build incrementally, test continuously
