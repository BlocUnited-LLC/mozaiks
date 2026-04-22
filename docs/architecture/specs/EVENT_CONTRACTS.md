# Event Contracts

**Status:** Specification
**Created:** 2026-04-06

This document defines all event contracts for the Mozaiks ecosystem.

---

## Event Envelope Schema

Every event MUST conform to this envelope:

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "MozaiksEvent",
  "type": "object",
  "required": ["event_id", "type", "timestamp", "tenant", "payload"],
  "properties": {
    "event_id": {
      "type": "string",
      "format": "uuid",
      "description": "Unique identifier for this event instance"
    },
    "type": {
      "type": "string",
      "pattern": "^[A-Z][a-zA-Z]+\\.[A-Z][a-zA-Z]+$",
      "description": "Event type in Domain.EventName format"
    },
    "timestamp": {
      "type": "string",
      "format": "date-time",
      "description": "ISO-8601 timestamp when event occurred"
    },
    "version": {
      "type": "integer",
      "minimum": 1,
      "default": 1,
      "description": "Schema version for this event type"
    },
    "tenant": {
      "type": "object",
      "required": ["app_id"],
      "properties": {
        "platform_id": {
          "type": "string",
          "description": "Platform identifier (e.g., 'mozaiks-platform')"
        },
        "app_id": {
          "type": "string",
          "description": "Application identifier"
        },
        "user_id": {
          "type": "string",
          "description": "User identifier (if user-scoped)"
        },
        "run_id": {
          "type": "string",
          "description": "Workflow run identifier (if run-scoped)"
        }
      }
    },
    "actor": {
      "type": "object",
      "properties": {
        "type": {
          "type": "string",
          "enum": ["user", "agent", "system", "integration"]
        },
        "id": {
          "type": "string"
        }
      }
    },
    "causation_id": {
      "type": "string",
      "format": "uuid",
      "description": "ID of the event that caused this event"
    },
    "correlation_id": {
      "type": "string",
      "format": "uuid",
      "description": "ID for tracing related events"
    },
    "payload": {
      "type": "object",
      "description": "Event-specific data"
    },
    "metadata": {
      "type": "object",
      "properties": {
        "source": {
          "type": "string",
          "description": "Source package (mozaiks-ai, mozaiks-plugins, external)"
        },
        "environment": {
          "type": "string",
          "enum": ["production", "staging", "development"]
        }
      }
    }
  }
}
```

---

## Platform-Routed Events

These events are automatically forwarded to the mozaiks-platform.

### Commerce Events

Used for billing and monetization.

#### Commerce.CreditsConsumed

Emitted when AI credits are consumed (LLM calls).

```json
{
  "type": "Commerce.CreditsConsumed",
  "payload": {
    "credits": 150,
    "reason": "llm_call",
    "details": {
      "model": "claude-3-opus",
      "tokens_input": 2500,
      "tokens_output": 8000,
      "workflow": "AppGenerator",
      "tool": "generate_code"
    }
  }
}
```

**Routed to:** Payment.API
**Used for:** Token-based billing

#### Commerce.SandboxTimeUsed

Emitted when E2B sandbox time is consumed.

```json
{
  "type": "Commerce.SandboxTimeUsed",
  "payload": {
    "duration_seconds": 180,
    "sandbox_id": "sbx_123",
    "purpose": "build",
    "workflow": "AppGenerator"
  }
}
```

**Routed to:** Payment.API
**Used for:** Sandbox time billing

#### Commerce.BandwidthUsed

Emitted periodically for bandwidth consumption.

```json
{
  "type": "Commerce.BandwidthUsed",
  "payload": {
    "bytes_in": 1048576,
    "bytes_out": 5242880,
    "period_start": "2026-04-06T00:00:00Z",
    "period_end": "2026-04-06T01:00:00Z"
  }
}
```

**Routed to:** Payment.API
**Used for:** Bandwidth billing

#### Commerce.StorageUsed

Emitted periodically for storage consumption.

```json
{
  "type": "Commerce.StorageUsed",
  "payload": {
    "bytes": 104857600,
    "storage_type": "database",
    "measured_at": "2026-04-06T00:00:00Z"
  }
}
```

**Routed to:** Payment.API
**Used for:** Storage billing

#### Commerce.SubscriptionStarted

Emitted when a subscription begins.

```json
{
  "type": "Commerce.SubscriptionStarted",
  "payload": {
    "subscription_id": "sub_123",
    "plan_id": "pro",
    "billing_cycle": "monthly",
    "started_at": "2026-04-06T00:00:00Z"
  }
}
```

**Routed to:** Payment.API
**Used for:** Subscription tracking

#### Commerce.SubscriptionUpgraded

Emitted when a subscription is upgraded.

```json
{
  "type": "Commerce.SubscriptionUpgraded",
  "payload": {
    "subscription_id": "sub_123",
    "previous_plan": "starter",
    "new_plan": "pro",
    "effective_at": "2026-04-06T00:00:00Z"
  }
}
```

#### Commerce.SubscriptionCanceled

Emitted when a subscription is canceled.

```json
{
  "type": "Commerce.SubscriptionCanceled",
  "payload": {
    "subscription_id": "sub_123",
    "plan_id": "pro",
    "canceled_at": "2026-04-06T00:00:00Z",
    "effective_end": "2026-05-06T00:00:00Z",
    "reason": "user_requested"
  }
}
```

#### Commerce.PaymentFailed

Emitted when a payment fails.

```json
{
  "type": "Commerce.PaymentFailed",
  "payload": {
    "payment_id": "pay_123",
    "amount": 2900,
    "currency": "usd",
    "failure_reason": "card_declined",
    "retry_scheduled": true
  }
}
```

---

### Observability Events

Used for metrics, monitoring, and analytics.

#### Observability.MetricRecorded

Generic metric recording.

```json
{
  "type": "Observability.MetricRecorded",
  "payload": {
    "metric_name": "api_latency_ms",
    "value": 45.2,
    "tags": {
      "endpoint": "/api/orders",
      "method": "GET",
      "status": 200
    }
  }
}
```

**Routed to:** Analytics service
**Used for:** Performance monitoring

#### Observability.ErrorOccurred

Emitted when an error occurs.

```json
{
  "type": "Observability.ErrorOccurred",
  "payload": {
    "error_type": "ValidationError",
    "message": "Invalid order data",
    "stack_trace": "...",
    "context": {
      "endpoint": "/api/orders",
      "request_id": "req_123"
    }
  }
}
```

**Routed to:** Analytics service
**Used for:** Error tracking

#### Observability.HealthCheckCompleted

Emitted periodically with health status.

```json
{
  "type": "Observability.HealthCheckCompleted",
  "payload": {
    "status": "healthy",
    "checks": {
      "database": {"status": "ok", "latency_ms": 5},
      "event_bus": {"status": "ok"},
      "modules": {"status": "ok", "loaded": 5},
      "workflows": {"status": "ok", "loaded": 3}
    },
    "uptime_seconds": 86400
  }
}
```

**Routed to:** Analytics service
**Used for:** Health monitoring

#### Observability.AdminPageViewed

Emitted when admin dashboard is accessed.

```json
{
  "type": "Observability.AdminPageViewed",
  "payload": {
    "page": "users",
    "admin_user_id": "user_456"
  }
}
```

#### Observability.AdminActionTaken

Emitted when admin performs an action.

```json
{
  "type": "Observability.AdminActionTaken",
  "payload": {
    "action": "user_disabled",
    "target_type": "user",
    "target_id": "user_789",
    "admin_user_id": "user_456"
  }
}
```

---

### Learning Events

Used for AI improvement and pattern discovery.

#### Learning.PatternDiscovered

Emitted when a useful pattern is identified.

```json
{
  "type": "Learning.PatternDiscovered",
  "payload": {
    "pattern_id": "pat_123",
    "pattern_type": "user_preference",
    "description": "User prefers Tailwind over Bootstrap",
    "confidence": 0.85,
    "sample_size": 12,
    "workflow": "AppGenerator"
  }
}
```

**Routed to:** Learning service
**Used for:** Improving AI generations

#### Learning.SkillImproved

Emitted when a workflow/tool is refined.

```json
{
  "type": "Learning.SkillImproved",
  "payload": {
    "skill_type": "tool",
    "skill_name": "generate_component",
    "improvement": "Added TypeScript support",
    "source": "user_feedback"
  }
}
```

---

### Evaluation Events

Used for quality tracking and feedback.

#### Evaluation.HumanApproved

Emitted when a user approves generated output.

```json
{
  "type": "Evaluation.HumanApproved",
  "payload": {
    "artifact_type": "generated_app",
    "artifact_id": "art_123",
    "workflow": "AppGenerator",
    "run_id": "run_789",
    "feedback": "positive",
    "edits_made": false,
    "time_to_approval_seconds": 30
  }
}
```

**Routed to:** Learning service
**Used for:** Quality metrics, model improvement

#### Evaluation.HumanRejected

Emitted when a user rejects generated output.

```json
{
  "type": "Evaluation.HumanRejected",
  "payload": {
    "artifact_type": "generated_app",
    "artifact_id": "art_123",
    "workflow": "AppGenerator",
    "run_id": "run_789",
    "rejection_reason": "missing_feature",
    "feedback_text": "Needs user authentication"
  }
}
```

#### Evaluation.QualityScored

Emitted when automated quality check runs.

```json
{
  "type": "Evaluation.QualityScored",
  "payload": {
    "artifact_type": "generated_code",
    "artifact_id": "art_123",
    "scores": {
      "syntax_valid": true,
      "tests_passing": true,
      "lint_score": 0.95,
      "complexity_score": 0.7
    }
  }
}
```

---

### Orchestration Events

Selected orchestration events are platform-routed for analytics.

#### Orchestration.RunCompleted

Emitted when a workflow run completes successfully.

```json
{
  "type": "Orchestration.RunCompleted",
  "payload": {
    "run_id": "run_789",
    "workflow": "AppGenerator",
    "duration_ms": 45000,
    "tasks_completed": 12,
    "tools_used": ["generate_spec", "create_files", "run_tests"],
    "artifacts_produced": ["app_spec", "source_code"]
  }
}
```

**Routed to:** Analytics service
**Used for:** Workflow analytics

#### Orchestration.RunFailed

Emitted when a workflow run fails.

```json
{
  "type": "Orchestration.RunFailed",
  "payload": {
    "run_id": "run_789",
    "workflow": "AppGenerator",
    "duration_ms": 15000,
    "failure_stage": "code_generation",
    "error_type": "LLMError",
    "error_message": "Rate limit exceeded",
    "retryable": true
  }
}
```

**Routed to:** Analytics service
**Used for:** Error tracking, support

---

### Entitlement Events

Used for access control and feature gating.

#### Entitlements.Granted

Emitted when access is granted.

```json
{
  "type": "Entitlements.Granted",
  "payload": {
    "entitlement_type": "feature",
    "entitlement_id": "ai_workflows",
    "granted_to": "user_456",
    "granted_by": "subscription_upgrade",
    "expires_at": null
  }
}
```

#### Entitlements.Revoked

Emitted when access is revoked.

```json
{
  "type": "Entitlements.Revoked",
  "payload": {
    "entitlement_type": "feature",
    "entitlement_id": "ai_workflows",
    "revoked_from": "user_456",
    "revoked_by": "subscription_canceled",
    "effective_at": "2026-05-06T00:00:00Z"
  }
}
```

---

## Local-Only Events

These events stay within the app and are NOT forwarded to the platform.
Hosting and provisioning events are the exception when managed hosting is enabled.

### App Domain Events

Custom events defined by the app.

```json
{
  "type": "Orders.Created",
  "payload": {
    "order_id": "ord_123",
    "customer_id": "cust_456",
    "items": [...],
    "total": 9900
  }
}
```

### Notification Events

#### Notification.Triggered

Emitted when a notification should be sent.

```json
{
  "type": "Notification.Triggered",
  "payload": {
    "notification_type": "workflow_complete",
    "recipient_user_id": "user_456",
    "channels": ["in_app", "email"],
    "template": "workflow_complete",
    "data": {
      "workflow_name": "AppGenerator",
      "result": "success"
    }
  }
}
```

**Handled by:** Notification handler (built-in or external)

#### Notification.Sent

Emitted after notification is sent.

```json
{
  "type": "Notification.Sent",
  "payload": {
    "notification_id": "notif_123",
    "channel": "email",
    "recipient": "user@example.com",
    "status": "delivered"
  }
}
```

### Hosting Events

#### Hosting.Requested

Emitted when app deployment is requested.

```json
{
  "type": "Hosting.Requested",
  "payload": {
    "app_id": "app_123",
    "github_repo": "user/my-app",
    "branch": "main",
    "tier": "pro"
  }
}
```

**Handled by:** Platform Hosting.API

#### Hosting.AppProvisioned

Emitted when app container is provisioned.

```json
{
  "type": "Hosting.AppProvisioned",
  "payload": {
    "app_id": "app_123",
    "container_id": "cnt_456",
    "url": "https://my-app.mozaiks.app",
    "region": "eastus"
  }
}
```

#### Hosting.AppStarted

Emitted when app starts successfully.

```json
{
  "type": "Hosting.AppStarted",
  "payload": {
    "app_id": "app_123",
    "container_id": "cnt_456",
    "started_at": "2026-04-06T12:00:00Z"
  }
}
```

### Hosted Control-Plane Events

These events are emitted for domain, DNS, TLS, and provisioning lifecycle in
managed hosting mode.

#### Provisioning.StageStarted

Emitted when a provisioning stage begins.

```json
{
  "type": "Provisioning.StageStarted",
  "payload": {
    "provisioning_job_id": "prov_123",
    "environment_id": "env_456",
    "stage": "configure_domain_tls",
    "attempt": 1,
    "total_stages": 8
  }
}
```

#### Provisioning.StageCompleted

Emitted when a provisioning stage completes successfully.

```json
{
  "type": "Provisioning.StageCompleted",
  "payload": {
    "provisioning_job_id": "prov_123",
    "environment_id": "env_456",
    "stage": "configure_domain_tls",
    "duration_ms": 8200
  }
}
```

#### Provisioning.WaitingForInput

Emitted when provisioning is blocked on user action.

```json
{
  "type": "Provisioning.WaitingForInput",
  "payload": {
    "provisioning_job_id": "prov_123",
    "environment_id": "env_456",
    "reason": "domain_verification",
    "required_action": "Add TXT record at _acme-challenge.example.com"
  }
}
```

#### Provisioning.Failed

Emitted when a provisioning stage fails.

```json
{
  "type": "Provisioning.Failed",
  "payload": {
    "provisioning_job_id": "prov_123",
    "environment_id": "env_456",
    "stage": "configure_domain_tls",
    "retryable": true,
    "error_code": "DOMAIN_VALIDATION_TIMEOUT",
    "error_message": "TXT verification record not detected within timeout window"
  }
}
```

#### Domain.BindRequested

Emitted when a custom domain is submitted for environment binding.

```json
{
  "type": "Domain.BindRequested",
  "payload": {
    "environment_id": "env_456",
    "domain": "app.customer.com",
    "mode": "byod"
  }
}
```

#### Domain.VerificationRequired

Emitted when DNS/domain verification details are available.

```json
{
  "type": "Domain.VerificationRequired",
  "payload": {
    "environment_id": "env_456",
    "domain": "app.customer.com",
    "record_type": "TXT",
    "record_name": "_acme-challenge.app.customer.com",
    "record_value": "verification-token"
  }
}
```

#### Domain.Verified

Emitted once custom-domain verification succeeds.

```json
{
  "type": "Domain.Verified",
  "payload": {
    "environment_id": "env_456",
    "domain": "app.customer.com",
    "verified_at": "2026-04-06T12:00:00Z"
  }
}
```

#### Dns.ZoneProvisioned

Emitted when the DNS zone is created and delegated.

```json
{
  "type": "Dns.ZoneProvisioned",
  "payload": {
    "environment_id": "env_456",
    "zone": "customer.com",
    "provider": "azure-dns",
    "name_servers": ["ns1-01.azure-dns.com", "ns2-01.azure-dns.net"]
  }
}
```

#### Dns.RecordApplied

Emitted after a required DNS record has been created or updated.

```json
{
  "type": "Dns.RecordApplied",
  "payload": {
    "environment_id": "env_456",
    "zone": "customer.com",
    "record_type": "CNAME",
    "record_name": "app",
    "record_value": "env-456-host.mozaiks.app"
  }
}
```

#### Edge.DomainBound

Emitted when the edge layer has attached the custom domain.

```json
{
  "type": "Edge.DomainBound",
  "payload": {
    "environment_id": "env_456",
    "provider": "azure-frontdoor",
    "domain": "app.customer.com",
    "route": "primary"
  }
}
```

#### Certificate.Issued

Emitted when managed TLS is successfully issued and active.

```json
{
  "type": "Certificate.Issued",
  "payload": {
    "environment_id": "env_456",
    "domain": "app.customer.com",
    "provider": "azure-frontdoor",
    "status": "active",
    "expires_at": "2027-04-06T00:00:00Z"
  }
}
```

#### Hosting.Ready

Emitted when endpoint, domain, TLS, and monitoring are all ready.

```json
{
  "type": "Hosting.Ready",
  "payload": {
    "environment_id": "env_456",
    "app_id": "app_123",
    "endpoint": "https://app.customer.com",
    "region": "eastus",
    "provisioning_job_id": "prov_123"
  }
}
```

**Routed to:** Hosting.API / Ops Control Plane

---

## Event Routing Configuration

### In mozaiks.config.yaml

```yaml
platform:
  events:
    endpoint: https://events.mozaiks.app/ingest
    api_key: ${MOZAIKS_PLATFORM_API_KEY}

    # Batching
    batch_size: 100
    flush_interval_ms: 5000

    # What gets forwarded (prefix matching)
    route:
      - "Commerce.*"
      - "Observability.*"
      - "Learning.*"
      - "Evaluation.*"
      - "Orchestration.RunCompleted"
      - "Orchestration.RunFailed"
      - "Entitlements.*"
      - "Hosting.*"
      - "Provisioning.*"
      - "Domain.*"
      - "Dns.*"
      - "Edge.*"
      - "Certificate.*"

    # Privacy - scrub these fields before forwarding
    scrub_fields:
      - "payload.email"
      - "payload.password"
      - "payload.api_key"
      - "payload.credit_card"
