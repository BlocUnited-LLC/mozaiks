# Mozaiks Core Event Taxonomy (Source of Truth)
> **Doc Status:** authoritative (runtime and consuming apps depend on this doc)
> **Version:** 2.0.0
> **Last Updated:** 2026-02-23
> **Owner:** mozaiks

This document defines the canonical event taxonomy for mozaiks.
It is the source of truth for:

- Event domains and type names
- Payload contracts (required fields)
- Provenance and causation semantics
- Example use cases and event flows

All runtime implementations and agent reasoning MUST align with this document.

---

## Design Principles

- Immutable facts: events describe what happened, not commands.
- Typed and structured: every event has an explicit schema.
- Causality links: every event has `causation_id` and `correlation_id`.
- Provenance: every event records actor, source, and timestamp.
- Domain separation: each domain answers a single question.
- No direct state mutation: all state must be derived from events.

---

## Naming Convention

All event type names use **lowercase dot-notation**:

```
domain.event_name
```

This format is enforced at runtime by `DomainEvent.event_type` validation
(`^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$`).

Examples: `process.started`, `notification.delivered`, `subscription.limit_reached`.

---

## Canonical Event Envelope

All events MUST use this envelope.

```json
{
  "event_id": "uuid",
  "event_type": "domain.event_name",
  "timestamp": "ISO8601",
  "actor": {
    "id": "agent|system|user",
    "type": "agent|system|user",
    "metadata": {
      "source": "tool|agent|user|system|integration"
    }
  },
  "payload": {},
  "causation_id": "event_id of the triggering event",
  "correlation_id": "run_id|task_id|workflow_id"
}
```

Envelope requirements:

- `event_id` is a UUID string, unique and immutable.
- `event_type` is the canonical event name. Use the exact names in this document.
- `timestamp` is ISO-8601 UTC.
- `actor.type` is one of `agent`, `system`, `user`.
- `actor.metadata.source` is required and indicates the immediate emission source.
- `causation_id` must point to the direct triggering event.
- `correlation_id` groups events for the same run, task, or workflow.

Root events with no prior cause MUST still include `causation_id` and set it to
`null` or an explicit root marker (e.g., `root`).

---

## Domain Overview

Each domain answers a single question.

| Domain Prefix | Question |
| --- | --- |
| `perception` | What was observed from the world or UI? |
| `semantics` | What was interpreted from raw perception? |
| `control` | What decisions were made about planning? |
| `orchestration` | What should run when and how? |
| `action` | What did agents attempt internally? |
| `integration` | What external systems were invoked or responded? |
| `world_state` | How did belief about the world change? |
| `evaluation` | Did it work and how well? |
| `learning` | What patterns or skills improved? |
| `commerce` | Subscription, credits, and monetary state |
| `entitlement` | What is allowed? |
| `settings` | Preferences and configuration |
| `subscription` | Plan lifecycle and metering |
| `notification` | Delivery actions for subscribers |
| `process` | Workflow run lifecycle |
| `task` | Individual task lifecycle within a run |
| `chat` | Chat message and usage events |
| `artifact` | Artifact CRUD events |
| `replay` | Replay boundary and snapshot events |
| `ui.tool` | Human-in-the-loop UI tool events |

---

## Event Domains and Types

### process (Workflow Run Lifecycle)

| Event Type | Description | Payload (required fields) |
| --- | --- | --- |
| `process.created` | Run created | `run_id` |
| `process.started` | Run executing | `run_id` |
| `process.running` | Run in progress | `run_id` |
| `process.resume_requested` | Resume requested | `run_id` |
| `process.resumed` | Run resumed | `run_id` |
| `process.completed` | Run succeeded | `run_id`, `outputs` |
| `process.failed` | Run failed | `run_id`, `error` |
| `process.cancelled` | Run cancelled | `run_id` |

### task

| Event Type | Description | Payload (required fields) |
| --- | --- | --- |
| `task.started` | Task executing | `task_id` |
| `task.retrying` | Task retry | `task_id`, `attempt` |
| `task.completed` | Task succeeded | `task_id`, `outputs` |
| `task.failed` | Task failed | `task_id`, `error` |

