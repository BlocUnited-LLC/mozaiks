# Alerting Configuration

Recommended alert thresholds for Mozaiks runtime observability. Wire these into
your alerting system (Prometheus Alertmanager, Grafana, Datadog, PagerDuty, etc.).

---

## Prometheus Alert Rules

If using Prometheus + Alertmanager, add to `prometheus/rules/mozaiks.yml`:

```yaml
groups:
  - name: mozaiks_runtime
    interval: 30s
    rules:

      # ---- Availability ----

      - alert: MozaiksInstanceDown
        expr: up{job="mozaiks"} == 0
        for: 1m
        labels:
          severity: critical
        annotations:
          summary: "Mozaiks instance {{ $labels.instance }} is down"
          description: "The Mozaiks runtime has been unreachable for more than 1 minute."

      - alert: MozaiksHighWorkflowFailureRate
        expr: |
          rate(mozaiks_workflows_failed_total[5m]) /
          (rate(mozaiks_workflows_started_total[5m]) + 0.001) > 0.05
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "Mozaiks workflow failure rate > 5%"
          description: "More than 5% of workflow runs are failing over the last 5 minutes."

      - alert: MozaiksWorkflowFailureRateCritical
        expr: |
          rate(mozaiks_workflows_failed_total[5m]) /
          (rate(mozaiks_workflows_started_total[5m]) + 0.001) > 0.20
        for: 2m
        labels:
          severity: critical
        annotations:
          summary: "Mozaiks workflow failure rate > 20%"
          description: "Critical workflow failure rate — check LLM API and app backend."

      # ---- Capacity ----

      - alert: MozaiksWorkflowQueueSaturated
        expr: mozaiks_active_workflows >= 18
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "Workflow concurrency near limit"
          description: "Active workflows approaching max concurrency ({{ $value }}). Scale out."

      # ---- Circuit Breakers ----

      - alert: MozaiksCircuitBreakerOpen
        expr: increase(mozaiks_circuit_breaker_opens_total[5m]) > 0
        labels:
          severity: warning
        annotations:
          summary: "Circuit breaker opened: {{ $labels.circuit_name }}"
          description: "The {{ $labels.circuit_name }} circuit is open — downstream service is failing."

      - alert: MozaiksCircuitBreakerRejections
        expr: rate(mozaiks_circuit_breaker_rejections_total[5m]) > 1
        labels:
          severity: warning
        annotations:
          summary: "Circuit breaker rejecting requests: {{ $labels.circuit_name }}"
          description: "Requests are being rejected because the circuit is open."

      # ---- Auth ----

      - alert: MozaiksAuthFailureSpike
        expr: rate(mozaiks_auth_failures_total[5m]) > 10
        for: 2m
        labels:
          severity: warning
        annotations:
          summary: "High authentication failure rate"
          description: "More than 10 auth failures per second — possible brute force or misconfiguration."

      # ---- Module Actions ----

      - alert: MozaiksModuleErrorRate
        expr: |
          rate(mozaiks_module_action_errors_total[5m]) /
          (rate(mozaiks_module_actions_total[5m]) + 0.001) > 0.05
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "Module action error rate > 5%"
          description: "Module {{ $labels.module_id }} action {{ $labels.action_id }} error rate is high."
```

---

## Key Metrics Reference

| Metric | Type | Description |
|--------|------|-------------|
| `mozaiks_workflows_started_total` | Counter | Workflow runs started (label: `workflow_name`) |
| `mozaiks_workflows_completed_total` | Counter | Workflow runs completed (label: `workflow_name`) |
| `mozaiks_workflows_failed_total` | Counter | Workflow runs failed (label: `workflow_name`) |
| `mozaiks_active_workflows` | Gauge | Currently executing workflows |
| `mozaiks_module_actions_total` | Counter | Module dispatches (labels: `module_id`, `action_id`, `outcome`) |
| `mozaiks_module_action_errors_total` | Counter | Module errors (labels: `module_id`, `action_id`) |
| `mozaiks_tokens_input_total` | Counter | Input tokens consumed (label: `workflow_name`) |
| `mozaiks_tokens_output_total` | Counter | Output tokens generated (label: `workflow_name`) |
| `mozaiks_auth_failures_total` | Counter | Auth failures (label: `provider`) |
| `mozaiks_circuit_breaker_opens_total` | Counter | Circuit breaker openings (label: `circuit_name`) |
| `mozaiks_circuit_breaker_rejections_total` | Counter | Rejected requests (label: `circuit_name`) |
| `mozaiks_http_requests_total` | Counter | HTTP requests (labels: `method`, `path`, `status`) |
| `mozaiks_uptime_seconds` | Gauge | Process uptime in seconds |

Metrics endpoint: `GET /metrics` (Prometheus text format)
Authentication: set `PROMETHEUS_METRICS_TOKEN` env var to protect with Bearer token.

---

## Grafana Dashboard

Import the pre-built dashboard from `infra/grafana/mozaiks-dashboard.json`.

Panel layout:
- **Workflow Health**: started/completed/failed rates, active count
- **Module Health**: action rates, error rates by module
- **Token Usage**: input/output tokens per hour, cost estimate
- **Auth**: failure rate, failures by provider
- **Infrastructure**: circuit breaker status, uptime