```

### Custom Event Handlers

Apps can register custom handlers for any event:

```python
from mozaiks_core.events import on_event

@on_event("Orders.Created")
async def handle_order_created(event):
    # Custom logic
    await send_confirmation_email(event.payload["customer_id"])
```

---

## Event Versioning

When event schemas change:

1. Increment the `version` field
2. Maintain backward compatibility when possible
3. Document migration path for breaking changes
4. Update JSON schemas

Example:

```json
// Version 1
{
  "type": "Commerce.CreditsConsumed",
  "version": 1,
  "payload": {
    "credits": 150
  }
}

// Version 2 (added details)
{
  "type": "Commerce.CreditsConsumed",
  "version": 2,
  "payload": {
    "credits": 150,
    "details": {
      "model": "claude-3-opus",
      "tokens_input": 2500,
      "tokens_output": 8000
    }
  }
}
```

---

## Summary

| Event Prefix | Routed To | Purpose |
|--------------|-----------|---------|
| `Commerce.*` | Payment.API | Billing |
| `Observability.*` | Analytics | Metrics |
| `Learning.*` | Learning Service | AI improvement |
| `Evaluation.*` | Learning Service | Quality |
| `Orchestration.Run*` | Analytics | Workflow stats |
| `Entitlements.*` | Hosting.API | Access control |
| `Hosting.*` | Hosting.API | Hosted app lifecycle |
| `Provisioning.*` | Hosting.API | Provisioning execution state |
| `Domain.*` | Hosting.API | Domain binding and verification |
| `Dns.*` | Hosting.API | DNS zone and record state |
| `Edge.*` | Hosting.API | Edge routing and domain attachment |
| `Certificate.*` | Hosting.API | TLS issuance and certificate lifecycle |
| All others | Local only | App logic |