### perception

| Event Type | Description | Payload (required fields) |
| --- | --- | --- |
| `perception.user_input_received` | User submitted intent | `user_id`, `text` |
| `perception.file_ingested` | File uploaded | `file_id`, `filename`, `size` |
| `perception.webhook_event_received` | External webhook received | `source`, `payload` |

### semantics

| Event Type | Description | Payload (required fields) |
| --- | --- | --- |
| `semantics.intent_extracted` | Parsed user intent | `intent`, `entities`, `confidence` |
| `semantics.constraint_identified` | Extracted constraint | `key`, `value` |
| `semantics.entity_mention_detected` | Entity mention from text | `entity_type`, `entity_id` |

### control

| Event Type | Description | Payload (required fields) |
| --- | --- | --- |
| `control.goal_created` | New top-level goal | `goal_id`, `description` |
| `control.task_decomposed` | Tasks broken out | `dag` |
| `control.plan_accepted` | Planner output accepted | `plan_id` |
| `control.plan_rejected` | Planner output rejected | `plan_id`, `reason` |

### orchestration

| Event Type | Description | Payload (required fields) |
| --- | --- | --- |
| `orchestration.task_queued` | Task enqueued | `task_id` |
| `orchestration.task_ready` | All deps met | `task_id` |
| `orchestration.task_started` | Executor begins task | `task_id` |
| `orchestration.task_paused` | Task paused with reason | `task_id`, `reason` |
| `orchestration.task_resumed` | Task resumed | `task_id` |
| `orchestration.task_completed` | Task finished | `task_id`, `outputs` |
| `orchestration.task_failed` | Task error | `task_id`, `error` |
| `orchestration.dependency_satisfied` | Dependency met | `task_id`, `dep_id` |
| `orchestration.run_started` | Workflow run started | `run_id`, `dag_id` |
| `orchestration.run_completed` | Workflow run completed | `run_id` |
| `orchestration.run_failed` | Workflow run failed | `run_id`, `error` |

### action

| Event Type | Description | Payload (required fields) |
| --- | --- | --- |
| `action.skill_invoked` | Skill selected | `skill_name`, `task_id` |
| `action.operation_requested` | Sub-action attempted | `op_name`, `inputs` |
| `action.proposed` | Agent proposed next action | `details` |
| `action.approved` | Action cleared by policy | `action_id` |
| `action.rejected` | Action blocked | `action_id`, `reason` |

### integration

| Event Type | Description | Payload (required fields) |
| --- | --- | --- |
| `integration.tool_call_sent` | Tool or service invoked | `tool`, `inputs` |
| `integration.tool_call_succeeded` | Tool succeeded | `tool`, `outputs` |
| `integration.tool_call_failed` | Tool failed | `tool`, `error` |
| `integration.webhook_received` | External webhook received | `source`, `data` |

### world_state

All world_state events MUST include `source_event` in payload to link back to
the immediate cause. The `source_event` value MUST be the `event_id` of the
causation event or a stable reference to it.

| Event Type | Description | Payload (required fields) |
| --- | --- | --- |
| `world_state.entity_created` | New entity | `entity_type`, `id`, `source_event` |
| `world_state.attribute_set` | Attribute assigned | `entity_id`, `attribute`, `value`, `source_event` |
| `world_state.attribute_changed` | Attribute changed | `entity_id`, `attribute`, `old`, `new`, `source_event` |
| `world_state.relationship_added` | Relation added | `from`, `rel`, `to`, `source_event` |
| `world_state.relationship_removed` | Relation removed | `from`, `rel`, `to`, `source_event` |
| `world_state.fact_invalidated` | Prior fact revoked | `entity_id`, `field`, `source_event` |
| `world_state.confidence_adjusted` | Confidence changed | `entity_id`, `field`, `new`, `source_event` |

### evaluation

| Event Type | Description | Payload (required fields) |
| --- | --- | --- |
| `evaluation.outcome_verified` | Verified success | `run_id`, `criteria` |
| `evaluation.sla_exceeded` | Performance or HWM violation | `run_id`, `metric`, `value` |
| `evaluation.confidence_dropped` | Confidence below threshold | `entity_id`, `value` |
| `evaluation.result_approved` | Human verified result | `run_id` |

