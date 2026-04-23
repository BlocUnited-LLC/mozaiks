# Mozaiks Platform: .NET to Python Migration Plan

**Status:** Historical / superseded.

The current canonical architecture lives in `ARCHITECTURE.md`. The hosted
product is now modeled as `mozaiks_app.py` on top of `platform_app.py`, with App
Zero's active app root at `mozaiks-platform/app/`. This document is retained as
legacy planning context only.

## Executive Summary

Migrate `mozaiks-platform` from .NET microservices to Python/FastAPI to:
1. Unify the stack with `mozaiksai` (AI runtime)
2. Simplify integration between platform and runtime
3. Enable code sharing and consistent patterns

## Current State Analysis

### .NET Services Inventory

| Service | Purpose | Complexity | Priority |
|---------|---------|------------|----------|
| **Apps** | App CRUD, users, invitations | Medium | P0 |
| **Hosting** | App hosting, provisioning, domains | High | P0 |
| **Admin** | Admin panel backend | Medium | P1 |
| **Teams** | Team management | Low | P1 |
| **Messaging** | Communication service | Medium | P2 |
| **Notification** | Push notifications | Low | P2 |
| **Discovery** | App marketplace | Medium | P2 |
| **Governance** | Platform governance | Low | P3 |
| **Growth** | Ad engine, analytics | Medium | P3 |
| **Monetization** | Billing/subscriptions | High | P1 |
| **Payment** | Payment processing | High | P1 |
| **Provisioning** | Resource provisioning | High | P0 |

### Building Blocks (Shared Infrastructure)

| Block | Purpose | Python Equivalent |
|-------|---------|-------------------|
| EventBus.Messages | MassTransit events | aio-pika + custom events |
| Mozaiks.Auth | JWT utilities | python-jose + FastAPI deps |
| Mozaiks.Auditing | Audit logging | structlog + MongoDB |

### Technology Mapping

| .NET | Python Equivalent |
|------|-------------------|
| ASP.NET Core | FastAPI |
| MassTransit | aio-pika (RabbitMQ) or Celery |
| MongoDB.Driver | Motor (async) / PyMongo |
| Npgsql | asyncpg |
| Azure.Storage.Blobs | azure-storage-blob |
| JWT Bearer | python-jose + FastAPI |
| Swagger | FastAPI built-in OpenAPI |

## Target Architecture

### Unified Monorepo Structure

```
mozaiks-platform/
├── platform/                    # Python platform services
│   ├── __init__.py
│   ├── main.py                  # FastAPI app entry point
│   ├── config.py                # Configuration management
│   │
│   ├── core/                    # Shared infrastructure (Building Blocks)
│   │   ├── auth/                # JWT, user context
│   │   ├── events/              # Event bus (RabbitMQ)
│   │   ├── audit/               # Audit logging
│   │   ├── storage/             # Azure Blob, file storage
│   │   └── database/            # MongoDB, Postgres connections
│   │
│   ├── services/                # Domain services
│   │   ├── apps/                # App management
│   │   │   ├── router.py
│   │   │   ├── models.py
│   │   │   ├── repository.py
│   │   │   └── service.py
│   │   │
│   │   ├── hosting/             # Hosting & provisioning
│   │   │   ├── router.py
│   │   │   ├── models.py
│   │   │   ├── repository.py
│   │   │   ├── service.py
│   │   │   └── workers/         # Background jobs
│   │   │       ├── provisioning_worker.py
│   │   │       └── domain_renewal_worker.py
│   │   │
│   │   ├── teams/
│   │   ├── monetization/
│   │   ├── payments/
│   │   └── ...
│   │
│   └── api/                     # API routers (aggregation)
│       ├── v1/
│       │   ├── __init__.py
│       │   └── router.py        # Mounts all service routers
│       └── internal/            # Internal service-to-service APIs
│
├── app/                         # Platform workflows (stays same)
│   └── workflows/
│       ├── ValueEngine/
│       ├── AgentGenerator/
│       └── AppGenerator/
│
├── tests/
├── requirements.txt
├── pyproject.toml
└── Dockerfile
```

### Service Pattern (Python)

Each service follows a consistent pattern:

```python
# platform/services/apps/models.py
from pydantic import BaseModel, Field
from datetime import datetime
from typing import Optional, List

class App(BaseModel):
    id: str = Field(alias="_id")
    name: str
    owner_id: str
    created_at: datetime
    settings: Optional["AppSettings"] = None
    members: List["AppMember"] = []

class AppCreate(BaseModel):
    name: str

class AppUpdate(BaseModel):
    name: Optional[str] = None
```

```python
# platform/services/apps/repository.py
from motor.motor_asyncio import AsyncIOMotorCollection
from .models import App, AppCreate

class AppRepository:
    def __init__(self, collection: AsyncIOMotorCollection):
        self.collection = collection

    async def get_by_id(self, app_id: str) -> App | None:
        doc = await self.collection.find_one({"_id": app_id})
        return App(**doc) if doc else None

    async def create(self, owner_id: str, data: AppCreate) -> App:
        doc = {
            "name": data.name,
            "owner_id": owner_id,
            "created_at": datetime.utcnow(),
        }
        result = await self.collection.insert_one(doc)
        doc["_id"] = str(result.inserted_id)
        return App(**doc)
```

```python
# platform/services/apps/router.py
from fastapi import APIRouter, Depends, HTTPException
from .models import App, AppCreate
from .repository import AppRepository
from platform.core.auth import get_current_user

router = APIRouter(prefix="/apps", tags=["apps"])

@router.get("/{app_id}")
async def get_app(
    app_id: str,
    repo: AppRepository = Depends(),
    user = Depends(get_current_user)
) -> App:
    app = await repo.get_by_id(app_id)
    if not app:
        raise HTTPException(404, "App not found")
    return app

@router.post("/")
async def create_app(
    data: AppCreate,
    repo: AppRepository = Depends(),
    user = Depends(get_current_user)
) -> App:
    return await repo.create(user.id, data)
```

