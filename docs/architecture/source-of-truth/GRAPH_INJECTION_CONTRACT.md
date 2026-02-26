# Graph Injection Contract

**Status:** Source of truth  
**Last updated:** 2026-02-26

## Purpose

This document defines the runtime contract for graph-based context injection and graph mutation rules used by workflows.

## Scope

Graph injection provides two hook types:

1. **Before-turn injection**: query graph context and inject it into agent/runtime context.
2. **After-event mutation**: run graph writes when matching events occur.

Graph injection is optional and must degrade safely when unavailable.

## File Location

Per workflow:

```text
workflows/{workflow_name}/backend/graph_injection.yaml
```

## Contract Shape

```yaml
version: "1.0"
extends: "../_shared/graph_injection_base.yaml"  # optional

injection_rules:
  - name: inject_recent_patterns
    agents: ["PlannerAgent"]
    condition: "$context.phase == 'planning'"  # optional
    queries:
      - id: recent_patterns
        cypher: |
          MATCH (p:Pattern)
          RETURN p.name, p.score
          ORDER BY p.score DESC
          LIMIT 5
        params:
          tenant_id: "$context.tenant_id"
        inject_as: "recent_patterns"
        format: "list"          # list | single | json | markdown
        max_results: 5            # optional

mutation_rules:
  - name: record_workflow_success
    events: ["telemetry.run.summary"]
    condition: "$event.outcome == 'completed'"  # optional
    mutations:
      - id: upsert_workflow_score
        cypher: |
          MERGE (w:Workflow {name: $workflow_name})
          SET w.last_score = $score,
              w.updated_at = datetime()
        params:
          workflow_name: "$workflow.name"
          score: "$event.score"
```

## Parameter Resolution

| Token | Resolves from |
|---|---|
| `$context.*` | runtime context values |
| `$event.*` | triggering event payload |
| `$workflow.*` | workflow metadata |
| literals | unchanged |

Missing required parameters should skip the rule with structured logging.

## Execution Semantics

### Injection rules

- evaluated before eligible agent turns
- filtered by `agents` and optional `condition`
- query results formatted and attached under `inject_as`

### Mutation rules

- evaluated when event type matches `events`
- optional `condition` gate
- each mutation executes independently

## Inheritance (`extends`)

When `extends` is present:

- parent rules load first
- child rule with same `name` replaces parent rule
- unique names are merged

## Multi-Tenancy

Graph operations must be tenant/app scoped.

Recommended namespacing pattern:

```text
graph_name = "mozaiks_{app_or_tenant_id}"
```

## Failure Behavior

Graph injection must be non-fatal by default.

| Failure | Expected behavior |
|---|---|
| YAML parse/validation error | fail workflow load with clear error |
| graph query timeout | skip rule, continue workflow |
| missing parameter | skip rule, log warning |
| graph backend unavailable | continue workflow without injection/mutation |

## Separation of Concerns

Do not conflate:

- `graph_injection.yaml`: memory/context query/mutation rules
- `_pack/workflow_graph.json`: workflow-to-workflow orchestration dependencies

## Cross References

- [WORKFLOW_ARCHITECTURE.md](WORKFLOW_ARCHITECTURE.md)
- [APP_CREATION_GUIDE.md](APP_CREATION_GUIDE.md)
- [LEARNING_LOOP_ARCHITECTURE.md](LEARNING_LOOP_ARCHITECTURE.md)