### learning

| Event Type | Description | Payload (required fields) |
| --- | --- | --- |
| `learning.pattern_found` | Discovered pattern | `pattern`, `support` |
| `learning.skill_synthesized` | New reusable skill | `skill`, `dag_template` |
| `learning.policy_updated` | Policy refined | `rule`, `delta` |
| `learning.model_tuned` | Internal model update | `model_id`, `changes` |

### commerce

| Event Type | Description | Payload (required fields) |
| --- | --- | --- |
| `commerce.credits_purchased` | Purchased credits | `user_id`, `amount` |
| `commerce.credits_consumed` | Run cost consumed | `user_id`, `run_id`, `amount` |
| `commerce.subscription_started` | Subscribed | `user_id`, `plan` |
| `commerce.subscription_canceled` | Canceled | `user_id`, `plan` |
| `commerce.billing_failed` | Card or billing issue | `user_id`, `reason` |

### entitlement

| Event Type | Description | Payload (required fields) |
| --- | --- | --- |
| `entitlement.granted` | Permission granted | `subject`, `permission` |
| `entitlement.revoked` | Permission revoked | `subject`, `permission` |
| `entitlement.expired` | Permission expired | `subject`, `permission` |
| `entitlement.limited` | Scoped or performance limit | `subject`, `limit` |
| `entitlement.limit_warning` | Approaching resource limit | `user_id`, `resource`, `percent` |
| `entitlement.limit_reached` | Resource limit hit | `user_id`, `resource`, `current`, `limit` |
| `entitlement.feature_granted` | Feature access granted | `user_id`, `feature` |
| `entitlement.feature_revoked` | Feature access revoked | `user_id`, `feature` |

### settings

| Event Type | Description | Payload (required fields) |
| --- | --- | --- |
| `settings.preference_set` | Preference assigned | `owner`, `key`, `value` |
| `settings.preference_removed` | Preference removed | `owner`, `key` |
| `settings.scope_inherited` | Preference inheritance | `owner`, `parent_scope` |
| `settings.updated` | Bulk settings update | `user_id`, `workflow_name`, `fields` |

### subscription

| Event Type | Description | Payload (required fields) |
| --- | --- | --- |
| `subscription.plan_changed` | Plan changed | `user_id`, `old_plan`, `new_plan` |
| `subscription.trial_started` | Trial began | `user_id`, `plan`, `days` |
| `subscription.trial_ending` | Trial ending soon | `user_id`, `days_remaining` |
| `subscription.trial_ended` | Trial expired | `user_id` |
| `subscription.payment_failed` | Payment issue | `user_id`, `reason` |
| `subscription.renewed` | Subscription renewed | `user_id`, `plan` |
| `subscription.limit_warning` | Approaching limit | `user_id`, `resource`, `percent`, `threshold` |
| `subscription.limit_reached` | Limit hit | `user_id`, `resource`, `current`, `limit`, `behavior` |

### notification

| Event Type | Description | Payload (required fields) |
| --- | --- | --- |
| `notification.generated` | Notification triggered | `subscriber`, `message`, `channels` |
| `notification.delivered` | Delivered | `subscriber`, `channel` |
| `notification.failed` | Delivery failed | `subscriber`, `channel`, `reason` |
| `notification.acknowledged` | User acknowledged | `subscriber` |

### artifact

| Event Type | Description | Payload (required fields) |
| --- | --- | --- |
| `artifact.created` | Artifact created | `artifact_id`, `artifact_type` |
| `artifact.updated` | Artifact updated | `artifact_id` |
| `artifact.state_replaced` | Full state replacement | `artifact_id`, `state` |
| `artifact.state_patched` | Partial state update | `artifact_id`, `patch` |

### replay

| Event Type | Description | Payload (required fields) |
| --- | --- | --- |
| `replay.boundary` | Replay boundary marker | `replay_from_seq`, `replay_to_seq` |
| `replay.snapshot` | Snapshot for replay | `run_id`, `last_seq` |

