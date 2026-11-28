# ADR 0027: Workflow Launch Entitlement Gate

Date: 2026-11-24

Status: proposed; design only, no implementation

Design for part 2 of [issue #770](https://github.com/BlocUnited-LLC/mozaiks/issues/770).
Source references were read on `da2ec238d`.

## Decision

A workflow may name the capability it requires, in its own `orchestrator.yaml`:

```yaml
schema_version: mozaiks.orchestrator.v1
workflow_name: ResearchWorkflow
entitlement_gate: feature.workflow.research
```

The runtime checks that capability, against the verified identity of the person
running the workflow, every time a run starts, resumes, or accepts a user
message. A workflow without the field behaves exactly as today.

The plan catalog, the assignment store, and `EntitlementPort` do not change.
`app/config/subscriptions.yaml` still decides which plans grant a capability.

## Reason

A module action declares `entitlement_gate` and `ModuleExecutor` enforces it. A
workflow has no equivalent. `POST /api/chats/{app_id}/{workflow_name}/start`
calls only `chat_prereqs`, which is empty in OSS, so a plan cannot include or
exclude a workflow.

The Factory therefore refuses to sell workflows at all. The only available
pattern is to gate the module action whose event triggers the workflow. That
leaves the workflow itself launchable by any signed-in user, so it is not an
access control.

An app can build its own gate from a gated action, a launch ticket, and a recheck
in the first agent. That is sound, but it is hand-written per workflow and is not
something a generator should reproduce.

## Detailed design

### 1. The gate belongs to the workflow manifest

`OrchestratorConfig` gains one optional field, `entitlement_gate: str | None`,
validated with the same capability identifier rules as `ActionDef`.

The schema version stays `v1`. `OrchestratorConfig` forbids unknown fields and
the workflow loader parses every manifest through it, so a runtime that predates
the field rejects the workflow instead of running it ungated. This was checked
against the current contract.

`extension_registry.json` is not the authority. It describes routing, it is
optional, and a product overlay may redeclare or remove an entry, which could
drop a gate without anyone deciding to. A screen that wants to show a workflow
as locked reads a projection the host computes from the manifest and the
entitlement adapter.

### 2. One enforcement point

The check runs in the workflow bridge, before any session write, event-log write,
or model call. It covers:

- a new run and a resumed run (`_launch_workflow_run_locked`), under the chat
  execution lease
- a message to a live run (`_continue_live_ag2_workflow_run`), under the same lease
- a reply to a run waiting on a person, which `handle_user_input_from_api` submits
  before it takes the lease, so the check runs before that submit

Every hosted path reaches those three places:

| Path | How it reaches the bridge |
|---|---|
| `POST /api/chats/.../start` (runtime and platform hosts) | creates a session; the run begins on WebSocket connect or first message |
| WebSocket connect auto-start | `handle_user_input_from_api` |
| `POST /chat/.../input`, WebSocket `user.input.submit` | `handle_user_input_from_api` |
| WebSocket `chat.start_workflow`, `chat.start_workflow_batch`, `chat.switch_workflow` | `_run_workflow_background` |
| Artifact action `launch_workflow` | creates a session; the run begins on WebSocket connect |
| `/api/transitions/resolve`, Studio trigger, context refresh, review continuation | create a session; the run begins as above |
| `POST /api/workflows/{workflow_name}/trigger` | creates a session; the run begins as above |
| Module event trigger | `_start_workflow_background_if_available` |
| Journey advance to the next workflow | `_run_workflow_background` |

Two paths do not use the bridge. `workflow_manager.get_workflow_handler` builds a
handler that calls the adapter directly. It has no caller in this repository, so
it is removed. `mozaiks gen` and one smoke script call
`run_workflow_orchestration` as local operator tools and stay ungated.

Session creation also checks the gate, after routing has resolved the final
workflow, so a customer sees the refusal before a chat exists. This is a
convenience. The bridge remains the authority because session creation has many
callers and says nothing about a chat opened before a plan ended.

The check does not go in the AG2 adapter. The adapter is engine mechanics, and a
message to a live run does not pass through it.

### 3. The chat decides which workflow runs

The gate that applies is the gate of the workflow stored on the chat record. A
workflow name supplied by a caller may be compared with the stored name and the
request refused when they differ. It never selects what runs or which gate is
checked. The same rule covers the chat's owner and app.

### 4. Identity is verified and passed explicitly

Plans may belong to a user, a tenant, or a workspace, so a bare `user_id` string
is not enough to find one. Module dispatch already solves this: lookups are
keyed only by `PersistencePrincipal`, never by requested scope.

`handle_user_input_from_api` and `_run_workflow_background` gain a required,
keyword-only `run_principal: PersistencePrincipal | None`. It has no default, so
a new caller cannot forget it. This follows `ModuleRequest.authority`.

| Caller | Principal |
|---|---|
| HTTP route | `PersistencePrincipal.from_authenticated_user` |
| WebSocket handler or auto-start | `PersistencePrincipal.from_websocket_user` on the socket's user |
| Journey advance | the source chat's live socket user; advance already requires that socket |
| Module event trigger | `current_persistence_principal(app_id)`, captured while the emitting action is active |
| Anything else | `None` |

`None` holds only what the app grants everyone through its default plan. This is
the existing module rule. Event envelopes, workflow context, and stored session
fields are never identity.

Some hosts decide tenant and workspace by membership rather than by token claim.
They need one new optional hook:

```text
workflow_scope_resolver
    async (*, principal, app_id, workflow_name) -> Optional[Dict[str, Any]]
    May return verified_tenant_id and verified_workspace_id, with the meaning
    module_scope_resolver gives them. Explicit None removes a token binding.
    An exception refuses the run.
```

A separate hook keeps workflow names out of `module_name` and `action_name`,
which hosts may branch on. A host registers the same membership function in both.

### 5. Refusal is closed and recoverable

| Situation | Result |
|---|---|
| Capability granted | run proceeds |
| Not granted, expired, inactive, or adapter error | refused, `ENTITLEMENT_REQUIRED` |
| Platform host, app has no `subscriptions.yaml` | allowed, as for module actions |
| Host has no entitlement checker registered | refused, `ENTITLEMENT_AUTHORITY_UNAVAILABLE` |
| Explicit local development authority | allowed, under the same condition module dispatch uses |

The platform host registers one checker and gives the same instance to
`ModuleExecutor` and the bridge. `mozaiksai.hosts.runtime` loads no plan catalog,
so a gated workflow on that host is refused rather than assumed free. No bypass
kind is added.

HTTP returns 402, matching module actions. WebSocket sends `chat.error` with the
code, the capability id, and the workflow name.

A refusal is not a failed run. It does not mark the chat failed or terminal, does
not call `on_fail`, and does not emit a failed `runtime.process_completed`. The
customer can upgrade and retry in the same chat. A journey stays on the refused
step. Reading an existing chat's history is not gated.

### 6. When access ends mid-run

Access is checked at the start, at resume, and on each user message. It is not
checked between agent turns or when a person answers an in-run UI tool. A
customer whose plan ends mid-turn finishes that turn and is refused at the next
message. Token limits are separate and are still checked before every model call.

A workflow that needs a stricter rule calls a gated module action as its first
tool call through `dispatch_workflow_module_action`, which already enforces.

### 7. Generator

The model continues to choose only which approved features each plan includes.
Code derives everything else.

- The pricing inventory adds `workflow.<workflow_id>` for each workflow in the
  approved design surface map.
- Code derives `feature.workflow.<workflow_id>`, the plan grants, and the
  `orchestrator.yaml` gate. AgentGenerator omits the field, as AppGenerator omits
  it on actions today.
- When a module action's event triggers a gated workflow, code applies the same
  capability to that action. Otherwise the action succeeds and the workflow is
  refused with no explanation.
- The bundle scanner replaces its blanket rejection with closure: every
  `feature.workflow.*` grant matches exactly one manifest gate, and every
  manifest gate is granted by at least one plan.
- `entitlement_drift` and the startup warning for undeclared gates cover
  workflows as well as actions.
- The gated launch point is reachable from a page, as gated actions must be.

The current blocks are removed in the same change: `SubscriptionContractDesigner`
and `AgentGenerator` prompts, `AppGenerator` context descriptions,
`validate_pricing_workflow_features`, the inventory text in
`subscription_contract_context.py`, the scanner rejection, and the tests that
assert them. No half-enabled state ships.

### 8. Contributor guidance and interface

The `create-workflow` skill, for both coding agents, gains a short section on the
field. `chat-ui` and `web_shell` contain no handling for an entitlement refusal
today, so the same work adds one message that links to the pricing page.

## Alternatives considered

- **Gate in `extension_registry.json`.** One place to read, but optional,
  overlay-editable, and about routing. See section 1.
- **`chat_prereqs`.** It receives `app_id`, `user_id` and a workflow name, so it
  cannot see a verified tenant or workspace. It is also a host hook, so each
  product would write its own gate.
- **Check only when a session is created.** Misses chats opened before a plan
  ended, and has to be repeated in every caller that creates a session.
- **Store the launching identity on the chat.** Survives without a live person,
  but freezes a membership that may since have been revoked. Not needed while
  every gated run has a person present or an active dispatch.
- **Ticket and recheck in each workflow.** Works, and remains available, but
  depends on every workflow author getting it right.
- **Let a host gate a shared Factory workflow it does not own.** Useful later. It
  would need an overlay that can only add a gate. Deferred.

## Consequences

- Plans can include AI workflows, in hand-written and generated apps.
- Two bridge methods change signature, so every caller is updated at once.
- `orchestrator.yaml`, the platform extension bundle, and the refusal codes are
  public contracts and each gains a member.
- A gated workflow cannot run for a customer who is not present, such as a
  nightly job. That needs its own decision.
- Adding a capability to a plan does not reach existing subscribers whose record
  already carries a saved capability list. This is existing behaviour, applies to
  actions too, and affects how a new paid workflow is rolled out.
- A membership-based host must register `workflow_scope_resolver` before any of
  its workflows declares a gate. Workflows that already carry their own gate keep
  it and need no change.

## The other parts of issue #770

Not decided here.

**Usage limits** (part 1) stay display-only. They need an atomic reserve against a
per-period counter. The check in section 2 is where a workflow reservation would
go. A separate ADR is required, and prompts must keep saying limits are not
enforced until it lands.

**Cancellation** (part 3) should land before, or with, this change.
`ConfiguredEntitlementAdapter.check` denies every capability for an inactive or
expired record, while `current_plan_id` on the same adapter returns the default
plan for the same record. With a gate on a workflow that the free plan includes,
a former customer would be refused what a new visitor receives. The expected fix
is to fall back to the default plan in `check`. It first needs confirmation of
which hosted statuses mean an intentional suspension, since a suspended account
should not regain free access.

## Delivery order

Queued behind the verification-priority rule. This adds capability surface and
does not help a build finish.

1. Cancellation fallback.
2. Sections 1 to 6 and the interface message.
3. Section 7 and the guidance in section 8, once step 2 is proven live.

## Reversibility

Medium risk. The field is optional and additive, but once a generated app sells a
workflow, removing the gate would give paid access away.

## Affected invariants

- 1, provider neutrality: preserved. Nothing names a payment provider.
- 3, deterministic code validates and promotes: preserved. Code derives every gate.
- 4, versioned public contracts: `mozaiks.orchestrator.v1` gains an optional field.
- 6, authority bypass: not expanded.
- 7, App Zero dogfoods public contracts: hosted workflows may adopt the same field.

## Issue #411

Defers that direction. This ADR adds one field derived from decisions already
approved in the subscription contract. It creates no decision ledger and no second
authority for application meaning.

## OSS boundary

Keep OSS: foundational. Plan tiers, prices, and billing stay in the hosted product.

## Evidence

Run on `da2ec238d`: `OrchestratorConfig` and the workflow loader reject a manifest
that carries `entitlement_gate`. Everything else in this document was established
by reading source and has not been exercised.

## Validation

Required before implementation merges. All use real HTTP and WebSocket requests,
real authentication, and a disposable MongoDB.

1. Four customers (none, free, paid, cancelled) on every row of the section 2
   table. Only the paid customer runs the workflow, and a refused attempt makes
   no model call and no session or event-log write.
2. A request that names a different workflow from the one stored on the chat is
   refused, for a new run and for a live run.
3. A paid customer is downgraded between two messages. The second is refused, the
   history still loads, and an upgrade makes the same chat usable again.
4. A token with a tenant or workspace claim but no membership does not select
   that tenant's plan on a membership-based host.
5. Module event trigger with a paid emitter, a free emitter, and no principal.
6. A journey whose second workflow is gated.
7. Adapter error, missing checker, and the runtime-only host all refuse.
8. A static test that only the bridge calls the orchestration adapter.
9. The manifest field is rejected by the contract that predates it.
10. One live generated app whose plan includes a workflow, built by the model and
    not from hand-written inputs, exercised as paid and free customers.
