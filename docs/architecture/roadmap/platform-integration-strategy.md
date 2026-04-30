# Mozaiks Platform Integration Strategy

**Status:** Historical / superseded.

This document predates the canonical layered host architecture. The current
source of truth is `ARCHITECTURE.md`: `mozaiksai/hosts/runtime.py`, `mozaiksai/hosts/platform.py`,
`mozaiksai/hosts/studio.py`, and `mozaiksai/hosts/mozaiks.py`, with App Zero rooted at
`mozaiks-platform/app/`. Treat references to separate .NET platform services as
legacy planning context.

Legacy filesystem examples that appear below, including `platform/workflows/`,
are preserved only to explain the superseded integration model.

## The Core Question

How do different types of users/customers integrate with Mozaiks AI capabilities?

## User Segments

### 1. Mozaiks (Internal - Dogfooding)
**Who:** mozaiks-platform, MOZ-UI - your own product
**Stack:** .NET microservices + React
**Goal:** Use mozaiksai to power AppGenerator, ValueEngine, etc.

### 2. Enterprise Customers with Existing Apps
**Who:** Companies with existing codebases (any language)
**Stack:** Anything - .NET, Java, Python, Node, etc.
**Goal:** Add AI capabilities to existing apps without rewriting

### 3. New Mozaiks-First Apps
**Who:** Developers starting fresh with Mozaiks
**Stack:** Whatever they want for app, mozaiksai for AI
**Goal:** Build AI-native apps from scratch

### 4. OSS Community
**Who:** Developers exploring, learning, contributing
**Stack:** Varies
**Goal:** Try it out, build modules, contribute workflows

---

## Deployment Models

### Option A: Hosted Runtime (SaaS)
```
Customer App (.NET, Java, etc.)
        │
        │ HTTP/WebSocket (SDK)
        ▼
┌─────────────────────────┐
│   Mozaiks Cloud         │
│   (ai.mozaiks.io)       │
│                         │
│   - Shared runtime      │
│   - Multi-tenant        │
│   - Managed workflows   │
│   - Pay per use         │
└─────────────────────────┘
```

**Pros:**
- Zero infrastructure for customers
- Automatic updates
- Managed security
- Pay-as-you-go

**Cons:**
- Data leaves customer's environment
- Vendor lock-in concerns
- Latency for some use cases

**Best for:** SMBs, quick starts, customers who don't want to manage infra

### Option B: Self-Hosted Runtime
```
Customer Infrastructure
┌─────────────────────────────────────────┐
│                                         │
│   Customer App        mozaiksai         │
│   (.NET, etc.)  ───►  (their copy)      │
│                       │                 │
│                       ▼                 │
│               platform/workflows/       │
│               (their workflows)         │
│                                         │
└─────────────────────────────────────────┘
```

**Pros:**
- Data stays in customer's environment
- Full control
- Can customize runtime
- No per-call costs

**Cons:**
- Customer manages infra
- They handle updates
- More complex setup

**Best for:** Enterprises with data sensitivity, large-scale usage

### Option C: Hybrid (Most Likely for Mozaiks Platform)
```
┌─────────────────────────────────────────────────────┐
│ mozaiks-platform (Azure)                            │
│                                                     │
│  ┌──────────────┐     ┌──────────────────────────┐  │
│  │ .NET Services│     │ mozaiksai runtime        │  │
│  │              │────►│                          │  │
│  │ - Hosting    │ SDK │ platform/workflows/      │  │
│  │ - Apps       │     │ - AppGenerator           │  │
│  │ - Payments   │     │ - ValueEngine            │  │
│  │              │     │ - etc.                   │  │
│  └──────────────┘     └──────────────────────────┘  │
│                                                     │
└─────────────────────────────────────────────────────┘
```

**This is what YOU (Mozaiks) should do:**
- Run mozaiksai alongside your .NET services
- .NET services call mozaiksai via internal network (fast, secure)
- Share same K8s cluster / Azure resource group
- You own both, deploy together

---

## Integration Patterns by Segment

### 1. Mozaiks Platform (Dogfooding)

**Current state:**
- mozaiks-platform: .NET microservices (Hosting, Apps, etc.)
- mozaiksai: Python FastAPI (AI runtime)
- MOZ-UI: React (frontend)

**Integration:**
```
MOZ-UI (React)
    │
    ├──► mozaiks-platform APIs (CRUD, payments, etc.)
    │
    └──► mozaiksai APIs (AI workflows)
            │
            └── Uses Mozaiks.Sdk internally for .NET→Python calls
```

**What we built today:**
- `Mozaiks.Sdk` - .NET SDK for calling mozaiksai
- `MozaiksAiService` in Hosting.API - wrapper for common operations
- Trigger endpoint for backend-to-backend workflow invocation

**Dogfooding scenario:**
1. User creates app in MOZ-UI
2. MOZ-UI calls mozaiks-platform to create app record
3. MOZ-UI connects to mozaiksai WebSocket for AppGenerator workflow
4. OR: Hosting.API triggers AppGenerator workflow server-side
5. Workflow runs, generates artifacts
6. Artifacts stored via mozaiks-platform (Azure Blob)
7. App provisioned by Hosting.API