### chat

| Event Type | Description | Payload (required fields) |
| --- | --- | --- |
| `chat.text` | Chat text message | `content` |
| `chat.message_sent` | Message sent | `user_id`, `content` |
| `chat.usage_delta` | LLM token usage | `total_tokens` |

### ui.tool

| Event Type | Description | Payload (required fields) |
| --- | --- | --- |
| `ui.tool.requested` | UI tool invocation requested | `tool_id`, `payload` |
| `ui.tool.completed` | UI tool completed | `tool_id`, `result` |
| `ui.tool.failed` | UI tool failed | `tool_id`, `error` |

---

## Provenance and Causation

- `causation_id` points to the immediate triggering event.
- `correlation_id` binds all events for a run, task, or workflow.
- `actor` identifies who produced the event; it does not replace causation.
- Every event MUST include `actor.metadata.source` to identify the emission path.
- For multi-hop workflows, do not skip causation links. Chain them.

---

## Example Use Cases

### User input received

```json
{
  "event_id": "1d0a6a62-3b10-4d4d-9f5f-9a7c7a8f1e3a",
  "event_type": "perception.user_input_received",
  "timestamp": "2026-02-06T01:10:00Z",
  "actor": {
    "id": "user_123",
    "type": "user",
    "metadata": { "source": "user" }
  },
  "payload": { "user_id": "user_123", "text": "Build a hiring workflow" },
  "causation_id": null,
  "correlation_id": "run_abc123"
}
```

### Intent extracted

```json
{
  "event_id": "a1c1f9a5-f0f9-4b1e-a1b0-8e6f8b77b9b3",
  "event_type": "semantics.intent_extracted",
  "timestamp": "2026-02-06T01:10:01Z",
  "actor": {
    "id": "intent_parser",
    "type": "agent",
    "metadata": { "source": "agent" }
  },
  "payload": { "intent": "create_workflow", "entities": ["Hiring"], "confidence": 0.92 },
  "causation_id": "1d0a6a62-3b10-4d4d-9f5f-9a7c7a8f1e3a",
  "correlation_id": "run_abc123"
}
```

### Task started

```json
{
  "event_id": "a7f8d1df-2cb5-4a78-9e1d-25f992aa30e3",
  "event_type": "orchestration.task_started",
  "timestamp": "2026-02-06T01:10:05Z",
  "actor": {
    "id": "runtime",
    "type": "system",
    "metadata": { "source": "system" }
  },
  "payload": { "task_id": "task_generate_agents" },
  "causation_id": "b1d0bd7d-3d51-4f95-9e1b-1ea2dd7b4f3e",
  "correlation_id": "run_abc123"
}
```

---

## Example Event Flow (Full Stack)

```
perception.user_input_received
semantics.intent_extracted
control.goal_created
control.task_decomposed
orchestration.run_started
orchestration.task_queued
orchestration.task_started
action.skill_invoked
integration.tool_call_sent
integration.tool_call_succeeded
world_state.attribute_set
evaluation.outcome_verified
orchestration.task_completed
orchestration.run_completed
learning.pattern_found
learning.skill_synthesized
```

---

## AG2 Interop Mapping

AG2 native events are normalized on ingest into this taxonomy.

| AG2 Event | Canonical Event |
| --- | --- |
| `chat.run_complete` | `orchestration.run_completed` |
| `chat.orchestration.run_completed` | `orchestration.run_completed` |
| `auto_tool.tool_call` | `integration.tool_call_sent` |
| `auto_tool.tool_response` | `integration.tool_call_succeeded` |
| `select_speaker` | `action.skill_invoked` |

Normalization rules:

- Map all AG2 run completion variants to `orchestration.run_completed`.
- Preserve AG2-specific fields inside the `payload` where needed.
- Always wrap in the canonical envelope before persistence or projection.

---

## Implementation Guardrails

- No state mutation without emitting a matching event.
- Events are append-only; corrections create new events.
- Projections and state machines are derived from the event log.
- Schema changes require new event types or versioned payloads.
