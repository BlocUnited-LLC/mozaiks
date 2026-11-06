# Core, Product, and App Bundle Boundary

This document defines the ownership boundary between the main layers in
Mozaiks.

The rewrite adds one boundary that must stay explicit:

- app backend behavior
- AI workflow behavior

## The Five Ownership Zones

### 1. App backend core

Owns generic non-AI application capabilities:

- entities, views, actions, and policies at runtime
- module hosting
- settings, notifications, subscriptions
- app-facing APIs
- post-commit domain event emission

The design target here is broad coverage of recurring SaaS infrastructure
concerns, not bespoke application logic.

Current implementation zone:

- `mozaiksai/hosts/platform.py` and app bundle modules in this repo
- optional external/generated app backends connected through `AppBackendPort`

Current framework contract in this repo:

- `mozaiksai/core/ports/app_backend.py`
- `mozaiksai/core/adapters/http_app_backend.py`

The HTTP adapter owns transport mechanics for optional external backends. Apps
can configure private transport without copying an HTTP client:

```python
backend = HttpAppBackendAdapter(
    base_url=backend_url,
    internal_api_key=resolved_key,
    timeout=15.0,
    connect_timeout=3.0,
    trust_env=False,
    strict_json=True,
    circuit_breaker_enabled=False,
)
```

`trust_env` defaults to `True`, retaining HTTPX environment proxy/certificate
configuration for existing consumers. Setting it to `False` bypasses both
proxies and certificate environment settings such as `SSL_CERT_FILE` and
`SSL_CERT_DIR`; it is not a proxy-only switch. Redirects are never followed.
`timeout` controls response, write and pool
timeouts; `connect_timeout` controls connection establishment. Their defaults
remain 30 and 10 seconds respectively. An omitted/`None` internal key uses
`INTERNAL_API_KEY`; an explicit empty string sends no default key. Additional
headers and user tokens apply only to the current request.

Circuit protection defaults to enabled. Its state belongs to the normalized
configured base URL, including its path prefix. Separate upstreams do not share
failure counts; adapters for the same
upstream share protection across request paths and credentials. Registry names
hash that identity so circuit logs do not contain the URL. The process-wide
registry has no eviction: use a fixed set of configured upstream URLs, not URLs
supplied per request or per tenant. Set `circuit_breaker_enabled=False` when the
caller needs each request attempted independently. This bypasses registry
access and existing open circuits, including circuits for the same upstream;
it does not reset their state. Neither mode retries any request. With protection
enabled, transport errors and 5xx responses count toward opening the circuit;
2xx (including 207), 3xx/4xx, and strict JSON parse failures do not.
A received 2xx counts as transport success even when strict JSON parsing fails,
so it can close a half-open circuit. The circuit does not validate response
syntax or the app's response contract.

`BackendResponse.data` can hold any JSON value, including arrays, scalars and
null. `strict_json` defaults to `False`: unparsable 2xx responses still succeed
with `data={"raw": ...}`. With `strict_json=True`, malformed, excessively nested,
empty or non-JSON 2xx bodies fail with the actual HTTP status,
`error="backend_invalid_response"` and `data=None`. Non-JSON numeric constants
such as `NaN` and `Infinity` are rejected in this mode. An actually empty 204
is the exception: it succeeds with `data=None`. Empty 200/201 responses fail;
a valid JSON object containing a `raw` field remains ordinary successful data.
A failed parse does not mean the server rejected or rolled back the request:
a 2xx response, including 202, may follow acceptance or execution of a mutation.
Do not automatically retry a mutation because its response failed strict parsing;
reconcile its outcome through the app's operation contract.

All returned HTTP failures preserve their actual status, including 5xx.
Transport failures without an HTTP response and open-circuit rejections have
status `0`. Network/5xx failures use `error="backend_unavailable"`; open circuits
use `error="backend_circuit_open"`. Non-5xx HTTP errors still include up to 500
response-text characters in `BackendResponse.error`. Transport failures are
logged by category, without raw exception text, request paths or bodies.