### Event Bus (Python)

```python
# platform/core/events/bus.py
import aio_pika
from pydantic import BaseModel
from typing import Type, Callable

class EventBus:
    def __init__(self, connection: aio_pika.Connection):
        self.connection = connection
        self._handlers: dict[str, list[Callable]] = {}

    async def publish(self, event: BaseModel):
        channel = await self.connection.channel()
        exchange = await channel.declare_exchange("platform_events", aio_pika.ExchangeType.TOPIC)

        message = aio_pika.Message(
            body=event.model_dump_json().encode(),
            content_type="application/json"
        )
        await exchange.publish(message, routing_key=type(event).__name__)

    def subscribe(self, event_type: Type[BaseModel]):
        def decorator(handler: Callable):
            self._handlers.setdefault(event_type.__name__, []).append(handler)
            return handler
        return decorator

# platform/core/events/messages.py
from pydantic import BaseModel
from datetime import datetime

class ProvisioningRequested(BaseModel):
    app_id: str
    user_id: str
    requested_at: datetime

class AppCreated(BaseModel):
    app_id: str
    name: str
    owner_id: str
```

## Migration Phases

### Phase 0: Foundation (Week 1-2)

**Goal:** Set up Python platform skeleton with shared infrastructure.

```
Tasks:
□ Create platform/ directory structure
□ Set up FastAPI application shell
□ Implement core/auth (JWT, user context)
□ Implement core/database (MongoDB, Postgres connections)
□ Implement core/events (Event bus skeleton)
□ Set up Docker development environment
□ Create basic CI/CD pipeline
```

**Deliverable:** Empty platform that boots and authenticates.

### Phase 1: Apps Service (Week 2-3)

**Goal:** Migrate Apps service - the simplest but most critical.

```
Tasks:
□ Migrate App model and repository
□ Migrate User model and repository
□ Migrate AppInvitation model and repository
□ Implement CRUD endpoints
□ Test against existing MongoDB data
□ Update MOZ-UI to call new endpoints
```

**Deliverable:** Apps API fully functional in Python.

### Phase 2: Hosting Service (Week 3-5)

**Goal:** Migrate the complex Hosting service.

```
Tasks:
□ Migrate HostedApp model and repository
□ Migrate ProvisioningJob model and repository
□ Migrate DomainOrder model and repository
□ Implement provisioning worker (background job)
□ Implement domain renewal worker
□ Migrate Azure Blob artifact store
□ Test provisioning flow end-to-end
```

**Deliverable:** Hosting API and workers functional.

### Phase 3: Monetization & Payments (Week 5-7)

**Goal:** Migrate billing-critical services.

```
Tasks:
□ Migrate subscription models
□ Migrate payment processing
□ Integrate with Stripe/payment provider
□ Test billing flows
```

**Deliverable:** Monetization working.

### Phase 4: Remaining Services (Week 7-10)

```
□ Teams service
□ Messaging service
□ Notification service
□ Discovery service
□ Admin service
□ Governance service
□ Growth service
```

### Phase 5: Integration & Cleanup (Week 10-12)

```
□ Remove .NET services
□ Update all infrastructure (Docker, K8s)
□ Performance testing
□ Documentation update
```

## Integration with mozaiksai

The key benefit of Python migration is direct integration with the AI runtime.

### Option A: Shared Process (Recommended for MVP)

Run `mozaiksai` and `platform` in the same FastAPI process:

```python
# mozaiks-platform/main.py
from fastapi import FastAPI
from platform.api.v1 import router as platform_router
from mozaiksai.core.transport.websocket import router as ai_router

app = FastAPI(title="Mozaiks Platform")

# Platform APIs
app.include_router(platform_router, prefix="/api/v1")

# AI Runtime APIs
app.include_router(ai_router, prefix="/ai")
```

### Option B: Separate Services with SDK

Keep them separate but with a Python SDK for integration:

```python
# In platform code
from mozaiksai.client import MozaiksClient

client = MozaiksClient(url="http://mozaiksai:8000")

# Start a workflow for app generation
session = await client.start_workflow(
    workflow="AppGenerator",
    context={
        "app_id": app.id,
        "user_id": user.id,
        "concept_overview": app.concept
    }
)
```

## Risks & Mitigations

| Risk | Mitigation |
|------|------------|
| Data migration issues | Test against production data clone |
| Performance regression | Benchmark critical paths before/after |
| Feature parity gaps | Create detailed feature checklist |
| Team learning curve | Pair Python experts with .NET developers |
| Downtime during cutover | Use feature flags for gradual rollout |

## Decision Points

1. **Monolith vs Microservices?**
   - Recommendation: Start with modular monolith, split later if needed
   - Python FastAPI handles high load well
   - Simpler deployment and debugging

2. **Event bus technology?**
   - Option A: aio-pika (RabbitMQ) - closest to MassTransit
   - Option B: Redis Streams - simpler, good enough
   - Option C: Celery - if heavy background jobs needed

3. **Database strategy?**
   - Keep MongoDB (Motor driver)
   - Keep Postgres for relational needs (asyncpg)
   - Same schema, just different drivers

## Next Steps

1. [ ] Review this plan with the team
2. [ ] Set up Python platform skeleton in mozaiks-platform repo
3. [ ] Create migration feature checklist for Apps service
4. [ ] Start Phase 0 implementation