### 2. Enterprise Customers (Existing Apps)

**Their options:**

**A. Use Hosted Runtime (Recommended for most)**
```csharp
// Their .NET app
var mozaiks = new MozaiksClient("https://ai.mozaiks.io", "their-api-key");

// Start a workflow
var session = await mozaiks.StartChatAsync(
    appId: "their-app-id",
    workflowName: "CustomerSupport",  // Custom workflow they defined
    userId: user.Id,
    context: new Dictionary<string, object>
    {
        ["customer_data"] = customerRecord
    }
);
```

**B. Self-Host Runtime**
- Clone mozaiksai
- Deploy to their infra
- Add their workflows to `platform/workflows/`
- Their app calls their runtime instance

**C. SDK Only (Headless)**
- For backend automation (no chat UI)
- Trigger workflows, get results via webhook
```csharp
var result = await mozaiks.TriggerWorkflowAsync(
    workflowName: "DataProcessor",
    userId: "system",
    context: data,
    webhookUrl: "https://their-app.com/webhook/complete"
);
```

### 3. New Mozaiks-First Apps

**Recommended approach:**
```
mozaiks create my-app --template=starter
```

**Generates:**
```
my-app/
├── app/                    # Their app code (Node/Python/.NET)
│   └── ...
├── platform/
│   └── workflows/          # Declarative AI workflows
│       └── MyWorkflow/
│           ├── orchestrator.yaml
│           ├── agents.yaml
│           ├── tools.yaml
│           └── tools/
│               └── my_tool.py
├── docker-compose.yml      # Runs mozaiksai + their app
└── README.md
```

**They build:**
- Workflows (YAML + Python tools)
- App code in any language
- UI components for artifacts

**Runtime provided by:**
- Local: `docker-compose up` (includes mozaiksai)
- Production: Deploy to Mozaiks Cloud or self-host

### 4. OSS Community

**CLI for getting started:**
```bash
# Install
pip install mozaiks-cli

# Create new project
mozaiks init my-project
mozaiks workflow create CustomerSupport

# Run locally
mozaiks dev

# Deploy (to self-hosted or cloud)
mozaiks deploy
```

**What they get:**
- Templates for common patterns
- Local development with hot-reload
- Documentation and examples
- Community workflows library

---

## Key Decisions to Make

### 1. Multi-tenancy Model

**Question:** Does each customer get their own runtime, or share?

**Recommendation:** Hybrid
- **Shared runtime** for hosted/SaaS (multi-tenant)
- **Dedicated runtime** for enterprise self-hosted
- **Local runtime** for development

**Multi-tenant isolation:**
- Workflows scoped by `app_id`
- Context variables isolated per session
- MongoDB collections scoped by tenant

### 2. Workflow Distribution

**Question:** How do customers get/deploy workflows?

**Options:**
a) **Git-based**: Customers fork/clone, add their workflows
b) **Registry-based**: Push workflows to a registry, pull to runtime
c) **Inline definition**: Define workflows via API
d) **Marketplace**: Browse and install pre-built workflows

**Recommendation for MVP:** Git-based for self-hosted, API-based for hosted

### 3. Runtime Versioning

**Question:** How do customers manage runtime versions?

**For hosted:**
- Mozaiks manages versions
- Customers opt into upgrades
- Breaking changes announced in advance

**For self-hosted:**
- Semantic versioning
- Docker tags for versions
- Migration guides for breaking changes

---

## Immediate Next Steps (Dogfooding Focus)

### Week 1-2: Internal Integration
1. [ ] Deploy mozaiksai alongside mozaiks-platform services
2. [ ] Configure internal networking (service mesh or K8s service)
3. [ ] Add `MOZAIKS_RUNTIME_URL` config to Hosting.API
4. [ ] Test MozaiksAiService health check from Hosting.API

### Week 3-4: First Workflow Integration
1. [ ] Identify first workflow to integrate (AppGenerator?)
2. [ ] Add trigger from Hosting.API to mozaiksai
3. [ ] Handle webhook callback for completion
4. [ ] Store artifacts in existing Azure Blob via Hosting.API

### Week 5-6: Full Loop
1. [ ] MOZ-UI connects directly to mozaiksai WebSocket
2. [ ] Test complete user journey: create app → run workflow → see results
3. [ ] Measure latency, identify bottlenecks
4. [ ] Document learnings for customer-facing version

---

## Summary

| Segment | Runtime | Integration | Workflows |
|---------|---------|-------------|-----------|
| **Mozaiks (You)** | Self-hosted (alongside .NET) | SDK + Direct WS | Your own |
| **Enterprise** | Self-hosted or Hosted | SDK | Their own + marketplace |
| **New Apps** | Hosted or Local | Built-in | Their own |
| **OSS** | Local (Docker) | CLI + SDK | Community |

**The .NET SDK we built today** is specifically for Segment 1 & 2 - existing apps that want to call mozaiksai from a different tech stack.

**Mozaiks eating its own dogfood** = mozaiks-platform using mozaiksai via SDK. This proves the integration pattern works for any enterprise customer with existing apps.