App-owned clients still validate their allowed origins/actions, trusted caller
context and response contracts. Strict JSON parsing proves syntax, not a
backend-specific schema or authority. A private client must validate successful
data and map errors to safe product errors before exposing them.
Transport reuse grants no authority and does not replace those app boundaries.

### 2. AI runtime core

Owns generic workflow and orchestration capabilities:

- workflow loading
- run lifecycle
- engine adapters
- stream transport
- artifacts
- UI tool round-trips
- orchestration and resume semantics

Current implementation zone:

- `mozaiksai/`

### 3. Execution engine

Owns agent-native semantics.

AG2 should continue to own native concepts such as:

- handoffs
- AG2 1.0 workflow progression
- reply hooks
- tool calling

Mozaiks should wrap around AG2 where the gap is real, not duplicate AG2 inside
core.

### 4. First-party product

Owns product-specific behavior such as:

- app building
- provisioning
- monetization
- hosted operations
- templates and guided creation flows

The first-party builder is a consumer of the platform, not the platform.

People build app bundles on Mozaiks.

They do not build app logic inside the hosted Refinement Engine.

Its main job is to turn intent into:

- a `ProductSpec` the user can approve
- an `ExistingProductSpec` when the request starts from a live host product
- `CapabilitySpec[]` describing deterministic product capabilities
- an `ExperienceSpec` for persistent app UI
- an `AgentAugmentationPlan` for AI behavior
- a `BuildGraph` for concrete compilation and validation
- thin stubs on top of core where core coverage already exists

Hosted product responsibilities also include:

- hosted Refinement Engine and tenant operations
- environment provisioning
- domains, TLS, secrets, and monitoring
- billing and monetized platform operations

### 5. App bundle

Owns application-specific declaratives:

- app-backend model
- modules
- workflow triggers
- workflows
- shell composition

The app bundle is input to the platform host and runtime. It is not runtime
internals.

## The Boundary Rule That Matters Most

The app backend may emit domain events.

The app backend must not emit workflow names or directly encode AG2 workflow-run
meaning.

`mozaiksai` owns:

- event validation
- automation routing
- workflow route selection
- run and resume execution

This is the boundary that prevents the non-AI runtime from becoming a hidden
workflow engine.

## What Belongs in the App Backend

A feature belongs in the app backend if it is primarily about:

- durable business state
- persistent product surfaces
- deterministic mutations
- policies and access rules
- platform services such as notifications or settings

If the feature should still make sense with AI turned off, it probably starts in
the app backend.

## What Belongs in the AI Runtime

A feature belongs in the AI runtime if it is primarily about:

- reasoning
- multi-turn interaction
- orchestration
- human checkpoints
- agent tooling
- run lifecycle

If the feature still matters even when the app changes verticals, it probably
belongs in the AI runtime.

## What Belongs in the App Bundle

A feature belongs in the app bundle if it answers a question about one app:

- what entities exist
- what modules and views exist
- what events matter
- what automations exist
- what workflows exist

Generated output should target these app-bundle contracts, not patches to core.

Important qualification:

- if a concern is already covered by the core runtime, the bundle should prefer
  configuration over fresh implementation
- if a concern only needs a thin extension, the bundle should author a stub on
  top of core rather than invent a new runtime primitive

## What Belongs in the Product

A feature belongs in the first-party product if it is about:

- helping users describe an app
- decomposing intent
- generating bundles from typed planning artifacts
- provisioning hosted apps
- billing and SaaS operations

Self-hosters should not need the first-party product layer to understand the
runtime architecture.

## Decision Test

Put it in the app backend if:

- it is a fact about app state or a deterministic app action

Put it in the AI runtime if:

- it is a fact about workflow execution or orchestration

Put it in the app bundle if:

- it defines one app's model, routes, workflows, or shell

Put it in the product if:

- it exists to build, host, or monetize apps
- it exists to turn user intent into approved specs, provision plans, and build
  graphs

Keep it in AG2 if:

- AG2 already has a native concept for it and Mozaiks only needs to configure or
  adapt it

## Cross References

- [../workflows/workflow-architecture.md](../workflows/workflow-architecture.md)
- [events-and-data/event-system.md](events-and-data/event-system.md)
- [../app/app-bundle-declaratives.md](../app/app-bundle-declaratives.md)
